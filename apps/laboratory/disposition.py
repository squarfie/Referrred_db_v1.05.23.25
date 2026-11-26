"""Display-only disposition summaries from the existing isolate and audit records.

A Bench A routing decision may be complete while identification, physical
storage, and eventual final processing remain outstanding.
"""
from apps.receiving.models import LaboratoryIsolate, ReceivingEvent


def isolate_disposition_summary(isolate):
    if isolate.endorsed_at:
        if isolate.status in {
            LaboratoryIsolate.Status.BIOBANKED,
            LaboratoryIsolate.Status.READY_FOR_BIOBANK,
        } or isolate.biobanked_at:
            next_step = "Complete or review final Bench A storage records."
        elif isolate.id_completed_at or isolate.status in {
            LaboratoryIsolate.Status.ID_COMPLETE,
            LaboratoryIsolate.Status.ID_RETURNED,
            LaboratoryIsolate.Status.ID_REVIEWED,
        }:
            next_step = "Main Laboratory Final ID is available; proceed with Bench A final processing."
        elif isolate.acknowledged_at or isolate.status == LaboratoryIsolate.Status.ID_TESTING:
            next_step = "Main Laboratory is responsible for ID testing and recording the Final ID."
        else:
            next_step = "Await Main Laboratory receipt for ID testing."
        return {
            "choice": "Endorsed to Main Laboratory",
            "badge": "Endorsed to Main Lab",
            "description": "Bench A handed this isolate to Main Laboratory for confirmatory ID. Main Lab acknowledgment and identification are separately recorded.",
            "actor": isolate.endorsed_by,
            "when": isolate.endorsed_at,
            "next_step": next_step,
            "kind": "main_lab",
        }

    site_status = isolate.status in {
        LaboratoryIsolate.Status.READY_FOR_BIOBANK,
        LaboratoryIsolate.Status.SITE_BANKED,
    } and isolate.identification_source == "site"
    if not site_status:
        return None

    # Retain the recorded decision timestamp even before physical storage is
    # performed. Older records may only contain the storage timestamp.
    event = ReceivingEvent.objects.filter(
        receipt=isolate.accession_range.laboratory_receiving.receipt,
        event_type=ReceivingEvent.EventType.CORRECTED,
        remarks__startswith=(
            f"Disposition: Temporary Site ID banking selected for "
            f"{isolate.accession_number} (isolate {isolate.pk});"
        ),
    ).select_related("performed_by").order_by("-recorded_at", "-pk").first()
    return {
        "choice": "Temporary banking using Site ID",
        "badge": "Temporary Site ID route selected",
        "description": "Bench A selected provisional Site ID banking. This decision is recorded; physical storage and Main Lab confirmation remain separate actions.",
        "actor": event.performed_by if event else isolate.site_banked_by,
        "when": event.occurred_at if event else isolate.site_banked_at,
        "next_step": (
            "Await Main Laboratory retrieval for ID testing."
            if isolate.status == LaboratoryIsolate.Status.SITE_BANKED
            else "Record temporary storage details before Main Lab retrieval."
        ),
        "kind": "site",
    }
