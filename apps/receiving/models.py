
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class ReferralReceipt(models.Model):
    """
    Records the receipt of physical referred isolates from a courier
    and their subsequent endorsement to the laboratory.

    One receipt can represent a courier delivery containing multiple
    isolates. Individual isolates will be linked in a later module.
    """

    class Status(models.TextChoices):
        RECEIVED = "received", "Received by Secretariat"
        ENDORSED = "endorsed", "Endorsed to Laboratory"
        LAB_RECEIVED = "lab_received", "Received by Laboratory"

    # --------------------------------------------------
    # RECEIVING IDENTIFICATION
    # --------------------------------------------------

    receiving_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
    )

    tracking_number = models.CharField(
        max_length=255,
        db_index=True,
        verbose_name="Courier Tracking Number",
    )

    site = models.ForeignKey(
        "home.SiteData",
        on_delete=models.PROTECT,
        related_name="referral_receipts",
        verbose_name="Referring Site",
    )

    # --------------------------------------------------
    # COURIER INFORMATION
    # --------------------------------------------------

    date_sent = models.DateField(
        verbose_name="Date Sent by Courier",
    )

    # --------------------------------------------------
    # SECRETARIAT RECEIVING
    # --------------------------------------------------

    date_received = models.DateField(
        verbose_name="Date Received by Secretariat",
    )

    time_received = models.TimeField(
        verbose_name="Time Received by Secretariat",
    )

    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="secretariat_received_referrals",
        verbose_name="Received By",
    )

    # --------------------------------------------------
    # ENDORSEMENT TO LABORATORY
    # --------------------------------------------------

    endorsed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Date and Time Endorsed to Laboratory",
    )

    endorsed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="endorsed_referrals",
        verbose_name="Endorsed By",
    )

    # --------------------------------------------------
    # LABORATORY RECEIVING
    # --------------------------------------------------

    lab_date_received = models.DateField(
        null=True,
        blank=True,
        verbose_name="Date Received by Laboratory",
    )

    lab_time_received = models.TimeField(
        null=True,
        blank=True,
        verbose_name="Time Received by Laboratory",
    )

    lab_received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="laboratory_received_referrals",
        verbose_name="Received By Laboratory",
    )

    # --------------------------------------------------
    # WORKFLOW STATUS
    # --------------------------------------------------

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.RECEIVED,
        db_index=True,
    )

    remarks = models.TextField(
        blank=True,
        default="",
    )

    # --------------------------------------------------
    # SYSTEM TIMESTAMPS
    # --------------------------------------------------

    created_at = models.DateTimeField(
        default=timezone.now,
        editable=False,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        db_table = "referral_receipts"
        ordering = ["-created_at"]
        verbose_name = "Referral Receipt"
        verbose_name_plural = "Referral Receipts"

    def __str__(self):
        return f"{self.tracking_number} - {self.site}"


class ReceivingEvent(models.Model):
    """
    Historical record of events and changes during receiving.

    Events should be appended rather than overwritten.
    """

    class EventType(models.TextChoices):
        RECEIVED = "received", "Received by Secretariat"
        ENDORSED = "endorsed", "Endorsed to Laboratory"
        LAB_RECEIVED = "lab_received", "Received by Laboratory"
        CORRECTED = "corrected", "Information Corrected"

    receipt = models.ForeignKey(
        ReferralReceipt,
        on_delete=models.CASCADE,
        related_name="events",
    )

    event_type = models.CharField(
        max_length=20,
        choices=EventType.choices,
    )

    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="receiving_events",
    )

    occurred_at = models.DateTimeField(
        default=timezone.now,
        verbose_name="Actual Event Date and Time",
    )

    recorded_at = models.DateTimeField(
        auto_now_add=True,
    )

    remarks = models.TextField(
        blank=True,
        default="",
    )

    class Meta:
        db_table = "receiving_events"
        ordering = ["-recorded_at", "-id"]

    def __str__(self):
        return f"{self.receipt.tracking_number} - {self.get_event_type_display()}"
