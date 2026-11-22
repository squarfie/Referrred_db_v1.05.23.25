"""Locked, audited isolate workflow transitions. Revival precedes ID disposition.

Do not infer a completed physical step from a browser print or an ID upload.
All state mutations must pass through these services, never just a UI check.
"""
from datetime import datetime

from django.core.exceptions import ValidationError
from .organism_lookup import resolve_organism
from django.db import transaction
from django.utils import timezone

from .models import LaboratoryAccession, LaboratoryBiobank, LaboratoryIsolate, ReceivingEvent
from .permissions import can_manage_id_testing, can_manage_biobank, can_review_id_results


class WorkflowError(ValueError):
    pass


S = LaboratoryIsolate.Status


@transaction.atomic
def acknowledge_id_receipt(*, isolate_id, user):
    if not can_manage_id_testing(user):
        raise WorkflowError("Only authorized Main Laboratory personnel may receive an isolate for ID testing.")
    candidate = LaboratoryIsolate.objects.only("accession_range_id").get(pk=isolate_id)
    accession = LaboratoryAccession.objects.select_for_update().get(pk=candidate.accession_range_id)
    isolate = LaboratoryIsolate.objects.select_for_update().get(pk=isolate_id)
    if isolate.status != S.ID_PENDING:
        return isolate, False
    received_at = timezone.now()
    isolate.status = S.ID_TESTING
    isolate.acknowledged_by = user
    isolate.acknowledged_at = received_at
    isolate.save(update_fields=("status", "acknowledged_by", "acknowledged_at"))
    ReceivingEvent.objects.create(
        receipt_id=accession.laboratory_receiving.receipt_id,
        event_type=ReceivingEvent.EventType.ID_RECEIVED, performed_by=user,
        occurred_at=received_at,
        remarks=f"Received isolate {isolate.accession_number} for ID testing.",
    )
    return isolate, True


@transaction.atomic
def prepare_revival(accession_id, user):
    """Create accession records for actual revival; do not queue or endorse ID work."""
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
    LaboratoryIsolate.objects.bulk_create([
        LaboratoryIsolate(accession_range=range_obj, accession_number=number,
                          status=S.REVIVAL_PENDING)
        for number in accession_numbers
    ])
    return True


def _advance_id_handoff(accession_id, user, isolate_id, *, endorse):
    """Keep queue selection separate from physical endorsement to Main Lab."""
    if not (can_manage_id_testing(user) if endorse else
            (can_manage_id_testing(user) or can_review_id_results(user))):
        raise WorkflowError("You do not have permission to perform this Main Laboratory handoff action.")
    accession = LaboratoryAccession.objects.select_for_update().select_related(
        "laboratory_receiving__receipt"
    ).get(pk=accession_id)
    if accession.laboratory_receiving.receipt.status != "lab_received":
        raise WorkflowError("Confirm laboratory custody before queueing Main Laboratory work.")
    items = LaboratoryIsolate.objects.select_for_update().filter(accession_range=accession)
    if items.count() != accession.isolate_count:
        raise WorkflowError("Record revival for the accession range before queueing Main Laboratory ID.")
    if isolate_id is not None:
        items = items.filter(pk=isolate_id)
    items = list(items)
    expected = (S.ID_QUEUED,) if endorse else (S.REVIVED, S.READY_FOR_BIOBANK, S.SITE_BANKED)
    ready = [
        item for item in items
        if item.status in expected and not item.returned_at
        and not item.endorsed_at and not item.identifications.exists()
        and (item.status == S.REVIVED or item.identification_source == "site" or endorse)
    ]
    if not ready:
        if items and (any(item.endorsed_at for item in items) or all(item.status in ((S.ID_QUEUED,) if not endorse else ()) + (S.ID_PENDING, S.ID_TESTING, S.ID_RETURNED,
                        S.ID_REVIEWED, S.BIOBANKED) for item in items)):
            return False
        raise WorkflowError("Queue revived isolates before endorsement to Main Laboratory." if endorse else
                            "Complete revival successfully before queueing for Main Laboratory endorsement.")
    if any(not item.revival_started_at or not item.revival_completed_at for item in ready):
        raise WorkflowError("Record the actual revival start and completion dates before queueing ID testing.")
    now = timezone.now()
    actor_field, at_field = ("endorsed_by", "endorsed_at") if endorse else ("id_queued_by", "id_queued_at")
    for item in ready:
        item.status = S.ID_PENDING if endorse else S.ID_QUEUED
        # The original Site ID and storage history remain recorded separately.
        # Site ID is provisional and must not be mistaken for approved Main Lab ID.
        if not endorse and item.identification_source == "site":
            item.identification_source = ""
            item.final_organism = ""
            item.approved_organism_code = ""
        setattr(item, at_field, now)
        setattr(item, actor_field, user)
    LaboratoryIsolate.objects.bulk_update(
        ready, ["status", at_field, actor_field, "identification_source",
                "final_organism", "approved_organism_code"]
    )
    return True


