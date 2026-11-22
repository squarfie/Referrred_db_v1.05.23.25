from urllib.parse import urlencode
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Case, CharField, Count, F, Q, Value, When
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from apps.home.services.batch_creation import (AccessionConflict, BatchAlreadyExists, BatchCreationError, create_referred_batch, preview_referred_batch_conflicts)
from apps.receiving.services import InvalidReceivingTransition, confirm_lab_receipt
from .dashboard import render_laboratory_dashboard as _render_receiving_dashboard
from .forms import (LabReceiptForm, LaboratoryAccessionForm, LaboratoryOrganismCountForm, LaboratoryReceivingForm, LaboratoryRegisterFilterForm)
from .models import LaboratoryAccession, LaboratoryIsolate, LaboratoryOrganismCount, LaboratoryReceiving, ReceivingEvent, ReferralReceipt
from .permissions import can_confirm_lab_receipt, lab_receiving_required, can_read_laboratory_receiving, lab_receiving_read_required
from apps.receiving.permissions import receiving_read_required
from .reports import REGISTER_COLUMNS, build_register_rows, render_register_pdf

def _receipt_or_404(receiving_id):
    return get_object_or_404(
        ReferralReceipt.objects.select_related(
            "site",
            "received_by",
            "endorsed_by",
            "lab_received_by",
        ),
        receiving_id=receiving_id,
    )


@login_required
@receiving_read_required
def laboratory_receiving_dashboard(request):
    if not can_read_laboratory_receiving(request.user):
        messages.error(request, "Only Laboratory Receiving personnel may open this queue.")
        return redirect("home")
    if request.GET.get("tab") == "returned":
        response = redirect("laboratory_processing_worklist")
        response["Location"] += "?" + urlencode({"history": "1", "q": request.GET.get("q", "")})
        return response
    return _render_receiving_dashboard(request, queue="laboratory")


def _laboratory_workflow_receipts(receipts):
    """Derived receipt queues; never advance the scientific or custody record."""
    path = "laboratory_receiving__accession_ranges__isolate_records"
    receipts = receipts.annotate(
        workflow_isolates=Count(path, distinct=True),
        workflow_closed=Count(path, filter=Q(**{path + "__status__in": ["non_viable"]}) |
            (Q(**{path + "__status": "biobanked"}) &
             Q(**{path + "__identification_source": "main_lab"}) &
             Q(**{path + "__returned_at__isnull": False}) &
             Q(**{path + "__reviewed_at__isnull": False}) &
             Q(**{path + "__lyophilization_completed_at__isnull": False}) &
             Q(**{path + "__biobanked_at__isnull": False})), distinct=True),
        workflow_pending_id=Count(path, filter=Q(**{path + "__status__in": ["id_queued", "id_pending", "id_testing"]}), distinct=True),
    )
    complete = Q(
        laboratory_receiving__total_isolates_received__gt=0,
        laboratory_receiving__total_isolates_received=F("laboratory_receiving__isolates_accepted") + F("laboratory_receiving__isolates_rejected"),
        workflow_isolates=F("laboratory_receiving__isolates_accepted"),
        workflow_closed=F("laboratory_receiving__isolates_accepted"),
    )
    return receipts.annotate(lab_workflow=Case(
        When(status=ReferralReceipt.Status.LAB_RECEIVED, then=Case(
            When(complete, then=Value("complete")),
            When(workflow_isolates=0, then=Value("received")),
            When(workflow_pending_id__gt=0, then=Value("main_lab")),
            default=Value("processing"), output_field=CharField(),
        )),
        default=Value("pending"), output_field=CharField(),
    ))


def _laboratory_record(receipt):
    if receipt.status != ReferralReceipt.Status.LAB_RECEIVED:
        return None
    return LaboratoryReceiving.objects.filter(receipt=receipt).first()


