<!-- Source: Phase_2_Referral_Laboratory_Management_Living_Project_History_v0.2.docx -->
<!-- Repository representation of the complete v0.2 living history document. -->

# Phase 2: End-to-End Referral Laboratory Management

Referred Database Expansion — Secretariat, Laboratory AST, DMU and Rejection Management

DOCUMENT STATUS: Living project history + requirements discovery | Document v0.2 | Updated: 11 Nov 2026 | Proposed software milestone: Version 2.0

## 1. Naming and versioning decision

Use “Phase 2” as the implementation program and “Version 2.0” as the eventual software release. This avoids promising that the new system is production-ready before its workflows, permissions, testing and deployment are complete. Naming is the documentation convention; the scope and release acceptance criteria remain provisional.

| Term | Recommended use |
| --- | --- |
| Phase 2 | Multi-stage program for expanding the current DMU-focused database into an end-to-end system. |
| Version 2.0 | Release label only after agreed Phase 2 scope has been implemented, tested and accepted. |
| Document v0.1 | Editable baseline for requirements gathering; not an approved SOP or final system design. |

## 2. Executive summary

The existing Django/PostgreSQL Referred Database already supports a substantial portion of DMU activities, including accession-linked raw/final data, batch management, verification, reporting and WGS integration. Phase 2 proposes a unified online workflow beginning with referral receipt and ending with authorized result release, with a parallel documented process for rejected isolates.

The core principle is to extend the existing system without disrupting released records or rebuilding established DMU workflows. This document records proposed capabilities; laboratory and secretariat procedures must be validated with actual unit SOPs and responsible personnel.

## 3. Goals, scope and boundaries

- Create a traceable receipt-to-release record for every referred isolate, including submissions rejected before laboratory testing.
- Give Secretariat, Laboratory AST and DMU distinct, role-appropriate worklists within a shared accession-tracking workflow.
- Capture acceptance/rejection decisions, laboratory activities, reviews, handoffs, notification events and turnaround times.
- Reuse existing Django authentication, records, verification, reference data and WGS modules wherever appropriate.
- Maintain data integrity, controlled corrections, a complete audit trail and protection of authorized/released results.

## 4. Proposed end-to-end lifecycle

| Stage | Responsible unit (provisional) | System event / output |
| --- | --- | --- |
| 1. Referral / receipt | Secretariat | Register receipt, source site, documents, specimen/isolate metadata and arrival time. |
| 2. Acceptance screening | Secretariat + designated laboratory authority | Record acceptance decision, reason and reviewer. |
| 3A. Rejection path | Responsible approving unit / Secretariat | Document rejection reasons, notification, disposition, resubmission and closure. |
| 3B. Accepted path | Laboratory AST | Create worklist, record testing, QC, repeats and results. |
| 4. Laboratory validation | Assigned Checker / Verifier / lab authority | Review findings and authorize the handoff; keep task assignment distinct from actual sign-off. |
| 5. DMU processing | DMU | Use existing raw/final and verification processes; reconcile with laboratory-authorized results. |
| 6. Release and closure | Authorized release personnel | Generate approved report, record release/acknowledgment and close case. |

Note: Unit ownership and sequence are proposals, not statements of the current ARSRL SOP. They need confirmation at workflow-mapping sessions.

## 5. Proposed functional modules

### 5.1 Secretariat: receiving, triage and dispatch

- Referral registration and receiving log with receipt timestamp, referring site and submitted documents.
- Status tracking: received, awaiting review, accepted, rejected, awaiting clarification and closed (final list to be approved).
- Acceptance/rejection reasons, attachments, notifications and documented actions.
- Rejection closure and resubmission linkage without erasing the original history.
- Dashboard for pending receipts, unacknowledged referrals, overdue actions and rejection statistics.

### 5.2 Laboratory AST: worklist to validated result

- Accepted-isolate worklist linked to receipt/tracking ID and official isolate accession.
- AST test event records: instrument or method, antibiotic, MIC/zone value, dates, operator and repeat history.
- Versioned interpretation standards and breakpoint references; preserve raw measurement and interpreted result separately.
- QC checks, exception handling, review and laboratory authorization before results enter existing DMU workflows.
- Controlled corrections: no silent overwriting of signed or released findings.

### 5.3 DMU integration

- Reuse Raw Data, Final Data, verification stages, accession matching and reporting functionality.
- Document a single accountable transfer/handoff from authorized laboratory results into DMU processing.
- Avoid direct cross-unit editing of released Final Data; share status and references instead.
- Preserve the WGS module as an independent sequencing dataset linked by Final accession where available.

## 6. Rejection management specification (draft)

