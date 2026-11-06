import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from apps.home.forms import staff_role_q
from apps.home.models import (
    AntibioticEntry,
    Antibiotic_List,
    Batch_Table,
    Referred_Data,
    TATform,
    arsStaff_Details,
)
from apps.home.permissions import (
    ROLE_ADMIN,
    ROLE_CHECKER,
    ROLE_ENCODER,
    ROLE_LAB_MANAGER,
    ROLE_VERIFIER,
    can_manage_batch,
    get_user_roles,
    role_required,
)
from apps.home.templatetags.custom_filters import mic_format
from apps.home_final.models import Final_AntibioticEntry, Final_Data
from apps.home_final.utils import antibiotic_print_order, sort_abx_codes_by_antibiotic

from .models import VerificationCase, VerificationComment
from .workflow import next_raw_draft_status


GENERAL_NOTE_FIELD_PREFIX = "case-note:"
GENERAL_NOTE_AUDIENCE_CHOICES = (
    ("encoder", "Encoder"),
    ("checker", "Checker"),
    ("verifier", "Verifier"),
    ("lab_manager", "Lab Manager"),
    ("head", "Head/Admin"),
    ("all", "All reviewers"),
)
GENERAL_NOTE_AUDIENCE_LABELS = dict(GENERAL_NOTE_AUDIENCE_CHOICES)


def _tat_pressure_for_days(days, target_days=None):
    display_target_days = target_days or 40
    if days is None or not display_target_days:
        return "none"
    remaining_days = display_target_days - days
    tat_ratio = days / display_target_days
    if days > display_target_days:
        return "overdue"
    if 0 <= remaining_days <= 5:
        return "near"
    if tat_ratio >= 0.75:
        return "watch"
    return "safe"


def _tat_summary_from_entry(tat):
    if not tat:
        return None
    days = tat.tat_Running_TAT
    target_days = tat.tat_Target_Days or 40
    pressure = _tat_pressure_for_days(days, target_days)
    overdue_by = (days - target_days) if days is not None and target_days and days > target_days else None
    return {
        "days": days,
        "target_days": target_days,
        "pressure": pressure,
        "status": tat.tat_Status_Release or "",
        "overdue_by": overdue_by,
        "label": f"Overdue {overdue_by} days" if overdue_by is not None else (f"{days} days" if days is not None else "No TAT"),
    }


def _attach_tat_summaries(cases):
    case_list = list(cases)
    batch_ids = [case.final_batch_id for case in case_list if case.final_batch_id]
    batch_codes = [
        (case.final_batch.bat_Batch_Code or case.final_batch.bat_Batch_Name or "").strip()
        for case in case_list
        if case.final_batch_id and case.final_batch and (case.final_batch.bat_Batch_Code or case.final_batch.bat_Batch_Name)
    ]
    tat_by_batch_id = {
        tat.tat_Batch_Isolates_id: tat
        for tat in TATform.objects.filter(tat_Batch_Isolates_id__in=batch_ids)
    }
    tat_by_batch_code = {
        tat.tat_Batch_Code.strip(): tat
        for tat in TATform.objects.filter(tat_Batch_Code__in=batch_codes)
        if tat.tat_Batch_Code
    }
    for case in case_list:
        batch_code = ""
        if case.final_batch:
            batch_code = (case.final_batch.bat_Batch_Code or case.final_batch.bat_Batch_Name or "").strip()
        tat = tat_by_batch_id.get(case.final_batch_id) or tat_by_batch_code.get(batch_code)
        case.tat_summary = _tat_summary_from_entry(tat)
    return case_list


def _tat_summary_for_case(case):
    if not case.final_batch_id:
        return None
    batch_code = ""
    if case.final_batch:
        batch_code = (case.final_batch.bat_Batch_Code or case.final_batch.bat_Batch_Name or "").strip()
    tat = TATform.objects.filter(tat_Batch_Isolates_id=case.final_batch_id).first()
    if not tat and batch_code:
        tat = TATform.objects.filter(tat_Batch_Code=batch_code).first()
    return _tat_summary_from_entry(tat)


FINAL_REVIEW_FIELDS = (
    ("f_AccessionNo", "Accession No:"),
    ("f_Patient_ID", "Patient ID:"),
    ("f_First_Name", "First Name:"),
    ("f_Mid_Name", "Middle Name:"),
    ("f_Last_Name", "Last Name:"),
    ("f_Date_Birth", "Date of Birth:"),
    ("f_Age_Display", "Age:"),
    ("f_Sex", "Sex:"),
    ("f_SiteCode", "Site Code:"),
    ("f_Site_Name", "Site Name:"),
    ("f_Referral_Date", "Referral Date:"),
    ("f_Date_Admis", "Admission Date:"),
    ("f_Ward", "Ward:"),
    ("f_Spec_Date", "Specimen Date:"),
    ("f_Spec_Type", "Specimen Type:"),
    ("f_Spec_Num", "Specimen No:"),
    ("f_Reason", "Reason:"),
    ("f_Site_Pre_ed", "Site Pre Phenotype:"),
    ("f_Site_OrgName", "Site Organism Name:"),
    ("f_Site_Pos_ed", "Site Post Phenotype:"),
    ("f_Comments", "Remarks:"),
    ("f_ars_Pre_ed", "ARSRL Pre Phenotype:"),
    ("f_ars_OrgName", "ARSRL Organism Name:"),
    ("f_ars_Post_ed", "ARSRL Post Phenotype:"),
    ("f_ars_ct_ctl", "ESBL CT/CTL:"),
    ("f_ars_tz_tzl", "ESBL TZ/TZL:"),
    ("f_ars_cn_cni", "AmpC CN/CNI:"),
    ("f_ars_ip_ipi", "MBL IP/IPI:"),
    ("f_ars_reco", "Recommendation:"),
)

FINAL_REVIEW_FALLBACK_FIELDS = {
    "f_Site_Pre_ed": "f_Site_Pre",
    "f_Site_Pos_ed": "f_Site_Pos",
    "f_ars_Pre_ed": "f_ars_Pre",
    "f_ars_Post_ed": "f_ars_Post",
}


def _antibiotic_code_maps():
    rows = Antibiotic_List.objects.exclude(Abx_code__exact="").values("Whonet_Abx", "Abx_code", "Antibiotic")
    whonet_to_print = {}
    print_to_name = {}
    canonical_print_codes = {}
    for row in rows:
        print_code = (row["Abx_code"] or "").strip()
        whonet_code = (row["Whonet_Abx"] or "").strip().upper()
        if not print_code:
            continue
        canonical_print_codes.setdefault(print_code.upper(), print_code)
        if whonet_code:
            whonet_to_print[whonet_code] = print_code
        print_to_name.setdefault(print_code.upper(), (row["Antibiotic"] or "").strip())
    return whonet_to_print, print_to_name, canonical_print_codes


def _canonical_print_code(raw_code, whonet_to_print, canonical_print_codes):
    code = (raw_code or "").strip()
    if not code:
        return ""
    upper_code = code.upper()
    return whonet_to_print.get(upper_code) or canonical_print_codes.get(upper_code) or code


def _aligned_antibiotic_codes(site_codes, ars_codes, site_print_order, ars_print_order):
    combined = {code for code in [*site_codes, *ars_codes] if code}
    aligned = []
    for code in [*site_print_order, *ars_print_order]:
        if code in combined and code not in aligned:
            aligned.append(code)
    aligned.extend(sort_abx_codes_by_antibiotic(code for code in combined if code not in aligned))
    return aligned


def _antibiotic_comment_choices():
    whonet_to_print, print_to_name, canonical_print_codes = _antibiotic_code_maps()
    ordered_codes = []
    for code in [*antibiotic_print_order(show_site=True), *antibiotic_print_order(show_ars=True)]:
        canonical_code = _canonical_print_code(code, whonet_to_print, canonical_print_codes)
        if canonical_code and canonical_code not in ordered_codes:
            ordered_codes.append(canonical_code)
    ordered_codes.extend(
        sort_abx_codes_by_antibiotic(
            code for code in canonical_print_codes.values() if code not in ordered_codes
        )
    )
    return [
        {
            "code": code,
            "name": print_to_name.get(code.upper()) or code,
            "label": f"{code} - {print_to_name.get(code.upper()) or code}",
        }
        for code in ordered_codes
    ]


def _staff_for_role(*roles):
    return (
        arsStaff_Details.objects
        .filter(staff_role_q(*roles), User_Account__isnull=False)
        .select_related("User_Account")
        .distinct()
        .order_by("Staff_Name", "User_Account__username")
    )


def _staff_for_user(user):
    if not user or not user.is_authenticated:
        return None
    try:
        return user.arsp_staff_profile
    except arsStaff_Details.DoesNotExist:
        return None


