"""Regression tests: Bench A endorsement and atomic DMU receipt/batch creation."""
from datetime import date, time, datetime
from unittest.mock import patch
from django.utils import timezone

from django.contrib.auth.models import Group, User
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.home.models import Batch_Table, SiteData
from apps.home.permissions import ROLE_ENCODER, ROLE_LAB_ENCODER, ROLE_CHECKER
from apps.home.services.batch_creation import BatchCreationError, create_referred_batch
from apps.receiving.models import (
    ReferralReceipt, LaboratoryReceiving, LaboratoryAccession,
    LaboratoryIsolate, LaboratoryBatchHandoff, LaboratoryBatchHandoffEvent,
)
from apps.receiving.prebatch import (
    endorse_accession_to_dmu, confirm_dmu_accession, create_dmu_batch,
    confirm_dmu_receipt_and_create_batch,
)


class LaboratoryToDMUHandoffTests(TestCase):
    def setUp(self):
        self.lab = self.user("prebatch-lab", ROLE_LAB_ENCODER)
        self.dmu = self.user("prebatch-dmu", ROLE_ENCODER)
        self.checker = self.user("prebatch-checker", ROLE_CHECKER)
        self.site = SiteData.objects.create(SiteCode="PBD", SiteName="Prebatch Test Hospital")
        self.receipt = ReferralReceipt.objects.create(
            tracking_number="PBD-COURIER-001", site=self.site,
            date_sent=date(2026, 11, 18), date_received=date(2026, 11, 19),
            time_received=time(10, 0), status=ReferralReceipt.Status.LAB_RECEIVED,
            received_by=self.lab, lab_date_received=date(2026, 11, 19),
            lab_time_received=time(11, 0), lab_received_by=self.lab,
            endorsed_by=self.lab,
            endorsed_at=timezone.make_aware(datetime(2026, 11, 19, 10, 30)),
        )
        self.lab_receiving = LaboratoryReceiving.objects.create(
            receipt=self.receipt, recorded_by=self.lab,
            total_isolates_received=1, isolates_accepted=1,
            date_accepted=date(2026, 11, 19),
        )
        self.accession = LaboratoryAccession.objects.create(
            laboratory_receiving=self.lab_receiving,
            date_referred=date(2026, 11, 19),
            start_number=1, end_number=1, batch_number=1, total_batches=1,
            assigned_by=self.lab,
        )
        self.isolate = LaboratoryIsolate.objects.create(
            accession_range=self.accession,
            accession_number="26ARS_PBD0001",
            status=LaboratoryIsolate.Status.NON_VIABLE,
        )

    @staticmethod
    def user(name, role):
        user = User.objects.create_user(username=name, password="test-pass")
        user.groups.add(Group.objects.get_or_create(name=role)[0])
        return user

    def test_bench_a_endorse_does_not_create_batch_and_records_audit(self):
        self.client.force_login(self.lab)
        response = self.client.post(reverse("laboratory_batch_endorse_dmu", args=[self.accession.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Batch_Table.objects.filter(bat_SiteCode="PBD").exists())
        self.assertIsNone(LaboratoryAccession.objects.get(pk=self.accession.pk).batch_id)
        handoff = LaboratoryBatchHandoff.objects.get(accession=self.accession)
        self.assertEqual(handoff.status, LaboratoryBatchHandoff.Status.PENDING)
        self.assertEqual(handoff.events.count(), 1)
        self.assertEqual(handoff.events.first().action, LaboratoryBatchHandoffEvent.Action.ENDORSED)
        with self.assertRaises(ValidationError):
            endorse_accession_to_dmu(accession_id=self.accession.pk, user=self.lab)
        self.assertEqual(handoff.events.count(), 1)

    def test_unfinished_isolates_cannot_be_endorsed(self):
        self.isolate.status = LaboratoryIsolate.Status.REVIVAL_PENDING
        self.isolate.save(update_fields=("status",))
        with self.assertRaises(ValidationError):
            endorse_accession_to_dmu(accession_id=self.accession.pk, user=self.lab)
        self.assertFalse(LaboratoryBatchHandoff.objects.exists())

    def test_bench_a_button_only_enabled_after_workflow_completion(self):
        self.client.force_login(self.lab)
        path = reverse("laboratory_receiving_workspace", args=[self.receipt.receiving_id])
        ready = self.client.get(path)
        self.assertContains(ready, reverse("laboratory_batch_endorse_dmu", args=[self.accession.pk]))
        self.assertContains(ready, ">Endorse to DMU</button>")

        self.isolate.status = LaboratoryIsolate.Status.REVIVAL_PENDING
        self.isolate.save(update_fields=("status",))
        incomplete = self.client.get(path)
        self.assertContains(incomplete, "Complete Bench A final processing")
        self.assertContains(incomplete, 'aria-disabled="true"')
        self.assertNotContains(
            incomplete,
            'action="' + reverse("laboratory_batch_endorse_dmu", args=[self.accession.pk]) + '"',
            html=False,
        )

    def test_endorsed_accession_inventory_and_metadata_are_locked(self):
        endorse_accession_to_dmu(accession_id=self.accession.pk, user=self.lab)
        self.client.force_login(self.lab)
        edit = self.client.post(
            reverse("laboratory_accession_edit", args=[self.accession.pk]),
            {"date_referred": "2026-11-20", "start_number": "2", "end_number": "2",
             "batch_number": "1", "total_batches": "1"},
        )
        self.assertEqual(edit.status_code, 302)
        self.accession.refresh_from_db()
        self.assertEqual(self.accession.start_number, 1)
        delete = self.client.post(
            reverse("laboratory_accession_delete", args=[self.accession.pk]), {"confirmation": "DELETE"}
        )
        self.assertEqual(delete.status_code, 302)
        self.assertTrue(LaboratoryAccession.objects.filter(pk=self.accession.pk).exists())
        counts = self.client.post(
            reverse("laboratory_organism_matrix_save", args=[self.accession.pk]),
            {"organism_HA": "1"},
        )
        self.assertEqual(counts.status_code, 302)
        self.assertEqual(self.accession.organism_counts.count(), 0)

    def test_dmu_confirmation_creates_batch_in_one_action(self):
        handoff = endorse_accession_to_dmu(accession_id=self.accession.pk, user=self.lab)
        with self.assertRaises(ValidationError):
            create_dmu_batch(handoff_id=handoff.pk, user=self.dmu)
        with self.assertRaises(PermissionDenied):
            confirm_dmu_receipt_and_create_batch(handoff_id=handoff.pk, user=self.checker)
        with self.assertRaises(PermissionDenied):
            confirm_dmu_receipt_and_create_batch(handoff_id=handoff.pk, user=self.lab)
        self.assertFalse(Batch_Table.objects.filter(bat_SiteCode="PBD").exists())

        self.client.force_login(self.dmu)
        pending = self.client.get(reverse("receiving_workflow_dashboard"), {"tab": "pending"})
        self.assertContains(pending, "PBD-COURIER-001")
        self.assertContains(pending, "</i>Confirm Receipt</button>")
        self.assertContains(pending, reverse("receiving_dmu_confirm", args=[handoff.pk]))
        self.assertNotContains(pending, reverse("receiving_dmu_create_batch", args=[handoff.pk]))
        self.assertEqual(self.client.get(reverse("receiving_dmu_confirm", args=[handoff.pk])).status_code, 405)

        confirmed = self.client.post(reverse("receiving_dmu_confirm", args=[handoff.pk]))
        self.assertEqual(confirmed.status_code, 302)
        self.assertIn("tab=processing", confirmed["Location"])
        handoff.refresh_from_db()
        self.accession.refresh_from_db()
        self.assertEqual(handoff.status, LaboratoryBatchHandoff.Status.CREATED)
        self.assertEqual(handoff.confirmed_by, self.dmu)
        self.assertEqual(handoff.created_by, self.dmu)
        self.assertIsNotNone(handoff.confirmed_at)
        self.assertIsNotNone(self.accession.batch_id)
        self.assertEqual(self.accession.batch.created_by, self.dmu)
        self.assertEqual(self.accession.batch.receiving_custody.current_role, ROLE_ENCODER)
        self.assertEqual(list(handoff.events.values_list("action", flat=True)), [
            "endorsed", "confirmed", "batch_created",
        ])
        second = self.client.post(reverse("receiving_dmu_confirm", args=[handoff.pk]))
        self.assertEqual(second.status_code, 302)
        self.assertEqual(Batch_Table.objects.filter(bat_SiteCode="PBD").count(), 1)
        self.assertEqual(handoff.events.count(), 3)

    def test_atomic_confirmation_rolls_back_when_batch_creation_fails(self):
        handoff = endorse_accession_to_dmu(accession_id=self.accession.pk, user=self.lab)
        with patch("apps.receiving.prebatch.create_referred_batch", side_effect=BatchCreationError("Batch creation failed")):
            with self.assertRaises(BatchCreationError):
                confirm_dmu_receipt_and_create_batch(handoff_id=handoff.pk, user=self.dmu)
        handoff.refresh_from_db()
        self.assertEqual(handoff.status, LaboratoryBatchHandoff.Status.PENDING)
        self.assertIsNone(handoff.confirmed_by)
        self.assertEqual(list(handoff.events.values_list("action", flat=True)), ["endorsed"])
        self.assertFalse(Batch_Table.objects.filter(bat_SiteCode="PBD").exists())

        self.client.force_login(self.dmu)
        with patch("apps.receiving.prebatch.create_referred_batch", side_effect=BatchCreationError("Batch creation failed")):
            response = self.client.post(reverse("receiving_dmu_confirm", args=[handoff.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn("tab=pending", response["Location"])
        handoff.refresh_from_db()
        self.assertEqual(handoff.status, LaboratoryBatchHandoff.Status.PENDING)
        self.assertEqual(handoff.events.count(), 1)

    def test_legacy_confirmed_handoff_can_still_create_batch(self):
        handoff = endorse_accession_to_dmu(accession_id=self.accession.pk, user=self.lab)
        confirm_dmu_accession(handoff_id=handoff.pk, user=self.dmu)
        self.client.force_login(self.dmu)
        received = self.client.get(reverse("receiving_workflow_dashboard"), {"tab": "received"})
        self.assertContains(received, reverse("receiving_dmu_create_batch", args=[handoff.pk]))
        created = self.client.post(reverse("receiving_dmu_create_batch", args=[handoff.pk]))
        self.assertEqual(created.status_code, 302)
        handoff.refresh_from_db()
        self.assertEqual(handoff.status, LaboratoryBatchHandoff.Status.CREATED)
        self.assertEqual(Batch_Table.objects.filter(bat_SiteCode="PBD").count(), 1)

    def test_existing_manual_batch_creation_and_signatory_editor_are_preserved(self):
        self.client.force_login(self.dmu)
        manual_form = self.client.get(reverse("batch_create_view"))
        self.assertEqual(manual_form.status_code, 200)

        manual_batch = create_referred_batch(
            site_code="PBD", site_name=self.site.SiteName,
            referral_date=date(2026, 11, 19), start_number=20, end_number=20,
            batch_number=2, total_batches=2, created_by=self.dmu,
            personnel={"bat_Enc_Lic": "12345"},
        ).batch
        self.assertEqual(manual_batch.bat_Enc_Lic, "12345")
        editor = self.client.get(reverse("batch_edit_view", args=[manual_batch.pk]))
        self.assertEqual(editor.status_code, 200)
        self.assertIn("bat_Encoder", editor.context["form"].fields)
        self.assertIn("bat_Checker", editor.context["form"].fields)
        self.assertFalse(editor.context["form"].fields["bat_Verifier"].disabled)

        handoff = endorse_accession_to_dmu(accession_id=self.accession.pk, user=self.lab)
        confirm_dmu_receipt_and_create_batch(handoff_id=handoff.pk, user=self.dmu)
        self.accession.refresh_from_db()
        auto_editor = self.client.get(reverse("batch_edit_view", args=[self.accession.batch_id]))
        self.assertEqual(auto_editor.status_code, 200)
        self.assertIn("bat_LabManager", auto_editor.context["form"].fields)
        self.assertFalse(auto_editor.context["form"].fields["bat_Head"].disabled)
        manual_batch.refresh_from_db()
        self.assertEqual(manual_batch.bat_Enc_Lic, "12345")

    def test_generic_add_batch_service_cannot_bypass_dmu_receipt(self):
        args = dict(
            site_code="PBD", site_name=self.site.SiteName,
            referral_date=date(2026, 11, 19),
            start_number=1, end_number=1, batch_number=1, total_batches=1,
            created_by=self.dmu,
        )
        with self.assertRaises(BatchCreationError):
            create_referred_batch(**args)
        handoff = endorse_accession_to_dmu(accession_id=self.accession.pk, user=self.lab)
        with self.assertRaises(BatchCreationError):
            create_referred_batch(**args)
        confirm_dmu_accession(handoff_id=handoff.pk, user=self.dmu)
        with self.assertRaises(BatchCreationError):
            create_referred_batch(**args)
        self.assertFalse(Batch_Table.objects.filter(bat_SiteCode="PBD").exists())

    def test_receiving_history_tracks_three_handoff_events(self):
        handoff = endorse_accession_to_dmu(accession_id=self.accession.pk, user=self.lab)
        self.client.force_login(self.lab)
        history = self.client.get(reverse("receiving_detail", args=[self.receipt.receiving_id]))
        self.assertContains(history, "Endorsed to DMU")
        self.assertContains(history, "DMU Handoff")
        self.client.force_login(self.dmu)
        confirm_dmu_accession(handoff_id=handoff.pk, user=self.dmu)
        create_dmu_batch(handoff_id=handoff.pk, user=self.dmu)
        history = self.client.get(reverse("receiving_detail", args=[self.receipt.receiving_id]))
        self.assertContains(history, "DMU receipt confirmed")
        self.assertContains(history, "Raw Data batch created by DMU")

    def test_legacy_lab_create_url_does_not_create_batch(self):
        self.client.force_login(self.lab)
        response = self.client.post(reverse("laboratory_batch_create", args=[self.accession.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(LaboratoryAccession.objects.get(pk=self.accession.pk).batch_id)
        self.assertEqual(LaboratoryBatchHandoff.objects.filter(accession=self.accession).count(), 1)

    def test_receiving_dashboard_uses_one_header_for_all_record_types(self):
        handoff = endorse_accession_to_dmu(accession_id=self.accession.pk, user=self.lab)
        self.client.force_login(self.dmu)
        pending = self.client.get(reverse("receiving_workflow_dashboard"), {"tab": "pending", "year": "2026"})
        self.assertEqual(pending.status_code, 200)
        self.assertContains(pending, "Accession Handoff")
        self.assertContains(pending, "</i>Confirm Receipt</button>")
        self.assertContains(pending, 'receiving-status-endorsed"><i class="fas fa-circle" aria-hidden="true"></i>')
        self.assertContains(pending, 'class="btn btn-sm btn-success receiving-action-primary mb-1"')
        self.assertContains(pending, 'receiving-action-detail mb-1')
        self.assertContains(pending, reverse("receiving_dmu_confirm", args=[handoff.pk]))
        self.assertEqual(pending.content.count(b"<thead>"), 1)
        self.assertEqual(pending.content.count(b'<table class="table receiving-table receiving-unified-table">'), 1)
        self.assertContains(pending, 'class="receiving-date">')
        self.assertContains(pending, 'data-label="Date / Activity"')
        self.assertNotContains(pending, 'class="receiving-unified-pagination"')

        confirm_dmu_receipt_and_create_batch(handoff_id=handoff.pk, user=self.dmu)
        received = self.client.get(reverse("receiving_workflow_dashboard"), {"tab": "received", "year": "2026"})
        self.assertNotContains(received, reverse("receiving_dmu_create_batch", args=[handoff.pk]))
        self.assertNotContains(received, reverse("receiving_dmu_confirm", args=[handoff.pk]))
        self.assertEqual(received.context["prebatch_count"], 0)
        self.accession.refresh_from_db()
        self.assertIsNotNone(self.accession.batch_id)
        processing = self.client.get(reverse("receiving_workflow_dashboard"), {"tab": "processing", "year": "2026"})
        self.assertContains(processing, self.accession.batch.bat_Batch_Code)