@login_required
@lab_receiving_read_required
def laboratory_receiving_workspace(request, receiving_id):
    receipt = _receipt_or_404(receiving_id)
    record = _laboratory_record(receipt)
    if record is None:
        messages.warning(request, "Confirm laboratory custody before recording acceptance decisions.")
        return redirect("laboratory_receiving_dashboard")
    can_edit = can_confirm_lab_receipt(request.user)
    if request.method == "POST":
        if not can_edit:
            messages.error(request, "Only a LAB Encoder may update laboratory decisions.")
            return redirect("laboratory_receiving_workspace", receiving_id=receiving_id)
        form = LaboratoryReceivingForm(request.POST, instance=record)
        if form.is_valid():
            updated = form.save(commit=False)
            updated.recorded_by = request.user
            updated.save()
            messages.success(request, "Laboratory acceptance and rejection decision saved.")
            return redirect("laboratory_receiving_workspace", receiving_id=receiving_id)
    else:
        form = LaboratoryReceivingForm(instance=record)
    ranges = list(record.accession_ranges.select_related("batch").prefetch_related("organism_counts", "additional_organism_counts", "isolate_records"))
    organism_rows = []
    for accession in ranges:
        values = {count.organism_code: count.count for count in accession.organism_counts.all()}
        organism_rows.append({
            "accession": accession,
            "cells": [
                {"code": code, "short": short, "label": label, "count": values.get(code, 0)}
                for code, short, label in REGISTER_COLUMNS
            ],
            "total": sum(values.values()) + sum(item.count for item in accession.additional_organism_counts.all()),
            "fixed_total": sum(values.values()),
            "additional_rows": [{"item": item, "cells": [
                {"code": code, "label": label, "count": item.inventory_values.get(code, 0)}
                for code, _short, label in REGISTER_COLUMNS
            ]} for item in accession.additional_organism_counts.all()],
        })
    batch_previews = {}
    if can_edit:
        for accession in ranges:
            if accession.batch_id:
                continue
            warnings = preview_referred_batch_conflicts(
                site_code=receipt.site.SiteCode,
                referral_date=accession.date_referred,
                start_number=accession.start_number,
                end_number=accession.end_number,
                batch_number=accession.batch_number,
                total_batches=accession.total_batches,
            )
            batch_previews[accession.pk] = {
                "warnings": warnings,
                "message": " ".join(warnings),
                "blocked": bool(warnings),
            }
    batch_rows = [
        {"accession": accession, "preview": batch_previews.get(accession.pk, {})}
        for accession in ranges
    ]
    from collections import Counter
    workflow_rows = []
    for accession in ranges:
        isolates = list(accession.isolate_records.all())
        # Counter returns zero for missing keys, so Django templates resolve
        # stages.items as an integer instead of the dict.items method.
        stages = dict(Counter(item.status for item in isolates))
        accession.can_queue_id = any(item.status in ("revived", "site_banked", "ready_for_biobank") and not item.returned_at for item in isolates)
        # A custody handoff to Main Lab is one batch-level decision; partial queues
        # remain pending until all viable isolates have revival and Site ID data.
        all_present = bool(isolates) and len(isolates) == accession.isolate_count
        all_disposed = all_present and all(i.status in ("id_queued", "non_viable") for i in isolates)
        queued = [i for i in isolates if i.status == "id_queued"]
        accession.can_endorse_id = bool(queued) and all_disposed and all(
            i.revival_started_at and i.revival_completed_at
            and i.site_organism_code.strip() and i.site_organism_name.strip() for i in queued
        )
        workflow_rows.append({
            "accession": accession, "stages": stages,
            "count": len(isolates), "expected": accession.isolate_count,
            "can_handoff": can_edit and accession.can_endorse_id,
            "can_prepare": can_edit and not isolates,
            "can_queue": can_edit and accession.can_queue_id,
            "can_start": can_edit and len(isolates) == accession.isolate_count and all(i.status == "revival_queued" for i in isolates),
            "can_complete": can_edit and len(isolates) == accession.isolate_count and any(i.status == "revival_in_progress" for i in isolates) and all(i.status in {"revival_in_progress", "revived"} for i in isolates),

        })
    return render(request, "laboratory/laboratory_workspace.html", {
        "receipt": receipt, "record": record, "form": form,
        "accession_form": LaboratoryAccessionForm(initial={"date_referred": record.date_accepted or receipt.lab_date_received}),
        "organism_form": LaboratoryOrganismCountForm(),
        "ranges": ranges, "batch_rows": batch_rows, "workflow_rows": workflow_rows, "organism_rows": organism_rows, "register_columns": REGISTER_COLUMNS, "can_edit": can_edit,
    })


