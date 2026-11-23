"""Identification availability, physical retrieval and append-only corrections."""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import (LaboratoryAccession, LaboratoryIsolate, LaboratoryIdentification,
                     LaboratoryIdentificationCorrection, LaboratoryBiobank)
from .organism_lookup import resolve_organism
from .permissions import can_access_main_lab, can_review_id_results
from .workflow import WorkflowError, save_biobank, lock_isolate

S = LaboratoryIsolate.Status


def _locked(isolate_id):
    return lock_isolate(isolate_id)


def _organism(code, name, *, require_code=True):
    try:
        code, name = resolve_organism(code=code, name=name, require_code=require_code)
    except ValidationError as exc:
        raise WorkflowError('; '.join(exc.messages)) from exc
    if len(code) > 20 or len(name) > 255:
        raise WorkflowError('Organism code/name exceeds its maximum length.')
    return code, name


def has_completed_id(item):
    return bool(item.id_completed_at or item.returned_at)


def final_banking_filter(prefix=''):
    from django.db.models import Q
    def q(**values):
        return Q(**{prefix + key: value for key, value in values.items()})
    return (q(status=S.BIOBANKED, identification_source='main_lab',
              lyophilization_completed_at__isnull=False, biobanked_at__isnull=False)
            & (q(id_completed_at__isnull=False, final_identified_at__isnull=False)
               | (q(returned_at__isnull=False) & (q(reviewed_at__isnull=False) | q(final_identified_at__isnull=False)))))


def is_final_banked(item):
    return bool(item.status == S.BIOBANKED and item.identification_source == 'main_lab'
                and item.lyophilization_completed_at and item.biobanked_at
                and ((item.id_completed_at and item.final_identified_at)
                     or (item.returned_at and (item.reviewed_at or item.final_identified_at))))


@transaction.atomic
def record_identification(isolate_id, code, name, user, *, instrument='', cassette_reference='',
                          source=LaboratoryIdentification.Source.MANUAL, final_code=None, final_name=None):
    if not can_access_main_lab(user):
        raise WorkflowError('Only Main Lab Staff or Admin may save Main Lab results.')
    item = _locked(isolate_id)
    code, name = _organism(code, name, require_code=False)
    # Keep the instrument result distinct from the Final ID, which is owned by
    # Main Lab. Old clients/uploads may omit separate Final ID fields: in that
    # case only an already coded instrument result can supply the Final ID.
    final_code, final_name = _organism(
        code if final_code is None else final_code,
        name if final_name is None else final_name,
    )
    if len(instrument) > 100 or len(cassette_reference) > 100:
        raise WorkflowError('Instrument and cassette references must be at most 100 characters.')
    latest = item.identifications.first()
    if item.id_completed_at:
        if (latest and latest.whonet_organism_code == code and latest.organism_reported == name
                and latest.instrument == instrument and latest.cassette_reference == cassette_reference
                and item.approved_organism_code == final_code and item.final_organism == final_name):
            return latest
        raise WorkflowError('Testing is complete. Use a result correction with a reason.')
    # Endorsement only transfers custody to Main Lab; it does not prove
    # acknowledgment. Only actual receipt, retrieval, or documented legacy
    # testing evidence can authorize a result-completion transition.
    if item.status != S.ID_TESTING or not (
        item.acknowledged_at or item.retrieved_at or item.identifications.exists()
    ):
        raise WorkflowError('Acknowledge the Main Lab handoff or retrieve the temporarily banked isolate before recording ID results.')
    result = LaboratoryIdentification.objects.create(isolate=item, whonet_organism_code=code,
        organism_reported=name, instrument=instrument, cassette_reference=cassette_reference,
        source=source, recorded_by=user)
    now = timezone.now()
    item.status = S.ID_COMPLETE
    item.id_completed_at, item.id_completed_by = now, user
    # Final ID is a Main Laboratory decision, made at ID completion rather
    # than later at Bench A's lyophilization or final-banking step.
    item.final_organism, item.approved_organism_code = final_name, final_code
    item.final_identified_at, item.final_identified_by = now, user
    item.identification_source = 'main_lab'
    item.save(update_fields=[
        'status', 'id_completed_at', 'id_completed_by',
        'final_organism', 'approved_organism_code',
        'final_identified_at', 'final_identified_by', 'identification_source',
    ])
    # A temporarily banked isolate may have a stale provisional Site ID sticker.
    LaboratoryBiobank.objects.filter(isolate=item).update(whonet_code=final_code)
    return result


