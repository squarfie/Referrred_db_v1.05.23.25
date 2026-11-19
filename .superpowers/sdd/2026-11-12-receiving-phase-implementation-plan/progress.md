# SDD ledger — plan: docs/phase2/2026-11-12-receiving-phase-implementation-plan.md

Execution: native on the existing `main` branch with explicit user consent.
Ruling: Commit checkpoints are omitted — the user authorized direct edits on `main`, and the assistant stated no commits would be made without a separate explicit request — cost if wrong: changes remain uncommitted and must be committed manually later.
Ruling: Superpowers execution scripts cannot run because this Windows environment has no Bash — maintain equivalent task briefs and ledger entries manually — cost if wrong: automated commit-range metadata will be unavailable, but test evidence and rulings remain recorded.
Baseline: `python manage.py test apps.receiving -v 2` → 0 tests, PASS; `python manage.py check` → no issues.
Baseline full suite: `python manage.py test -v 1 --keepdb` → 146 tests, 2 pre-existing failures: `PasswordResetSecurityTests.test_expired_link_is_rejected` and `PasswordResetSecurityTests.test_valid_link_resets_once_and_reuse_fails`; both expect the text `invalid or has expired`.
Pre-flight Task 1→Task 3/4: Task 1 produces receiving role functions and flags consumed by views/templates; names and role sets match the spec.
Pre-flight Task 2→Task 3: Task 2 produces forms/services consumed by views; signatures match the plan.
Pre-flight Task 3→Task 4: Task 3 produces route names and contexts consumed by templates; route/context requirements are consistent.
Pre-flight Task 1→Task 2: Task 2 lists Task 1 permission functions as consumed, but services must not infer HTTP authorization; permission checks remain in views/decorators while services enforce state invariants.
Task 1: RED `ReceivingPermissionTests` → missing Secretariat constant/flags and receiving permission module; GREEN 7/7 with dedicated read/manage/lab-confirm boundaries.
Task 1: complete (uncommitted by ruling, tests: `python manage.py test apps.receiving.tests.ReceivingPermissionTests -v 1 --keepdb` → 7 tests PASS; migration `0044_alter_arsstaff_details_staff_role.py` generated).
Task 4 note from user screenshot: place Receiving directly below Dashboard and show it only for receiving-read roles.
Task 2: RED form tests → missing receiving forms; GREEN 5/5 for defaults, normalization, and timestamp ordering.
Task 2: RED service tests → missing transaction services; GREEN 7/7 for creation/events, locked transitions, stale submissions, and custody timestamps.
Task 2: complete (uncommitted by ruling, tests: `python manage.py test apps.receiving.tests.ReceivingFormTests apps.receiving.tests.ReceivingServiceTests -v 1 --keepdb` → 12 tests PASS).
Task 3: RED QR tests → missing stable payload/PNG utility; GREEN 4/4. RED view tests → no receiving routes; GREEN QR + HTTP suite 13/13.
Task 3: complete (uncommitted by ruling, tests: `python manage.py test apps.receiving.tests.ReceivingQrTests apps.receiving.tests.ReceivingViewTests -v 1 --keepdb` → 13 tests PASS).
Task 4: RED UI tests → no routes/templates/navigation; initial GREEN attempt found 2 assertion/presentation failures. Systematic debugging established one test searched status text outside the Activity region and the label used Django `M` (no period) instead of `N` (AP-style period). Timeline assertion scoped to Activity; label switched to `N j, Y`.
Task 4: complete (uncommitted by ruling, tests: `python manage.py test apps.receiving.tests.ReceivingTemplateTests -v 1 --keepdb` → 8 tests PASS).
Pause checkpoint requested by user: Tasks 1–4 implemented. Resume at Task 5 with full `apps.receiving` suite, migration consistency, Django check, full suite comparison against the two baseline authentication failures, and browser smoke testing if available.