@login_required
@lab_receiving_required
@require_POST
@transaction.atomic
def laboratory_accession_add(request, receiving_id):
    receipt = _receipt_or_404(receiving_id)
    record = LaboratoryReceiving.objects.select_for_update().filter(receipt=receipt).first()
    if record is None:
        messages.error(request, "Laboratory custody has not been confirmed.")
        return redirect("laboratory_receiving_detail", receiving_id=receiving_id)
    if record.isolates_accepted <= 0:
        messages.error(request, "Save a laboratory decision with accepted isolates before assigning accessions.")
        return redirect("laboratory_receiving_workspace", receiving_id=receiving_id)
    form = LaboratoryAccessionForm(request.POST)
    if form.is_valid():
        accession = form.save(commit=False)
        accession.laboratory_receiving = record
        accession.assigned_by = request.user
        try:
            accession.full_clean()
        except Exception as exc:
            messages.error(request, "; ".join(getattr(exc, "messages", [str(exc)])))
        else:
            with transaction.atomic():
                accession.save()
                ReceivingEvent.objects.create(
                    receipt=receipt,
                    event_type=ReceivingEvent.EventType.ACCESSION_ASSIGNED,
                    performed_by=request.user,
                    remarks=f"Assigned {accession.reference_range} for batch {accession.batch_number}/{accession.total_batches}.",
                )
            messages.success(request, f"Accession range {accession.reference_range} saved. Create Batch is a separate action.")
    else:
        messages.error(request, "Please correct the accession range details.")
    return redirect("laboratory_receiving_workspace", receiving_id=receiving_id)


@login_required
@lab_receiving_required
@require_POST
@transaction.atomic
def laboratory_accession_edit(request, accession_id):
    accession = get_object_or_404(
        LaboratoryAccession.objects.select_for_update().select_related(
            "laboratory_receiving__receipt"
        ),
        pk=accession_id,
    )
    destination = redirect(
        "laboratory_receiving_workspace",
        receiving_id=accession.laboratory_receiving.receipt.receiving_id,
    )
    if accession.isolate_records.exists():
        messages.error(request, "This range has been endorsed to ID testing and cannot be edited.")
        return destination
    if accession.batch_id:
        messages.error(request, "Generated batches cannot be edited here. Contact an administrator for a reviewed correction.")
        return destination
    form = LaboratoryAccessionForm(request.POST, instance=accession)
    if not form.is_valid():
        messages.error(request, "Please correct the accession details.")
        return destination

    previous_size = accession.isolate_count
    previous_range = accession.reference_range
    changed = form.save(commit=False)
    recorded_total = sum(
        changed.organism_counts.values_list("count", flat=True)
    ) + sum(changed.additional_organism_counts.values_list("count", flat=True))
    if recorded_total > changed.isolate_count:
        messages.error(request, "The new range is smaller than its recorded organism counts.")
        return destination
    try:
        changed.full_clean()
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return destination
    changed.assigned_by = request.user
    changed.save()
    ReceivingEvent.objects.create(
        receipt=changed.laboratory_receiving.receipt,
        event_type=ReceivingEvent.EventType.CORRECTED,
        performed_by=request.user,
        remarks=f"Updated accession range {previous_range} to {changed.reference_range} (previous isolate count: {previous_size}).",
    )
    messages.success(request, f"Accession range {changed.reference_range} updated.")
    return destination


@login_required
@lab_receiving_required
@require_POST
@transaction.atomic
def laboratory_accession_delete(request, accession_id):
    accession = get_object_or_404(
        LaboratoryAccession.objects.select_for_update().select_related(
            "laboratory_receiving__receipt"
        ), pk=accession_id,
    )
    destination = redirect(
        "laboratory_receiving_workspace",
        receiving_id=accession.laboratory_receiving.receipt.receiving_id,
    )
    if accession.isolate_records.exists():
        messages.error(request, "This range has ID laboratory records and cannot be deleted.")
        return destination
    if accession.batch_id:
        messages.error(request, "This accession range has generated a batch and cannot be deleted here.")
        return destination
    if request.POST.get("confirmation") != "DELETE":
        messages.error(request, "Confirmation is required to delete the accession range.")
        return destination
    label = accession.reference_range
    receipt = accession.laboratory_receiving.receipt
    # These rows are exclusively attached to the ungenerated range.
    accession.organism_counts.all().delete()
    accession.additional_organism_counts.all().delete()
    accession.delete()
    ReceivingEvent.objects.create(
        receipt=receipt,
        event_type=ReceivingEvent.EventType.CORRECTED,
        performed_by=request.user,
        remarks=f"Deleted ungenerated accession range {label}.",
    )
    messages.success(request, f"Accession range {label} deleted.")
    return destination


