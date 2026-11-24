"""Unified receiving dashboard for new batch-level handoffs.

Courier intake and laboratory worksheets remain in their existing applications.
Custody transitions do not replace scientific checking or report approval.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.home.permissions import ROLE_ADMIN
from apps.receiving.permissions import receiving_read_required
from .handoffs import (
    DESTINATION_ROLES, confirm_batch_receipt, custody_roles,
    endorse_batch, undo_batch_endorsement,
)
from .models import BatchCustody, BatchCustodyEvent

TABS = (
    ("pending", "Pending"),
    ("received", "Received"),
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
        return queryset.filter(pending_role="") if ROLE_ADMIN in roles else queryset.filter(current_role__in=roles, pending_role="")
    if tab == "endorsed":
        return queryset.filter(events__action=BatchCustodyEvent.Action.ENDORSED).distinct() if ROLE_ADMIN in roles else queryset.filter(
            events__action=BatchCustodyEvent.Action.ENDORSED,
            events__from_role__in=roles,
        ).distinct()
    if tab == "completed":
        return queryset.filter(batch__tat_entry__tat_Status_Release="Released")
    return queryset


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
    years = sorted(
        {value.year for value in base.exclude(batch__bat_Referral_Date__isnull=True).dates("batch__bat_Referral_Date", "year")}
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

    counts = {key: _tab_filter(base, key, request.user).count() for key, _ in TABS}
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
            custody.pending_role and can_act_as_receiver
            and (ROLE_ADMIN in roles or custody.endorsed_by_id != request.user.pk)
        )
        custody.can_undo = bool(
            custody.pending_role and (
                ROLE_ADMIN in roles or custody.endorsed_by_id == request.user.pk
            )
        )
        custody.can_endorse = bool(
            not custody.pending_role and can_act_as_holder
        )

    return render(request, "receiving/workflow_dashboard.html", {
        "page_obj": page_obj,
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
    return render(request, "receiving/workflow_detail.html", {
        "custody": custody, "events": events,
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
