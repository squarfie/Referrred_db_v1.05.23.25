# Laboratory Revival, ID, Lyophilization, Label, and Biobank Workflow Design

## Intent

Correct the Receiving and Laboratory workflow so Secretariat Receiving ends at endorsement, while Laboratory Receiving owns custody confirmation, acceptance, accessioning, revival, returned-ID review, lyophilization, harvest recording, isolate-label printing, and the handoff to biobanking. Main Laboratory staff own ID testing and may also perform biobanking. Both LAB Encoder and Laboratory Staff can open the Laboratory Receiving worksheet, but mutations remain role- and stage-controlled.

The completed operational sequence is:

`Secretariat receipt → endorsement to Laboratory Receiving → laboratory custody confirmation → acceptance/rejection → accessioning per batch → queued for revival → revival in progress → revival completed → endorsement to Main Laboratory → ID testing → cassette/results returned to Laboratory Receiving → identification review → lyophilization and harvest recording → optional sticker printing → biobanking`

Sticker printing is an available output, not a stored workflow stage, status, event, or prerequisite for biobanking.

## Goals

- Keep Secretariat Receiving free of Laboratory Receiving worksheet actions and messages.
- Let Laboratory Receiving personnel confirm custody from either the laboratory dashboard or its laboratory-specific receipt page.
- Return laboratory users to the Laboratory Receiving dashboard, never the Secretariat queue.
- Make the worksheet available to LAB Encoder and Laboratory Staff only after the courier batch is `Received by Laboratory`.
- Preserve the existing acceptance, accession range, organism inventory, and shared batch-generation behavior.
- Create individual accessioned isolate records before revival starts, grouped by the saved laboratory accession batch/range.
- Add explicit, validated revival, Main Laboratory handoff, ID return, lyophilization, and biobank states.
- Print a batch sheet containing one label per isolate. Each label and QR payload use the exact accession number, for example `26ARS_APM0001`.
- Show only the approved WHONET organism code (e.g., KPN), exact accession QR, printed accession, and harvest date (MM/DD/YYYY) on each compact isolate label.
- Permit both LAB Encoder and Laboratory Staff to access the biobanking function after the required laboratory work is complete.
- Preserve an auditable actor and timestamp for each custody/workflow transition.

## Non-goals

- Treating sticker printing as a workflow transition or recording whether a browser print dialog was completed.
- Allowing Secretariat users to confirm laboratory receipt, open the worksheet, perform revival, or see worksheet calls to action.
- Replacing `ReferralReceipt`, `LaboratoryReceiving`, `LaboratoryAccession`, `Batch_Table`, or the shared batch-creation service.
- Changing the established accession format.
- Automatically inferring an organism, harvest date, lyophilization date, or biobank location.
- Allowing workflow stages to be skipped because a user knows the target URL.
- Combining the courier-batch QR label with the later isolate sticker. They remain different artifacts and identifiers.

## Existing State and Constraints

- `ReferralReceipt` already owns courier custody through `received`, `endorsed`, and `lab_received`.
- The shared receipt-detail page currently mixes Secretariat controls with Laboratory worksheet links and always sends its Back button to the generic Receiving route.
- The Laboratory Receiving dashboard already has a `Confirm receipt` action for endorsed records and an `Update / Create Batch` action for laboratory-received records.
- `LaboratoryReceiving` owns the acceptance decision and has one or more `LaboratoryAccession` ranges.
- `LaboratoryAccession.accession_numbers` already produces the required exact accession format, including the four-digit sequence.
- `LaboratoryIsolate` is currently created only when a range is endorsed directly to ID testing. Its current statuses skip revival.
- Main Laboratory ID entry already preserves multiple instrument results and cassette/slot references.
- `LaboratoryBiobank` already stores harvest, lyophilization, supplementary-storage, and location fields, but saving is currently allowed too early.
- The current sticker contains only accession and final organism and has no QR code or batch-print view.
- Current role boundaries are LAB Encoder for receiving/review and Laboratory Staff for ID/biobank. Both roles already have some laboratory worklist visibility.

## Approaches Considered

### 1. Extend the existing isolate workflow and expose batch transitions in the current worksheet — selected

Expand `LaboratoryIsolate` with explicit statuses and transition audit fields. Create isolate rows from a saved accession range when it is queued for revival. Use atomic batch actions for revival and Main Laboratory endorsement, while retaining per-isolate ID, return review, lyophilization, label content, and biobank data.

This builds on the current models, URLs, permissions, and screens; preserves existing accession and batch links; and minimizes migration and compatibility risk.

