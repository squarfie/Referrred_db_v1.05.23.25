# Independent Laboratory Receiving and Shared Batch Creation Design

## Intent

Extend the existing custody workflow from laboratory acknowledgment into accession assignment and batch creation without duplicating `Batch_Table`, accession generation, Raw record synchronization, TAT creation, or duplicate-protection logic. Secretariat receiving remains unchanged. LAB Encoder staff gain a laboratory workspace that preserves receipt details, condition/discrepancy information, processing counts, organism summary, multiple accession ranges, and links to generated batches.

The completed sequence is:

`Secretariat receipt → endorsement → laboratory custody confirmation → laboratory details/ranges → shared batch creation → DMU processing`

## Goals

- Preserve `ReferralReceipt` and `ReceivingEvent` as the canonical custody timeline.
- Keep laboratory custody confirmation independent from accession/batch generation.
- Support multiple accession ranges and therefore multiple `Batch_Table` records from one courier receipt.
- Keep the existing manual Create Batch page operational for Admin, Checker, and DMU Encoder roles.
- Use one shared transactional batch-creation service for manual and laboratory entry points.
- Generate four-digit ranges, batch codes, `Referred_Data` accessions, and `TATform` records with the established conventions.
- Detect exact and partial conflicts without overwriting Raw, Final, verification, or unrelated batch data.
- Keep laboratory receiving and accession history accessible after batches enter DMU or verification workflows.
- Include the requested acceptance/rejection/processed counts and structured organism inventory in this phase.
- Generate a monthly landscape receiving-and-inventory register matching the supplied paper form.

## Non-goals

- Replacing the existing Raw, Final, verification, or TAT models.
- Requiring a laboratory receiving record for manually created batches.
- Moving custody timestamps away from `ReferralReceipt`.
- Automatically deciding whether isolates are accepted or rejected. These totals are entered and certified by the LAB Encoder.
- Creating a second batch table or a second accession-generation implementation.
- Allowing LAB Encoder through the existing DMU Create Batch route.
- Editing or replacing signed, verified, or released data during batch generation.

## Existing State and Constraints

- `ReferralReceipt` already stores laboratory received date, time, user, terminal custody status, and the canonical `LAB_RECEIVED` event.
- An incomplete, unmigrated `LaboratoryReceiving` class has been appended locally. It has no relationship to `ReferralReceipt` and can be safely replaced before its first migration.
- `batch_create_view()` currently performs validation, accession generation, exact-batch detection, accession-conflict detection, orphan-shell recovery, `Batch_Table` creation, Raw membership synchronization, and `TATform` synchronization inline.
- `Referred_Data.AccessionNo` is unique. `TATform` has a one-to-one relationship with `Batch_Table`.
- PR #23 added server-side 1–9999 range validation and four-digit reference generation to `BatchTable_form`.
- `Batch_Table.created_by` currently doubles as audit metadata and DMU Encoder ownership. Checker/Admin can manage every batch; a DMU Encoder normally manages only batches they created.
- The repair command imports private batch helpers from `apps.home.views`; those imports must move to the shared service.

## Approaches Considered

### 1. Supplemental laboratory record with canonical custody on `ReferralReceipt` — selected

`LaboratoryReceiving` has a protected one-to-one relationship to `ReferralReceipt`. It stores only laboratory details not already owned by the receipt: condition, discrepancies, remarks, requested counts, acceptance date, and organism summary. Date/time/personnel/site are exposed from the linked receipt. `LaboratoryAccession` stores each range and its optional generated batch.

This avoids conflicting custody copies, preserves existing records, supports multiple ranges cleanly, and keeps future corrections attributable to the correct domain object.

### 2. Copy custody fields into `LaboratoryReceiving`

This makes the laboratory record self-contained but creates two authoritative dates, times, personnel references, and site values. Corrections could diverge and audit reporting would require reconciliation. Rejected.

### 3. Move existing custody fields from `ReferralReceipt` into the new model

This creates a cleaner long-term schema but requires a destructive compatibility migration and changes the already-tested QR acknowledgment flow. It adds risk without providing a user-visible benefit in this phase. Rejected.

## Data Model

### `LaboratoryReceiving`

- `receipt`: `OneToOneField(ReferralReceipt, on_delete=PROTECT, related_name="laboratory_receiving")`
- `specimen_condition`: free-text `CharField` pending an approved SOP code list
- `discrepancies`: optional `TextField`
- `remarks`: optional `TextField`
- `total_isolates_received`: non-negative integer
- `isolates_rejected`: non-negative integer
- `isolates_accepted`: non-negative integer
- `date_accepted`: nullable date
- `total_isolates_processed`: non-negative integer
- `created_at`, `updated_at`