def _staff_signature(staff):
    if not staff:
        return "", ""
    return staff.display_name, staff.Staff_License or ""


def _sync_case_signatories(case, checker_staff=None, verifier_staff=None, lab_manager_staff=None, head_staff=None):
    batch = case.final_batch
    if not batch:
        return

    checker_name, checker_license = _staff_signature(checker_staff)
    verifier_name, verifier_license = _staff_signature(verifier_staff)
    lab_manager_name, lab_manager_license = _staff_signature(lab_manager_staff)
    head_name, head_license = _staff_signature(head_staff)

    batch_updates = []
    if checker_name:
        batch.bat_Checker = checker_name
        batch.bat_Chec_Lic = checker_license
        batch_updates.extend(["bat_Checker", "bat_Chec_Lic"])
    if verifier_name:
        batch.bat_Verifier = verifier_name
        batch.bat_Ver_Lic = verifier_license
        batch_updates.extend(["bat_Verifier", "bat_Ver_Lic"])
    if lab_manager_name:
        batch.bat_LabManager = lab_manager_name
        batch.bat_Lab_Lic = lab_manager_license
        batch_updates.extend(["bat_LabManager", "bat_Lab_Lic"])
    if head_name:
        batch.bat_Head = head_name
        batch.bat_Head_Lic = head_license
        batch_updates.extend(["bat_Head", "bat_Head_Lic"])
    if batch_updates:
        batch.save(update_fields=sorted(set(batch_updates)))

    final_updates = {}
    if checker_name:
        final_updates["f_arsp_Checker"] = checker_name
        final_updates["f_arsp_Chec_Lic"] = checker_license
    if verifier_name:
        final_updates["f_arsp_Verifier"] = verifier_name
        final_updates["f_arsp_Ver_Lic"] = verifier_license
    if lab_manager_name:
        final_updates["f_arsp_LabManager"] = lab_manager_name
        final_updates["f_arsp_Lab_Lic"] = lab_manager_license
    if head_name:
        final_updates["f_arsp_Head"] = head_name
        final_updates["f_arsp_Head_Lic"] = head_license
    if final_updates:
        Final_Data.objects.filter(f_Batch_id=batch).update(**final_updates)


def _case_queryset(request):
    cases = (
        VerificationCase.objects
        .select_related(
            "final_batch",
            "final_accession",
            "assigned_checker",
            "assigned_verifier",
            "assigned_lab_manager",
            "assigned_head",
        )
        .annotate(
            comment_count=Count("comments", distinct=True),
            general_note_count=Count(
                "comments",
                filter=Q(comments__field_name__startswith=GENERAL_NOTE_FIELD_PREFIX),
                distinct=True,
            ),
            handled_comment_count=Count(
                "comments",
                filter=Q(comments__resolution_status=VerificationComment.RESOLUTION_HANDLED),
                distinct=True,
            ),
        )
    )
    roles = get_user_roles(request.user)
    if ROLE_ADMIN in roles or ROLE_CHECKER in roles:
        return cases
    visibility_q = Q(created_by=request.user) | Q(assigned_head=request.user)
    if ROLE_VERIFIER in roles:
        visibility_q |= Q(assigned_verifier=request.user) | Q(assigned_verifier__isnull=True)
    if ROLE_LAB_MANAGER in roles:
        visibility_q |= Q(assigned_lab_manager=request.user) | Q(assigned_lab_manager__isnull=True)
    return cases.filter(visibility_q).distinct()


def _bulk_comment_update(request, comments, action, note=""):
    roles = get_user_roles(request.user)
    allowed_encoder_checker = roles.intersection({ROLE_ADMIN, ROLE_CHECKER, ROLE_ENCODER})
    allowed_verifier = roles.intersection({ROLE_ADMIN, ROLE_VERIFIER})

    if action == "apply_all":
        messages.warning(
            request,
            "Comments are free text right now, so they cannot be auto-applied safely. Edit the fields manually, then mark them handled or resolved.",
        )
        return 0

    if action == "mark_handled":
        if not allowed_encoder_checker:
            messages.error(request, "Only encoders, checkers, or admins can mark comments as handled.")
            return 0
        resolution_status = VerificationComment.RESOLUTION_HANDLED
        is_resolved = True
    elif action == "resolve_all":
        if not allowed_verifier:
            messages.error(request, "Only verifiers or admins can mark comments as resolved.")
            return 0
        resolution_status = VerificationComment.RESOLUTION_RESOLVED
        is_resolved = True
    elif action == "reject_all":
        if not roles.intersection({ROLE_ADMIN, ROLE_CHECKER, ROLE_VERIFIER}):
            messages.error(request, "Only checkers, verifiers, or admins can reject comments.")
            return 0
        resolution_status = VerificationComment.RESOLUTION_REJECTED
        is_resolved = True
    else:
        messages.error(request, "Unsupported comment action.")
        return 0

    updated = comments.filter(is_resolved=False).update(
        is_resolved=is_resolved,
        resolution_status=resolution_status,
        resolution_note=note,
        resolved_at=timezone.now(),
        resolved_by=request.user,
    )
    if updated and action == "mark_handled":
        case_ids = (
            comments
            .filter(verification_case__status__in=[
                VerificationCase.STATUS_RETURNED_TO_ENCODER,
                VerificationCase.STATUS_RETURNED_TO_CHECKER,
            ])
            .values_list("verification_case_id", flat=True)
            .distinct()
        )
        VerificationCase.objects.filter(pk__in=case_ids).update(
            status=VerificationCase.STATUS_FOR_VERIFICATION,
            sent_to_verifier_at=timezone.now(),
            sent_by_checker=request.user,
            updated_at=timezone.now(),
        )
    return updated


def _case_detail_url(case_id, accession_id=None):
    url = reverse("verification_case_detail", kwargs={"case_id": case_id})
    if accession_id:
        url = f"{url}?accession_id={accession_id}"
    return url


def _is_assigned_lab_manager(case, user):
    return not case.assigned_lab_manager_id or case.assigned_lab_manager_id == user.id


def _can_write_case_field_comments(case, roles, user):
    if ROLE_ADMIN in roles:
        return True
    if ROLE_VERIFIER in roles and case.status in {
        VerificationCase.STATUS_FOR_RAW_VERIFICATION,
        VerificationCase.STATUS_FOR_VERIFICATION,
        VerificationCase.STATUS_RETURNED_TO_VERIFIER,
        VerificationCase.STATUS_RETURNED_TO_CHECKER,
    }:
        return True
    if (
        ROLE_LAB_MANAGER in roles
        and case.status == VerificationCase.STATUS_FOR_LAB_MANAGER
        and _is_assigned_lab_manager(case, user)
    ):
        return True
    is_assigned_head = case.assigned_head_id and case.assigned_head_id == user.id
    if is_assigned_head and case.status == VerificationCase.STATUS_FOR_HEAD:
        return True
    return False


def _can_submit_verifier_actions(case, roles):
    # Admins may act across batches, but cannot act outside review stages.
    if case.status not in {
        VerificationCase.STATUS_FOR_RAW_VERIFICATION,
        VerificationCase.STATUS_FOR_VERIFICATION,
        VerificationCase.STATUS_RETURNED_TO_VERIFIER,
        VerificationCase.STATUS_RETURNED_TO_CHECKER,
    }:
        return False
    return bool(ROLE_ADMIN in roles or ROLE_VERIFIER in roles)


def _can_submit_lab_manager_actions(case, roles, user):
    # No role, including Admin, may skip Lab Manager review.
    if case.status != VerificationCase.STATUS_FOR_LAB_MANAGER:
        return False
    return bool(
        ROLE_ADMIN in roles
        or (ROLE_LAB_MANAGER in roles and _is_assigned_lab_manager(case, user))
    )


def _can_route_final_review(case, roles, user):
    if case.status != VerificationCase.STATUS_FOR_FINAL_VERIFICATION:
        return False
    if ROLE_ADMIN in roles:
        return True
    if ROLE_VERIFIER in roles:
        return not case.assigned_verifier_id or case.assigned_verifier_id == user.id
    return False


def _can_route_lab_final_review(case, roles, user):
    return case.status == VerificationCase.STATUS_FOR_LAB_MANAGER and (
        ROLE_ADMIN in roles
        or (
            ROLE_LAB_MANAGER in roles
            and _is_assigned_lab_manager(case, user)
        )
    )


def _can_undo_send_to_lab_manager(case, roles, user):
    return case.status == VerificationCase.STATUS_FOR_LAB_MANAGER and (
        ROLE_ADMIN in roles
        or (
            ROLE_VERIFIER in roles
            and (
                not case.assigned_verifier_id
                or case.assigned_verifier_id == user.id
                or case.sent_to_lab_manager_by_id == user.id
            )
        )
    )


