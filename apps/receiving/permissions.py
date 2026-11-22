from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect

from apps.home.permissions import (
    ROLE_ADMIN,
    ROLE_SECRETARIAT,
    get_user_roles,
)


def can_read_receiving(user):
    """Shared custody records are visible to every signed-in account."""
    return bool(user and user.is_authenticated)


def can_manage_secretariat_receiving(user):
    """All authenticated roles may record and endorse initial courier custody."""
    return bool(user and user.is_authenticated)


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
