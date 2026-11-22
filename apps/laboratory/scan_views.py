"""Accession QR endpoints receive pending ID handoffs and open the current step."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from apps.receiving.qr import build_isolate_scan_url, render_qr_png
from .guidance import next_action
from .models import LaboratoryIsolate
from .permissions import can_access_main_lab, lab_worklist_required
from .workflow import acknowledge_id_receipt


def _current_step(isolate, user):
    url = reverse("main_lab_isolate_detail" if can_access_main_lab(user) and isolate.endorsed_at else "laboratory_isolate_detail", args=[isolate.pk])
    return redirect(url + "#" + next_action(isolate, user)["section"])


@login_required
@lab_worklist_required
@never_cache
@require_http_methods(["GET", "POST"])
def laboratory_isolate_scan(request, accession_number):
    isolate = get_object_or_404(LaboratoryIsolate, accession_number=accession_number)
    if isolate.status != LaboratoryIsolate.Status.ID_PENDING:
        return _current_step(isolate, request.user)
    if not can_access_main_lab(request.user):
        messages.warning(request, "Only authorized Main Laboratory personnel may receive this isolate for ID testing.")
        return _current_step(isolate, request.user)
    if request.method == "POST":
        isolate, received = acknowledge_id_receipt(isolate_id=isolate.pk, user=request.user)
        if received:
            messages.success(request, f"Received {isolate.accession_number} for ID testing. Your account and receipt time have been recorded.")
        return _current_step(isolate, request.user)
    return render(request, "receiving/scan_receive.html", {
        "scan_title": "Receive isolate for ID testing", "scan_identifier": isolate.accession_number,
        "scan_description": "Recording Main Laboratory receipt, then opening the current isolate process.",
        "scan_action": request.path,
    })


@login_required
@lab_worklist_required
@never_cache
@require_GET
def laboratory_isolate_qr(request, isolate_id):
    isolate = get_object_or_404(LaboratoryIsolate, pk=isolate_id)
    return HttpResponse(render_qr_png(build_isolate_scan_url(isolate=isolate, request=request)), content_type="image/png")


@login_required
@lab_worklist_required
@require_GET
def laboratory_isolate_scan_label(request, isolate_id):
    isolate = get_object_or_404(LaboratoryIsolate, pk=isolate_id)
    return render(request, "laboratory/lab_accession_label.html", {"isolate": isolate})
