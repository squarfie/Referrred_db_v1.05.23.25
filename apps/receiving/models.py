
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone


class ReferralReceipt(models.Model):
    """
    Records the receipt of physical referred isolates from a courier
    and their subsequent endorsement to the laboratory.

    One receipt can represent a courier delivery containing multiple
    isolates. Individual isolates will be linked in a later module.
    """

    class Status(models.TextChoices):
        RECEIVED = "received", "Isolates Received"
        ENDORSED = "endorsed", "Endorsed to Laboratory Receiving"
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
        verbose_name="Date Received",
    )

    time_received = models.TimeField(
        verbose_name="Time Received",
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
        constraints = [
            models.CheckConstraint(
                check=(
                    Q(
                        status="received",
                        endorsed_at__isnull=True,
                        lab_date_received__isnull=True,
                        lab_time_received__isnull=True,
                    )
                    | Q(
                        status="endorsed",
                        endorsed_at__isnull=False,
                        lab_date_received__isnull=True,
                        lab_time_received__isnull=True,
                    )
                    | Q(
                        status="lab_received",
                        endorsed_at__isnull=False,
                        lab_date_received__isnull=False,
                        lab_time_received__isnull=False,
                    )
                ),
                name="receiving_valid_custody_state",
            ),
        ]

    def __str__(self):
        return f"{self.tracking_number} - {self.site}"

    @property
    def can_permanently_delete(self):
        return (
            self.status == self.Status.RECEIVED
            and self.endorsed_at is None
            and self.lab_date_received is None
            and self.lab_time_received is None
        )


class ReceivingEvent(models.Model):
    """
    Historical record of events and changes during receiving.

    Events should be appended rather than overwritten.
    """

    class EventType(models.TextChoices):
        RECEIVED = "received", "Isolates Received"
        ENDORSED = "endorsed", "Endorsed to Laboratory Receiving"
        LAB_RECEIVED = "lab_received", "Received by Laboratory"
        ID_RECEIVED = "id_received", "Received for ID Testing"
        ACCESSION_ASSIGNED = "accession_assigned", "Accession Range Assigned"
        BATCH_CREATED = "batch_created", "Laboratory Batch Created"
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
        constraints = [
            models.UniqueConstraint(
                fields=("receipt", "event_type"),
                condition=Q(event_type__in=("received", "endorsed", "lab_received")),
                name="receiving_one_canonical_event",
            ),
        ]

    def __str__(self):
        return f"{self.receipt.tracking_number} - {self.get_event_type_display()}"



class LaboratoryReceiving(models.Model):
    """Laboratory sorting decision for one receiving custody record."""

    receipt = models.OneToOneField(
        ReferralReceipt,
        on_delete=models.PROTECT,
        related_name="laboratory_receiving",
    )
    specimen_condition = models.CharField(max_length=255, blank=True, default="")
    discrepancies = models.TextField(blank=True, default="")
    remarks = models.TextField(blank=True, default="")
    total_isolates_received = models.PositiveIntegerField(default=0)
    isolates_rejected = models.PositiveIntegerField(default=0)
    isolates_accepted = models.PositiveIntegerField(default=0)
    date_accepted = models.DateField(null=True, blank=True)
    total_isolates_processed = models.PositiveIntegerField(default=0)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="laboratory_receiving_records",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "laboratory_receiving"
        ordering = ("-receipt__lab_date_received", "-id")
        constraints = [
            models.CheckConstraint(
                check=Q(isolates_accepted__lte=F("total_isolates_received")),
                name="lab_receiving_accepted_lte_received",
            ),
            models.CheckConstraint(
                check=Q(isolates_rejected__lte=F("total_isolates_received")),
                name="lab_receiving_rejected_lte_received",
            ),
            models.CheckConstraint(
                check=Q(total_isolates_processed__lte=F("isolates_accepted")),
                name="lab_receiving_processed_lte_accepted",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        decided = self.isolates_accepted + self.isolates_rejected
        if decided > self.total_isolates_received:
            errors["isolates_accepted"] = "Accepted and rejected totals cannot exceed total received."
        if decided != self.total_isolates_received:
            errors["isolates_accepted"] = "Accepted and rejected totals must equal total received before saving."
        if self.total_isolates_processed > self.isolates_accepted:
            errors["total_isolates_processed"] = "Processed isolates cannot exceed accepted isolates."
        if self.pk:
            assigned_total = sum(
                item.end_number - item.start_number + 1
                for item in self.accession_ranges.all()
            )
            if assigned_total > self.isolates_accepted:
                errors["isolates_accepted"] = "Accepted isolates cannot be lower than already assigned accession ranges."
        if self.isolates_accepted and not self.date_accepted:
            errors["date_accepted"] = "Date accepted is required when isolates are accepted."
        if self.date_accepted:
            if self.receipt_id and self.receipt.lab_date_received and self.date_accepted < self.receipt.lab_date_received:
                errors["date_accepted"] = "Date accepted cannot be earlier than laboratory receipt."
            if self.date_accepted > timezone.localdate():
                errors["date_accepted"] = "Date accepted cannot be in the future."
        if errors:
            raise ValidationError(errors)

    @property
    def site(self):
        return self.receipt.site

    def __str__(self):
        return f"Laboratory receiving - {self.receipt.tracking_number}"


class LaboratoryAccession(models.Model):
    laboratory_receiving = models.ForeignKey(
        LaboratoryReceiving,
        on_delete=models.PROTECT,
        related_name="accession_ranges",
    )
    date_referred = models.DateField()
    start_number = models.PositiveIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(9999)]
    )
    end_number = models.PositiveIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(9999)]
    )
    batch_number = models.PositiveIntegerField(default=1)
    total_batches = models.PositiveIntegerField(default=1)
    batch = models.OneToOneField(
        "home.Batch_Table",
        on_delete=models.PROTECT,
        related_name="laboratory_accession",
        null=True,
        blank=True,
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="laboratory_accession_ranges",
    )
    assigned_at = models.DateTimeField(auto_now_add=True)
    batch_created_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "laboratory_accession_ranges"
        ordering = ("date_referred", "batch_number", "start_number")
        constraints = [
            models.UniqueConstraint(
                fields=("laboratory_receiving", "start_number", "end_number"),
                name="lab_accession_unique_range",
            ),
            models.CheckConstraint(
                check=Q(start_number__lte=F("end_number")),
                name="lab_accession_ordered_range",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.start_number and self.end_number and self.start_number > self.end_number:
            errors["start_number"] = "Start number cannot be greater than end number."
        if self.batch_number > self.total_batches:
            errors["batch_number"] = "Batch number cannot exceed total batches."
        if self.laboratory_receiving_id and self.start_number and self.end_number:
            overlapping = type(self).objects.filter(
                laboratory_receiving_id=self.laboratory_receiving_id,
                start_number__lte=self.end_number,
                end_number__gte=self.start_number,
            )
            if self.pk:
                overlapping = overlapping.exclude(pk=self.pk)
            if overlapping.exists():
                errors["start_number"] = "This range overlaps another range for the same receipt."
            other_total = sum(
                item.end_number - item.start_number + 1
                for item in type(self).objects.filter(
                    laboratory_receiving_id=self.laboratory_receiving_id
                ).exclude(pk=self.pk)
            )
            if other_total + self.isolate_count > self.laboratory_receiving.isolates_accepted:
                errors["end_number"] = "Saved accession ranges cannot exceed the accepted isolate total."
        if errors:
            raise ValidationError(errors)

    @property
    def reference_range(self):
        return f"{self.start_number:04d}-{self.end_number:04d}"

    @property
    def isolate_count(self):
        return self.end_number - self.start_number + 1

    @property
    def accession_numbers(self):
        year = self.date_referred.strftime("%y")
        site_code = self.laboratory_receiving.receipt.site.SiteCode.strip()
        return [
            f"{year}ARS_{site_code}{number:04d}"
            for number in range(self.start_number, self.end_number + 1)
        ]

    def __str__(self):
        return f"{self.laboratory_receiving.receipt.site.SiteCode} {self.reference_range}"


class LaboratoryOrganismCount(models.Model):
    class OrganismCode(models.TextChoices):
        HA = "HA", "Haemophilus"
        NGO = "NGO", "Neisseria gonorrhoeae"
        SPN = "SPN", "Streptococcus pneumoniae"
        FAST_OTHER = "FAST_OTHER", "Other fastidious"
        ECO = "ECO", "Escherichia coli"
        KLE = "KLE", "Klebsiella"
        ENTERO_OTHER = "ENTERO_OTHER", "Other Enterobacterales"
        SAL = "SAL", "Salmonella"
        SHI = "SHI", "Shigella"
        VIB = "VIB", "Vibrio"
        GNR_OTHER = "GNR_OTHER", "Other Gram-negative"
        PAE = "PAE", "Pseudomonas aeruginosa"
        ACI = "ACI", "Acinetobacter"
        NFO_OTHER = "NFO_OTHER", "Other non-fermenting"
        SAU = "SAU", "Staphylococcus aureus"
        ENT = "ENT", "Enterococcus"
        STR = "STR", "Streptococcus"
        GP_OTHER = "GP_OTHER", "Other Gram-positive"

    GROUPS = {
        "HA": "fastidious", "NGO": "fastidious", "SPN": "fastidious", "FAST_OTHER": "fastidious",
        "ECO": "enterobacterales", "KLE": "enterobacterales", "ENTERO_OTHER": "enterobacterales",
        "SAL": "gram_negative", "SHI": "gram_negative", "VIB": "gram_negative", "GNR_OTHER": "gram_negative",
        "PAE": "non_fermenting", "ACI": "non_fermenting", "NFO_OTHER": "non_fermenting",
        "SAU": "gram_positive", "ENT": "gram_positive", "STR": "gram_positive", "GP_OTHER": "gram_positive",
    }

    accession_range = models.ForeignKey(
        LaboratoryAccession,
        on_delete=models.PROTECT,
        related_name="organism_counts",
    )
    organism_code = models.CharField(max_length=20, choices=OrganismCode.choices)
    count = models.PositiveIntegerField(validators=[MinValueValidator(1)])

    class Meta:
        db_table = "laboratory_organism_counts"
        ordering = ("accession_range", "organism_code")
        constraints = [
            models.UniqueConstraint(
                fields=("accession_range", "organism_code"),
                name="lab_organism_unique_per_range",
            ),
        ]

    def clean(self):
        super().clean()
        if not self.accession_range_id or not self.count:
            return
        existing = type(self).objects.filter(accession_range_id=self.accession_range_id)
        if self.pk:
            existing = existing.exclude(pk=self.pk)
        additional = sum(self.accession_range.additional_organism_counts.values_list("count", flat=True))
        if sum(existing.values_list("count", flat=True)) + additional + self.count > self.accession_range.isolate_count:
            raise ValidationError({"count": "Organism counts cannot exceed the accession-range isolate count."})

    @property
    def organism_group(self):
        return self.GROUPS[self.organism_code]

    def __str__(self):
        return f"{self.get_organism_code_display()}: {self.count}"


class LaboratoryAdditionalOrganismCount(models.Model):
    accession_range = models.ForeignKey(LaboratoryAccession, on_delete=models.PROTECT,
        related_name="additional_organism_counts")
    whonet_code = models.CharField(max_length=20, blank=True, default="")
    organism_name = models.CharField(max_length=255, blank=True, default="")
    register_category = models.CharField(max_length=20, blank=True, default="", choices=LaboratoryOrganismCount.OrganismCode.choices)
    inventory_counts = models.JSONField(default=dict, blank=True)
    count = models.PositiveIntegerField(default=0, validators=[MinValueValidator(0)])

    class Meta:
        ordering = ("accession_range", "organism_name", "pk")
        constraints = [models.UniqueConstraint(fields=("accession_range", "whonet_code"),
            condition=~Q(whonet_code=""),
            name="lab_additional_organism_per_range")]

    def clean(self):
        super().clean()
        if self.inventory_counts:
            allowed = set(LaboratoryOrganismCount.OrganismCode.values)
            if not isinstance(self.inventory_counts, dict) or any(
                key not in allowed or type(value) is not int or value < 0
                for key, value in self.inventory_counts.items()
            ):
                raise ValidationError({"inventory_counts": "Enter non-negative whole counts in the original organism columns."})
            self.count = sum(self.inventory_counts.values())
        if not self.accession_range_id or not self.count:
            return
        fixed = sum(self.accession_range.organism_counts.values_list("count", flat=True))
        extra = sum(type(self).objects.filter(accession_range_id=self.accession_range_id)
                    .exclude(pk=self.pk).values_list("count", flat=True))
        if fixed + extra + self.count > self.accession_range.isolate_count:
            raise ValidationError({"count": "All organism rows together cannot exceed the accession-range isolate count."})

    @property
    def inventory_values(self):
        if self.inventory_counts:
            return self.inventory_counts
        return {self.register_category: self.count} if self.register_category else {}


class LaboratoryIsolate(models.Model):
    """One accepted isolate; accessions are assigned by Laboratory Receiving."""
    class Status(models.TextChoices):
        REVIVAL_PENDING = "revival_pending", "Awaiting revival"
        REVIVAL_QUEUED = "revival_queued", "Queued for revival"
        REVIVAL_IN_PROGRESS = "revival_in_progress", "Revival in progress"
        REVIVED = "revived", "Revival completed"
        REVIVAL_FAILED = "revival_failed", "Revival failed"
        NON_VIABLE = "non_viable", "Non-viable"
        ID_QUEUED = "id_queued", "Queued for Main Laboratory endorsement"
        ID_PENDING = "id_pending", "Awaiting Main Laboratory acknowledgment"
        ID_TESTING = "id_testing", "ID testing"
        ID_RETURNED = "id_returned", "ID results and cassette returned"
        ID_REVIEWED = "id_reviewed", "Identification reviewed"
        READY_FOR_BIOBANK = "ready_for_biobank", "Ready for biobank"
        SITE_BANKED = "site_banked", "Temporarily banked using Site ID"
        BIOBANKED = "biobanked", "Final biobank complete"

    accession_range = models.ForeignKey(
        LaboratoryAccession, on_delete=models.PROTECT, related_name="isolate_records"
    )
    accession_number = models.CharField(max_length=80, unique=True, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.REVIVAL_PENDING)
    identification_source = models.CharField(
        max_length=10, blank=True, default="",
        choices=(("site", "Referring site"), ("main_lab", "Main Laboratory")),
    )
    site_organism_code = models.CharField(max_length=20, blank=True, default="")
    site_organism_name = models.CharField(max_length=255, blank=True, default="")
    site_identified_at = models.DateTimeField(null=True, blank=True)
    site_identified_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="laboratory_site_identifications")
    id_queued_at = models.DateTimeField(null=True, blank=True)
    id_queued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="isolates_queued_for_id",
    )
    revival_queued_at = models.DateTimeField(null=True, blank=True)
    revival_queued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="lab_revival_queued_isolates",
    )
    revival_started_at = models.DateTimeField(null=True, blank=True)
    revival_started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="lab_revival_started_isolates",
    )
    revival_failed_reason = models.TextField(blank=True, default="")
    revival_retry_count = models.PositiveIntegerField(default=0)
    revival_retried_at = models.DateTimeField(null=True, blank=True)
    revival_retried_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="lab_revival_retried_isolates",
    )
    revival_completed_at = models.DateTimeField(null=True, blank=True)
    revival_completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="lab_revival_completed_isolates",
    )
    endorsed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="isolates_endorsed_for_id",
    )
    endorsed_at = models.DateTimeField(null=True, blank=True)
    acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="isolates_acknowledged_for_id",
    )
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    returned_at = models.DateTimeField(null=True, blank=True)
    returned_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="lab_id_returned_isolates")
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="isolates_id_reviewed",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    final_organism = models.CharField(max_length=255, blank=True, default="")
    approved_organism_code = models.CharField(max_length=20, blank=True, default="")
    lyophilization_completed_at = models.DateTimeField(null=True, blank=True)
    lyophilization_completed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="lab_lyophilization_completed_isolates")
    site_banked_at = models.DateTimeField(null=True, blank=True)
    site_banked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="lab_site_banked_isolates")
    site_bank_storage = models.JSONField(default=dict, blank=True)
    biobanked_at = models.DateTimeField(null=True, blank=True)
    biobanked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="lab_biobanked_isolates")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "laboratory_isolates"
        ordering = ("accession_number",)

    def __str__(self):
        return self.accession_number