| Required element | Purpose |
| --- | --- |
| Receipt / tracking ID | Every submitted isolate needs a traceable record, including those rejected before official accession assignment. |
| Reason code + narrative | Standardized rejection category with an optional explanation; codes derived from actual SOP. |
| Decision metadata | Reviewer, decision timestamp, evidence and authority. |
| Site communication | Sent time, recipient/channel and acknowledgment or follow-up status. |
| Disposition | Return, discard, hold, re-request or other approved SOP action; not hard-coded until confirmed. |
| Resubmission link | Keep the original rejection as a permanent event, connect any replacement submission. |
| Closure and metrics | Document responsible closer, date and receipt-to-decision/notification times. |

## 7. Shared data architecture (conceptual)

Recommended new Django apps: secretariat and laboratory_ast. A shared referral_workflow app should only be introduced if common state and event history cannot be modeled cleanly without one. Preserve existing home, home_final, verification and wgs_app responsibilities.

| Entity / object | Proposed role / relationship |
| --- | --- |
| ReferralReceipt / IsolateTracking | Stable internal tracking key at receipt; may exist without an official isolate accession. |
| ReferralDecision / RejectionEvent | History of acceptance, rejection, reason, reviewer and timestamp. |
| ASTTest / ASTResult / ASTReview | Laboratory-owned measurement, interpretation, QC and authorization records. |
| WorkflowEvent / Handoff | Immutable who/what/when events, queue transfers and audit metadata. |
| Existing Referred_Data / Final_Data | Retain existing verified data structures and approvals; link using controlled, explicit references. |
| Existing WGS_Project / results | Continue independent WGS persistence and matching. |

Do not assume the same number can represent receipt tracking, specimen identity, sequencing-run identity and final accession. Assign separate identifiers and document mappings explicitly.

## 8. Access and accountability

- Secretariat: receipt and correspondence privileges, but no default permission to change AST measurements or verified results.
- Laboratory AST: testing and review permissions according to laboratory roles; separate performer from reviewer.
- DMU: existing encoding/checking/verifying/releasing permissions retained unless an approved policy changes them.
- Use request.user and server-side permission checks for each action; enforce POST-only destructive endpoints and document bulk actions.
- Track assigned_to separately from performed_by/approved_by; preserve historical reassignment and sign-off events.

## 9. Implementation roadmap

| Increment | Scope | Acceptance evidence |
| --- | --- | --- |
| P2.1 | Process discovery, accession policy, Secretariat receiving and tracking | Approved workflow diagrams, roles matrix, requirements sign-off. |
| P2.2 | Rejection handling and communication log | Acceptance/rejection/resubmission scenarios pass user acceptance tests. |
| P2.3 | Laboratory AST entry, QC, review and standards/version controls | Validation cases, QC scenarios and audit tests pass. |
| P2.4 | Authorized laboratory-to-DMU transfer and end-to-end traceability | Receipt-to-release test cases and reconciliation reports pass. |
| P2.5 | Operational dashboards, SOP alignment and production rollout | Training, backup/rollback testing and release sign-off. |

## 10. Nonfunctional requirements

- Privacy, least-privilege access, auditability, record retention and attachment security.
- Transaction-safe transitions, uniqueness checks and recovery of interrupted handoffs.
- Database backups, migration verification, deployment rollback and monitoring.
- Configurable SOP-defined statuses, reasons and turnaround-time targets.
- Scalable worklists/search, export access controls and accessibility for users in multiple units.

## 11. Open decisions requiring workflow owners

| Decision | Responsible for confirming |
| --- | --- |
| When is the official ARSRL accession issued: receipt, acceptance, or later? | Secretariat + Laboratory + DMU |
| Who can accept/reject an isolate, reverse a decision, and approve disposal? | Laboratory/Secretariat SOP owner |
| What are the required receipt documents, reject reasons and notification channels? | Secretariat SOP owner |
| What testing platforms, AST standards, breakpoint versions and QC rules apply? | Laboratory AST authority |
| At what exact point are AST results authorized for DMU processing? | Laboratory + DMU |
| Who releases reports and who receives acknowledgment? | Authorized release/Secretariat staff |
| What records require retention, archiving, access controls and audit export? | Management / designated compliance lead |

## 12. Immediate next actions

- Conduct a joint Secretariat–Laboratory–DMU process-mapping session, using one accepted, one rejected and one resubmitted isolate as examples.
- Collect existing forms, logs, SOPs, report templates and reference-code lists; identify current system fields that can be reused.
- Approve the accession/tracking ID policy and event timeline before designing database migrations.
- Write user stories and acceptance criteria for P2.1 (receiving and initial triage).
- Develop P2.1 in a feature branch and staging environment, not by changing production workflows first.

End of initial scope baseline. Governance and historical registers follow. No software changes were made by this document revision.

## 13. Documentation governance and source of truth

