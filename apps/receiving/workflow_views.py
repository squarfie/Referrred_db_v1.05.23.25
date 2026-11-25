"""Unified receiving dashboard for new batch-level handoffs.

Courier intake and laboratory worksheets remain in their existing applications.
Custody transitions do not replace scientific checking or report approval.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, F, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.home.permissions import ROLE_ADMIN, ROLE_SECRETARIAT
from apps.laboratory.permissions import can_read_laboratory_receiving, can_confirm_lab_receipt
from apps.receiving.permissions import receiving_read_required
from .handoffs import (
    DESTINATION_ROLES, confirm_batch_receipt, custody_roles,
    endorse_batch, undo_batch_endorsement,
)
from .models import BatchCustody, BatchCustodyEvent, ReferralReceipt

TABS = (
    ("pending", "Pending"),
    ("received", "Received"),
    ("processing", "Processing"),
    ("endorsed", "Endorsed"),
    ("completed", "Completed"),
    ("all", "All Records"),
)


def visible_custodies(user):
    roles = custody_roles(user)
    records = BatchCustody.objects.select_related("batch", "endorsed_by")
    if ROLE_ADMIN in roles:
        return records
    return records.filter(
        Q(current_role__in=roles)
        | Q(pending_role__in=roles)
        | Q(events__from_role__in=roles)
        | Q(events__to_role__in=roles)
    ).distinct()


def _tab_filter(queryset, tab, user):
    roles = custody_roles(user)
    if tab == "pending":
        return queryset.exclude(pending_role="") if ROLE_ADMIN in roles else queryset.filter(pending_role__in=roles).exclude(pending_role="")
    if tab == "received":
        # A generated batch is already in its downstream data workflow.
        # Received is reserved for newly received physical courier shipments
        # that are awaiting Secretariat endorsement.
        return queryset.none()
    if tab == "processing":
        active = queryset.filter(
            pending_role=""
        ).exclude(
            Q(batch__tat_entry__tat_Status_Release="Released")
            | Q(batch__verification_cases__status="released")
        ).distinct()
        return active if ROLE_ADMIN in roles else active.filter(current_role__in=roles)
    if tab == "endorsed":
        return queryset.filter(events__action=BatchCustodyEvent.Action.ENDORSED).distinct() if ROLE_ADMIN in roles else queryset.filter(
            events__action=BatchCustodyEvent.Action.ENDORSED,
            events__from_role__in=roles,
        ).distinct()
    if tab == "completed":
        # Only explicitly released records belong in the Completed tab.
        # Laboratory work completion and Head signing do not equal release.
        return queryset.filter(
            Q(batch__tat_entry__tat_Status_Release="Released")
            | Q(batch__verification_cases__status="released")
        ).distinct()
    return queryset


def visible_receipts(user):
    """Existing physical receipts, never cloned into batch custody.

    Lab Encoders receive the laboratory queue; Secretariat receives courier
    intake records. Other workflow roles see only their authorized batches.
    """
    roles = custody_roles(user)
    qs = ReferralReceipt.objects.select_related("site", "received_by", "endorsed_by")
    if ROLE_ADMIN in roles:
        return qs
    if can_read_laboratory_receiving(user):
        return qs.filter(status__in=(
            ReferralReceipt.Status.ENDORSED, ReferralReceipt.Status.LAB_RECEIVED,
        ))
    if ROLE_SECRETARIAT in roles:
        return qs
    return qs.none()


def endorseable_receipt_custodies(receipt, user):
    """Only linked batches the current user can actually endorse.

    A confirmed courier shipment may have no generated batches yet. Its
    laboratory-processing queue is not itself a transferable data batch.
    """
    roles = custody_roles(user)
    queryset = visible_custodies(user).filter(
        batch__laboratory_accession__laboratory_receiving__receipt=receipt,
        pending_role="",
    ).select_related("batch")
    if ROLE_ADMIN not in roles:
        queryset = queryset.filter(current_role__in=roles)
    return queryset.exclude(
        Q(batch__tat_entry__tat_Status_Release="Released")
        | Q(batch__verification_cases__status="released")
    ).distinct()


def _receipt_release_counts(receipts):
    """Use recorded release results, not lab completion, for courier progress."""
    return receipts.annotate(
        tracked_ranges=Count(
            "laboratory_receiving__accession_ranges", distinct=True
        ),
        released_ranges=Count(
            "laboratory_receiving__accession_ranges",
            filter=(
                Q(laboratory_receiving__accession_ranges__batch__tat_entry__tat_Status_Release="Released")
                | Q(laboratory_receiving__accession_ranges__batch__verification_cases__status="released")
            ),
            distinct=True,
        ),
    )


def _receipt_tab_filter(receipts, tab, user):
    roles = custody_roles(user)
    lab = can_read_laboratory_receiving(user)
    if tab == "pending":
        # Physical Laboratory receipt is the pending incoming endorsement.
        return receipts.filter(status=ReferralReceipt.Status.ENDORSED) if lab else receipts.none()
    if tab == "received":
        # An unendorsed courier delivery is in Secretariat custody.
        # Laboratory confirmation advances it to Processing, not Received.
        if ROLE_ADMIN in roles or ROLE_SECRETARIAT in roles:
            return receipts.filter(status=ReferralReceipt.Status.RECEIVED)
        return receipts.none()
    if tab == "processing":
        # A laboratory-confirmed shipment can contain multiple isolate ranges.
        # Keep it here until every generated batch is officially released.
        # If no batch has been generated, processing is still ongoing.
        return _receipt_release_counts(
            receipts.filter(status=ReferralReceipt.Status.LAB_RECEIVED)
        ).filter(Q(tracked_ranges=0) | Q(tracked_ranges__gt=F("released_ranges")))
    if tab == "endorsed":
        if ROLE_SECRETARIAT in roles or ROLE_ADMIN in roles:
            return receipts.filter(status__in=(
                ReferralReceipt.Status.ENDORSED, ReferralReceipt.Status.LAB_RECEIVED,
            ))
        return receipts.none()
    if tab == "completed":
        # All linked accession ranges must have an officially released batch.
        # A receipt without accession ranges is not complete.
        return _receipt_release_counts(
            receipts.filter(status=ReferralReceipt.Status.LAB_RECEIVED)
        ).filter(tracked_ranges__gt=0, tracked_ranges=F("released_ranges"))
    return receipts


@login_required
@receiving_read_required
def workflow_dashboard(request):
    """Role-scoped batch custody dashboard; original courier intake stays available."""
    tab = (request.GET.get("tab") or "pending").strip()
    if tab not in dict(TABS):
        tab = "pending"
    per_page_str = (request.GET.get("per_page") or "25").strip()
    per_page = int(per_page_str) if per_page_str in ("10", "25", "50") else 25
    query = (request.GET.get("q") or "").strip()
    current_year = timezone.localdate().year
    base = visible_custodies(request.user)
    physical_receipts = visible_receipts(request.user)
    years = sorted(
        {value.year for value in base.exclude(batch__bat_Referral_Date__isnull=True).dates("batch__bat_Referral_Date", "year")}
        | {value.year for value in physical_receipts.dates("date_received", "year")}
        | {current_year},
        reverse=True,
    )
    year_param = (request.GET.get("year") or "").strip()
    selected_year = current_year
    if year_param == "all":
        selected_year = "all"
    elif year_param.isascii() and year_param.isdecimal() and int(year_param) in years:
        selected_year = int(year_param)
    if selected_year != "all":
        base = base.filter(batch__bat_Referral_Date__year=selected_year)

    # Physical courier and Bench A receipts are surfaced alongside batch
    # handoffs. A receipt exists before a downstream batch can be generated.
    receipt_base = visible_receipts(request.user)
    if selected_year != "all":
        receipt_base = receipt_base.filter(date_received__year=selected_year)
    receipt_counts = {
        key: _receipt_tab_filter(receipt_base, key, request.user).count()
        for key, _ in TABS
    }
    courier_rows = _receipt_tab_filter(receipt_base, tab, request.user)
    if query:
        courier_rows = courier_rows.filter(
            Q(tracking_number__icontains=query)
            | Q(site__SiteCode__icontains=query)
            | Q(site__SiteName__icontains=query)
        )
    courier_page_obj = Paginator(
        courier_rows.order_by("-created_at", "-pk"), per_page,
    ).get_page(request.GET.get("courier_page"))
    is_lab_receiving = can_read_laboratory_receiving(request.user)
    for receipt in courier_page_obj:
        receipt.can_confirm_unified = (
            tab == "pending"
            and is_lab_receiving
            and can_confirm_lab_receipt(request.user)
            and receipt.status == ReferralReceipt.Status.ENDORSED
        )
        # The receipt may contain multiple isolates/batches. The Details page
        # chooses the appropriate endorsement target without treating a whole
        # physical shipment as a single scientific data batch.
        receipt.can_endorse_unified = (
            tab in {"received", "processing"}
            and (
                (
                    receipt.status == ReferralReceipt.Status.RECEIVED
                    and (ROLE_SECRETARIAT in custody_roles(request.user) or ROLE_ADMIN in custody_roles(request.user))
                )
                or (
                    receipt.status == ReferralReceipt.Status.LAB_RECEIVED
                    and is_lab_receiving
                    and bool(endorseable_receipt_custodies(receipt, request.user))
                )
            )
        )
    counts = {
        key: _tab_filter(base, key, request.user).count() + receipt_counts[key]
        for key, _ in TABS
    }
    rows = _tab_filter(base, tab, request.user)
    if query:
        rows = rows.filter(
            Q(batch__bat_Batch_Code__icontains=query)
            | Q(batch__bat_SiteCode__icontains=query)
            | Q(batch__bat_RefNo__icontains=query)
        )
    page_obj = Paginator(rows.order_by("-updated_at", "-pk"), per_page).get_page(request.GET.get("page"))
    roles = custody_roles(request.user)
    for custody in page_obj:
        can_act_as_holder = ROLE_ADMIN in roles or custody.current_role in roles
        can_act_as_receiver = ROLE_ADMIN in roles or custody.pending_role in roles
        custody.can_confirm = bool(
            tab == "pending"
            and custody.pending_role and can_act_as_receiver
            and (ROLE_ADMIN in roles or custody.endorsed_by_id != request.user.pk)
        )
        custody.can_undo = bool(
            tab == "endorsed"
            and custody.pending_role and (
                ROLE_ADMIN in roles or custody.endorsed_by_id == request.user.pk
            )
        )
        custody.can_endorse = bool(
            tab == "processing" and not custody.pending_role and can_act_as_holder
            and not (
                custody.batch.verification_cases.filter(status="released").exists()
                or getattr(getattr(custody.batch, "tat_entry", None), "tat_Status_Release", None) == "Released"
            )
        )

    return render(request, "receiving/workflow_dashboard.html", {
        "page_obj": page_obj,
        "courier_page_obj": courier_page_obj,
        "courier_count": courier_page_obj.paginator.count,
        "is_lab_receiving": is_lab_receiving,
        "can_create_receipt": bool(request.user.is_authenticated),
        "tabs": [{"key": key, "label": label, "count": counts[key], "active": key == tab} for key, label in TABS],
        "active_tab": tab,
        "active_tab_label": dict(TABS)[tab],
        "q": query,
        "per_page": per_page,
        "allowed_page_sizes": (10, 25, 50),
        "selected_year": selected_year,
        "available_years": years,
        "destination_roles": DESTINATION_ROLES,
        "is_admin": ROLE_ADMIN in roles,
    })


@login_required
@receiving_read_required
def workflow_detail(request, batch_id):
    custody = get_object_or_404(visible_custodies(request.user), batch_id=batch_id)
    events = custody.events.select_related("performed_by").order_by("performed_at", "pk")
    activity = [
        {
            "when": event.performed_at,
            "label": event.get_action_display(),
            "actor": event.performed_by,
            "description": (
                f"{event.from_role} → {event.to_role}" if event.to_role else event.from_role
            ) + (f" — {event.remarks}" if event.remarks else ""),
            "source": "Custody",
        }
        for event in events
    ]
    linked_accession = getattr(custody.batch, "laboratory_accession", None)
    receipt = linked_accession.laboratory_receiving.receipt if linked_accession else None
    if receipt:
        for event in receipt.events.select_related("performed_by").order_by("occurred_at", "pk"):
            activity.append({
                "when": event.occurred_at,
                "label": event.get_event_type_display(),
                "actor": event.performed_by,
                "description": event.remarks or receipt.tracking_number,
                "source": "Courier and Laboratory",
            })
    verification_cases = custody.batch.verification_cases.select_related(
        "assigned_checker", "assigned_verifier", "assigned_lab_manager", "assigned_head"
    )
    verification_events = (
        ("sent_to_verifier_at", "Submitted for verification", "sent_by_checker"),
        ("verifier_decision_at", "Verifier decision recorded", "verifier_decision_by"),
        ("sent_to_lab_manager_at", "Submitted to Lab Manager", "sent_to_lab_manager_by"),
        ("lab_manager_decision_at", "Lab Manager decision recorded", "lab_manager_decision_by"),
        ("sent_to_head_at", "Submitted to Head", "sent_to_head_by"),
        ("head_decision_at", "Head decision recorded", "head_decision_by"),
        ("signed_by_head_at", "Signed by Head", "signed_by_head"),
    )
    for case in verification_cases:
        for date_field, label, actor_field in verification_events:
            when = getattr(case, date_field, None)
            if when:
                activity.append({
                    "when": when, "label": label,
                    "actor": getattr(case, actor_field, None),
                    "description": case.get_status_display(),
                    "source": "Verification",
                })
        if case.released_at:
            activity.append({
                "when": case.released_at, "label": "Released",
                "actor": None, "description": "Official release recorded",
                "source": "Verification",
            })
    activity.sort(key=lambda entry: entry["when"])
    roles = custody_roles(request.user)
    is_holder = ROLE_ADMIN in roles or custody.current_role in roles
    is_released = (
        custody.batch.verification_cases.filter(status="released").exists()
        or getattr(getattr(custody.batch, "tat_entry", None), "tat_Status_Release", None) == "Released"
    )
    return render(request, "receiving/workflow_detail.html", {
        "custody": custody, "activity": activity,
        "receipt": receipt,
        "destination_roles": DESTINATION_ROLES,
        "can_endorse_here": bool(is_holder and not custody.pending_role and not is_released),
    })


def _transition(request, batch_id, action):
    get_object_or_404(visible_custodies(request.user), batch_id=batch_id)
    try:
        if action == "endorse":
            destination_role = (request.POST.get("destination_role") or "").strip()
            endorse_batch(
                batch_id=batch_id, user=request.user, destination_role=destination_role,
                remarks=(request.POST.get("remarks") or "").strip(),
            )
            messages.success(request, "Batch endorsed. The recipient must confirm receipt.")
        elif action == "confirm":
            confirm_batch_receipt(batch_id=batch_id, user=request.user)
            messages.success(request, "Receipt confirmed.")
        elif action == "undo":
            undo_batch_endorsement(
                batch_id=batch_id, user=request.user,
                remarks=(request.POST.get("remarks") or "").strip(),
            )
            messages.success(request, "Unconfirmed endorsement recalled.")
        else:
            raise Http404
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    # PermissionDenied intentionally returns HTTP 403.
    return redirect("receiving_workflow_detail", batch_id=batch_id)


@login_required
@receiving_read_required
@require_POST
def workflow_endorse(request, batch_id):
    return _transition(request, batch_id, "endorse")


@login_required
@receiving_read_required
@require_POST
def workflow_confirm(request, batch_id):
    return _transition(request, batch_id, "confirm")


@login_required
@receiving_read_required
@require_POST
def workflow_undo(request, batch_id):
    return _transition(request, batch_id, "undo")
