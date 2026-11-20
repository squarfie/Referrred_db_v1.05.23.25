from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from .dashboard import render_receiving_dashboard as _render_receiving_dashboard
from .forms import ReferralReceiptForm
from .models import ReferralReceipt
from .permissions import (can_read_laboratory_receiving, can_confirm_lab_receipt, can_permanently_delete_receipt, admin_receiving_delete_required, can_manage_secretariat_receiving, receiving_read_required, secretariat_receiving_required)
from .qr import build_receipt_detail_url, render_qr_png
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
@secretariat_receiving_required
def secretariat_receiving_dashboard(request):
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
    # This legacy URL is Secretariat-facing. Laboratory personnel have
    # a separate receipt detail that does not expose Secretariat actions.
    receipt = _receipt_or_404(receiving_id)
    # Preserve existing QR/bookmark links but route laboratory-only accounts
    # to their dedicated detail page rather than Secretariat controls.
    if (can_read_laboratory_receiving(request.user)
            and not can_manage_secretariat_receiving(request.user)):
        return redirect("laboratory_receiving_detail", receiving_id=receiving_id)
    events = receipt.events.select_related("performed_by").order_by(
        "occurred_at", "recorded_at", "id"
    )
    can_manage = can_manage_secretariat_receiving(request.user)
    return render(
        request,
        "receiving/receipt_detail.html",
        {
            "receipt": receipt,
            "events": events,
            "can_print_label": can_manage,
            "can_permanently_delete": can_delete_receipt_safely(receipt, request.user),
            "can_endorse": can_manage and receipt.status == ReferralReceipt.Status.RECEIVED,
            "can_confirm_lab": False,
            "can_work_lab": False,
            "laboratory_detail": False,
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
@admin_receiving_delete_required
@require_POST
def receiving_delete_permanently(request, receiving_id):
    """Delete mistaken courier receipts with server-enforced custody safeguards.

    Secretariat may correct records before Laboratory Receiving confirms
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



# Backwards-compatible imports; laboratory views are owned by apps.laboratory.
from apps.laboratory.receiving_views import (
    laboratory_receiving_dashboard,
    laboratory_receiving_workspace,
    laboratory_accession_add,
    laboratory_accession_edit,
    laboratory_accession_delete,
    laboratory_organism_matrix_save,
    laboratory_organism_add,
    laboratory_batch_create,
    laboratory_receiving_register,
    laboratory_receiving_register_pdf,
    laboratory_receiving_detail,
    receiving_lab_confirm,
)
