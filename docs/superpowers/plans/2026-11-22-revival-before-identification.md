# Revival before identification implementation plan

**Goal:** Implement the clarified sequence: actual revival → either Bank directly using the referring site's ID, or Queue for endorsement → Endorse to Main Lab → ID testing → return → confirmation and lyophilization → final banking. Final banking confirms ID and the storage map.

**Architecture:** Add an awaiting-revival status and an identification-source field. Create isolates when recording revival, without inventing actual dates. Queue only successfully revived isolates for Main Lab or snapshot the Raw record's site ID for direct banking. Preserve existing scientific records, including records already following the former ID-first sequence.

**Tech stack:** Django, PostgreSQL, Django templates.

## Tasks

- [x] Add failing workflow regressions for preparing revival, both Bank/Queue branches, handoff gates, failed siblings, actual date validation, returned-ID confirmation, source snapshots, harvest placement and historical record preservation.
- [x] Correct `apps/laboratory/workflow.py`, guidance and view stage gates. Keep Main Lab queue timestamps separate from actual revival dates and reject invalid chronological dates.
- [x] Update receiving worksheet, worklists and isolate/batch screens to expose Bank/Queue after revival and confirmation after cassette return.
- [x] Update obsolete ID-first test expectations, run laboratory/receiving regression suites, Django system checks and migration checks.

## Verification

- 130 affected laboratory, receiving-workspace, QR scan and navigation tests passed. Final run used a faster test-only password hasher; application settings were unchanged.
- Django system checks passed; migration dry-run found no missing model migrations; local database migrations through 0011 are current.
- Independent review found and verified fixes for historical returned-ID revival continuation and complete box/position pairing at final banking.
- Historical results and audit dates remain unchanged; missing revival work is recorded explicitly, never backfilled with invented dates.
- Queueing and endorsement are distinct actions with separate actors/timestamps. Queued isolates remain in Processing until endorsement; they cannot be received, manually tested or imported as ID results before endorsement. Individual and batch queue actions support both laboratory roles.

## Review focus

- Queue and endorsement must be idempotent and lock the accession range.
- A failed/non-viable sibling must not be endorsed or prevent a revived sibling from being endorsed.
- Earlier ID results, revival dates and audit actors must not be rewritten.
- New revival dates must not precede laboratory receipt, exceed the current time, or precede a retry.
- The direct-bank source is Raw data for the exact accession and referring site; do not infer an organism or create a Main Lab result.
- Main Lab banking must retain the harvest/lyophilization gate; direct site banking does not require those steps, but sticker printing still requires harvest date.
- Harvest date is entered with lyophilization, never during revival. If site-ID banking also includes physical lyophilization, staff can explicitly record it without creating a Main Lab result.
- Existing pending/testing/returned ID records must remain actionable without manufacturing historical dates.
