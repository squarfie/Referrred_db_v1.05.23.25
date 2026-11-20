from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from importlib import import_module
from datetime import date, datetime, time
from unittest.mock import patch
from io import BytesIO
from types import SimpleNamespace
from uuid import UUID

from apps.home.models import Batch_Table, Referred_Data, SiteData, TATform
from apps.receiving.forms import ReferralReceiptForm
from apps.receiving.admin import ReferralReceiptAdmin
from apps.receiving.models import (
    LaboratoryAccession,
    LaboratoryOrganismCount,
    LaboratoryReceiving,
    ReceivingEvent,
    ReferralReceipt,
)
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

    def test_receipt_form_rejects_future_secretariat_timestamp(self):
        now = timezone.make_aware(datetime(2026, 11, 12, 10, 0))
        form = ReferralReceiptForm(
            data=self.receipt_data(date_received="2026-11-12", time_received="10:01"),
            now=now,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("time_received", form.errors)

    def test_lab_form_rejects_future_laboratory_timestamp(self):
        endorsed_at = timezone.make_aware(datetime(2026, 11, 12, 9, 30))
        receipt = ReferralReceipt(
            tracking_number="LAB-FUTURE",
            site=self.site,
            date_sent=date(2026, 11, 11),
            date_received=date(2026, 11, 12),
            time_received=time(9, 0),
            status=ReferralReceipt.Status.ENDORSED,
            endorsed_at=endorsed_at,
        )
        form = self.receiving_forms().LabReceiptForm(
            data={"lab_date_received": "2026-11-12", "lab_time_received": "10:01"},
            receipt=receipt,
            now=timezone.make_aware(datetime(2026, 11, 12, 10, 0)),
        )

        self.assertFalse(form.is_valid())
        self.assertIn("lab_time_received", form.errors)


class ReceivingServiceTests(TestCase):
    def setUp(self):
        self.site = SiteData.objects.create(SiteCode="RMC", SiteName="Regional Medical Center")
        self.secretariat = User.objects.create_user("service_secretariat", password="test-pass")
        self.lab_encoder = User.objects.create_user("service_lab", password="test-pass")
        secretariat_group, _ = Group.objects.get_or_create(name=home_permissions.ROLE_SECRETARIAT)
        lab_group, _ = Group.objects.get_or_create(name=home_permissions.ROLE_LAB_ENCODER)
        self.secretariat.groups.add(secretariat_group)
        self.lab_encoder.groups.add(lab_group)

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
        receipt = self.create_receipt(
            status=ReferralReceipt.Status.ENDORSED,
            endorsed_at=timezone.make_aware(datetime(2026, 11, 12, 9, 30)),
            endorsed_by=self.secretariat,
        )

        with self.assertRaises(services.InvalidReceivingTransition):
            services.endorse_receipt(receipt_id=receipt.pk, user=self.secretariat)

        receipt.refresh_from_db()
        self.assertEqual(receipt.status, ReferralReceipt.Status.ENDORSED)
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

    def test_direct_service_calls_reject_unrelated_role(self):
        services = self.services()
        unrelated = User.objects.create_user("service_unrelated", password="test-pass")
        receipt = self.create_receipt()

        with self.assertRaises(services.ReceivingPermissionDenied):
            services.create_receipt(form=self.valid_receipt_form("DENIED-CREATE"), user=unrelated)
        with self.assertRaises(services.ReceivingPermissionDenied):
            services.endorse_receipt(receipt_id=receipt.pk, user=unrelated)

        self.assertFalse(ReferralReceipt.objects.filter(tracking_number="DENIED-CREATE").exists())
        receipt.refresh_from_db()
        self.assertEqual(receipt.status, ReferralReceipt.Status.RECEIVED)

    def test_direct_lab_service_rejects_future_timestamp(self):
        services = self.services()
        endorsed_at = timezone.make_aware(datetime(2026, 11, 12, 9, 30))
        receipt = self.create_receipt(
            status=ReferralReceipt.Status.ENDORSED,
            endorsed_at=endorsed_at,
            endorsed_by=self.secretariat,
        )

        with patch(
            "apps.receiving.services.timezone.now",
            return_value=timezone.make_aware(datetime(2026, 11, 12, 10, 0)),
        ):
            with self.assertRaises(services.InvalidReceivingTransition):
                services.confirm_lab_receipt(
                    receipt_id=receipt.pk,
                    user=self.lab_encoder,
                    lab_date_received=date(2026, 11, 12),
                    lab_time_received=time(10, 1),
                )

        receipt.refresh_from_db()
        self.assertEqual(receipt.status, ReferralReceipt.Status.ENDORSED)
        self.assertFalse(
            receipt.events.filter(event_type=ReceivingEvent.EventType.LAB_RECEIVED).exists()
        )


class ReceivingHardeningTests(TestCase):
    def setUp(self):
        self.site = SiteData.objects.create(SiteCode="HRD", SiteName="Hardening Test Site")

    def receipt_values(self, **overrides):
        values = {
            "tracking_number": "HARDEN-001",
            "site": self.site,
            "date_sent": date(2026, 11, 10),
            "date_received": date(2026, 11, 12),
            "time_received": time(8, 0),
            "status": ReferralReceipt.Status.RECEIVED,
        }
        values.update(overrides)
        return values

    def test_receipt_admin_is_read_only_for_workflow_records(self):
        model_admin = ReferralReceiptAdmin(ReferralReceipt, AdminSite())
        request = RequestFactory().get("/admin/receiving/referralreceipt/")

        readonly = set(model_admin.get_readonly_fields(request))

        self.assertEqual(
            readonly,
            {field.name for field in ReferralReceipt._meta.fields},
        )
        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_delete_permission(request))

    def test_database_rejects_endorsed_status_without_endorsement_timestamp(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ReferralReceipt.objects.create(
                    **self.receipt_values(status=ReferralReceipt.Status.ENDORSED)
                )

    def test_database_rejects_duplicate_canonical_transition_event(self):
        receipt = ReferralReceipt.objects.create(**self.receipt_values())
        occurred_at = timezone.make_aware(datetime(2026, 11, 12, 8, 0))
        ReceivingEvent.objects.create(
            receipt=receipt,
            event_type=ReceivingEvent.EventType.RECEIVED,
            occurred_at=occurred_at,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ReceivingEvent.objects.create(
                    receipt=receipt,
                    event_type=ReceivingEvent.EventType.RECEIVED,
                    occurred_at=occurred_at,
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



    def test_secretariat_default_tab_is_pending_endorsement(self):
        self.create_receipt("PENDING-001", ReferralReceipt.Status.RECEIVED)
        self.create_receipt("ENDORSED-001", ReferralReceipt.Status.ENDORSED)
        self.client.force_login(self.secretariat)
        response = self.client.get(self.route("receiving_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_tab"], "pending")
        self.assertEqual(response.context["page_obj"].paginator.count, 1)
        self.assertContains(response, "PENDING-001")
        self.assertNotContains(response, "ENDORSED-001")
        self.assertContains(response, "Pending Endorsement")

    def test_lab_encoder_default_tab_is_pending_lab_receipt(self):
        self.create_receipt("PENDING-001", ReferralReceipt.Status.RECEIVED)
        self.create_receipt("ENDORSED-001", ReferralReceipt.Status.ENDORSED)
        self.create_receipt("LAB-001", ReferralReceipt.Status.LAB_RECEIVED)
        self.client.force_login(self.lab_encoder)
        response = self.client.get(self.route("receiving_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_tab"], "pending")
        self.assertEqual(response.context["page_obj"].paginator.count, 1)
        self.assertContains(response, "ENDORSED-001")
        self.assertNotContains(response, "PENDING-001")
        self.assertNotContains(response, "LAB-001")
        self.assertContains(response, "Confirm receipt")

    def test_tabs_are_filtered_and_counts_are_not_search_dependent(self):
        self.create_receipt("PENDING-001", ReferralReceipt.Status.RECEIVED)
        self.create_receipt("ENDORSED-001", ReferralReceipt.Status.ENDORSED)
        self.create_receipt("LAB-001", ReferralReceipt.Status.LAB_RECEIVED)
        self.client.force_login(self.secretariat)
        response = self.client.get(self.route("receiving_dashboard"), {"tab": "endorsed", "q": "NO-MATCH"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_obj"].paginator.count, 0)
        self.assertEqual(
            {tab["key"]: tab["count"] for tab in response.context["receiving_tabs"]},
            {"pending": 1, "endorsed": 1, "received": 1, "all": 3},
        )

    def test_legacy_status_filter_remains_supported(self):
        matched = self.create_receipt("LEGACY-ENDORSED", ReferralReceipt.Status.ENDORSED)
        self.create_receipt("LEGACY-RECEIVED", ReferralReceipt.Status.RECEIVED)
        self.client.force_login(self.secretariat)

        response = self.client.get(
            self.route("receiving_dashboard"),
            {"status": ReferralReceipt.Status.ENDORSED},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["page_obj"].object_list), [matched])
        self.assertEqual(response.context["active_tab"], "endorsed")

    def test_explicit_tab_takes_precedence_over_legacy_status(self):
        matched = self.create_receipt("TAB-PENDING", ReferralReceipt.Status.RECEIVED)
        self.create_receipt("TAB-ENDORSED", ReferralReceipt.Status.ENDORSED)
        self.client.force_login(self.secretariat)

        response = self.client.get(
            self.route("receiving_dashboard"),
            {"tab": "pending", "status": ReferralReceipt.Status.ENDORSED},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["page_obj"].object_list), [matched])

    def test_invalid_tab_falls_back_to_pending(self):
        self.client.force_login(self.lab_encoder)
        response = self.client.get(self.route("receiving_dashboard"), {"tab": "not-a-tab"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_tab"], "pending")

    def test_sidebar_does_not_offer_settings_or_batch_creation_to_secretariat(self):
        self.client.force_login(self.secretariat)
        response = self.client.get(self.route("receiving_dashboard"))
        self.assertNotContains(response, 'href="' + reverse("batch_create_view") + '"')
        self.assertNotContains(response, 'href="' + reverse("settings_page") + '"')

    def test_sidebar_does_not_offer_settings_to_lab_encoder(self):
        self.client.force_login(self.lab_encoder)
        response = self.client.get(self.route("receiving_dashboard"))
        self.assertNotContains(response, 'href="' + reverse("settings_page") + '"')


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
        self.assertContains(response, "/static/assets/css/receiving.css")
        self.assertContains(response, 'class="receiving-page"')
        self.assertContains(response, "receiving-hero")
        self.assertContains(response, "receiving-surface")
        self.assertContains(response, "Courier batch receiving records")

    def test_receiving_forms_share_responsive_page_shell(self):
        receipt = self.create_receipt(ReferralReceipt.Status.ENDORSED)

        self.client.force_login(self.secretariat)
        create_response = self.client.get(self.route("receiving_create"))
        self.client.force_login(self.lab_encoder)
        lab_response = self.client.get(
            self.route("receiving_lab_confirm", receiving_id=receipt.receiving_id)
        )

        for response in (create_response, lab_response):
            self.assertContains(response, "/static/assets/css/receiving.css")
            self.assertContains(response, 'class="receiving-page"')
            self.assertContains(response, "receiving-hero")
            self.assertContains(response, "receiving-form-card")

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
        self.assertContains(response, "/static/assets/css/receiving.css")
        self.assertContains(response, 'class="receiving-label-page"')


class LaboratoryReceivingModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="lab-model", password="test-pass")
        self.site = SiteData.objects.create(SiteCode="RI", SiteName="Referral Institute")
        self.receipt = ReferralReceipt.objects.create(
            tracking_number="LAB-MODEL-001",
            site=self.site,
            date_sent=date(2026, 11, 18),
            date_received=date(2026, 11, 18),
            time_received=time(8, 0),
            received_by=self.user,
            endorsed_at=timezone.make_aware(datetime(2026, 11, 18, 9, 0)),
            endorsed_by=self.user,
            lab_date_received=date(2026, 11, 19),
            lab_time_received=time(8, 30),
            lab_received_by=self.user,
            status=ReferralReceipt.Status.LAB_RECEIVED,
        )

    def laboratory_receiving(self, **overrides):
        values = {
            "receipt": self.receipt,
            "specimen_condition": "Intact and properly labeled",
            "total_isolates_received": 5,
            "isolates_rejected": 1,
            "isolates_accepted": 4,
            "date_accepted": date(2026, 11, 19),
            "total_isolates_processed": 4,
            "recorded_by": self.user,
        }
        values.update(overrides)
        return LaboratoryReceiving(**values)

    def test_lab_encoder_decision_totals_must_balance_when_saved(self):
        record = self.laboratory_receiving(isolates_rejected=0)

        with self.assertRaises(ValidationError) as caught:
            record.full_clean()

        self.assertIn("isolates_accepted", caught.exception.message_dict)

    def test_zero_value_custody_placeholder_is_valid(self):
        record = self.laboratory_receiving(
            total_isolates_received=0,
            isolates_rejected=0,
            isolates_accepted=0,
            total_isolates_processed=0,
            date_accepted=None,
        )

        record.full_clean()
        record.save()

        self.assertEqual(record.receipt, self.receipt)

    def test_processed_count_cannot_exceed_accepted_count(self):
        record = self.laboratory_receiving(total_isolates_processed=5)

        with self.assertRaises(ValidationError) as caught:
            record.full_clean()

        self.assertIn("total_isolates_processed", caught.exception.message_dict)

    def test_accession_range_has_padded_reference_and_accessions(self):
        record = self.laboratory_receiving(isolates_rejected=0, isolates_accepted=5)
        record.full_clean()
        record.save()
        accession = LaboratoryAccession(
            laboratory_receiving=record,
            date_referred=date(2026, 11, 19),
            start_number=90,
            end_number=94,
            batch_number=1,
            total_batches=1,
            assigned_by=self.user,
        )

        accession.full_clean()

        self.assertEqual(accession.reference_range, "0090-0094")
        self.assertEqual(accession.isolate_count, 5)
        self.assertEqual(accession.accession_numbers[0], "26ARS_RI0090")
        self.assertEqual(accession.accession_numbers[-1], "26ARS_RI0094")

    def test_accession_ranges_cannot_overlap_within_receipt(self):
        record = self.laboratory_receiving(isolates_rejected=0, isolates_accepted=5)
        record.full_clean()
        record.save()
        LaboratoryAccession.objects.create(
            laboratory_receiving=record,
            date_referred=date(2026, 11, 19),
            start_number=90,
            end_number=92,
            batch_number=1,
            total_batches=2,
            assigned_by=self.user,
        )
        overlapping = LaboratoryAccession(
            laboratory_receiving=record,
            date_referred=date(2026, 11, 19),
            start_number=92,
            end_number=94,
            batch_number=2,
            total_batches=2,
            assigned_by=self.user,
        )

        with self.assertRaises(ValidationError) as caught:
            overlapping.full_clean()

        self.assertIn("start_number", caught.exception.message_dict)

    def test_organism_count_uses_register_group_and_cannot_exceed_range(self):
        record = self.laboratory_receiving(
            total_isolates_received=2,
            isolates_rejected=0,
            isolates_accepted=2,
            total_isolates_processed=2,
        )
        record.full_clean()
        record.save()
        accession = LaboratoryAccession.objects.create(
            laboratory_receiving=record,
            date_referred=date(2026, 11, 19),
            start_number=90,
            end_number=91,
            batch_number=1,
            total_batches=1,
            assigned_by=self.user,
        )
        first = LaboratoryOrganismCount(
            accession_range=accession,
            organism_code=LaboratoryOrganismCount.OrganismCode.NGO,
            count=1,
        )
        first.full_clean()
        first.save()
        too_many = LaboratoryOrganismCount(
            accession_range=accession,
            organism_code=LaboratoryOrganismCount.OrganismCode.SPN,
            count=2,
        )

        with self.assertRaises(ValidationError):
            too_many.full_clean()

        self.assertEqual(first.organism_group, "fastidious")


class SharedBatchCreationServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="batch-service", password="test-pass")
        SiteData.objects.create(SiteCode="RI", SiteName="Referral Institute")

    def test_creates_standard_batch_raw_accessions_and_tat(self):
        from apps.home.services.batch_creation import create_referred_batch

        result = create_referred_batch(
            site_code="RI",
            referral_date=date(2026, 11, 19),
            start_number=90,
            end_number=94,
            batch_number=1,
            total_batches=1,
            created_by=self.user,
            site_name="Referral Institute",
        )

        self.assertEqual(result.batch.bat_Batch_Code, "RI_11192026_1.1_0090-0094")
        self.assertEqual(result.batch.bat_RefNo, "0090-0094")
        self.assertEqual(result.accession_numbers[0], "26ARS_RI0090")
        self.assertEqual(result.accession_numbers[-1], "26ARS_RI0094")
        self.assertEqual(Referred_Data.objects.filter(Batch_id=result.batch).count(), 5)
        self.assertEqual(TATform.objects.get(tat_Batch_Isolates=result.batch).tat_Num_Isolate, 5)

    def test_refuses_to_overwrite_an_existing_populated_batch(self):
        from apps.home.services.batch_creation import BatchAlreadyExists, create_referred_batch

        first = create_referred_batch(
            site_code="RI",
            referral_date=date(2026, 11, 19),
            start_number=90,
            end_number=94,
            batch_number=1,
            total_batches=1,
            created_by=self.user,
        )

        with self.assertRaises(BatchAlreadyExists) as caught:
            create_referred_batch(
                site_code="RI",
                referral_date=date(2026, 11, 19),
                start_number=90,
                end_number=94,
                batch_number=1,
                total_batches=1,
                created_by=self.user,
            )

        self.assertEqual(caught.exception.batch.pk, first.batch.pk)
        self.assertEqual(Batch_Table.objects.count(), 1)

    def test_existing_create_batch_page_uses_same_existing_batch_table(self):
        group, _ = Group.objects.get_or_create(name=home_permissions.ROLE_ENCODER)
        self.user.groups.add(group)
        self.client.force_login(self.user)

        response = self.client.post(reverse("batch_create_view"), {
            "bat_SiteCode": "RI",
            "bat_Site_NameGen": "Referral Institute",
            "bat_Referral_Date": "2026-11-19",
            "bat_BatchNo": "90",
            "bat_Total_batch": "94",
            "bat_RefNo": "ignored-by-form",
        })

        self.assertEqual(response.status_code, 302)
        batch = Batch_Table.objects.get()
        self.assertEqual(batch.bat_Batch_Code, "RI_11192026_90.94_0090-0094")
        self.assertEqual(Referred_Data.objects.filter(Batch_id=batch).count(), 5)


class LaboratoryReceivingWorkspaceTests(TestCase):
    def setUp(self):
        self.lab_user = User.objects.create_user(username="lab-workspace", password="test-pass")
        group, _ = Group.objects.get_or_create(name=home_permissions.ROLE_LAB_ENCODER)
        self.lab_user.groups.add(group)
        self.site = SiteData.objects.create(SiteCode="RI", SiteName="Referral Institute")
        self.receipt = ReferralReceipt.objects.create(
            tracking_number="LAB-WORK-001", site=self.site,
            date_sent=date(2026, 11, 18), date_received=date(2026, 11, 18),
            time_received=time(8), received_by=self.lab_user,
            endorsed_at=timezone.make_aware(datetime(2026, 11, 18, 9)), endorsed_by=self.lab_user,
            lab_date_received=date(2026, 11, 19), lab_time_received=time(8, 30),
            lab_received_by=self.lab_user, status=ReferralReceipt.Status.LAB_RECEIVED,
        )
        self.lab_record = LaboratoryReceiving.objects.create(
            receipt=self.receipt, recorded_by=self.lab_user
        )
        self.client.force_login(self.lab_user)

    def test_lab_encoder_manually_saves_accepted_and_rejected_totals(self):
        response = self.client.post(
            reverse("laboratory_receiving_workspace", kwargs={"receiving_id": self.receipt.receiving_id}),
            {
                "specimen_condition": "Intact", "discrepancies": "", "remarks": "",
                "total_isolates_received": 5, "isolates_rejected": 1,
                "isolates_accepted": 4, "date_accepted": "2026-11-19",
                "total_isolates_processed": 4,
            },
        )

        self.assertRedirects(
            response,
            reverse("laboratory_receiving_workspace", kwargs={"receiving_id": self.receipt.receiving_id}),
        )
        self.lab_record.refresh_from_db()
        self.assertEqual(self.lab_record.isolates_rejected, 1)
        self.assertEqual(self.lab_record.isolates_accepted, 4)
        self.assertFalse(hasattr(self.lab_record, "is_finalized"))
        self.assertNotContains(response, "Finalize the accepted/rejected decision", status_code=302)

    def test_lab_encoder_sees_prominent_workspace_action_on_custody_record(self):
        response = self.client.get(
            reverse("receiving_detail", kwargs={"receiving_id": self.receipt.receiving_id})
        )

        self.assertContains(response, "Update Lab Receiving")
        self.assertContains(
            response,
            reverse("laboratory_receiving_workspace", kwargs={"receiving_id": self.receipt.receiving_id}),
        )

    def test_saved_range_creates_batch_in_existing_batch_table(self):
        self.lab_record.total_isolates_received = 5
        self.lab_record.isolates_accepted = 5
        self.lab_record.total_isolates_processed = 5
        self.lab_record.date_accepted = date(2026, 11, 19)
        self.lab_record.save()
        add_response = self.client.post(
            reverse("laboratory_accession_add", kwargs={"receiving_id": self.receipt.receiving_id}),
            {"date_referred": "2026-11-19", "start_number": 90, "end_number": 94,
             "batch_number": 1, "total_batches": 1},
        )
        self.assertEqual(add_response.status_code, 302)
        accession = LaboratoryAccession.objects.get(laboratory_receiving=self.lab_record)

        response = self.client.post(reverse("laboratory_batch_create", kwargs={"accession_id": accession.pk}))

        self.assertEqual(response.status_code, 302)
        accession.refresh_from_db()
        self.assertIsNotNone(accession.batch_id)
        self.assertEqual(accession.batch.bat_Batch_Code, "RI_11192026_1.1_0090-0094")
        self.assertEqual(Referred_Data.objects.filter(Batch_id=accession.batch).count(), 5)

    def test_monthly_register_and_pdf_are_available(self):
        html = self.client.get(reverse("laboratory_receiving_register"), {"month": 11, "year": 2026})
        pdf = self.client.get(reverse("laboratory_receiving_register_pdf"), {"month": 11, "year": 2026})

        self.assertEqual(html.status_code, 200)
        self.assertContains(html, "Receiving and Inventory of Referred Isolates")
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF"))

    def create_pending_accession(self):
        self.lab_record.total_isolates_received = 5
        self.lab_record.isolates_accepted = 5
        self.lab_record.date_accepted = date(2026, 11, 19)
        self.lab_record.save()
        return LaboratoryAccession.objects.create(
            laboratory_receiving=self.lab_record,
            date_referred=date(2026, 11, 19),
            start_number=1,
            end_number=5,
            batch_number=1,
            total_batches=1,
            assigned_by=self.lab_user,
        )

    def test_workspace_has_four_ordered_steps_and_fixed_organism_headings(self):
        accession = self.create_pending_accession()
        response = self.client.get(reverse(
            "laboratory_receiving_workspace",
            kwargs={"receiving_id": self.receipt.receiving_id},
        ))
        self.assertEqual(response.status_code, 200)
        text = response.content.decode("utf-8")
        labels = [
            "Step 1: Receiving and acceptance summary",
            "Step 2: Accessioning",
            "Step 3: Organism inventory",
            "Step 4: Batch generation",
        ]
        positions = [text.index(label) for label in labels]
        self.assertEqual(positions, sorted(positions))
        self.assertContains(response, "Fastidious Organisms")
        self.assertContains(response, "organism_HA")
        self.assertContains(response, reverse(
            "laboratory_accession_edit", kwargs={"accession_id": accession.pk}
        ))

    def test_organism_matrix_creates_and_replaces_counts(self):
        accession = self.create_pending_accession()
        url = reverse("laboratory_organism_matrix_save", kwargs={"accession_id": accession.pk})
        first = self.client.post(url, {"organism_HA": "2", "organism_ECO": "1"})
        self.assertEqual(first.status_code, 302)
        self.assertEqual(dict(accession.organism_counts.values_list("organism_code", "count")), {
            "HA": 2, "ECO": 1,
        })
        second = self.client.post(url, {"organism_HA": "1", "organism_ECO": ""})
        self.assertEqual(second.status_code, 302)
        self.assertEqual(dict(accession.organism_counts.values_list("organism_code", "count")), {
            "HA": 1,
        })

    def test_invalid_matrix_count_keeps_existing_values(self):
        accession = self.create_pending_accession()
        LaboratoryOrganismCount.objects.create(accession_range=accession, organism_code="HA", count=2)
        url = reverse("laboratory_organism_matrix_save", kwargs={"accession_id": accession.pk})
        self.client.post(url, {"organism_HA": "6"})
        self.assertEqual(accession.organism_counts.get(organism_code="HA").count, 2)
        self.client.post(url, {"organism_HA": "-1"})
        self.assertEqual(accession.organism_counts.get(organism_code="HA").count, 2)

    def test_ungenerated_accession_can_be_edited_and_deleted(self):
        accession = self.create_pending_accession()
        edit_url = reverse("laboratory_accession_edit", kwargs={"accession_id": accession.pk})
        edited = self.client.post(edit_url, {
            "date_referred": "2026-11-19", "start_number": 2,
            "end_number": 5, "batch_number": 1, "total_batches": 1,
        })
        self.assertEqual(edited.status_code, 302)
        accession.refresh_from_db()
        self.assertEqual(accession.reference_range, "0002-0005")
        delete_url = reverse("laboratory_accession_delete", kwargs={"accession_id": accession.pk})
        self.client.post(delete_url, {"confirmation": "wrong"})
        self.assertTrue(LaboratoryAccession.objects.filter(pk=accession.pk).exists())
        self.client.post(delete_url, {"confirmation": "DELETE"})
        self.assertFalse(LaboratoryAccession.objects.filter(pk=accession.pk).exists())

    def test_generated_accession_cannot_be_edited_or_deleted(self):
        accession = self.create_pending_accession()
        result = self.client.post(reverse("laboratory_batch_create", kwargs={"accession_id": accession.pk}))
        self.assertEqual(result.status_code, 302)
        accession.refresh_from_db()
        self.assertIsNotNone(accession.batch_id)
        edit_url = reverse("laboratory_accession_edit", kwargs={"accession_id": accession.pk})
        self.client.post(edit_url, {
            "date_referred": "2026-11-19", "start_number": 2,
            "end_number": 5, "batch_number": 1, "total_batches": 1,
        })
        self.client.post(
            reverse("laboratory_accession_delete", kwargs={"accession_id": accession.pk}),
            {"confirmation": "DELETE"},
        )
        accession.refresh_from_db()
        self.assertEqual(accession.reference_range, "0001-0005")
        self.assertIsNotNone(accession.batch_id)

    def test_secretariat_cannot_change_accessions_or_organism_counts(self):
        accession = self.create_pending_accession()
        secretariat = User.objects.create_user(username="lab-matrix-secretariat", password="test-pass")
        secretariat.groups.add(Group.objects.get_or_create(name=home_permissions.ROLE_SECRETARIAT)[0])
        self.client.force_login(secretariat)
        responses = [
            self.client.post(reverse("laboratory_accession_edit", kwargs={"accession_id": accession.pk}), {}),
            self.client.post(reverse("laboratory_accession_delete", kwargs={"accession_id": accession.pk}), {"confirmation": "DELETE"}),
            self.client.post(reverse("laboratory_organism_matrix_save", kwargs={"accession_id": accession.pk}), {"organism_HA": "2"}),
        ]
        self.assertTrue(all(response.status_code == 302 for response in responses))
        self.assertTrue(LaboratoryAccession.objects.filter(pk=accession.pk).exists())
        self.assertFalse(accession.organism_counts.exists())

    def test_batch_conflict_preview_disables_creation_before_post(self):
        accession = self.create_pending_accession()
        # An accession already assigned to another batch must be detected
        # without attempting to create another batch.
        Referred_Data.objects.create(
            AccessionNo="26ARS_RI0001",
            Batch_Code="ANOTHER_BATCH",
        )
        response = self.client.get(reverse(
            "laboratory_receiving_workspace",
            kwargs={"receiving_id": self.receipt.receiving_id},
        ))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cannot create this batch")
        self.assertContains(response, "26ARS_RI0001")
        self.assertContains(response, 'aria-disabled="true"')

    def test_batch_conflict_preview_keeps_button_enabled_without_conflicts(self):
        self.create_pending_accession()
        response = self.client.get(reverse(
            "laboratory_receiving_workspace",
            kwargs={"receiving_id": self.receipt.receiving_id},
        ))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Cannot create this batch")
        self.assertContains(response, ">Create Batch</button>")

    def test_batch_conflict_preview_detects_existing_batch_code(self):
        accession = self.create_pending_accession()
        Batch_Table.objects.create(
            bat_Batch_Code="RI_11192026_1.1_0001-0005",
            bat_Batch_Name="existing",
        )
        # An unreferenced empty batch is recoverable by create_referred_batch,
        # so an actual Raw record must exist to make the code unavailable.
        existing = Batch_Table.objects.get(bat_Batch_Code="RI_11192026_1.1_0001-0005")
        Referred_Data.objects.create(
            AccessionNo="26ARS_RI9999", Batch_id=existing, Batch_Code=existing.bat_Batch_Code,
        )
        response = self.client.get(reverse(
            "laboratory_receiving_workspace",
            kwargs={"receiving_id": self.receipt.receiving_id},
        ))
        self.assertContains(response, "Batch code already exists")
        self.assertContains(response, 'aria-disabled="true"')

    def test_workspace_does_not_render_finalize_control(self):
        response = self.client.get(
            reverse("laboratory_receiving_workspace", kwargs={"receiving_id": self.receipt.receiving_id})
        )

        self.assertNotContains(response, "Finalize the accepted/rejected decision")
        self.assertNotContains(response, 'name="is_finalized"')
