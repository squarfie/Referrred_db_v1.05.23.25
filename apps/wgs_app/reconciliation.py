"""Conservative WGS accession reconciliation.

WGS results remain independent from Final Data.  A Final Data deletion may
remove WGS_Project (CASCADE), while result rows retain their accession and
their project FK becomes NULL (SET_NULL). Reconciliation restores only
unambiguous matches; it never deletes or overwrites sequencing results.
"""
from collections import defaultdict

from django.db import transaction

from apps.home_final.models import Final_Data
from .models import (
    WGS_Project, CustomWGSPipelineRecord, SampleInformation, BactScout,
    GtdbTk, Gambit, Mlst, Checkm2, AssemblyScan, Amrfinderplus,
)

# (result model, project foreign key, stored accession, project accession,
#  project summary field). Do not use sample/run names as accession keys.
BUILTIN_LINKS = (
    (SampleInformation, "sample_project", "sample_accession", "WGS_SampleInfo_Acc", "WGS_SampleInfoSummary"),
    (BactScout, "bactscout_project", "BactScout_Accession", "WGS_BactScout_Acc", "WGS_BactScoutSummary"),
    (GtdbTk, "gtdbtk_project", "GtdbTk_Accession", "WGS_GtdbTk_Acc", "WGS_GtdbTkSummary"),
    (Gambit, "gambit_project", "Gambit_Accession", "WGS_Gambit_Acc", "WGS_GambitSummary"),
    (Mlst, "mlst_project", "Mlst_Accession", "WGS_Mlst_Acc", "WGS_MlstSummary"),
    (Checkm2, "checkm2_project", "Checkm2_Accession", "WGS_Checkm2_Acc", "WGS_Checkm2Summary"),
    (AssemblyScan, "assembly_project", "Assembly_Accession", "WGS_Assembly_Acc", "WGS_AssemblySummary"),
    (Amrfinderplus, "amrfinder_project", "Amrfinder_Accession", "WGS_Amrfinder_Acc", "WGS_AmrfinderSummary"),
)


def accession_keys(value):
    """Normalize case, separators and known WGS file extensions conservatively."""
    value = str(value or "").strip()
    if not value:
        return set()
    filename = value.replace("\\", "/").rsplit("/", 1)[-1]
    names = {value, filename}
    stem = filename
    for extension in (".fasta", ".fastq", ".fna", ".fa", ".fq", ".gz"):
        if stem.lower().endswith(extension):
            stem = stem[:-len(extension)]
            names.add(stem)
    return {name.strip().upper().replace("-", "_") for name in names if name.strip()}


def _final_index():
    index = defaultdict(list)
    for pk, accession in Final_Data.objects.values_list("pk", "f_AccessionNo"):
        for key in accession_keys(accession):
            index[key].append(pk)
    return index


def _unique_final(value, index):
    matches = set()
    for key in accession_keys(value):
        matches.update(index.get(key, ()))
    return next(iter(matches)) if len(matches) == 1 else None


@transaction.atomic
def reconcile_wgs_matches(*, accession=None, include_builtin=True, dry_run=False):
    """Reassociate orphaned WGS rows with uniquely matched Final accessions.

    Designed for explicit command execution after bulk imports and for
    post-save of individual Final_Data rows. Never relink rows that already
    have a project, since a project may represent a distinct sequencing run.
    """
    index = _final_index()
    final_accessions = dict(Final_Data.objects.values_list("pk", "f_AccessionNo"))
    target_keys = accession_keys(accession) if accession else None

    def eligible(value):
        return bool(accession_keys(value) & target_keys) if target_keys else True

    counts = {"custom_matched": 0, "custom_unmatched": 0,
              "builtin_relinked": 0, "ambiguous_or_unmatched": 0}
    custom = CustomWGSPipelineRecord.objects.filter(matched_final_data__isnull=True)
    for record in custom.iterator():
        if not eligible(record.accession):
            continue
        final_pk = _unique_final(record.accession, index)
        if final_pk:
            counts["custom_matched"] += 1
            if not dry_run:
                CustomWGSPipelineRecord.objects.filter(pk=record.pk).update(
                    matched_final_data_id=final_pk,
                    match_status="matched",
                    match_source="final",
                )
        elif record.match_status == "matched":
            counts["custom_unmatched"] += 1
            if not dry_run:
                CustomWGSPipelineRecord.objects.filter(pk=record.pk).update(
                    match_status="unmatched", match_source="",
                )

    if include_builtin:
        # Do not merge distinct existing WGS_Project rows or sample runs.
        # Multiple orphan rows per accession require manual run identification.
        for model, project_fk, accession_field, project_field, summary_field in BUILTIN_LINKS:
            orphans = model.objects.filter(**{project_fk + "__isnull": True})
            by_accession = defaultdict(list)
            for pk, value in orphans.values_list("pk", accession_field).iterator():
                if value and eligible(value):
                    by_accession[value].append(pk)
            for value, ids in by_accession.items():
                # One accession can have multiple independent sequencing runs.
                # Without a retained run identifier, combining orphan rows would
                # silently destroy project/run separation; leave them unmatched.
                if len(ids) != 1:
                    counts["ambiguous_or_unmatched"] += len(ids)
                    continue
                final_pk = _unique_final(value, index)
                if not final_pk:
                    counts["ambiguous_or_unmatched"] += len(ids)
                    continue
                # A matching project from the same pipeline is safe to reuse
                # only when exactly one such project exists.
                projects = list(WGS_Project.objects.filter(
                    Ref_Accession_id=final_accessions[final_pk],
                    **{project_field: value},
                ).values_list("pk", flat=True)[:2])
                if len(projects) > 1:
                    counts["ambiguous_or_unmatched"] += len(ids)
                    continue
                counts["builtin_relinked"] += len(ids)
                if not dry_run:
                    if projects:
                        project_pk = projects[0]
                        WGS_Project.objects.filter(pk=project_pk).update(**{summary_field: True})
                    else:
                        project = WGS_Project.objects.create(
                            Ref_Accession_id=final_accessions[final_pk],
                            **{project_field: value, summary_field: True},
                        )
                        project_pk = project.pk
                    model.objects.filter(pk__in=ids, **{
                        project_fk + "__isnull": True
                    }).update(**{project_fk + "_id": project_pk})
    return counts
