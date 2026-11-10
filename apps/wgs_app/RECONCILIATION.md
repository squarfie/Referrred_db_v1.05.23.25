# WGS accession reconciliation

WGS matching uses **Final Data accessions only**. Raw Data is not a WGS
matching target.

## Deletion behavior

- Deleting Final Data still deletes its `WGS_Project` connectors
  (`CASCADE`).
- Built-in WGS result rows with a `SET_NULL` project reference remain
  stored, including their own accession fields.
- Custom pipeline records remain stored after their Final FK is cleared.
  Their match status becomes `unmatched`.
- Deleting a WGS project does not delete the linked `SET_NULL` sequencing
  result rows.
- These rules do **not** change permissions for deleting Raw/Final
  laboratory records via their regular pages or WGS pages.

## Reconciliation

For an individually created Final Data row, a post-save handler attempts to
restore orphan matches. Django `bulk_create` bypasses post-save; after bulk
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
- Already-linked WGS data are not overwritten.
- Built-in orphan records are relinked only where the accession is
  unambiguous and the project/run identity is not in conflict.
- Multiple orphan rows for one pipeline/accession are left unchanged
  rather than silently merging distinct sequencing runs. They need manual
  confirmation or re-upload with their sample/run identity.
- Missing or ambiguous Final matches are preserved; WGS results are never
  deleted by reconciliation.