### 2. Add a parallel laboratory-batch workflow model

Store all stage state on a new batch-level object and mirror selected state to individual isolates. This models group operations directly but creates two sources of truth and requires reconciliation when one isolate needs correction or retesting. Rejected.

### 3. Rebuild the post-receipt process as a separate module

Create a new workflow application and migrate existing ID and biobank records into it. This offers strong separation but duplicates working screens and services and introduces unnecessary migration risk. Rejected.

## Navigation and Queue Separation

### Secretariat Receiving

- The Secretariat dashboard and receipt detail retain courier intake, label printing, endorsement, and custody history only.
- Remove the `Open laboratory worksheet` success-notice link and the worksheet action from Secretariat-facing receipt details.
- Secretariat users cannot access the laboratory confirmation form or worksheet through crafted URLs.
- The Secretariat Back action always returns to `secretariat_receiving_dashboard`.

### Laboratory Receiving

- Add a laboratory-specific receipt-detail route/view using the shared receipt data but laboratory actions and navigation.
- The Laboratory dashboard `Open` action uses this laboratory-specific detail route.
- An endorsed receipt exposes `Receive by Laboratory` from both the dashboard row and the laboratory detail page. Both use the existing validated custody-confirmation form/service.
- All Back and Cancel controls in this path return to `laboratory_receiving_dashboard` or the laboratory-specific detail page.
- After successful custody confirmation, redirect to the Laboratory Receiving worksheet.
- A laboratory-received record exposes `Open worksheet`; an endorsed or Secretariat-only record does not.

## Worksheet Access and Role Boundaries

- LAB Encoder, Laboratory Staff, and Admin may GET the Laboratory Receiving worksheet after `ReferralReceipt.status == lab_received`.
- LAB Encoder owns acceptance/rejection, accession ranges, organism inventory, batch generation, revival transitions, returned cassette/result acceptance, final identification review, lyophilization, and harvest recording.
- Laboratory Staff owns Main Laboratory acknowledgment, ID result entry/import, and return of the cassette/results.
- LAB Encoder and Laboratory Staff may open and save biobank information only after the isolate meets the biobank gate.
- Admin may exercise both operational roles for support and testing, with its actions audited normally.
- Every mutation remains login-required, role-checked, POST-only, CSRF-protected, and validated against current database state inside a transaction.

## Isolate Creation and Batch Grouping

- Saving an accession range continues to generate its deterministic list of accession numbers but does not create ID records immediately.
- The worksheet exposes `Queue batch for revival` for a saved, unqueued accession range after the acceptance totals permit that range.
- This action atomically creates one `LaboratoryIsolate` per accession number with status `revival_queued`. Existing accession-number conflicts abort the entire action.
- The range becomes locked against edits/deletion as soon as isolate rows exist.
- Revival queue/start/complete and endorsement to Main Laboratory are batch actions scoped to one `LaboratoryAccession`; all isolates must be in the expected source status, and the transition updates all of them atomically.
- ID results, returned cassette review, final organism, lyophilization/harvest, sticker contents, and biobank records remain isolate-specific.
- Batch actions never silently skip an ineligible isolate. A mixed-status range shows a clear error and requires correction/reconciliation.

## Isolate State Machine

Replace the current coarse statuses with the following forward-only operational states:

1. `revival_queued` — accession exists and the isolate awaits revival.
2. `revival_in_progress` — Laboratory Receiving has started revival.
3. `revived` — revival is complete and the isolate may be endorsed to Main Laboratory.
4. `id_pending` — endorsed to Main Laboratory and awaiting its acknowledgment.
5. `id_testing` — Main Laboratory acknowledged the cassette/isolate and is recording identification.
6. `id_returned` — Main Laboratory returned the cassette and at least one identification result to Laboratory Receiving.
7. `id_reviewed` — LAB Encoder accepted the returned identification and recorded the approved organism.
8. `ready_for_biobank` — required lyophilization and harvest dates have been recorded.
9. `biobanked` — storage information was saved.

Legacy data migration maps:

- `pending` → `id_pending`
- `testing` → `id_testing`
- `returned` → `id_returned`
- `reviewed` → `id_reviewed`, or `ready_for_biobank`/`biobanked` when existing related data proves the later stage

The migration must preserve every existing timestamp, result, approved organism, and biobank row. Ambiguous legacy records remain at the earliest provable safe state rather than assuming work was completed.

## Transition Audit Data

Add actor/timestamp fields needed for the new stages:

- revival queued, started, and completed by/at;
- Main Laboratory endorsement by/at (reuse current endorsement fields after semantic migration);
- Main Laboratory acknowledgment by/at (reuse current acknowledgment fields);
- returned by/at;
- Laboratory Receiving review by/at (reuse current review fields);
- lyophilization/harvest completion by/at;
- biobanked by/at.

Detailed scientific dates remain on `LaboratoryBiobank`; transition timestamps record when the application action occurred. Sticker printing creates no audit field.

## ID Return, Lyophilization, and Biobank Gates

- Main Laboratory may return an isolate only from `id_testing` and only when at least one identification result exists.
- LAB Encoder may approve the returned result only from `id_returned` and must provide the approved organism.
- Lyophilization/harvest completion is available only from `id_reviewed` and requires both `date_harvested` and `date_lyophilized`, neither in the future and neither earlier than laboratory custody. Date ordering must be scientifically consistent (`date_harvested <= date_lyophilized`) unless existing project rules explicitly require the reverse; this is a builder validation call to confirm against current field meaning.
- Saving those required dates moves the isolate to `ready_for_biobank`. Additional lyophilization fields may be updated without bypassing the gate.
- Biobank save is available to LAB Encoder, Laboratory Staff, and Admin only from `ready_for_biobank` or `biobanked`.
- The first valid save moves the isolate to `biobanked`; later edits retain that status and audit the latest editor through existing fields.
- Direct POSTs that attempt to skip a stage are rejected without partial writes.

## Sticker Output

- Add a batch label-sheet route scoped to `LaboratoryAccession` and accessible to authorized laboratory roles.
- Render one printable sticker per isolate in accession order.
- Each sticker shows:
  - exact accession number;
  - a QR code whose decoded payload is exactly that accession number and nothing else;
  - approved organism identifier/name;
  - harvest date.
- Labels are printable once the isolate has an accession number, approved organism, and harvest date. A batch sheet clearly identifies any isolate whose label data is incomplete and does not generate a misleading partial sticker for it.
- Sticker printing is not stored, does not change isolate status, and does not gate biobanking.
- The existing courier batch label and QR remain unchanged.

## Worksheet UI

- Keep the existing Laboratory Receiving worksheet as the central batch workspace.
- Add a compact workflow summary per accession range showing isolate counts by current stage.
- Display batch actions only when every isolate in that accession range satisfies the source-state requirement.
- Keep acceptance/accession/organism/batch-generation sections and place the revival/ID workflow after accessioning and before biobank completion.
- Link from a batch summary to the individual isolate worklist/detail for result entry and returned-ID review.
- Rename action text to match the physical handoff: queue for revival, start revival, complete revival, endorse to Main Laboratory, acknowledge for ID, return cassette/results, accept returned identification, complete lyophilization/harvest, and save biobank.
- Keep form fields explicitly named and labeled in templates rather than rendering new workflow forms through a generic field loop.

## Validation and Concurrency

- Implement transitions as service functions that lock the accession range and affected isolates with `select_for_update()`.
- Validate the full source set before modifying any row.
- Make repeat submission idempotent only when the entire batch already reached the requested destination; otherwise return a conflict message.
- Preserve unique accession numbers and never regenerate or renumber an accession after revival has been queued.
- Reject future scientific dates and invalid ordering.
- Prevent deletion/editing of accession ranges with isolate workflow history.
- Do not infer successful browser printing.

## Migration and Compatibility

- Add one schema/data migration after the current receiving migrations.
- Alter `LaboratoryIsolate.status`, add new audit fields, and migrate legacy state conservatively.
- Preserve current URLs where practical with redirects to the correct role-specific page.
- Keep existing ID results and biobank records reachable.
- Do not apply the migration to production in this task; provide migration/check/test evidence only.

## Testing and Acceptance

- Navigation tests: Secretariat pages contain no worksheet action; laboratory Open/Back/Cancel routes remain in the Laboratory Receiving queue.
- Access tests: LAB Encoder and Laboratory Staff can view the worksheet; Secretariat cannot; mutations follow the role matrix.
- Receipt tests: receive action works from the dashboard and laboratory detail; worksheet is unavailable until `lab_received`; successful confirmation opens the worksheet.
- State-machine tests: every valid transition, every skipped/reversed transition denial, repeat submission, mixed-status batch rollback, and concurrent batch action.
- Migration tests: all four legacy statuses and existing biobank combinations map without data loss.
- ID tests: results required before return; LAB Encoder review required before lyophilization.
- Date tests: required harvest/lyophilization values, future-date rejection, ordering, and transaction rollback.
- Sticker tests: one sticker per eligible isolate, accession ordering, exact QR payload, approved organism, harvest date, and no status mutation.
- Biobank tests: both laboratory roles can save only after `ready_for_biobank`; first save advances status; later edits retain it.
- Regression: Receiving tests, Home/sidebar tests, Django checks, migration consistency, full project suite, and `git diff --check`.
- Manual acceptance: exercise Secretariat and Laboratory dashboards with separate users; scan sample isolate QR codes; print a multi-isolate batch sheet; verify the worksheet at desktop and narrow widths.