def _can_withdraw_from_head(case, roles, user):
    return case.status in {
        VerificationCase.STATUS_FOR_HEAD,
        VerificationCase.STATUS_FOR_FINAL_PRINTING,
    } and (
        ROLE_ADMIN in roles
        or (
            ROLE_LAB_MANAGER in roles
            and (
                not case.assigned_lab_manager_id
                or case.assigned_lab_manager_id == user.id
                or case.sent_to_head_by_id == user.id
            )
        )
    )


def _can_submit_head_actions(case, roles, user):
    return case.status in {
        VerificationCase.STATUS_FOR_HEAD,
        VerificationCase.STATUS_FOR_FINAL_PRINTING,
    } and (
        ROLE_ADMIN in roles or (case.assigned_head_id and case.assigned_head_id == user.id)
    )


def _can_withdraw_from_ready(case, roles, user):
    return case.status == VerificationCase.STATUS_SIGNED_BY_HEAD and (
        ROLE_ADMIN in roles or (case.assigned_head_id and case.assigned_head_id == user.id)
    )


def _can_undo_head_disapprove(case, roles, user):
    return case.status == VerificationCase.STATUS_DISAPPROVED_BY_HEAD and (
        ROLE_ADMIN in roles or (case.assigned_head_id and case.assigned_head_id == user.id)
    )


def _case_uses_raw_records(case):
    if not case.final_batch_id:
        return False
    if case.status in {
        VerificationCase.STATUS_FOR_RAW_VERIFICATION,
        VerificationCase.STATUS_RETURNED_TO_ENCODER,
    }:
        return True
    return not Final_Data.objects.filter(f_Batch_id=case.final_batch).exists()


def _case_accession_nav(case, requested_accession_id=None):
    if case.final_batch_id:
        if _case_uses_raw_records(case):
            accession_ids = list(
                Referred_Data.objects.filter(Batch_id=case.final_batch)
                .order_by("bat_seq", "AccessionNo", "id")
                .values_list("id", flat=True)
            )
        else:
            accession_ids = list(
                Final_Data.objects.filter(f_Batch_id=case.final_batch)
                .order_by("f_bat_seq", "f_AccessionNo", "id")
                .values_list("id", flat=True)
            )
    elif case.final_accession_id:
        accession_ids = [case.final_accession_id]
    else:
        accession_ids = []

    if not accession_ids:
        return None, None

    selected_id = requested_accession_id if requested_accession_id in accession_ids else accession_ids[0]
    selected_index = accession_ids.index(selected_id)
    return selected_id, {
        "first_id": accession_ids[0],
        "previous_id": accession_ids[selected_index - 1] if selected_index > 0 else None,
        "next_id": accession_ids[selected_index + 1] if selected_index + 1 < len(accession_ids) else None,
        "last_id": accession_ids[-1],
        "position": selected_index + 1,
        "total": len(accession_ids),
    }


def _recommendation_items(value):
    text = str(value or "").strip()
    if not text or text == "-":
        return []

    candidates = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = line.strip(" -\t")
        if line:
            candidates.append(re.sub(r"^\d+\s*[\.\)-]\s*", "", line).strip())

    if len(candidates) <= 1 and ";" in text:
        candidates = [
            re.sub(r"^\d+\s*[\.\)-]\s*", "", part.strip(" -\t")).strip()
            for part in text.split(";")
            if part.strip(" -\t")
        ]

    return candidates


@login_required(login_url="login")
def verification_dashboard(request):
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    active_tab = request.GET.get("tab", "").strip() or "pending"

    roles = get_user_roles(request.user)
    cases = _case_queryset(request)
    if query:
        cases = cases.filter(
            Q(final_batch__bat_Batch_Code__icontains=query)
            | Q(final_batch__bat_Batch_Name__icontains=query)
            | Q(final_batch__bat_SiteCode__icontains=query)
            | Q(final_batch__bat_Site_NameGen__icontains=query)
            | Q(final_accession__f_AccessionNo__icontains=query)
        )
    if status:
        cases = cases.filter(status=status)

    tab_groups = {
        "pending": {
            "label": "Pending For Action",
            "statuses": set(),
        },
        "sent_lab": {
            "label": "Sent to Lab Manager",
            "statuses": {VerificationCase.STATUS_FOR_LAB_MANAGER},
        },
        "returned": {
            "label": "Returned / Needs Correction",
            "statuses": {
                VerificationCase.STATUS_RETURNED_TO_ENCODER,
                VerificationCase.STATUS_RETURNED_TO_CHECKER,
                VerificationCase.STATUS_RETURNED_TO_VERIFIER,
            },
        },
        "for_head": {
            "label": "For Head Approval",
            "statuses": {
                VerificationCase.STATUS_FOR_HEAD,
                VerificationCase.STATUS_FOR_FINAL_PRINTING,
            },
        },
        "ready": {
            "label": "Ready for Release",
            "statuses": {VerificationCase.STATUS_SIGNED_BY_HEAD},
        },
        "disapproved": {
            "label": "Disapproved",
            "statuses": {
                VerificationCase.STATUS_DISAPPROVED_BY_LAB_MANAGER,
                VerificationCase.STATUS_DISAPPROVED_BY_HEAD,
            },
        },
        "released": {
            "label": "Released / History",
            "statuses": {
                VerificationCase.STATUS_RELEASED,
            },
        },
        "all": {
            "label": "All",
            "statuses": None,
        },
    }

    pending_q = Q(pk__in=[])
    if roles & {ROLE_ADMIN, ROLE_CHECKER}:
        pending_q |= Q(status__in=[
            VerificationCase.STATUS_FOR_RAW_VERIFICATION,
            VerificationCase.STATUS_FOR_FINAL_ENCODING,
            VerificationCase.STATUS_FOR_VERIFICATION,
            VerificationCase.STATUS_RETURNED_TO_CHECKER,
            VerificationCase.STATUS_RETURNED_TO_ENCODER,
        ])
    if ROLE_VERIFIER in roles:
        pending_q |= (
            Q(status__in=[
                VerificationCase.STATUS_FOR_RAW_VERIFICATION,
                VerificationCase.STATUS_FOR_FINAL_VERIFICATION,
            ])
            & (Q(assigned_verifier=request.user) | Q(assigned_verifier__isnull=True))
        ) | (
            Q(status=VerificationCase.STATUS_RETURNED_TO_VERIFIER)
            & (Q(assigned_verifier=request.user) | Q(assigned_verifier__isnull=True))
        )
    if ROLE_LAB_MANAGER in roles:
        pending_q |= (
            Q(status=VerificationCase.STATUS_FOR_LAB_MANAGER)
            & (Q(assigned_lab_manager=request.user) | Q(assigned_lab_manager__isnull=True))
        ) | Q(status=VerificationCase.STATUS_FOR_FINAL_VERIFICATION)
    if request.user.is_authenticated:
        pending_q |= Q(
            assigned_head=request.user,
            status__in=[
                VerificationCase.STATUS_FOR_HEAD,
                VerificationCase.STATUS_FOR_FINAL_PRINTING,
            ],
        )

    returned_q = Q(status__in=[
        VerificationCase.STATUS_RETURNED_TO_ENCODER,
        VerificationCase.STATUS_RETURNED_TO_CHECKER,
        VerificationCase.STATUS_RETURNED_TO_VERIFIER,
    ])
    if request.user.is_authenticated:
        returned_q |= Q(
            assigned_head=request.user,
            status=VerificationCase.STATUS_FOR_LAB_MANAGER,
        )

    if active_tab not in tab_groups:
        active_tab = "pending"

    pending_cases = cases.filter(pending_q).distinct()
    selected_tab = tab_groups[active_tab]
    if active_tab == "pending":
        displayed_cases = pending_cases
    elif active_tab == "returned":
        displayed_cases = cases.filter(returned_q).distinct()
    elif selected_tab["statuses"] is None:
        displayed_cases = cases
    elif selected_tab["statuses"]:
        displayed_cases = cases.filter(status__in=selected_tab["statuses"])
    else:
        displayed_cases = cases.none()

    displayed_cases = displayed_cases.order_by("-updated_at", "-created_at", "-id")
    paginator = Paginator(displayed_cases, 20)
    page_obj = paginator.get_page(request.GET.get("page"))
    page_case_ids = [case.id for case in page_obj.object_list]
    latest_notes_by_case = {}
    if page_case_ids:
        latest_notes = (
            VerificationComment.objects
            .filter(
                verification_case_id__in=page_case_ids,
                field_name__startswith=GENERAL_NOTE_FIELD_PREFIX,
            )
            .select_related("created_by")
            .order_by("verification_case_id", "-created_at")
        )
        for note in latest_notes:
            if note.verification_case_id not in latest_notes_by_case:
                latest_notes_by_case[note.verification_case_id] = note
        for case in page_obj.object_list:
            case.latest_general_note = latest_notes_by_case.get(case.id)
    page_obj.object_list = _attach_tat_summaries(page_obj.object_list)

    verification_tabs = []
    for tab_key, tab_config in tab_groups.items():
        tab_qs = cases
        if tab_key == "pending":
            count = pending_cases.count()
        elif tab_key == "returned":
            count = tab_qs.filter(returned_q).distinct().count()
        elif tab_config["statuses"] is None:
            count = tab_qs.count()
        elif tab_config["statuses"]:
            count = tab_qs.filter(status__in=tab_config["statuses"]).count()
        else:
            count = 0
        verification_tabs.append({
            "key": tab_key,
            "label": tab_config["label"],
            "count": count,
            "active": tab_key == active_tab,
        })

    existing_case_batch_ids = VerificationCase.objects.filter(
        final_batch__isnull=False,
        final_accession__isnull=True,
    ).values_list("final_batch_id", flat=True)
    open_final_batches = (
        Batch_Table.objects
        .filter(final_isolates__isnull=False)
        .exclude(id__in=existing_case_batch_ids)
        .annotate(final_count=Count("final_isolates", distinct=True))
        .order_by("-bat_Referral_Date", "-id")[:60]
    )

    context = {
        "cases": cases[:100],
        "displayed_cases": page_obj.object_list,
        "page_obj": page_obj,
        "displayed_case_count": paginator.count,
        "pending_cases": pending_cases[:100],
        "verification_tabs": verification_tabs,
        "active_tab": active_tab,
        "active_tab_label": selected_tab["label"],
        "open_final_batches": open_final_batches,
        "verifier_staff": _staff_for_role(ROLE_VERIFIER, ROLE_ADMIN),
        "lab_manager_staff": _staff_for_role(ROLE_LAB_MANAGER, ROLE_ADMIN),
        "head_staff": arsStaff_Details.objects.filter(
            Q(Is_Default_Head=True) | staff_role_q(ROLE_ADMIN),
            User_Account__isnull=False,
        ).select_related("User_Account").distinct().order_by("-Is_Default_Head", "Staff_Name"),
        "status_choices": VerificationCase.WORKFLOW_FILTER_CHOICES,
        "selected_status": status,
        "query": query,
    }
    return render(request, "verification/dashboard.html", context)


