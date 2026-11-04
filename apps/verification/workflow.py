from .models import VerificationCase, VerificationComment


RAW_DRAFT_SEQUENCE = {
    "First Draft": "Second Draft",
    "Second Draft": "Third Draft",
    "Third Draft": "Third Draft",
}


def next_raw_draft_status(current_status):
    return RAW_DRAFT_SEQUENCE.get(current_status, "First Draft")


def open_correction_comments():
    return (
        VerificationComment.objects
        .filter(
            is_resolved=False,
            decision__in=[
                VerificationComment.DECISION_NEEDS_CORRECTION,
                VerificationComment.DECISION_RETURNED,
            ],
        )
        .exclude(
            resolution_status__in=[
                VerificationComment.RESOLUTION_HANDLED,
                VerificationComment.RESOLUTION_RESOLVED,
                VerificationComment.RESOLUTION_REJECTED,
            ]
        )
        .exclude(field_name__startswith="case-note:")
    )


def raw_encoding_is_editable(case_status, *, final_records_exist, has_open_corrections=False):
    """Raw edits follow verification status, not whether a Final copy exists.

    A correction explicitly requested during Raw Verification remains permitted.
    Final records must be flagged for review if Raw changes after copying.
    """
    if has_open_corrections and case_status == VerificationCase.STATUS_FOR_RAW_VERIFICATION:
        return True
    return case_status in {None, "", VerificationCase.STATUS_RETURNED_TO_ENCODER, VerificationCase.STATUS_WITHDRAWN}


def final_encoding_is_editable(case_status, *, final_records_exist, has_open_corrections=False):
    return bool(
        final_records_exist
        and (
            case_status in {None, "", VerificationCase.STATUS_FOR_FINAL_ENCODING}
            or (
                has_open_corrections
                and case_status in {
                    VerificationCase.STATUS_FOR_VERIFICATION,
                    VerificationCase.STATUS_RETURNED_TO_CHECKER,
                    VerificationCase.STATUS_RETURNED_TO_VERIFIER,
                    VerificationCase.STATUS_FOR_LAB_MANAGER,
                }
            )
        )
    )


def raw_batch_is_fully_copied(*, raw_count, final_count):
    return bool(raw_count and final_count >= raw_count)


def raw_table_has_active_workflow(case_status, *, final_records_exist):
    return bool(case_status and not final_records_exist)