@transaction.atomic
def queue_for_id(accession_id, user, isolate_id=None):
    return _advance_id_handoff(accession_id, user, isolate_id, endorse=False)


@transaction.atomic
def create_id_handoff(accession_id, user, isolate_id=None):
    """Endorse the entire accession batch, never selected individual isolates.

    Queueing an individual isolate is allowed while the receiving team completes
    revival and Site ID encoding. The actual custody handoff is one atomic action.
    Non-viable isolates are documented terminal outcomes, not testing candidates.
    """
    if isolate_id is not None:
        raise WorkflowError("Main Laboratory endorsement must be performed for the entire batch.")
    if not can_review_id_results(user):
        raise WorkflowError("Only Laboratory Receiving can endorse a prepared batch to Main Laboratory.")
    accession = LaboratoryAccession.objects.select_for_update().select_related(
        "laboratory_receiving__receipt"
    ).get(pk=accession_id)
    if accession.laboratory_receiving.receipt.status != "lab_received":
        raise WorkflowError("Laboratory Receiving must acknowledge the original Receiving handoff first.")
    items = list(LaboratoryIsolate.objects.select_for_update()
                 .filter(accession_range=accession).order_by("pk"))
    if len(items) != accession.isolate_count or not items:
        raise WorkflowError("Every isolate in the batch must be accessioned before Main Lab endorsement.")
    if all(item.status in (S.ID_PENDING, S.ID_TESTING, S.ID_RETURNED, S.ID_REVIEWED,
                           S.READY_FOR_BIOBANK, S.BIOBANKED, S.NON_VIABLE) for item in items):
        return False
    if any(item.status not in (S.ID_QUEUED, S.NON_VIABLE) for item in items):
        raise WorkflowError("All viable isolates must be revived, assigned a Site ID, and queued before the whole batch can be endorsed.")
    queued = [item for item in items if item.status == S.ID_QUEUED]
    if not queued:
        raise WorkflowError("No viable queued isolates to endorse to Main Laboratory.")
    for item in queued:
        if not (item.revival_started_at and item.revival_completed_at):
            raise WorkflowError("Every queued isolate needs actual revival start and completion times.")
        if not (item.site_organism_code.strip() and item.site_organism_name.strip()):
            raise WorkflowError("Every viable isolate must have its Site ID explicitly encoded before whole-batch endorsement.")
        site_code, site_name = site_identification(item)
        if not site_code or not site_name:
            raise WorkflowError("Every queued isolate needs a valid Site ID.")
        if item.endorsed_at or item.returned_at or item.identifications.exists():
            raise WorkflowError("Previously endorsed isolates cannot be re-endorsed.")
    now = timezone.now()
    for item in queued:
        item.status = S.ID_PENDING
        item.endorsed_at = now
        item.endorsed_by = user
    LaboratoryIsolate.objects.bulk_update(queued, ["status", "endorsed_at", "endorsed_by"])
    return True


