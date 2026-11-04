import uuid

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone


class VerificationCase(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_FOR_RAW_VERIFICATION = "for_raw_verification"
    STATUS_FOR_FINAL_ENCODING = "for_final_encoding"
    STATUS_FOR_VERIFICATION = "for_verification"
    STATUS_FOR_FINAL_VERIFICATION = STATUS_FOR_VERIFICATION
    STATUS_FOR_FINAL_PRINTING = "for_final_printing"
    STATUS_WITHDRAWN = "withdrawn_by_checker"
    STATUS_RETURNED_TO_ENCODER = "returned_to_encoder"
    STATUS_RETURNED_TO_CHECKER = "returned_to_checker"
    STATUS_APPROVED_BY_VERIFIER = "approved_by_verifier"
    STATUS_FOR_LAB_MANAGER = "for_lab_manager"
    STATUS_RETURNED_TO_VERIFIER = "returned_to_verifier"
    STATUS_APPROVED_BY_LAB_MANAGER = "approved_by_lab_manager"
    STATUS_FOR_HEAD = "for_head"
    STATUS_DISAPPROVED_BY_LAB_MANAGER = "disapproved_by_lab_manager"
    STATUS_DISAPPROVED_BY_HEAD = "disapproved_by_head"
    STATUS_SIGNED_BY_HEAD = "signed_by_head"
    STATUS_RELEASED = "released"

    STATUS_CHOICES = (
        (STATUS_DRAFT, "Draft"),
        (STATUS_FOR_RAW_VERIFICATION, "For Raw Verification"),
        (STATUS_FOR_FINAL_ENCODING, "For Final Encoding"),
        (STATUS_FOR_VERIFICATION, "For Final Verification"),
        (STATUS_FOR_FINAL_PRINTING, "For Final Printing"),
        (STATUS_WITHDRAWN, "Withdrawn by Checker"),
        (STATUS_RETURNED_TO_ENCODER, "Returned to Encoder"),
        (STATUS_RETURNED_TO_CHECKER, "Returned to Checker"),
        (STATUS_APPROVED_BY_VERIFIER, "Approved by Verifier"),
        (STATUS_FOR_LAB_MANAGER, "For Lab Manager"),
        (STATUS_RETURNED_TO_VERIFIER, "Returned to Verifier"),
        (STATUS_APPROVED_BY_LAB_MANAGER, "Approved by Lab Manager"),
        (STATUS_FOR_HEAD, "For ARSRL Head"),
        (STATUS_DISAPPROVED_BY_LAB_MANAGER, "Disapproved by Lab Manager"),
        (STATUS_DISAPPROVED_BY_HEAD, "Disapproved by ARSRL Head"),
        (STATUS_SIGNED_BY_HEAD, "Signed by ARSRL Head"),
        (STATUS_RELEASED, "Released"),
    )

    WORKFLOW_FILTER_CHOICES = (
        (STATUS_FOR_RAW_VERIFICATION, "For Raw Verification"),
        (STATUS_FOR_FINAL_ENCODING, "For Final Encoding"),
        (STATUS_FOR_VERIFICATION, "For Final Verification"),
        (STATUS_FOR_FINAL_PRINTING, "For Final Printing"),
        (STATUS_RETURNED_TO_CHECKER, "Returned to Checker"),
        (STATUS_FOR_LAB_MANAGER, "For Lab Manager"),
        (STATUS_RETURNED_TO_VERIFIER, "Returned to Verifier"),
        (STATUS_FOR_HEAD, "For Head Approval"),
        (STATUS_DISAPPROVED_BY_HEAD, "Disapproved by ARSRL Head"),
        (STATUS_SIGNED_BY_HEAD, "Ready for Release"),
        (STATUS_RELEASED, "Released"),
    )

    final_batch = models.ForeignKey(
        "home.Batch_Table",
        on_delete=models.CASCADE,
        related_name="verification_cases",
        null=True,
        blank=True,
    )
    final_accession = models.ForeignKey(
        "home_final.Final_Data",
        on_delete=models.CASCADE,
        related_name="verification_cases",
        null=True,
        blank=True,
    )
    assigned_checker = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="assigned_checker_cases",
        null=True,
        blank=True,
    )
    assigned_verifier = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="assigned_verifier_cases",
        null=True,
        blank=True,
    )
    assigned_lab_manager = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="assigned_lab_manager_cases",
        null=True,
        blank=True,
    )
    assigned_head = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="assigned_head_cases",
        null=True,
        blank=True,
    )
    status = models.CharField(max_length=40, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    note = models.TextField(blank=True, default="")

    sent_to_verifier_at = models.DateTimeField(null=True, blank=True)
    sent_by_checker = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="sent_verification_cases",
        null=True,
        blank=True,
    )
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    withdrawn_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="withdrawn_verification_cases",
        null=True,
        blank=True,
    )
    verifier_decision_at = models.DateTimeField(null=True, blank=True)
    verifier_decision_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="decided_verification_cases",
        null=True,
        blank=True,
    )
    sent_to_lab_manager_at = models.DateTimeField(null=True, blank=True)
    sent_to_lab_manager_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="sent_lab_manager_verification_cases",
        null=True,
        blank=True,
    )
    lab_manager_decision_at = models.DateTimeField(null=True, blank=True)
    lab_manager_decision_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="lab_manager_decided_verification_cases",
        null=True,
        blank=True,
    )
    sent_to_head_at = models.DateTimeField(null=True, blank=True)
    sent_to_head_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="sent_head_verification_cases",
        null=True,
        blank=True,
    )
    head_decision_at = models.DateTimeField(null=True, blank=True)
    head_decision_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="head_decided_verification_cases",
        null=True,
        blank=True,
    )
    signed_by_head_at = models.DateTimeField(null=True, blank=True)
    signed_by_head = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="head_signed_verification_cases",
        null=True,
        blank=True,
    )

    qr_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    qr_generated_at = models.DateTimeField(null=True, blank=True)
    released_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="created_verification_cases",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ("-updated_at", "-created_at")
        constraints = [
            models.UniqueConstraint(
                fields=("final_batch",),
                condition=models.Q(final_batch__isnull=False, final_accession__isnull=True),
                name="one_batch_verification_case",
            ),
            models.UniqueConstraint(
                fields=("final_accession",),
                condition=models.Q(final_accession__isnull=False, final_batch__isnull=True),
                name="one_accession_verification_case",
            ),
        ]

    def __str__(self):
        target = self.final_batch or self.final_accession
        return f"{target} - {self.get_status_display()}"

    def mark_qr_generated(self):
        if not self.qr_generated_at:
            self.qr_generated_at = timezone.now()


