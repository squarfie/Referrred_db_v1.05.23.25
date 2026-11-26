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
from apps.verification.models import VerificationCase


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

    def test_all_records_shows_undo_only_for_unconfirmed_sender_handoff(self):
        initialize_custody(batch=self.batch, user=self.lab, initial_role=ROLE_LAB_ENCODER)
        endorse_batch(batch_id=self.batch.pk, user=self.lab, destination_role=ROLE_ENCODER)
        dashboard_url = reverse("receiving_workflow_dashboard")
        undo_url = reverse("receiving_workflow_undo", args=[self.batch.pk])

        self.client.force_login(self.lab)
        sender = self.client.get(dashboard_url, {"tab": "all"})
        self.assertContains(sender, self.batch.bat_Batch_Code)
        self.assertContains(sender, 'action="' + undo_url + '"', html=False)
        self.assertContains(sender, 'aria-label="Undo unconfirmed batch endorsement"')
        self.assertNotContains(sender, ">Undo</button>", html=False)
        self.assertContains(sender, "Details")
        self.assertNotContains(sender, "Confirm Receipt")
        role_sender = self.client.get(reverse("receiving_role_handoffs"))
        self.assertContains(role_sender, 'action="' + undo_url + '"', html=False)
        self.assertContains(role_sender, 'aria-label="Undo unconfirmed batch endorsement"')

        self.client.force_login(self.dmu)
        recipient = self.client.get(dashboard_url, {"tab": "all"})
        self.assertContains(recipient, self.batch.bat_Batch_Code)
        self.assertNotContains(recipient, 'action="' + undo_url + '"', html=False)
        pending = self.client.get(dashboard_url, {"tab": "pending"})
        self.assertNotContains(pending, 'action="' + reverse("receiving_workflow_confirm", args=[self.batch.pk]) + '"', html=False)
        role_receiver = self.client.get(reverse("receiving_role_handoffs"))
        self.assertContains(role_receiver, 'action="' + reverse("receiving_workflow_confirm", args=[self.batch.pk]) + '"', html=False)
        self.assertNotContains(role_receiver, 'aria-label="Undo unconfirmed batch endorsement"')

        self.client.force_login(self.lab)
        recalled = self.client.post(undo_url)
        self.assertEqual(recalled.status_code, 302)
        custody = BatchCustody.objects.get(batch=self.batch)
        self.assertEqual(custody.pending_role, "")
        self.assertEqual(custody.events.last().action, BatchCustodyEvent.Action.UNDONE)
        after = self.client.get(dashboard_url, {"tab": "all"})
        self.assertNotContains(after, 'action="' + undo_url + '"', html=False)

    def test_receiving_has_two_workspace_tabs_and_handoff_undo_stays_inline(self):
        initialize_custody(batch=self.batch, user=self.lab, initial_role=ROLE_LAB_ENCODER)
        endorse_batch(batch_id=self.batch.pk, user=self.lab, destination_role=ROLE_ENCODER)
        self.client.force_login(self.lab)
        url = reverse("receiving_workflow_dashboard")
        receiving = self.client.get(url, {"tab": "all"})
        self.assertContains(receiving, "My Batch Handoffs")
        self.assertContains(receiving, 'aria-label="Receiving workspaces"')
        self.assertContains(receiving, 'aria-label="Undo unconfirmed batch endorsement"')
        self.assertNotContains(receiving, "No generated batch handoffs currently assigned to your role.")
        handoffs = self.client.get(url, {"section": "handoffs"})
        self.assertEqual(handoffs.status_code, 200)
        self.assertContains(handoffs, self.batch.bat_Batch_Code)
        self.assertContains(handoffs, 'value="receiving_handoffs"')
        self.assertContains(handoffs, reverse("receiving_role_handoff_detail", args=[self.batch.pk]))
        undone = self.client.post(
            reverse("receiving_workflow_undo", args=[self.batch.pk]),
            {"next": "receiving_handoffs"},
        )
        self.assertRedirects(undone, url + "?section=handoffs")
        self.assertNotContains(
            self.client.get(url, {"section": "handoffs"}),
            'aria-label="Undo unconfirmed batch endorsement"',
        )

    def test_confirmed_endorsement_keeps_history_without_undo(self):
        initialize_custody(batch=self.batch, user=self.lab, initial_role=ROLE_LAB_ENCODER)
        endorse_batch(batch_id=self.batch.pk, user=self.lab, destination_role=ROLE_ENCODER)
        confirm_batch_receipt(batch_id=self.batch.pk, user=self.dmu)
        self.client.force_login(self.lab)
        all_records = self.client.get(
            reverse("receiving_workflow_dashboard"), {"tab": "all"}
        )
        self.assertContains(all_records, self.batch.bat_Batch_Code)
        self.assertNotContains(
            all_records,
            'action="' + reverse("receiving_workflow_undo", args=[self.batch.pk]) + '"',
            html=False,
        )
        self.assertEqual(
            BatchCustodyEvent.objects.filter(
                custody__batch=self.batch, action=BatchCustodyEvent.Action.ENDORSED
            ).count(),
            1,
        )

    def test_role_scoped_dashboard_and_shortcut_navigation(self):
        initialize_custody(batch=self.batch, user=self.lab, initial_role=ROLE_LAB_ENCODER)
        self.client.force_login(self.lab)
        dashboard = self.client.get(reverse("receiving_workflow_dashboard"), {"tab": "processing"})
        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(dashboard.context["page_obj"].paginator.count, 1)
        self.assertNotContains(dashboard, 'action="' + reverse("receiving_workflow_endorse", args=[self.batch.pk]) + '"', html=False)
        role_detail = self.client.get(reverse("receiving_role_handoff_detail", args=[self.batch.pk]))
        self.assertContains(role_detail, 'action="' + reverse("receiving_workflow_endorse", args=[self.batch.pk]) + '"', html=False)
        self.assertContains(dashboard, "New receipt")

        self.client.force_login(self.checker)
        unseen = self.client.get(reverse("receiving_workflow_dashboard"), {"tab": "all"})
        self.assertEqual(unseen.context["page_obj"].paginator.count, 0)
        hidden = self.client.get(reverse("receiving_workflow_detail", args=[self.batch.pk]))
        self.assertEqual(hidden.status_code, 404)

    def test_unified_worklist_reuses_existing_inline_receiving_filters(self):
        initialize_custody(batch=self.batch, user=self.lab, initial_role=ROLE_LAB_ENCODER)
        self.client.force_login(self.lab)
        response = self.client.get(reverse("receiving_workflow_dashboard"), {"tab": "processing"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="receiving-tabbar"')
        self.assertContains(response, 'class="receiving-filters receiving-filters--inline"')
        self.assertContains(response, 'class="receiving-search-group"')
        self.assertContains(response, 'class="receiving-filter-reset"')
        self.assertNotContains(response, 'class="receiving-board-search"')
        markup = response.content.decode()
        self.assertLess(markup.index('class="receiving-tabbar"'), markup.index('class="table-responsive receiving-unified-scroll"'))

    def test_completed_tab_requires_actual_release_not_head_signature(self):
        initialize_custody(batch=self.batch, user=self.lab, initial_role=ROLE_LAB_ENCODER)
        case = VerificationCase.objects.create(
            final_batch=self.batch,
            status=VerificationCase.STATUS_SIGNED_BY_HEAD,
        )
        self.client.force_login(self.lab)
        signed = self.client.get(reverse("receiving_workflow_dashboard"), {"tab": "completed"})
        self.assertEqual(signed.context["page_obj"].paginator.count, 0)
        self.assertEqual(next(tab["count"] for tab in signed.context["tabs"] if tab["key"] == "completed"), 0)
        self.assertNotContains(signed, self.batch.bat_Batch_Code)

        case.status = VerificationCase.STATUS_RELEASED
        case.save(update_fields=("status",))
        released = self.client.get(reverse("receiving_workflow_dashboard"), {"tab": "completed"})
        self.assertEqual(released.context["page_obj"].paginator.count, 1)
        self.assertContains(released, self.batch.bat_Batch_Code)
        self.assertContains(released, "Released")

    def test_receiving_detail_does_not_offer_batch_endorsement_but_role_page_does(self):
        initialize_custody(batch=self.batch, user=self.lab, initial_role=ROLE_LAB_ENCODER)
        self.client.force_login(self.lab)
        read_only = self.client.get(reverse("receiving_workflow_detail", args=[self.batch.pk]))
        self.assertEqual(read_only.status_code, 200)
        self.assertNotContains(read_only, 'id="receiving-endorsement"')
        self.assertNotContains(read_only, "Submit endorsement")
        self.assertContains(read_only, "Activity and tracking")
        role_detail = self.client.get(reverse("receiving_role_handoff_detail", args=[self.batch.pk]))
        self.assertContains(role_detail, 'action="' + reverse("receiving_workflow_endorse", args=[self.batch.pk]) + '"', html=False)

        self.client.force_login(self.checker)
        self.assertEqual(
            self.client.get(reverse("receiving_role_handoff_detail", args=[self.batch.pk])).status_code,
            404,
        )

    def test_secretariat_only_role_has_no_generated_batch_handoff_workspace(self):
        secretariat = self.make_user("secretariat-role-handoff", "Secretariat")
        self.client.force_login(secretariat)
        self.assertEqual(
            self.client.get(reverse("receiving_role_handoffs")).status_code, 403,
        )
        self.assertEqual(
            self.client.get(reverse("receiving_role_handoff_detail", args=[self.batch.pk])).status_code,
            403,
        )

    def test_generated_batch_actions_are_separate_from_receiving_history(self):
        initialize_custody(batch=self.batch, user=self.lab, initial_role=ROLE_LAB_ENCODER)
        self.client.force_login(self.lab)
        for tab in ("processing", "all"):
            dashboard = self.client.get(reverse("receiving_workflow_dashboard"), {"tab": tab})
            self.assertNotContains(
                dashboard,
                'action="' + reverse("receiving_workflow_endorse", args=[self.batch.pk]) + '"',
                html=False,
            )
        role_queue = self.client.get(reverse("receiving_role_handoffs"))
        self.assertContains(role_queue, self.batch.bat_Batch_Code)
        role_detail = self.client.get(reverse("receiving_role_handoff_detail", args=[self.batch.pk]))
        self.assertContains(role_detail, 'action="' + reverse("receiving_workflow_endorse", args=[self.batch.pk]) + '"', html=False)

    def test_released_verification_case_cannot_be_endorsed_by_direct_post(self):
        initialize_custody(batch=self.batch, user=self.lab, initial_role=ROLE_LAB_ENCODER)
        VerificationCase.objects.create(
            final_batch=self.batch, status=VerificationCase.STATUS_RELEASED,
        )
        self.client.force_login(self.lab)
        detail = self.client.get(reverse("receiving_role_handoff_detail", args=[self.batch.pk]))
        self.assertNotContains(detail, 'action="' + reverse("receiving_workflow_endorse", args=[self.batch.pk]) + '"', html=False)
        response = self.client.post(
            reverse("receiving_workflow_endorse", args=[self.batch.pk]),
            {"destination_role": ROLE_ENCODER},
        )
        self.assertEqual(response.status_code, 302)
        custody = BatchCustody.objects.get(batch=self.batch)
        self.assertEqual(custody.pending_role, "")
        self.assertEqual(custody.events.filter(action=BatchCustodyEvent.Action.ENDORSED).count(), 0)

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
