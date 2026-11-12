# Receiving Phase Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver an authenticated, role-controlled receiving workflow from Secretariat courier-batch entry through LAB Encoder receipt confirmation, with a permanent Secretariat batch QR and append-only event history.

**Architecture:** Extend the existing `apps.receiving` app with focused forms, permissions, transaction-safe services, UUID-based views, and templates. QR PNGs are rendered on demand from a canonical receipt-detail URL; workflow state and audit events are changed together under database locks. The existing shared staff-role resolver gains a `Secretariat` role without changing permissions for any current role.

**Tech Stack:** Django 5, PostgreSQL, Django forms/templates/auth, `qrcode` 7.4.2, Pillow, Bootstrap/Argon, Django `TestCase`

**Spec:** `docs/phase2/2026-11-12-receiving-phase-design.md`

**Execution status (paused 2026-11-12):** Tasks 1–4 are implemented and their focused suites pass. Resume at Task 5 (migration, system, full-suite, and browser verification). Detailed RED/GREEN evidence and rulings are recorded in `.superpowers/sdd/2026-11-12-receiving-phase-implementation-plan/progress.md`.

## Global Constraints

- This phase ends at laboratory custody confirmation; sorting, acceptance/rejection, isolate creation, accession assignment, and accession QR labels are out of scope.
- One `ReferralReceipt` is one courier batch and may later relate to multiple isolates.
- The Secretariat QR is never replaced by an accession QR.
- QR payloads contain only the authenticated receipt-detail URL and no patient or isolate data.
- State-changing endpoints are POST-only, CSRF-protected, role-checked, and idempotent against stale/repeated submissions.
- All receiving templates live below `apps/templates/receiving/`.
- All documentation remains below `docs/phase2/`.
- The current workspace is not detected by Git as an active worktree, so commit checkpoints must be performed only if repository metadata is restored before execution.

## Review Focus

- Two users submit endorsement simultaneously: exactly one status change and one endorsement event must be persisted (Task 2 test).
- A LAB Encoder enters a laboratory timestamp earlier than endorsement: the form must reject it without changing the receipt (Task 2 test).
- A request reaches the QR endpoint through a local/IP hostname while `RECEIVING_QR_BASE_URL` is configured: the encoded destination must still use the configured canonical origin (Task 3 test).
- A user carries multiple roles, including Secretariat or LAB Encoder: permissions must be additive and the correct actions must remain available (Task 1 and Task 4 tests).
- Search input contains surrounding whitespace or a site name rather than a site code: dashboard filtering must normalize the query and return the expected receipt (Task 3 test).

---

### Task 1: Secretariat role and receiving permission boundaries

**Files:**
- Modify: `apps/home/permissions.py`
- Modify: `apps/home/forms.py`
- Modify: `apps/home/models.py`
- Create: `apps/home/migrations/0044_alter_arsstaff_details_staff_role.py`
- Create: `apps/receiving/permissions.py`
- Modify: `apps/receiving/tests.py`

**Interfaces:**
- Produces: `ROLE_SECRETARIAT = "Secretariat"` in `apps.home.permissions`
- Produces: `can_read_receiving(user) -> bool`, `can_manage_secretariat_receiving(user) -> bool`, and `can_confirm_lab_receipt(user) -> bool` in `apps.receiving.permissions`
- Produces: decorators `receiving_read_required`, `secretariat_receiving_required`, and `lab_receiving_required`
- Consumes: existing `get_user_roles(user)` and `role_required(*roles)`

- [ ] **Step 1: Write failing role-resolution and permission tests**

Add tests proving:

```python
class ReceivingPermissionTests(TestCase):
    def test_secretariat_group_is_resolved_as_secretariat_role(self): ...
    def test_secretariat_can_read_create_and_endorse_but_not_confirm_lab_receipt(self): ...
    def test_lab_encoder_can_read_and_confirm_but_not_create_or_endorse(self): ...
    def test_admin_can_read_create_and_endorse_but_not_confirm_lab_receipt(self): ...
    def test_unrelated_role_has_no_receiving_access(self): ...
    def test_multiple_roles_grant_the_union_of_receiving_permissions(self): ...
```