@login_required
@lab_receiving_required
@require_POST
@transaction.atomic
def laboratory_organism_matrix_save(request, accession_id):
    accession = get_object_or_404(
        LaboratoryAccession.objects.select_for_update().select_related(
            "laboratory_receiving__receipt"
        ), pk=accession_id,
    )
    destination = redirect(
        "laboratory_receiving_workspace",
        receiving_id=accession.laboratory_receiving.receipt.receiving_id,
    )
    counts = {}
    for code, _short, _label in REGISTER_COLUMNS:
        raw = (request.POST.get(f"organism_{code}") or "").strip()
        if not raw:
            counts[code] = 0
        elif not raw.isascii() or not raw.isdecimal():
            messages.error(request, f"{code}: please enter a non-negative whole number.")
            return destination
        else:
            counts[code] = int(raw)
    additional = sum(accession.additional_organism_counts.values_list("count", flat=True))
    if sum(counts.values()) + additional > accession.isolate_count:
        messages.error(request, "The total organism count cannot exceed the accession-range isolate count.")
        return destination

    # The model enforces positive counts; the whole row is validated above.
    # Replace within one transaction so a failed save rolls back completely.
    LaboratoryOrganismCount.objects.select_for_update().filter(
        accession_range=accession
    ).delete()
    LaboratoryOrganismCount.objects.bulk_create([
        LaboratoryOrganismCount(accession_range=accession, organism_code=code, count=value)
        for code, value in counts.items() if value
    ])
    messages.success(request, f"Organism counts for {accession.reference_range} saved.")
    return destination


@login_required
@lab_receiving_required
@require_POST
@transaction.atomic
def laboratory_organism_add(request, accession_id):
    accession = get_object_or_404(
        LaboratoryAccession.objects.select_for_update().select_related("laboratory_receiving__receipt"), pk=accession_id
    )
    form = LaboratoryOrganismCountForm(request.POST)
    if form.is_valid():
        item = form.save(commit=False)
        item.accession_range = accession
        try:
            item.full_clean()
            item.save()
        except Exception as exc:
            messages.error(request, "; ".join(getattr(exc, "messages", [str(exc)])))
        else:
            messages.success(request, "Organism inventory count saved.")
    else:
        messages.error(request, "Please correct the organism count.")
    return redirect("laboratory_receiving_workspace", receiving_id=accession.laboratory_receiving.receipt.receiving_id)


@login_required
@lab_receiving_required
@require_POST
def laboratory_batch_create(request, accession_id):
    with transaction.atomic():
        accession = get_object_or_404(
            LaboratoryAccession.objects.select_for_update().select_related(
                "laboratory_receiving__receipt__site"
            ), pk=accession_id,
        )
        receipt = accession.laboratory_receiving.receipt
        if accession.batch_id:
            messages.info(request, "This accession range already has a generated batch.")
            return redirect("laboratory_receiving_workspace", receiving_id=receipt.receiving_id)
        try:
            result = create_referred_batch(
                site_code=receipt.site.SiteCode,
                site_name=receipt.site.SiteName,
                referral_date=accession.date_referred,
                start_number=accession.start_number,
                end_number=accession.end_number,
                batch_number=accession.batch_number,
                total_batches=accession.total_batches,
                created_by=request.user,
            )
        except BatchAlreadyExists as exc:
            messages.error(request, f"Batch {exc.batch.bat_Batch_Code} already exists and was not overwritten.")
        except AccessionConflict as exc:
            labels = [row.get("AccessionNo") or row.get("f_AccessionNo") for row in exc.conflicts[:5]]
            messages.error(request, "Accession conflict: " + ", ".join(filter(None, labels)))
        except BatchCreationError as exc:
            messages.error(request, str(exc))
        else:
            accession.batch = result.batch
            accession.batch_created_at = timezone.now()
            accession.save(update_fields=("batch", "batch_created_at"))
            ReceivingEvent.objects.create(
                receipt=receipt,
                event_type=ReceivingEvent.EventType.BATCH_CREATED,
                performed_by=request.user,
                remarks=f"Created {result.batch.bat_Batch_Code} with {result.isolate_count} isolate(s).",
            )
            messages.success(request, f"Batch {result.batch.bat_Batch_Code} created in the existing Batch Table.")
    return redirect("laboratory_receiving_workspace", receiving_id=receipt.receiving_id)