Custody properties delegate to `receipt.lab_date_received`, `receipt.lab_time_received`, `receipt.lab_received_by`, and `receipt.site`. The LAB Encoder explicitly enters the accepted and rejected totals; the application never derives or changes that decision. There is no separate finalize step. Each Save requires accepted + rejected to equal received, processed not to exceed accepted, and the acceptance date not to precede laboratory receipt or lie in the future. The custody-created blank record remains valid at zero totals until the LAB Encoder saves actual receiving information.

### `LaboratoryAccession`

- `laboratory_receiving`: protected foreign key, related name `accession_ranges`
- `start_number`, `end_number`: positive integers limited to 1–9999
- `batch`: nullable `OneToOneField(home.Batch_Table, on_delete=PROTECT, related_name="laboratory_accession")`
- `assigned_by`: nullable user with `SET_NULL`
- `assigned_at`
- `batch_created_at`: nullable timestamp

The model exposes the padded reference (`0090-0094`), accession count, accession list, and expected batch code. A database uniqueness constraint prevents duplicate identical ranges per laboratory receiving record. The service locks the parent record and rejects partial overlaps with its other ranges.

### `LaboratoryOrganismCount`

- `accession_range`: protected foreign key to `LaboratoryAccession`
- `organism_code`: normalized code from the printed register (`HA`, `NGO`, `SPN`, `ECO`, `KLE`, `SAL`, `SHI`, `VIB`, `PAE`, `ACI`, `SAU`, `ENT`, `STR`, or `OTHER`)
- `organism_group`: stored choice used to place the count under Fastidious, Enterobacterales, other Gram-negative, non-fermenting, or Gram-positive sections
- `count`: positive integer

The same organism code may appear only once per accession range. The sum of organism counts for a range cannot exceed its accession count. Repeatable rows avoid a brittle set of dozens of nullable columns and allow the printed form to retain its fixed grouped headings.

### Existing models

- Add `ACCESSION_ASSIGNED` and `BATCH_CREATED` to `ReceivingEvent.EventType`. These event types may occur multiple times and are not included in the existing canonical-event uniqueness constraint.
- Do not remove or duplicate any `ReferralReceipt` custody field.
- Backfill one `LaboratoryReceiving` row for each existing `ReferralReceipt` already in `lab_received` state.

## Shared Batch-Creation Service

Create `apps/home/services/batch_creation.py` with public interfaces:

```python
create_referred_batch(
    *,
    site_code,
    referral_date,
    start_number,
    end_number,
    created_by,
    site_name="",
    personnel=None,
) -> BatchCreationResult
```

`BatchCreationResult` contains `batch`, `accession_numbers`, `isolate_count`, and `recovered_orphan`.

Typed exceptions distinguish invalid range, exact existing batch, and accession conflicts. Conflict exceptions carry the existing batch where available so each view can render an appropriate link.

The service:

1. Normalizes and validates site/range/date inputs.
2. Builds the padded reference, accession numbers, summary, and batch code.
3. Opens one database transaction.
4. Locks exact batch candidates and conflicting Raw accessions.
5. Rejects an existing batch containing Raw/Final records or linked to a different laboratory range.
6. Recovers only an exact orphan shell with no Raw, Final, verification, or laboratory-source relationship.
7. Rejects accessions already assigned to another batch and any matching Final record.
8. Creates/updates the `Batch_Table`, synchronizes Raw membership, and creates/synchronizes `TATform`.
9. Never changes patient/specimen/AST fields, Final results, or verification state.

Move the reusable range, batch-code, accession-summary, personnel, and membership helpers from `apps.home.views` into this service. Update `batch_edit_view` and `repair_orphan_batches` to import them from the service so there is one implementation.

## Entry Point A: Existing Create Batch

- Keep the existing URL, form, template, and role decorator.
- After `BatchTable_form` validation, pass normalized values and staff/signatory fields to `create_referred_batch()`.
- Translate typed service errors into the current message/link patterns.
- Do not require or create a `LaboratoryReceiving` row.
- Preserve current ownership: `created_by=request.user`.

## Entry Point B: Laboratory Receiving

### Custody confirmation

The existing LAB Encoder confirmation remains a separate POST. On success, create or retrieve the supplemental `LaboratoryReceiving` row and redirect to its workspace. Batch failure can never roll back this custody confirmation because it is a later operation.

### Laboratory workspace

The workspace displays Secretariat/custody details read-only and provides:

- laboratory condition, discrepancy, remarks and decision-count form with one Save action;
- accession range form with live padded reference, isolate count and batch-code preview;
- saved pending/generated ranges;
- separate POST action to generate the batch for a pending range;
- links to generated Raw batch results.
- repeatable organism-code/count rows for each saved accession range;
- a month/year register preview and PDF download.

Only LAB Encoder can save laboratory details, add ranges, or generate batches. Admin retains read access but does not impersonate the LAB Encoder custody action. Existing shared Receiving read roles may view the read-only workspace.

### Range and batch operations

- Saving a range does not create a batch.
- Adding a range locks the laboratory receiving record and rejects overlap with any saved range for that record.
- Creating a batch locks the range, is idempotent when already linked, calls the shared service, then links the returned batch and appends `BATCH_CREATED`.
- A failed batch operation leaves custody and the pending range intact.
- Exact or partial conflicts show the conflicting accessions and an existing batch link where available.

## Ownership and DMU Handoff

The laboratory-generated batch keeps the LAB Encoder in `Batch_Table.created_by` for auditability. Update `can_manage_batch()` so a DMU Encoder may manage a batch linked through `LaboratoryAccession`; ordinary manually created batches retain creator-based ownership. Checker/Admin behavior is unchanged. This is a targeted handoff rule, not a general Create Batch permission expansion.

## Permissions and Security

- Every mutation is login-required, role-checked, POST-only, and CSRF-protected.
- Service functions repeat the authorization checks needed for direct/internal callers.
- Posted users, sites, receipt IDs, batch links, status, and generated identifiers are never trusted.
- Receipt/site/date values for laboratory-origin batches come from the canonical linked receipt.
- Laboratory models are read-only/no-delete in Django admin until a controlled correction policy exists.
- Protected relationships prevent deleting a receipt with laboratory history or deleting a batch referenced by an accession range.

## Migration and Compatibility

1. Replace the incomplete unmigrated local `LaboratoryReceiving` definition with the selected schema.
2. Add `LaboratoryAccession` and constraints in receiving migration 0003.
3. Backfill supplemental rows for existing `lab_received` receipts without modifying their timestamps, personnel, status, or events.
4. Run a preflight for duplicate exact batch codes and existing inconsistent receiving rows before non-test deployment.
5. Do not apply production migrations until backup, rollback, and staging checks are recorded.

## UI

Extend the existing Receiving detail experience and shared `receiving.css` visual system. Do not add unrelated navigation. The laboratory workspace uses the same microscopy background, hero, surfaces, notices, responsive grids, status badges, tables, messages, and confirmation patterns already established for Receiving.

At narrow widths, summary cards and forms stack, accession rows become readable cards, and all mutation buttons remain full-width/touch-friendly.

## Monthly Receiving and Inventory Register

Add a filterable HTML preview and a generated A4-landscape PDF for a selected month and year. The report follows the supplied form: Date Received, Referring Institution, total received/rejected/accepted, date referred/processed, batch number, number of isolates, accession/stock range, grouped organism counts, and laboratory staff in charge. Each receipt may span multiple batch rows. A final totals row sums receipt, decision, batch, and organism columns.

The report reads only saved receiving, accession, batch, and organism-count records. It does not accept handwritten-style free-form totals and does not infer acceptance decisions. The PDF is produced with ReportLab, reopened for structural checks in tests, rendered to PNG during visual verification, and offered alongside the browser print preview.

## Testing and Acceptance

- Model/form tests: count consistency, date bounds, 1–9999 ranges, padded previews, overlap, protected relations, backward-compatible backfill.
- Shared-service tests: manual and laboratory creation produce identical batch/accession/TAT structures; exact duplicates, partial overlaps, Final conflicts, orphan recovery, repeat submission, and transactional rollback.
- Permission tests: LAB Encoder laboratory actions, existing DMU manual route, crafted POST denial, targeted DMU ownership handoff.
- View/template tests: custody save independent of batch generation, multiple pending/generated ranges, conflict links, POST-only actions, read-only Secretariat information, responsive shared layout.
- Register tests: month/year filtering, multi-batch row grouping, organism column placement, totals, access control, PDF headers, and landscape page generation.
- Regression: full Receiving, Home, Verification, Django checks, migration consistency, and project suite.
- Manual acceptance: desktop/mobile laboratory workspace, optional QR navigation, printed label scan, generated batch link, DMU handoff, and verified/released data protection.

## Documentation

Record the approved design, implementation/migration evidence, test outcomes, browser acceptance, and deployment status in `PHASE_2_LIVING_PROJECT_HISTORY.md`. Do not claim production rollout from local tests or a merge.
