# WGS accession reconciliation

WGS matching uses **Final Data accessions only**. Raw Data is not a WGS
matching target.

## Deletion behavior

- Deleting Final Data **preserves** its `WGS_Project` connectors
  (`SET_NULL`). The Final foreign key is cleared while
  `original_accession` remains available for future matching.
- Built-in WGS result rows remain attached to their preserved
  `WGS_Project`; if a connector is deleted separately, the results
  themselves remain stored with a `SET_NULL` reference.
- Custom pipeline records remain stored after their Final FK is cleared.
  Their match status becomes `unmatched`.
- Deleting a WGS project does not delete the linked `SET_NULL` sequencing
  result rows.
- These rules do **not** change permissions for deleting Raw/Final
  laboratory records via their regular pages or WGS pages.

## Reconciliation

For an individually created Final Data row, a post-save handler attempts to
restore links to existing orphaned WGS projects (without replacing their
identities or sequencing-run associations), then to orphaned result rows.
Migration `0024_wgs_project_original_accession` safely backfills the
new accession field for projects that still have a Final reference when
it runs. Already-orphaned projects with no preserved accession cannot be
reliably backfilled automatically. Django `bulk_create` bypasses post-save; after bulk
Final imports, run:

```powershell
python manage.py reconcile_wgs_matches --dry-run
python manage.py reconcile_wgs_matches
```

To limit work to one accession:

```powershell
python manage.py reconcile_wgs_matches --accession 26ARS_ZMC0023 --dry-run
python manage.py reconcile_wgs_matches --accession 26ARS_ZMC0023
```

Only custom pipelines:

```powershell
python manage.py reconcile_wgs_matches --custom-only
```

These commands are safe to rerun. For automated ongoing reconciliation,
schedule the non-dry-run command in your application's existing job runner
or a server-side cron/systemd timer, after bulk Final Data imports. No
schedule is enabled by this code change.

## Matching safety

- An accession must match **exactly one Final accession** after supported
  separator/case and filename-extension normalization.
- Already-linked WGS data are not overwritten. Existing unlinked
  `WGS_Project` rows are matched using their persisted
  `original_accession`, preserving distinct sequencing runs.
- Built-in orphan records are relinked only where the accession is
  unambiguous and the project/run identity is not in conflict.
- Multiple orphan rows for one pipeline/accession are left unchanged
  rather than silently merging distinct sequencing runs. They need manual
  confirmation or re-upload with their sample/run identity.
- Missing or ambiguous Final matches are preserved; WGS results are never
  deleted by reconciliation.
