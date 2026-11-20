from django.contrib import messages
from django.core.exceptions import ValidationError
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.home.services.batch_creation import (
    AccessionConflict,
    BatchAlreadyExists,
    BatchCreationError,
    create_referred_batch,
)

from .forms import (
    LabReceiptForm,
    LaboratoryAccessionForm,
    LaboratoryOrganismCountForm,
    LaboratoryReceivingForm,
    LaboratoryRegisterFilterForm,
    ReferralReceiptForm,
)
from .models import (
    LaboratoryAccession,
    LaboratoryOrganismCount,
    LaboratoryReceiving,
    ReceivingEvent,
    ReferralReceipt,
)
from .permissions import (
    can_confirm_lab_receipt,
    can_permanently_delete_receipt,
    admin_receiving_delete_required,
    can_manage_secretariat_receiving,
    lab_receiving_required,
    receiving_read_required,
    secretariat_receiving_required,
)
from .qr import build_receipt_detail_url, render_qr_png
from .reports import REGISTER_COLUMNS, build_register_rows, render_register_pdf
from .services import (
    InvalidReceivingTransition,
    confirm_lab_receipt,
    create_receipt,
    delete_receipt_permanently,
    endorse_receipt,
)


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
def receiving_dashboard(request):
    all_receipts = ReferralReceipt.objects.select_related("site", "received_by")
    counts = {
        row["status"]: row["total"]
        for row in all_receipts.values("status").annotate(total=Count("id"))
    }

    can_manage_secretariat = can_manage_secretariat_receiving(request.user)
    can_confirm_lab = can_confirm_lab_receipt(request.user)
    # Prefer the laboratory queue for users who have both responsibilities.
    is_lab_worklist = can_confirm_lab
    if is_lab_worklist:
        tab_definitions = [
            ("pending", "Pending Lab Receipt", ReferralReceipt.Status.ENDORSED),
            ("received", "Received by Laboratory", ReferralReceipt.Status.LAB_RECEIVED),
            ("all", "All Records", None),
        ]
    else:
        tab_definitions = [
            ("pending", "Pending Endorsement", ReferralReceipt.Status.RECEIVED),
            ("endorsed", "Endorsed", ReferralReceipt.Status.ENDORSED),
            ("received", "Lab Received", ReferralReceipt.Status.LAB_RECEIVED),
            ("all", "All Records", None),
        ]

    tab_status = {key: value for key, _label, value in tab_definitions}
    explicit_tab = (request.GET.get("tab") or "").strip()
    legacy_status = (request.GET.get("status") or "").strip()
    valid_statuses = {value for value, _label in ReferralReceipt.Status.choices}

    # Keep old ?status=... links working. Explicit ?tab=... takes precedence.
    # A legacy status without a corresponding role tab uses All Records.
    selected_tab = explicit_tab or "pending"
    if not explicit_tab and legacy_status in valid_statuses:
        selected_tab = next(
            (key for key, _label, value in tab_definitions if value == legacy_status),
            "all",
        )
    if selected_tab not in tab_status:
        selected_tab = "pending"

    query = (request.GET.get("q") or "").strip()
    receipts = all_receipts
    if query:
        receipts = receipts.filter(
            Q(tracking_number__icontains=query)
            | Q(site__SiteCode__icontains=query)
            | Q(site__SiteName__icontains=query)
        )

    # Counts are for the full queue and do not change with search or pagination.
    tabs = [
        {
            "key": key,
            "label": label,
            "count": sum(counts.values()) if tab_filter is None else counts.get(tab_filter, 0),
            "active": key == selected_tab,
        }
        for key, label, tab_filter in tab_definitions
    ]
    selected_status = tab_status[selected_tab]
    if not explicit_tab and legacy_status in valid_statuses:
        receipts = receipts.filter(status=legacy_status)
    elif selected_status is not None:
        receipts = receipts.filter(status=selected_status)

    page_obj = Paginator(receipts.order_by("-created_at", "-pk"), 25).get_page(request.GET.get("page"))
    return render(
        request,
        "receiving/dashboard.html",
        {
            "page_obj": page_obj,
            "q": query,
            "active_tab": selected_tab,
            "active_tab_label": next(tab["label"] for tab in tabs if tab["active"]),
            "receiving_tabs": tabs,
            "received_count": counts.get(ReferralReceipt.Status.RECEIVED, 0),
            "endorsed_count": counts.get(ReferralReceipt.Status.ENDORSED, 0),
            "lab_received_count": counts.get(ReferralReceipt.Status.LAB_RECEIVED, 0),
            "can_manage_secretariat": can_manage_secretariat,
            "can_confirm_lab": can_confirm_lab,
        },
    )


