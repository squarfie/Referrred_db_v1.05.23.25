# Simplified Bench A and Main Lab workflow

Date: 2026-11-23

Status: Reconstructed from the decisions in **Add batch and site accordion views**, ending with the instruction to continue the following day. This document makes those decisions concrete for implementation; product code has not been changed in this continuation.

## Intent and agreed scope

Make ownership and the next action obvious throughout Receiving, Bench A, Main Lab and Biobank Inventory. Remove the separate Main Lab queueing, result-return and ID-review steps from the normal user journey. Preserve physical custody history independently from availability of an identification result.

The agreed outcome is:

1. Receiving records courier intake and endorses to Bench A.
2. Bench A acknowledges receipt, assigns accessions, records actual revival and records Site ID.
3. Bench A either endorses revived isolates for Main Lab ID testing or banks them temporarily using Site ID.
4. Main Lab acknowledges endorsed isolates. Alternatively, Main Lab retrieves a temporarily banked isolate directly into testing, recording the retriever and time without another acknowledgment.
5. Saving a Main Lab ID result completes testing and makes the isolate available in Bench A's **ID complete / ready for lyophilization** tab.
6. Bench A selects **Proceed to lyophilization**, records actual harvest and lyophilization details, then confirms Final ID and storage.
7. **Finalize banking** completes the laboratory workflow. Creating a DMU data batch remains an independent, optional task.

Temporary Site ID storage never completes the laboratory workflow. Failed revival and non-viable outcomes retain their existing handling.

## Approaches and selected direction

The prior conversation selected simplification of the existing application and preservation of existing records and internal role identifiers.

- **Selected: evolve the existing Django workflow with explicit result completion and retrieval evidence.** Keep existing model ownership, tables, URL names where practical, accession/site associations and audit dates. Add forward migrations only for new facts that existing fields cannot truthfully represent. This avoids inventing physical-return history and keeps historical records actionable.
- **Retaining all existing steps with different labels** would leave queueing, return and review gates in place, contrary to the agreed simplification.
- **Replacing the laboratory schema or renaming persisted roles** would increase migration risk without improving the requested user flow.

The implementation stays in the current Django/PostgreSQL/template architecture. No replacement frontend or new runtime dependency is required.

## Current implementation and required changes

`apps/laboratory` owns workflow services, views and templates. Persisted classes and migrations remain owned by `apps.receiving`; `apps/laboratory/models.py` exposes aliases of those same classes.

- `workflow.py` currently separates `queue_for_id` from `create_id_handoff`. Direct endorsement must replace that two-step UI while retaining old queue evidence.
- `views.py` currently saves a result without leaving `id_testing`, or saves and sets `returned_at`. Result completion must become an independent transition used consistently by manual entry and imports.
- `laboratory_review_id`, lyophilization, banking and receipt-completion predicates currently rely on return/review evidence. New records must use truthful ID-completion and Bench A continuation evidence instead.
- `record_site_identification` currently forbids corrections after endorsement. A separate audited correction path must support later corrections without changing the Main Lab result.
- Existing Main Lab and inventory accordions, tab badges and separate workspace permissions provide the base to preserve.

## Roles and authorization

| Displayed role | Workspace menus | Scientific edits |
| --- | --- | --- |
| LAB Encoder — Bench A | Bench A and Biobank Inventory | Receipt, accessioning, revival, Site ID, endorsement, lyophilization, Final ID and final storage |
| Main Lab Staff | Main Lab and Biobank Inventory | Acknowledgment, retrieval from temporary storage, Main Lab ID results and their corrections |
| Admin | Both workspaces and Biobank Inventory | Both teams' authorized actions |

Combined-role users see both workspaces. Keep `ROLE_LAB_ENCODER`, `ROLE_LAB_STAFF`, stored role values, memberships and unrelated permissions compatible. Change display labels through an explicit display mapping rather than a database role rename.

Enforce the same role policy at POST endpoints and workflow-service boundaries. Shared inventory access does not grant Main Lab permission to change Site ID or Final ID. Keep existing authorized inventory/storage correction access; do not expand unrelated roles.

## State transitions and evidence

| Trigger | Resulting work state | Evidence written | Next destination |
| --- | --- | --- | --- |
| Bench A endorses successfully revived isolates with valid Site ID | Awaiting Main Lab acknowledgment | Actual endorsement actor/time | Main Lab: Awaiting acknowledgment |
| Main Lab acknowledges endorsement | Testing in progress | Actual acknowledgment actor/time | Main Lab testing form |
| Bench A saves temporary Site ID storage | Temporary storage | Site ID, original storage snapshot and banking actor/time | Biobank Inventory: Temporary storage |
| Main Lab retrieves from temporary storage | Testing in progress | Explicit retrieval actor/time and storage snapshot | Main Lab testing form |
| Main Lab saves a valid ID result | ID complete | Result, result author/time and independent completion evidence | Main Lab: Completed; Bench A: ID complete |
| Bench A proceeds to lyophilization | Lyophilization work | Bench A continuation actor/time | Harvest/lyophilization form |
| Bench A saves actual harvest and lyophilization details | Ready for final storage | Actual dates and recording actor/time | Final ID and storage form |
| Bench A finalizes banking | Final banking complete | Final ID, matching code, paired storage location and banking actor/time | Completed inventory |

Implementation choices:

- Preserve existing historical enum values; add an explicit ID-complete state and evidence rather than reinterpreting `returned_at` as completion. A Main Lab result save never writes physical-return or Bench A-review timestamps.
- Preserve any already-recorded physical-return facts as history. The simplified flow does not require or infer physical return; if a physical-return recording control remains for compatibility, it must explicitly describe that physical action and be independent of result completion.
- Retain whole-batch endorsement as the existing custody action, with the site/batch accordion showing readiness and blockers. Eliminate its preliminary queue click: revived candidates can be endorsed directly; existing queued candidates remain eligible. Non-viable siblings do not enter testing. Previously handed-off siblings are not re-endorsed. Existing unfinished revival/Site ID work must remain visible as a blocker.
- Retrieval is an isolate action from temporary inventory. It must be discoverable to Main Lab Staff even before that isolate has endorsement evidence. Retrieval requires a saved temporary storage record and successfully recorded revival. It does not create a fictitious endorsement or acknowledgment.
- Do not erase provisional Site ID or temporary-storage history during endorsement/retrieval. Mark the record as in testing so it cannot appear available for a second retrieval.
- Lock the accession and isolate consistently for transitions. Duplicate requests do not create duplicate result-completion, retrieval or endorsement events. Invalid or unauthorized requests leave records unchanged.
- Use the established organism resolver to validate names against codes. Manual saves and uploaded results follow the same completion rules. A failed multi-row import remains atomic.
- Record Final ID at final banking. The banking form may propose the latest completed Main Lab result; Bench A explicitly confirms it. Lyophilization validates its selected organism independently without silently treating that proposal as a final approval.

## Identification corrections and audit

Bench A and Admin may correct Site ID and Final ID, including Final ID after banking. Main Lab Staff and Admin may correct Main Lab results. Preserve the original Main Lab result and retest history.

For every correction, require a nonblank reason and save the isolate, identification kind, previous code/name, new code/name, editor and timestamp in structured history. Use append-only correction records in normal application paths. A correction must not rewrite custody dates, revival dates, completion dates or the stored original result.

Implementation choices:

- First-time Site ID entry remains a normal encoding action; changing an existing Site ID uses the correction path and requires a reason.
- A Main Lab result correction appends a linked revision, leaving the original result readable. It does not reopen completed laboratory work or overwrite an already-confirmed Final ID; Bench A can then make a separate Final ID correction if needed.
- A Site ID correction changes the referring-site identification only. For a temporarily banked isolate whose active identification is explicitly Site ID, update the derived provisional label fields and banking code consistently in the same transaction. Preserve the original Site ID/storage snapshot in history. Never replace a Main Lab-derived Final ID with Site ID.
- A Final ID correction updates the approved code/name and dependent sticker organism code atomically. It leaves the final-banked state and original banking date intact. Future single, batch and date-range sticker prints use the corrected identification.
- Existing Raw Data and Final Data tables remain independently managed. A laboratory correction does not silently rewrite those tables or generate a data batch.

## Pages, navigation and feedback

Every work page shows the current stage, responsible team and next action. Distinguish temporary storage, testing, ID complete, lyophilization and final banking in wording and filters.

- Bench A gets an **ID complete / ready for lyophilization** tab with a count badge and site/batch accordions.
- Main Lab gets Awaiting acknowledgment, Testing in progress, Completed and retrieval access to Temporary storage. Completed counts use completion evidence, not cassette-return timestamps.
- Inventory remains shared and grouped per site and accession batch. Preserve sticker selection and filters.
- Remove ordinary queue, return-results and ID-review screens from the normal flow. Existing bookmarked URLs should redirect to the appropriate page or provide a compatible, authorized transition without imposing the removed gates.
- Each successful Save states the recorded action and destination. Errors retain submitted values where practical and state the blocking requirement.
- Preserve tab, search and pagination context through links and actions using validated same-site destinations or explicit known query parameters. Do not accept arbitrary redirect targets.
- Show completed workflow facts as protected history, alongside separate authorized correction actions and their audit entries.
- Keep optional data-batch creation visible as a separate task; its absence must not keep an otherwise complete laboratory record open.

## Migration and historical compatibility

Use additive migrations under `apps/receiving/migrations/`. Do not move tables, relabel apps, rename role memberships or fabricate dates for existing records.

Old returned/reviewed/banked records remain readable and actionable according to existing evidence. A historical returned result can be presented as available without writing a new completion timestamp. Existing completed final banking stays completed. Existing testing records with saved draft results remain in testing until an explicit completion action; the migration must not guess whether a saved result was final.

Existing queued records can use direct endorsement. Historical missing-revival continuation remains available, with actual work recorded explicitly. Keep receipt completion consistent across dashboards, worklists and inventory: final Main Lab banking and terminal non-viable outcomes count toward laboratory completion; temporary banking does not.

Do not apply schema migrations to a live/non-test database as part of writing or reviewing this design.

## Validation and completion criteria

Write and run focused regression tests before behavior changes. Cover:

1. Bench A/Main Lab/Admin/combined-role menus, direct endpoint access and service permissions.
2. Direct endorsement without queueing, mixed batches, repeated requests and unresolved revival blockers.
3. Temporary storage retrieval, storage-history retention and denial of duplicate/unauthorized retrieval.
4. Manual and uploaded ID completion, failed-import rollback, count badges and Bench A visibility, with no invented return/review facts.
5. Proceed-to-lyophilization, actual-date validation, Final ID/storage confirmation and completion independent of data-batch creation.
6. Site ID, Main Lab result and Final ID corrections, required reasons, original-value retention, role denials and corrected sticker output.
7. Old queued, testing, returned, reviewed and banked cases, including historical missing revival.
8. Site/batch grouping, sticker selection, search/tab preservation and safe return navigation.

Run Django checks, migration consistency checks, focused laboratory/receiving/home permission regressions and then the broader relevant suite. Record failures separately when demonstrably pre-existing. Inspect representative rendered pages for both role-specific workspaces and inventory.

Implementation is complete only when those checks pass or any remaining limitation is explicitly documented. Local implementation and testing do not establish production deployment or laboratory SOP approval.
