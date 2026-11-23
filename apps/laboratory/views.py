import csv
import base64
from datetime import datetime, timedelta
from io import BytesIO, StringIO
from itertools import islice
from zipfile import BadZipFile
from hashlib import sha256
from urllib.parse import urlencode

from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.db.models import Count, Prefetch, Q
from .organism_lookup import find_organisms, resolve_organism
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .guidance import next_action
from .identification_workflow import record_identification, finalize_banking, final_banking_filter, is_final_banked
from .navigation import return_url, detail_url
from .forms import StickerDateRangeForm
from apps.receiving.qr import build_isolate_scan_url, render_qr_png
from .workflow import (WorkflowError, transition_batch, transition_isolate_revival,
    record_revival_outcome, record_revival_failure, retry_revival, complete_revival_isolate, complete_lyophilization, save_biobank, create_id_handoff, acknowledge_id_receipt,
    bank_using_site_id, site_identification, record_site_identification, queue_for_id, id_handoff_readiness)
from .models import (
    LaboratoryAccession, LaboratoryBiobank, LaboratoryIdentification, LaboratoryIsolate,
)
from .permissions import (
    can_access_laboratory_worklist, can_manage_id_testing, can_manage_biobank,
    can_review_id_results, lab_staff_required, lab_review_required, lab_worklist_required, main_lab_required, can_access_main_lab,
)

@login_required
@lab_worklist_required
def laboratory_organism_search(request):
    query = (request.GET.get("q") or "").strip()[:100]
    return JsonResponse({"results": find_organisms(query) if len(query) >= 1 else []})


MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_ROWS = 1000
UPLOAD_COLUMNS = ("accession_number", "organism_reported", "instrument", "cassette_reference", "organism_code")



@login_required
@lab_worklist_required
def laboratory_biobank_list(request):
    return render(request, "laboratory/lab_biobank_list.html", _biobank_inventory_context(request))


def _biobank_inventory_context(request):
    search = (request.GET.get("q") or "").strip()
    S = LaboratoryIsolate.Status
    final = final_banking_filter()
    temporary = Q(status=S.SITE_BANKED) | Q(status=S.READY_FOR_BIOBANK, identification_source="site")
    continuing = (Q(site_banked_at__isnull=False) | Q(biobank__isnull=False)) & Q(status__in=(
        S.ID_QUEUED, S.ID_PENDING, S.ID_TESTING, S.ID_COMPLETE, S.ID_RETURNED, S.ID_REVIEWED,
    )) | Q(status=S.READY_FOR_BIOBANK, identification_source="main_lab")
    isolates = LaboratoryIsolate.objects.select_related(
        "accession_range__laboratory_receiving__receipt__site", "biobank"
    ).filter(Q(status__in=(S.READY_FOR_BIOBANK, S.SITE_BANKED, S.BIOBANKED)) | continuing)
    definitions = (
        ("all", "All records", Q()),
        ("temporary", "Temporary / awaiting Main Lab ID", temporary),
        ("main_lab", "Main Lab ID / processing", continuing),
        ("final", "Final banked", final),
    )
    selected_tab = request.GET.get("tab", "all")
    if selected_tab not in {key for key, _, _ in definitions}:
        selected_tab = "all"
    tabs = [{"key": key, "label": label, "count": isolates.filter(condition).count(),
             "active": key == selected_tab} for key, label, condition in definitions]
    isolates = isolates.filter(next(condition for key, _, condition in definitions if key == selected_tab))
    isolates = isolates.order_by("accession_range_id", "accession_number")
    if search:
        matches = Q(accession_number__icontains=search) | Q(
            accession_range__laboratory_receiving__receipt__site__SiteCode__icontains=search
        )
        bounds = search.split("-")
        if len(bounds) == 2 and all(
            1 <= len(part.strip()) <= 4 and part.strip().isdecimal() for part in bounds
        ):
            matches |= Q(accession_range__start_number=int(bounds[0]),
                         accession_range__end_number=int(bounds[1]))
        isolates = isolates.filter(matches)
    batches = LaboratoryAccession.objects.filter(
        pk__in=isolates.values("accession_range_id")
    ).select_related("laboratory_receiving__receipt__site").order_by(
        "laboratory_receiving__receipt__site__SiteCode", "-date_referred", "-pk"
    )
    page = Paginator(batches, 25).get_page(request.GET.get("page"))
    page.object_list = page.object_list.prefetch_related(
        Prefetch("isolate_records", queryset=isolates, to_attr="inventory_isolates")
    )
    for batch in page.object_list:
        for item in batch.inventory_isolates:
            item.inventory_final = is_final_banked(item)
            item.inventory_next_action = next_action(item, request.user)
    return {
        "inventory_batches": page.object_list, "page_obj": page, "query": search,
        "inventory_tabs": tabs, "selected_tab": selected_tab,
        "can_queue_inventory": can_review_id_results(request.user),
        "can_retrieve": can_access_main_lab(request.user),
        "origin_url": request.get_full_path(),
        "can_bank": can_manage_biobank(request.user),
        "sticker_date_form": StickerDateRangeForm(initial={
            "from_date": timezone.localdate(), "to_date": timezone.localdate(),
        }),
    }


