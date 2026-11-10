from django.conf import settings
from django.db import models

from apps.home_final.models import Classification_Table, Final_Data

# Create your models here.


class DemogsData_upload(models.Model):
    DemogsDataFile = models.FileField(upload_to='uploads/final/', null=True, blank=True)

    class Meta:
        db_table ="DemogsData_upload"




# Connector Table for WGS Projects
class WGS_Project(models.Model):
    Ref_Accession = models.ForeignKey(
        'home_final.Final_Data',    # connects to Final_Data model
        on_delete=models.SET_NULL,  # Keep the WGS project when Final Data is deleted
        null=True,
        blank=True,
        related_name='project_entries',
        to_field='f_AccessionNo'
    )
    # Persistent accession identifier for restoring a Final Data match.
    original_accession = models.CharField(
        max_length=255, blank=True, default="", db_index=True
    )

    WGS_SampleInfo_Acc = models.CharField(max_length=255, blank=True, null=True)
    WGS_SampleInfoSummary = models.BooleanField(default=False, blank=True)

    WGS_BactScout_Acc = models.CharField(max_length=255, blank=True, null=True)
    WGS_BactScoutSummary = models.BooleanField(default=False, blank=True)

    WGS_GtdbTk_Acc = models.CharField(max_length=255, blank=True, null=True)
    WGS_GtdbTkSummary = models.BooleanField(default=False, blank=True)


    WGS_Gambit_Acc = models.CharField(max_length=255, blank=True, null=True)
    WGS_GambitSummary = models.BooleanField(default=False, blank=True)
    WGS_Mlst_Acc = models.CharField(max_length=255, blank=True, null=True)
    WGS_MlstSummary = models.BooleanField(default=False, blank=True, null=True)
    WGS_Checkm2_Acc = models.CharField(max_length=255, blank=True, null=True)
    WGS_Checkm2Summary = models.BooleanField(default=False, blank=True)
    WGS_Assembly_Acc = models.CharField(max_length=255, blank=True, null=True)
    WGS_AssemblySummary = models.BooleanField(default=False, blank=True)
    WGS_Amrfinder_Acc = models.CharField(max_length=255, blank=True, null=True)
    WGS_AmrfinderSummary = models.BooleanField(default=False, blank=True)

    class Meta:
        db_table = "WGS_Project"  # table name in DB
        verbose_name = "WGS Project"
        verbose_name_plural = "WGS Projects"


    def __str__(self):
        return str(self.Ref_Accession) if self.Ref_Accession else ""


class CustomWGSPipeline(models.Model):
    CATEGORY_CHOICES = [
        ("quality_control", "Quality control"),
        ("genome_assembly", "Genome assembly"),
        ("species_identification", "Species identification"),
        ("serotyping", "Serotyping"),
        ("phylogrouping", "Phylogrouping"),
        ("sequence_typing", "Sequence typing"),
        ("amr_detection", "Antimicrobial resistance detection"),
        ("virulence_detection", "Virulence detection"),
        ("genome_annotation", "Genome annotation"),
        ("variant_calling", "Variant calling"),
        ("phylogenetics", "Phylogenetic analysis"),
        ("other", "Other"),
    ]
    SEQUENCING_TYPE_CHOICES = [
        ("short_read", "Short read"),
        ("long_read", "Long read"),
        ("hybrid", "Hybrid"),
        ("other", "Other"),
    ]
    PLATFORM_LABELS = {
        "illumina": "Illumina",
        "nanopore": "Nanopore",
        "pacbio": "PacBio",
        "hybrid": "Hybrid",
        "other": "Other",
    }

    name = models.CharField(max_length=150, unique=True)
    slug = models.SlugField(max_length=170, unique=True)
    description = models.TextField(blank=True)
    sequencing_type = models.CharField(max_length=20, choices=SEQUENCING_TYPE_CHOICES, default="short_read")
    platform = models.CharField(max_length=100, default="Illumina")
    category = models.JSONField(default=list, blank=True)
    sheet_name = models.CharField(max_length=120, blank=True)
    accession_column = models.CharField(max_length=120, default="accession")
    sample_name_column = models.CharField(max_length=120, blank=True)
    date_column = models.CharField(max_length=120, blank=True)
    show_in_upload_center = models.BooleanField(default=True)
    show_in_overview = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="custom_wgs_pipelines",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "custom_wgs_pipeline"
        ordering = ["sequencing_type", "name"]

    def __str__(self):
        return self.name

    def category_values(self):
        if isinstance(self.category, list):
            return [value for value in self.category if value]
        if self.category:
            return [str(self.category)]
        return []

    def get_category_display(self):
        labels = dict(self.CATEGORY_CHOICES)
        return ", ".join(labels.get(value, value) for value in self.category_values())

    def get_platform_display(self):
        platform = str(self.platform or "").strip()
        return self.PLATFORM_LABELS.get(platform.lower(), platform)