Assert the exact boolean results of the three permission functions and that `role_flags(user)` includes `is_secretariat` and `can_view_receiving`.

- [ ] **Step 2: Run the permission tests and verify RED**

Run: `python manage.py test apps.receiving.tests.ReceivingPermissionTests -v 2`

Expected: FAIL because `ROLE_SECRETARIAT` and `apps.receiving.permissions` do not exist.

- [ ] **Step 3: Add Secretariat to the shared role system**

In `apps/home/permissions.py`, define `ROLE_SECRETARIAT`, add it to normalization/group lookup/role priority, and expose `is_secretariat` plus `can_view_receiving` from `role_flags`. Do not add Secretariat to the existing raw/final/WGS/settings write-role sets.

In `apps/home/forms.py`, import the constant and include it in `STAFF_ROLE_CHOICES`. In `apps/home/models.py`, include `Secretariat` in `arsStaff_Details.Role_choices`.

- [ ] **Step 4: Add receiving-specific permission functions and decorators**

Implement:

```python
def can_read_receiving(user) -> bool
def can_manage_secretariat_receiving(user) -> bool
def can_confirm_lab_receipt(user) -> bool
```

with exact role sets `{Admin, Secretariat, LAB Encoder}`, `{Admin, Secretariat}`, and `{LAB Encoder}` respectively. Decorators must use the project-standard denied-action message and redirect to `home`.

- [ ] **Step 5: Generate and inspect the staff-role choices migration**

Run: `python manage.py makemigrations home`

Expected: one `AlterField` operation for `arsStaff_Details.Staff_Role`; rename the generated file to `0044_alter_arsstaff_details_staff_role.py` if Django uses a longer generated name.

- [ ] **Step 6: Run permission tests and verify GREEN**

Run: `python manage.py test apps.receiving.tests.ReceivingPermissionTests -v 2`

Expected: PASS.

- [ ] **Step 7: Commit checkpoint if Git metadata is available**

Commit message: `feat: add secretariat receiving permissions`

---

### Task 2: Validated forms and atomic workflow services

**Files:**
- Modify: `apps/receiving/models.py`
- Create: `apps/receiving/forms.py`
- Create: `apps/receiving/services.py`
- Modify: `apps/receiving/admin.py`
- Modify: `apps/receiving/tests.py`

**Interfaces:**
- Produces: `ReferralReceiptForm(forms.ModelForm)` with fields `tracking_number`, `site`, `date_sent`, `date_received`, `time_received`, `remarks`
- Produces: `LabReceiptForm(forms.Form)` initialized with `receipt`, with fields `lab_date_received`, `lab_time_received`
- Produces: `InvalidReceivingTransition(Exception)`
- Produces: `create_receipt(*, form: ReferralReceiptForm, user) -> ReferralReceipt`
- Produces: `endorse_receipt(*, receipt_id: int, user) -> ReferralReceipt`
- Produces: `confirm_lab_receipt(*, receipt_id: int, user, lab_date_received: date, lab_time_received: time) -> ReferralReceipt`
- Consumes: Task 1 permission functions and existing `ReferralReceipt`/`ReceivingEvent` models

- [ ] **Step 1: Write failing form tests**

Add `ReceivingFormTests` proving:

```python
def test_receipt_form_defaults_received_date_and_time_to_local_now(self): ...
def test_receipt_form_strips_and_collapses_tracking_number_whitespace(self): ...
def test_receipt_form_rejects_date_sent_after_secretariat_received_date(self): ...
def test_lab_form_rejects_timestamp_before_endorsement(self): ...
def test_lab_form_accepts_timestamp_equal_to_or_after_endorsement(self): ...
```

Freeze/patch the local-time provider only where needed; assert actual cleaned values and field errors rather than widget internals.

- [ ] **Step 2: Run form tests and verify RED**

Run: `python manage.py test apps.receiving.tests.ReceivingFormTests -v 2`

Expected: FAIL because `apps.receiving.forms` does not exist.

- [ ] **Step 3: Implement the two forms**