@login_required(login_url="login")
@role_required(ROLE_ADMIN, ROLE_CHECKER, ROLE_ENCODER)
@transaction.atomic
def send_batch_to_verifier(request, batch_id):
    if request.method != "POST":
        return redirect("verification_dashboard")

    next_url = request.POST.get("next") or request.GET.get("next") or ""
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = ""

    batch = get_object_or_404(Batch_Table, pk=batch_id)
    if not can_manage_batch(request.user, batch):
        messages.error(request, "You can only endorse batches that you are allowed to manage.")
        return redirect(next_url or "show_data")
    note = request.POST.get("note", "").strip()

    case, created = VerificationCase.objects.get_or_create(
        final_batch=batch,
        final_accession=None,
        defaults={
            "created_by": request.user,
            "assigned_checker": None,
        },
    )
    has_final_records = Final_Data.objects.filter(f_Batch_id=batch).exists()
    is_raw_endorsement = not has_final_records and (
        created
        or case.status in {
            VerificationCase.STATUS_DRAFT,
            VerificationCase.STATUS_WITHDRAWN,
            VerificationCase.STATUS_RETURNED_TO_ENCODER,
        }
    )
    is_final_endorsement = (
        has_final_records
        and (
            created
            or case.status in {
                VerificationCase.STATUS_DRAFT,
                VerificationCase.STATUS_WITHDRAWN,
                VerificationCase.STATUS_FOR_FINAL_ENCODING,
            }
        )
    )
    if not is_raw_endorsement and not is_final_endorsement:
        messages.error(request, "This batch is not currently available for endorsement to the verifier.")
        return redirect(next_url or _case_detail_url(case.id))
    sender_roles = get_user_roles(request.user)
    if is_raw_endorsement and not sender_roles.intersection({ROLE_ADMIN, ROLE_ENCODER}):
        messages.error(request, "Only the Raw Data Encoder can endorse Raw Data for verification.")
        return redirect(next_url or _case_detail_url(case.id))
    if is_final_endorsement and not sender_roles.intersection({ROLE_ADMIN, ROLE_CHECKER}):
        messages.error(request, "Only the Final Encoder can endorse Final Data for verification.")
        return redirect(next_url or _case_detail_url(case.id))

    verifier_staff = None
    if is_final_endorsement:
        verifier_staff = get_object_or_404(
            _staff_for_role(ROLE_VERIFIER, ROLE_ADMIN),
            pk=request.POST.get("verifier_staff"),
        )
        case.assigned_checker = request.user
        case.assigned_verifier = verifier_staff.User_Account
    else:
        case.assigned_verifier = None
    case.status = (
        VerificationCase.STATUS_FOR_RAW_VERIFICATION
        if is_raw_endorsement
        else VerificationCase.STATUS_FOR_FINAL_VERIFICATION
    )
    case.sent_by_checker = request.user
    case.sent_to_verifier_at = timezone.now()
    case.withdrawn_at = None
    case.withdrawn_by = None
    if note:
        case.note = note
    case.save()

    if is_raw_endorsement:
        batch.bat_Status = next_raw_draft_status(batch.bat_Status)
        batch.save(update_fields=["bat_Status"])

    if verifier_staff:
        _sync_case_signatories(
            case,
            checker_staff=_staff_for_user(request.user),
            verifier_staff=verifier_staff,
        )

    action = "created and sent" if created else "sent"
    messages.success(request, f"Verification case {action} for {batch.bat_Batch_Code or batch.bat_Batch_Name}.")
    return redirect(next_url or _case_detail_url(case.id))


@login_required(login_url="login")
@role_required(ROLE_ADMIN, ROLE_CHECKER, ROLE_ENCODER)
@transaction.atomic
def withdraw_verification_case(request, case_id):
    if request.method != "POST":
        return redirect("verification_case_detail", case_id=case_id)

    case = get_object_or_404(VerificationCase, pk=case_id)
    if case.final_batch_id and not can_manage_batch(request.user, case.final_batch):
        messages.error(request, "You can only withdraw batches that you are allowed to manage.")
        return redirect("show_data")
    next_url = request.POST.get("next") or request.GET.get("next") or ""
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = ""
    if case.status in {VerificationCase.STATUS_SIGNED_BY_HEAD, VerificationCase.STATUS_RELEASED}:
        messages.error(request, "Signed or released cases cannot be withdrawn.")
        return redirect(next_url or _case_detail_url(case.id))

    case.status = VerificationCase.STATUS_WITHDRAWN
    case.withdrawn_at = timezone.now()
    case.withdrawn_by = request.user
    case.save(update_fields=["status", "withdrawn_at", "withdrawn_by", "updated_at"])
    messages.success(request, "Verification case withdrawn from the verifier queue.")
    return redirect(next_url or _case_detail_url(case.id))


