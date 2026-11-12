# Receiving Phase Design

## Intent

Build the first phase of the referred-isolate lifecycle for Secretariat and laboratory staff. The phase records a courier batch when it reaches the Secretariat, gives that batch a traceable QR label, records endorsement to the laboratory, and ends when a LAB Encoder confirms the laboratory received it.

The batch may contain multiple isolates. Individual isolate sorting, accession-number creation, and accession QR labels belong to the next phase and are not part of this implementation.

## Goals

- Let a Secretariat user record the courier tracking number, courier sent date, referring site, Secretariat received date and time, receiving user, and remarks.
- Generate one permanent receipt-level QR per courier batch without storing generated image files.
- Let Secretariat users print a simple batch label and endorse the batch to the laboratory.
- Let a LAB Encoder record the laboratory received date and time.
- Preserve an append-only event history for creation, endorsement, and laboratory receipt.
- Provide an authenticated dashboard and receipt-detail page with search, filtering, status, and available workflow actions.
- Add a dedicated `Secretariat` role to the existing staff-role system.
- Keep all receiving templates under `apps/templates/receiving/`.

## Non-goals

- Creating isolate records or laboratory accession numbers.
- Accepting or rejecting isolates; those decisions occur only after laboratory sorting.
- Recording rejection reasons, rejected quantities, or rejection dispositions.
- Generating the future per-accession QR label (for example, `26ARS_BGH001`).
- Replacing or changing the Secretariat batch QR after accessioning.
- Designing the final accession-sticker layout.
- Deleting receiving records.
- Public or unauthenticated QR access.

## Anti-goals

- Do not encode patient, isolate, or other sensitive details in the receipt QR.
- Do not allow users to skip, reverse, or repeat workflow transitions.
- Do not trust posted user IDs, status values, or transition timestamps when those values should be determined by the authenticated user or server workflow.
- Do not store generated QR PNGs in the database or media storage.

## Existing Constraints

- The existing `apps.receiving` app, `ReferralReceipt`, `ReceivingEvent`, and initial migration are the starting point and may be revised through new migrations where needed.
- `ReferralReceipt` represents one courier batch and can later be linked to multiple isolate/accession records.
- `SiteData.SiteCode` supplies the referring-site code.
- The project already uses Django authentication, staff profiles/groups, the Argon page layout, and `qrcode` with Pillow.
- Existing application roles remain in place; `Secretariat` is added without changing their current permissions.

## Approaches Considered

### 1. Generate the QR on demand from the receipt UUID — selected

The QR image endpoint renders a PNG when requested. Its payload is the absolute authenticated receipt-detail URL keyed by `receiving_id`. This avoids media-file lifecycle problems, supports unlimited reprints, and exposes neither a sequential database ID nor referral details.

### 2. Store a generated PNG for every receipt

This makes the image directly reusable but adds file creation, backup, synchronization, replacement, and cleanup work without improving this workflow.

### 3. Encode receipt details directly in the QR

This could be read without opening the application, but it would expose operational data outside authentication and could become stale after corrections.

## Workflow and Permissions

### Shared read access

Authenticated users with `Secretariat`, `LAB Encoder`, or `Admin` roles can open the receiving dashboard and receipt-detail pages. Other authenticated roles are denied receiving-module access.

### Secretariat receipt

1. A `Secretariat` or `Admin` user opens the new-receipt form.
2. They enter courier tracking number, site, courier sent date, Secretariat received date, Secretariat received time, and optional remarks.
3. The server assigns `received_by` to the authenticated user and `status` to `received`.
4. The receipt and its initial `ReceivingEvent(RECEIVED)` are created atomically.
5. The detail page offers a printable receipt-level label and an endorsement action.

### Endorsement

1. Only a `Secretariat` or `Admin` user can endorse.
2. Endorsement is available only while status is `received`.
3. A confirmed POST records server time in `endorsed_at`, the authenticated user in `endorsed_by`, changes status to `endorsed`, and appends `ReceivingEvent(ENDORSED)` atomically.
4. Repeated or stale endorsement attempts do not create duplicate events and return an explanatory message.

### Laboratory receipt

1. Only a user with the `LAB Encoder` role can perform the normal laboratory-receipt action. Admin may view the record but does not perform this transition.
2. Confirmation is available only while status is `endorsed`.
3. The LAB Encoder enters laboratory received date and time; the server assigns `lab_received_by` to the authenticated user.
4. Validation prevents a laboratory receipt timestamp earlier than the endorsement timestamp.
5. A successful POST changes status to `lab_received` and appends `ReceivingEvent(LAB_RECEIVED)` atomically.
6. `lab_received` confirms custody only; it does not mean the batch or any isolate has been accepted.
7. `lab_received` is terminal for this phase. Sorting and isolate-level acceptance or rejection begin in the next phase.

### Corrections and deletion

This first delivery does not expose general edit or delete actions. The existing `CORRECTED` event type remains available for a later controlled-correction workflow. Administrative database correction remains outside the receiving UI until correction authorization and field-level rules are designed.

## QR and Labels

### Secretariat batch QR

