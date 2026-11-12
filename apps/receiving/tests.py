from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.utils import timezone
from importlib import import_module
from datetime import date, datetime, time
from unittest.mock import patch
from io import BytesIO
from types import SimpleNamespace
from uuid import UUID

from apps.home.models import SiteData
from apps.receiving.forms import ReferralReceiptForm
from apps.receiving.models import ReceivingEvent, ReferralReceipt
from django.test import RequestFactory, override_settings
from django.urls import NoReverseMatch, reverse
from PIL import Image

from apps.home import permissions as home_permissions


class ReceivingPermissionTests(TestCase):
    def create_user_with_roles(self, username, *roles, is_superuser=False):
        user = User.objects.create_user(
            username=username,
            password="test-pass",
            is_superuser=is_superuser,
        )
        for role in roles:
            group, _ = Group.objects.get_or_create(name=role)
            user.groups.add(group)
        return user

    def receiving_permissions(self):
        try:
            return import_module("apps.receiving.permissions")
        except ModuleNotFoundError:
            self.fail("apps.receiving.permissions must define the receiving role boundary")

    def test_secretariat_group_is_resolved_as_secretariat_role(self):
        secretariat_role = getattr(home_permissions, "ROLE_SECRETARIAT", "")
        user = self.create_user_with_roles("secretariat", "Secretariat")

        self.assertEqual(secretariat_role, "Secretariat")
        self.assertEqual(home_permissions.get_user_roles(user), {"Secretariat"})

    def test_secretariat_flags_expose_receiving_navigation_without_other_write_access(self):
        user = self.create_user_with_roles("secretariat_flags", "Secretariat")

        flags = home_permissions.role_flags(user)

        self.assertTrue(flags.get("is_secretariat", False))
        self.assertTrue(flags.get("can_view_receiving", False))
        self.assertFalse(flags["can_create"])
        self.assertFalse(flags["can_wgs_create"])
        self.assertFalse(flags["can_manage_settings"])

    def test_secretariat_can_read_and_manage_but_not_confirm_lab_receipt(self):
        permissions = self.receiving_permissions()
        user = self.create_user_with_roles("secretariat_permissions", "Secretariat")

        self.assertTrue(permissions.can_read_receiving(user))
        self.assertTrue(permissions.can_manage_secretariat_receiving(user))
        self.assertFalse(permissions.can_confirm_lab_receipt(user))

    def test_lab_encoder_can_read_and_confirm_but_not_manage_secretariat_receiving(self):
        permissions = self.receiving_permissions()
        user = self.create_user_with_roles(
            "lab_encoder_permissions",
            home_permissions.ROLE_LAB_ENCODER,
        )

        self.assertTrue(permissions.can_read_receiving(user))
        self.assertFalse(permissions.can_manage_secretariat_receiving(user))
        self.assertTrue(permissions.can_confirm_lab_receipt(user))

    def test_admin_can_read_and_manage_but_not_confirm_lab_receipt(self):
        permissions = self.receiving_permissions()
        user = self.create_user_with_roles("admin_permissions", is_superuser=True)

        self.assertTrue(permissions.can_read_receiving(user))
        self.assertTrue(permissions.can_manage_secretariat_receiving(user))
        self.assertFalse(permissions.can_confirm_lab_receipt(user))

    def test_unrelated_role_has_no_receiving_access(self):
        permissions = self.receiving_permissions()
        user = self.create_user_with_roles(
            "verifier_permissions",
            home_permissions.ROLE_VERIFIER,
        )

        self.assertFalse(permissions.can_read_receiving(user))
        self.assertFalse(permissions.can_manage_secretariat_receiving(user))
        self.assertFalse(permissions.can_confirm_lab_receipt(user))

    def test_multiple_roles_grant_union_of_receiving_permissions(self):
        permissions = self.receiving_permissions()
        user = self.create_user_with_roles(
            "multi_receiving_permissions",
            "Secretariat",
            home_permissions.ROLE_LAB_ENCODER,
        )

        self.assertTrue(permissions.can_read_receiving(user))
        self.assertTrue(permissions.can_manage_secretariat_receiving(user))
        self.assertTrue(permissions.can_confirm_lab_receipt(user))


