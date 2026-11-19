# Independent Laboratory Receiving Implementation Plan

> Execute test-first and preserve the existing Secretariat custody flow and manual Create Batch route.

## 1. Model the laboratory register

- Replace the incomplete unmigrated `LaboratoryReceiving` draft with a protected one-to-one receipt extension.
- Add accession-range and structured organism-count models, validation, constraints, admin read-only registration, and migration/backfill.
- Add failing model/form tests first, including manual accepted/rejected arithmetic and range overlap rules.

## 2. Extract shared batch creation

- Move batch range/code/accession/personnel/membership helpers and transactional creation into `apps/home/services/batch_creation.py`.
- Keep existing manual Create Batch behavior and error messages through typed service errors.
- Update batch edit and orphan-repair imports.
- Prove with tests that manual and laboratory entry produce the same `Batch_Table`, `Referred_Data`, and `TATform` structures.

## 3. Add the laboratory workspace

- Create detail, range, organism-count, and create-batch forms/actions with LAB Encoder mutation permissions and shared read access.
- Redirect completed custody confirmation to the workspace.
- Add clear receipt totals, explicit Accepted/Rejected inputs, pending/generated ranges, organism inventory, conflict feedback, and generated-batch links.
- Extend the shared microscopy-background Receiving UI and mobile layout.

## 4. Add the monthly register

- Build a month/year HTML preview matching the supplied receiving-and-inventory form.
- Generate an A4-landscape ReportLab PDF with grouped headings, multi-row receipt/batch layout, totals, and personnel.
- Add PDF access, data, and structural tests; render a representative PDF to PNG and visually inspect it.

## 5. Integrate, document, and verify

- Add targeted DMU handoff permission for laboratory-origin batches.
- Update Receiving events, sidebar active states, living project history, and design references.
- Run migration consistency, Django checks, focused Receiving/Home tests, then the full suite; distinguish known unrelated failures.

## 6. Simplify decision saving (change request 2026-11-19)

- Remove the separate `is_finalized` state and checkbox through a forward migration.
- Require received = accepted + rejected on every laboratory-decision Save; zero/zero remains valid for the custody-created placeholder.
- Permit accession ranges after a valid saved decision with at least one accepted isolate.
- Exclude zero-received placeholder records from the monthly register.
- Update model, workspace and regression tests first, then update the form, view, template, migration and living history.