- One QR is generated per `ReferralReceipt`.
- It encodes only the absolute URL of the receipt-detail route using the non-sequential `receiving_id` UUID.
- The destination requires login and receiving-module read permission.
- The QR image and printable label endpoints are restricted to `Secretariat` and `Admin` users because the receipt label is for Secretariat use.
- The printable label initially shows the QR, courier tracking number, site code, Secretariat received date, and full receipt UUID. It is intentionally simple and print-friendly; this phase does not invent a second human-facing batch-number scheme.
- The QR remains stable for the lifetime of the receipt and is never overwritten by an accession QR.

### Future laboratory accession QR

After the laboratory sorts the courier batch, each isolate will receive its own accession. A later phase will generate one QR per accession whose payload is only the accession string, such as `26ARS_BGH001`. Its sticker will include the accession QR, site code, and received date. That later label is separate from the Secretariat batch label.

Sorting may also identify isolates that must be rejected. Rejection status, reason, disposition, and any related label or notification will be designed with that later sorting/accession phase rather than inferred during receipt confirmation.

## Screens

### Receiving dashboard

- Status summary counts for Received by Secretariat, Endorsed to Laboratory, and Received by Laboratory.
- Search by courier tracking number or site code/name.
- Status filter and pagination.
- Rows show tracking number, site, Secretariat receipt date/time, receiver, current status, and the next available action appropriate to the current user.
- `New receipt` is visible only to Secretariat/Admin.

### New receipt form

- Uses Django form validation and project styling.
- Date and time inputs use native date/time controls.
- Defaults Secretariat received date/time to the current local Asia/Manila date/time but allows the user to correct them before submission.
- Rejects a courier sent date later than the Secretariat received date.
- Does not expose receiver or status fields.

### Receipt detail

- Shows courier, site, Secretariat, endorsement, and laboratory-receipt information.
- Shows the chronological audit timeline from `ReceivingEvent`.
- Shows only actions valid for both the current status and user role.
- Secretariat/Admin can open the print label and endorse while allowed.
- LAB Encoder can confirm laboratory receipt while allowed.

### Print label

- Uses a minimal print-only layout under `templates/receiving/`.
- Includes no patient or isolate data.
- Has a print button that is hidden by print CSS.

## Model and Service Rules

- Keep `receiving_id` immutable and unique; use it in external receiving URLs.
- Normalize tracking-number whitespace in the form but do not force global uniqueness because courier tracking-number reuse/entry correction policy has not been established.
- Add model-level helper properties only for display/state queries; keep role checks in permissions and multi-row workflow transitions in a receiving service module.
- Transition services use `transaction.atomic()` and `select_for_update()` so two submissions cannot create duplicate state changes or events.
- Event `occurred_at` reflects the actual workflow time: the submitted Secretariat receipt date/time for the initial event, server endorsement time for endorsement, and submitted laboratory receipt date/time for laboratory receipt.
- Server `recorded_at` preserves when each event was entered.

## URL and File Shape

- `core/urls.py` mounts the app at `/receiving/`.
- `RECEIVING_QR_BASE_URL` supplies the canonical deployment origin used in every QR payload (for example, `https://database.example.org`). Local development may fall back to the current request origin, but a production deployment must configure this value so reprints from different hosts produce the same QR payload.
- Named routes cover dashboard, create, detail by UUID, QR PNG, printable label, endorse POST, and laboratory-receipt form/POST.
- `apps/receiving/forms.py` owns receipt-entry and laboratory-receipt validation.
- `apps/receiving/permissions.py` owns receiving-specific role gates using the shared role resolver.
- `apps/receiving/services.py` owns atomic creation and transitions.
- `apps/receiving/views.py` stays focused on HTTP behavior.
- All templates live below `apps/templates/receiving/`.

## Error Handling and Security

- All receiving routes require authentication.
- State-changing routes require POST and CSRF protection.
- Authorization is enforced server-side even when buttons are hidden.
- Missing receipts return 404; unauthorized access returns the project-standard permission redirect/message behavior.
- Invalid state transitions preserve data and display a clear message.
- QR generation failures return an HTTP error without changing receipt state.
- No protected information is placed in query strings or QR payloads.

## Verification

- Model/form tests cover date ordering, defaults, and normalized tracking input.
- Permission tests cover Secretariat creation/endorsement, LAB Encoder-only laboratory confirmation, Admin read/create/endorse behavior, and denial of unrelated roles.
- Workflow tests cover atomic event creation, allowed transitions, skipped/repeated transitions, timestamp ordering, and terminal status.
- View tests cover authentication, dashboard filtering/search, UUID detail routes, POST-only actions, QR PNG response/content, and label access.
- Template tests confirm receiving pages render under the project layout and expose actions only to the correct role/status combinations.
- Run the focused receiving tests, Django system check, migration consistency check, and the full project test suite; report pre-existing failures separately from regressions.

## Builder Calls

- Keep the first label compact and functional; visual refinement is intentionally deferred.
- Prefer a small dedicated receiving permission layer instead of broadening existing global write-role flags.
- Do not add an isolate-count field unless a later requirement defines whether it means declared, physically received, accepted, or accessioned isolates.
- Add the canonical QR origin as an environment-backed Django setting; do not hardcode the currently listed PythonAnywhere domain because deployment may change.