class ReceivingFormTests(TestCase):
    def setUp(self):
        self.site = SiteData.objects.create(SiteCode="BGH", SiteName="Baguio General Hospital")

    def receiving_forms(self):
        try:
            return import_module("apps.receiving.forms")
        except ModuleNotFoundError:
            self.fail("apps.receiving.forms must provide receiving validation")

    def receipt_data(self, **overrides):
        data = {
            "tracking_number": "TRACK-001",
            "site": self.site.pk,
            "date_sent": "2026-11-10",
            "date_received": "2026-11-12",
            "time_received": "09:30",
            "remarks": "Package intact",
        }
        data.update(overrides)
        return data

    def test_receipt_form_defaults_received_date_and_time_to_local_now(self):
        forms = self.receiving_forms()
        local_now = timezone.make_aware(datetime(2026, 11, 12, 8, 45))

        form = forms.ReferralReceiptForm(now=local_now)

        self.assertEqual(form.initial["date_received"], date(2026, 11, 12))
        self.assertEqual(form.initial["time_received"], time(8, 45))

    def test_receipt_form_strips_and_collapses_tracking_number_whitespace(self):
        forms = self.receiving_forms()
        form = forms.ReferralReceiptForm(
            data=self.receipt_data(tracking_number="  ABC   123  ")
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["tracking_number"], "ABC 123")

    def test_receipt_form_rejects_date_sent_after_secretariat_received_date(self):
        forms = self.receiving_forms()
        form = forms.ReferralReceiptForm(
            data=self.receipt_data(date_sent="2026-11-13")
        )

        self.assertFalse(form.is_valid())
        self.assertIn("date_sent", form.errors)

    def test_lab_form_rejects_timestamp_before_endorsement(self):
        forms = self.receiving_forms()
        receipt = ReferralReceipt.objects.create(
            tracking_number="TRACK-002",
            site=self.site,
            date_sent=date(2026, 11, 10),
            date_received=date(2026, 11, 12),
            time_received=time(8, 0),
            status=ReferralReceipt.Status.ENDORSED,
            endorsed_at=timezone.make_aware(datetime(2026, 11, 12, 10, 0)),
        )
        form = forms.LabReceiptForm(
            data={"lab_date_received": "2026-11-12", "lab_time_received": "09:59"},
            receipt=receipt,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("lab_time_received", form.errors)

    def test_lab_form_accepts_timestamp_equal_to_endorsement(self):
        forms = self.receiving_forms()
        receipt = ReferralReceipt.objects.create(
            tracking_number="TRACK-003",
            site=self.site,
            date_sent=date(2026, 11, 10),
            date_received=date(2026, 11, 12),
            time_received=time(8, 0),
            status=ReferralReceipt.Status.ENDORSED,
            endorsed_at=timezone.make_aware(datetime(2026, 11, 12, 10, 0)),
        )
        form = forms.LabReceiptForm(
            data={"lab_date_received": "2026-11-12", "lab_time_received": "10:00"},
            receipt=receipt,
        )

        self.assertTrue(form.is_valid(), form.errors)


class ReceivingServiceTests(TestCase):
    def setUp(self):
        self.site = SiteData.objects.create(SiteCode="RMC", SiteName="Regional Medical Center")
        self.secretariat = User.objects.create_user("service_secretariat", password="test-pass")
        self.lab_encoder = User.objects.create_user("service_lab", password="test-pass")

    def services(self):
        try:
            return import_module("apps.receiving.services")
        except ModuleNotFoundError:
            self.fail("apps.receiving.services must own atomic workflow changes")

    def valid_receipt_form(self, tracking_number="SERVICE-001"):
        form = ReferralReceiptForm(
            data={
                "tracking_number": tracking_number,
                "site": self.site.pk,
                "date_sent": "2026-11-10",
                "date_received": "2026-11-12",
                "time_received": "08:15",
                "remarks": "Received sealed",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        return form

    def create_receipt(self, *, status=ReferralReceipt.Status.RECEIVED, **overrides):
        values = {
            "tracking_number": "SERVICE-EXISTING",
            "site": self.site,
            "date_sent": date(2026, 11, 10),
            "date_received": date(2026, 11, 12),
            "time_received": time(8, 15),
            "status": status,
        }
        values.update(overrides)
        return ReferralReceipt.objects.create(**values)

    def test_create_receipt_sets_server_fields_and_received_event(self):
        services = self.services()
        receipt = services.create_receipt(
            form=self.valid_receipt_form(),
            user=self.secretariat,
        )

        self.assertEqual(receipt.status, ReferralReceipt.Status.RECEIVED)
        self.assertEqual(receipt.received_by, self.secretariat)
        event = receipt.events.get()
        self.assertEqual(event.event_type, ReceivingEvent.EventType.RECEIVED)
        self.assertEqual(event.performed_by, self.secretariat)
        self.assertEqual(
            timezone.localtime(event.occurred_at).replace(tzinfo=None),
            datetime(2026, 11, 12, 8, 15),
        )

    def test_endorsement_sets_server_time_user_status_and_one_event(self):
        services = self.services()
        receipt = self.create_receipt()
        endorsed_at = timezone.make_aware(datetime(2026, 11, 12, 9, 30))

        with patch("apps.receiving.services.timezone.now", return_value=endorsed_at):
            updated = services.endorse_receipt(receipt_id=receipt.pk, user=self.secretariat)

        self.assertEqual(updated.status, ReferralReceipt.Status.ENDORSED)
        self.assertEqual(updated.endorsed_by, self.secretariat)
        self.assertEqual(updated.endorsed_at, endorsed_at)
        event = updated.events.get(event_type=ReceivingEvent.EventType.ENDORSED)
        self.assertEqual(event.occurred_at, endorsed_at)
        self.assertEqual(event.performed_by, self.secretariat)

    def test_repeated_endorsement_raises_without_duplicate_event(self):
        services = self.services()
        receipt = self.create_receipt()
        services.endorse_receipt(receipt_id=receipt.pk, user=self.secretariat)

        with self.assertRaises(services.InvalidReceivingTransition):
            services.endorse_receipt(receipt_id=receipt.pk, user=self.secretariat)

        self.assertEqual(
            ReceivingEvent.objects.filter(
                receipt=receipt,
                event_type=ReceivingEvent.EventType.ENDORSED,
            ).count(),
            1,
        )

    def test_endorsement_rejects_invalid_starting_state(self):
        services = self.services()
        receipt = self.create_receipt(status=ReferralReceipt.Status.LAB_RECEIVED)

        with self.assertRaises(services.InvalidReceivingTransition):
            services.endorse_receipt(receipt_id=receipt.pk, user=self.secretariat)

        receipt.refresh_from_db()
        self.assertEqual(receipt.status, ReferralReceipt.Status.LAB_RECEIVED)
        self.assertFalse(receipt.events.exists())

    def test_lab_confirmation_sets_submitted_time_user_status_and_one_event(self):
        services = self.services()
        endorsed_at = timezone.make_aware(datetime(2026, 11, 12, 9, 30))
        receipt = self.create_receipt(
            status=ReferralReceipt.Status.ENDORSED,
            endorsed_at=endorsed_at,
            endorsed_by=self.secretariat,
        )

        updated = services.confirm_lab_receipt(
            receipt_id=receipt.pk,
            user=self.lab_encoder,
            lab_date_received=date(2026, 11, 12),
            lab_time_received=time(10, 5),
        )

        self.assertEqual(updated.status, ReferralReceipt.Status.LAB_RECEIVED)
        self.assertEqual(updated.lab_received_by, self.lab_encoder)
        self.assertEqual(updated.lab_date_received, date(2026, 11, 12))
        self.assertEqual(updated.lab_time_received, time(10, 5))
        event = updated.events.get(event_type=ReceivingEvent.EventType.LAB_RECEIVED)
        self.assertEqual(
            timezone.localtime(event.occurred_at).replace(tzinfo=None),
            datetime(2026, 11, 12, 10, 5),
        )

    def test_lab_confirmation_before_endorsement_raises_without_changes(self):
        services = self.services()
        receipt = self.create_receipt(status=ReferralReceipt.Status.RECEIVED)

        with self.assertRaises(services.InvalidReceivingTransition):
            services.confirm_lab_receipt(
                receipt_id=receipt.pk,
                user=self.lab_encoder,
                lab_date_received=date(2026, 11, 12),
                lab_time_received=time(10, 5),
            )

        receipt.refresh_from_db()
        self.assertEqual(receipt.status, ReferralReceipt.Status.RECEIVED)
        self.assertIsNone(receipt.lab_received_by)
        self.assertFalse(receipt.events.exists())

    def test_repeated_lab_confirmation_raises_without_duplicate_event(self):
        services = self.services()
        receipt = self.create_receipt(
            status=ReferralReceipt.Status.ENDORSED,
            endorsed_at=timezone.make_aware(datetime(2026, 11, 12, 9, 30)),
        )
        services.confirm_lab_receipt(
            receipt_id=receipt.pk,
            user=self.lab_encoder,
            lab_date_received=date(2026, 11, 12),
            lab_time_received=time(10, 5),
        )

        with self.assertRaises(services.InvalidReceivingTransition):
            services.confirm_lab_receipt(
                receipt_id=receipt.pk,
                user=self.lab_encoder,
                lab_date_received=date(2026, 11, 12),
                lab_time_received=time(10, 10),
            )

        self.assertEqual(
            ReceivingEvent.objects.filter(
                receipt=receipt,
                event_type=ReceivingEvent.EventType.LAB_RECEIVED,
            ).count(),
            1,
        )


class ReceivingQrTests(TestCase):
    receiving_id = UUID("12345678-1234-5678-1234-567812345678")

    def qr_module(self):
        try:
            return import_module("apps.receiving.qr")
        except ModuleNotFoundError:
            self.fail("apps.receiving.qr must generate stable receipt QR payloads")

    @override_settings(RECEIVING_QR_BASE_URL="https://receiving.example.org")
    def test_detail_url_uses_canonical_origin_and_receipt_uuid(self):
        qr = self.qr_module()
        request = RequestFactory().get("/", HTTP_HOST="127.0.0.1:8000")
        receipt = SimpleNamespace(receiving_id=self.receiving_id)

        url = qr.build_receipt_detail_url(receipt=receipt, request=request)

        self.assertEqual(
            url,
            "https://receiving.example.org/receiving/12345678-1234-5678-1234-567812345678/",
        )

    @override_settings(RECEIVING_QR_BASE_URL="https://receiving.example.org/")
    def test_detail_url_normalizes_trailing_slash(self):
        qr = self.qr_module()
        request = RequestFactory().get("/", HTTP_HOST="localhost:8000")
        receipt = SimpleNamespace(receiving_id=self.receiving_id)

        url = qr.build_receipt_detail_url(receipt=receipt, request=request)

        self.assertEqual(
            url,
            "https://receiving.example.org/receiving/12345678-1234-5678-1234-567812345678/",
        )

    @override_settings(RECEIVING_QR_BASE_URL="")
    def test_local_detail_url_falls_back_to_request_origin(self):
        qr = self.qr_module()
        request = RequestFactory().get("/", HTTP_HOST="localhost:8000")
        receipt = SimpleNamespace(receiving_id=self.receiving_id)

        url = qr.build_receipt_detail_url(receipt=receipt, request=request)

        self.assertEqual(
            url,
            "http://localhost:8000/receiving/12345678-1234-5678-1234-567812345678/",
        )

    def test_render_qr_png_returns_valid_png_bytes(self):
        qr = self.qr_module()

        png_bytes = qr.render_qr_png("https://receiving.example.org/receiving/example/")

        self.assertTrue(png_bytes.startswith(b"\x89PNG\r\n\x1a\n"))
        with Image.open(BytesIO(png_bytes)) as image:
            self.assertEqual(image.format, "PNG")
            self.assertGreater(image.width, 0)
            self.assertGreater(image.height, 0)


class ReceivingViewTests(TestCase):
    def setUp(self):
        self.site = SiteData.objects.create(SiteCode="PGH", SiteName="Philippine General Hospital")
        self.secretariat = self.create_user("view_secretariat", "Secretariat")
        self.lab_encoder = self.create_user(
            "view_lab_encoder",
            home_permissions.ROLE_LAB_ENCODER,
        )
        self.verifier = self.create_user("view_verifier", home_permissions.ROLE_VERIFIER)
        self.admin = User.objects.create_superuser(
            "view_admin",
            "view_admin@example.org",
            "test-pass",
        )

    def create_user(self, username, *roles):
        user = User.objects.create_user(username, password="test-pass")
        for role in roles:
            group, _ = Group.objects.get_or_create(name=role)
            user.groups.add(group)
        return user

    def route(self, name, **kwargs):
        try:
            return reverse(name, kwargs=kwargs or None)
        except NoReverseMatch:
            self.fail(f"Receiving route {name!r} must be registered")

    def create_receipt(self, tracking="VIEW-001", status=ReferralReceipt.Status.RECEIVED):
        values = {
            "tracking_number": tracking,
            "site": self.site,
            "date_sent": date(2026, 11, 10),
            "date_received": date(2026, 11, 12),
            "time_received": time(8, 0),
            "received_by": self.secretariat,
            "status": status,
        }
        if status in {ReferralReceipt.Status.ENDORSED, ReferralReceipt.Status.LAB_RECEIVED}:
            values.update(
                endorsed_at=timezone.make_aware(datetime(2026, 11, 12, 9, 0)),
                endorsed_by=self.secretariat,
            )
        if status == ReferralReceipt.Status.LAB_RECEIVED:
            values.update(
                lab_date_received=date(2026, 11, 12),
                lab_time_received=time(10, 0),
                lab_received_by=self.lab_encoder,
            )
        return ReferralReceipt.objects.create(**values)

    def test_receiving_dashboard_redirects_anonymous_user_to_login(self):
        response = self.client.get(self.route("receiving_dashboard"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response.url)

    def test_unrelated_role_cannot_open_dashboard_or_detail(self):
        receipt = self.create_receipt()
        self.client.force_login(self.verifier)

        dashboard = self.client.get(self.route("receiving_dashboard"))
        detail = self.client.get(
            self.route("receiving_detail", receiving_id=receipt.receiving_id)
        )

        self.assertEqual(dashboard.status_code, 302)
        self.assertEqual(detail.status_code, 302)
        self.assertEqual(dashboard.url, reverse("home"))
        self.assertEqual(detail.url, reverse("home"))

    def test_secretariat_create_uses_authenticated_user_and_received_status(self):
        self.client.force_login(self.secretariat)
        response = self.client.post(
            self.route("receiving_create"),
            {
                "tracking_number": " CREATE   001 ",
                "site": self.site.pk,
                "date_sent": "2026-11-10",
                "date_received": "2026-11-12",
                "time_received": "08:30",
                "remarks": "Intact",
                "received_by": self.admin.pk,
                "status": ReferralReceipt.Status.LAB_RECEIVED,
            },
        )

        receipt = ReferralReceipt.objects.get(tracking_number="CREATE 001")
        self.assertRedirects(
            response,
            self.route("receiving_detail", receiving_id=receipt.receiving_id),
        )
        self.assertEqual(receipt.received_by, self.secretariat)
        self.assertEqual(receipt.status, ReferralReceipt.Status.RECEIVED)
        self.assertEqual(receipt.events.count(), 1)

    def test_dashboard_searches_trimmed_site_name_and_filters_status(self):
        matched = self.create_receipt("SEARCH-MATCH", ReferralReceipt.Status.ENDORSED)
        self.create_receipt("SEARCH-OTHER", ReferralReceipt.Status.RECEIVED)
        self.client.force_login(self.secretariat)

        response = self.client.get(
            self.route("receiving_dashboard"),
            {"q": "  General Hospital  ", "status": ReferralReceipt.Status.ENDORSED},
        )

        self.assertEqual(response.status_code, 200)
        receipts = list(response.context["page_obj"].object_list)
        self.assertEqual(receipts, [matched])
        self.assertEqual(response.context["q"], "General Hospital")

    def test_detail_is_looked_up_by_receiving_uuid(self):
        receipt = self.create_receipt()
        self.client.force_login(self.secretariat)

        response = self.client.get(
            self.route("receiving_detail", receiving_id=receipt.receiving_id)
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["receipt"], receipt)

    def test_endorsement_is_post_only_and_secretariat_can_endorse(self):
        receipt = self.create_receipt()
        url = self.route("receiving_endorse", receiving_id=receipt.receiving_id)
        self.client.force_login(self.secretariat)

        self.assertEqual(self.client.get(url).status_code, 405)
        response = self.client.post(url)

        self.assertRedirects(
            response,
            self.route("receiving_detail", receiving_id=receipt.receiving_id),
        )
        receipt.refresh_from_db()
        self.assertEqual(receipt.status, ReferralReceipt.Status.ENDORSED)

    def test_lab_encoder_can_confirm_receipt_but_secretariat_and_admin_cannot(self):
        receipt = self.create_receipt(status=ReferralReceipt.Status.ENDORSED)
        url = self.route("receiving_lab_confirm", receiving_id=receipt.receiving_id)
        payload = {"lab_date_received": "2026-11-12", "lab_time_received": "10:00"}

        for denied_user in (self.secretariat, self.admin):
            self.client.force_login(denied_user)
            denied = self.client.post(url, payload)
            self.assertEqual(denied.status_code, 302)
            receipt.refresh_from_db()
            self.assertEqual(receipt.status, ReferralReceipt.Status.ENDORSED)

        self.client.force_login(self.lab_encoder)
        confirmed = self.client.post(url, payload)

        self.assertRedirects(
            confirmed,
            self.route("receiving_detail", receiving_id=receipt.receiving_id),
        )
        receipt.refresh_from_db()
        self.assertEqual(receipt.status, ReferralReceipt.Status.LAB_RECEIVED)
        self.assertEqual(receipt.lab_received_by, self.lab_encoder)

    def test_stale_endorsement_does_not_duplicate_event(self):
        receipt = self.create_receipt(status=ReferralReceipt.Status.ENDORSED)
        ReceivingEvent.objects.create(
            receipt=receipt,
            event_type=ReceivingEvent.EventType.ENDORSED,
            performed_by=self.secretariat,
            occurred_at=receipt.endorsed_at,
        )
        self.client.force_login(self.secretariat)

        response = self.client.post(
            self.route("receiving_endorse", receiving_id=receipt.receiving_id)
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            receipt.events.filter(event_type=ReceivingEvent.EventType.ENDORSED).count(),
            1,
        )

    def test_qr_and_label_are_secretariat_or_admin_only(self):
        receipt = self.create_receipt()
        qr_url = self.route("receiving_qr", receiving_id=receipt.receiving_id)
        label_url = self.route("receiving_label", receiving_id=receipt.receiving_id)

        self.client.force_login(self.lab_encoder)
        self.assertEqual(self.client.get(qr_url).status_code, 302)
        self.assertEqual(self.client.get(label_url).status_code, 302)

        self.client.force_login(self.secretariat)
        qr_response = self.client.get(qr_url)
        label_response = self.client.get(label_url)
        self.assertEqual(qr_response.status_code, 200)
        self.assertEqual(qr_response["Content-Type"], "image/png")
        self.assertTrue(qr_response.content.startswith(b"\x89PNG"))
        self.assertEqual(label_response.status_code, 200)


class ReceivingTemplateTests(TestCase):
    def setUp(self):
        self.site = SiteData.objects.create(SiteCode="VSM", SiteName="Vicente Sotto Medical Center")
        self.secretariat = self.create_user("template_secretariat", "Secretariat")
        self.lab_encoder = self.create_user(
            "template_lab",
            home_permissions.ROLE_LAB_ENCODER,
        )
        self.admin = User.objects.create_superuser(
            "template_admin",
            "template_admin@example.org",
            "test-pass",
        )

    def create_user(self, username, *roles):
        user = User.objects.create_user(username, password="test-pass")
        for role in roles:
            group, _ = Group.objects.get_or_create(name=role)
            user.groups.add(group)
        return user

    def route(self, name, **kwargs):
        try:
            return reverse(name, kwargs=kwargs or None)
        except NoReverseMatch:
            self.fail(f"Receiving route {name!r} must be registered")

    def create_receipt(self, status=ReferralReceipt.Status.RECEIVED):
        values = {
            "tracking_number": f"TEMPLATE-{status}",
            "site": self.site,
            "date_sent": date(2026, 11, 10),
            "date_received": date(2026, 11, 12),
            "time_received": time(8, 0),
            "received_by": self.secretariat,
            "status": status,
        }
        if status in {ReferralReceipt.Status.ENDORSED, ReferralReceipt.Status.LAB_RECEIVED}:
            values.update(
                endorsed_at=timezone.make_aware(datetime(2026, 11, 12, 9, 0)),
                endorsed_by=self.secretariat,
            )
        if status == ReferralReceipt.Status.LAB_RECEIVED:
            values.update(
                lab_date_received=date(2026, 11, 12),
                lab_time_received=time(10, 0),
                lab_received_by=self.lab_encoder,
            )
        return ReferralReceipt.objects.create(**values)

    def test_dashboard_uses_layout_displays_counts_and_receiving_menu(self):
        self.create_receipt(ReferralReceipt.Status.RECEIVED)
        self.create_receipt(ReferralReceipt.Status.ENDORSED)
        self.create_receipt(ReferralReceipt.Status.LAB_RECEIVED)
        self.client.force_login(self.secretariat)

        response = self.client.get(self.route("receiving_dashboard"))

        self.assertTemplateUsed(response, "layouts/base.html")
        self.assertTemplateUsed(response, "receiving/dashboard.html")
        self.assertContains(response, "Receiving")
        self.assertContains(response, "Received by Secretariat")
        self.assertContains(response, "Endorsed to Laboratory")
        self.assertContains(response, "Received by Laboratory")
        self.assertContains(response, 'href="/receiving/"', html=False)

    def test_secretariat_sees_print_and_valid_endorsement_action(self):
        receipt = self.create_receipt(ReferralReceipt.Status.RECEIVED)
        self.client.force_login(self.secretariat)

        response = self.client.get(
            self.route("receiving_detail", receiving_id=receipt.receiving_id)
        )

        self.assertContains(response, "Print batch label")
        self.assertContains(response, "Endorse to laboratory")
        self.assertNotContains(response, "Confirm laboratory receipt")

    def test_lab_encoder_sees_only_confirmation_for_endorsed_receipt(self):
        receipt = self.create_receipt(ReferralReceipt.Status.ENDORSED)
        self.client.force_login(self.lab_encoder)

        response = self.client.get(
            self.route("receiving_detail", receiving_id=receipt.receiving_id)
        )

        self.assertContains(response, "Confirm laboratory receipt")
        self.assertNotContains(response, "Print batch label")
        self.assertNotContains(response, "Endorse to laboratory")

    def test_admin_has_secretariat_actions_but_not_lab_confirmation(self):
        receipt = self.create_receipt(ReferralReceipt.Status.ENDORSED)
        self.client.force_login(self.admin)

        response = self.client.get(
            self.route("receiving_detail", receiving_id=receipt.receiving_id)
        )

        self.assertContains(response, "Print batch label")
        self.assertNotContains(response, "Confirm laboratory receipt")

    def test_terminal_lab_received_detail_has_no_transition_action(self):
        receipt = self.create_receipt(ReferralReceipt.Status.LAB_RECEIVED)
        self.client.force_login(self.secretariat)

        response = self.client.get(
            self.route("receiving_detail", receiving_id=receipt.receiving_id)
        )

        self.assertContains(response, "Custody confirmed")
        self.assertNotContains(response, "Endorse to laboratory")
        self.assertNotContains(response, "Confirm laboratory receipt")

    def test_multi_role_user_sees_create_and_lab_confirmation_capabilities(self):
        user = self.create_user(
            "template_multi",
            "Secretariat",
            home_permissions.ROLE_LAB_ENCODER,
        )
        receipt = self.create_receipt(ReferralReceipt.Status.ENDORSED)
        self.client.force_login(user)

        dashboard = self.client.get(self.route("receiving_dashboard"))
        detail = self.client.get(
            self.route("receiving_detail", receiving_id=receipt.receiving_id)
        )

        self.assertContains(dashboard, "New receipt")
        self.assertContains(detail, "Print batch label")
        self.assertContains(detail, "Confirm laboratory receipt")

    def test_detail_displays_events_in_chronological_order(self):
        receipt = self.create_receipt(ReferralReceipt.Status.ENDORSED)
        ReceivingEvent.objects.create(
            receipt=receipt,
            event_type=ReceivingEvent.EventType.ENDORSED,
            performed_by=self.secretariat,
            occurred_at=receipt.endorsed_at,
        )
        ReceivingEvent.objects.create(
            receipt=receipt,
            event_type=ReceivingEvent.EventType.RECEIVED,
            performed_by=self.secretariat,
            occurred_at=timezone.make_aware(datetime(2026, 11, 12, 8, 0)),
        )
        self.client.force_login(self.secretariat)

        response = self.client.get(
            self.route("receiving_detail", receiving_id=receipt.receiving_id)
        )
        content = response.content.decode()
        activity = content[content.index(">Activity<") :]

        self.assertLess(
            activity.index("Received by Secretariat"),
            activity.index("Endorsed to Laboratory"),
        )

    def test_label_contains_batch_fields_and_no_patient_data(self):
        receipt = self.create_receipt()
        self.client.force_login(self.secretariat)

        response = self.client.get(
            self.route("receiving_label", receiving_id=receipt.receiving_id)
        )

        self.assertContains(response, receipt.tracking_number)
        self.assertContains(response, self.site.SiteCode)
        self.assertContains(response, "Nov. 12, 2026")
        self.assertContains(response, str(receipt.receiving_id))
        self.assertNotContains(response, "Patient")