Use native `DateInput(type="date")` and `TimeInput(type="time")`, Asia/Manila-aware defaults from `timezone.localtime()`, cross-field date validation, and a `receipt` argument for laboratory timestamp validation. Do not expose status or user foreign keys.

- [ ] **Step 4: Run form tests and verify GREEN**

Run: `python manage.py test apps.receiving.tests.ReceivingFormTests -v 2`

Expected: PASS.

- [ ] **Step 5: Write failing service tests**

Add `ReceivingServiceTests` proving:

```python
def test_create_receipt_sets_server_owned_fields_and_appends_received_event(self): ...
def test_received_event_uses_submitted_secretariat_timestamp(self): ...
def test_endorsement_sets_server_time_user_status_and_one_event(self): ...
def test_repeated_endorsement_raises_without_duplicate_event(self): ...
def test_endorsement_cannot_skip_from_lab_received_or_other_invalid_state(self): ...
def test_lab_confirmation_sets_submitted_time_user_status_and_one_event(self): ...
def test_lab_confirmation_before_endorsement_raises_without_changes(self): ...
def test_repeated_lab_confirmation_raises_without_duplicate_event(self): ...
def test_competing_endorsements_persist_one_endorsement_event(self): ...
```

The concurrency regression may simulate the stale second transition sequentially against refreshed database state when the test database backend cannot safely run threaded transactions.

- [ ] **Step 6: Run service tests and verify RED**

Run: `python manage.py test apps.receiving.tests.ReceivingServiceTests -v 2`

Expected: FAIL because `apps.receiving.services` does not exist.

- [ ] **Step 7: Implement atomic workflow services**

Use `transaction.atomic()` for all three operations. Use `select_for_update()` inside both transition services, re-check status after locking, and raise `InvalidReceivingTransition` before any mutation for stale/invalid transitions. Combine submitted dates/times with `timezone.make_aware(..., timezone.get_current_timezone())` for event timestamps.

Add small model state properties such as `can_endorse` and `can_confirm_lab_receipt` only if they reduce duplicated display logic; do not embed role checks in models.

- [ ] **Step 8: Register receiving models in Django admin**

Register `ReferralReceipt` and `ReceivingEvent` with read-friendly list displays, search, filters, and `ReceivingEvent` audit fields read-only. Do not create an admin workflow that bypasses service transitions.

- [ ] **Step 9: Run form and service tests and verify GREEN**

Run: `python manage.py test apps.receiving.tests.ReceivingFormTests apps.receiving.tests.ReceivingServiceTests -v 2`

Expected: PASS.

- [ ] **Step 10: Commit checkpoint if Git metadata is available**

Commit message: `feat: add receiving workflow services`

---

### Task 3: UUID routes, dashboard HTTP behavior, and stable QR generation

**Files:**
- Create: `apps/receiving/urls.py`
- Create: `apps/receiving/qr.py`
- Modify: `apps/receiving/views.py`
- Modify: `core/urls.py`
- Modify: `core/settings.py`
- Modify: `apps/receiving/tests.py`

**Interfaces:**
- Produces named routes: `receiving_dashboard`, `receiving_create`, `receiving_detail`, `receiving_qr`, `receiving_label`, `receiving_endorse`, `receiving_lab_confirm`
- Produces: `build_receipt_detail_url(*, receipt: ReferralReceipt, request) -> str`
- Produces: `render_qr_png(payload: str) -> bytes`
- Consumes: Task 1 decorators and Task 2 forms/services

- [ ] **Step 1: Write failing QR utility tests**

Add `ReceivingQrTests` proving:

```python
@override_settings(RECEIVING_QR_BASE_URL="https://receiving.example.org")
def test_detail_url_uses_canonical_origin_and_receipt_uuid(self): ...

@override_settings(RECEIVING_QR_BASE_URL="https://receiving.example.org/")
def test_detail_url_normalizes_trailing_slash(self): ...

def test_local_detail_url_falls_back_to_request_origin_when_setting_blank(self): ...
def test_render_qr_png_returns_valid_png_bytes(self): ...
```

The configured-origin test must send a request with a different host and still assert `https://receiving.example.org/receiving/<uuid>/`.

- [ ] **Step 2: Run QR tests and verify RED**

