# apps/wgs_app/signals.py
from django.apps import apps
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from .models import *
from apps.home_final.models import Final_Data



def update_summary_flag(project, field_name):
    """Recalculate the flag value dynamically based on whether related records still exist."""
    related_model_map = {
        'WGS_SampleInfoSummary': (SampleInformation, 'sample_project'),
        'WGS_BactScoutSummary': (BactScout, 'bactscout_project'),
        'WGS_GtdbTkSummary': (GtdbTk, 'gtdbtk_project'),
        'WGS_MlstSummary': (Mlst, 'mlst_project'),
        'WGS_Checkm2Summary': (Checkm2, 'checkm2_project'),
        'WGS_AssemblySummary': (AssemblyScan, 'assembly_project'),
        'WGS_GambitSummary': (Gambit, 'gambit_project'),
        'WGS_AmrfinderSummary': (Amrfinderplus, 'amrfinder_project'),
    }

    model, rel_field = related_model_map[field_name]
    has_records = model.objects.filter(**{rel_field: project}).exists()
    setattr(project, field_name, has_records)
    project.save(update_fields=[field_name])


def sync_classification_from_sampleinfo(sampleinfo):
    """
    Keep WGS sample-info programme flags in the final isolate classification row.
    SampleInformation is the WGS-facing source of truth for programme flags.
    """
    accession = (sampleinfo.sample_accession or "").strip()
    if not accession:
        return

    FinalData = apps.get_model("home_final", "Final_Data")
    ClassificationTable = apps.get_model("home_final", "Classification_Table")

    isolate = FinalData.objects.filter(f_AccessionNo=accession).first()
    if not isolate:
        return

    classification, _ = ClassificationTable.objects.get_or_create(
        Class_idNumReferred=isolate,
        defaults={"Class_AccessionNo": accession},
    )

    classification.Class_AccessionNo = accession
    classification.Class_Chk_Emerging = bool(sampleinfo.emerging)
    classification.Class_Chk_Structured = bool(sampleinfo.structured)
    classification.Class_Chk_Satscan = bool(sampleinfo.satscan)
    classification.Class_Chk_Serotyping = bool(sampleinfo.serotyping)
    classification.Class_Chk_GHRU_all = bool(sampleinfo.ghru_all or sampleinfo.ghru)
    classification.Class_Chk_GHRU_Neo = bool(sampleinfo.ghru_neo)
    classification.Class_Chk_EGASP = bool(sampleinfo.egasp)
    classification.Class_Chk_Tricycle = bool(sampleinfo.tricycle)
    classification.Class_Chk_Pulsenet = bool(sampleinfo.pulsenet)
    classification.Class_Chk_Tulip = bool(sampleinfo.tulip)
    classification.save(update_fields=[
        "Class_AccessionNo",
        "Class_Chk_Emerging",
        "Class_Chk_Structured",
        "Class_Chk_Satscan",
        "Class_Chk_Serotyping",
        "Class_Chk_GHRU_all",
        "Class_Chk_GHRU_Neo",
        "Class_Chk_EGASP",
        "Class_Chk_Tricycle",
        "Class_Chk_Pulsenet",
        "Class_Chk_Tulip",
    ])

# === Sample Information ===
@receiver([post_save, post_delete], sender=SampleInformation)
def sync_sampleinfo_flag(sender, instance, **kwargs):
    if instance.sample_project:
        update_summary_flag(instance.sample_project, 'WGS_SampleInfoSummary')
    if kwargs.get("signal") == post_save:
        sync_classification_from_sampleinfo(instance)

# === BactScout ===
@receiver([post_save, post_delete], sender=BactScout)
def sync_bactscout_flag(sender, instance, **kwargs):
    if instance.bactscout_project:
        update_summary_flag(instance.bactscout_project, 'WGS_BactScoutSummary')

# === GTDB-Tk ===
@receiver([post_save, post_delete], sender=GtdbTk)
def sync_gtdbtk_flag(sender, instance, **kwargs):
    if instance.gtdbtk_project:
        update_summary_flag(instance.gtdbtk_project, 'WGS_GtdbTkSummary')

# === MLST ===
@receiver([post_save, post_delete], sender=Mlst)
def sync_mlst_flag(sender, instance, **kwargs):
    if instance.mlst_project:
        update_summary_flag(instance.mlst_project, 'WGS_MlstSummary')

# === CheckM2 ===
@receiver([post_save, post_delete], sender=Checkm2)
def sync_checkm2_flag(sender, instance, **kwargs):
    if instance.checkm2_project:
        update_summary_flag(instance.checkm2_project, 'WGS_Checkm2Summary')

# === Assembly ===
@receiver([post_save, post_delete], sender=AssemblyScan)
def sync_assembly_flag(sender, instance, **kwargs):
    if instance.assembly_project:
        update_summary_flag(instance.assembly_project, 'WGS_AssemblySummary')

# === Gambit ===
@receiver([post_save, post_delete], sender=Gambit)
def sync_gambit_flag(sender, instance, **kwargs):
    if instance.gambit_project:
        update_summary_flag(instance.gambit_project, 'WGS_GambitSummary')

# === AMRFinder ===
@receiver([post_save, post_delete], sender=Amrfinderplus)
def sync_amrfinder_flag(sender, instance, **kwargs):
    if instance.amrfinder_project:
        update_summary_flag(instance.amrfinder_project, 'WGS_AmrfinderSummary')


# Final accessions are the only laboratory reference for WGS matching.
# Existing WGS results remain independent of the Final verification workflow.
@receiver(post_delete, sender=Final_Data)
def mark_orphaned_custom_wgs_records_unmatched(sender, instance, **kwargs):
    CustomWGSPipelineRecord.objects.filter(
        matched_final_data__isnull=True,
        match_status="matched",
        accession__iexact=instance.f_AccessionNo,
    ).update(match_status="unmatched", match_source="")


@receiver(post_save, sender=Final_Data)
def reconcile_wgs_after_final_reimport(sender, instance, created, **kwargs):
    if not created:
        return
    # Bulk imports (bulk_create) bypass post_save; run reconcile_wgs_matches
    # from the management command after those imports.
    from apps.home_final.signals import signals_suspended_for_import
    if signals_suspended_for_import():
        return
    from .reconciliation import reconcile_wgs_matches
    reconcile_wgs_matches(accession=instance.f_AccessionNo)