def _review_rows_for_case(case, selected_accession_id=None):
    whonet_to_print, print_to_name, canonical_print_codes = _antibiotic_code_maps()
    site_print_order = antibiotic_print_order(show_site=True)
    ars_print_order = antibiotic_print_order(show_ars=True)

    def antibiotic_result(method_prefix, disk_value, disk_ris, mic_operand, mic_value, mic_ris):
        methods = []
        result_parts = []
        if disk_value not in (None, "") or disk_ris:
            methods.append("Disk")
            result_parts.append(f"{disk_value or '-'} {disk_ris or ''}".strip())
        if mic_value not in (None, "") or mic_operand or mic_ris:
            methods.append("MIC")
            formatted_mic = mic_format(mic_value) if mic_value not in (None, "") else "-"
            mic_display = f"{mic_operand or ''}{formatted_mic} {mic_ris or ''}".strip()
            result_parts.append(mic_display)
        return {
            "test_method": " / ".join(methods) or method_prefix,
            "encoded_result": " | ".join(result_parts) or "-",
        }

    final_qs = Final_Data.objects.none()
    if case.final_batch_id:
        final_qs = Final_Data.objects.filter(f_Batch_id=case.final_batch)
    elif case.final_accession_id:
        final_qs = Final_Data.objects.filter(pk=case.final_accession_id)

    is_raw = _case_uses_raw_records(case)
    if is_raw:
        accession_qs = Referred_Data.objects.filter(Batch_id=case.final_batch)
        if selected_accession_id:
            accession_qs = accession_qs.filter(pk=selected_accession_id)
        accession_qs = accession_qs.prefetch_related(
            Prefetch(
                "antibiotic_entries",
                queryset=AntibioticEntry.objects.order_by("ab_Antibiotic", "ab_Retest_Antibiotic"),
            )
        ).order_by("bat_seq", "AccessionNo", "id")
    else:
        accession_qs = final_qs
        if selected_accession_id:
            accession_qs = accession_qs.filter(pk=selected_accession_id)
        accession_qs = accession_qs.prefetch_related(
            Prefetch(
                "final_entries",
                queryset=Final_AntibioticEntry.objects.order_by("ab_Antibiotic", "ab_Retest_Antibiotic"),
            )
        ).order_by("f_bat_seq", "f_AccessionNo", "id")

    rows = []
    for accession in accession_qs:
        accession_comment_filter = (
            {"raw_accession_ref_sequence": accession}
            if is_raw
            else {"final_accession_ref_sequence": accession}
        )
        accession_comments = list(
            case.comments
            .filter(**accession_comment_filter)
            .select_related("created_by", "resolved_by")
        )
        comments_by_field = {}
        for comment in accession_comments:
            comments_by_field.setdefault(comment.field_name or "", []).append(comment)

        field_rows = []
        for field_name, label in FINAL_REVIEW_FIELDS:
            source_field = field_name[2:] if is_raw and field_name.startswith("f_") else field_name
            value = getattr(accession, source_field, "")
            fallback_field = FINAL_REVIEW_FALLBACK_FIELDS.get(field_name)
            if fallback_field and value in ("", None):
                if is_raw and fallback_field.startswith("f_"):
                    fallback_field = fallback_field[2:]
                value = getattr(accession, fallback_field, "")
            if field_name == "f_Age_Display" and value in ("", None):
                value = getattr(accession, "Age" if is_raw else "f_Age", "")
            recommendation_items = _recommendation_items(value) if field_name == "f_ars_reco" else []
            field_rows.append({
                "name": field_name,
                "label": label,
                "value": value if value is not None else "",
                "is_recommendation": field_name == "f_ars_reco",
                "recommendation_items": recommendation_items,
                "comments": comments_by_field.get(field_name, []),
            })

        antibiotic_rows_by_code = {}
        antibiotic_entries = accession.antibiotic_entries.all() if is_raw else accession.final_entries.all()
        for entry in antibiotic_entries:
            site_result = antibiotic_result(
                "Site",
                entry.ab_Disk_value,
                entry.ab_Disk_enRIS,
                entry.ab_MIC_operand,
                entry.ab_MIC_value,
                entry.ab_MIC_enRIS,
            )
            ars_result = antibiotic_result(
                "Retest",
                entry.ab_Retest_DiskValue,
                entry.ab_Retest_Disk_enRIS,
                entry.ab_Retest_MIC_operand,
                entry.ab_Retest_MICValue,
                entry.ab_Retest_MIC_enRIS,
            )
            has_site_result = bool(entry.ab_Antibiotic and site_result["encoded_result"] != "-")
            has_arsrl_result = bool(entry.ab_Retest_Antibiotic and ars_result["encoded_result"] != "-")
            if has_site_result or has_arsrl_result:
                site_field_name = f"abx:{entry.pk}:site"
                arsrl_field_name = f"abx:{entry.pk}:arsrl"
                site_print_code = _canonical_print_code(
                    entry.ab_Abx_code or entry.ab_Abx,
                    whonet_to_print,
                    canonical_print_codes,
                )
                arsrl_print_code = _canonical_print_code(
                    entry.ab_Retest_Abx_code or entry.ab_Retest_Abx,
                    whonet_to_print,
                    canonical_print_codes,
                )
                print_code = arsrl_print_code or site_print_code
                site_code = entry.ab_Abx_code or entry.ab_Abx or ""
                arsrl_code = entry.ab_Retest_Abx_code or entry.ab_Retest_Abx or ""
                whonet_code = arsrl_code or site_code
                target_field_name = arsrl_field_name if has_arsrl_result else site_field_name
                target_value = ars_result["encoded_result"] if has_arsrl_result else site_result["encoded_result"]
                comments = (
                    comments_by_field.get(site_field_name, [])
                    + comments_by_field.get(arsrl_field_name, [])
                )
                row_key = print_code or site_print_code or arsrl_print_code or f"entry-{entry.pk}"
                row = {
                    "name": target_field_name,
                    "label": entry.ab_Retest_Antibiotic or entry.ab_Antibiotic,
                    "antibiotic_name": entry.ab_Retest_Antibiotic or entry.ab_Antibiotic,
                    "whonet_code": whonet_code,
                    "site_test_method": site_result["test_method"] if has_site_result else "-",
                    "site_whonet_code": site_code if has_site_result else "-",
                    "site_result": site_result["encoded_result"] if has_site_result else "-",
                    "arsrl_test_method": ars_result["test_method"] if has_arsrl_result else "-",
                    "arsrl_whonet_code": arsrl_code if has_arsrl_result else "-",
                    "arsrl_result": ars_result["encoded_result"] if has_arsrl_result else "-",
                    "value": target_value,
                    "comments": comments,
                    "print_code": print_code,
                }
                existing_row = antibiotic_rows_by_code.get(row_key)
                if existing_row:
                    if existing_row["site_result"] == "-" and row["site_result"] != "-":
                        existing_row["site_result"] = row["site_result"]
                    if existing_row["arsrl_result"] == "-" and row["arsrl_result"] != "-":
                        existing_row["arsrl_result"] = row["arsrl_result"]
                        existing_row["arsrl_test_method"] = row["arsrl_test_method"]
                        existing_row["arsrl_whonet_code"] = row["arsrl_whonet_code"]
                        existing_row["name"] = row["name"]
                        existing_row["value"] = row["value"]
                    if existing_row["site_test_method"] == "-" and row["site_test_method"] != "-":
                        existing_row["site_test_method"] = row["site_test_method"]
                    if existing_row["site_whonet_code"] == "-" and row["site_whonet_code"] != "-":
                        existing_row["site_whonet_code"] = row["site_whonet_code"]
                    if not existing_row.get("antibiotic_name") or existing_row["antibiotic_name"] == "-":
                        existing_row["antibiotic_name"] = row["antibiotic_name"]
                    if row["whonet_code"] not in {"", "-"}:
                        existing_row["whonet_code"] = row["whonet_code"]
                    existing_row["comments"].extend(
                        comment for comment in row["comments"]
                        if comment not in existing_row["comments"]
                    )
                else:
                    antibiotic_rows_by_code[row_key] = row

        ordered_codes = _aligned_antibiotic_codes(
            antibiotic_rows_by_code.keys(),
            antibiotic_rows_by_code.keys(),
            site_print_order,
            ars_print_order,
        )
        antibiotic_rows = []
        for code in ordered_codes:
            row = antibiotic_rows_by_code.pop(code, None)
            if row:
                antibiotic_rows.append(row)
        for code in sorted(antibiotic_rows_by_code):
            antibiotic_rows.append(antibiotic_rows_by_code[code])

        rows.append({
            "accession": accession,
            "accession_id": accession.id,
            "accession_no": getattr(accession, "AccessionNo" if is_raw else "f_AccessionNo", ""),
            "patient_name": " ".join(
                str(getattr(accession, field, "") or "").strip()
                for field in (
                    ("First_Name", "Last_Name") if is_raw else ("f_First_Name", "f_Last_Name")
                )
            ).strip(),
            "age_display": getattr(accession, "Age_Display" if is_raw else "f_Age_Display", "")
                or getattr(accession, "Age" if is_raw else "f_Age", ""),
            "sex": getattr(accession, "Sex" if is_raw else "f_Sex", ""),
            "site_code": getattr(accession, "SiteCode" if is_raw else "f_SiteCode", ""),
            "specimen_type": getattr(accession, "Spec_Type" if is_raw else "f_Spec_Type", ""),
            "organism": (
                getattr(accession, "ars_OrgName" if is_raw else "f_ars_OrgName", "")
                or getattr(accession, "Site_OrgName" if is_raw else "f_Site_OrgName", "")
                or getattr(accession, "ars_OrgCode" if is_raw else "f_ars_OrgCode", "")
                or getattr(accession, "Site_Org" if is_raw else "f_Site_Org", "")
            ),
            "specimen_date": getattr(accession, "Spec_Date" if is_raw else "f_Spec_Date", None),
            "is_raw": is_raw,
            "field_rows": field_rows,
            "antibiotic_rows": antibiotic_rows,
            "additional_antibiotic_comments": [
                comment
                for field_name, comments in comments_by_field.items()
                if field_name.startswith("abx-extra:")
                for comment in comments
            ],
            "general_comments": comments_by_field.get("", []),
        })
    return rows