Run: `python manage.py test apps.receiving.tests.ReceivingQrTests -v 2`

Expected: FAIL because `apps.receiving.qr` does not exist.

- [ ] **Step 3: Add the canonical base-URL setting and QR utility**

In `core/settings.py`, add:

```python
RECEIVING_QR_BASE_URL = config("RECEIVING_QR_BASE_URL", default="").rstrip("/")
```

Build the UUID detail path with `reverse`, join it to the configured origin when present, otherwise use `request.build_absolute_uri`. Render PNG bytes in memory with `qrcode`; do not write media files.

- [ ] **Step 4: Run QR tests and verify GREEN**

Run: `python manage.py test apps.receiving.tests.ReceivingQrTests -v 2`

Expected: PASS.

- [ ] **Step 5: Write failing view and route tests**

Add `ReceivingViewTests` proving:

```python
def test_all_receiving_pages_redirect_anonymous_users_to_login(self): ...
def test_unrelated_role_cannot_open_receiving_dashboard_or_detail(self): ...
def test_secretariat_create_post_ignores_spoofed_status_and_user_fields(self): ...
def test_create_redirects_to_uuid_detail_and_creates_received_event(self): ...
def test_dashboard_searches_trimmed_tracking_number_site_code_and_site_name(self): ...
def test_dashboard_filters_each_status_and_paginates(self): ...
def test_detail_is_looked_up_by_receiving_uuid_not_database_id(self): ...
def test_endorsement_and_lab_confirmation_reject_get_requests(self): ...
def test_secretariat_can_endorse_but_cannot_confirm_lab_receipt(self): ...
def test_lab_encoder_can_confirm_but_cannot_endorse(self): ...
def test_admin_cannot_confirm_lab_receipt(self): ...
def test_stale_transition_returns_message_without_duplicate_event(self): ...
def test_qr_endpoint_is_secretariat_or_admin_only_and_returns_image_png(self): ...
def test_label_endpoint_is_secretariat_or_admin_only(self): ...
```

- [ ] **Step 6: Run view tests and verify RED**

Run: `python manage.py test apps.receiving.tests.ReceivingViewTests -v 2`

Expected: FAIL because receiving URLs/views are not implemented or mounted.

- [ ] **Step 7: Implement URLs and views**

Mount `path("receiving/", include("apps.receiving.urls"))` before the catch-all home include. Use `<uuid:receiving_id>` for detail, QR, label, endorse, and lab-confirm routes.

Implement dashboard search with trimmed `q`, `Q(tracking_number__icontains=q) | Q(site__SiteCode__icontains=q) | Q(site__SiteName__icontains=q)`, validated status choices, `select_related` user/site fields, status counts, and pagination.

Use Task 2 services for all mutations, `require_POST` for endorsement, and the lab form for confirmation. Convert `InvalidReceivingTransition` into a clear Django message and redirect back to detail without mutation.

- [ ] **Step 8: Implement QR and label responses**

The QR endpoint returns `HttpResponse(png_bytes, content_type="image/png")` and a private-cache policy. The label view renders the receipt and QR endpoint URL; it does not duplicate QR generation in the template.

- [ ] **Step 9: Run all Task 3 tests and verify GREEN**

Run: `python manage.py test apps.receiving.tests.ReceivingQrTests apps.receiving.tests.ReceivingViewTests -v 2`

Expected: PASS.

- [ ] **Step 10: Commit checkpoint if Git metadata is available**

Commit message: `feat: add receiving routes and qr workflow`

---

### Task 4: Receiving pages, role-aware actions, and navigation

**Files:**
- Create: `apps/templates/receiving/dashboard.html`
- Create: `apps/templates/receiving/receipt_form.html`
- Create: `apps/templates/receiving/receipt_detail.html`
- Create: `apps/templates/receiving/lab_receipt_form.html`
- Create: `apps/templates/receiving/receipt_label.html`
- Modify: `apps/templates/includes/sidenav.html`
- Modify: `apps/receiving/tests.py`

**Interfaces:**
- Consumes: Task 3 contexts/routes and Task 1 `user_access.can_view_receiving` / `user_access.is_secretariat`
- Produces: project-styled dashboard, forms, detail/timeline, print label, and receiving sidebar entry

