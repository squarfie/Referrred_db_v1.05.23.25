"""Locked, audited isolate workflow transitions.

Do not infer a completed physical step from a browser print or an ID upload.
All state mutations must pass through these services, never just a UI check.
"""
from datetime import datetime

from django.db import transaction
from django.utils import timezone

from .models import LaboratoryAccession, LaboratoryBiobank, LaboratoryIsolate


class WorkflowError(ValueError):
    pass


S = LaboratoryIsolate.Status


@transaction.atomic
def queue_for_revival(accession_id, user):
    range_obj = LaboratoryAccession.objects.select_for_update().select_related(
        "laboratory_receiving__receipt__site"
    ).get(pk=accession_id)
    receipt = range_obj.laboratory_receiving.receipt
    if receipt.status != "lab_received" or range_obj.laboratory_receiving.isolates_accepted <= 0:
        raise WorkflowError("Laboratory custody and an accepted-isolate decision are required.")
    existing = list(LaboratoryIsolate.objects.select_for_update().filter(accession_range=range_obj))
    if existing:
        if len(existing) == range_obj.isolate_count:
            return False
        raise WorkflowError("This accession range has incomplete isolate records; reconcile before retrying.")
    accession_numbers = range_obj.accession_numbers
    if LaboratoryIsolate.objects.filter(accession_number__in=accession_numbers).exists():
        raise WorkflowError("Accession numbers already exist elsewhere; no isolates were created.")
    now = timezone.now()
    LaboratoryIsolate.objects.bulk_create([
        LaboratoryIsolate(accession_range=range_obj, accession_number=number,
                          status=S.REVIVAL_QUEUED, revival_queued_by=user, revival_queued_at=now)
        for number in accession_numbers
    ])
    return True


BATCH_TRANSITIONS = {
    "start": (S.REVIVAL_QUEUED, S.REVIVAL_IN_PROGRESS, "revival_started_by", "revival_started_at"),
    "complete": (S.REVIVAL_IN_PROGRESS, S.REVIVED, "revival_completed_by", "revival_completed_at"),
    "endorse": (S.REVIVED, S.ID_PENDING, "endorsed_by", "endorsed_at"),
}


@transaction.atomic
def transition_batch(accession_id, action, user):
    if action not in BATCH_TRANSITIONS:
        raise WorkflowError("Unknown laboratory transition.")
    range_obj = LaboratoryAccession.objects.select_for_update().get(pk=accession_id)
    isolates = list(LaboratoryIsolate.objects.select_for_update()
                    .filter(accession_range=range_obj).order_by("pk"))
    if len(isolates) != range_obj.isolate_count:
        raise WorkflowError("Create all isolate records before advancing this accession range.")
    expected, target, actor_field, at_field = BATCH_TRANSITIONS[action]
    if all(item.status == target for item in isolates):
        return False
    # Completing a batch after a retry can include already-revived isolates.
    # Never overwrite their original completion actor and timestamp.
    accepted = (expected, target) if action == "complete" else (expected,)
    if any(item.status not in accepted for item in isolates):
        raise WorkflowError(
            "Every isolate in the accession range must be at the expected stage. "
            "A failed, non-viable, or unreconciled isolate blocks the entire batch."
        )
    pending = [item for item in isolates if item.status == expected]
    now = timezone.now()
    for item in pending:
        item.status = target
        setattr(item, actor_field, user)
        setattr(item, at_field, now)
    if pending:
        LaboratoryIsolate.objects.bulk_update(pending, ["status", actor_field, at_field])
    return True


@transaction.atomic
def record_revival_failure(isolate_id, status, reason, user):
    if status not in (S.REVIVAL_FAILED, S.NON_VIABLE):
        raise WorkflowError("Select Revival failed or Non-viable.")
    if not reason or not reason.strip():
        raise WorkflowError("A reason is required for unsuccessful revival.")
    if len(reason.strip()) > 1000:
        raise WorkflowError("Revival failure reason cannot exceed 1,000 characters.")
    base = LaboratoryIsolate.objects.select_related("accession_range").get(pk=isolate_id)
    LaboratoryAccession.objects.select_for_update().get(pk=base.accession_range_id)
    item = LaboratoryIsolate.objects.select_for_update().get(pk=isolate_id)
    if item.status not in (S.REVIVAL_QUEUED, S.REVIVAL_IN_PROGRESS):
        raise WorkflowError("An unsuccessful revival can only be recorded before revival completes.")
    item.status = status
    item.revival_failed_reason = (item.revival_failed_reason + "\\n" if item.revival_failed_reason else "") + reason.strip()
    item.revival_completed_by = user
    item.revival_completed_at = timezone.now()
    item.save(update_fields=["status", "revival_failed_reason",
                             "revival_completed_by", "revival_completed_at"])
    return item