def site_identification(isolate, *, require_code=True):
    """Read the referring site's ID for this exact accession and site."""
    if isolate.site_organism_code or isolate.site_organism_name:
        try:
            return resolve_organism(code=isolate.site_organism_code, name=isolate.site_organism_name,
                                    require_code=require_code)
        except ValidationError as exc:
            raise WorkflowError("Check the recorded site identification: " + "; ".join(exc.messages)) from exc
    from apps.home.models import Referred_Data
    site_code = isolate.accession_range.laboratory_receiving.receipt.site.SiteCode
    raw = Referred_Data.objects.filter(
        AccessionNo=isolate.accession_number, SiteCode__iexact=site_code.strip()
    ).only("Site_Org", "Site_OrgName").first()
    if raw is None or not (raw.Site_Org or raw.Site_OrgName):
        raise WorkflowError("Record the referring site's identification for this accession during revival.")
    try:
        return resolve_organism(code=raw.Site_Org, name=raw.Site_OrgName, require_code=require_code)
    except ValidationError as exc:
        raise WorkflowError("Check the referring site's identification: " + "; ".join(exc.messages)) from exc


@transaction.atomic
def record_site_identification(isolate_id, code, name, user):
    if not can_review_id_results(user):
        raise WorkflowError("Only Laboratory Receiving encoders may record the referring site's ID.")
    candidate = LaboratoryIsolate.objects.only("accession_range_id").get(pk=isolate_id)
    LaboratoryAccession.objects.select_for_update().get(pk=candidate.accession_range_id)
    item = LaboratoryIsolate.objects.select_for_update().get(pk=isolate_id)
    if item.status not in (S.REVIVAL_PENDING, S.REVIVAL_QUEUED, S.REVIVAL_IN_PROGRESS,
                           S.REVIVAL_FAILED, S.REVIVED, S.ID_QUEUED) or item.endorsed_at or item.identifications.exists():
        raise WorkflowError("Site identification is preserved after Main Lab endorsement or selection for banking.")
    try:
        code, name = resolve_organism(code=code, name=name)
    except ValidationError as exc:
        raise WorkflowError("; ".join(exc.messages)) from exc
    if len(code) > 20 or len(name) > 255:
        raise WorkflowError("Site organism code must be at most 20 characters and its name at most 255 characters.")
    item.site_organism_code, item.site_organism_name = code, name
    item.site_identified_at, item.site_identified_by = timezone.now(), user
    item.save(update_fields=["site_organism_code", "site_organism_name", "site_identified_at", "site_identified_by"])
    return item


@transaction.atomic
def bank_using_site_id(isolate_id, user):
    if not can_manage_biobank(user):
        raise WorkflowError("You do not have permission to bank this isolate.")
    candidate = LaboratoryIsolate.objects.only("accession_range_id").get(pk=isolate_id)
    LaboratoryAccession.objects.select_for_update().get(pk=candidate.accession_range_id)
    item = LaboratoryIsolate.objects.select_for_update().select_related(
        "accession_range__laboratory_receiving__receipt__site"
    ).get(pk=isolate_id)
    if item.identification_source == "site" and item.status in (S.READY_FOR_BIOBANK, S.SITE_BANKED):
        return item
    if (item.status != S.REVIVED or not item.revival_started_at or not item.revival_completed_at
            or item.endorsed_at or item.returned_at or item.identifications.exists()):
        raise WorkflowError("Direct site-ID banking is available only after successful revival and before Main Laboratory handoff.")
    code, organism = site_identification(item)
    item.identification_source = "site"
    item.final_organism, item.approved_organism_code = organism, code
    item.status = S.READY_FOR_BIOBANK
    item.save(update_fields=["identification_source", "final_organism", "approved_organism_code", "status"])
    bank, _ = LaboratoryBiobank.objects.get_or_create(isolate=item)
    bank.whonet_code, bank.recorded_by = code, user
    bank.save(update_fields=["whonet_code", "recorded_by"])
    return item


BATCH_TRANSITIONS = {
    "start": (S.REVIVAL_QUEUED, S.REVIVAL_IN_PROGRESS, "revival_started_by", "revival_started_at"),
    "complete": (S.REVIVAL_IN_PROGRESS, S.REVIVED, "revival_completed_by", "revival_completed_at"),
}

