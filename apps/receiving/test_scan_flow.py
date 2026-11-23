"""Authenticated QR receipt: POST only, once, then the current workflow step."""
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.db import connections
from django.test import Client, TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone

from apps.home.models import SiteData
from apps.home.permissions import ROLE_LAB_STAFF
from apps.receiving.models import (
    LaboratoryAccession, LaboratoryBiobank, LaboratoryIsolate,
    LaboratoryReceiving, ReceivingEvent, ReferralReceipt,
)


class ScanReceiptFixtures:
    @classmethod
    def setUpTestData(cls):
        cls.receiver = User.objects.create_user("scan-receiver")
        cls.receiver.groups.add(Group.objects.get_or_create(name="LAB Encoder")[0])
        cls.staff = User.objects.create_user("scan-main-lab")
        cls.staff.groups.add(Group.objects.get_or_create(name=ROLE_LAB_STAFF)[0])
        cls.secretariat = User.objects.create_user("scan-secretariat")
        cls.secretariat.groups.add(Group.objects.get_or_create(name="Secretariat")[0])
        cls.admin = User.objects.create_superuser("scan-admin", "scan@example.org", "test-pass")
        cls.site = SiteData.objects.create(SiteCode="SCN", SiteName="Scan test site")
        cls.endorsed_at = timezone.now() - timedelta(hours=2)
        cls.receipt = ReferralReceipt.objects.create(
            tracking_number="SCAN-COURIER", site=cls.site,
            date_sent=timezone.localdate() - timedelta(days=1),
            date_received=timezone.localdate() - timedelta(days=1),
            time_received=timezone.localtime().time(), received_by=cls.secretariat,
            status=ReferralReceipt.Status.ENDORSED,
            endorsed_at=cls.endorsed_at, endorsed_by=cls.secretariat,
        )
        cls.received_batch = ReferralReceipt.objects.create(
            tracking_number="SCAN-ISOLATES", site=cls.site,
            date_sent=timezone.localdate() - timedelta(days=1),
            date_received=timezone.localdate() - timedelta(days=1),
            time_received=timezone.localtime().time(), received_by=cls.secretariat,
            status=ReferralReceipt.Status.LAB_RECEIVED,
            endorsed_at=cls.endorsed_at, endorsed_by=cls.secretariat,
            lab_date_received=timezone.localdate(),
            lab_time_received=timezone.localtime().time(), lab_received_by=cls.receiver,
        )
        cls.record = LaboratoryReceiving.objects.create(
            receipt=cls.received_batch, total_isolates_received=1, isolates_accepted=1,
        )
        cls.accession = LaboratoryAccession.objects.create(
            laboratory_receiving=cls.record, date_referred=timezone.localdate(),
            start_number=1, end_number=1, batch_number=1, total_batches=1,
        )
        cls.isolate = LaboratoryIsolate.objects.create(
            accession_range=cls.accession, accession_number="26ARS_SCN0001",
            status=LaboratoryIsolate.Status.ID_PENDING,
            revival_started_at=timezone.now() - timedelta(hours=4),
            revival_completed_at=timezone.now() - timedelta(hours=3),
            endorsed_by=cls.receiver, endorsed_at=cls.endorsed_at,
        )


class ScanReceiveFlowTests(ScanReceiptFixtures, TestCase):
    def batch_url(self):
        return reverse("receiving_scan", args=[self.receipt.receiving_id])

    def isolate_url(self):
        return reverse("laboratory_isolate_scan", args=[self.isolate.accession_number])

    def test_anonymous_scan_requires_login_preserving_destination(self):
        for url in (self.batch_url(), self.isolate_url()):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertRedirects(response, reverse("login") + "?next=" + url,
                                     fetch_redirect_response=False)

    def test_courier_scan_get_renders_auto_post_without_receiving(self):
        self.client.force_login(self.receiver)
        response = self.client.get(self.batch_url())
        self.assertContains(response, 'id="scan-receive-form"')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertContains(response, 'scan-receive.js')
        self.assertIn("no-store", response["Cache-Control"])
        self.receipt.refresh_from_db()
        self.assertEqual(self.receipt.status, ReferralReceipt.Status.ENDORSED)
        self.assertFalse(self.receipt.events.exists())

    def test_courier_scan_post_records_server_actor_time_and_opens_worksheet(self):
        self.client.force_login(self.receiver)
        received_at = timezone.now()
        with patch("apps.receiving.scan_views.timezone.now", return_value=received_at):
            response = self.client.post(self.batch_url(), {"lab_date_received": "2000-01-01"})
        self.assertRedirects(response, reverse("laboratory_receiving_workspace", args=[self.receipt.receiving_id]), fetch_redirect_response=False)
        self.receipt.refresh_from_db()
        self.assertEqual(self.receipt.status, ReferralReceipt.Status.LAB_RECEIVED)
        self.assertEqual(self.receipt.lab_received_by, self.receiver)
        local = timezone.localtime(received_at)
        self.assertEqual(self.receipt.lab_date_received, local.date())
        self.assertEqual(self.receipt.lab_time_received, local.time())
        event = self.receipt.events.get(event_type=ReceivingEvent.EventType.LAB_RECEIVED)
        self.assertEqual(event.performed_by, self.receiver)
        self.assertEqual(event.occurred_at, received_at)
        self.assertTrue(LaboratoryReceiving.objects.filter(receipt=self.receipt).exists())

    def test_courier_replay_preserves_original_receipt(self):
        self.client.force_login(self.receiver)
        self.client.post(self.batch_url())
        self.receipt.refresh_from_db()
        original = (self.receipt.lab_received_by_id, self.receipt.lab_date_received, self.receipt.lab_time_received)
        self.client.force_login(self.admin)
        for method in (self.client.get, self.client.post):
            self.assertRedirects(method(self.batch_url()), reverse("laboratory_receiving_workspace", args=[self.receipt.receiving_id]), fetch_redirect_response=False)
        self.receipt.refresh_from_db()
        self.assertEqual((self.receipt.lab_received_by_id, self.receipt.lab_date_received, self.receipt.lab_time_received), original)
        self.assertEqual(self.receipt.events.filter(event_type="lab_received").count(), 1)

    def test_unendorsed_batch_scan_cannot_skip_endorsement(self):
        self.receipt.status = ReferralReceipt.Status.RECEIVED
        self.receipt.endorsed_at = None
        self.receipt.endorsed_by = None
        self.receipt.save()
        self.client.force_login(self.receiver)
        for method in (self.client.get, self.client.post):
            self.assertEqual(method(self.batch_url()).status_code, 302)
        self.receipt.refresh_from_db()
        self.assertEqual(self.receipt.status, ReferralReceipt.Status.RECEIVED)
        self.assertFalse(self.receipt.events.exists())

    def test_wrong_roles_cannot_receive_courier(self):
        for user in (self.secretariat, self.staff):
            self.client.force_login(user)
            for method in (self.client.get, self.client.post):
                self.assertEqual(method(self.batch_url()).status_code, 302)
        self.receipt.refresh_from_db()
        self.assertEqual(self.receipt.status, ReferralReceipt.Status.ENDORSED)

    def test_accession_scan_get_does_not_acknowledge(self):
        self.client.force_login(self.staff)
        response = self.client.get(self.isolate_url())
        self.assertContains(response, 'id="scan-receive-form"')
        self.isolate.refresh_from_db()
        self.assertEqual(self.isolate.status, LaboratoryIsolate.Status.ID_PENDING)
        self.assertIsNone(self.isolate.acknowledged_at)

    def test_accession_scan_post_receives_once_and_opens_identification_step(self):
        self.client.force_login(self.staff)
        received_at = timezone.now()
        with patch("apps.laboratory.workflow.timezone.now", return_value=received_at):
            response = self.client.post(self.isolate_url())
        self.assertRedirects(response, reverse("main_lab_isolate_detail", args=[self.isolate.pk]) + "#identification", fetch_redirect_response=False)
        self.isolate.refresh_from_db()
        self.assertEqual(self.isolate.status, LaboratoryIsolate.Status.ID_TESTING)
        self.assertEqual(self.isolate.acknowledged_by, self.staff)
        self.assertEqual(self.isolate.acknowledged_at, received_at)
        event = self.received_batch.events.get(event_type="id_received")
        self.assertIn(self.isolate.accession_number, event.remarks)
        self.assertEqual(event.performed_by, self.staff)
        self.assertEqual(event.occurred_at, received_at)
        self.client.force_login(self.admin)
        self.client.post(self.isolate_url())
        self.isolate.refresh_from_db()
        self.assertEqual(self.isolate.acknowledged_by, self.staff)
        self.assertEqual(self.isolate.acknowledged_at, received_at)
        self.assertEqual(self.received_batch.events.filter(event_type="id_received").count(), 1)

    def test_bench_a_encoder_cannot_acknowledge_main_lab_id_by_scan(self):
        self.client.force_login(self.receiver)
        self.assertEqual(self.client.get(self.isolate_url()).status_code, 302)
        self.client.post(self.isolate_url())
        self.isolate.refresh_from_db()
        self.assertEqual(self.isolate.status, LaboratoryIsolate.Status.ID_PENDING)
        self.assertIsNone(self.isolate.acknowledged_by)

    def test_user_without_laboratory_role_cannot_acknowledge_id(self):
        self.receiver.groups.clear()
        self.client.force_login(self.receiver)
        for method in (self.client.get, self.client.post):
            self.assertEqual(method(self.isolate_url()).status_code, 302)
        self.isolate.refresh_from_db()
        self.assertEqual(self.isolate.status, LaboratoryIsolate.Status.ID_PENDING)
        self.assertFalse(self.received_batch.events.exists())

    def test_later_accession_stages_open_current_step_without_advancing(self):
        self.client.force_login(self.staff)
        for status, section in (("id_testing", "identification"), ("id_returned", "id_complete"),
                                ("ready_for_biobank", "biobank"), ("biobanked", "closed")):
            with self.subTest(status=status):
                self.isolate.status = status
                self.isolate.save(update_fields=["status"])
                for method in (self.client.get, self.client.post):
                    self.assertRedirects(method(self.isolate_url()), reverse("main_lab_isolate_detail", args=[self.isolate.pk]) + "#" + section, fetch_redirect_response=False)
                self.isolate.refresh_from_db()
                self.assertEqual(self.isolate.status, status)
        self.assertFalse(self.received_batch.events.exists())

    def test_scan_post_requires_csrf_for_both_qr_types(self):
        for user, url in ((self.receiver, self.batch_url()), (self.staff, self.isolate_url())):
            with self.subTest(url=url):
                client = Client(enforce_csrf_checks=True)
                client.force_login(user)
                self.assertEqual(client.post(url).status_code, 403)
                self.assertEqual(client.get(url).status_code, 200)
                token = client.cookies["csrftoken"].value
                self.assertEqual(client.post(url, {"csrfmiddlewaretoken": token}).status_code, 302)

    @override_settings(RECEIVING_QR_BASE_URL="https://lab.example.org")
    def test_generated_qr_payloads_are_canonical_scan_links(self):
        self.client.force_login(self.admin)
        with patch("apps.receiving.views.render_qr_png", return_value=b"PNG") as qr:
            self.client.get(reverse("receiving_qr", args=[self.receipt.receiving_id]))
        qr.assert_called_once_with("https://lab.example.org" + self.batch_url())
        with patch("apps.laboratory.scan_views.render_qr_png", return_value=b"PNG") as qr:
            response = self.client.get(reverse("laboratory_isolate_qr", args=[self.isolate.pk]))
        self.assertEqual(response["Content-Type"], "image/png")
        qr.assert_called_once_with("https://lab.example.org" + self.isolate_url())

    @override_settings(RECEIVING_QR_BASE_URL="https://lab.example.org")
    def test_biobank_sticker_encodes_accession_scan_url(self):
        self.isolate.status = LaboratoryIsolate.Status.READY_FOR_BIOBANK
        self.isolate.final_organism = "Klebsiella pneumoniae"
        self.isolate.save()
        LaboratoryBiobank.objects.create(isolate=self.isolate, whonet_code="KPN", date_harvested=timezone.localdate())
        self.client.force_login(self.staff)
        with patch("apps.laboratory.views.render_qr_png", return_value=b"PNG") as qr:
            response = self.client.get(reverse("laboratory_biobank_label", args=[self.isolate.pk]))
        self.assertEqual(response.status_code, 200)
        qr.assert_called_once_with("https://lab.example.org" + self.isolate_url())

    def test_isolate_page_exposes_qr_before_biobank_eligibility(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("main_lab_isolate_detail", args=[self.isolate.pk]))
        self.assertContains(response, reverse("laboratory_isolate_qr", args=[self.isolate.pk]))
        label = self.client.get(reverse("laboratory_isolate_scan_label", args=[self.isolate.pk]))
        self.assertContains(label, self.isolate.accession_number)
        self.assertContains(label, reverse("laboratory_isolate_qr", args=[self.isolate.pk]))

    def test_unknown_codes_return_not_found(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("laboratory_isolate_scan", args=["MISSING-ACCESSION"])).status_code, 404)


@skipUnlessDBFeature("has_select_for_update")
class ConcurrentScanReceiptTests(ScanReceiptFixtures, TransactionTestCase):
    def setUp(self):
        self.setUpTestData()

    def simultaneous_posts(self, url, first_user, second_user):
        ready = Barrier(2)

        def post(user_id):
            try:
                client = Client()
                client.force_login(User.objects.get(pk=user_id))
                ready.wait(timeout=10)
                return client.post(url).status_code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(post, [first_user.pk, second_user.pk]))
        self.assertEqual(results, [302, 302])

    def test_simultaneous_courier_scans_record_exactly_one_receipt(self):
        self.simultaneous_posts(reverse("receiving_scan", args=[self.receipt.receiving_id]), self.receiver, self.admin)
        self.receipt.refresh_from_db()
        self.assertEqual(self.receipt.status, ReferralReceipt.Status.LAB_RECEIVED)
        event = self.receipt.events.get(event_type="lab_received")
        self.assertEqual(event.performed_by_id, self.receipt.lab_received_by_id)
        self.assertEqual(timezone.localtime(event.occurred_at).time(), self.receipt.lab_time_received)

    def test_simultaneous_accession_scans_record_exactly_one_receipt(self):
        self.simultaneous_posts(reverse("laboratory_isolate_scan", args=[self.isolate.accession_number]), self.staff, self.admin)
        self.isolate.refresh_from_db()
        self.assertEqual(self.isolate.status, LaboratoryIsolate.Status.ID_TESTING)
        event = self.received_batch.events.get(event_type="id_received")
        self.assertEqual(event.performed_by_id, self.isolate.acknowledged_by_id)
        self.assertEqual(event.occurred_at, self.isolate.acknowledged_at)
