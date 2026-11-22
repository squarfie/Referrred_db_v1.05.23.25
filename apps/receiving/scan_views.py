"""QR landing pages prepare an automatic, CSRF-protected receipt POST."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.laboratory.permissions import can_confirm_lab_receipt, can_read_laboratory_receiving
from .models import ReferralReceipt
from .permissions import receiving_read_required
from .services import InvalidReceivingTransition, confirm_lab_receipt
from .views import _receipt_or_404


@login_required
@receiving_read_required
@never_cache
@require_http_methods(["GET", "POST"])
def receiving_scan(request, receiving_id):
    receipt = _receipt_or_404(receiving_id)
    if receipt.status == ReferralReceipt.Status.LAB_RECEIVED:
        destination = "laboratory_receiving_workspace" if can_read_laboratory_receiving(request.user) else "receiving_detail"
        return redirect(destination, receiving_id=receiving_id)
    if not can_confirm_lab_receipt(request.user):
        messages.warning(request, "Only authorized Laboratory Receiving personnel may receive this courier batch.")
        return redirect("receiving_detail", receiving_id=receiving_id)
    if receipt.status != ReferralReceipt.Status.ENDORSED:
        messages.warning(request, "This batch must be endorsed to the laboratory before it can be received.")
        return redirect("receiving_detail", receiving_id=receiving_id)
    if request.method == "POST":
        received_at = timezone.localtime(timezone.now())
        try:
            confirm_lab_receipt(
                receipt_id=receipt.pk, user=request.user,
                lab_date_received=received_at.date(), lab_time_received=received_at.time(),
            )
        except InvalidReceivingTransition as exc:
            # A simultaneous scan may already have received the same batch.
            receipt.refresh_from_db()
            if receipt.status != ReferralReceipt.Status.LAB_RECEIVED:
                messages.warning(request, str(exc))
                return redirect("receiving_detail", receiving_id=receiving_id)
        else:
            messages.success(request, "Batch received. Your account and receipt time have been recorded.")
        return redirect("laboratory_receiving_workspace", receiving_id=receiving_id)
    return render(request, "receiving/scan_receive.html", {
        "scan_title": "Receive courier batch", "scan_identifier": receipt.tracking_number,
        "scan_description": "Recording laboratory custody, then opening the laboratory worksheet.",
        "scan_action": request.path,
    })
