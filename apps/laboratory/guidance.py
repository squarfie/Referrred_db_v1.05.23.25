"""Role-aware guidance; authorization remains enforced at each POST endpoint."""
from .models import LaboratoryIsolate
from .permissions import can_manage_id_testing, can_review_id_results, can_manage_biobank


def next_action(isolate, user):
    S = LaboratoryIsolate.Status
    actions = {
        S.ID_PENDING: ('Receive for ID testing', 'Main Laboratory', can_manage_id_testing, 'handoff'),
        S.ID_TESTING: ('Record ID and return cassette', 'Main Laboratory', can_manage_id_testing, 'identification'),
        S.ID_RETURNED: ('Record revival outcome', 'Laboratory Receiving', can_review_id_results, 'revival'),
        S.REVIVAL_QUEUED: ('Record revival outcome', 'Laboratory Receiving', can_review_id_results, 'revival'),
        S.REVIVAL_IN_PROGRESS: ('Record revival outcome', 'Laboratory Receiving', can_review_id_results, 'revival'),
        S.REVIVAL_FAILED: ('Retry revival', 'Laboratory Receiving', can_review_id_results, 'revival'),
        S.REVIVED: ('Review identification', 'Laboratory Receiving', can_review_id_results, 'review'),
        S.ID_REVIEWED: ('Record harvest and lyophilization', 'Laboratory Receiving', can_review_id_results, 'lyophilization'),
        S.READY_FOR_BIOBANK: ('Record biobank storage', 'Biobank staff', can_manage_biobank, 'biobank'),
    }
    label, owner, permission, section = actions.get(isolate.status, ('Workflow complete' if isolate.status == S.BIOBANKED else 'Non-viable — closed', 'Laboratory Receiving', lambda user: False, 'closed'))
    return {'label': label, 'owner': owner, 'allowed': permission(user), 'section': section}
