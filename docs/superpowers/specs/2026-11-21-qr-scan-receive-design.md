# QR scan and receive

The user requested automatic receipt after scanning both courier and laboratory
accession QRs, followed by the next existing process page.

## Behavior

- A new courier scan URL authenticates the scanner, receives an endorsed batch
  for authorized Laboratory Receiving personnel, and opens its worksheet.
- A new accession scan URL authenticates the scanner, acknowledges an ID-pending
  isolate for authorized Main Laboratory personnel, and opens its current step.
- Record the signed-in actor and server timestamp. Preserve existing receipt
  details on repeat scans. Require existing endorsement and role checks.
- A GET renders an automatic CSRF-protected POST; receipt occurs on POST only.
  Without JavaScript, provide a Receive and continue button.
- Unendorsed batches and unauthorized actors cannot be received. Later isolate
  stages open their existing page without inferring scientific work.
- Receipt history includes isolate acknowledgment using the existing append-only
  event record and a new event-type choice; no new columns are needed.
- New QR images encode scan URLs. Existing accession-only stickers must be
  reprinted. Keep accession text visible. Provide an on-screen accession QR
  and printable accession QR before ID testing; biobank sticker eligibility
  remains unchanged.

## Implementation choices

Use dedicated scan URLs instead of changing the meaning of existing detail URLs;
ordinary browsing remains read-only. Use an automatic POST landing page instead
of mutating on GET. Reuse the existing batch confirmation service and extract a
shared, locked isolate acknowledgment service for manual and scan receipt.

QR URLs use RECEIVING_QR_BASE_URL when configured, otherwise the request origin.
The hostname of that configured origin is included in Django's allowed hosts.
Phones must be able to reach that origin. No new dependencies or automatic
endorsement, acceptance, scientific results, or storage decisions.