## Documentation and Deployment

- Update the Phase 2 living history with the approved workflow, migration, verification evidence, and remaining deployment gates.
- Record any pre-existing full-suite failures separately from regressions.
- Do not claim deployment, migration application, or production acceptance without direct evidence.

## Implementation Clarifications — November 20, 2026

These requirements supersede conflicting examples above.

- Revival may fail or produce a non-viable isolate. LAB Encoder records a mandatory reason; **Revival failed** is eligible for an audited retry, while **Non-viable** is terminal. Unreconciled failures or mixed statuses block whole-batch transitions.
- A successfully retried isolate retains its original revival-completion actor/time. Remaining in-progress isolates can subsequently complete together without overwriting that history.
- Box position is the required storage location detail to mark an isolate biobanked. It may be provided in the standard, lyophilization, or supplementary storage position field.
- The compact sticker displays the exact accession as text (e.g., `26ARS_APM0001`), a QR whose decoded content is **only that accession string**, the WHONET organism code (e.g., `KPN`) and the harvest date in `MM/DD/YYYY`. No full organism name or storage details appear.
- A batch sticker sheet prints multiple complete stickers in accession order; incomplete rows are listed separately and omitted from the printable sticker grid. Printing is never recorded as a workflow transition.
- LAB Encoder and Laboratory Staff can GET a laboratory-received worksheet, but its acceptance, accession, revival, and review mutations remain LAB Encoder/Admin-only. Main Laboratory personnel manage ID result entry/return; both lab roles may enter authorized biobank storage after lyophilization.

## Feature Branch Implementation Evidence

- Development branch: `feature/lab-revival-lyophilization-biobank-workflow`.
- Database migration: `receiving.0006_laboratory_revival_biobank_stages`. It adds new states and audit columns, and maps legacy statuses conservatively.
- State-transition implementation: `apps/receiving/laboratory_workflow.py`; role-checked routes in `laboratory_views.py`; worksheet/receipt separation in `views.py` and receiving templates.
- Printable compact label: `apps/templates/receiving/lab_biobank_label.html` (single and per-accession-batch views).
- Regression tests added/updated in `apps/receiving/tests.py` and `apps/home/tests.py` for role restrictions, invalid steps, retries, label QR payloads, date and storage validation, navigation, and migration mapping.
- **Validation not yet performed:** local `python manage.py check`, `makemigrations --check --dry-run`, `python manage.py test apps.receiving apps.home`, and manual printed/scanned QR acceptance. Do not apply this migration to production or merge until these succeed.

## Corrected Sequence — November 20, 2026 (ID First)

This update **supersedes all earlier statements** in this document that describe
revival before Main Laboratory identification:

1. Secretariat receives and endorses the isolates.
2. Laboratory Receiving confirms custody, acceptance and accession numbers.
3. Laboratory Receiving endorses accessioned isolates **to Main Laboratory for ID**.
4. Main Laboratory acknowledges the isolates, records ID results, and forwards
   the cassette/slot reference and ID results back to Laboratory Receiving.
   At least one result with a cassette reference and an authenticated return
   timestamp/actor are mandatory for each isolate.
5. Only after **every** isolate in the accession range has a recorded return
   may Laboratory Receiving queue and start revival.
6. Laboratory Receiving records successful revival, failed revival (retriable)
   or non-viable (terminal); unresolved mixed statuses block batch completion.
7. LAB Encoder reviews the instrument identification **after revival completes**.
8. Lyophilization, harvest, position-gated biobanking, and compact batch QR
   stickers follow the approved organism and WHONET code.

The visible workflow and the server-side transitions must agree.
Migration `0008_main_lab_id_before_revival` changes the default for new
isolates to `id_pending` and conservatively reclassifies **untouched**
legacy pre-ID revival queues. Active, completed or cassette-returned revival
records are preserved for human review rather than silently modified.

The separate organism lookup improvement lives in
`feature/laboratory-organism-lookup` and uses `Organism_List` for searchable
code/name pairs. Migrations `0007` and `0008` are not applied to production
until local tests and migration review pass.
