"""Read-only cross-process history for a courier receipt.

Events are projected from authoritative models; viewing history must never
advance custody, verification, biobanking, or release state.
"""
from .models import LaboratoryAccession


ISOLATE_MILESTONES = (
    ("id_queued_at", "Queued for Main Laboratory", "id_queued_by", "Laboratory"),
    ("endorsed_at", "Endorsed to Main Laboratory", "endorsed_by", "Custody"),
    ("acknowledged_at", "Main Laboratory acknowledged isolate", "acknowledged_by", "Custody"),
    ("id_completed_at", "Main Laboratory identification completed", "id_completed_by", "Main Laboratory"),
    ("returned_at", "Main Laboratory returned identification", "returned_by", "Custody"),
    ("retrieved_at", "Isolate retrieved from storage", "retrieved_by", "Biobank"),
    ("revival_queued_at", "Revival queued", "revival_queued_by", "Bench A"),
    ("revival_started_at", "Revival started", "revival_started_by", "Bench A"),
    ("revival_completed_at", "Revival completed", "revival_completed_by", "Bench A"),
    ("revival_retried_at", "Revival retried", "revival_retried_by", "Bench A"),
    ("reviewed_at", "Identification reviewed", "reviewed_by", "Bench A"),
    ("final_identified_at", "Final identification recorded", "final_identified_by", "Bench A"),
    ("lyophilization_started_at", "Lyophilization started", "lyophilization_started_by", "Bench A"),
    ("lyophilization_completed_at", "Lyophilization completed", "lyophilization_completed_by", "Bench A"),
    ("site_banked_at", "Temporarily stored under Site ID", "site_banked_by", "Biobank"),
    ("biobanked_at", "Isolate stored in biobank", "biobanked_by", "Biobank"),
)

VERIFICATION_MILESTONES = (
    ("sent_to_verifier_at", "Submitted to Verifier", "sent_by_checker"),
    ("withdrawn_at", "Verification submission withdrawn", "withdrawn_by"),
    ("verifier_decision_at", "Verifier decision recorded", "verifier_decision_by"),
    ("sent_to_lab_manager_at", "Submitted to Lab Manager", "sent_to_lab_manager_by"),
    ("lab_manager_decision_at", "Lab Manager decision recorded", "lab_manager_decision_by"),
    ("sent_to_head_at", "Submitted to Head", "sent_to_head_by"),
    ("head_decision_at", "Head decision recorded", "head_decision_by"),
    ("signed_by_head_at", "Signed by Head", "signed_by_head"),
    ("released_at", "Report released", None),
)


