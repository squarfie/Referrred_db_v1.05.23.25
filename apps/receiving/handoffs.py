"""Audited batch handoffs. Endorsement never changes checking or approval state."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.home.permissions import (
    ROLE_ADMIN, ROLE_CHECKER, ROLE_ENCODER, ROLE_LAB_ENCODER,
    ROLE_LAB_MANAGER, ROLE_SECRETARIAT, ROLE_VERIFIER, get_user_roles,
    get_staff_profile,
)
from .models import BatchCustody, BatchCustodyEvent

ROLE_HEAD = "Head"
DESTINATION_ROLES = (
    ROLE_SECRETARIAT, ROLE_LAB_ENCODER, ROLE_ENCODER, ROLE_CHECKER,
    ROLE_VERIFIER, ROLE_LAB_MANAGER, ROLE_HEAD,
)


def custody_roles(user):
    """Recognize the existing role resolver plus the explicit Head role."""
    roles = set(get_user_roles(user))
    if not user or not user.is_authenticated:
        return roles
    if user.groups.filter(name__iexact=ROLE_HEAD).exists():
        roles.add(ROLE_HEAD)
    profile = get_staff_profile(user)
    if profile and ROLE_HEAD.lower() in {
        part.strip().lower()
        for part in str(profile.Staff_Role or "").replace(",", "|").split("|")
    }:
        roles.add(ROLE_HEAD)
    return roles


def _authorize(user, role):
    if not user or not user.is_authenticated:
        raise PermissionDenied("You must sign in.")
    if ROLE_ADMIN not in custody_roles(user) and role not in custody_roles(user):
        raise PermissionDenied("You are not authorized to act for this role.")


def _record(custody, action, actor, from_role, to_role="", remarks=""):
    return BatchCustodyEvent.objects.create(
        custody=custody, action=action, performed_by=actor,
        from_role=from_role, to_role=to_role, remarks=remarks,
    )


@transaction.atomic
def initialize_custody(*, batch, user, initial_role):
    """Explicitly initialize an existing generated batch; never backfill by guess."""
    if initial_role not in DESTINATION_ROLES:
        raise ValidationError("Invalid initial custody role.")
    _authorize(user, initial_role)
    custody, created = BatchCustody.objects.get_or_create(
        batch=batch, defaults={"current_role": initial_role, "confirmed_by": user, "confirmed_at": timezone.now()}
    )
    if created:
        _record(custody, BatchCustodyEvent.Action.INITIALIZED, user, initial_role)
    return custody


@transaction.atomic
def endorse_batch(*, batch_id, user, destination_role, remarks=""):
    if destination_role not in DESTINATION_ROLES:
        raise ValidationError("Select an authorized destination role.")
    custody = BatchCustody.objects.select_for_update().get(batch_id=batch_id)
    _authorize(user, custody.current_role)
    if custody.pending_role:
        raise ValidationError("This endorsement is still awaiting confirmation.")
    if destination_role == custody.current_role:
        raise ValidationError("Select a different destination role.")
    if len(remarks) > 1000:
        raise ValidationError("The endorsement reason must be 1000 characters or fewer.")
    if destination_role not in (ROLE_LAB_ENCODER, ROLE_ENCODER, ROLE_CHECKER, ROLE_VERIFIER, ROLE_LAB_MANAGER, ROLE_HEAD) and not remarks.strip():
        raise ValidationError("A reason is required for this destination.")
    now = timezone.now()
    origin = custody.current_role
    custody.pending_role = destination_role
    custody.endorsed_by = user
    custody.endorsed_at = now
    custody.save(update_fields=("pending_role", "endorsed_by", "endorsed_at", "updated_at"))
    _record(custody, BatchCustodyEvent.Action.ENDORSED, user, origin, destination_role, remarks.strip())
    return custody


@transaction.atomic
def confirm_batch_receipt(*, batch_id, user):
    custody = BatchCustody.objects.select_for_update().get(batch_id=batch_id)
    if not custody.pending_role:
        raise ValidationError("No endorsement is awaiting receipt.")
    _authorize(user, custody.pending_role)
    if custody.endorsed_by_id == user.pk and ROLE_ADMIN not in custody_roles(user):
        raise PermissionDenied("The sender cannot confirm their own endorsement.")
    previous = custody.current_role
    destination = custody.pending_role
    custody.current_role = destination
    custody.pending_role = ""
    custody.confirmed_by = user
    custody.confirmed_at = timezone.now()
    custody.save(update_fields=("current_role", "pending_role", "confirmed_by", "confirmed_at", "updated_at"))
    _record(custody, BatchCustodyEvent.Action.CONFIRMED, user, previous, destination)
    return custody


@transaction.atomic
def undo_batch_endorsement(*, batch_id, user, remarks=""):
    custody = BatchCustody.objects.select_for_update().get(batch_id=batch_id)
    if not custody.pending_role:
        raise ValidationError("Only an unconfirmed endorsement can be recalled.")
    if ROLE_ADMIN not in custody_roles(user) and custody.endorsed_by_id != user.pk:
        raise PermissionDenied("Only the sender can recall this endorsement.")
    destination = custody.pending_role
    custody.pending_role = ""
    custody.endorsed_by = None
    custody.endorsed_at = None
    custody.save(update_fields=("pending_role", "endorsed_by", "endorsed_at", "updated_at"))
    _record(custody, BatchCustodyEvent.Action.UNDONE, user, custody.current_role, destination, remarks.strip()[:1000])
    return custody