def _posted_case_accession(request, case):
    raw_accession_id = request.POST.get("raw_accession_id")
    final_accession_id = request.POST.get("final_accession_id")
    if raw_accession_id:
        raw_accession = get_object_or_404(
            Referred_Data,
            pk=raw_accession_id,
            Batch_id=case.final_batch_id,
        )
        return raw_accession, None, raw_accession, raw_accession.id
    if final_accession_id:
        final_filters = {"pk": final_accession_id}
        if case.final_batch_id:
            final_filters["f_Batch_id"] = case.final_batch_id
        elif case.final_accession_id:
            final_filters["pk"] = case.final_accession_id
        final_accession = get_object_or_404(Final_Data, **final_filters)
        return final_accession, final_accession, None, final_accession.id
    return None, None, None, None


@login_required(login_url="login")
def verification_case_detail(request, case_id):
    case = get_object_or_404(
        _case_queryset(request).prefetch_related("comments"),
        pk=case_id,
    )
    roles = get_user_roles(request.user)
    can_write_field_comments = _can_write_case_field_comments(case, roles, request.user)

    if request.method == "POST":
        action = request.POST.get("action", "")

        if action == "general_note":
            audience = request.POST.get("audience", "all").strip() or "all"
            if audience not in GENERAL_NOTE_AUDIENCE_LABELS:
                audience = "all"
            note = request.POST.get("general_note", "").strip()
            if not note:
                messages.error(request, "General remarks cannot be blank.")
                return redirect("verification_case_detail", case_id=case.id)

            VerificationComment.objects.create(
                verification_case=case,
                field_name=f"{GENERAL_NOTE_FIELD_PREFIX}{audience}",
                field_label=f"General remark to {GENERAL_NOTE_AUDIENCE_LABELS[audience]}",
                comment=note,
                decision=VerificationComment.DECISION_NOTE,
                created_by=request.user,
            )
            messages.success(request, f"General remark saved for {GENERAL_NOTE_AUDIENCE_LABELS[audience]}.")
            return redirect("verification_case_detail", case_id=case.id)

        if action == "comment":
            if not can_write_field_comments:
                messages.error(request, "Field comments are locked while this case is with another review role. Use General Remarks unless the case is returned to you.")
                return redirect("verification_case_detail", case_id=case.id)
            accession, final_accession, raw_accession, accession_id = _posted_case_accession(request, case)

            decision = request.POST.get("decision") or VerificationComment.DECISION_NOTE
            field_name = request.POST.get("field_name", "").strip()
            field_label = request.POST.get("field_label", "").strip()
            field_value_snapshot = request.POST.get("field_value_snapshot", "").strip()

            if decision == VerificationComment.DECISION_APPROVED:
                comments_to_clear = VerificationComment.objects.filter(
                    verification_case=case,
                    final_accession_ref_sequence=final_accession,
                    raw_accession_ref_sequence=raw_accession,
                    field_name=field_name,
                )
                cleared_count, _ = comments_to_clear.delete()
                if cleared_count:
                    messages.success(request, f"Saved comment for {field_label or field_name or 'this field'} was cleared.")
                else:
                    messages.success(request, f"{field_label or field_name or 'Field'} marked approved.")
                return redirect(_case_detail_url(case.id, accession_id))

            comment = request.POST.get("comment", "").strip()
            if not comment:
                if decision == VerificationComment.DECISION_RESOLVED:
                    comment = "Resolved."
                else:
                    messages.error(request, "Comment cannot be blank.")
                    return redirect(_case_detail_url(case.id, accession_id))

            VerificationComment.objects.create(
                verification_case=case,
                final_accession_ref_sequence=final_accession,
                raw_accession_ref_sequence=raw_accession,
                field_name=field_name,
                field_label=field_label,
                field_value_snapshot=field_value_snapshot,
                comment=comment,
                decision=decision,
                created_by=request.user,
            )
            messages.success(request, "Comment saved.")
            return redirect(_case_detail_url(case.id, accession_id))

        if action == "antibiotic_comment":
            if not can_write_field_comments:
                messages.error(request, "Antibiotic comments are locked while this case is with another review role. Use General Remarks unless the case is returned to you.")
                return redirect("verification_case_detail", case_id=case.id)
            accession, final_accession, raw_accession, accession_id = _posted_case_accession(request, case)
            if accession is None:
                messages.error(request, "The accession could not be identified.")
                return redirect("verification_case_detail", case_id=case.id)
            antibiotic_code = request.POST.get("antibiotic_code", "").strip()
            test_method = request.POST.get("test_method", "").strip()
            arsrl_test_method = request.POST.get("arsrl_test_method", "").strip()
            site_value_note = request.POST.get("site_value_note", "").strip()
            value_note = request.POST.get("value_note", "").strip()
            decision = request.POST.get("decision") or VerificationComment.DECISION_NOTE
            comment = request.POST.get("comment", "").strip()

            if not antibiotic_code:
                messages.error(request, "Please choose an antibiotic.")
                return redirect(_case_detail_url(case.id, accession_id))
            if not test_method:
                messages.error(request, "Please choose a test method.")
                return redirect(_case_detail_url(case.id, accession_id))
            field_name = f"abx-extra:{antibiotic_code}:{test_method}"
            method_label = " / ".join(
                method for method in [test_method, arsrl_test_method]
                if method
            )
            field_label = f"Antibiotic Comment: {antibiotic_code} {method_label}"
            if decision == VerificationComment.DECISION_APPROVED:
                cleared_count, _ = VerificationComment.objects.filter(
                    verification_case=case,
                    final_accession_ref_sequence=final_accession,
                    raw_accession_ref_sequence=raw_accession,
                    field_name=field_name,
                ).delete()
                if cleared_count:
                    messages.success(request, f"Saved antibiotic comment for {antibiotic_code} {test_method} was cleared.")
                else:
                    messages.success(request, f"{antibiotic_code} {test_method} marked approved.")
                return redirect(_case_detail_url(case.id, accession_id))
            if not comment:
                messages.error(request, "Comment cannot be blank.")
                return redirect(_case_detail_url(case.id, accession_id))

            VerificationComment.objects.create(
                verification_case=case,
                final_accession_ref_sequence=final_accession,
                raw_accession_ref_sequence=raw_accession,
                field_name=field_name,
                field_label=field_label,
                field_value_snapshot=" | ".join(
                    part for part in [
                        f"Site: {site_value_note}" if site_value_note else "",
                        f"ARSRL {arsrl_test_method}: {value_note}" if value_note and arsrl_test_method else "",
                        f"ARSRL: {value_note}" if value_note and not arsrl_test_method else "",
                    ]
                    if part
                ),
                comment=comment,
                decision=decision,
                created_by=request.user,
            )
            messages.success(request, "Antibiotic comment saved.")
            return redirect(_case_detail_url(case.id, accession_id))

        if action in {
            "return_raw_encoding",
            "send_final_encoding",
            "return_final_encoding",
            "send_final_printing",
        }:
            roles = get_user_roles(request.user)
            if action in {"return_raw_encoding", "send_final_encoding"}:
                allowed = (
                    case.status == VerificationCase.STATUS_FOR_RAW_VERIFICATION
                    and (
                        ROLE_ADMIN in roles
                        or (
                            ROLE_VERIFIER in roles
                            and (
                                not case.assigned_verifier_id
                                or case.assigned_verifier_id == request.user.id
                            )
                        )
                    )
                )
                if not allowed:
                    messages.error(request, "Raw review actions are available only to Verifiers.")
                    return redirect("verification_case_detail", case_id=case.id)
                case.status = (
                    VerificationCase.STATUS_RETURNED_TO_ENCODER
                    if action == "return_raw_encoding"
                    else VerificationCase.STATUS_FOR_FINAL_ENCODING
                )
                case.verifier_decision_at = timezone.now()
                case.verifier_decision_by = request.user
            else:
                can_verifier_route = _can_route_final_review(case, roles, request.user)
                can_lab_route = _can_route_lab_final_review(case, roles, request.user)
                if action == "return_final_encoding" and not (can_verifier_route or can_lab_route):
                    messages.error(request, "This case cannot currently be returned for Final Encoding.")
                    return redirect("verification_case_detail", case_id=case.id)
                if action == "send_final_printing" and not can_lab_route:
                    messages.error(request, "Only the assigned Lab Manager can endorse this case for Final Printing.")
                    return redirect("verification_case_detail", case_id=case.id)
                if action == "return_final_encoding":
                    case.status = VerificationCase.STATUS_FOR_FINAL_ENCODING
                else:
                    head_staff = get_object_or_404(
                        arsStaff_Details.objects.filter(
                            Q(Is_Default_Head=True) | staff_role_q(ROLE_ADMIN),
                            User_Account__isnull=False,
                        ),
                        pk=request.POST.get("head_staff"),
                    )
                    case.status = VerificationCase.STATUS_FOR_FINAL_PRINTING
                    case.assigned_head = head_staff.User_Account
                    case.sent_to_head_at = timezone.now()
                    case.sent_to_head_by = request.user
                    _sync_case_signatories(case, head_staff=head_staff)
                if ROLE_LAB_MANAGER in roles:
                    case.lab_manager_decision_at = timezone.now()
                    case.lab_manager_decision_by = request.user
                else:
                    case.verifier_decision_at = timezone.now()
                    case.verifier_decision_by = request.user
            case.save()
            messages.success(request, f"Case updated to: {case.get_status_display()}.")
            return redirect("verification_case_detail", case_id=case.id)

        if action == "return_encoder":
            messages.error(request, "Verifier corrections must be returned to Checker.")
            return redirect("verification_case_detail", case_id=case.id)
        if action in {"lab_return_encoder", "lab_return_checker"}:
            messages.error(request, "Lab Manager returns must go back to the Verifier.")
            return redirect("verification_case_detail", case_id=case.id)
        if action in {"head_return_checker", "head_return_verifier"}:
            messages.error(request, "Head returns must go back to the Lab Manager.")
            return redirect("verification_case_detail", case_id=case.id)

        if action in {
            "approve",
            "return_checker",
            "send_lab_manager",
            "undo_send_lab_manager",
            "lab_approve",
            "lab_disapprove",
            "lab_return_verifier",
            "send_head",
            "withdraw_from_head",
            "head_approve",
            "head_disapprove",
            "head_return_lab",
            "undo_head_disapprove",
            "sign_head",
            "withdraw_from_ready",
            "release",
        }:
            roles = get_user_roles(request.user)
            is_assigned_head = case.assigned_head_id and case.assigned_head_id == request.user.id
            can_release_case = bool(ROLE_ADMIN in roles or ROLE_CHECKER in roles)
            # Final-review actions must never be used during Raw review, Draft,
            # signed/released review, or any unrelated workflow stage.
            if action in {"approve", "return_checker", "send_lab_manager"} and case.status not in {
                VerificationCase.STATUS_FOR_FINAL_VERIFICATION,
                VerificationCase.STATUS_RETURNED_TO_VERIFIER,
                VerificationCase.STATUS_RETURNED_TO_CHECKER,
            }:
                messages.error(request, "This action is only available during Final Verification review.")
                return redirect("verification_case_detail", case_id=case.id)
            if action in {"approve", "return_checker", "send_lab_manager"} and not _can_submit_verifier_actions(case, roles):
                messages.error(request, "Verifier actions are available only while the case is on the verifier side.")
                return redirect("verification_case_detail", case_id=case.id)
            if action == "undo_send_lab_manager" and not _can_undo_send_to_lab_manager(case, roles, request.user):
                messages.error(request, "Only the assigned Verifier or admin can undo sending this case to the Lab Manager.")
                return redirect("verification_case_detail", case_id=case.id)
            if action in {"lab_approve", "lab_disapprove", "lab_return_verifier", "send_head"} and not _can_submit_lab_manager_actions(case, roles, request.user):
                messages.error(request, "Lab Manager actions are available only to the assigned Lab Manager while the case is on the Lab Manager side.")
                return redirect("verification_case_detail", case_id=case.id)
            if action == "withdraw_from_head" and not _can_withdraw_from_head(case, roles, request.user):
                messages.error(request, "Only the assigned Lab Manager or admin can withdraw a case from Head review.")
                return redirect("verification_case_detail", case_id=case.id)
            if action in {"head_approve", "head_disapprove", "head_return_lab", "sign_head"} and not _can_submit_head_actions(case, roles, request.user):
                messages.error(request, "Head/Admin actions are available only while the case is on Head review.")
                return redirect("verification_case_detail", case_id=case.id)
            if action == "withdraw_from_ready" and not _can_withdraw_from_ready(case, roles, request.user):
                messages.error(request, "Only the assigned Head or admin can withdraw a case from Ready for Release.")
                return redirect("verification_case_detail", case_id=case.id)
            if action == "undo_head_disapprove" and not _can_undo_head_disapprove(case, roles, request.user):
                messages.error(request, "Only the assigned Head or admin can undo a head disapproval.")
                return redirect("verification_case_detail", case_id=case.id)
            if action == "release":
                if not can_release_case:
                    messages.error(request, "Only checkers or admins can release signed cases.")
                    return redirect("verification_case_detail", case_id=case.id)
                if case.status != VerificationCase.STATUS_SIGNED_BY_HEAD:
                    messages.error(request, "Only cases marked Ready for Release can be released.")
                    return redirect("verification_case_detail", case_id=case.id)

            if action == "approve":
                case.status = VerificationCase.STATUS_APPROVED_BY_VERIFIER
                case.verifier_decision_at = timezone.now()
                case.verifier_decision_by = request.user
            elif action == "return_checker":
                case.status = VerificationCase.STATUS_RETURNED_TO_CHECKER
                case.verifier_decision_at = timezone.now()
                case.verifier_decision_by = request.user
            elif action == "send_lab_manager":
                lab_manager_staff = get_object_or_404(
                    _staff_for_role(ROLE_LAB_MANAGER, ROLE_ADMIN),
                    pk=request.POST.get("lab_manager_staff"),
                )
                case.status = VerificationCase.STATUS_FOR_LAB_MANAGER
                case.assigned_lab_manager = lab_manager_staff.User_Account
                case.sent_to_lab_manager_at = timezone.now()
                case.sent_to_lab_manager_by = request.user
                _sync_case_signatories(case, lab_manager_staff=lab_manager_staff)
            elif action == "undo_send_lab_manager":
                case.status = VerificationCase.STATUS_FOR_VERIFICATION
                case.assigned_lab_manager = None
                case.sent_to_lab_manager_at = None
                case.sent_to_lab_manager_by = None
                case.lab_manager_decision_at = None
                case.lab_manager_decision_by = None
            elif action == "lab_approve":
                case.status = VerificationCase.STATUS_APPROVED_BY_LAB_MANAGER
                case.lab_manager_decision_at = timezone.now()
                case.lab_manager_decision_by = request.user
            elif action == "lab_disapprove":
                case.status = VerificationCase.STATUS_DISAPPROVED_BY_LAB_MANAGER
                case.lab_manager_decision_at = timezone.now()
                case.lab_manager_decision_by = request.user
            elif action == "lab_return_verifier":
                case.status = VerificationCase.STATUS_FOR_FINAL_ENCODING
                case.lab_manager_decision_at = timezone.now()
                case.lab_manager_decision_by = request.user
            elif action == "send_head":
                head_staff = get_object_or_404(
                    arsStaff_Details.objects.filter(
                        Q(Is_Default_Head=True) | staff_role_q(ROLE_ADMIN),
                        User_Account__isnull=False,
                    ),
                    pk=request.POST.get("head_staff"),
                )
                case.status = VerificationCase.STATUS_FOR_FINAL_PRINTING
                case.assigned_head = head_staff.User_Account
                case.sent_to_head_at = timezone.now()
                case.sent_to_head_by = request.user
                _sync_case_signatories(case, head_staff=head_staff)
            elif action == "withdraw_from_head":
                case.status = VerificationCase.STATUS_FOR_LAB_MANAGER
                case.lab_manager_decision_at = timezone.now()
                case.lab_manager_decision_by = request.user
            elif action == "head_approve":
                case.status = VerificationCase.STATUS_SIGNED_BY_HEAD
                case.head_decision_at = timezone.now()
                case.head_decision_by = request.user
                case.signed_by_head_at = timezone.now()
                case.signed_by_head = request.user
                case.mark_qr_generated()
            elif action == "head_disapprove":
                case.status = VerificationCase.STATUS_DISAPPROVED_BY_HEAD
                case.head_decision_at = timezone.now()
                case.head_decision_by = request.user
            elif action == "head_return_lab":
                case.status = VerificationCase.STATUS_FOR_LAB_MANAGER
                case.head_decision_at = timezone.now()
                case.head_decision_by = request.user
            elif action == "sign_head":
                case.status = VerificationCase.STATUS_SIGNED_BY_HEAD
                case.signed_by_head_at = timezone.now()
                case.signed_by_head = request.user
                case.mark_qr_generated()
            elif action == "withdraw_from_ready":
                case.status = VerificationCase.STATUS_FOR_FINAL_PRINTING
                case.qr_generated_at = None
                case.head_decision_at = timezone.now()
                case.head_decision_by = request.user
            elif action == "undo_head_disapprove":
                case.status = VerificationCase.STATUS_FOR_HEAD
                case.head_decision_at = timezone.now()
                case.head_decision_by = request.user
            elif action == "release":
                case.status = VerificationCase.STATUS_RELEASED
                case.released_at = timezone.now()
                case.mark_qr_generated()

            case.save()
            messages.success(request, f"Case updated to: {case.get_status_display()}.")
            return redirect("verification_case_detail", case_id=case.id)

    requested_accession_id = request.GET.get("accession_id")
    try:
        requested_accession_id = int(requested_accession_id) if requested_accession_id else None
    except (TypeError, ValueError):
        requested_accession_id = None
    selected_accession_id, case_accession_nav = _case_accession_nav(case, requested_accession_id)
    is_assigned_head = case.assigned_head_id and case.assigned_head_id == request.user.id
    can_delete_case_comments = bool(roles.intersection({ROLE_ADMIN, ROLE_VERIFIER, ROLE_LAB_MANAGER}) or is_assigned_head)
    can_review_as_head = bool(ROLE_ADMIN in roles or is_assigned_head)
    can_submit_head_actions = _can_submit_head_actions(case, roles, request.user)
    can_withdraw_from_ready = _can_withdraw_from_ready(case, roles, request.user)
    can_undo_head_disapprove = _can_undo_head_disapprove(case, roles, request.user)
    can_submit_raw_review = bool(
        case.status == VerificationCase.STATUS_FOR_RAW_VERIFICATION
        and _can_submit_verifier_actions(case, roles)
    )
    can_route_final_review = _can_route_final_review(case, roles, request.user)
    can_route_lab_final_review = _can_route_lab_final_review(case, roles, request.user)
    can_submit_verifier_actions = bool(
        _can_submit_verifier_actions(case, roles)
        and case.status != VerificationCase.STATUS_FOR_RAW_VERIFICATION
        and case.status != VerificationCase.STATUS_FOR_FINAL_VERIFICATION
    )
    can_submit_lab_manager_actions = _can_submit_lab_manager_actions(case, roles, request.user)
    can_undo_send_lab_manager = _can_undo_send_to_lab_manager(case, roles, request.user)
    can_withdraw_from_head = _can_withdraw_from_head(case, roles, request.user)
    general_case_notes = (
        VerificationComment.objects
        .filter(verification_case=case, field_name__startswith=GENERAL_NOTE_FIELD_PREFIX)
        .select_related("created_by")
        .order_by("-created_at")[:8]
    )

    context = {
        "case": case,
        "review_rows": _review_rows_for_case(case, selected_accession_id),
        "case_accession_nav": case_accession_nav,
        "comment_decisions": VerificationComment.DECISION_CHOICES,
        "antibiotic_comment_choices": _antibiotic_comment_choices(),
        "general_note_audience_choices": GENERAL_NOTE_AUDIENCE_CHOICES,
        "general_case_notes": general_case_notes,
        "can_write_field_comments": can_write_field_comments,
        "can_submit_verifier_actions": can_submit_verifier_actions,
        "can_submit_raw_review": can_submit_raw_review,
        "can_route_final_review": can_route_final_review,
        "can_route_lab_final_review": can_route_lab_final_review,
        "is_raw_stage": case.status in {
            VerificationCase.STATUS_FOR_RAW_VERIFICATION,
            VerificationCase.STATUS_RETURNED_TO_ENCODER,
        },
        "can_submit_lab_manager_actions": can_submit_lab_manager_actions,
        "can_undo_send_lab_manager": can_undo_send_lab_manager,
        "can_withdraw_from_head": can_withdraw_from_head,
        "can_delete_case_comments": can_delete_case_comments,
        "can_review_as_head": can_review_as_head,
        "can_submit_head_actions": can_submit_head_actions,
        "can_withdraw_from_ready": can_withdraw_from_ready,
        "can_undo_head_disapprove": can_undo_head_disapprove,
        "tat_summary": _tat_summary_for_case(case),
        "lab_manager_staff": _staff_for_role(ROLE_LAB_MANAGER, ROLE_ADMIN),
        "head_staff": arsStaff_Details.objects.filter(
            Q(Is_Default_Head=True) | staff_role_q(ROLE_ADMIN),
            User_Account__isnull=False,
        ).select_related("User_Account").distinct().order_by("-Is_Default_Head", "Staff_Name"),
    }
    return render(request, "verification/case_detail.html", context)


