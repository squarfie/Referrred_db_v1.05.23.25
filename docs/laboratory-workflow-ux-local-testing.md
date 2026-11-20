# Laboratory workflow UX: local review

Branch: `feature/laboratory-workflow-ux-simplification`.

The sidebar separates Receiving & Accessioning, Identification Testing, Revival & Processing, and Biobank Inventory. Each batch lists status, next action and responsible laboratory. The isolate page shows the current form and keeps results, actor/timestamp history, revival observations, approved identification and storage in a collapsible scientific record.

No database migration is required. Existing rows, statuses, accession relationships and legacy action endpoints are retained. Batch creation remains an explicit, conflict-checked action because accessioning can be partial. Old receiving return bookmarks lead to processing history.

Laboratory dashboard cards and tabs count receipts by current workflow: pending custody, received/accessioning before isolate handoff, pending Main Lab identification, revival/processing, and complete. A mixed receipt remains pending ID while any isolate is pending/testing. Completion requires a saved acceptance/rejection decision and all accepted isolates accounted for and biobanked or non-viable; an entirely rejected receipt is also closed. Failed revival and missing isolate records are not complete. These filters do not change custody status, and Secretariat retains its separate courier counters.

## Local walkthrough

1. As LAB Encoder, confirm receipt and acceptance, assign a range, and use Create Batch. Verify the batch code and accession range before submission.
2. As Laboratory Staff, open Identification Testing → batch → isolate. Acknowledge physical receipt. Save multiple instrument results if needed; use Save result and return cassette when physically returning it.
3. As LAB Encoder, open Revival & Processing. Verify the returned isolate can progress while another isolate is still in ID testing. Record actual start/completion dates and an observed outcome; unsuccessful outcomes require reasons.
4. Retry a failed revival. Confirm the new attempt dates and the original timestamps/observations remain in scientific history. Non-viable isolates remain closed.
5. After successful revival, accept the reported identification or explicitly choose a different approved organism. Confirm the original instrument report remains unchanged.
6. Record harvest and lyophilization dates. An approved WHONET code is reused; an older record without a code still offers the authoritative organism picker.
7. Open Biobank Inventory. Enter new storage data, save separately from lyophilization, and verify location/box/position and sticker contents.
8. Check Previous Isolate, Next Isolate and Back to Batch, role-specific waiting messages, completed history, and the receiving return bookmark redirect.

## Automated checks

Run from the project directory using the existing virtual environment:

```powershell
..\referred\Scripts\python.exe manage.py check
..\referred\Scripts\python.exe manage.py makemigrations --check --dry-run
..\referred\Scripts\python.exe manage.py test apps.receiving
..\referred\Scripts\python.exe manage.py test apps.receiving apps.home
```

Automated tests use an isolated test database. The implementation run uses Django's MD5 password hasher only within the test process to reduce fixture setup time; production password settings are unchanged.

Merge remains pending the user's confirmation of local testing. Unrelated settings, workbook and uploaded media changes are excluded from these commits.
