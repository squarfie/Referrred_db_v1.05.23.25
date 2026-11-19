from datetime import datetime

from django.db import transaction
from django.utils import timezone

from .models import ReceivingEvent, ReferralReceipt
from .permissions import (
    can_confirm_lab_receipt,
    can_manage_secretariat_receiving,
    can_permanently_delete_receipt,
)


class InvalidReceivingTransition(Exception):
    pass


class ReceivingPermissionDenied(Exception):
    pass


def _aware_datetime(day, clock_time):
    return timezone.make_aware(
        datetime.combine(day, clock_time),
        timezone.get_current_timezone(),
    )


def _require_permission(allowed, message):
    if not allowed:
        raise ReceivingPermissionDenied(message)


def _reject_future_timestamp(value, message):
    if value > timezone.now():
        raise InvalidReceivingTransition(message)


@transaction.atomic
def create_receipt(*, form, user):
    _require_permission(
        can_manage_secretariat_receiving(user),
        "Only Secretariat or Admin users may create receiving records.",
    )
    receipt = form.save(commit=False)
    receipt.received_by = user
    receipt.status = ReferralReceipt.Status.RECEIVED
    receipt.endorsed_at = None
    receipt.endorsed_by = None
    receipt.lab_date_received = None
    receipt.lab_time_received = None
    receipt.lab_received_by = None
    _reject_future_timestamp(
        _aware_datetime(receipt.date_received, receipt.time_received),
        "Secretariat receipt cannot be later than the current time.",
    )
    receipt.save()
    ReceivingEvent.objects.create(
        receipt=receipt,
        event_type=ReceivingEvent.EventType.RECEIVED,
        performed_by=user,
        occurred_at=_aware_datetime(receipt.date_received, receipt.time_received),
        remarks=receipt.remarks,
    )
    return receipt


@transaction.atomic
def endorse_receipt(*, receipt_id, user):
    _require_permission(
        can_manage_secretariat_receiving(user),
        "Only Secretariat or Admin users may endorse receiving records.",
    )
    receipt = ReferralReceipt.objects.select_for_update().get(pk=receipt_id)
    if receipt.status != ReferralReceipt.Status.RECEIVED:
        raise InvalidReceivingTransition("This courier batch has already been endorsed.")

    endorsed_at = timezone.now()
    receipt.status = ReferralReceipt.Status.ENDORSED
    receipt.endorsed_at = endorsed_at
    receipt.endorsed_by = user
    receipt.save(update_fields=("status", "endorsed_at", "endorsed_by", "updated_at"))
    ReceivingEvent.objects.create(
        receipt=receipt,
        event_type=ReceivingEvent.EventType.ENDORSED,
        performed_by=user,
        occurred_at=endorsed_at,
    )
    return receipt


@transaction.atomic
def confirm_lab_receipt(
    *,
    receipt_id,
    user,
    lab_date_received,
    lab_time_received,
):
    _require_permission(
        can_confirm_lab_receipt(user),
        "Only LAB Encoder users may confirm laboratory receipt.",
    )
    receipt = ReferralReceipt.objects.select_for_update().get(pk=receipt_id)
    if receipt.status != ReferralReceipt.Status.ENDORSED:
        raise InvalidReceivingTransition(
            "This courier batch is not awaiting laboratory receipt confirmation."
        )

    received_at = _aware_datetime(lab_date_received, lab_time_received)
    _reject_future_timestamp(
        received_at,
        "Laboratory receipt cannot be later than the current time.",
    )
    if not receipt.endorsed_at or received_at < receipt.endorsed_at:
        raise InvalidReceivingTransition(
            "Laboratory receipt cannot be earlier than endorsement."
        )

    receipt.status = ReferralReceipt.Status.LAB_RECEIVED
    receipt.lab_date_received = lab_date_received
    receipt.lab_time_received = lab_time_received
    receipt.lab_received_by = user
    receipt.save(
        update_fields=(
            "status",
            "lab_date_received",
            "lab_time_received",
            "lab_received_by",
            "updated_at",
        )
    )
    ReceivingEvent.objects.create(
        receipt=receipt,
        event_type=ReceivingEvent.EventType.LAB_RECEIVED,
        performed_by=user,
        occurred_at=received_at,
    )
    return receipt


@transaction.atomic
def delete_receipt_permanently(*, receipt_id, user):
    _require_permission(
        can_permanently_delete_receipt(user),
        "Only Admin users may permanently delete receiving records.",
    )
    receipt = ReferralReceipt.objects.select_for_update().get(pk=receipt_id)
    if not receipt.can_permanently_delete:
        raise InvalidReceivingTransition(
            "Only an unendorsed receiving record may be permanently deleted."
        )
    receipt.delete()