@login_required
@lab_worklist_required
def laboratory_worklist(request, area="identification"):
    """Legacy worklist: route Main Lab-only users into their own dashboard."""
    if can_access_main_lab(request.user) and not can_review_id_results(request.user):
        return redirect("main_lab_dashboard")
    query = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip()
    batches = LaboratoryAccession.objects.select_related(
        "laboratory_receiving__receipt__site", "batch"
    ).filter(isolate_records__isnull=False).distinct().order_by(
        "-date_referred", "-pk"
    )
    if query:
        batches = batches.filter(
            Q(isolate_records__accession_number__icontains=query)
            | Q(laboratory_receiving__receipt__tracking_number__icontains=query)
            | Q(laboratory_receiving__receipt__site__SiteCode__icontains=query)
        ).distinct()
    if status in LaboratoryIsolate.Status.values:
        batches = batches.filter(isolate_records__status=status).distinct()
    show_history = request.GET.get("history") == "1"
    S = LaboratoryIsolate.Status
    active = (S.ID_PENDING, S.ID_TESTING) if area == "identification" else (
        S.ID_COMPLETE, S.ID_RETURNED, S.ID_QUEUED, S.REVIVAL_PENDING, S.REVIVAL_QUEUED, S.REVIVAL_IN_PROGRESS, S.REVIVAL_FAILED,
        S.REVIVED, S.ID_REVIEWED, S.READY_FOR_BIOBANK, S.SITE_BANKED,
    )
    # Scope badges count isolates across the complete queue, before filters and
    # the batch display limit. Returned history can overlap active processing.
    scope_counts = {
        "active": LaboratoryIsolate.objects.filter(status__in=active).count(),
        "history": (LaboratoryIsolate.objects.filter(returned_at__isnull=False).count()
                    if area == "processing" else LaboratoryIsolate.objects.count()),
    }
    if request.GET.get("tab") == "id_complete":
        batches = batches.filter(isolate_records__status__in=(S.ID_COMPLETE, S.ID_RETURNED)).distinct()
    if show_history and area == "processing":
        batches = batches.filter(isolate_records__returned_at__isnull=False).distinct()
    if not show_history:
        batches = batches.filter(isolate_records__status__in=active).distinct()
    rows = []
    for accession in batches.prefetch_related("isolate_records")[:250]:
        isolates = list(accession.isolate_records.all())
        counts = {}
        for isolate in isolates:
            counts[isolate.status] = counts.get(isolate.status, 0) + 1
        rows.append({
            "accession": accession,
            "count": len(isolates),
            "status_counts": counts,
            "returned_count": sum(bool(item.returned_at) for item in isolates),
            "reviewed_count": sum(bool(item.reviewed_at) for item in isolates),
            "biobanked_count": counts.get(S.BIOBANKED, 0) + counts.get(S.SITE_BANKED, 0),
            "awaiting_id_count": sum(counts.get(st, 0) for st in (S.ID_PENDING, S.ID_TESTING)),
            "awaiting_revival_count": sum(counts.get(st, 0) for st in (S.REVIVAL_PENDING, S.REVIVAL_QUEUED, S.REVIVAL_IN_PROGRESS, S.REVIVAL_FAILED)),
            "awaiting_disposition_count": sum(item.status == S.REVIVED and not item.returned_at for item in isolates),
            "awaiting_endorsement_count": counts.get(S.ID_QUEUED, 0) + counts.get(S.SITE_BANKED, 0),
            "awaiting_review_count": sum((item.status in (S.ID_COMPLETE, S.ID_RETURNED) and not item.lyophilization_started_at) or (item.status == S.REVIVED and bool(item.returned_at)) for item in isolates),
            "awaiting_lyophilization_count": sum(item.status == S.ID_REVIEWED or (item.status in (S.ID_COMPLETE, S.ID_RETURNED) and bool(item.lyophilization_started_at)) for item in isolates),
            "awaiting_biobank_count": counts.get(S.READY_FOR_BIOBANK, 0),
            "completed_count": sum(item.status == S.NON_VIABLE or is_final_banked(item) for item in isolates),
        })
    return render(request, "laboratory/lab_worklist.html", {
        "batches": rows, "query": query, "selected_status": status,
        "area": area, "show_history": show_history, "scope_counts": scope_counts,
        "origin_url": request.get_full_path(),
        "worklist_url": "laboratory_processing_worklist" if area == "processing" else "laboratory_worklist",
        "statuses": LaboratoryIsolate.Status.choices,
        "can_test": can_access_main_lab(request.user),
        "can_review": can_review_id_results(request.user),
        "can_bank": can_manage_biobank(request.user),
    })


