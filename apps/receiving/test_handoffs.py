"""Regression tests for unified, role-scoped batch custody.

The receiving service is independent of laboratory validation and verification approvals.
"""
from datetime import date

from django.contrib.auth.models import Group, User
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.home.models import Batch_Table
from apps.home.permissions import ROLE_CHECKER, ROLE_ENCODER, ROLE_LAB_ENCODER, ROLE_VERIFIER
from apps.receiving.handoffs import (
    confirm_batch_receipt, endorse_batch, initialize_custody, undo_batch_endorsement,
)
from apps.receiving.models import BatchCustody, BatchCustodyEvent


class UnifiedReceivingHandoffTests(TestCase):
    def setUp(self):
        self.lab = self.make_user("lab-handoff", ROLE_LAB_ENCODER)
        self.dmu = self.make_user("dmu-handoff", ROLE_ENCODER)
        self.checker = self.make_user("checker-handoff", ROLE_CHECKER)
        self.verifier = self.make_user("verifier-handoff", ROLE_VERIFIER)
        self.head = self.make_user("head-handoff", "Head")
        self.batch = Batch_Table.objects.create(
            bat_Batch_Code="HND_11252026_1.1_0001-0002",
            bat_SiteCode="HND",
            bat_Referral_Date=date(2026, 11, 25),
            created_by=self.lab,
        )

    @staticmethod
    def make_user(username, role):
        user = User.objects.create_user(username=username, password="test-pass")
        user.groups.add(Group.objects.get_or_create(name=role)[0])
        return user

    def test_normal_handoff_requires_receiver_confirmation_and_keeps_audit(self):
        custody = initialize_custody(batch=self.batch, user=self.lab, initial_role=ROLE_LAB_ENCODER)
        self.assertEqual(custody.events.count(), 1)
        endorse_batch(batch_id=self.batch.pk, user=self.lab, destination_role=ROLE_ENCODER)
        custody.refresh_from_db()
        self.assertEqual(custody.current_role, ROLE_LAB_ENCODER)
        self.assertEqual(custody.pending_role, ROLE_ENCODER)

        with self.assertRaises(PermissionDenied):
            confirm_batch_receipt(batch_id=self.batch.pk, user=self.checker)

        confirm_batch_receipt(batch_id=self.batch.pk, user=self.dmu)
        custody.refresh_from_db()
        self.assertEqual(custody.current_role, ROLE_ENCODER)
        self.assertEqual(custody.pending_role, "")
        self.assertEqual(
            list(custody.events.values_list("action", flat=True)),
            ["initialized", "endorsed", "confirmed"],
        )

    def test_undo_is_available_only_before_confirmation(self):
        initialize_custody(batch=self.batch, user=self.lab, initial_role=ROLE_LAB_ENCODER)
        endorse_batch(batch_id=self.batch.pk, user=self.lab, destination_role=ROLE_ENCODER)
        with self.assertRaises(PermissionDenied):
            undo_batch_endorsement(batch_id=self.batch.pk, user=self.checker)

        undo_batch_endorsement(batch_id=self.batch.pk, user=self.lab)
        custody = BatchCustody.objects.get(batch=self.batch)
        self.assertEqual(custody.current_role, ROLE_LAB_ENCODER)
        self.assertEqual(custody.pending_role, "")
        self.assertEqual(custody.events.last().action, BatchCustodyEvent.Action.UNDONE)

        endorse_batch(batch_id=self.batch.pk, user=self.lab, destination_role=ROLE_ENCODER)
        confirm_batch_receipt(batch_id=self.batch.pk, user=self.dmu)
        with self.assertRaises(ValidationError):
            undo_batch_endorsement(batch_id=self.batch.pk, user=self.lab)

    def test_out_of_sequence_endorsement_to_head_requires_reason(self):
        initialize_custody(batch=self.batch, user=self.lab, initial_role=ROLE_LAB_ENCODER)
        with self.assertRaises(ValidationError):
            endorse_batch(batch_id=self.batch.pk, user=self.lab, destination_role="Head")
        endorse_batch(
            batch_id=self.batch.pk, user=self.lab, destination_role="Head",
            remarks="Urgent consultation requested",
        )
        confirm_batch_receipt(batch_id=self.batch.pk, user=self.head)
        self.assertEqual(BatchCustody.objects.get(batch=self.batch).current_role, "Head")

    def test_endorsement_does_not_modify_batch_verification_status(self):
        self.batch.bat_Status = "Encoding"
        self.batch.save(update_fields=("bat_Status",))
        initialize_custody(batch=self.batch, user=self.lab, initial_role=ROLE_LAB_ENCODER)
        endorse_batch(batch_id=self.batch.pk, user=self.lab, destination_role=ROLE_ENCODER)
        confirm_batch_receipt(batch_id=self.batch.pk, user=self.dmu)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.bat_Status, "Encoding")

    def test_role_scoped_dashboard_and_shortcut_navigation(self):
        initialize_custody(batch=self.batch, user=self.lab, initial_role=ROLE_LAB_ENCODER)
        self.client.force_login(self.lab)
        dashboard = self.client.get(reverse("receiving_workflow_dashboard"), {"tab": "received"})
        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(dashboard.context["page_obj"].paginator.count, 1)
        self.assertContains(dashboard, "Endorse")
        self.assertContains(dashboard, "New receipt")

        self.client.force_login(self.checker)
        unseen = self.client.get(reverse("receiving_workflow_dashboard"), {"tab": "all"})
        self.assertEqual(unseen.context["page_obj"].paginator.count, 0)
        hidden = self.client.get(reverse("receiving_workflow_detail", args=[self.batch.pk]))
        self.assertEqual(hidden.status_code, 404)

    def test_transition_endpoints_reject_get_and_wrong_role(self):
        initialize_custody(batch=self.batch, user=self.lab, initial_role=ROLE_LAB_ENCODER)
        endorse_url = reverse("receiving_workflow_endorse", args=[self.batch.pk])
        self.client.force_login(self.lab)
        self.assertEqual(self.client.get(endorse_url).status_code, 405)
        response = self.client.post(endorse_url, {"destination_role": ROLE_ENCODER})
        self.assertEqual(response.status_code, 302)

        confirm_url = reverse("receiving_workflow_confirm", args=[self.batch.pk])
        self.client.force_login(self.checker)
        self.assertEqual(self.client.post(confirm_url).status_code, 404)
        self.client.force_login(self.dmu)
        self.assertEqual(self.client.post(confirm_url).status_code, 302)
        self.assertEqual(BatchCustody.objects.get(batch=self.batch).current_role, ROLE_ENCODER)
