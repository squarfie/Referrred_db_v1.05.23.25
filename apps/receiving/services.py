from datetime import datetime

from django.db import transaction
from django.utils import timezone

from .models import ReceivingEvent, ReferralReceipt


class InvalidReceivingTransition(Exception):
    pass


def _aware_datetime(day, clock_time):
    return timezone.make_aware(
        datetime.combine(day, clock_time),
        timezone.get_current_timezone(),
    )


@transaction.atomic
def create_receipt(*, form, user):
    receipt = form.save(commit=False)
    receipt.received_by = user
    receipt.status = ReferralReceipt.Status.RECEIVED
    receipt.endorsed_at = None
    receipt.endorsed_by = None
    receipt.lab_date_received = None
    receipt.lab_time_received = None
    receipt.lab_received_by = None
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
    receipt = ReferralReceipt.objects.select_for_update().get(pk=receipt_id)
    if receipt.status != ReferralReceipt.Status.ENDORSED:
        raise InvalidReceivingTransition(
            "This courier batch is not awaiting laboratory receipt confirmation."
        )

    received_at = _aware_datetime(lab_date_received, lab_time_received)
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