@login_required
@lab_worklist_required
def laboratory_batch_detail(request, accession_id):
    if can_access_main_lab(request.user) and not can_review_id_results(request.user):
        return redirect("main_lab_dashboard")
    accession = get_object_or_404(
        LaboratoryAccession.objects.select_related(
            "laboratory_receiving__receipt__site", "batch"
        ), pk=accession_id,
    )
    isolates = accession.isolate_records.prefetch_related(
        "identifications"
    ).order_by("accession_number", "pk")
    isolates = list(isolates)
    for isolate in isolates:
        isolate.next_action = next_action(isolate, request.user)
        try:
            isolate.reported_site_id = site_identification(isolate, require_code=False)
        except WorkflowError:
            isolate.reported_site_id = None
    return render(request, "laboratory/lab_batch_detail.html", {
        "accession": accession, "isolates": isolates,
        "show_history": request.GET.get("history") == "1",
        "area": request.GET.get("area") if request.GET.get("area") in ("identification", "processing", "biobank") else ("processing" if any(item.status not in ("id_pending", "id_testing") for item in isolates) else "identification"),
        "can_review": can_review_id_results(request.user),
        "can_test": can_access_main_lab(request.user),
        "id_handoff": id_handoff_readiness(accession, isolates),
        "return_to": return_url(request),
    })

@login_required
@lab_review_required
@require_POST
def laboratory_endorse_id(request, accession_id):
    """Endorse only isolates previously queued for Main Laboratory ID testing."""
    try:
        created = create_id_handoff(accession_id, request.user)
        messages.success(request, "The entire ready batch was endorsed from Laboratory Receiving to Main Laboratory for ID testing." if created else
                         "The batch has already been endorsed or closed.")
    except WorkflowError as exc:
        messages.error(request, str(exc))
    accession = get_object_or_404(LaboratoryAccession, pk=accession_id)
    return redirect("laboratory_receiving_workspace",
                    receiving_id=accession.laboratory_receiving.receipt.receiving_id)


@login_required
@lab_review_required
@require_POST
def laboratory_queue_id_batch(request, accession_id):
    accession = get_object_or_404(LaboratoryAccession, pk=accession_id)
    try:
        queue_for_id(accession.pk, request.user)
        messages.success(request, "Revived isolates queued for endorsement to Main Laboratory.")
    except WorkflowError as exc:
        messages.error(request, str(exc))
    return redirect("laboratory_receiving_workspace",
                    receiving_id=accession.laboratory_receiving.receipt.receiving_id)


@login_required
@lab_review_required
@require_POST
def laboratory_queue_main_lab(request, isolate_id):
    isolate = get_object_or_404(LaboratoryIsolate, pk=isolate_id)
    try:
        queue_for_id(isolate.accession_range_id, request.user, isolate_id=isolate.pk)
        messages.success(request, "Queued for endorsement at Bench A, not yet endorsed. Open the batch to endorse all ready accessions together; they will then appear in Main Laboratory's awaiting ID acknowledgment queue.")
    except WorkflowError as exc:
        messages.error(request, str(exc))
    return redirect("laboratory_isolate_detail", isolate_id=isolate.pk)


@login_required
@lab_staff_required
@require_POST
def laboratory_endorse_isolate(request, isolate_id):
    isolate = get_object_or_404(LaboratoryIsolate, pk=isolate_id)
    messages.warning(request, "Individual Main Lab endorsement is disabled. Finish revival and Site ID encoding for the batch, then endorse the entire batch from Receiving & Accessioning.")
    return redirect("laboratory_receiving_workspace",
                    receiving_id=isolate.accession_range.laboratory_receiving.receipt.receiving_id)


@login_required
@lab_worklist_required
@require_POST
def laboratory_bank_site_id(request, isolate_id):
    get_object_or_404(LaboratoryIsolate, pk=isolate_id)
    try:
        bank_using_site_id(isolate_id, request.user)
        messages.success(request, "Site ID selected. Record temporary storage; Main Lab ID testing will still be required.")
    except WorkflowError as exc:
        messages.error(request, str(exc))
    return redirect(detail_url(request, isolate_id))


