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
admin_receiving_delete_required = _permission_required(can_permanently_delete_receipt)


# Compatibility imports for callers of the original receiving policy module.
from apps.laboratory.permissions import (
    can_access_laboratory_worklist,
    can_confirm_lab_receipt,
    can_manage_biobank,
    can_manage_id_testing,
    can_read_laboratory_receiving,
    can_review_id_results,
    lab_receiving_read_required,
    lab_receiving_required,
    lab_review_required,
    lab_staff_required,
    lab_worklist_required,
)
