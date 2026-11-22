"""Laboratory role policy and view access decorators."""

from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect

from apps.home.permissions import ROLE_ADMIN, ROLE_LAB_ENCODER, ROLE_LAB_STAFF, get_user_roles


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


def can_confirm_lab_receipt(user):
    return bool(get_user_roles(user) & {ROLE_ADMIN, ROLE_LAB_ENCODER})


lab_receiving_required = _permission_required(can_confirm_lab_receipt)


def can_manage_id_testing(user):
    return bool(get_user_roles(user) & {ROLE_ADMIN, ROLE_LAB_ENCODER, ROLE_LAB_STAFF})


def can_review_id_results(user):
    return bool(get_user_roles(user) & {ROLE_ADMIN, ROLE_LAB_ENCODER})


def can_manage_biobank(user):
    return bool(get_user_roles(user) & {ROLE_ADMIN, ROLE_LAB_STAFF, ROLE_LAB_ENCODER})


def can_access_laboratory_worklist(user):
    return bool(get_user_roles(user) & {ROLE_ADMIN, ROLE_LAB_STAFF, ROLE_LAB_ENCODER})


def can_access_main_lab(user):
    """Main Laboratory is a distinct workstation, not an extension of Bench A."""
    return bool(get_user_roles(user) & {ROLE_ADMIN, ROLE_LAB_STAFF})


main_lab_required = _permission_required(can_access_main_lab)


lab_staff_required = _permission_required(can_manage_id_testing)


lab_review_required = _permission_required(can_review_id_results)


lab_worklist_required = _permission_required(can_access_laboratory_worklist)


def can_read_laboratory_receiving(user):
    """Bench A queue and custody pages are separate from Main Lab ID work."""
    return bool(get_user_roles(user) & {ROLE_ADMIN, ROLE_LAB_ENCODER})


lab_receiving_read_required = _permission_required(can_read_laboratory_receiving)