@transaction.atomic
def record_physical_return(isolate_id, user):
    """Compatibility action: physical return never changes completed ID availability."""
    if not can_access_main_lab(user):
        raise WorkflowError('Only Main Lab Staff or Admin may record cassette return.')
    item = _locked(isolate_id)
    if item.returned_at:
        return item
    if item.status not in (S.ID_COMPLETE, S.ID_RETURNED, S.ID_REVIEWED, S.READY_FOR_BIOBANK, S.BIOBANKED):
        raise WorkflowError('Complete ID testing before recording the physical cassette return.')
    if not item.identifications.filter(cassette_reference__gt='').exists():
        raise WorkflowError('Record an ID result with a cassette reference before physical cassette return.')
    # Physical return is an independent custody fact. It must not mark ID
    # testing complete or replace a scientifically meaningful workflow status.
    item.returned_at, item.returned_by = timezone.now(), user
    item.save(update_fields=['returned_at', 'returned_by'])
    return item


@transaction.atomic
def retrieve_for_testing(isolate_id, user):
    if not can_access_main_lab(user):
        raise WorkflowError('Only Main Lab Staff or Admin may retrieve isolates for testing.')
    item = _locked(isolate_id)
    if item.retrieved_at:
        return item
    bank = LaboratoryBiobank.objects.select_for_update().filter(isolate=item).first()
    if (item.status != S.SITE_BANKED or item.identification_source != 'site'
            or not item.site_banked_at or not bank
            or not item.revival_started_at or not item.revival_completed_at
            or item.endorsed_at or item.identifications.exists()):
        raise WorkflowError('Retrieve an isolate with saved temporary Site ID storage and completed revival.')
    item.retrieved_storage = {field: getattr(bank, field) for field in (
        'storage_box', 'box_position', 'storage_location', 'lyo_box_name', 'lyo_box_number',
        'lyo_position', 'lyo_location', 'refrigerator_number', 'supplementary_box_name',
        'supplementary_box_number', 'supplementary_position', 'supplementary_location', 'freezer_number')}
    item.retrieved_at, item.retrieved_by = timezone.now(), user
    item.status = S.ID_TESTING
    # These are provisional label values; the Site ID and its snapshot stay intact.
    item.identification_source, item.final_organism, item.approved_organism_code = '', '', ''
    item.save(update_fields=['status', 'retrieved_at', 'retrieved_by', 'retrieved_storage',
                             'identification_source', 'final_organism', 'approved_organism_code'])
    return item


@transaction.atomic
def proceed_to_lyophilization(isolate_id, user):
    if not can_review_id_results(user):
        raise WorkflowError('Only Bench A or Admin may proceed to lyophilization.')
    item = _locked(isolate_id)
    if item.lyophilization_started_at:
        return item
    if (item.status not in (S.ID_COMPLETE, S.ID_RETURNED, S.ID_REVIEWED, S.REVIVED)
            or not has_completed_id(item) or not item.identifications.exists()
            or not item.revival_completed_at):
        raise WorkflowError('Complete Main Lab ID and record actual revival before lyophilization.')
    if item.id_completed_at and not (item.final_identified_at and item.approved_organism_code
                                     and item.final_organism and item.identification_source == 'main_lab'):
        raise WorkflowError('Main Lab must record the Final ID before Bench A proceeds to lyophilization.')
    item.lyophilization_started_at, item.lyophilization_started_by = timezone.now(), user
    item.identification_source = 'main_lab'
    item.save(update_fields=['lyophilization_started_at', 'lyophilization_started_by', 'identification_source'])
    return item