This document is intended to serve as the continuous historical record of the Referred Database, not merely a one-time Phase 2 plan. New decisions, reversals, requirements, bug fixes, implementation evidence and release outcomes must be appended and cross-referenced. Previously approved decisions must not be silently overwritten.

| Record | Purpose / rule |
| --- | --- |
| Decision register (D-###) | Record the decision, decision date, owner/approver, options, rationale, affected scope, status and source evidence. |
| Change request (CR-###) | Document why a modification is needed, requested date, impact, approval, plan and relationship to prior decisions. |
| Implementation register (IMP-###) | Record actual development start/completion dates, branch/PR/commit, migrations, tests and environment of deployment. |
| Issue/risk register (R-###) | Log unresolved operational, data-integrity, security and deployment risks with owners and due dates. |
| Document revisions (DOC-###) | Record each edit of this document with date, author/editor, amended sections and rationale. |

Dates must be recorded as actual calendar dates (YYYY-MM-DD). A planned target date is not an actual completion date. Record both separately. For meetings, use the local Philippines date; for GitHub or deployment evidence, retain the source timestamp and its timezone if relevant.

### 13.1 Decision lifecycle

| Status | Meaning |
| --- | --- |
| Proposed | Raised for consideration; not yet authorized as a requirement or SOP. |
| Under review | Awaiting owner consultation, impact analysis, or formal approval. |
| Approved | Authorized by the designated workflow or project owner; date and evidence recorded. |
| Implemented | Code/configuration/process changed with verified evidence and implementation date. |
| Validated | Acceptance testing or SOP sign-off completed; reviewer and evidence recorded. |
| Superseded / Rejected | Keep the historical entry and link the replacement decision or reason for rejection. |

Approval, implementation, validation and production deployment are distinct events; a GitHub merge is not proof of production deployment.

### 13.2 How the existing sections will be maintained

- Sections 4-8 remain provisional process designs until the Secretariat, Laboratory AST and DMU agree on the complete workflow and authorized role ownership.
- When a subject is finalized, mark its section with the controlling decision ID, approval date, SOP/reference version, effective date and acceptance criteria; update diagrams, tables and user stories together.
- When a requirement changes after approval, issue a new change request and decision, preserve the old historical decision as superseded, and cite the revision in the document history.
- Keep planned dates as TBD until agreed. Record actual code delivery, testing and deployment dates only after evidence exists.
- Store source evidence in GitHub (issues, pull requests, tags, commits), meeting minutes/SOP approvals, and migration/deployment records; include stable links or document IDs in the registers.

## 14. Dated decision register — initial entries

Initial entries are reconstructed only from the documented project discussion on 11 Nov 2026; they do not imply Secretariat/Laboratory SOP approval. Historical decisions from older releases should be backfilled separately from verified GitHub and official records.

| ID / decided or raised | Subject / goal | Status and evidence / next gate |
| --- | --- | --- |
| D-001 \| 2026-11-11 | Adopt "Phase 2" for the end-to-end program and reserve "Version 2.0" for a future accepted release. | Planning convention; release date TBD. Confirm formal project naming at governance review. |
| D-002 \| 2026-11-11 | Extend the DMU-centered Referred Database to Secretariat receiving/rejection and laboratory AST, through result release. | Scope direction expressed by project team; proposed workflow ownership remains under review. |
| D-003 \| 2026-11-11 | Preserve rejected referrals as traceable cases rather than treating them as deleted isolates. | Proposed design requirement; confirm rejection SOP, authority and retention rules. |
| D-004 \| 2026-11-11 | Maintain one continuous history of decisions, goals, plan dates, code changes, validation and actual rollout. | Documentation rule agreed for this draft; incorporated into document v0.2. |
| D-005 \| 2026-11-11 | Update itemized specifications when the complete cross-unit process is finalized, keeping prior versions traceable. | Documentation rule; full workflow remains provisional pending unit review. |

## 15. Goals, milestones and implementation register

The dates below are intentionally unassigned. Record a planned start/target only after a responsible owner agrees; record actual delivery or editing dates only after the work takes place.

| ID / Goal | Planned start → target | Actual implementation / evidence / state |
| --- | --- | --- |
| P2.1 — Map intake, accession and receipt rules; Secretariat receiving | TBD → TBD | Not started / awaiting approved workflow and accession policy. |
| P2.2 — Rejection, resubmission and communications | TBD → TBD | Not started / dependent on rejection SOP and responsible roles. |
| P2.3 — Laboratory AST, QC, review and authorization | TBD → TBD | Not started / dependent on laboratory SOP and standard/version rules. |
| P2.4 — Laboratory-to-DMU transfer, end-to-end traceability | TBD → TBD | Not started / dependent on handoff and verification agreement. |
| P2.5 — Dashboards, operational validation and rollout | TBD → TBD | Not started / production release subject to validation and sign-off. |

### 15.1 Baseline evidence from existing project — separate from Phase 2 delivery

| Date | Existing database event / evidence | Interpretation |
| --- | --- | --- |
| 2026-11-11 | GitHub PR #18 merged to main: WGS project preservation, deletion safeguards and reconciliation. PR: https://github.com/squarfie/Referrred_db_v1.05.23.25/pull/18 | Existing-system baseline; not a Phase 2 feature or proof that production was deployed. |
| 2026-11-11 | Reported local checks: Django system checks, 23 WGS tests, 49 verification tests, 61 home tests; migrations 0023 and 0024 applied locally. | Local validation reported in project discussion; production implementation date not established. |
| 2026-11-11 | Project team estimates around 95% of DMU processes have been incorporated. | Planning estimate only; no independently verified coverage inventory yet. |

## 16. Change request and implementation record templates

Duplicate the following fields for every proposed modification. Link related entries rather than replacing or removing the original record.

| Field | What to record |
| --- | --- |
| Change request ID / title | CR-### / concise change description and affected module or workflow section. |
| Request / decision dates | Requested on; reviewed on; approved or rejected on; effective date (if applicable). |
| Reason and goal | Operational problem, requested behavior, safety/data-integrity benefit, measurable completion criteria. |
| Requester / decision owner | Requesting unit; workflow owner; technical owner; designated approver. |
| Impact assessment | Affected users, roles, tables, permissions, imports, reporting, SOPs, existing records, migrations, risks. |
| Plan / actual dates | Planned start and target; actual started, developed, tested, merged, deployed and validated dates. |
| Evidence / changes | Issue/PR/commit/tag, changed files, migration IDs, test outcomes, backup and rollback records. |
| Final disposition | Proposed, approved, implemented, validated, deferred, superseded or rejected; subsequent CR/D IDs. |

## 17. Document revision history

| Revision / date | Summary of edits | Status |
| --- | --- | --- |
| DOC-001 \| v0.1 \| 2026-11-11 | Initial Phase 2 scope: Secretariat, Laboratory AST, DMU, rejection workflow, architecture, roadmap and open decisions. | Concept draft; no software implementation. |
| DOC-002 \| v0.2 \| 2026-11-11 | Added decision/change/implementation registries, dated baseline, goal tracker, approval rules and explicit requirement for future subject-level updates. | Living planning draft; no software implementation. |

Future entries must identify exactly which numbered sections were amended, the controlling D-/CR-/IMP- IDs, and the date of review/approval. Do not replace this history with a single "last updated" date.

Next documentary gate: complete and approve the receipt-to-release/rejection flow with Secretariat, AST and DMU owners before marking Sections 4-8 "Approved".


## 18. QR-based laboratory acknowledgment and permanent receiving deletion (2026-11-12)

**Decision / change request:** D-008 / CR-001 (QR handoff), D-009 / CR-002 (irreversible deletion). Requested and planned on 2026-11-12. Both are development decisions, not evidence of production deployment.

- **Goal:** A laboratory encoder scans the Secretariat batch QR, signs in, returns to the scanned receipt, reviews it and explicitly confirms laboratory custody. A scan itself cannot change status or sign the user in.
- **Proposed implementation:** Preserve a validated same-site `next` URL on login; give the laboratory a mobile-friendly confirmation page; show terminal acknowledged status without a repeat confirmation; correct the suggested receipt minute when a fresh endorsement contains seconds.
- **Permanent deletion decision:** Allow an administrator to permanently delete a mistakenly entered Receiving batch, along with its `ReceivingEvent` records, without recording a separate deletion-history event. Require login, administrator permissions, explicit POST and typed `DELETE PERMANENTLY` confirmation. Secretariat-only and LAB Encoder users do not receive this action.
- **Important limitation:** No in-application receiving-deletion event is retained. Infrastructure, hosting or database logs may still retain evidence; the application cannot promise the absence of such external logs. This deletion cannot be undone without a backup.
- **Affected areas:** `apps.authentication`, `apps.receiving`, login and receiving templates; no new migrations.
- **Implementation branch:** `phase2/qr-lab-acknowledgment`. Code changes have been committed to this branch on 2026-11-12. Tests included but results and production deployment are **not yet confirmed**.
- **Planned verification:** `python manage.py test apps.receiving`, `python manage.py check`, verify QR origin configuration and scan on a mobile browser, verify permission denial, successful acknowledgment, repeat scan and irreversible deletion on non-production data.
- **Supersedes:** Any earlier proposal to prohibit permanent deletion altogether. Normal business-event history remains intact except for an explicitly permanently deleted receiving batch and its associated events.

