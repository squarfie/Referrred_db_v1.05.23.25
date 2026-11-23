"""Role-aware guidance; authorization remains enforced at each POST endpoint."""
from .models import LaboratoryIsolate
from .permissions import can_manage_id_testing, can_review_id_results, can_manage_biobank, can_access_main_lab


def next_action(isolate, user):
    S = LaboratoryIsolate.Status
    actions = {
        S.ID_QUEUED: ('Await whole-batch endorsement to Main Lab', 'Laboratory Receiving', can_review_id_results, 'endorsement'),
        S.ID_PENDING: ('Receive for ID testing', 'Main Laboratory', can_access_main_lab, 'handoff'),
        S.ID_TESTING: ('Save ID result', 'Main Laboratory', can_access_main_lab, 'identification'),
        S.ID_COMPLETE: ('Proceed to lyophilization', 'Bench A', can_review_id_results, 'id_complete'),
        S.ID_RETURNED: ('Proceed to lyophilization', 'Bench A', can_review_id_results, 'id_complete'),
        S.REVIVAL_PENDING: ('Record revival outcome', 'Laboratory Receiving', can_review_id_results, 'revival'),
        S.REVIVAL_QUEUED: ('Record revival outcome', 'Laboratory Receiving', can_review_id_results, 'revival'),
        S.REVIVAL_IN_PROGRESS: ('Record revival outcome', 'Laboratory Receiving', can_review_id_results, 'revival'),
        S.REVIVAL_FAILED: ('Retry revival', 'Laboratory Receiving', can_review_id_results, 'revival'),
        S.REVIVED: ('Temporarily bank using Site ID or endorse for Main Lab ID', 'Laboratory Receiving',
                    lambda user: can_review_id_results(user) or can_manage_id_testing(user) or can_manage_biobank(user), 'disposition'),
        S.SITE_BANKED: ('Await Main Lab retrieval for ID testing', 'Main Laboratory',
                        can_access_main_lab, 'site_banked'),
        S.ID_REVIEWED: ('Record lyophilization', 'Laboratory Receiving', can_review_id_results, 'lyophilization'),
        S.READY_FOR_BIOBANK: ('Record biobank storage', 'Biobank staff', can_manage_biobank, 'biobank'),
    }
    if isolate.status == S.ID_RETURNED and not isolate.revival_completed_at:
        actions[S.ID_RETURNED] = ('Record missing revival outcome', 'Laboratory Receiving', can_review_id_results, 'revival')
    # Preserve the continuation of historical ID-first records without rewriting dates.
    if isolate.status == S.REVIVED and (isolate.returned_at or isolate.identifications.exists()):
        actions[S.REVIVED] = ('Proceed to lyophilization', 'Bench A', can_review_id_results, 'id_complete')
    if isolate.lyophilization_started_at and isolate.status in (S.ID_COMPLETE, S.ID_RETURNED, S.REVIVED, S.ID_REVIEWED):
        actions[isolate.status] = ('Record lyophilization', 'Bench A', can_review_id_results, 'lyophilization')
    if isolate.status == S.READY_FOR_BIOBANK and isolate.identification_source == 'main_lab':
        actions[S.READY_FOR_BIOBANK] = ('Record final storage', 'Bench A', can_review_id_results, 'biobank')
    label, owner, permission, section = actions.get(isolate.status, ('Workflow complete' if isolate.status == S.BIOBANKED else 'Non-viable — closed', 'Laboratory Receiving', lambda user: False, 'closed'))
    return {'label': label, 'owner': owner, 'allowed': permission(user), 'section': section}