@transaction.atomic
def finalize_banking(isolate_id, details, code, name, user):
    if not can_review_id_results(user):
        raise WorkflowError('Only Bench A or Admin may record final banking.')
    item = _locked(isolate_id)
    if item.status != S.READY_FOR_BIOBANK or item.identification_source != 'main_lab':
        raise WorkflowError('Complete lyophilization before final banking.')
    # New cases must have their Final ID supplied by Main Lab. Historic
    # result-return cases preserve their existing reviewed ID rather than
    # manufacturing a new Main Lab confirmation.
    if not (item.approved_organism_code and item.final_organism):
        raise WorkflowError('Main Lab Final ID is missing. Have Main Lab record it before banking.')
    if item.id_completed_at and not item.final_identified_at:
        raise WorkflowError('Final ID must be recorded by Main Lab before banking.')
    if (code and str(code).strip().upper() != item.approved_organism_code.upper()) or (
            name and str(name).strip() != item.final_organism):
        raise WorkflowError('Banking cannot change Main Lab Final ID. Use an audited ID correction.')
    bank = LaboratoryBiobank.objects.select_for_update().get(isolate=item)
    if bank.whonet_code != item.approved_organism_code:
        bank.whonet_code = item.approved_organism_code
        bank.save(update_fields=['whonet_code'])
    return save_biobank(item.pk, details, user)


@transaction.atomic
def correct_identification(isolate_id, kind, code, name, reason, user, *, result_id=None):
    if kind not in LaboratoryIdentificationCorrection.Kind.values:
        raise WorkflowError('Select Site ID, Main Lab result or Final ID.')
    allowed = can_access_main_lab(user) if kind == 'main_lab' else can_review_id_results(user)
    if not allowed:
        raise WorkflowError('Your role cannot correct this identification.')
    reason = (reason or '').strip()
    if kind == 'site' and not reason:
        # Bench A Site ID edits need no free-text reason, but their audit row
        # still identifies the change without inventing a justification.
        reason = 'Site ID corrected (no reason supplied)'
    if not reason or len(reason) > 1000:
        raise WorkflowError('A correction reason of at most 1,000 characters is required for Main Lab or Final ID changes.')
    code, name = _organism(code, name)
    item = _locked(isolate_id)
    original, revision = None, None
    if kind == 'main_lab':
        try:
            original = item.identifications.filter(pk=result_id).first()
        except (TypeError, ValueError) as exc:
            raise WorkflowError('Select a valid result from this isolate.') from exc
        if original is None or original.corrections.exists():
            raise WorkflowError('Select an existing, unamended Main Lab result. Refresh its history before correcting.')
        previous = original.whonet_organism_code, original.organism_reported
    elif kind == 'site':
        from .workflow import site_identification
        previous = site_identification(item, require_code=False)
    else:
        if not item.final_organism or not item.approved_organism_code or item.identification_source != 'main_lab':
            raise WorkflowError('A confirmed Main Lab-derived Final ID is required before correction.')
        previous = item.approved_organism_code, item.final_organism
    if previous == (code, name):
        return item
    if kind == 'main_lab':
        revision = LaboratoryIdentification.objects.create(isolate=item,
            organism_reported=name, whonet_organism_code=code, instrument=original.instrument,
            cassette_reference=original.cassette_reference, tested_at=original.tested_at,
            source=original.source, recorded_by=user)
    elif kind == 'site':
        item.site_organism_code, item.site_organism_name = code, name
        item.save(update_fields=['site_organism_code', 'site_organism_name'])
        if item.identification_source == 'site':
            item.approved_organism_code, item.final_organism = code, name
            item.save(update_fields=['approved_organism_code', 'final_organism'])
            LaboratoryBiobank.objects.filter(isolate=item).update(whonet_code=code)
    else:
        item.approved_organism_code, item.final_organism = code, name
        item.save(update_fields=['approved_organism_code', 'final_organism'])
        LaboratoryBiobank.objects.filter(isolate=item).update(whonet_code=code)
    LaboratoryIdentificationCorrection.objects.create(isolate=item, kind=kind,
        previous_code=previous[0], previous_name=previous[1], new_code=code, new_name=name,
        reason=reason, performed_by=user, original_result=original, revised_result=revision)
    return item