def _laboratory_record(receipt):
    if receipt.status != ReferralReceipt.Status.LAB_RECEIVED:
        return None
    return LaboratoryReceiving.objects.filter(receipt=receipt).first()


@login_required
@receiving_read_required
def laboratory_receiving_workspace(request, receiving_id):
    receipt = _receipt_or_404(receiving_id)
    record = _laboratory_record(receipt)
    if record is None:
        messages.warning(request, "Confirm laboratory custody before recording acceptance decisions.")
        return redirect("receiving_detail", receiving_id=receiving_id)
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
    ranges = list(record.accession_ranges.select_related("batch").prefetch_related("organism_counts"))
    organism_rows = []
    for accession in ranges:
        values = {count.organism_code: count.count for count in accession.organism_counts.all()}
        organism_rows.append({
            "accession": accession,
            "cells": [
                {"code": code, "short": short, "label": label, "count": values.get(code, 0)}
                for code, short, label in REGISTER_COLUMNS
            ],
            "total": sum(values.values()),
        })
    return render(request, "receiving/laboratory_workspace.html", {
        "receipt": receipt, "record": record, "form": form,
        "accession_form": LaboratoryAccessionForm(initial={"date_referred": record.date_accepted or receipt.lab_date_received}),
        "organism_form": LaboratoryOrganismCountForm(),
        "ranges": ranges, "organism_rows": organism_rows, "register_columns": REGISTER_COLUMNS, "can_edit": can_edit,
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
        return redirect("receiving_detail", receiving_id=receiving_id)
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
    )
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
    if sum(counts.values()) > accession.isolate_count:
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
        "accession_ranges__batch", "accession_ranges__organism_counts"
    )
    rows, totals, organism_totals = build_register_rows(records)
    return form, month, year, records, rows, totals, organism_totals


@login_required
@receiving_read_required
def laboratory_receiving_register(request):
    form, month, year, _records, rows, totals, organism_totals = _register_context(request)
    return render(request, "receiving/laboratory_register.html", {
        "form": form, "month": month, "year": year, "rows": rows,
        "totals": totals, "organism_totals": organism_totals,
        "organism_choices": LaboratoryOrganismCount.OrganismCode.choices,
        "register_columns": REGISTER_COLUMNS,
    })


