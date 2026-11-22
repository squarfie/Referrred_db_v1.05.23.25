"""Inline additional organism rows, scoped and limited to an accession range."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from .forms import AdditionalOrganismForm
from .models import LaboratoryAccession, LaboratoryAdditionalOrganismCount
from .permissions import lab_receiving_required
from .reports import REGISTER_COLUMNS


@login_required
@lab_receiving_required
@require_POST
@transaction.atomic
def inventory_row_save(request, accession_id=None, row_id=None):
    candidate = get_object_or_404(LaboratoryAdditionalOrganismCount, pk=row_id) if row_id else None
    accession = get_object_or_404(LaboratoryAccession.objects.select_for_update(),
        pk=candidate.accession_range_id if candidate else accession_id)
    item = get_object_or_404(LaboratoryAdditionalOrganismCount, pk=row_id) if row_id else LaboratoryAdditionalOrganismCount(accession_range=accession)
    response = redirect("laboratory_receiving_workspace", receiving_id=accession.laboratory_receiving.receipt.receiving_id)
    response["Location"] += "#organism-inventory"
    counts = {}
    for code, _short, _label in REGISTER_COLUMNS:
        value = (request.POST.get(f"organism_{code}") or "").strip()
        if value and (not value.isascii() or not value.isdecimal() or len(value) > 9):
            messages.error(request, f"{code}: enter a non-negative whole number.")
            return response
        counts[code] = int(value) if value else 0
    item.inventory_counts = counts
    item.count = sum(counts.values())
    try:
        item.full_clean()
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return response
    item.save()
    messages.success(request, "Organism inventory row saved.")
    return response


@login_required
@lab_receiving_required
@require_POST
@transaction.atomic
def additional_organism_save(request, accession_id=None, row_id=None):
    existing = get_object_or_404(LaboratoryAdditionalOrganismCount, pk=row_id) if row_id else None
    accession = get_object_or_404(LaboratoryAccession.objects.select_for_update(),
        pk=existing.accession_range_id if existing else accession_id)
    if row_id:
        existing = get_object_or_404(LaboratoryAdditionalOrganismCount, pk=row_id)
    item = existing or LaboratoryAdditionalOrganismCount(accession_range=accession)
    form = AdditionalOrganismForm(request.POST, instance=item)
    if form.is_valid():
        form.save()
        messages.success(request, "Additional organism row saved within this accession range.")
    else:
        messages.error(request, " ".join(str(error) for errors in form.errors.values() for error in errors))
    response = redirect("laboratory_receiving_workspace", receiving_id=accession.laboratory_receiving.receipt.receiving_id)
    response["Location"] += "#organism-inventory"
    return response


@login_required
@lab_receiving_required
@require_POST
@transaction.atomic
def additional_organism_remove(request, row_id):
    item = get_object_or_404(LaboratoryAdditionalOrganismCount, pk=row_id)
    accession = get_object_or_404(LaboratoryAccession.objects.select_for_update(), pk=item.accession_range_id)
    item.delete()
    response = redirect("laboratory_receiving_workspace", receiving_id=accession.laboratory_receiving.receipt.receiving_id)
    response["Location"] += "#organism-inventory"
    return response
