"""Regression tests for QR scan login, lab handoff, and irreversible deletion."""
from datetime import date, datetime, time
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.home.models import SiteData
from apps.receiving.forms import LabReceiptForm
from apps.receiving.models import ReferralReceipt, ReceivingEvent


class QrAcknowledgmentAndDeletionTests(TestCase):
    def setUp(self):
        self.site = SiteData.objects.create(SiteCode="QRX", SiteName="QR Test Site")
        self.secretariat = User.objects.create_user("qr_secretariat", password="test-pass")
        self.secretariat.groups.add(Group.objects.get_or_create(name="Secretariat")[0])
        self.lab = User.objects.create_user("qr_lab", password="test-pass")
        self.lab.groups.add(Group.objects.get_or_create(name="LAB Encoder")[0])
        self.admin = User.objects.create_superuser("qr_admin", "qradmin@example.org", "test-pass")
        self.receipt = ReferralReceipt.objects.create(
            tracking_number="TRACK-QRX",
            site=self.site,
            date_sent=date(2026, 11, 10),
            date_received=date(2026, 11, 12),
            time_received=time(8, 0),
            received_by=self.secretariat,
        )
        ReceivingEvent.objects.create(
            receipt=self.receipt,
            event_type=ReceivingEvent.EventType.RECEIVED,
            performed_by=self.secretariat,
        )

    @override_settings(ACCOUNT_APPROVAL_REQUIRE_STAFF_LINK=False)
    def test_anonymous_qr_link_preserves_destination_through_login(self):
        detail = reverse("receiving_detail", kwargs={"receiving_id": self.receipt.receiving_id})
        response = self.client.get(detail)
        self.assertEqual(response.status_code, 302)
        self.assertIn("next=", response["Location"])
        login_url = response["Location"]
        login_page = self.client.get(login_url)
        self.assertContains(login_page, 'name="next"')
        self.assertContains(login_page, detail)
        response = self.client.post(login_url, {
            "username": self.lab.username,
            "password": "test-pass",
            "next": detail,
        })
        self.assertRedirects(response, detail)

    @override_settings(ACCOUNT_APPROVAL_REQUIRE_STAFF_LINK=False)
    def test_login_rejects_external_next_redirect(self):
        login_url = reverse("login")
        response = self.client.post(login_url, {
            "username": self.lab.username,
            "password": "test-pass",
            "next": "https://evil.example/steal",
        })
        self.assertRedirects(response, "/")

    def test_lab_confirmation_is_not_automatic_on_qr_scan(self):
        self.client.force_login(self.lab)
        detail = reverse("receiving_detail", kwargs={"receiving_id": self.receipt.receiving_id})
        self.assertEqual(self.client.get(detail).status_code, 200)
        self.receipt.refresh_from_db()
        self.assertEqual(self.receipt.status, ReferralReceipt.Status.RECEIVED)
        self.assertEqual(self.receipt.events.count(), 1)

    def test_scanning_confirmed_batch_shows_read_only_detail(self):
        self.receipt.status = ReferralReceipt.Status.LAB_RECEIVED
        self.receipt.endorsed_at = timezone.make_aware(datetime(2026, 11, 12, 9, 0))
        self.receipt.lab_date_received = date(2026, 11, 12)
        self.receipt.lab_time_received = time(10, 0)
        self.receipt.lab_received_by = self.lab
        self.receipt.save()
        self.client.force_login(self.lab)
        response = self.client.get(reverse(
            "receiving_lab_confirm",
            kwargs={"receiving_id": self.receipt.receiving_id},
        ))
        self.assertRedirects(response, reverse("receiving_detail", kwargs={"receiving_id": self.receipt.receiving_id}))
        self.assertEqual(self.receipt.events.count(), 1)

    def test_form_default_is_not_before_endorsement_in_same_minute(self):
        endorsed_at = timezone.make_aware(datetime(2026, 11, 12, 9, 30, 45))
        self.receipt.endorsed_at = endorsed_at
        form = LabReceiptForm(receipt=self.receipt, now=timezone.make_aware(datetime(2026, 11, 12, 9, 30, 50)))
        self.assertEqual(form.initial["lab_time_received"], time(9, 31))

    def test_secretariat_cannot_delete_and_post_is_required_to_mutate(self):
        url = reverse("receiving_delete_permanently", kwargs={"receiving_id": self.receipt.receiving_id})
        self.client.force_login(self.secretariat)
        self.assertEqual(self.client.post(url, {"confirmation": "DELETE PERMANENTLY"}).status_code, 302)
        self.assertTrue(ReferralReceipt.objects.filter(pk=self.receipt.pk).exists())
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertTrue(ReferralReceipt.objects.filter(pk=self.receipt.pk).exists())

    def test_deletion_requires_exact_confirmation_phrase(self):
        url = reverse("receiving_delete_permanently", kwargs={"receiving_id": self.receipt.receiving_id})
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(url, {"confirmation": "DELETE"}).status_code, 200)
        self.assertTrue(ReferralReceipt.objects.filter(pk=self.receipt.pk).exists())

    def test_admin_permanently_deletes_receipt_and_events(self):
        url = reverse("receiving_delete_permanently", kwargs={"receiving_id": self.receipt.receiving_id})
        self.client.force_login(self.admin)
        response = self.client.post(url, {"confirmation": "DELETE PERMANENTLY"})
        self.assertRedirects(response, reverse("receiving_dashboard"))
        self.assertFalse(ReferralReceipt.objects.filter(pk=self.receipt.pk).exists())
        self.assertFalse(ReceivingEvent.objects.filter(receipt_id=self.receipt.pk).exists())