@login_required
@receiving_read_required
def laboratory_receiving_register_pdf(request):
    _form, month, year, records, _rows, _totals, _organism_totals = _register_context(request)
    response = HttpResponse(render_register_pdf(records, month, year), content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="receiving-register-{year}-{month:02d}.pdf"'
    return response


@login_required
@secretariat_receiving_required
def receiving_create(request):
    if request.method == "POST":
        form = ReferralReceiptForm(request.POST)
        if form.is_valid():
            receipt = create_receipt(form=form, user=request.user)
            messages.success(request, "Courier batch recorded successfully.")
            return redirect("receiving_detail", receiving_id=receipt.receiving_id)
    else:
        form = ReferralReceiptForm()
    return render(request, "receiving/receipt_form.html", {"form": form})


@login_required
@receiving_read_required
def receiving_detail(request, receiving_id):
    receipt = _receipt_or_404(receiving_id)
    events = receipt.events.select_related("performed_by").order_by(
        "occurred_at", "recorded_at", "id"
    )
    can_manage = can_manage_secretariat_receiving(request.user)
    can_confirm = can_confirm_lab_receipt(request.user)
    return render(
        request,
        "receiving/receipt_detail.html",
        {
            "receipt": receipt,
            "events": events,
            "can_print_label": can_manage,
            "can_permanently_delete": can_permanently_delete_receipt(request.user)
            and receipt.can_permanently_delete,
            "can_endorse": can_manage and receipt.status == ReferralReceipt.Status.RECEIVED,
            "can_confirm_lab": can_confirm
            and receipt.status == ReferralReceipt.Status.ENDORSED,
            "can_work_lab": can_confirm
            and receipt.status == ReferralReceipt.Status.LAB_RECEIVED,
        },
    )


@login_required
@secretariat_receiving_required
def receiving_qr(request, receiving_id):
    receipt = _receipt_or_404(receiving_id)
    payload = build_receipt_detail_url(receipt=receipt, request=request)
    response = HttpResponse(render_qr_png(payload), content_type="image/png")
    response["Cache-Control"] = "private, max-age=3600"
    return response


@login_required
@secretariat_receiving_required
def receiving_label(request, receiving_id):
    receipt = _receipt_or_404(receiving_id)
    return render(request, "receiving/receipt_label.html", {"receipt": receipt})


@login_required
@secretariat_receiving_required
@require_POST
def receiving_endorse(request, receiving_id):
    receipt = _receipt_or_404(receiving_id)
    try:
        endorse_receipt(receipt_id=receipt.pk, user=request.user)
    except InvalidReceivingTransition as exc:
        messages.warning(request, str(exc))
    else:
        messages.success(request, "Courier batch endorsed to the laboratory.")
    return redirect("receiving_detail", receiving_id=receiving_id)


@login_required
@lab_receiving_required
def receiving_lab_confirm(request, receiving_id):
    receipt = _receipt_or_404(receiving_id)
    if receipt.status == ReferralReceipt.Status.LAB_RECEIVED:
        messages.info(request, "This batch has already been received by the laboratory.")
        return redirect("receiving_detail", receiving_id=receiving_id)
    if receipt.status != ReferralReceipt.Status.ENDORSED:
        messages.warning(request, "This batch has not yet been endorsed to the laboratory.")
        return redirect("receiving_detail", receiving_id=receiving_id)
    if request.method == "POST":
        form = LabReceiptForm(request.POST, receipt=receipt)
        if form.is_valid():
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
                messages.success(request, "Laboratory receipt confirmed. Open the laboratory worksheet to record the manual acceptance decision.")
            return redirect("receiving_detail", receiving_id=receiving_id)
    else:
        form = LabReceiptForm(receipt=receipt)
    return render(
        request,
        "receiving/lab_receipt_form.html",
        {"form": form, "receipt": receipt},
    )


@login_required
@admin_receiving_delete_required
@require_POST
def receiving_delete_permanently(request, receiving_id):
    """Administrators may irreversibly remove a mistakenly entered courier batch.

    Existing ReceivingEvent rows cascade away; no deletion-history event is created.
    This does not delete independent Raw/Final/Data or accessioning records.
    """
    receipt = _receipt_or_404(receiving_id)
    if request.POST.get("confirmation", "").strip() != "DELETE PERMANENTLY":
        messages.error(request, "Type DELETE PERMANENTLY to confirm.")
        return redirect("receiving_detail", receiving_id=receiving_id)
    try:
        delete_receipt_permanently(receipt_id=receipt.pk, user=request.user)
    except InvalidReceivingTransition as exc:
        messages.warning(request, str(exc))
        return redirect("receiving_detail", receiving_id=receiving_id)
    messages.success(request, "Receiving batch permanently deleted.")
    return redirect("receiving_dashboard")