@login_required(login_url="login")
@transaction.atomic
def bulk_case_comment_action(request, case_id):
    if request.method != "POST":
        return redirect("verification_case_detail", case_id=case_id)

    case = get_object_or_404(_case_queryset(request), pk=case_id)
    action = request.POST.get("comment_action", "")
    note = request.POST.get("resolution_note", "").strip()
    updated = _bulk_comment_update(request, case.comments.all(), action, note)
    if updated:
        messages.success(request, f"{updated} comment(s) updated.")
    return redirect("verification_case_detail", case_id=case.id)


@login_required(login_url="login")
@transaction.atomic
def delete_verification_comment(request, case_id, comment_id):
    if request.method != "POST":
        return redirect("verification_case_detail", case_id=case_id)

    case = get_object_or_404(_case_queryset(request), pk=case_id)
    accession_id = request.POST.get("final_accession_id") or request.GET.get("accession_id")
    roles = get_user_roles(request.user)
    comment = get_object_or_404(VerificationComment, pk=comment_id, verification_case=case)
    can_delete_comment = bool(roles.intersection({ROLE_ADMIN, ROLE_VERIFIER, ROLE_LAB_MANAGER}))
    if case.assigned_head_id and case.assigned_head_id == request.user.id:
        can_delete_comment = True
    if not can_delete_comment:
        messages.error(request, "Only verification reviewers can delete saved comments.")
        return redirect(_case_detail_url(case.id, accession_id))
    if (
        not comment.field_name.startswith(GENERAL_NOTE_FIELD_PREFIX)
        and not _can_write_case_field_comments(case, roles, request.user)
    ):
        messages.error(request, "Field comments are locked while this case is with another review role. Use General Remarks unless the case is returned to you.")
        return redirect(_case_detail_url(case.id, accession_id))

    label = comment.field_label or comment.field_name or "comment"
    comment.delete()
    messages.success(request, f"Deleted saved comment for {label}.")
    return redirect(_case_detail_url(case.id, accession_id))


