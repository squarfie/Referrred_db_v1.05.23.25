# Simplified laboratory workflow implementation plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task by task.

**Goal:** Implement the approved Bench A/Main Lab flow with truthful completion, retrieval and correction history.

**Architecture:** Extend the existing receiving-owned models additively. Keep transitions in locked laboratory services and share completion predicates across dashboards. Keep legacy routes and evidence compatible.

**Tech Stack:** Django, PostgreSQL, Django templates, existing organism resolver and PDF labels.

**Spec:** `docs/superpowers/specs/2026-11-23-simplified-bench-a-main-lab-design.md` (approved 2026-11-23).

## Global constraints

- Preserve existing records, dates, role identifiers, site/batch grouping and storage snapshots.
- Result availability never implies physical cassette return.
- Bench A/Admin edit Site ID and Final ID; Main Lab/Admin edit Main Lab results.
- Temporary storage never completes laboratory work; data-batch creation is optional.
- Do not apply migrations to the non-test database or modify the user's two Excel files.

## Review focus

- Replayed or concurrent completion, retrieval and endorsement requests must not duplicate evidence.
- A corrected Main Lab result must not overwrite Final ID or the original result.
- Legacy returned/reviewed cases remain actionable without fabricated completion dates.
- Every sticker entry point must read the corrected organism consistently.
- Redirect context must not allow external destinations or lose selected filters.

## Task 1: Persist truthful evidence and transitions

**Files:** receiving/models.py and a forward migration; laboratory/models.py, workflow.py; new laboratory/test_simplified_workflow.py.

**Interfaces:** Add ID_COMPLETE, completion and retrieval actor/time, retrieval storage snapshot, continuation actor/time; LaboratoryIdentificationCorrection linked optionally to a result and its revision. Services: `record_identification`, `retrieve_for_testing`, `proceed_to_lyophilization`, `correct_identification`. Add shared final-banking predicates.

- [ ] Write service regressions proving direct endorsement, retrieval, completion without return, continuation, final confirmation and permission/reason/organism validation.
- [ ] Run the focused tests and verify failures arise from the missing behavior.
- [ ] Implement additive models and locked services; use correction rows for before/after values. Keep original result rows unchanged.
- [ ] Generate migration without applying to the non-test database. Run focused tests to green.

## Task 2: Wire endpoints, imports and queue calculations

**Files:** laboratory/views.py, urls.py, main_lab_views.py, receiving_views.py, dashboard.py, guidance.py, permissions.py; home/permissions.py and display-label consumers.

**Interfaces:** Consume Task 1 services; expose POST retrieval, continuation and correction routes. Use result completion for Main Lab Completed and Bench A ID-complete queues. Keep legacy return recording explicit and separate.

- [ ] Add endpoint regressions for roles, manual/import completion, import rollback, legacy records and safe context navigation.
- [ ] Watch failures, then route mutations through services and update queue predicates/counts.
- [ ] Preserve existing group/role values while displaying Main Lab Staff and LAB Encoder — Bench A.
- [ ] Run endpoint/service regressions and affected role/queue tests.

## Task 3: Simplify user-facing pages

**Files:** laboratory templates for main lab, isolate, batch, worklist, receiving workspace, inventory and history; shared navigation templates as needed.

**Interfaces:** Use Task 2 route names/context. Preserve accordions and query context. Show result/correction history and independent physical custody facts.

- [ ] Add rendering regressions for direct endorsement, result-save wording, retrieval, ID complete, Final ID confirmation and correction controls.
- [ ] Replace normal queue/return/review controls with direct endorsement, Save ID result and Proceed to lyophilization.
- [ ] Add Site ID/Final ID/Main Lab correction forms with required reasons; show history. Add final ID confirmation to final banking.
- [ ] Run rendering and sticker tests, inspect representative rendered HTML/pages.

## Task 4: Review and verify

- [ ] Update changed old expectations only where the approved behavior supersedes them.
- [ ] Run laboratory, receiving and affected home suites; Django check, migration dry run, diff check and broader regression suite.
- [ ] Request independent code review, resolve important findings and rerun affected tests.
- [ ] Update living history and this checklist with exact results and limitations. Deliver local changes without merging, pushing or applying non-test migrations.

## Execution ledger

- 2026-11-23: Design approved; implementation in the existing checkout on feature/simplified-lab-workflow. Native execution follows user approval and avoids an extra execution-method approval round. User Excel edits excluded.