@transaction.atomic
def transition_batch(accession_id, action, user):
    if action == "prepare":
        return prepare_revival(accession_id, user)
    if action == "queue":
        return queue_for_id(accession_id, user)
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
    accepted = (expected, target) if action == "complete" else (expected, S.REVIVAL_PENDING)
    if any(item.status not in accepted for item in isolates):
        raise WorkflowError(
            "Every isolate in the accession range must be at the expected stage. "
            "A failed, non-viable, or unreconciled isolate blocks the entire batch."
        )
    pending = [item for item in isolates if item.status != target]
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
    """Progress revival or queue this successfully revived accession for Main Lab."""
    if action == "queue":
        item = LaboratoryIsolate.objects.get(pk=isolate_id)
        queue_for_id(item.accession_range_id, user, isolate_id=item.pk)
        item.refresh_from_db()
        return item
    transitions = {
        "start": (S.REVIVAL_QUEUED, S.REVIVAL_IN_PROGRESS, "revival_started_by", "revival_started_at"),
        "complete": (S.REVIVAL_IN_PROGRESS, S.REVIVED, "revival_completed_by", "revival_completed_at"),
    }
    if action not in transitions:
        raise WorkflowError("Unknown isolate revival transition.")
    candidate = LaboratoryIsolate.objects.only("accession_range_id").get(pk=isolate_id)
    LaboratoryAccession.objects.select_for_update().get(pk=candidate.accession_range_id)
    item = LaboratoryIsolate.objects.select_for_update().get(pk=isolate_id)
    expected, target, actor_field, at_field = transitions[action]
    if item.status != expected and not (action == "start" and item.status == S.REVIVAL_PENDING):
        raise WorkflowError("Isolate is not at the expected stage for this revival action.")
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
    if item.status not in (S.REVIVAL_PENDING, S.REVIVAL_QUEUED, S.REVIVAL_IN_PROGRESS):
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
    # A retry is independent of siblings following either banking or Main Lab ID.
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
    site_banking = item.identification_source == "site" and item.status in (S.READY_FOR_BIOBANK, S.SITE_BANKED)
    if (item.status != S.ID_REVIEWED and not site_banking) or not item.final_organism:
        raise WorkflowError("Confirm Main Laboratory identification or select site-ID banking before recording lyophilization.")
    bank, _ = LaboratoryBiobank.objects.select_for_update().get_or_create(isolate=item)
    harvest = bank.date_harvested
    if harvest:
        if harvested and _valid_date(harvested, "Harvest date") != harvest:
            raise WorkflowError("Use the previously recorded harvest date.")
    else:
        # Harvest is recorded with lyophilization, not with revival or storage.
        harvest = _valid_date(harvested, "Harvest date")
    lyoph = _valid_date(lyophilized, "Lyophilization date")
    if bank.date_lyophilized and lyoph != bank.date_lyophilized:
        raise WorkflowError("The recorded lyophilization date cannot be overwritten.")
    date_received = item.accession_range.laboratory_receiving.receipt.lab_date_received
    if date_received and min(harvest, lyoph) < date_received:
        raise WorkflowError("Harvest and lyophilization dates cannot precede laboratory receipt.")
    if item.revival_completed_at and lyoph < timezone.localdate(item.revival_completed_at):
        raise WorkflowError("Lyophilization cannot precede revival completion.")
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
    bank.date_harvested = harvest
    bank.date_lyophilized = lyoph
    bank.whonet_code = code
    bank.recorded_by = user
    bank.save()
    if item.status not in (S.BIOBANKED, S.SITE_BANKED):
        item.status = S.READY_FOR_BIOBANK
    if not item.lyophilization_completed_at:
        item.lyophilization_completed_at = timezone.now()
        item.lyophilization_completed_by = user
    item.save(update_fields=["status", "lyophilization_completed_at", "lyophilization_completed_by"])
    return item