class VerificationComment(models.Model):
    DECISION_NOTE = "note"
    DECISION_APPROVED = "approved"
    DECISION_NEEDS_CORRECTION = "needs_correction"
    DECISION_RETURNED = "returned"
    DECISION_RESOLVED = "resolved"

    DECISION_CHOICES = (
        (DECISION_NOTE, "Note"),
        (DECISION_APPROVED, "Approved"),
        (DECISION_NEEDS_CORRECTION, "Needs Correction"),
        (DECISION_RETURNED, "Returned"),
        (DECISION_RESOLVED, "Resolved"),
    )

    RESOLUTION_OPEN = "open"
    RESOLUTION_HANDLED = "handled"
    RESOLUTION_RESOLVED = "resolved"
    RESOLUTION_REJECTED = "rejected"

    RESOLUTION_CHOICES = (
        (RESOLUTION_OPEN, "Open"),
        (RESOLUTION_HANDLED, "Correction applied"),
        (RESOLUTION_RESOLVED, "Resolved by Verifier"),
        (RESOLUTION_REJECTED, "Rejected"),
    )

    verification_case = models.ForeignKey(
        VerificationCase,
        on_delete=models.CASCADE,
        related_name="comments",
    )
    final_accession_ref_sequence = models.ForeignKey(
        "home_final.Final_Data",
        on_delete=models.CASCADE,
        related_name="verifier_comments",
        null=True,
        blank=True,
    )
    raw_accession_ref_sequence = models.ForeignKey(
        "home.Referred_Data",
        on_delete=models.CASCADE,
        related_name="verifier_comments",
        null=True,
        blank=True,
    )
    field_name = models.CharField(max_length=150, blank=True, default="")
    field_label = models.CharField(max_length=150, blank=True, default="")
    field_value_snapshot = models.TextField(blank=True, default="")
    comment = models.TextField()
    decision = models.CharField(max_length=30, choices=DECISION_CHOICES, default=DECISION_NOTE)
    is_resolved = models.BooleanField(default=False)
    resolution_status = models.CharField(
        max_length=30,
        choices=RESOLUTION_CHOICES,
        default=RESOLUTION_OPEN,
    )
    resolution_note = models.TextField(blank=True, default="")
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="resolved_verification_comments",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="verification_comments",
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = "Verifier Comment"
        verbose_name_plural = "Verifier Comments"
        ordering = ("-created_at",)

    def __str__(self):
        accession = self.final_accession_ref_sequence or self.raw_accession_ref_sequence or "Batch"
        label = self.field_label or self.field_name or "General"
        return f"{accession} - {label}"
