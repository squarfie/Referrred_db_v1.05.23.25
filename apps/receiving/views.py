from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import LabReceiptForm, ReferralReceiptForm
from .models import ReferralReceipt
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
from .services import (
    InvalidReceivingTransition,
    confirm_lab_receipt,
    create_receipt,
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

    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip()
    valid_statuses = {choice for choice, _label in ReferralReceipt.Status.choices}
    receipts = all_receipts
    if q:
        receipts = receipts.filter(
            Q(tracking_number__icontains=q)
            | Q(site__SiteCode__icontains=q)
            | Q(site__SiteName__icontains=q)
        )
    if status in valid_statuses:
        receipts = receipts.filter(status=status)
    else:
        status = ""

    page_obj = Paginator(receipts, 25).get_page(request.GET.get("page"))
    return render(
        request,
        "receiving/dashboard.html",
        {
            "page_obj": page_obj,
            "q": q,
            "status": status,
            "status_choices": ReferralReceipt.Status.choices,
            "received_count": counts.get(ReferralReceipt.Status.RECEIVED, 0),
            "endorsed_count": counts.get(ReferralReceipt.Status.ENDORSED, 0),
            "lab_received_count": counts.get(ReferralReceipt.Status.LAB_RECEIVED, 0),
            "can_manage_secretariat": can_manage_secretariat_receiving(request.user),
        },
    )


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
            "can_permanently_delete": can_permanently_delete_receipt(request.user),
            "can_endorse": can_manage and receipt.status == ReferralReceipt.Status.RECEIVED,
            "can_confirm_lab": can_confirm
            and receipt.status == ReferralReceipt.Status.ENDORSED,
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
                messages.success(request, "Laboratory receipt confirmed. Custody recorded.")
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
def receiving_delete_permanently(request, receiving_id):
    """Administrators may irreversibly remove a mistakenly entered courier batch.

    Existing ReceivingEvent rows cascade away; no deletion-history event is created.
    This does not delete independent Raw/Final/Data or accessioning records.
    """
    receipt = _receipt_or_404(receiving_id)
    if request.method == "POST":
        if request.POST.get("confirmation", "").strip() != "DELETE PERMANENTLY":
            messages.error(request, "Type DELETE PERMANENTLY to confirm.")
        else:
            with transaction.atomic():
                receipt.delete()
            messages.success(request, "Receiving batch permanently deleted.")
            return redirect("receiving_dashboard")
    return render(request, "receiving/confirm_permanent_delete.html", {"receipt": receipt})