@transaction.atomic
def save_biobank(isolate_id, details, user):
    item = LaboratoryIsolate.objects.select_for_update().get(pk=isolate_id)
    if item.status not in (S.READY_FOR_BIOBANK, S.SITE_BANKED, S.BIOBANKED):
        raise WorkflowError("Choose temporary Site ID storage, or complete Main Laboratory ID and lyophilization, before saving storage.")
    if item.identification_source and not (item.final_organism and item.approved_organism_code):
        raise WorkflowError("An organism identification is required to finalize banking.")
    bank, _ = LaboratoryBiobank.objects.select_for_update().get_or_create(isolate=item)
    def storage_value(name):
        return (details[name] if name in details else getattr(bank, name) or "").strip()
    mapped = (
        (storage_value("storage_box"), storage_value("box_position")),
        (storage_value("lyo_box_name") or storage_value("lyo_box_number"), storage_value("lyo_position")),
        (storage_value("supplementary_box_name") or storage_value("supplementary_box_number"), storage_value("supplementary_position")),
    )
    if not any(box and position for box, position in mapped):
        raise WorkflowError("A box identifier and position in the same storage section are required to finalize banking.")
    if item.identification_source == "main_lab" and not (bank.date_harvested and bank.date_lyophilized):
        raise WorkflowError("Record harvest and lyophilization dates before finalizing Main Laboratory banking.")
    if item.identification_source and bank.whonet_code.upper() != item.approved_organism_code.upper():
        raise WorkflowError("The banking organism code must match the selected identification.")
    # Scientific dates belong to lyophilization; storage edits must not rewrite them.
    blocked = {"date_harvested", "date_lyophilized", "whonet_code"}
    if blocked.intersection(details):
        raise WorkflowError("Harvest and lyophilization dates cannot be edited from storage.")
    for name, value in details.items():
        setattr(bank, name, value)
    bank.recorded_by = user
    bank.full_clean()
    bank.save()
    if item.identification_source == "site":
        # Site storage is a holding state, even after a box/position is assigned.
        # It does not satisfy the Main Lab identification requirement.
        if not item.site_banked_at:
            item.site_banked_at = timezone.now()
            item.site_banked_by = user
            item.site_bank_storage = {
                "storage_box": bank.storage_box, "box_position": bank.box_position,
                "lyo_box_name": bank.lyo_box_name, "lyo_box_number": bank.lyo_box_number,
                "lyo_position": bank.lyo_position,
                "supplementary_box_name": bank.supplementary_box_name,
                "supplementary_box_number": bank.supplementary_box_number,
                "supplementary_position": bank.supplementary_position,
            }
        item.status = S.SITE_BANKED
        item.save(update_fields=["status", "site_banked_at", "site_banked_by", "site_bank_storage"])
    elif item.status == S.READY_FOR_BIOBANK:
        if item.identification_source != "main_lab":
            raise WorkflowError("Main Lab identification approval is required for final biobanking.")
        if not (item.returned_at and item.reviewed_at and item.lyophilization_completed_at):
            raise WorkflowError("Main Lab ID return, review and lyophilization are required for final banking.")
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
def record_revival_outcome(isolate_id, status, started_at, completed_at, reason, user,
                           site_code=None, site_name=None):
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
    legacy_return = item.status == S.ID_RETURNED and not item.revival_completed_at
    if item.status not in (S.REVIVAL_PENDING, S.REVIVAL_QUEUED, S.REVIVAL_IN_PROGRESS) and not legacy_return:
        raise WorkflowError('Isolate is not ready for a revival outcome.')
    if legacy_return and not item.identifications.exists():
        raise WorkflowError('The historical returned identification must be present before recording its missing revival.')
    if legacy_return and item.returned_at and start < item.returned_at:
        raise WorkflowError('Record the actual missing revival after the historical cassette return.')
    date_received = item.accession_range.laboratory_receiving.receipt.lab_date_received
    if date_received and timezone.localdate(start) < date_received:
        raise WorkflowError('Revival cannot precede laboratory receipt.')
    if item.revival_started_at and not item.revival_retry_count and start.replace(second=0, microsecond=0) != item.revival_started_at.replace(second=0, microsecond=0):
        raise WorkflowError('Use the recorded revival start time.')
    if item.revival_started_at and end < item.revival_started_at:
        raise WorkflowError('Revival completion cannot precede the recorded start.')
    if item.revival_retried_at and start < item.revival_retried_at:
        raise WorkflowError('Retry work cannot precede the recorded retry.')
    if site_code or site_name:
        record_site_identification(item.pk, site_code, site_name, user)
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
