# Receiving Hardening and UI Refinement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the receiving custody audit path, align all receiving pages with the established ARSP visual system and microscopy background, and update the Phase 2 living documentation with verified implementation evidence.

**Architecture:** Keep the existing three-state custody workflow and explicit administrator-only mistaken-entry deletion, but enforce workflow rules at service, admin, view, and database boundaries. Consolidate receiving presentation in one static stylesheet that reuses `assets/img/theme/micros_2.png`, then apply a consistent hero, content shell, cards, table, status badges, responsive actions, and accessible forms to every receiving page. Preserve the living history by appending and superseding records rather than rewriting earlier entries.

**Tech Stack:** Django 5.2, PostgreSQL, Django forms/admin/templates, Bootstrap 4/Argon, project static assets, Django `TestCase`

**Spec:** `docs/phase2/2026-11-12-receiving-phase-design.md` and `docs/phase2/PHASE_2_LIVING_PROJECT_HISTORY.md`

## Global Constraints

- Preserve the existing user-owned `core/settings.py` host change.
- Receiving remains a courier-batch custody workflow; isolate sorting, accessioning, acceptance, and rejection remain out of scope.
- Existing QR payloads and UUID routes remain stable.
- Permanent deletion remains administrator-only but is limited to unendorsed `received` records.
- Workflow-owned fields cannot be edited through Django admin.
- Submitted custody timestamps cannot be later than the current server-local time.
- Every behavioral change follows RED-GREEN TDD.
- Documentation distinguishes merged code, local verification, browser validation, and production deployment.

## Review Focus

- A non-receiving user calling a service directly must not create or advance custody records.
- An administrator must not permanently delete an endorsed or laboratory-received custody record.
- Direct model/admin edits must not create status/field combinations that contradict the custody state.
- A receipt or laboratory timestamp later than the current server-local time must be rejected without mutation.
- Receiving pages must remain usable on narrow screens while loading the same `micros_2.png` background used by the established data pages.

---

### Task 1: Audit and transition hardening

**Files:**
- Modify: `apps/receiving/tests.py`
- Modify: `apps/receiving/test_qr_delete.py`
- Modify: `apps/receiving/forms.py`
- Modify: `apps/receiving/services.py`
- Modify: `apps/receiving/models.py`
- Modify: `apps/receiving/admin.py`
- Modify: `apps/receiving/views.py`
- Create: `apps/receiving/migrations/0002_receiving_integrity_constraints.py`

**Interfaces:**
- Produces: `ReceivingPermissionDenied`, service-level role enforcement, timestamp validation, `can_permanently_delete` state policy, and database check/unique constraints.
- Consumes: existing receiving permission functions and three workflow states.

- [ ] **Step 1: Write failing tests**

Add tests proving direct service calls reject unauthorized users, receipt/laboratory forms reject future timestamps, admin workflow fields are read-only and admin add/delete are disabled, endorsed/laboratory-received records cannot be permanently deleted, and database constraints reject inconsistent custody states and duplicate transition events.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python manage.py test apps.receiving.tests.ReceivingHardeningTests apps.receiving.test_qr_delete.QrAcknowledgmentAndDeletionTests -v 2`

Expected: FAIL because the hardening policies and constraints do not exist.

- [ ] **Step 3: Implement minimal hardening**

Enforce permissions in services, validate future timestamps in forms and services, expose a model state property for safe permanent deletion, lock and re-check state during deletion, make `ReferralReceiptAdmin` read-only with no add/delete path, and add database constraints for state completeness and one canonical event per transition type.

- [ ] **Step 4: Generate/inspect migration and run focused tests GREEN**

Run: `python manage.py makemigrations receiving`

Expected: one migration containing the new constraints.

Run: `python manage.py test apps.receiving.tests.ReceivingHardeningTests apps.receiving.test_qr_delete.QrAcknowledgmentAndDeletionTests -v 2`

Expected: PASS.

---

### Task 2: Shared receiving visual system and page layouts

**Files:**
- Create: `apps/static/assets/css/receiving.css`
- Modify: `apps/templates/receiving/dashboard.html`
- Modify: `apps/templates/receiving/receipt_form.html`
- Modify: `apps/templates/receiving/receipt_detail.html`
- Modify: `apps/templates/receiving/lab_receipt_form.html`
- Modify: `apps/templates/receiving/receipt_label.html`
- Modify: `apps/templates/receiving/confirm_permanent_delete.html`
- Modify: `apps/receiving/tests.py`

**Interfaces:**
- Produces: `.receiving-page`, `.receiving-hero`, `.receiving-surface`, status badge, table, form, timeline, responsive-action, and print-label styles.
- Consumes: `assets/img/theme/micros_2.png`, existing view context and route names.

- [ ] **Step 1: Write failing rendered-page tests**

Add tests proving the receiving pages load `assets/css/receiving.css`, use the shared page/hero/surface structure, preserve role/status actions, contain accessible table/form labels, and render the print label with the shared background visual while excluding patient data.

- [ ] **Step 2: Run template tests and verify RED**

Run: `python manage.py test apps.receiving.tests.ReceivingTemplateTests -v 2`

Expected: FAIL because the shared stylesheet and page-shell structure do not exist.

- [ ] **Step 3: Implement the shared stylesheet and refactor templates**

Reuse the established navy-to-teal header, translucent light surfaces, microscopy background, compact responsive controls, semantic status colors, improved empty state, custody timeline, and mobile stacking. Keep all workflow wording and authorization conditions intact.

- [ ] **Step 4: Run template tests GREEN**

Run: `python manage.py test apps.receiving.tests.ReceivingTemplateTests -v 2`

Expected: PASS.

---

### Task 3: Living documentation and end-to-end verification

**Files:**
- Modify: `docs/phase2/PHASE_2_LIVING_PROJECT_HISTORY.md`
- Modify: `docs/phase2/2026-11-12-receiving-phase-design.md`
- Modify: `docs/phase2/2026-11-12-receiving-phase-implementation-plan.md`

**Interfaces:**
- Produces: DOC-003, implementation/risk/change entries, supersession notes, merged-PR evidence, and current verification state.
- Consumes: Tasks 1-2 results and current Git history.

- [ ] **Step 1: Update living records without rewriting prior history**

Append the merged receiving implementation, manual acknowledgment, audit-hardening/UI change request, risks and resolutions, local verification results, and explicit absence of browser/production evidence. Mark stale plan/design statements as superseded where appropriate.

- [ ] **Step 2: Run complete receiving verification**

Run: `python manage.py test apps.receiving -v 2`

Expected: PASS.

Run: `python manage.py makemigrations --check --dry-run`

Expected: `No changes detected`.

Run: `python manage.py check`

Expected: no issues.

- [ ] **Step 3: Run the full regression suite**

Run: `python manage.py test -v 1`

Expected: receiving remains green; report any unrelated failures by exact test name.

- [ ] **Step 4: Review the complete diff**

Confirm the existing `core/settings.py` change is untouched, no production/deployment claim is added, and every documentation status matches observed evidence.
