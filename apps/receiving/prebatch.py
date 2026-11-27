"""Bench A -> DMU custody of an accession range before a batch exists.

These services are deliberately independent of BatchCustody: the latter is
created only after a DMU user creates the real Batch_Table.
"""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.home.permissions import ROLE_ADMIN, ROLE_ENCODER, ROLE_LAB_ENCODER, get_user_roles
from apps.home.services.batch_creation import create_referred_batch
from apps.laboratory.identification_workflow import is_final_banked
from .handoffs import initialize_custody
from .models import (
    LaboratoryAccession, LaboratoryBatchHandoff, LaboratoryBatchHandoffEvent,
    LaboratoryIsolate, ReferralReceipt,
)


def _check_role(user, role):
    if not user or not user.is_authenticated or not (get_user_roles(user) & {ROLE_ADMIN, role}):
        raise PermissionDenied("This action is not permitted for your role.")


def _event(handoff, action, user, remarks=""):
    LaboratoryBatchHandoffEvent.objects.create(
        handoff=handoff, action=action, performed_by=user, remarks=remarks,
    )


def endorsement_blocker(accession):
    """Return the next prerequisite before Bench A can endorse the range."""
    if accession.laboratory_receiving.receipt.status != ReferralReceipt.Status.LAB_RECEIVED:
        return "Confirm physical receipt in Receiving first."
    if accession.batch_id:
        return "The Raw Data batch has already been generated."
    if accession.has_active_dmu_handoff:
        return "This accession is already endorsed to DMU."
    return _lab_readiness_blocker(accession)


def _lab_readiness_blocker(accession):
    isolates = list(accession.isolate_records.all())
    if len(isolates) != accession.isolate_count:
        return "Complete laboratory isolate records before endorsing to DMU."
    if any(
        item.status != LaboratoryIsolate.Status.NON_VIABLE
        and not is_final_banked(item)
        for item in isolates
    ):
        return "Complete Bench A final processing or close non-viable isolates first."
    return ""


@transaction.atomic
def endorse_accession_to_dmu(*, accession_id, user):
    _check_role(user, ROLE_LAB_ENCODER)
    accession = LaboratoryAccession.objects.select_for_update().select_related(
        "laboratory_receiving__receipt",
    ).get(pk=accession_id)
    blocker = endorsement_blocker(accession)
    if blocker:
        raise ValidationError(blocker)
    handoff, created = LaboratoryBatchHandoff.objects.select_for_update().get_or_create(
        accession=accession, defaults={"endorsed_by": user},
    )
    if not created:
        if handoff.status != LaboratoryBatchHandoff.Status.RECALLED:
            raise ValidationError("Only a recalled endorsement can be sent again.")
        handoff.status = LaboratoryBatchHandoff.Status.PENDING
        handoff.endorsed_by = user
        handoff.endorsed_at = timezone.now()
        handoff.save(update_fields=("status", "endorsed_by", "endorsed_at"))
    _event(handoff, LaboratoryBatchHandoffEvent.Action.ENDORSED, user,
           f"Accession range {accession.reference_range} endorsed to DMU.")
    return handoff


@transaction.atomic
def recall_accession_from_dmu(*, accession_id, user, remarks=""):
    """Recall only an unaccepted accession handoff; preserve its event history."""
    _check_role(user, ROLE_LAB_ENCODER)
    handoff = LaboratoryBatchHandoff.objects.select_for_update().select_related(
        "accession",
    ).get(accession_id=accession_id)
    if handoff.status != LaboratoryBatchHandoff.Status.PENDING:
        raise ValidationError("DMU has already received or processed this endorsement; recall is blocked.")
    if handoff.accession.batch_id or handoff.confirmed_at or handoff.created_at:
        raise ValidationError("A received or generated DMU batch cannot be recalled.")
    if ROLE_ADMIN not in get_user_roles(user) and handoff.endorsed_by_id != user.pk:
        raise PermissionDenied("Only the endorsing Bench A user or Admin may recall this handoff.")
    if len(remarks) > 1000:
        raise ValidationError("Recall reason exceeds 1000 characters.")
    handoff.status = LaboratoryBatchHandoff.Status.RECALLED
    handoff.save(update_fields=("status",))
    _event(
        handoff, LaboratoryBatchHandoffEvent.Action.RECALLED, user,
        remarks.strip() or f"Recalled accession range {handoff.accession.reference_range} before DMU receipt.",
    )
    return handoff


@transaction.atomic
def confirm_dmu_accession(*, handoff_id, user):
    _check_role(user, ROLE_ENCODER)
    handoff = LaboratoryBatchHandoff.objects.select_for_update().get(pk=handoff_id)
    if handoff.status != LaboratoryBatchHandoff.Status.PENDING:
        raise ValidationError("This endorsement is not awaiting DMU receipt.")
    if handoff.endorsed_by_id == user.pk and ROLE_ADMIN not in get_user_roles(user):
        raise PermissionDenied("The sender cannot confirm their own endorsement.")
    handoff.status = LaboratoryBatchHandoff.Status.CONFIRMED
    handoff.confirmed_by = user
    handoff.confirmed_at = timezone.now()
    handoff.save(update_fields=("status", "confirmed_by", "confirmed_at"))
    _event(handoff, LaboratoryBatchHandoffEvent.Action.CONFIRMED, user)
    return handoff


@transaction.atomic
def create_dmu_batch(*, handoff_id, user):
    _check_role(user, ROLE_ENCODER)
    handoff = LaboratoryBatchHandoff.objects.select_for_update().select_related(
        "accession__laboratory_receiving__receipt__site",
    ).get(pk=handoff_id)
    if handoff.status != LaboratoryBatchHandoff.Status.CONFIRMED:
        raise ValidationError("DMU must confirm receipt before creating the Raw Data batch.")
    accession = LaboratoryAccession.objects.select_for_update().get(pk=handoff.accession_id)
    if accession.batch_id:
        raise ValidationError("This accession range already has a generated batch.")
    blocker = _lab_readiness_blocker(accession)
    if blocker:
        raise ValidationError(blocker)
    receipt = handoff.accession.laboratory_receiving.receipt
    result = create_referred_batch(
        site_code=receipt.site.SiteCode,
        site_name=receipt.site.SiteName,
        referral_date=accession.date_referred,
        start_number=accession.start_number,
        end_number=accession.end_number,
        batch_number=accession.batch_number,
        total_batches=accession.total_batches,
        created_by=user,
        authorized_handoff_id=handoff.pk,
    )
    initialize_custody(batch=result.batch, user=user, initial_role=ROLE_ENCODER)
    accession.batch = result.batch
    accession.batch_created_at = timezone.now()
    accession.save(update_fields=("batch", "batch_created_at"))
    handoff.status = LaboratoryBatchHandoff.Status.CREATED
    handoff.created_by = user
    handoff.created_at = timezone.now()
    handoff.save(update_fields=("status", "created_by", "created_at"))
    _event(handoff, LaboratoryBatchHandoffEvent.Action.BATCH_CREATED, user,
           f"DMU created batch {result.batch.bat_Batch_Code}.")
    return result


@transaction.atomic
def confirm_dmu_receipt_and_create_batch(*, handoff_id, user):
    """Single DMU acknowledgment: record receipt and create the Raw Data batch.

    Both steps share an outer transaction. If batch validation or creation
    fails, the receipt confirmation and its audit event roll back together.
    Existing confirmed handoffs can still use create_dmu_batch for recovery.
    """
    confirm_dmu_accession(handoff_id=handoff_id, user=user)
    return create_dmu_batch(handoff_id=handoff_id, user=user)
