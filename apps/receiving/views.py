from apps.laboratory.permissions import can_read_laboratory_receiving
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from .dashboard import render_receiving_dashboard as _render_receiving_dashboard
from .activity import receipt_activity
from .forms import ReferralReceiptForm
from .models import ReferralReceipt
from .handoffs import custody_roles, DESTINATION_ROLES
from .workflow_views import endorseable_receipt_custodies
from apps.home.permissions import ROLE_ADMIN, ROLE_LAB_ENCODER
from .permissions import (can_permanently_delete_receipt, admin_receiving_delete_required, can_manage_secretariat_receiving, receiving_read_required, secretariat_receiving_required)
from .qr import build_receipt_scan_url, render_qr_png
from .services import (InvalidReceivingTransition, create_receipt, delete_receipt_permanently, can_delete_receipt_safely, endorse_receipt)

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
    """Preserve old links and role-aware defaults for existing bookmarks."""
    return _render_receiving_dashboard(request)


@login_required
@receiving_read_required
def secretariat_receiving_dashboard(request):
    """Shared receiving history and intake for all authenticated users."""
    return _render_receiving_dashboard(request, queue="secretariat")


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
    roles = custody_roles(request.user)
    # An endorsement is available only for a linked batch currently owned
    # by this role; a physical courier receipt alone cannot stand in for it.
    available_endorsements = list(
        endorseable_receipt_custodies(receipt, request.user)
    )
    can_endorse_receipt = can_manage and receipt.status == ReferralReceipt.Status.RECEIVED
    # Preserve the originating unified Receiving tab on the detail back link.
    from_tab = (request.GET.get("from_tab") or "").strip()
    if from_tab not in {"pending", "received", "processing", "endorsed", "completed", "all"}:
        if receipt.status == ReferralReceipt.Status.ENDORSED:
            # The incoming laboratory acknowledgment is the LAB Encoder's
            # Pending queue. Courier intake permissions are shared among
            # authenticated users, so they cannot determine this default.
            from_tab = "pending" if can_read_laboratory_receiving(request.user) else "endorsed"
        elif receipt.status == ReferralReceipt.Status.LAB_RECEIVED:
            from_tab = "processing"
        else:
            from_tab = "received"
    return render(
        request,
        "receiving/receipt_detail.html",
        {
            "receipt": receipt,
            "return_tab": from_tab,
            "events": events,
            "activity": receipt_activity(receipt),
            "can_print_label": can_manage,
            "can_permanently_delete": can_delete_receipt_safely(receipt, request.user),
            "can_endorse": can_endorse_receipt,
            "linked_endorsements": available_endorsements,
            "destination_roles": DESTINATION_ROLES,
            "can_show_endorsement": can_endorse_receipt or (
                receipt.status == ReferralReceipt.Status.LAB_RECEIVED
                and bool(available_endorsements)
            ),
            "can_confirm_lab": False,
            "can_work_lab": False,
            "laboratory_detail": False,
        },
    )


@login_required
@secretariat_receiving_required
def receiving_qr(request, receiving_id):
    receipt = _receipt_or_404(receiving_id)
    payload = build_receipt_scan_url(receipt=receipt, request=request)
    response = HttpResponse(render_qr_png(payload), content_type="image/png")
    response["Cache-Control"] = "private, no-cache"
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
    # Legacy POSTs have no destination value; new forms specify the only
    # valid courier-level destination. Do not silently accept a forged role.
    requested_role = (request.POST.get("destination_role") or "").strip()
    if requested_role and requested_role != ROLE_LAB_ENCODER:
        messages.error(request, "Courier receipts may only be endorsed to Laboratory Receiving.")
        return redirect("receiving_detail", receiving_id=receiving_id)
    try:
        endorse_receipt(receipt_id=receipt.pk, user=request.user)
    except InvalidReceivingTransition as exc:
        messages.warning(request, str(exc))
    else:
        messages.success(request, "Courier batch endorsed to the laboratory.")
    return redirect("receiving_detail", receiving_id=receiving_id)


@login_required
@admin_receiving_delete_required
@require_POST
def receiving_delete_permanently(request, receiving_id):
    """Delete mistaken courier receipts with server-enforced custody safeguards.

    Authorized receiving users may correct records before Laboratory Receiving confirms
    custody. Admins may also clean up laboratory-received test records if no
    accession or other dependent laboratory work exists.
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
    messages.success(request, "Receiving record permanently deleted.")
    return redirect("secretariat_receiving_dashboard")
