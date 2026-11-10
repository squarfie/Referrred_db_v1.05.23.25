from functools import wraps

from django.contrib import messages
from django.db.models import Q
from django.shortcuts import redirect


ROLE_ADMIN = "Admin"
ROLE_VERIFIER = "Verifier"
ROLE_ENCODER = "DMU Encoder"
ROLE_LAB_ENCODER = "LAB Encoder"
ROLE_CHECKER = "Checker"
ROLE_LAB_MANAGER = "Manager"
LEGACY_ROLE_ENCODER = "Encoder"
LEGACY_ROLE_LAB_MANAGER = "Lab Manager"

WRITE_ROLES = {ROLE_ADMIN, ROLE_CHECKER, ROLE_ENCODER}
WGS_WRITE_ROLES = {ROLE_ADMIN, ROLE_CHECKER, ROLE_LAB_ENCODER}
SETTINGS_ROLES = {ROLE_ADMIN, ROLE_CHECKER}
VERIFICATION_OUTPUT_READY_STATUSES = {"signed_by_head", "released"}
VERIFICATION_OUTPUT_INACTIVE_STATUSES = {"withdrawn_by_checker"}
ROLE_BY_NAME = {
    ROLE_ADMIN.lower(): ROLE_ADMIN,
    ROLE_VERIFIER.lower(): ROLE_VERIFIER,
    ROLE_ENCODER.lower(): ROLE_ENCODER,
    ROLE_LAB_ENCODER.lower(): ROLE_LAB_ENCODER,
    ROLE_CHECKER.lower(): ROLE_CHECKER,
    ROLE_LAB_MANAGER.lower(): ROLE_LAB_MANAGER,
    LEGACY_ROLE_ENCODER.lower(): ROLE_ENCODER,
    LEGACY_ROLE_LAB_MANAGER.lower(): ROLE_LAB_MANAGER,
    "dmu encoder": ROLE_ENCODER,
    "dmu_encoder": ROLE_ENCODER,
    "dmuencoder": ROLE_ENCODER,
    "lab encoder": ROLE_LAB_ENCODER,
    "lab_encoder": ROLE_LAB_ENCODER,
    "labencoder": ROLE_LAB_ENCODER,
    "labmanager": ROLE_LAB_MANAGER,
    "lab manager": ROLE_LAB_MANAGER,
    "lab mgr": ROLE_LAB_MANAGER,
    "laboratory manager": ROLE_LAB_MANAGER,
}


def normalize_role(value):
    return ROLE_BY_NAME.get((value or "").strip().lower(), "")


def normalize_roles(value):
    roles = set()
    for part in str(value or "").replace(",", "|").replace(";", "|").split("|"):
        role = normalize_role(part)
        if role:
            roles.add(role)
    return roles


def get_staff_profile(user):
    if not user or not user.is_authenticated:
        return None

    try:
        return user.arsp_staff_profile
    except Exception:
        pass

    from apps.home.models import arsStaff_Details

    candidates = Q()
    if user.email:
        candidates |= Q(Staff_EmailAdd__iexact=user.email)
    if user.get_full_name():
        candidates |= Q(Staff_Name__iexact=user.get_full_name())
    if user.username:
        candidates |= Q(Staff_Name__iexact=user.username)

    if not candidates:
        return None

    return arsStaff_Details.objects.filter(candidates).first()


def get_user_roles(user):
    if not user or not user.is_authenticated:
        return set()
    if user.is_superuser:
        return {ROLE_ADMIN}

    role_group_names = [
        ROLE_ADMIN,
        ROLE_VERIFIER,
        ROLE_ENCODER,
        ROLE_LAB_ENCODER,
        ROLE_CHECKER,
        ROLE_LAB_MANAGER,
        LEGACY_ROLE_ENCODER,
    ]
    roles = set(user.groups.filter(name__in=role_group_names).values_list("name", flat=True))
    roles = {normalize_role(role) for role in roles}
    roles.discard("")

    profile = get_staff_profile(user)
    if profile:
        roles.update(normalize_roles(profile.Staff_Role))

    if user.is_staff:
        roles.add(ROLE_ADMIN)

    return roles


def get_user_role(user):
    roles = get_user_roles(user)
    for role in (ROLE_ADMIN, ROLE_CHECKER, ROLE_ENCODER, ROLE_LAB_ENCODER, ROLE_VERIFIER, ROLE_LAB_MANAGER):
        if role in roles:
            return role
    return ""