- [ ] **Step 1: Write failing template/visibility tests**

Add `ReceivingTemplateTests` proving:

```python
def test_dashboard_uses_project_layout_and_displays_status_counts(self): ...
def test_secretariat_sees_new_receipt_print_and_endorse_actions_only_when_valid(self): ...
def test_lab_encoder_sees_only_lab_confirmation_for_endorsed_receipt(self): ...
def test_admin_sees_secretariat_actions_but_not_lab_confirmation(self): ...
def test_terminal_lab_received_detail_exposes_no_transition_action(self): ...
def test_multi_role_user_sees_actions_granted_by_each_role(self): ...
def test_detail_displays_events_in_chronological_workflow_order(self): ...
def test_label_contains_qr_tracking_site_date_uuid_and_no_patient_fields(self): ...
def test_receiving_sidebar_link_is_visible_only_to_receiving_read_roles(self): ...
```

- [ ] **Step 2: Run template tests and verify RED**

Run: `python manage.py test apps.receiving.tests.ReceivingTemplateTests -v 2`

Expected: FAIL because receiving templates and navigation do not exist.

- [ ] **Step 3: Build the dashboard and entry forms**

Extend `layouts/base.html`; use existing Argon/Bootstrap components. Preserve `q` and `status` across pagination. Render field-level/non-field errors, CSRF tokens, native date/time inputs, and safe cancel/back links.

- [ ] **Step 4: Build detail and audit timeline**

Present courier, site, Secretariat, endorsement, and laboratory custody sections. Render transition actions only when both role and current state allow them. Show `lab_received` as “Received by Laboratory” and explicitly avoid “accepted” language.

- [ ] **Step 5: Build the print label**

Render only the receipt QR, courier tracking number, site code, Secretariat received date, and full receipt UUID. Add print CSS and a screen-only print button. Keep visual refinement intentionally minimal.

- [ ] **Step 6: Add role-aware receiving navigation**

Add a Receiving link with active-state handling. It is shown only when `user_access.can_view_receiving` is true, in both the review-only and normal sidebar branches where relevant.

- [ ] **Step 7: Run template tests and verify GREEN**

Run: `python manage.py test apps.receiving.tests.ReceivingTemplateTests -v 2`

Expected: PASS.

- [ ] **Step 8: Commit checkpoint if Git metadata is available**

Commit message: `feat: add receiving user interface`

---

### Task 5: Migration, system, and regression verification

**Files:**
- Modify only files required to resolve verified regressions from Tasks 1–4
- Update: `docs/phase2/2026-11-12-receiving-phase-implementation-plan.md` checkboxes during execution

**Interfaces:**
- Consumes: all prior tasks
- Produces: verified receiving feature with no missing migrations or newly introduced project failures

- [ ] **Step 1: Run the complete receiving test module**

Run: `python manage.py test apps.receiving -v 2`

Expected: all receiving tests PASS with no warnings attributable to the feature.

- [ ] **Step 2: Run migration consistency checks**

Run: `python manage.py makemigrations --check --dry-run`

Expected: `No changes detected`.

Run: `python manage.py migrate --plan`

Expected: the new home role-choice migration appears in a valid dependency order and no migration conflict is reported.

- [ ] **Step 3: Run Django system checks**

Run: `python manage.py check`

Expected: `System check identified no issues`.

- [ ] **Step 4: Run the full project test suite**

Run: `python manage.py test -v 2`

Expected: PASS. If unrelated pre-existing tests fail, record each failing test by name and demonstrate the focused receiving suite remains green; do not report a full green suite when it is not green.

- [ ] **Step 5: Perform a local browser smoke test when the environment supports it**

Verify, with one Secretariat and one LAB Encoder account: create batch, print/scan QR, endorse, confirm laboratory receipt, review timeline, and verify no rejection/accession action exists. Confirm the QR destination uses `RECEIVING_QR_BASE_URL` when configured.

- [ ] **Step 6: Final commit if Git metadata is available**

Commit message: `feat: complete isolate receiving phase`