@login_required(login_url="login")
@transaction.atomic
def bulk_final_comment_action(request, final_id):
    if request.method != "POST":
        return redirect("edit_final_data", id=final_id)

    final_record = get_object_or_404(Final_Data, pk=final_id)
    action = request.POST.get("comment_action", "")
    note = request.POST.get("resolution_note", "").strip()
    comments = VerificationComment.objects.filter(final_accession_ref_sequence=final_record)
    updated = _bulk_comment_update(request, comments, action, note)
    if updated:
        messages.success(request, f"{updated} comment(s) updated for {final_record.f_AccessionNo}.")
    return redirect("edit_final_data", id=final_record.id)


@login_required(login_url="login")
@transaction.atomic
def final_comment_action(request, final_id, comment_id):
    if request.method != "POST":
        return redirect("edit_final_data", id=final_id)

    final_record = get_object_or_404(Final_Data, pk=final_id)
    comment = get_object_or_404(
        VerificationComment,
        pk=comment_id,
        final_accession_ref_sequence=final_record,
    )
    action = request.POST.get("comment_action", "")
    note = request.POST.get("resolution_note", "").strip()
    updated = _bulk_comment_update(
        request,
        VerificationComment.objects.filter(pk=comment.pk),
        action,
        note,
    )
    if updated:
        label = comment.field_label or comment.field_name or "comment"
        messages.success(request, f"{label} comment updated.")
    return redirect("edit_final_data", id=final_record.id)


@login_required(login_url="login")
@transaction.atomic
def raw_comment_action(request, raw_id, comment_id):
    if request.method != "POST":
        return redirect("edit_data", id=raw_id)

    raw_record = get_object_or_404(Referred_Data, pk=raw_id)
    comment = get_object_or_404(
        VerificationComment,
        pk=comment_id,
        raw_accession_ref_sequence=raw_record,
    )
    action = request.POST.get("comment_action", "")
    note = request.POST.get("resolution_note", "").strip()
    updated = _bulk_comment_update(
        request,
        VerificationComment.objects.filter(pk=comment.pk),
        action,
        note,
    )
    if updated:
        label = comment.field_label or comment.field_name or "comment"
        messages.success(request, f"{label} comment updated.")
    return redirect("edit_data", id=raw_record.id)