def role_flags(user):
    roles = get_user_roles(user)
    role = get_user_role(user)
    review_roles = {ROLE_VERIFIER, ROLE_LAB_MANAGER}
    operational_roles = {ROLE_ADMIN, ROLE_CHECKER, ROLE_ENCODER, ROLE_LAB_ENCODER}
    is_review_only = bool(roles & review_roles) and not bool(roles & operational_roles)
    return {
        "role": role or ROLE_CHECKER,
        "roles": sorted(roles),
        "is_admin": ROLE_ADMIN in roles,
        "is_verifier": ROLE_VERIFIER in roles,
        "is_encoder": ROLE_ENCODER in roles,
        "is_dmu_encoder": ROLE_ENCODER in roles,
        "is_lab_encoder": ROLE_LAB_ENCODER in roles,
        "is_checker": ROLE_CHECKER in roles or not roles,
        "is_lab_manager": ROLE_LAB_MANAGER in roles,
        "is_review_only": is_review_only,
        "can_send_to_verification": bool(roles & {ROLE_ADMIN, ROLE_CHECKER, ROLE_ENCODER}),
        "can_send_raw_to_verification": bool(roles & {ROLE_ADMIN, ROLE_ENCODER, ROLE_CHECKER}),
        "can_send_final_to_verification": bool(roles & {ROLE_ADMIN, ROLE_CHECKER}),
        "can_view_correction_badges": ROLE_CHECKER in roles or not roles,
        "can_print_final_pdf": bool(roles & {ROLE_ADMIN, ROLE_CHECKER, ROLE_ENCODER, ROLE_VERIFIER, ROLE_LAB_MANAGER}),
        "can_print_raw_pdf": bool(roles & {ROLE_ADMIN, ROLE_CHECKER, ROLE_ENCODER, ROLE_VERIFIER, ROLE_LAB_MANAGER}),
        "can_print_concordance": bool(roles & {ROLE_ADMIN, ROLE_CHECKER, ROLE_ENCODER, ROLE_VERIFIER, ROLE_LAB_MANAGER}),
        "are_disabled": bool(roles & {ROLE_CHECKER, ROLE_ENCODER}),
        "can_view_verification": bool(roles & {ROLE_ADMIN, ROLE_VERIFIER, ROLE_LAB_MANAGER}),
        "can_read": bool(roles) or (user and user.is_authenticated),
        "can_create": bool(roles & WRITE_ROLES),
        "can_update": bool(roles & WRITE_ROLES),
        "can_delete": bool(roles & WRITE_ROLES),
        "can_wgs_create": bool(roles & WGS_WRITE_ROLES),
        "can_wgs_update": bool(roles & WGS_WRITE_ROLES),
        "can_wgs_delete": bool(roles & WGS_WRITE_ROLES),
        "can_manage_settings": bool(roles & SETTINGS_ROLES),
        "can_manage_staff": ROLE_ADMIN in roles,
        "can_add_staff": bool(roles & {ROLE_ADMIN, ROLE_CHECKER}),
        "can_edit_staff_roles": ROLE_ADMIN in roles,
        "can_view_all": bool(roles & {ROLE_ADMIN, ROLE_CHECKER, ROLE_VERIFIER, ROLE_LAB_MANAGER}),
        "can_view_wgs": bool(roles & {ROLE_ADMIN, ROLE_CHECKER, ROLE_VERIFIER, ROLE_LAB_MANAGER, ROLE_LAB_ENCODER}) or bool(user and user.is_authenticated),
    }


def verification_case_blocks_operational_outputs(case):
    return bool(
        case
        and case.status not in VERIFICATION_OUTPUT_READY_STATUSES
        and case.status not in VERIFICATION_OUTPUT_INACTIVE_STATUSES
    )


def verification_case_blocks_deletion(case):
    return bool(
        case
        and case.status not in VERIFICATION_OUTPUT_INACTIVE_STATUSES
    )


def verification_case_blocks_dmu_outputs(case):
    return verification_case_blocks_operational_outputs(case)


def can_manage_batch(user, batch):
    roles = get_user_roles(user)
    if roles & {ROLE_ADMIN, ROLE_CHECKER}:
        return True
    if ROLE_ENCODER in roles:
        return bool(batch and batch.created_by_id == user.id)
    return False


def role_required(*allowed_roles):
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            roles = get_user_roles(request.user)
            if not roles.intersection(allowed_roles):
                messages.error(request, "You do not have permission to perform that action.")
                return redirect("home")
            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator
