from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.receiving import tests as fixtures
from apps.receiving.models import LaboratoryIsolate, ReceivingEvent
from .workflow import WorkflowError, correct_revival_outcome


class RevivalCorrectionTests(TestCase):
    setUp = fixtures.LaboratoryReceivingWorkspaceTests.setUp
    create_pending_accession = fixtures.LaboratoryReceivingWorkspaceTests.create_pending_accession

    def create_saved_isolate(self, **changes):
        accession = self.create_pending_accession()
        now = timezone.now()
        data = {
            "accession_range": accession,
            "accession_number": accession.accession_numbers[0],
            "status": "revived",
            "revival_started_at": now - timedelta(hours=2),
            "revival_completed_at": now - timedelta(hours=1),
            "revival_failed_reason": "Initial notes",
        }
        data.update(changes)
        return LaboratoryIsolate.objects.create(**data)

    def correction_payload(self, item, **changes):
        values = {
            "status": item.status,
            "started_at": timezone.localtime(item.revival_started_at).strftime("%Y-%m-%dT%H:%M:%S"),
            "completed_at": timezone.localtime(item.revival_completed_at).strftime("%Y-%m-%dT%H:%M:%S"),
            "observations": "Corrected notes",
        }
        values.update(changes)
        return values

    def test_step_four_exposes_edit_history_after_revival_is_done(self):
        item = self.create_saved_isolate(
            status="id_testing", endorsed_at=timezone.now(), acknowledged_at=timezone.now())
        worksheet = self.client.get(reverse(
            "laboratory_receiving_workspace", args=[self.receipt.receiving_id]))
        self.assertContains(worksheet, "Bench A Preparation Done")
        self.assertNotContains(worksheet, "Review / Edit Saved Revival")
        batch_url = reverse("laboratory_batch_detail", args=[item.accession_range.pk])
        active = self.client.get(batch_url, {"area": "processing"})
        self.assertContains(active, "View Saved Revival History")
        history_link = "?area=processing&amp;history=1"
        self.assertContains(active, history_link, html=False)
        history = self.client.get(batch_url, {"area": "processing", "history": "1"})
        self.assertContains(history, "Back to Active Revival &amp; Site ID", html=False)
        self.assertContains(history, item.accession_number)
        detail = self.client.get(reverse("laboratory_isolate_detail", args=[item.pk]))
        self.assertContains(detail, "Edit saved revival information")
        self.assertContains(detail, 'name="started_at"')
        self.assertContains(detail, 'name="completed_at"')

    def test_bench_a_can_correct_saved_revival_with_append_only_audit(self):
        item = self.create_saved_isolate()
        start = item.revival_started_at
        end = item.revival_completed_at
        response = self.client.post(
            reverse("laboratory_revival_correct", args=[item.pk]),
            self.correction_payload(item, status="revival_failed",
                                    observations="No growth on repeat plate"),
        )
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.status, "revival_failed")
        self.assertEqual(item.revival_failed_reason, "No growth on repeat plate")
        self.assertEqual(timezone.localtime(item.revival_started_at).replace(microsecond=0),
                         timezone.localtime(start).replace(microsecond=0))
        self.assertEqual(timezone.localtime(item.revival_completed_at).replace(microsecond=0),
                         timezone.localtime(end).replace(microsecond=0))
        audit = ReceivingEvent.objects.get(
            receipt=self.receipt, event_type=ReceivingEvent.EventType.CORRECTED)
        self.assertEqual(audit.performed_by, self.lab_user)
        self.assertIn("Initial notes", audit.remarks)
        self.assertIn("No growth on repeat plate", audit.remarks)
        self.assertIn(item.accession_number, audit.remarks)
        detail = self.client.get(reverse("laboratory_isolate_detail", args=[item.pk]))
        self.assertContains(detail, "Revival correction history")
        self.assertContains(detail, "Initial notes")
        self.assertContains(detail, "No growth on repeat plate")

    def test_downstream_workflow_keeps_status_and_allows_date_correction(self):
        now = timezone.now()
        item = self.create_saved_isolate(
            status="id_testing", endorsed_at=now, acknowledged_at=now)
        response = self.client.post(
            reverse("laboratory_revival_correct", args=[item.pk]),
            self.correction_payload(item, status="unchanged", observations="Reviewed timestamps"),
        )
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.status, "id_testing")
        self.assertEqual(item.revival_failed_reason, "Reviewed timestamps")
        self.assertTrue(item.accession_range.laboratory_receiving.receipt.events.filter(
            event_type=ReceivingEvent.EventType.CORRECTED).exists())
        denied = self.client.post(
            reverse("laboratory_revival_correct", args=[item.pk]),
            self.correction_payload(item, status="revival_failed"),
        )
        self.assertEqual(denied.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.status, "id_testing")

    def test_invalid_dates_and_missing_unsuccessful_observations_are_rejected(self):
        item = self.create_saved_isolate()
        with self.assertRaises(WorkflowError):
            correct_revival_outcome(
                item.pk, "revived", timezone.now().isoformat(),
                (timezone.now()-timedelta(days=1)).isoformat(),
                "Changed", self.lab_user)
        with self.assertRaises(WorkflowError):
            correct_revival_outcome(
                item.pk, "revival_failed", item.revival_started_at.isoformat(),
                item.revival_completed_at.isoformat(), "", self.lab_user)
        item.refresh_from_db()
        self.assertEqual(item.status, "revived")
        self.assertFalse(ReceivingEvent.objects.filter(
            receipt=self.receipt, event_type=ReceivingEvent.EventType.CORRECTED).exists())

    def test_main_lab_only_user_cannot_see_or_post_revival_correction(self):
        item = self.create_saved_isolate()
        main_lab = User.objects.create_user("revival-correction-main-lab")
        main_lab.groups.add(Group.objects.get_or_create(name="Laboratory Staff")[0])
        self.client.force_login(main_lab)
        with self.assertRaises(WorkflowError):
            correct_revival_outcome(
                item.pk, "revived", item.revival_started_at.isoformat(),
                item.revival_completed_at.isoformat(), "Unauthorized", main_lab)
        response = self.client.post(
            reverse("laboratory_revival_correct", args=[item.pk]),
            self.correction_payload(item))
        self.assertNotEqual(response.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.revival_failed_reason, "Initial notes")
