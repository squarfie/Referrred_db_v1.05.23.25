"""Role-aware guidance; authorization remains enforced at each POST endpoint."""
from .models import LaboratoryIsolate
from .permissions import can_manage_id_testing, can_review_id_results, can_manage_biobank


def next_action(isolate, user):
    S = LaboratoryIsolate.Status
    actions = {
        S.ID_QUEUED: ('Endorse queued isolate to Main Laboratory', 'Laboratory Receiving', can_manage_id_testing, 'endorsement'),
        S.ID_PENDING: ('Receive for ID testing', 'Main Laboratory', can_manage_id_testing, 'handoff'),
        S.ID_TESTING: ('Record ID and return cassette', 'Main Laboratory', can_manage_id_testing, 'identification'),
        S.ID_RETURNED: ('Confirm returned identification', 'Laboratory Receiving', can_review_id_results, 'review'),
        S.REVIVAL_PENDING: ('Record revival outcome', 'Laboratory Receiving', can_review_id_results, 'revival'),
        S.REVIVAL_QUEUED: ('Record revival outcome', 'Laboratory Receiving', can_review_id_results, 'revival'),
        S.REVIVAL_IN_PROGRESS: ('Record revival outcome', 'Laboratory Receiving', can_review_id_results, 'revival'),
        S.REVIVAL_FAILED: ('Retry revival', 'Laboratory Receiving', can_review_id_results, 'revival'),
        S.REVIVED: ('Bank using site ID or queue for endorsement', 'Laboratory Receiving',
                    lambda user: can_manage_id_testing(user) or can_manage_biobank(user), 'disposition'),
        S.SITE_BANKED: ('Queue temporarily banked isolate for Main Lab ID', 'Laboratory Receiving',
                        can_manage_id_testing, 'site_banked'),
        S.ID_REVIEWED: ('Record lyophilization', 'Laboratory Receiving', can_review_id_results, 'lyophilization'),
        S.READY_FOR_BIOBANK: ('Record biobank storage', 'Biobank staff', can_manage_biobank, 'biobank'),
    }
    if isolate.status == S.ID_RETURNED and not isolate.revival_completed_at:
        actions[S.ID_RETURNED] = ('Record missing revival outcome', 'Laboratory Receiving', can_review_id_results, 'revival')
    # Preserve the continuation of historical ID-first records without rewriting dates.
    if isolate.status == S.REVIVED and (isolate.returned_at or isolate.identifications.exists()):
        actions[S.REVIVED] = ('Confirm returned identification', 'Laboratory Receiving', can_review_id_results, 'review')
    label, owner, permission, section = actions.get(isolate.status, ('Workflow complete' if isolate.status == S.BIOBANKED else 'Non-viable — closed', 'Laboratory Receiving', lambda user: False, 'closed'))
    return {'label': label, 'owner': owner, 'allowed': permission(user), 'section': section}