class LaboratoryIdentification(models.Model):
    """An instrument or manually transcribed ID result, including retests."""
    class Source(models.TextChoices):
        MANUAL = "manual", "Manual transcription"
        CSV = "csv", "CSV upload"
        XLSX = "xlsx", "Excel upload"

    isolate = models.ForeignKey(
        LaboratoryIsolate, on_delete=models.PROTECT, related_name="identifications"
    )
    organism_reported = models.CharField(max_length=255)
    whonet_organism_code = models.CharField(max_length=20, blank=True, default="")
    instrument = models.CharField(max_length=100, blank=True, default="")
    cassette_reference = models.CharField(max_length=100, blank=True, default="")
    tested_at = models.DateTimeField(null=True, blank=True)
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.MANUAL)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="laboratory_id_entries",
    )
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "laboratory_identifications"
        ordering = ("-recorded_at", "-id")

    def __str__(self):
        return f"{self.isolate.accession_number}: {self.organism_reported}"


class LaboratoryBiobank(models.Model):
    """Preservation and storage details; stage checks are enforced by workflow services."""
    isolate = models.OneToOneField(
        LaboratoryIsolate, on_delete=models.PROTECT, related_name="biobank"
    )
    date_harvested = models.DateField(null=True, blank=True)
    whonet_code = models.CharField(max_length=30, blank=True, default="")
    date_lyophilized = models.DateField(null=True, blank=True)
    lyo_box_name = models.CharField(max_length=100, blank=True, default="")
    lyo_box_number = models.CharField(max_length=50, blank=True, default="")
    lyo_position = models.CharField(max_length=50, blank=True, default="")
    refrigerator_number = models.CharField(max_length=50, blank=True, default="")
    lyo_location = models.CharField(max_length=100, blank=True, default="")
    lyo_date_encoded = models.DateField(null=True, blank=True)
    skimmed_milk = models.BooleanField(default=False)
    tsbg = models.BooleanField(default=False)
    supplementary_box_name = models.CharField(max_length=100, blank=True, default="")
    supplementary_box_number = models.CharField(max_length=50, blank=True, default="")
    supplementary_position = models.CharField(max_length=50, blank=True, default="")
    freezer_number = models.CharField(max_length=50, blank=True, default="")
    supplementary_location = models.CharField(max_length=100, blank=True, default="")
    supplementary_date_encoded = models.DateField(null=True, blank=True)
    preservation_method = models.CharField(max_length=100, blank=True, default="")
    container = models.CharField(max_length=100, blank=True, default="")
    storage_location = models.CharField(max_length=150, blank=True, default="")
    storage_box = models.CharField(max_length=100, blank=True, default="")
    box_position = models.CharField(max_length=30, blank=True, default="")
    remarks = models.TextField(blank=True, default="")
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="laboratory_biobank_entries",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "laboratory_biobank"

    def __str__(self):
        return self.isolate.accession_number