@transaction.atomic
def retry_revival(isolate_id, user):
    """Restart a failed revival without silently reopening a non-viable isolate.

    A retry enters revival_in_progress. Other isolates in the range must
    also be in progress or failed, to avoid creating an impossible batch.
    """
    item = LaboratoryIsolate.objects.select_related("accession_range").get(pk=isolate_id)
    LaboratoryAccession.objects.select_for_update().get(pk=item.accession_range_id)
    item = LaboratoryIsolate.objects.select_for_update().get(pk=isolate_id)
    if item.status != S.REVIVAL_FAILED:
        raise WorkflowError("Only Revival failed can be retried. Non-viable is closed.")
    siblings = LaboratoryIsolate.objects.select_for_update().filter(accession_range_id=item.accession_range_id)
    permitted = (S.REVIVAL_IN_PROGRESS, S.REVIVAL_FAILED, S.REVIVED)
    if siblings.exclude(status__in=permitted).exists():
        raise WorkflowError("Retry requires the entire batch to be in the active or failed revival stage.")
    item.status = S.REVIVAL_IN_PROGRESS
    item.revival_retry_count += 1
    item.revival_retried_by = user
    item.revival_retried_at = timezone.now()
    item.save(update_fields=("status", "revival_retry_count", "revival_retried_by", "revival_retried_at"))
    return item


@transaction.atomic
def complete_revival_isolate(isolate_id, user):
    """Complete retried isolate without prematurely advancing other isolates."""
    item = LaboratoryIsolate.objects.select_related("accession_range").get(pk=isolate_id)
    LaboratoryAccession.objects.select_for_update().get(pk=item.accession_range_id)
    item = LaboratoryIsolate.objects.select_for_update().get(pk=isolate_id)
    if item.status != S.REVIVAL_IN_PROGRESS or item.revival_retry_count < 1:
        raise WorkflowError("Only retried, in-progress isolates may be completed individually.")
    item.status = S.REVIVED
    item.revival_completed_by = user
    item.revival_completed_at = timezone.now()
    item.save(update_fields=("status", "revival_completed_by", "revival_completed_at"))
    return item



def _valid_date(value, title):
    if not value:
        raise WorkflowError(f"{title} is required.")
    try:
        day = datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError) as exc:
        raise WorkflowError(f"{title} must be a valid date.") from exc
    if day > timezone.localdate():
        raise WorkflowError(f"{title} cannot be in the future.")
    return day


@transaction.atomic
def complete_lyophilization(isolate_id, harvested, lyophilized, user, whonet_code=""):
    item = LaboratoryIsolate.objects.select_for_update().select_related(
        "accession_range__laboratory_receiving__receipt"
    ).get(pk=isolate_id)
    if item.status != S.ID_REVIEWED or not item.final_organism:
        raise WorkflowError("Only reviewed identifications may proceed to lyophilization.")
    harvest = _valid_date(harvested, "Harvest date")
    lyoph = _valid_date(lyophilized, "Lyophilization date")
    date_received = item.accession_range.laboratory_receiving.receipt.lab_date_received
    if date_received and min(harvest, lyoph) < date_received:
        raise WorkflowError("Scientific dates cannot precede laboratory receipt.")
    if harvest > lyoph:
        raise WorkflowError("Harvest date cannot be later than lyophilization date.")
    code = whonet_code.strip().upper()
    if not code:
        raise WorkflowError("WHONET organism code is required for the isolate label.")
    if len(code) > 30:
        raise WorkflowError("WHONET code exceeds 30 characters.")
    bank, _ = LaboratoryBiobank.objects.select_for_update().get_or_create(isolate=item)
    bank.date_harvested = harvest
    bank.date_lyophilized = lyoph
    bank.whonet_code = code
    bank.recorded_by = user
    bank.save()
    item.status = S.READY_FOR_BIOBANK
    item.lyophilization_completed_at = timezone.now()
    item.lyophilization_completed_by = user
    item.save(update_fields=["status", "lyophilization_completed_at", "lyophilization_completed_by"])
    return item


@transaction.atomic
def save_biobank(isolate_id, details, user):
    item = LaboratoryIsolate.objects.select_for_update().get(pk=isolate_id)
    if item.status not in (S.READY_FOR_BIOBANK, S.BIOBANKED):
        raise WorkflowError("Complete ID review and lyophilization before recording biobank storage.")
    position = (details.get("box_position") or details.get("lyo_position")
                or details.get("supplementary_position") or "").strip()
    if not position:
        raise WorkflowError("Position in box is required before the isolate is biobanked.")
    bank, _ = LaboratoryBiobank.objects.select_for_update().get_or_create(isolate=item)
    # Scientific dates belong to lyophilization; storage edits must not rewrite them.
    blocked = {"date_harvested", "date_lyophilized", "whonet_code"}
    if blocked.intersection(details):
        raise WorkflowError("Harvest and lyophilization dates cannot be edited from storage.")
    for name, value in details.items():
        setattr(bank, name, value)
    bank.recorded_by = user
    bank.full_clean()
    bank.save()
    if item.status == S.READY_FOR_BIOBANK:
        item.status = S.BIOBANKED
        item.biobanked_at = timezone.now()
        item.biobanked_by = user
        item.save(update_fields=["status", "biobanked_at", "biobanked_by"])
    return bank