@login_required
@lab_review_required
@require_POST
def laboratory_batch_transition(request, accession_id, action):
    try:
        changed = transition_batch(accession_id, action, request.user)
        messages.success(request, f"Batch transition '{action}' completed." if changed else
                         "Batch already at this workflow stage.")
    except WorkflowError as exc:
        messages.error(request, str(exc))
    accession = get_object_or_404(LaboratoryAccession, pk=accession_id)
    return redirect("laboratory_receiving_workspace",
                    receiving_id=accession.laboratory_receiving.receipt.receiving_id)


@login_required
@lab_review_required
@require_POST
def laboratory_isolate_revival(request, isolate_id, action):
    try:
        transition_isolate_revival(isolate_id, action, request.user)
        messages.success(request, f"Isolate revival action '{action}' completed.")
    except WorkflowError as exc:
        messages.error(request, str(exc))
    return redirect(detail_url(request, isolate_id))


@login_required
@lab_review_required
@require_POST
def laboratory_revival_failed(request, isolate_id):
    status = (request.POST.get("status") or "").strip()
    reason = (request.POST.get("reason") or "").strip()
    try:
        record_revival_failure(isolate_id, status, reason, request.user)
        messages.success(request, f"Revival outcome recorded: {status.replace('_', ' ')}.")
    except WorkflowError as exc:
        messages.error(request, str(exc))
    return redirect(detail_url(request, isolate_id))


@login_required
@lab_review_required
@require_POST
def laboratory_revival_retry(request, isolate_id):
    try:
        retry_revival(isolate_id, request.user)
        messages.success(request, "Revival retry started; previous failure reason remains recorded.")
    except WorkflowError as exc:
        messages.error(request, str(exc))
    return redirect(detail_url(request, isolate_id))


@login_required
@lab_review_required
@require_POST
def laboratory_revival_retry_complete(request, isolate_id):
    try:
        complete_revival_isolate(isolate_id, request.user)
        messages.success(request, "Retried isolate revived successfully.")
    except WorkflowError as exc:
        messages.error(request, str(exc))
    return redirect(detail_url(request, isolate_id))


