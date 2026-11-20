# Laboratory Workflow UX Implementation Plan

**Goal:** Make the next legitimate laboratory action clear without changing scientific history.

**Architecture:** Reuse LaboratoryAccession, LaboratoryIsolate, identification and biobank records. Add a presentation guidance helper, role-oriented queues, and explicit combined submissions backed by existing permissions and transactions. No new tables.

**Approved scope:** User approved the handoff and approach in chat, then instructed “ok start”. Work on feature/laboratory-workflow-ux-simplification; do not merge until local testing is confirmed.

## Assessment and decisions

The current isolate page presents six stages simultaneously. Queue/start/complete revival requires several visits, organism selection repeats approved data, and returned-isolate monitoring competes with receiving tasks. Existing transition services already protect cassette return and independent isolate progression.

Retain acknowledgment because it records physical custody. Consolidate saving ID and returning the cassette only under an explicit user-confirmed submission. Record revival outcome with actual start/completion times; do not invent scientific times. Preserve old transition routes for compatibility. Keep batch creation explicit: receipt decisions and accession ranges can be partial, and create_referred_batch must retain its conflict checks.

## State mapping

| Existing state | Primary action | Responsible role |
|---|---|---|
| id_pending | Begin ID testing / acknowledge custody | Laboratory Staff |
| id_testing | Submit ID and return cassette | Laboratory Staff |
| id_returned, revival_queued, revival_in_progress | Record revival outcome | LAB Encoder |
| revival_failed | Retry revival | LAB Encoder |
| non_viable | Read closed record | Authorized readers |
| revived | Review identification | LAB Encoder |
| id_reviewed | Record harvest and lyophilization | LAB Encoder |
| ready_for_biobank | Record storage | Authorized biobank personnel |
| biobanked | View inventory / print sticker | Authorized readers |

## Tasks

- [ ] Add role-aware guidance, task queues, counters and guarded submissions in laboratory_guidance.py, laboratory_views.py, laboratory_workflow.py and urls.py; test in test_workflow_ux.py.
- [ ] Reorganize sidebar/worklists into Receiving & Accessioning, Identification Testing, Revival & Processing, Biobank Inventory; preserve batch navigation and return history.
- [ ] Show one current-stage form on isolate detail; move completed records into collapsible read-only history; reuse approved values and keep storage separate.
- [ ] Review integrated changes; run Django checks, migration dry run, receiving suite and receiving/home suite; commit only relevant files in small commits.

## Verification and limits

Exercise role visibility and direct POST denials, every next-action state, independent processing, actual revival dates and failed reasons, code/name consistency, batch conflicts, partial records, labels and navigation. Preserve existing local settings, workbook and media changes. No spreadsheet automation, automatic physical events, historical advancement, production-data mutation, push or merge is included.