def receipt_activity(receipt):
    """Return chronological *recorded* milestones of all linked work.

    One courier receipt may produce many accession ranges, isolates, and
    batches. Null timestamps are ignored; nothing is inferred from status.
    """
    activity = []

    def add(when, label, source, description="", actor=None):
        if when is not None:
            activity.append({
                "when": when,
                "label": label,
                "source": source,
                "description": description,
                "actor": actor,
            })

    for event in receipt.events.select_related("performed_by").all():
        add(event.occurred_at, event.get_event_type_display(),
            "Receiving", event.remarks or receipt.tracking_number, event.performed_by)

    for accession in LaboratoryAccession.objects.filter(
        laboratory_receiving__receipt=receipt
    ).select_related("assigned_by", "batch").prefetch_related(
        "isolate_records__identifications__recorded_by",
        "isolate_records__identification_corrections__performed_by",
    ):
        code = accession.batch.bat_Batch_Code if accession.batch_id else (
            f"Accession {accession.start_number}–{accession.end_number}"
        )
        add(accession.assigned_at, "Accession range assigned",
            "Bench A", code, accession.assigned_by)
        add(accession.batch_created_at, "Raw Data batch generated",
            "Batch", code)
        # Pre-batch endorsement is recorded against the accession range,
        # before a Batch_Table can exist.
        handoff = getattr(accession, "dmu_handoff", None)
        if handoff:
            for event in handoff.events.select_related("performed_by").all():
                add(event.performed_at, event.get_action_display(),
                    "DMU Handoff", f"{code}: {event.remarks}".strip(" :"),
                    event.performed_by)

        for isolate in accession.isolate_records.all():
            for field, label, actor_field, source in ISOLATE_MILESTONES:
                add(getattr(isolate, field), label, source,
                    isolate.accession_number,
                    getattr(isolate, actor_field) if actor_field else None)

            for result in isolate.identifications.all():
                add(result.recorded_at, "Identification result recorded",
                    "Main Laboratory", isolate.accession_number,
                    result.recorded_by)

            for correction in isolate.identification_corrections.all():
                add(correction.performed_at, "Identification corrected",
                    "Main Laboratory" if correction.kind == "main_lab" else "Laboratory",
                    isolate.accession_number, correction.performed_by)

        if not accession.batch_id:
            continue

        custody = getattr(accession.batch, "receiving_custody", None)
        if custody:
            for event in custody.events.select_related("performed_by").all():
                description = f"{code}: {event.from_role}"
                if event.to_role:
                    description += f" → {event.to_role}"
                if event.remarks:
                    description += f" — {event.remarks}"
                add(event.performed_at, event.get_action_display(),
                    "Batch Custody", description, event.performed_by)

        for case in accession.batch.verification_cases.all():
            for field, label, actor_field in VERIFICATION_MILESTONES:
                when = getattr(case, field)
                actor = getattr(case, actor_field) if actor_field else None
                add(when, label, "Verification", code, actor)

            for comment in case.comments.select_related("created_by", "resolved_by").all():
                add(comment.created_at, "Verification comment recorded",
                    "Verification", f"{code}: {comment.get_decision_display()}", comment.created_by)
                add(comment.resolved_at, "Verification comment resolved",
                    "Verification", code, comment.resolved_by)

    # Deterministic chronology; source and label resolve equal timestamps.
    activity.sort(key=lambda item: (
        item["when"], item["source"], item["label"], item["description"],
    ))
    return activity


def receipt_progress_milestones(receipt):
    """Read-only, shipment-wide DMU and release progress.

    Each physical receipt can have multiple Laboratory accession ranges.
    Completion requires *all* of its ranges, never only the first batch.
    No timestamps are inferred from a status change.
    """
    ranges = list(
        LaboratoryAccession.objects.filter(laboratory_receiving__receipt=receipt)
        .select_related("dmu_handoff", "batch__tat_entry")
        .prefetch_related("batch__verification_cases")
    )
    total = len(ranges)
    dmu_count = 0
    dmu_dates = []
    released_count = 0
    release_dates = []

    for accession in ranges:
        handoff = getattr(accession, "dmu_handoff", None)
        if handoff is not None and handoff.confirmed_at is not None:
            dmu_count += 1
            dmu_dates.append(handoff.confirmed_at)

        if not accession.batch_id:
            continue
        batch = accession.batch
        verification_releases = [
            case.released_at
            for case in batch.verification_cases.all()
            if case.status == "released"
        ]
        has_verification_release = any(
            case.status == "released" for case in batch.verification_cases.all()
        )
        tat = getattr(batch, "tat_entry", None)
        has_tat_release = bool(tat and tat.tat_Status_Release == "Released")
        if not (has_verification_release or has_tat_release):
            continue
        released_count += 1
        # The recorded date can be absent in older released cases; don't
        # fabricate it from updated_at or the current date.
        known_dates = [value for value in verification_releases if value]
        if known_dates:
            release_dates.append(max(known_dates).date())
        elif tat and tat.tat_Date_Released:
            release_dates.append(tat.tat_Date_Released)

    dmu_done = bool(total and dmu_count == total)
    release_done = bool(total and released_count == total)
    return {
        "dmu_done": dmu_done,
        "dmu_count": dmu_count,
        "release_done": release_done,
        "released_count": released_count,
        "total": total,
        "dmu_at": max(dmu_dates) if dmu_done and dmu_dates else None,
        "released_on": max(release_dates) if release_done and len(release_dates) == total else None,
    }
