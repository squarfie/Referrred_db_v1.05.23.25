"""Locked, audited isolate workflow transitions. Main Lab identification precedes revival.

Do not infer a completed physical step from a browser print or an ID upload.
All state mutations must pass through these services, never just a UI check.
"""
from datetime import datetime

from django.core.exceptions import ValidationError
from .organism_lookup import resolve_organism
from django.db import transaction
from django.utils import timezone

from .models import LaboratoryAccession, LaboratoryBiobank, LaboratoryIsolate


class WorkflowError(ValueError):
    pass


S = LaboratoryIsolate.Status


@transaction.atomic
def create_id_handoff(accession_id, user):
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
                          status=S.ID_PENDING, endorsed_by=user, endorsed_at=now)
        for number in accession_numbers
    ])
    return True


BATCH_TRANSITIONS = {
    # ID results and cassettes must be returned for *all* isolates before
    # Laboratory Receiving can queue revival.
    "queue": (S.ID_RETURNED, S.REVIVAL_QUEUED, "revival_queued_by", "revival_queued_at"),
    "start": (S.REVIVAL_QUEUED, S.REVIVAL_IN_PROGRESS, "revival_started_by", "revival_started_at"),
    "complete": (S.REVIVAL_IN_PROGRESS, S.REVIVED, "revival_completed_by", "revival_completed_at"),
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
    if action == "queue" and any(
        item.returned_at is None
        or item.returned_by_id is None
        or not item.identifications.filter(cassette_reference__gt="").exists()
        for item in isolates
    ):
        raise WorkflowError("Main Laboratory must return ID results and cassette references for every isolate before revival.")
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
def transition_isolate_revival(isolate_id, action, user):
    """Progress an independently returned ID cassette through revival."""
    transitions = {
        "queue": (S.ID_RETURNED, S.REVIVAL_QUEUED, "revival_queued_by", "revival_queued_at"),
        "start": (S.REVIVAL_QUEUED, S.REVIVAL_IN_PROGRESS, "revival_started_by", "revival_started_at"),
        "complete": (S.REVIVAL_IN_PROGRESS, S.REVIVED, "revival_completed_by", "revival_completed_at"),
    }
    if action not in transitions:
        raise WorkflowError("Unknown isolate revival transition.")
    candidate = LaboratoryIsolate.objects.only("accession_range_id").get(pk=isolate_id)
    LaboratoryAccession.objects.select_for_update().get(pk=candidate.accession_range_id)
    item = LaboratoryIsolate.objects.select_for_update().get(pk=isolate_id)
    expected, target, actor_field, at_field = transitions[action]
    if item.status != expected:
        raise WorkflowError("Isolate is not at the expected stage for this revival action.")
    if (
        item.returned_at is None
        or item.returned_by_id is None
        or not item.identifications.filter(cassette_reference__gt="").exists()
    ):
        raise WorkflowError("A recorded ID result and authenticated cassette return are required.")
    item.status = target
    setattr(item, actor_field, user)
    setattr(item, at_field, timezone.now())
    item.save(update_fields=["status", actor_field, at_field])
    return item


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
    # Revival retries are independent of sibling isolates still in ID testing.
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
    code = (whonet_code or item.approved_organism_code).strip().upper()
    if item.approved_organism_code and code != item.approved_organism_code.upper():
        raise WorkflowError(
            "The selected WHONET code differs from the approved identification code."
        )
    if not code:
        raise WorkflowError("WHONET organism code is required for the isolate label.")
    if len(code) > 30:
        raise WorkflowError("WHONET code exceeds 30 characters.")
    try:
        # A sticker must never silently assign a different organism than the
        # identification approved by Laboratory Receiving.
        resolve_organism(code=code, name=item.final_organism, require_code=True)
    except ValidationError as exc:
        raise WorkflowError("Sticker organism does not match approved identification: " + "; ".join(exc.messages)) from exc
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


def _actual_datetime(value, title):
    from django.utils.dateparse import parse_datetime
    try:
        stamp = parse_datetime(value or '')
    except (TypeError, ValueError):
        stamp = None
    if stamp is None:
        raise WorkflowError(f'{title} must be a valid date and time.')
    if timezone.is_naive(stamp):
        stamp = timezone.make_aware(stamp)
    if stamp > timezone.now():
        raise WorkflowError(f'{title} cannot be in the future.')
    return stamp


@transaction.atomic
def record_revival_outcome(isolate_id, status, started_at, completed_at, reason, user):
    """Record actual physical work without overwriting earlier audit timestamps."""
    if status not in (S.REVIVED, S.REVIVAL_FAILED, S.NON_VIABLE):
        raise WorkflowError('Select a revival outcome.')
    start = _actual_datetime(started_at, 'Revival start')
    end = _actual_datetime(completed_at, 'Revival completion')
    if end < start:
        raise WorkflowError('Revival completion cannot precede its start.')
    reason = (reason or '').strip()
    if status != S.REVIVED and (not reason or len(reason) > 1000):
        raise WorkflowError('An unsuccessful revival requires a reason of at most 1,000 characters.')
    base = LaboratoryIsolate.objects.only('accession_range_id').get(pk=isolate_id)
    LaboratoryAccession.objects.select_for_update().get(pk=base.accession_range_id)
    item = LaboratoryIsolate.objects.select_for_update().get(pk=isolate_id)
    if item.status not in (S.ID_RETURNED, S.REVIVAL_QUEUED, S.REVIVAL_IN_PROGRESS):
        raise WorkflowError('Isolate is not ready for a revival outcome.')
    if not item.returned_at or not item.returned_by_id or not item.identifications.filter(cassette_reference__gt='').exists():
        raise WorkflowError('A recorded ID result and authenticated cassette return are required.')
    if start < item.returned_at:
        raise WorkflowError('Revival cannot precede cassette return.')
    if item.revival_started_at and not item.revival_retry_count and start.replace(second=0, microsecond=0) != item.revival_started_at.replace(second=0, microsecond=0):
        raise WorkflowError('Use the recorded revival start time.')
    if item.revival_started_at and end < item.revival_started_at:
        raise WorkflowError('Revival completion cannot precede the recorded start.')
    if item.revival_retried_at and start < item.revival_retried_at:
        raise WorkflowError('Retry work cannot precede the recorded retry.')
    fields = ['status']
    item.status = status
    for field, value in [('revival_started_at', start), ('revival_started_by', user), ('revival_completed_at', end), ('revival_completed_by', user)]:
        if getattr(item, field) is None:
            setattr(item, field, value)
            fields.append(field)
    entry = f'{status}: started {start.isoformat()}; completed {end.isoformat()}; recorded by {user.get_username()}'
    if reason:
        entry += f'; reason: {reason}'
    item.revival_failed_reason = '\n'.join(filter(None, [item.revival_failed_reason, entry]))
    fields.append('revival_failed_reason')
    item.save(update_fields=fields)
    return item
