# QR Scan and Receive Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans for inline execution.

**Goal:** Scan either courier or accession QR, record authorized receipt once,
and open the next existing process.

**Architecture:** Dedicated GET/POST scan views share a CSRF-protected automatic
submission template. Batch confirmation reuses its existing locked service;
isolate acknowledgment becomes a shared locked service with receipt history.

**Tech Stack:** Django, existing qrcode library, small browser script.

**Spec:** `docs/superpowers/specs/2026-11-21-qr-scan-receive-design.md`

## Constraints and review focus

- Preserve existing uncommitted changes and ordinary detail URLs.
- No writes on GET, no duplicate receipt on replay, no bypass of role/status gates.
- Preserve the original actor/timestamp across repeat scans and concurrent posts.
- Login must preserve the scan destination; POST requires CSRF.
- QR images must encode a reachable scan URL, including the accession identifier.
- Later workflow states open current step; never infer scientific completion.

## Tasks

- [x] Write failing integration tests in `apps/receiving/test_scan_flow.py` for both
   handoffs, authentication, permissions, POST-only mutation, replay, timestamps,
   activity entries, and next-page routing. Run against a disposable test DB.
- [x] Add shared absolute scan URL builders, courier scan view and route,
   accession scan/QR views and routes, plus the auto-submit template/script.
- [x] Extract isolate acknowledgment into `apps/laboratory/workflow.py`, with row
   locking, role validation, idempotency, and receipt history. Reuse it manually.
- [x] Switch generated batch and sticker QRs to scan URLs; expose an isolate QR
   before sticker eligibility. Update existing QR payload expectations.
- [x] Run focused integration tests and the affected receiving/laboratory suites,
   Django checks, migration drift check, and diff checks. Review all changed code.

## Execution notes

- Implementation continues in the current shared workspace to preserve the
  user's in-progress receiving changes. No automatic commit or integration.
- Added migration 0009 for the ID receipt event choice and existing received-label
  choices. PostgreSQL SQL inspection confirmed both operations are no-ops; the
  migration was applied to the local application database.
- The configured QR hostname was absent from ALLOWED_HOSTS. Add only that
  explicitly configured hostname; a regression test verifies no wildcard or
  arbitrary hostname is introduced.
- Focused tests: 32 passed; expanded scan/label/concurrency tests: 18 passed.
  Final broad run: 210/211 passed; the sole remaining failure was a stale
  dashboard-caption assertion. Its corrected test then passed in isolation.
  Production code was unchanged after that broad run.
- Django checks, migration drift checks, JavaScript syntax and auto-submit
  execution checks, and diff whitespace checks passed. Independent review found
  no actionable defects. Phone-camera behavior was not physically tested.