class CustomWGSPipelineField(models.Model):
    DATA_TYPE_CHOICES = [
        ("text", "Text"),
        ("integer", "Integer"),
        ("decimal", "Decimal"),
        ("date", "Date"),
        ("boolean", "Boolean"),
    ]

    pipeline = models.ForeignKey(CustomWGSPipeline, on_delete=models.CASCADE, related_name="fields")
    field_key = models.SlugField(max_length=120)
    display_label = models.CharField(max_length=150)
    source_column = models.CharField(max_length=150)
    column_aliases = models.TextField(blank=True)
    data_type = models.CharField(max_length=20, choices=DATA_TYPE_CHOICES, default="text")
    required = models.BooleanField(default=False)
    default_value = models.CharField(max_length=255, blank=True)
    show_in_table = models.BooleanField(default=True)
    show_in_detail = models.BooleanField(default=True)
    show_in_export = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "custom_wgs_pipeline_field"
        ordering = ["sort_order", "display_label"]
        unique_together = (("pipeline", "field_key"),)

    def __str__(self):
        return f"{self.pipeline.name}: {self.display_label}"

    def upload_column_options(self):
        options = [self.source_column]
        options.extend(line.strip() for line in self.column_aliases.splitlines() if line.strip())
        return options


class CustomWGSPipelineUploadBatch(models.Model):
    STATUS_CHOICES = [
        ("completed", "Completed"),
        ("completed_with_warnings", "Completed with warnings"),
        ("failed", "Failed"),
    ]

    pipeline = models.ForeignKey(CustomWGSPipeline, on_delete=models.CASCADE, related_name="upload_batches")
    file_name = models.CharField(max_length=255)
    sheet_name = models.CharField(max_length=120, blank=True)
    row_count = models.PositiveIntegerField(default=0)
    created_count = models.PositiveIntegerField(default=0)
    updated_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    matched_count = models.PositiveIntegerField(default=0)
    unmatched_count = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="completed")
    error_log = models.TextField(blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="custom_wgs_upload_batches",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "custom_wgs_pipeline_upload_batch"
        ordering = ["-uploaded_at", "-id"]

    def __str__(self):
        return f"{self.pipeline.name} upload {self.uploaded_at:%Y-%m-%d %H:%M}"


class CustomWGSPipelineRecord(models.Model):
    MATCH_STATUS_CHOICES = [
        ("matched", "Matched"),
        ("unmatched", "Unmatched"),
        ("invalid", "Invalid"),
    ]
    MATCH_SOURCE_CHOICES = [
        ("final", "Final data"),
        ("wgs_project", "WGS project"),
        ("", "None"),
    ]

    pipeline = models.ForeignKey(CustomWGSPipeline, on_delete=models.CASCADE, related_name="records")
    upload_batch = models.ForeignKey(
        CustomWGSPipelineUploadBatch,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="records",
    )
    accession = models.CharField(max_length=255, db_index=True)
    sample_name = models.CharField(max_length=255, blank=True)
    matched_final_data = models.ForeignKey(
        "home_final.Final_Data",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="custom_wgs_records",
        to_field="f_AccessionNo",
    )
    match_status = models.CharField(max_length=20, choices=MATCH_STATUS_CHOICES, default="unmatched", db_index=True)
    match_source = models.CharField(max_length=20, choices=MATCH_SOURCE_CHOICES, blank=True, default="")
    values_json = models.JSONField(default=dict, blank=True)
    raw_row_json = models.JSONField(default=dict, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="custom_wgs_records",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "custom_wgs_pipeline_record"
        ordering = ["pipeline", "accession", "-uploaded_at"]
        indexes = [
            models.Index(fields=["pipeline", "accession"]),
            models.Index(fields=["pipeline", "match_status"]),
        ]

    def __str__(self):
        return f"{self.pipeline.name}: {self.accession}"


class BuiltinWGSPipelineSetting(models.Model):
    PIPELINE_CHOICES = [
        ("sample_information", "Sample Information"),
        ("bactscout", "BactScout"),
        ("gtdbtk", "GTDB-Tk"),
        ("gambit", "Gambit"),
        ("mlst", "MLST"),
        ("checkm2", "CheckM2"),
        ("assembly", "Assembly Scan"),
        ("amrfinder", "AMRFinderPlus"),
    ]

    pipeline_key = models.CharField(max_length=50, choices=PIPELINE_CHOICES, unique=True)
    display_name = models.CharField(max_length=100)
    show_in_upload_center = models.BooleanField(default=True)
    show_in_overview = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "builtin_wgs_pipeline_setting"
        ordering = ["sort_order", "display_name"]

    def __str__(self):
        return self.display_name


# ──────────────────────────────────────────────
# Sample Information
# ──────────────────────────────────────────────
class SampleInformation(models.Model):
    sample_project = models.ForeignKey(
        "wgs_app.WGS_Project",
        on_delete=models.SET_NULL,
        null=True,
        related_name="sample_information_entries"
    )
    sample_accession = models.CharField(max_length=255, blank=True, null=True)
    batch_code = models.CharField(max_length=255, blank=True, null=True)
    sample_name = models.CharField(max_length=255, blank=True, null=True)
    status = models.CharField(max_length=255, blank=True, null=True)
    DNA_extraction = models.BooleanField(default=False, blank=True, db_default=False)
    dna_performed_by = models.CharField(max_length=255, blank=True, null=True)
    dna_performed_date = models.DateField(blank=True, null=True)
    library_preparation = models.BooleanField(default=False, blank=True, db_default=False)
    library_performed_by = models.CharField(max_length=255, blank=True, null=True)
    library_performed_date = models.DateField(blank=True, null=True)
    sequencing_platform = models.BooleanField(default=False, blank=True, db_default=False)


    # surveillance / programme flags
    emerging = models.BooleanField(default=False)
    structured = models.BooleanField(default=False)
    satscan = models.BooleanField(default=False)
    serotyping = models.BooleanField(default=False)
    ghru = models.BooleanField(default=False)
    ghru_all = models.BooleanField(default=False)
    ghru_neo = models.BooleanField(default=False)
    egasp = models.BooleanField(default=False)
    tricycle = models.BooleanField(default=False)
    pulsenet = models.BooleanField(default=False)
    tulip = models.BooleanField(default=False)
    

    Date_uploaded_si = models.DateField(auto_now_add=True)

    class Meta:
        db_table = "sample_information"
        verbose_name = "Sample Information"
        verbose_name_plural = "Sample Information"

    def __str__(self):
        return self.sample_name or ""


class SampleInfoUpload(models.Model):
    sampleinfo = models.FileField(upload_to='uploads/wgs/bactscout/', null=True, blank=True)

    class Meta:
        db_table = "SampleInfoUpload"


# ──────────────────────────────────────────────
# BactScout
# ──────────────────────────────────────────────
class BactScout(models.Model):
    FIELD_LABELS = [
        ("sample_id", "sample_id"),
        ("a_final_status", "a_final_status"),
        ("adapter_detection_status", "adapter_detection_status"),
        ("contamination_status", "contamination_status"),
        ("species_status", "species_status"),
        ("coverage_status", "coverage_status"),
        ("coverage_estimate_qualibact_status", "coverage_estimate_qualibact_status"),
        ("duplication_status", "duplication_status"),
        ("gc_content_status", "gc_content_status"),
        ("mlst_status", "mlst_status"),
        ("n_content_status", "n_content_status"),
        ("read_length_status", "read_length_status"),
        ("read_q30_status", "read_q30_status"),
        ("species", "species"),
        ("species_abundance", "species_abundance"),
        ("species_coverage", "species_coverage"),
        ("species_message", "species_message"),
        ("contamination_message", "contamination_message"),
        ("coverage_estimate_sylph", "coverage_estimate_sylph"),
        ("coverage_estimate_sylph_message", "coverage_estimate_sylph_message"),
        ("coverage_estimate_qualibact", "coverage_estimate_qualibact"),
        ("coverage_estimate_qualibact_message", "coverage_estimate_qualibact_message"),
        ("duplication_rate", "duplication_rate"),
        ("duplication_message", "duplication_message"),
        ("gc_content", "gc_content"),
        ("gc_content_lower", "gc_content_lower"),
        ("gc_content_upper", "gc_content_upper"),
        ("gc_content_message", "gc_content_message"),
        ("n_content_rate", "n_content_rate"),
        ("n_content_message", "n_content_message"),
        ("mlst_st", "mlst_st"),
        ("mlst_message", "mlst_message"),
        ("read1_mean_length", "read1_mean_length"),
        ("read2_mean_length", "read2_mean_length"),
        ("read_length_message", "read_length_message"),
        ("read_q20_bases", "read_q20_bases"),
        ("read_q20_rate", "read_q20_rate"),
        ("read_q30_bases", "read_q30_bases"),
        ("read_q30_rate", "read_q30_rate"),
        ("read_q30_message", "read_q30_message"),
        ("read_total_bases", "read_total_bases"),
        ("read_total_reads", "read_total_reads"),
        ("adapter_detection_message", "adapter_detection_message"),
        ("ref_genome", "ref_genome"),
        ("genome_size_expected", "genome_size_expected"),
    ]
    UPLOAD_FIELDS = [field for field, _label in FIELD_LABELS]

    bactscout_project = models.ForeignKey(
        "wgs_app.WGS_Project",
        on_delete=models.SET_NULL,
        null=True,
        related_name="bactscout_entries"
    )
    BactScout_Accession = models.CharField(max_length=255, blank=True, null=True, db_index=True)

    # sample information
    sample_id = models.CharField(max_length=255, blank=True, null=True)

    # overall status flags
    a_final_status = models.CharField(max_length=255, blank=True, null=True)
    adapter_detection_status = models.CharField(max_length=255, blank=True, null=True)
    contamination_status = models.CharField(max_length=255, blank=True, null=True)
    species_status = models.CharField(max_length=255, blank=True, null=True)
    coverage_status = models.CharField(max_length=255, blank=True, null=True)
    coverage_estimate_qualibact_status = models.CharField(max_length=255, blank=True, null=True)
    duplication_status = models.CharField(max_length=255, blank=True, null=True)
    gc_content_status = models.CharField(max_length=255, blank=True, null=True)
    mlst_status = models.CharField(max_length=255, blank=True, null=True)
    n_content_status = models.CharField(max_length=255, blank=True, null=True)
    read_length_status = models.CharField(max_length=255, blank=True, null=True)
    read_q30_status = models.CharField(max_length=255, blank=True, null=True)

    # species
    species = models.CharField(max_length=255, blank=True, null=True)
    species_abundance = models.TextField(blank=True, null=True)                     # e.g. 93.875 or 94.0779;0.5709
    species_coverage = models.TextField(blank=True, null=True)                      # e.g. 254.4 or 28.808;0.278
    species_message = models.TextField(blank=True, null=True)

    # contamination
    contamination_message = models.TextField(blank=True, null=True)

    # coverage
    coverage_estimate_sylph = models.FloatField(blank=True, null=True)              # e.g. 54.4
    coverage_estimate_sylph_message = models.TextField(blank=True, null=True)
    coverage_estimate_qualibact = models.FloatField(blank=True, null=True)          # e.g. 79.12
    coverage_estimate_qualibact_message = models.TextField(blank=True, null=True)

    # duplication
    duplication_rate = models.FloatField(blank=True, null=True)                     # e.g. 0.001143
    duplication_message = models.TextField(blank=True, null=True)

    # GC content (bactscout)
    gc_content = models.FloatField(blank=True, null=True)                           # e.g. 50.707
    gc_content_lower = models.IntegerField(blank=True, null=True)                   # e.g. 50
    gc_content_upper = models.IntegerField(blank=True, null=True)                   # e.g. 52
    gc_content_message = models.TextField(blank=True, null=True)

    # N content
    n_content_rate = models.FloatField(blank=True, null=True)                       # e.g. 0.0
    n_content_message = models.TextField(blank=True, null=True)

    # MLST (summary from bactscout)
    mlst_st = models.CharField(max_length=255, blank=True, null=True)               # e.g. "405" or text
    mlst_message = models.TextField(blank=True, null=True)

    # read length
    read1_mean_length = models.IntegerField(blank=True, null=True)                  # e.g. 129
    read2_mean_length = models.IntegerField(blank=True, null=True)                  # e.g. 130
    read_length_message = models.TextField(blank=True, null=True)

    # read quality
    read_q20_bases = models.FloatField(blank=True, null=True)                       # e.g. 3.88E+08 (stored as float)
    read_q20_rate = models.FloatField(blank=True, null=True)                        # e.g. 0.9421
    read_q30_bases = models.FloatField(blank=True, null=True)                       # e.g. 3.81E+08
    read_q30_rate = models.FloatField(blank=True, null=True)                        # e.g. 0.9258
    read_q30_message = models.TextField(blank=True, null=True)
    read_total_bases = models.FloatField(blank=True, null=True)                     # e.g. 4.11E+08
    read_total_reads = models.IntegerField(blank=True, null=True)                   # e.g. 3162342

    # adapter
    adapter_detection_message = models.TextField(blank=True, null=True)

    # reference
    ref_genome = models.CharField(max_length=255, blank=True, null=True)            # e.g. GCF_003697165.2
    genome_size_expected = models.IntegerField(blank=True, null=True)               # e.g. 5200000

    Date_uploaded_bs = models.DateField(auto_now_add=True)

    class Meta:
        db_table = "BactScout"

    def __str__(self):
        return self.sample_id or ""


class BactScoutUpload(models.Model):
    bactscoutfile = models.FileField(upload_to='uploads/wgs/bactscout/', null=True, blank=True)

    class Meta:
        db_table = "BactScoutUpload"




# ──────────────────────────────────────────────
# GTDB-Tk
# ──────────────────────────────────────────────
class GtdbTk(models.Model):
    gtdbtk_project = models.ForeignKey(
        "wgs_app.WGS_Project",
        on_delete=models.SET_NULL,
        null=True,
        related_name="gtdbtk_entries"
    )
    GtdbTk_Accession = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    user_genome = models.CharField(max_length=255, blank=True, null=True)           # sample ID
    classification = models.TextField(blank=True, null=True)                        # full taxonomy string d__;p__;...

    # closest genome (always populated)
    closest_genome_reference = models.CharField(max_length=255, blank=True, null=True)     # e.g. GCF_003315235.1
    closest_genome_reference_radius = models.IntegerField(blank=True, null=True)           # e.g. 95
    closest_genome_taxonomy = models.TextField(blank=True, null=True)                      # full taxonomy string
    closest_genome_ani = models.FloatField(blank=True, null=True)                          # e.g. 99.56
    closest_genome_af = models.FloatField(blank=True, null=True)                           # e.g. 0.974

    # closest placement (N/A when ani_screen method used)
    closest_placement_reference = models.CharField(max_length=255, blank=True, null=True)  # N/A or GCF_...
    closest_placement_radius = models.FloatField(blank=True, null=True)                    # N/A → null
    closest_placement_taxonomy = models.TextField(blank=True, null=True)
    closest_placement_ani = models.FloatField(blank=True, null=True)                       # N/A → null
    closest_placement_af = models.FloatField(blank=True, null=True)                        # N/A → null

    pplacer_taxonomy = models.TextField(blank=True, null=True)                             # N/A or taxonomy
    classification_method = models.CharField(max_length=255, blank=True, null=True)        # e.g. ani_screen
    note = models.TextField(blank=True, null=True)                                         # e.g. classification based on ANI only
    other_related_references = models.TextField(blank=True, null=True)                     # genome_id, species_name, radius, ANI, AF (long)
    msa_percent = models.FloatField(blank=True, null=True)                                 # N/A → null
    translation_table = models.IntegerField(blank=True, null=True)                         # N/A → null
    red_value = models.FloatField(blank=True, null=True)                                   # N/A → null
    warnings = models.TextField(blank=True, null=True)

    Date_uploaded_gt = models.DateField(auto_now_add=True)

    class Meta:
        db_table = "gtdbtk"

    def __str__(self):
        return self.user_genome or ""


class GtdbTkUpload(models.Model):
    GtdbTkFile = models.FileField(upload_to='uploads/wgs/gtdbtk/', null=True, blank=True)

    class Meta:
        db_table = "GtdbTkUpload"





# gambit
class Gambit(models.Model):
    FIELD_LABELS = [
        ("sample", "sample"),
        ("predicted_name", "predicted.name"),
        ("predicted_rank", "predicted.rank"),
        ("predicted_ncbi_id", "predicted.ncbi_id"),
        ("predicted_threshold", "predicted.threshold"),
        ("closest_distance", "closest.distance"),
        ("closest_description", "closest.description"),
        ("next_name", "next.name"),
        ("next_rank", "next.rank"),
        ("next_ncbi_id", "next.ncbi_id"),
        ("next_threshold", "next.threshold"),
    ]
    UPLOAD_FIELDS = [field for field, _label in FIELD_LABELS]

    gambit_project = models.ForeignKey(
        "wgs_app.WGS_Project",   # connects to WGS_Project model
        on_delete=models.SET_NULL,
        null=True,
        related_name="gambit_entries"
    )
    Gambit_Accession = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    sample = models.CharField(max_length=255, blank=True, null=True)
    predicted_name = models.CharField(max_length=255, blank=True, null=True)
    predicted_rank = models.CharField(max_length=255, blank=True, null=True)
    predicted_ncbi_id = models.CharField(max_length=255, blank=True, null=True)
    predicted_threshold = models.CharField(max_length=255, blank=True, null=True)
    closest_distance = models.CharField(max_length=255, blank=True, null=True)
    closest_description = models.CharField(max_length=255, blank=True, null=True)
    next_name = models.CharField(max_length=255, blank=True, null=True)
    next_rank = models.CharField(max_length=255, blank=True, null=True)
    next_ncbi_id = models.CharField(max_length=255, blank=True, null=True)
    next_threshold = models.CharField(max_length=255, blank=True, null=True)
    Date_uploaded_g= models.DateField(auto_now_add=True)

    class Meta:
        db_table = "gambit"
    def __str__(self):
        return self.sample or ""


# uploading project files
class GambitUpload(models.Model):
    GambitFile = models.FileField(upload_to='uploads/wgs/gambit/', null=True, blank=True)

    class Meta:
        db_table = "GambitUpload"


class GambitDisplayConfig(models.Model):
    field_name = models.CharField(max_length=100, unique=True)
    show = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.field_name} ({'Show' if self.show else 'Hide'})"



# mlst
class Mlst(models.Model):
    FIELD_LABELS = [
        ("file", "FILE"),
        ("scheme", "SCHEME"),
        ("st", "ST"),
        ("status", "STATUS"),
        ("score", "SCORE"),
        ("alleles", "ALLELES"),
    ]
    UPLOAD_FIELDS = (
        "file",
        "scheme",
        "st",
        "status",
        "score",
        "alleles",
    )

    mlst_project = models.ForeignKey(
        "wgs_app.WGS_Project",   # connects to WGS_Project model
        on_delete=models.SET_NULL,
        null=True,
        related_name="mlst_entries"
    )
    Mlst_Accession = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    file = models.CharField(max_length=255, blank=True, null=True)
    scheme = models.CharField(max_length=255, blank=True, null=True)
    st = models.CharField(max_length=255, blank=True, null=True)
    status = models.CharField(max_length=255, blank=True, null=True)
    score = models.CharField(max_length=255, blank=True, null=True)
    alleles = models.TextField(blank=True, null=True)
    Date_uploaded_m = models.DateField(auto_now_add=True)
    class Meta:
        db_table = "mlst"
    def __str__(self):
        return self.file or ""

class MlstUpload(models.Model):
    Mlstfile = models.FileField(upload_to='uploads/wgs/mlst/', null=True, blank=True)

    class Meta:
        db_table = "MlstUpload"


# Checkm2
class Checkm2(models.Model):
    FIELD_LABELS = [
        ("Name", "Name"),
        ("Completeness", "Completeness"),
        ("Contamination", "Contamination"),
        ("Completeness_Model_Used", "Completeness Model Used"),
        ("Translation_Table_Used", "Translation Table Used"),
        ("Coding_Density", "Coding Density"),
        ("Contig_N50", "Contig N50"),
        ("Average_Gene_Length", "Average Gene Length"),
        ("Genome_Size", "Genome Size"),
        ("GC_Content", "GC Content"),
        ("Total_Coding_Sequences", "Total Coding Sequences"),
        ("Total_Contigs", "Total Contigs"),
        ("Max_Contig_Length", "Max Contig Length"),
        ("Additional_Notes", "Additional Notes"),
    ]
    UPLOAD_FIELDS = (
        "Name",
        "Completeness",
        "Contamination",
        "Completeness_Model_Used",
        "Translation_Table_Used",
        "Coding_Density",
        "Contig_N50",
        "Average_Gene_Length",
        "Genome_Size",
        "GC_Content",
        "Total_Coding_Sequences",
        "Total_Contigs",
        "Max_Contig_Length",
        "Additional_Notes",
    )

    checkm2_project = models.ForeignKey(
        "wgs_app.WGS_Project",   # connects to WGS_Project model
        on_delete=models.SET_NULL,
        null=True,
        related_name="checkm2_entries"
    )
    Checkm2_Accession = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    Name = models.CharField(max_length=255, blank=True, null=True)
    Completeness = models.CharField(max_length=255, blank=True, null=True)
    Contamination = models.CharField(max_length=255, blank=True, null=True)
    Completeness_Model_Used = models.CharField(max_length=255, blank=True, null=True)
    Translation_Table_Used = models.CharField(max_length=255, blank=True, null=True)
    Coding_Density = models.CharField(max_length=255, blank=True, null=True)
    Contig_N50 = models.CharField(max_length=255, blank=True, null=True)
    Average_Gene_Length = models.CharField(max_length=255, blank=True, null=True)
    Genome_Size = models.CharField(max_length=255, blank=True, null=True)
    GC_Content = models.CharField(max_length=255, blank=True, null=True)
    Total_Coding_Sequences = models.CharField(max_length=255, blank=True, null=True)
    Total_Contigs = models.CharField(max_length=255, blank=True, null=True)
    Max_Contig_Length = models.CharField(max_length=255, blank=True, null=True)
    Additional_Notes = models.CharField(max_length=255, blank=True, null=True)
    Date_uploaded_c = models.DateField(auto_now_add=True)
    class Meta:
        db_table = "checkm2"
    def __str__(self):
        return self.Name or ""

class Checkm2Upload(models.Model):
    Checkm2file = models.FileField(upload_to='uploads/wgs/checkm2/', null=True, blank=True)

    class Meta:
        db_table = "Checkm2Upload"


# AssemblyScan
class AssemblyScan(models.Model):
    assembly_project = models.ForeignKey(
        "wgs_app.WGS_Project",   # connects to WGS_Project model
        on_delete=models.SET_NULL,
        null=True,
        related_name="assembly_entries"
    )
    Assembly_Accession = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    sample = models.CharField(max_length=255, blank=True, null=True)
    total_contig = models.CharField(max_length=255, blank=True, null=True)
    total_contig_length = models.CharField(max_length=255, blank=True, null=True)
    max_contig_length = models.CharField(max_length=255, blank=True, null=True)
    mean_contig_length = models.CharField(max_length=255, blank=True, null=True)
    median_contig_length = models.CharField(max_length=255, blank=True, null=True)
    min_contig_length = models.CharField(max_length=255, blank=True, null=True)
    n50_contig_length = models.CharField(max_length=255, blank=True, null=True)
    l50_contig_count = models.CharField(max_length=255, blank=True, null=True)
    num_contig_non_acgtn = models.CharField(max_length=255, blank=True, null=True)
    contig_percent_a = models.CharField(max_length=255, blank=True, null=True)
    contig_percent_c = models.CharField(max_length=255, blank=True, null=True)
    contig_percent_g = models.CharField(max_length=255, blank=True, null=True)
    contig_percent_t = models.CharField(max_length=255, blank=True, null=True)
    contig_percent_n = models.CharField(max_length=255, blank=True, null=True)
    contig_non_acgtn = models.CharField(max_length=255, blank=True, null=True)
    contigs_greater_1m = models.CharField(max_length=255, blank=True, null=True)
    contigs_greater_100k = models.CharField(max_length=255, blank=True, null=True)
    contigs_greater_10k = models.CharField(max_length=255, blank=True, null=True)
    contigs_greater_1k = models.CharField(max_length=255, blank=True, null=True)
    percent_contigs_greater_1m = models.CharField(max_length=255, blank=True, null=True)
    percent_contigs_greater_100k = models.CharField(max_length=255, blank=True, null=True)
    percent_contigs_greater_10k = models.CharField(max_length=255, blank=True, null=True)
    percent_contigs_greater_1k = models.CharField(max_length=255, blank=True, null=True)
    Date_uploaded_as = models.DateField(auto_now_add=True)
    class Meta:
        db_table = "assembly-scan"
    def __str__(self):
        return self.sample or ""
    
class AssemblyUpload(models.Model):
    Assemblyfile = models.FileField(upload_to='uploads/wgs/assemblyscan/', null=True, blank=True)

    class Meta:
        db_table = "AssemblyUpload"

# Amrfinderplus
class Amrfinderplus(models.Model):
    FIELD_LABELS = [
        ("name", "Name"),
        ("protein_id", "Protein id"),
        ("contig_id", "Contig id"),
        ("start", "Start"),
        ("stop", "Stop"),
        ("strand", "Strand"),
        ("element_symbol", "Element symbol"),
        ("element_name", "Element name"),
        ("scope", "Scope"),
        ("type_field", "Type"),
        ("subtype", "Subtype"),
        ("class_field", "Class"),
        ("subclass", "Subclass"),
        ("method", "Method"),
        ("target_length", "Target length"),
        ("reference_sequence_length", "Reference sequence length"),
        ("percent_coverage_of_reference", "% Coverage of reference"),
        ("percent_identity_to_reference", "% Identity to reference"),
        ("alignment_length", "Alignment length"),
        ("closest_reference_accession", "Closest reference accession"),
        ("closest_reference_name", "Closest reference name"),
        ("hmm_accession", "HMM accession"),
        ("hmm_description", "HMM description"),
    ]
    UPLOAD_FIELDS = (
        "name",
        "protein_id",
        "contig_id",
        "start",
        "stop",
        "strand",
        "element_symbol",
        "element_name",
        "scope",
        "type_field",
        "subtype",
        "class_field",
        "subclass",
        "method",
        "target_length",
        "reference_sequence_length",
        "percent_coverage_of_reference",
        "percent_identity_to_reference",
        "alignment_length",
        "closest_reference_accession",
        "closest_reference_name",
        "hmm_accession",
        "hmm_description",
    )

    amrfinder_project = models.ForeignKey(
        "wgs_app.WGS_Project",   # connects to WGS_Project model
        on_delete=models.SET_NULL,
        null=True,
        related_name="amrfinder_entries"
    )
    Amrfinder_Accession = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    amrfinder_id = models.CharField(max_length=255, blank=True, null=True)
    name = models.CharField(max_length=255, blank=True, null=True)
    protein_id = models.CharField(max_length=255, blank=True, null=True)
    contig_id = models.CharField(max_length=255, blank=True, null=True)
    start = models.CharField(max_length=255, blank=True, null=True)
    stop = models.CharField(max_length=255, blank=True, null=True)
    strand = models.CharField(max_length=255, blank=True, null=True)
    element_symbol = models.CharField(max_length=255, blank=True, null=True)
    element_name = models.CharField(max_length=255, blank=True, null=True)
    scope = models.CharField(max_length=255, blank=True, null=True)
    type_field = models.CharField(max_length=255, blank=True, null=True)  # renamed from "Type"
    subtype = models.CharField(max_length=255, blank=True, null=True)
    class_field = models.CharField(max_length=255, blank=True, null=True)  # renamed from "Class"
    subclass = models.CharField(max_length=255, blank=True, null=True)
    method = models.CharField(max_length=255, blank=True, null=True)
    target_length = models.CharField(max_length=255, blank=True, null=True)
    reference_sequence_length = models.CharField(max_length=255, blank=True, null=True)
    percent_coverage_of_reference = models.CharField(max_length=255, blank=True, null=True)
    percent_identity_to_reference = models.CharField(max_length=255, blank=True, null=True)
    alignment_length = models.CharField(max_length=255, blank=True, null=True)
    closest_reference_accession = models.CharField(max_length=255, blank=True, null=True)
    closest_reference_name = models.CharField(max_length=255, blank=True, null=True)
    hmm_accession = models.CharField(max_length=255, blank=True, null=True)
    hmm_description = models.CharField(max_length=255, blank=True, null=True)
    hierarchy_node = models.CharField(max_length=255, blank=True, null=True)
    Date_uploaded_am = models.DateField(auto_now_add=True)
    class Meta:
        db_table = "amrfinderplus"
    def __str__(self):
        return self.name or "No name"


class AmrfinderUpload(models.Model):
    Amrfinderfile = models.FileField(upload_to='uploads/wgs/amrfinder/', null=True, blank=True)

    class Meta:
        db_table = "AmrfinderUpload"
