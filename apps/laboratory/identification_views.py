"""Simplified laboratory transition and scientific-correction endpoints."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .identification_workflow import retrieve_for_testing, proceed_to_lyophilization, correct_identification
from .models import LaboratoryIsolate
from .navigation import detail_url, return_url
from .permissions import main_lab_required, lab_review_required, lab_worklist_required, can_access_main_lab
from .workflow import WorkflowError


@login_required
@lab_worklist_required
def laboratory_storage_detail(request, isolate_id):
    item = get_object_or_404(LaboratoryIsolate.objects.select_related('biobank'), pk=isolate_id,
        status__in=('site_banked', 'biobanked', 'ready_for_biobank'), biobank__isnull=False)
    return render(request, 'laboratory/storage_detail.html', {
        'isolate': item, 'biobank': item.biobank, 'return_to': return_url(request),
        'storage_only': True,
    })


@login_required
@main_lab_required
@require_POST
def laboratory_retrieve_for_testing(request, isolate_id):
    get_object_or_404(LaboratoryIsolate, pk=isolate_id)
    try:
        retrieve_for_testing(isolate_id, request.user)
        messages.success(request, 'Retrieved from temporary storage. Retrieval recorded; testing is now in progress.')
    except WorkflowError as exc:
        messages.error(request, str(exc))
        return redirect(return_url(request) or 'laboratory_biobank_list')
    return redirect(detail_url(request, isolate_id, main_lab=True))


@login_required
@lab_review_required
@require_POST
def laboratory_proceed_lyophilization(request, isolate_id):
    get_object_or_404(LaboratoryIsolate, pk=isolate_id)
    try:
        proceed_to_lyophilization(isolate_id, request.user)
        messages.success(request, 'Main Lab Final ID is available. Record harvest and lyophilization, then enter storage details.')
    except WorkflowError as exc:
        messages.error(request, str(exc))
    return redirect(detail_url(request, isolate_id))


@login_required
@lab_worklist_required
@require_POST
def laboratory_identification_correct(request, isolate_id):
    get_object_or_404(LaboratoryIsolate, pk=isolate_id)
    kind = request.POST.get('kind')
    try:
        correct_identification(isolate_id, kind, request.POST.get('organism_code'),
            request.POST.get('organism_name'), request.POST.get('reason'), request.user,
            result_id=request.POST.get('result_id') or None)
        messages.success(request, 'Identification corrected. Previous and new values are preserved in history.')
    except WorkflowError as exc:
        messages.error(request, str(exc))
        return redirect(detail_url(request, isolate_id, main_lab=kind == 'main_lab' and can_access_main_lab(request.user)))
    return redirect(return_url(request) or detail_url(request, isolate_id,
        main_lab=kind == 'main_lab' and can_access_main_lab(request.user)))