def _register_context(request):
    local_today = timezone.localdate()
    form = LaboratoryRegisterFilterForm(request.GET or {"month": local_today.month, "year": local_today.year})
    month, year = local_today.month, local_today.year
    if form.is_valid():
        month, year = form.cleaned_data["month"], form.cleaned_data["year"]
    records = LaboratoryReceiving.objects.filter(
        total_isolates_received__gt=0,
        receipt__lab_date_received__month=month,
        receipt__lab_date_received__year=year,
    ).select_related("receipt__site", "receipt__lab_received_by").prefetch_related(
        "accession_ranges__batch", "accession_ranges__organism_counts", "accession_ranges__additional_organism_counts"
    )
    rows, totals, organism_totals = build_register_rows(records)
    return form, month, year, records, rows, totals, organism_totals


@login_required
@lab_receiving_read_required
def laboratory_receiving_register(request):
    form, month, year, _records, rows, totals, organism_totals = _register_context(request)
    return render(request, "laboratory/laboratory_register.html", {
        "form": form, "month": month, "year": year, "rows": rows,
        "totals": totals, "organism_totals": organism_totals,
        "organism_choices": LaboratoryOrganismCount.OrganismCode.choices,
        "register_columns": REGISTER_COLUMNS,
    })


@login_required
@lab_receiving_read_required
def laboratory_receiving_register_pdf(request):
    _form, month, year, records, _rows, _totals, _organism_totals = _register_context(request)
    response = HttpResponse(render_register_pdf(records, month, year), content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="receiving-register-{year}-{month:02d}.pdf"'
    return response


@login_required
@lab_receiving_read_required
def laboratory_receiving_detail(request, receiving_id):
    receipt = _receipt_or_404(receiving_id)
    events = receipt.events.select_related("performed_by").order_by(
        "occurred_at", "recorded_at", "id"
    )
    can_confirm = can_confirm_lab_receipt(request.user)
    return render(request, "laboratory/receipt_detail.html", {
        "receipt": receipt, "events": events,
        "can_print_label": False, "can_permanently_delete": False,
        "can_endorse": False,
        "can_confirm_lab": can_confirm and receipt.status == ReferralReceipt.Status.ENDORSED,
        "can_work_lab": receipt.status == ReferralReceipt.Status.LAB_RECEIVED,
        "laboratory_detail": True,
    })


@login_required
@lab_receiving_required
def receiving_lab_confirm(request, receiving_id):
    receipt = _receipt_or_404(receiving_id)
    if receipt.status == ReferralReceipt.Status.LAB_RECEIVED:
        messages.info(request, "This batch has already been received by the laboratory.")
        return redirect("laboratory_receiving_detail", receiving_id=receiving_id)
    if receipt.status != ReferralReceipt.Status.ENDORSED:
        messages.warning(request, "This batch has not yet been endorsed to the laboratory.")
        return redirect("laboratory_receiving_detail", receiving_id=receiving_id)
    if request.method == "POST":
        form = LabReceiptForm(request.POST, receipt=receipt)
        if form.is_valid():
            confirmed = False
            try:
                confirm_lab_receipt(
                    receipt_id=receipt.pk,
                    user=request.user,
                    lab_date_received=form.cleaned_data["lab_date_received"],
                    lab_time_received=form.cleaned_data["lab_time_received"],
                )
            except InvalidReceivingTransition as exc:
                messages.warning(request, str(exc))
            else:
                confirmed = True
                messages.success(request, "Laboratory receipt confirmed. Open the laboratory worksheet to record the manual acceptance decision.")
            if confirmed:
                return redirect("laboratory_receiving_workspace", receiving_id=receiving_id)
            return redirect("laboratory_receiving_detail", receiving_id=receiving_id)
    else:
        form = LabReceiptForm(receipt=receipt)
    return render(
        request,
        "laboratory/lab_receipt_form.html",
        {"form": form, "receipt": receipt},
    )