@login_required
@lab_review_required
@require_POST
def laboratory_lyophilization_complete(request, isolate_id):
    try:
        complete_lyophilization(
            isolate_id, request.POST.get("date_harvested"),
            request.POST.get("date_lyophilized"), request.user,
            whonet_organism_name=(request.POST.get("whonet_organism_name") or "").strip(),
            whonet_code=(resolve_organism(
                code=request.POST.get("whonet_code") or "",
                name=request.POST.get("whonet_organism_name") or "",
                require_code=True,
            )[0] if request.POST.get("whonet_code") or request.POST.get("whonet_organism_name") else ""),
        )
        messages.success(request, "Harvest and lyophilization recorded.")
    except (WorkflowError, ValidationError) as exc:
        messages.error(request, "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc))
    return redirect(detail_url(request, isolate_id))


@login_required
@main_lab_required
@require_POST
def laboratory_acknowledge_id(request, isolate_id):
    get_object_or_404(LaboratoryIsolate, pk=isolate_id)
    isolate, received = acknowledge_id_receipt(isolate_id=isolate_id, user=request.user)
    if not received:
        messages.error(request, "Only pending isolates may be acknowledged.")
    else:
        messages.success(request, f"Received {isolate.accession_number} for ID testing.")
    return redirect(detail_url(request, isolate.pk, main_lab=True))


@login_required
@lab_worklist_required
def laboratory_isolate_detail(request, isolate_id):
    if can_access_main_lab(request.user) and not can_review_id_results(request.user):
        isolate_for_redirect = get_object_or_404(LaboratoryIsolate, pk=isolate_id)
        if isolate_for_redirect.endorsed_at or isolate_for_redirect.retrieved_at or isolate_for_redirect.status == "site_banked":
            return redirect("main_lab_isolate_detail", isolate_id=isolate_id)
        return redirect("main_lab_dashboard")
    isolate = get_object_or_404(
        LaboratoryIsolate.objects.select_related(
            "accession_range__laboratory_receiving__receipt__site", "reviewed_by"
        ).prefetch_related("identifications__recorded_by"), pk=isolate_id,
    )
    biobank = LaboratoryBiobank.objects.filter(isolate=isolate).first()
    siblings = list(isolate.accession_range.isolate_records.order_by(
        "accession_number", "pk"
    ))
    sibling_ids = [item.pk for item in siblings]
    index = sibling_ids.index(isolate.pk)
    attempt_start = isolate.revival_retried_at if isolate.revival_retry_count else isolate.revival_started_at
    if isolate.revival_retry_count and attempt_start and attempt_start.microsecond:
        attempt_start = attempt_start.replace(microsecond=0) + timedelta(seconds=1)
    site_id, site_id_error = None, ""
    try:
        site_id = site_identification(isolate, require_code=False)
    except WorkflowError as exc:
        site_id_error = str(exc)
    can_edit_site_id = can_review_id_results(request.user) and not (isolate.site_organism_code or isolate.site_organism_name) and not isolate.endorsed_at and isolate.status in (
        'revival_pending', 'revival_queued', 'revival_in_progress', 'revival_failed', 'revived', 'id_queued')
    return render(request, "laboratory/lab_isolate_detail.html", {
        "isolate": isolate, "biobank": biobank, "next_action": next_action(isolate, request.user),
        "return_to": return_url(request), "latest_result": isolate.identifications.first(),
        "can_correct_site": can_review_id_results(request.user) and bool(site_id) and not can_edit_site_id,
        "can_correct_final": can_review_id_results(request.user) and isolate.identification_source == "main_lab" and bool(isolate.final_organism),
        "show_history": request.GET.get("history") == "1",
        "area": request.GET.get("area") if request.GET.get("area") in ("identification", "processing", "biobank") else ("identification" if isolate.status in ("id_pending", "id_testing") else "processing"),
        "revival_attempt_start": attempt_start,
        "site_id": site_id, "site_id_error": site_id_error,
        "can_edit_site_id": can_edit_site_id,
        "previous_isolate_id": sibling_ids[index - 1] if index else None,
        "next_isolate_id": sibling_ids[index + 1] if index + 1 < len(sibling_ids) else None,
        "can_test": can_access_main_lab(request.user),
        "can_queue_id": can_manage_id_testing(request.user) or can_review_id_results(request.user),
        "can_review": can_review_id_results(request.user),
        "can_bank": can_manage_biobank(request.user),
        "id_handoff": id_handoff_readiness(isolate.accession_range, siblings)
            if isolate.status == LaboratoryIsolate.Status.ID_QUEUED else None,
    })


@login_required
@main_lab_required
@require_POST
@transaction.atomic
def laboratory_record_id(request, isolate_id):
    get_object_or_404(LaboratoryIsolate, pk=isolate_id)
    try:
        record_identification(isolate_id, request.POST.get("organism_reported_code"),
            request.POST.get("organism_reported"), request.user,
            instrument=(request.POST.get("instrument") or "").strip(),
            cassette_reference=(request.POST.get("cassette_reference") or "").strip())
        messages.success(request, "ID result saved. Testing is complete; Bench A can proceed to lyophilization. Physical cassette return has not been recorded.")
    except WorkflowError as exc:
        messages.error(request, str(exc))
    return redirect(detail_url(request, isolate_id, main_lab=True))


@login_required
@main_lab_required
@require_POST
@transaction.atomic
def laboratory_return_id(request, isolate_id):
    from .identification_workflow import record_physical_return
    get_object_or_404(LaboratoryIsolate, pk=isolate_id)
    try:
        record_physical_return(isolate_id, request.user)
        messages.success(request, "Physical cassette return recorded. ID availability and scientific stages are unchanged.")
    except WorkflowError as exc:
        messages.error(request, str(exc))
    return redirect(detail_url(request, isolate_id, main_lab=True))


@login_required
@lab_review_required
@require_POST
@transaction.atomic
def laboratory_review_id(request, isolate_id):
    isolate = get_object_or_404(LaboratoryIsolate.objects.select_for_update(), pk=isolate_id)
    organism = (request.POST.get("final_organism") or "").strip()
    code = (request.POST.get("final_organism_code") or "").strip()
    # Only validate code/name when the review is at the correct workflow stage.
    approved_code = ""
    can_confirm = (isolate.status == LaboratoryIsolate.Status.ID_RETURNED and isolate.revival_completed_at) or (
        isolate.status == LaboratoryIsolate.Status.REVIVED and (isolate.returned_at or isolate.identifications.exists())
    )
    if can_confirm:
        if request.POST.get("action") == "accept_reported" or request.POST.get("review_choice") == "accept_reported":
            latest = isolate.identifications.first()
            if latest is None:
                messages.error(request, "A reported identification is required before approval.")
                return redirect("laboratory_isolate_detail", isolate_id=isolate.pk)
            organism, code = latest.organism_reported, latest.whonet_organism_code
        try:
            approved_code, organism = resolve_organism(code=code, name=organism)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return redirect("laboratory_isolate_detail", isolate_id=isolate.pk)
    if not can_confirm or not isolate.identifications.exists():
        messages.error(request, "Main Laboratory must return the identification and cassette before confirmation.")
    elif not organism or len(organism) > 255:
        messages.error(request, "An approved organism is required (maximum 255 characters).")
    else:
        isolate.final_organism = organism
        isolate.approved_organism_code = approved_code
        isolate.status = LaboratoryIsolate.Status.ID_REVIEWED
        isolate.identification_source = "main_lab"
        isolate.reviewed_by = request.user
        isolate.reviewed_at = timezone.now()
        isolate.save(update_fields=("final_organism", "approved_organism_code", "status", "reviewed_by", "reviewed_at", "identification_source"))
        messages.success(request, "Identification reviewed and approved.")
    return redirect("laboratory_isolate_detail", isolate_id=isolate.pk)


@login_required
@lab_worklist_required
@require_POST
def laboratory_biobank_save(request, isolate_id):
    if not can_manage_biobank(request.user):
        messages.error(request, "You do not have permission to update biobank inventory.")
        return redirect(detail_url(request, isolate_id))
    limits = {
        "lyo_box_name": 100, "lyo_box_number": 50,
        "lyo_position": 50, "refrigerator_number": 50, "lyo_location": 100,
        "supplementary_box_name": 100, "supplementary_box_number": 50,
        "supplementary_position": 50, "freezer_number": 50,
        "supplementary_location": 100, "preservation_method": 100, "container": 100,
        "storage_location": 150, "storage_box": 100, "box_position": 30,
    }
    details = {key: (request.POST.get(key) or "").strip() for key in limits}
    if any(len(details[key]) > maximum for key, maximum in limits.items()):
        messages.error(request, "One or more biobank fields exceed their maximum length.")
        return redirect(detail_url(request, isolate_id))
    details["remarks"] = (request.POST.get("remarks") or "").strip()
    for field in ("lyo_date_encoded", "supplementary_date_encoded"):
        value = (request.POST.get(field) or "").strip()
        if value:
            try:
                date = datetime.strptime(value, "%Y-%m-%d").date()
            except ValueError:
                messages.error(request, f"Enter a valid {field.replace('_', ' ')}.")
                return redirect(detail_url(request, isolate_id))
            if date > timezone.localdate():
                messages.error(request, "Date encoded cannot be in the future.")
                return redirect(detail_url(request, isolate_id))
            details[field] = date
        else:
            details[field] = None
    details["skimmed_milk"] = request.POST.get("skimmed_milk") == "on"
    details["tsbg"] = request.POST.get("tsbg") == "on"
    try:
        item = get_object_or_404(LaboratoryIsolate, pk=isolate_id)
        if item.status == LaboratoryIsolate.Status.READY_FOR_BIOBANK and item.identification_source == "main_lab" and item.lyophilization_started_at:
            finalize_banking(isolate_id, details, request.POST.get("final_organism_code"),
                             request.POST.get("final_organism"), request.user)
        else:
            save_biobank(isolate_id, details, request.user)
        item = LaboratoryIsolate.objects.get(pk=isolate_id)
        messages.success(request, "Temporary Site ID storage saved; Main Lab confirmation is still required."
                         if item.status == LaboratoryIsolate.Status.SITE_BANKED
                         else "Identification and banking map finalized.")
    except (WorkflowError, ValueError) as exc:
        messages.error(request, str(exc))
    return redirect(detail_url(request, isolate_id))


def _label_item(isolate, request):
    """Render eligible biobank stickers with an accession scan-and-receive link."""
    try:
        bank = isolate.biobank
    except LaboratoryBiobank.DoesNotExist:
        bank = None
    if not bank or not bank.date_harvested or not bank.whonet_code.strip() or not isolate.final_organism:
        return None
    if isolate.status not in {
        LaboratoryIsolate.Status.READY_FOR_BIOBANK, LaboratoryIsolate.Status.SITE_BANKED, LaboratoryIsolate.Status.BIOBANKED
    }:
        return None
    png = render_qr_png(build_isolate_scan_url(isolate=isolate, request=request))
    return {
        "isolate": isolate,
        "code": bank.whonet_code.strip().upper(),
        "harvest": bank.date_harvested.strftime("%m/%d/%Y"),
        "qr_data_uri": "data:image/png;base64," + base64.b64encode(png).decode("ascii"),
    }


@login_required
@lab_worklist_required
def laboratory_biobank_label(request, isolate_id):
    isolate = get_object_or_404(LaboratoryIsolate, pk=isolate_id)
    label = _label_item(isolate, request)
    if not label:
        messages.error(request, "A reviewed organism, WHONET code, and harvest date are required for sticker printing.")
        return redirect("laboratory_isolate_detail", isolate_id=isolate.pk)
    if request.GET.get("format") == "pdf":
        return _sticker_pdf_response([label], f"isolate-{isolate.pk}-stickers.pdf")
    return render(request, "laboratory/lab_biobank_label.html", {
        "labels": [label], "unready": [], "batch": None,
        "accession": isolate.accession_range,
    })


@login_required
@lab_worklist_required
def laboratory_batch_labels(request, accession_id):
    accession = get_object_or_404(
        LaboratoryAccession.objects.select_related("laboratory_receiving__receipt"),
        pk=accession_id,
    )
    labels, unready = [], []
    for isolate in accession.isolate_records.order_by("accession_number"):
        label = _label_item(isolate, request)
        if label:
            labels.append(label)
        else:
            unready.append(isolate.accession_number)
    if request.GET.get("format") == "pdf":
        if not labels:
            messages.error(request, "No isolates in this batch are eligible for sticker printing yet.")
            return redirect("laboratory_batch_labels", accession_id=accession.pk)
        return _sticker_pdf_response(labels, f"batch-{accession.pk}-stickers.pdf")
    return render(request, "laboratory/lab_biobank_label.html", {
        "labels": labels, "unready": unready, "batch": accession,
        "accession": accession,
    })


@login_required
@lab_worklist_required
def laboratory_date_labels(request):
    form = StickerDateRangeForm(request.GET)
    if not form.is_valid():
        context = _biobank_inventory_context(request)
        context["sticker_date_form"] = form
        return render(request, "laboratory/lab_biobank_list.html", context, status=400)

    start, end = form.cleaned_data["from_date"], form.cleaned_data["to_date"]
    # DateField range lookup includes both endpoints; no inventory display limit.
    isolates = LaboratoryIsolate.objects.select_related("biobank").filter(
        biobank__date_harvested__range=(start, end),
    ).order_by("biobank__date_harvested", "accession_number", "pk")
    labels, unready = [], []
    for isolate in isolates:
        label = _label_item(isolate, request)
        if label:
            labels.append(label)
        else:
            unready.append(isolate.accession_number)
    params = {"from_date": start.isoformat(), "to_date": end.isoformat()}
    if request.GET.get("format") == "pdf":
        if not labels:
            return redirect(reverse("laboratory_date_labels") + "?" + urlencode(params))
        return _sticker_pdf_response(labels, f"harvest-{start.isoformat()}-to-{end.isoformat()}-stickers.pdf")
    return render(request, "laboratory/lab_biobank_label.html", {
        "labels": labels, "unready": unready, "date_range": True,
        "from_date": start, "to_date": end,
        "pdf_url": "?" + urlencode({**params, "format": "pdf"}),
    })


def _sticker_pdf_response(labels, filename):
    from .sticker_pdf import render_sticker_pdf
    response = HttpResponse(render_sticker_pdf(labels), content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response


def _upload_rows(upload):
    if upload.size > MAX_UPLOAD_BYTES:
        raise ValueError("Maximum upload size is 2 MB.")
    name = upload.name.lower()
    if name.endswith(".csv"):
        try:
            data = upload.read().decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("CSV files must be UTF-8 encoded.") from exc
        reader = csv.DictReader(StringIO(data))
        headings = reader.fieldnames or []
        rows = list(islice(reader, MAX_ROWS + 1))
    elif name.endswith(".xlsx"):
        from openpyxl import load_workbook
        from openpyxl.utils.exceptions import InvalidFileException
        try:
            workbook = load_workbook(BytesIO(upload.read()), read_only=True, data_only=True)
        except (BadZipFile, InvalidFileException, ValueError, OSError) as exc:
            raise ValueError("Invalid Excel workbook.") from exc
        try:
            values = workbook.active.iter_rows(values_only=True)
            headings = [str(v or "").strip() for v in next(values, [])]
            rows = [dict(zip(headings, row)) for row in islice(values, MAX_ROWS + 1)]
        finally:
            workbook.close()
    else:
        raise ValueError("Use a .csv or .xlsx identification file.")
    if not set(UPLOAD_COLUMNS[:2]).issubset(set(headings)):
        raise ValueError("Required headers: accession_number, organism_reported.")
    if len(rows) > MAX_ROWS:
        raise ValueError("Uploads are limited to 1,000 rows.")
    normalized = []
    for number, row in enumerate(rows, start=2):
        values = {key: str(row.get(key) or "").strip() for key in UPLOAD_COLUMNS}
        if not values["accession_number"] or not values["organism_reported"]:
            raise ValueError(f"Row {number}: accession and organism are required.")
        try:
            _resolved_code, canonical_name = resolve_organism(
                code=values["organism_code"], name=values["organism_reported"]
            )
            values["organism_reported"] = canonical_name
            values["organism_code"] = _resolved_code
        except ValidationError as exc:
            raise ValueError(f"Row {number}: " + "; ".join(exc.messages)) from exc
        if len(values["accession_number"]) > 80 or len(values["organism_reported"]) > 255:
            raise ValueError(f"Row {number}: accession or organism exceeds its length limit.")
        if len(values["instrument"]) > 100 or len(values["cassette_reference"]) > 100:
            raise ValueError(f"Row {number}: instrument or cassette reference too long.")
        normalized.append(values)
    if not normalized:
        raise ValueError("The uploaded file contains no ID results.")
    return normalized


@login_required
@main_lab_required
def laboratory_id_upload(request):
    if request.method != "POST":
        return render(request, "laboratory/lab_id_upload.html", {})
    file = request.FILES.get("file")
    if not file:
        messages.error(request, "Choose a CSV or Excel file.")
        return redirect("laboratory_id_upload")
    if file.size > MAX_UPLOAD_BYTES:
        messages.error(request, "Maximum upload size is 2 MB.")
        return redirect("laboratory_id_upload")
    digest = sha256(file.read()).hexdigest()
    file.seek(0)
    if request.POST.get("action") == "commit" and request.session.get("lab_id_preview_digest") != digest:
        messages.error(request, "Preview this exact file before importing it.")
        return redirect("laboratory_id_upload")
    try:
        rows = _upload_rows(file)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("laboratory_id_upload")
    accession_numbers = {row["accession_number"] for row in rows}
    isolates = {
        isolate.accession_number: isolate
        for isolate in LaboratoryIsolate.objects.filter(accession_number__in=accession_numbers)
    }
    problems = [
        row["accession_number"] for row in rows
        if row["accession_number"] not in isolates
        or isolates[row["accession_number"]].status != LaboratoryIsolate.Status.ID_TESTING
    ]
    if problems:
        messages.error(request, "Unknown or not-in-testing accessions: " + ", ".join(problems[:8]))
        return redirect("laboratory_id_upload")

    if request.POST.get("action") != "commit":
        request.session["lab_id_preview_digest"] = digest
        return render(request, "laboratory/lab_id_upload.html", {
            "rows": rows, "preview": True,
            "warning": "Review the records before saving. Upload the same file again to confirm."
        })

    # Recheck inside the transaction before writing; original instrument records
    # remain separate and are never overwritten.
    with transaction.atomic():
        list(LaboratoryAccession.objects.select_for_update().filter(
            pk__in=LaboratoryIsolate.objects.filter(accession_number__in=accession_numbers).values('accession_range_id')
        ).order_by('pk'))
        locked = {
            isolate.accession_number: isolate
            for isolate in LaboratoryIsolate.objects.select_for_update().filter(
                accession_number__in=accession_numbers
            )
        }
        if any(number not in locked or locked[number].status != LaboratoryIsolate.Status.ID_TESTING
               for number in accession_numbers):
            messages.error(request, "One or more isolate statuses changed. No results were imported.")
            return redirect("laboratory_id_upload")
        try:
            with transaction.atomic():
                for row in rows:
                    record_identification(locked[row["accession_number"]].pk,
                        row["organism_code"], row["organism_reported"], request.user,
                        instrument=row["instrument"], cassette_reference=row["cassette_reference"],
                        source=(LaboratoryIdentification.Source.CSV if file.name.lower().endswith(".csv")
                                else LaboratoryIdentification.Source.XLSX))
        except WorkflowError as exc:
            messages.error(request, str(exc) + " No results were imported.")
            return redirect("laboratory_id_upload")
    request.session.pop("lab_id_preview_digest", None)
    messages.success(request, f"Imported {len(rows)} identification results. Testing complete; results are available to Bench A.")
    return redirect(reverse("main_lab_dashboard") + "?tab=completed")


@login_required
@lab_worklist_required
def laboratory_processing_worklist(request):
    return laboratory_worklist(request, area="processing")


@login_required
@lab_review_required
@require_POST
def laboratory_revival_outcome(request, isolate_id):
    try:
        record_revival_outcome(isolate_id, request.POST.get("status"),
            request.POST.get("started_at"), request.POST.get("completed_at"),
            request.POST.get("reason"), request.user,
            site_code=request.POST.get("site_organism_code"), site_name=request.POST.get("site_organism_name"))
        messages.success(request, "Revival outcome recorded.")
    except WorkflowError as exc:
        messages.error(request, str(exc))
    return redirect(detail_url(request, isolate_id))


@login_required
@lab_review_required
@require_POST
def laboratory_site_id_save(request, isolate_id):
    get_object_or_404(LaboratoryIsolate, pk=isolate_id)
    try:
        record_site_identification(isolate_id, request.POST.get("site_organism_code"),
                                   request.POST.get("site_organism_name"), request.user, reason=request.POST.get("reason", ""))
        messages.success(request, "Site-reported identification saved for this accession.")
    except WorkflowError as exc:
        messages.error(request, str(exc))
    return redirect(detail_url(request, isolate_id))
