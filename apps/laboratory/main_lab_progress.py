"""Read-only Main Laboratory isolate milestones from persisted custody/ID evidence.

Main Laboratory is independent from Bench A final processing and DMU
handoff. Physical cassette return is tracked independently from ID completion.
"""
from apps.receiving.models import LaboratoryIsolate


def main_lab_progress(isolate):
    S = LaboratoryIsolate.Status
    handoff = bool(
        isolate.endorsed_at or isolate.retrieved_at or isolate.acknowledged_at
        or isolate.id_completed_at or isolate.identifications.exists()
    )
    # Temporary Site ID storage is *not* receipt into Main Lab.
    received = bool(isolate.acknowledged_at or isolate.retrieved_at)
    tested = bool(isolate.id_completed_at or (
        isolate.returned_at and isolate.identifications.exists()
    ))
    final_id = bool(
        isolate.approved_organism_code and isolate.final_organism
        and (isolate.final_identified_at or isolate.id_completed_at)
    )
    returned = bool(isolate.returned_at)
    steps = [
        ("handoff", "Endorsed to Main Lab", handoff, "Bench A / Main Lab"),
        ("received", "Received for testing", received, "Main Lab"),
        ("testing", "ID testing complete", tested, "Main Lab"),
        ("final", "Final ID recorded", final_id, "Main Lab"),
        ("return", "Cassette returned", returned, "Physical custody"),
    ]
    first_pending = next((key for key, _, done, _ in steps if not done), None)
    return {
        "steps": [
            {"key": key, "label": label, "done": done,
             "current": key == first_pending, "scope": scope}
            for key, label, done, scope in steps
        ],
        "completed": sum(bool(done) for _, _, done, _ in steps),
        "total": len(steps),
    }
