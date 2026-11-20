from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect

from apps.home.permissions import (
    ROLE_ADMIN,
    ROLE_LAB_ENCODER,
    ROLE_LAB_STAFF,
    ROLE_SECRETARIAT,
    get_user_roles,
)


def can_read_receiving(user):
    return bool(get_user_roles(user) & {ROLE_ADMIN, ROLE_SECRETARIAT, ROLE_LAB_ENCODER, ROLE_LAB_STAFF})


def can_manage_secretariat_receiving(user):
    return bool(get_user_roles(user) & {ROLE_ADMIN, ROLE_SECRETARIAT})


def can_confirm_lab_receipt(user):
    return bool(get_user_roles(user) & {ROLE_ADMIN, ROLE_LAB_ENCODER})


def can_permanently_delete_receipt(user):
    return bool(get_user_roles(user) & {ROLE_ADMIN, ROLE_SECRETARIAT})


def _permission_required(checker):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            if not checker(request.user):
                messages.error(request, "You do not have permission to perform that action.")
                return redirect("home")
            return view_func(request, *args, **kwargs)

        return wrapped

    return decorator


receiving_read_required = _permission_required(can_read_receiving)
secretariat_receiving_required = _permission_required(can_manage_secretariat_receiving)
lab_receiving_required = _permission_required(can_confirm_lab_receipt)
admin_receiving_delete_required = _permission_required(can_permanently_delete_receipt)


def can_manage_id_testing(user):
    return bool(get_user_roles(user) & {ROLE_ADMIN, ROLE_LAB_STAFF})


def can_review_id_results(user):
    return bool(get_user_roles(user) & {ROLE_ADMIN, ROLE_LAB_ENCODER})


def can_manage_biobank(user):
    return bool(get_user_roles(user) & {ROLE_ADMIN, ROLE_LAB_STAFF, ROLE_LAB_ENCODER})


def can_access_laboratory_worklist(user):
    return bool(get_user_roles(user) & {ROLE_ADMIN, ROLE_LAB_STAFF, ROLE_LAB_ENCODER})


lab_staff_required = _permission_required(can_manage_id_testing)
lab_review_required = _permission_required(can_review_id_results)
lab_worklist_required = _permission_required(can_access_laboratory_worklist)


def can_read_laboratory_receiving(user):
    return bool(get_user_roles(user) & {ROLE_ADMIN, ROLE_LAB_ENCODER, ROLE_LAB_STAFF})


lab_receiving_read_required = _permission_required(can_read_laboratory_receiving)
