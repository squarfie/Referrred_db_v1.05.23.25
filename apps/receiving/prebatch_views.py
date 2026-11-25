"""Actions for a laboratory accession range before its Raw Data batch exists."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.home.services.batch_creation import BatchCreationError
from .prebatch import confirm_dmu_accession, create_dmu_batch


@login_required
@require_POST
def confirm_dmu_handoff(request, handoff_id):
    try:
        confirm_dmu_accession(handoff_id=handoff_id, user=request.user)
    except (ValidationError, PermissionDenied) as exc:
        if isinstance(exc, PermissionDenied):
            raise
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, "Accession range received by DMU. You can now create its Raw Data batch.")
    return redirect(reverse("receiving_workflow_dashboard") + "?tab=received")


@login_required
@require_POST
def create_dmu_handoff_batch(request, handoff_id):
    try:
        result = create_dmu_batch(handoff_id=handoff_id, user=request.user)
    except PermissionDenied:
        raise
    except (ValidationError, BatchCreationError) as exc:
        detail = "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
        messages.error(request, detail)
        return redirect("/receiving/workflow/?tab=received")
    messages.success(request, f"DMU created Raw Data batch {result.batch.bat_Batch_Code}.")
    return redirect(reverse("receiving_workflow_dashboard") + "?tab=processing")
