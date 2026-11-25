"""Read-only progress indicators for the Bench A isolate detail.

The first three milestones are shared with the receipt/range; the remaining
three come from the isolate or its accession handoff. Never manufacture a
completed scientific step from an unrelated status or a printed QR.
"""

from apps.receiving.models import LaboratoryIsolate


def isolate_worksheet_steps(isolate):
    accession = isolate.accession_range
    lab_record = accession.laboratory_receiving
    S = LaboratoryIsolate.Status

    # Match the Receiving/acceptance worksheet completion requirement:
    # custody acknowledgement alone does not mean acceptance totals are saved.
    accepted = lab_record.isolates_accepted or 0
    rejected = lab_record.isolates_rejected or 0
    total = lab_record.total_isolates_received or 0
    receipt_done = bool(
        total and accepted + rejected == total and lab_record.date_accepted
    )
    accession_done = bool(isolate.pk and accession.pk)
    organisms_done = accession.organism_counts.filter(count__gt=0).exists()

    has_revival_record = bool(
        isolate.revival_started_at and isolate.revival_completed_at
    )
    has_site_id = bool(
        isolate.site_organism_code.strip() and isolate.site_organism_name.strip()
    )
    revival_done = has_revival_record and (
        isolate.status == S.NON_VIABLE
        or (isolate.status != S.REVIVAL_FAILED and has_site_id)
    )
    final_done = isolate.status in {S.BIOBANKED, S.NON_VIABLE}
    # This is an accession-level endorsement, not proof DMU accepted it.
    has_handoff = hasattr(accession, "dmu_handoff")
    dmu_done = bool(accession.batch_id or has_handoff)

    definitions = (
        ("receiving", "Receiving", receipt_done, "Acceptance summary saved", "Receipt"),
        ("accessioning", "Accessioning", accession_done, "Accession assigned", "Range"),
        ("organisms", "Organisms", organisms_done, "Inventory recorded", "Range"),
        ("revival", "Revival & Site ID", revival_done, "Outcome and Site ID", "Isolate"),
        ("final", "Final Processing", final_done, "Biobanking or closure", "Isolate"),
        ("dmu", "DMU Handoff", dmu_done, "Accession endorsed", "Range"),
    )
    first_pending = next(
        (key for key, _label, done, _hint, _scope in definitions if not done),
        None,
    )
    steps = [
        {
            "key": key,
            "label": label,
            "done": done,
            "current": key == first_pending,
            "hint": hint,
            "scope": scope,
        }
        for key, label, done, hint, scope in definitions
    ]
    return {
        "steps": steps,
        "completed": sum(step["done"] for step in steps),
        "total": len(steps),
        "all_done": first_pending is None,
    }
