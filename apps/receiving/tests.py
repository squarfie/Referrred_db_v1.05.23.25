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

from apps.home.models import Batch_Table, Referred_Data, SiteData, TATform, Organism_List
from apps.receiving.forms import ReferralReceiptForm
from apps.laboratory import forms as laboratory_forms
from apps.laboratory import permissions as laboratory_permissions
from apps.receiving.admin import ReferralReceiptAdmin
from apps.receiving.models import (
    LaboratoryIsolate, LaboratoryIdentification, LaboratoryBiobank,
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

    def test_secretariat_receiving_queue_is_distinct_from_laboratory_queue(self):
        user = self.create_user_with_roles("sidebar-secretariat", "Secretariat")
        self.client.force_login(user)
        response = self.client.get(reverse("secretariat_receiving_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Isolate Tracking")
        self.assertContains(response, "Pending Endorsement")
        self.assertContains(response, reverse("secretariat_receiving_dashboard") + "?tab=pending")
        self.assertNotContains(response, "Pending Lab Receipt")
        denied = self.client.get(reverse("laboratory_receiving_dashboard"))
        self.assertEqual(denied.status_code, 302)

    def test_lab_receiving_queue_is_distinct_from_secretariat_queue(self):
        user = self.create_user_with_roles("sidebar-lab-encoder", home_permissions.ROLE_LAB_ENCODER)
        self.client.force_login(user)
        response = self.client.get(reverse("laboratory_receiving_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Receiving &amp; Accessioning")
        self.assertContains(response, "Pending Lab Receipt")
        self.assertContains(response, reverse("laboratory_receiving_dashboard") + "?tab=pending")
        self.assertNotContains(response, "Pending Endorsement")
        shared = self.client.get(reverse("secretariat_receiving_dashboard"))
        self.assertEqual(shared.status_code, 200)
        self.assertFalse(shared.context["can_manage_secretariat"])

    def test_admin_can_view_both_receiving_queues(self):
        user = self.create_user_with_roles("sidebar-admin", home_permissions.ROLE_ADMIN)
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("secretariat_receiving_dashboard")).status_code, 200)
        self.assertEqual(self.client.get(reverse("laboratory_receiving_dashboard")).status_code, 200)

    def test_lab_staff_running_tat_and_wgs_visibility_remains_read_only(self):
        user = self.create_user_with_roles("sidebar-lab-staff", home_permissions.ROLE_LAB_STAFF)
        flags = home_permissions.role_flags(user)
        self.assertTrue(flags["nav_can_view_wgs"])
        self.assertTrue(flags["nav_can_view_reports"])
        self.assertFalse(flags["nav_can_upload_wgs"])
        self.assertFalse(flags["can_wgs_create"])

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
        self.assertFalse(laboratory_permissions.can_confirm_lab_receipt(user))

    def test_lab_encoder_can_read_and_confirm_but_not_manage_secretariat_receiving(self):
        permissions = self.receiving_permissions()
        user = self.create_user_with_roles(
            "lab_encoder_permissions",
            home_permissions.ROLE_LAB_ENCODER,
        )

        self.assertTrue(permissions.can_read_receiving(user))
        self.assertFalse(permissions.can_manage_secretariat_receiving(user))
        self.assertTrue(laboratory_permissions.can_confirm_lab_receipt(user))

    def test_admin_can_manage_secretariat_and_confirm_laboratory_receipt(self):
        permissions = self.receiving_permissions()
        user = self.create_user_with_roles("admin_permissions", is_superuser=True)

        self.assertTrue(permissions.can_read_receiving(user))
        self.assertTrue(permissions.can_manage_secretariat_receiving(user))
        self.assertTrue(laboratory_permissions.can_confirm_lab_receipt(user))

    def test_verifier_has_read_only_receiving_access(self):
        permissions = self.receiving_permissions()
        user = self.create_user_with_roles(
            "verifier_permissions",
            home_permissions.ROLE_VERIFIER,
        )

        self.assertTrue(permissions.can_read_receiving(user))
        self.assertFalse(permissions.can_manage_secretariat_receiving(user))
        self.assertFalse(laboratory_permissions.can_confirm_lab_receipt(user))

    def test_multiple_roles_grant_union_of_receiving_permissions(self):
        permissions = self.receiving_permissions()
        user = self.create_user_with_roles(
            "multi_receiving_permissions",
            "Secretariat",
            home_permissions.ROLE_LAB_ENCODER,
        )

        self.assertTrue(permissions.can_read_receiving(user))
        self.assertTrue(permissions.can_manage_secretariat_receiving(user))
        self.assertTrue(laboratory_permissions.can_confirm_lab_receipt(user))


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
        form = laboratory_forms.LabReceiptForm(
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
        form = laboratory_forms.LabReceiptForm(
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
        form = laboratory_forms.LabReceiptForm(
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

    def test_verifier_can_read_dashboard_and_detail_without_write_actions(self):
        receipt = self.create_receipt()
        self.client.force_login(self.verifier)

        dashboard = self.client.get(self.route("receiving_dashboard"))
        detail = self.client.get(
            self.route("receiving_detail", receiving_id=receipt.receiving_id)
        )

        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, receipt.tracking_number)
        self.assertFalse(dashboard.context["can_manage_secretariat"])
        self.assertFalse(detail.context["can_endorse"])
        self.assertFalse(detail.context["can_permanently_delete"])

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

    def test_lab_encoder_and_admin_can_confirm_receipt_but_secretariat_cannot(self):
        receipt = self.create_receipt(status=ReferralReceipt.Status.ENDORSED)
        url = self.route("receiving_lab_confirm", receiving_id=receipt.receiving_id)
        payload = {"lab_date_received": "2026-11-12", "lab_time_received": "10:00"}

        self.client.force_login(self.secretariat)
        denied = self.client.post(url, payload)
        self.assertEqual(denied.status_code, 302)
        receipt.refresh_from_db()
        self.assertEqual(receipt.status, ReferralReceipt.Status.ENDORSED)

        self.client.force_login(self.lab_encoder)
        confirmed = self.client.post(url, payload)
        self.assertRedirects(
            confirmed,
            self.route("laboratory_receiving_workspace", receiving_id=receipt.receiving_id),
            fetch_redirect_response=False,
        )
        receipt.refresh_from_db()
        self.assertEqual(receipt.status, ReferralReceipt.Status.LAB_RECEIVED)
        self.assertEqual(receipt.lab_received_by, self.lab_encoder)

    def test_admin_can_confirm_endorsed_laboratory_receipt(self):
        receipt = self.create_receipt(status=ReferralReceipt.Status.ENDORSED)
        url = self.route("receiving_lab_confirm", receiving_id=receipt.receiving_id)
        self.client.force_login(self.admin)
        response = self.client.post(url, {
            "lab_date_received": "2026-11-12", "lab_time_received": "10:00",
        })
        self.assertRedirects(
            response,
            self.route("laboratory_receiving_workspace", receiving_id=receipt.receiving_id),
            fetch_redirect_response=False,
        )
        receipt.refresh_from_db()
        self.assertEqual(receipt.status, ReferralReceipt.Status.LAB_RECEIVED)
        self.assertEqual(receipt.lab_received_by, self.admin)

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
        self.assertContains(response, "Isolates Received")
        self.assertContains(response, "Endorsed to Laboratory")
        self.assertContains(response, "Received by Laboratory")
        # The sidebar now routes Secretariat users to their dedicated worklist.
        self.assertContains(
            response, 'href="/receiving/secretariat/"', html=False
        )
        self.assertContains(response, "/static/assets/css/receiving.css")
        self.assertContains(response, 'class="receiving-page"')
        self.assertContains(response, "receiving-hero")
        self.assertContains(response, "receiving-surface")
        self.assertContains(response, "Courier batch receiving isolates")

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

    def test_secretariat_receipt_links_never_expose_laboratory_worksheet(self):
        receipt = self.create_receipt(ReferralReceipt.Status.LAB_RECEIVED)
        self.client.force_login(self.secretariat)
        response = self.client.get(self.route("receiving_detail", receiving_id=receipt.receiving_id))
        self.assertContains(response, 'href="' + self.route("secretariat_receiving_dashboard") + '"', html=False)
        self.assertNotContains(response, "Open laboratory worksheet")
        self.assertNotContains(response, "Confirm laboratory receipt")
        self.assertNotContains(response, "Update Lab Receiving")
        self.assertNotContains(response, "The LAB Encoder can now")

    def test_laboratory_receipt_detail_buttons_stay_in_laboratory_queue(self):
        receipt = self.create_receipt(ReferralReceipt.Status.ENDORSED)
        self.client.force_login(self.lab_encoder)
        lab_url = self.route("laboratory_receiving_detail", receiving_id=receipt.receiving_id)
        lab_queue_url = self.route("laboratory_receiving_dashboard")
        response = self.client.get(lab_url)
        self.assertContains(response, 'href="' + lab_queue_url + '"', html=False)
        self.assertContains(response, 'href="' + self.route("receiving_lab_confirm", receiving_id=receipt.receiving_id) + '"', html=False)
        self.assertNotContains(response, "Endorse to laboratory")
        self.assertNotContains(response, "New receipt")
        self.assertNotContains(response, "Print batch label")
        self.assertNotContains(response, "Open laboratory worksheet")

    def test_lab_receipt_confirm_form_back_and_cancel_link_to_lab_detail(self):
        receipt = self.create_receipt(ReferralReceipt.Status.ENDORSED)
        self.client.force_login(self.lab_encoder)
        response = self.client.get(self.route("receiving_lab_confirm", receiving_id=receipt.receiving_id))
        target = self.route("laboratory_receiving_detail", receiving_id=receipt.receiving_id)
        self.assertContains(response, 'href="' + target + '"', count=2, html=False)
        self.assertNotContains(response, 'href="' + self.route("receiving_detail", receiving_id=receipt.receiving_id) + '"', html=False)

    def test_lab_dashboard_open_links_to_lab_specific_record(self):
        receipt = self.create_receipt(ReferralReceipt.Status.ENDORSED)
        self.client.force_login(self.lab_encoder)
        response = self.client.get(self.route("laboratory_receiving_dashboard"))
        self.assertContains(response, 'href="' + self.route("laboratory_receiving_detail", receiving_id=receipt.receiving_id) + '"', html=False)
        self.assertNotContains(response, 'href="' + self.route("receiving_detail", receiving_id=receipt.receiving_id) + '"', html=False)

    def test_secretariat_dashboard_open_links_to_secretariat_record(self):
        receipt = self.create_receipt(ReferralReceipt.Status.RECEIVED)
        self.client.force_login(self.secretariat)
        response = self.client.get(self.route("secretariat_receiving_dashboard"))
        self.assertContains(response, 'href="' + self.route("receiving_detail", receiving_id=receipt.receiving_id) + '"', html=False)
        self.assertNotContains(response, 'href="' + self.route("laboratory_receiving_detail", receiving_id=receipt.receiving_id) + '"', html=False)

    def test_lab_staff_legacy_receiving_url_and_detail_use_laboratory_views(self):
        lab_staff = self.create_user("template_laboratory_staff", home_permissions.ROLE_LAB_STAFF)
        receipt = self.create_receipt(ReferralReceipt.Status.LAB_RECEIVED)
        self.client.force_login(lab_staff)
        dashboard = self.client.get(self.route("receiving_dashboard"))
        self.assertEqual(dashboard.status_code, 200)
        self.assertContains(dashboard, "Pending Lab Receipt")
        self.assertNotContains(dashboard, "New receipt")
        legacy = self.client.get(self.route("receiving_detail", receiving_id=receipt.receiving_id))
        self.assertRedirects(legacy, self.route(
            "laboratory_receiving_detail", receiving_id=receipt.receiving_id
        ), fetch_redirect_response=False)
        lab_detail = self.client.get(self.route("laboratory_receiving_detail", receiving_id=receipt.receiving_id))
        self.assertContains(lab_detail, "Open laboratory worksheet")
        self.assertNotContains(lab_detail, "Print batch label")
        self.assertNotContains(lab_detail, "Endorse to laboratory")

    def test_monthly_register_back_and_access_remain_laboratory_only(self):
        self.client.force_login(self.lab_encoder)
        response = self.client.get(self.route("laboratory_receiving_register"))
        self.assertContains(response,
            'href="' + self.route("laboratory_receiving_dashboard") + '"', html=False)
        self.client.force_login(self.secretariat)
        denied = self.client.get(self.route("laboratory_receiving_register"))
        self.assertRedirects(denied, self.route("home"), fetch_redirect_response=False)
        secretariat_dashboard = self.client.get(self.route("secretariat_receiving_dashboard"))
        self.assertNotContains(secretariat_dashboard, "Monthly Inventory")
        self.assertNotContains(secretariat_dashboard, "Open worksheet")

    def test_secretariat_new_receipt_back_and_cancel_stay_secretariat(self):
        self.client.force_login(self.secretariat)
        response = self.client.get(self.route("receiving_create"))
        self.assertContains(response,
            'href="' + self.route("secretariat_receiving_dashboard") + '"',
            count=3, html=False)

    def test_lab_encoder_sees_only_confirmation_for_endorsed_receipt(self):
        receipt = self.create_receipt(ReferralReceipt.Status.ENDORSED)
        self.client.force_login(self.lab_encoder)

        legacy = self.client.get(
            self.route("receiving_detail", receiving_id=receipt.receiving_id)
        )
        self.assertRedirects(
            legacy,
            self.route("laboratory_receiving_detail", receiving_id=receipt.receiving_id),
            fetch_redirect_response=False,
        )
        response = self.client.get(
            self.route("laboratory_receiving_detail", receiving_id=receipt.receiving_id)
        )
        self.assertContains(response, "Confirm laboratory receipt")
        self.assertContains(response, 'href="' + self.route("laboratory_receiving_dashboard") + '"', html=False)
        self.assertNotContains(response, "Print batch label")
        self.assertNotContains(response, "Endorse to laboratory")

    def test_admin_has_secretariat_actions_and_lab_confirmation(self):
        receipt = self.create_receipt(ReferralReceipt.Status.ENDORSED)
        self.client.force_login(self.admin)

        response = self.client.get(
            self.route("receiving_detail", receiving_id=receipt.receiving_id)
        )

        self.assertContains(response, "Print batch label")
        self.assertContains(response, "Confirm laboratory receipt")

    def test_terminal_lab_received_detail_has_no_transition_action(self):
        receipt = self.create_receipt(ReferralReceipt.Status.LAB_RECEIVED)
        self.client.force_login(self.secretariat)

        response = self.client.get(
            self.route("receiving_detail", receiving_id=receipt.receiving_id)
        )

        self.assertContains(response, "Laboratory custody confirmed")
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

        # A user holding both roles keeps both capabilities, but each
        # receiving process now has a dedicated queue.
        secretariat_dashboard = self.client.get(
            self.route("secretariat_receiving_dashboard")
        )
        laboratory_dashboard = self.client.get(
            self.route("laboratory_receiving_dashboard")
        )
        detail = self.client.get(
            self.route("receiving_detail", receiving_id=receipt.receiving_id)
        )

        self.assertContains(secretariat_dashboard, "New receipt")
        self.assertContains(secretariat_dashboard, "Pending Endorsement")
        self.assertNotContains(secretariat_dashboard, "Confirm receipt")
        self.assertContains(laboratory_dashboard, "Pending Lab Receipt")
        self.assertContains(laboratory_dashboard, "Confirm receipt")
        self.assertNotContains(laboratory_dashboard, "New receipt")
        self.assertContains(detail, "Print batch label")
        self.assertContains(detail, "Confirm laboratory receipt")
        lab_detail = self.client.get(
            self.route("laboratory_receiving_detail", receiving_id=receipt.receiving_id)
        )
        self.assertContains(lab_detail, "Confirm laboratory receipt")
        self.assertNotContains(lab_detail, "Endorse to laboratory")

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
            activity.index("Isolates Received"),
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


class ReceivingDeletionGuardTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("delete-admin", "delete-admin@example.org", "test-pass")
        self.secretariat = User.objects.create_user("delete-secretariat", password="test-pass")
        self.secretariat.groups.add(Group.objects.get_or_create(name=home_permissions.ROLE_SECRETARIAT)[0])
        self.lab_user = User.objects.create_user("delete-lab", password="test-pass")
        self.lab_user.groups.add(Group.objects.get_or_create(name=home_permissions.ROLE_LAB_ENCODER)[0])
        self.site = SiteData.objects.create(SiteCode="DLP", SiteName="Delete test site")

    def receipt(self, status=ReferralReceipt.Status.RECEIVED):
        data = {
            "tracking_number": f"DELETE-{status}", "site": self.site,
            "date_sent": date(2026, 11, 18), "date_received": date(2026, 11, 18),
            "time_received": time(8), "received_by": self.secretariat,
            "status": status,
        }
        if status != ReferralReceipt.Status.RECEIVED:
            data.update(endorsed_by=self.secretariat,
                        endorsed_at=timezone.make_aware(datetime(2026, 11, 18, 9)))
        if status == ReferralReceipt.Status.LAB_RECEIVED:
            data.update(lab_date_received=date(2026, 11, 19),
                        lab_time_received=time(9), lab_received_by=self.lab_user)
        return ReferralReceipt.objects.create(**data)

    def delete_url(self, receipt):
        return reverse("receiving_delete_permanently", kwargs={"receiving_id": receipt.receiving_id})

    def test_secretariat_can_delete_received_mistake_from_dashboard(self):
        receipt = self.receipt()
        self.client.force_login(self.secretariat)
        dashboard = self.client.get(reverse("secretariat_receiving_dashboard"))
        self.assertContains(dashboard, self.delete_url(receipt))
        response = self.client.post(self.delete_url(receipt), {"confirmation": "DELETE PERMANENTLY"})
        self.assertRedirects(response, reverse("secretariat_receiving_dashboard"), fetch_redirect_response=False)
        self.assertFalse(ReferralReceipt.objects.filter(pk=receipt.pk).exists())

    def test_secretariat_can_delete_endorsed_but_unacknowledged_record(self):
        receipt = self.receipt(ReferralReceipt.Status.ENDORSED)
        self.client.force_login(self.secretariat)
        response = self.client.post(self.delete_url(receipt), {"confirmation": "DELETE PERMANENTLY"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ReferralReceipt.objects.filter(pk=receipt.pk).exists())

    def test_admin_can_remove_lab_received_test_record_without_accessions(self):
        receipt = self.receipt(ReferralReceipt.Status.LAB_RECEIVED)
        LaboratoryReceiving.objects.create(receipt=receipt, recorded_by=self.lab_user)
        self.client.force_login(self.admin)
        dashboard = self.client.get(reverse("secretariat_receiving_dashboard") + "?tab=received")
        self.assertContains(dashboard, self.delete_url(receipt))
        self.client.post(self.delete_url(receipt), {"confirmation": "DELETE PERMANENTLY"})
        self.assertFalse(ReferralReceipt.objects.filter(pk=receipt.pk).exists())
        self.assertFalse(LaboratoryReceiving.objects.filter(receipt_id=receipt.pk).exists())

    def test_secretariat_cannot_remove_lab_received_record(self):
        receipt = self.receipt(ReferralReceipt.Status.LAB_RECEIVED)
        LaboratoryReceiving.objects.create(receipt=receipt, recorded_by=self.lab_user)
        self.client.force_login(self.secretariat)
        dashboard = self.client.get(reverse("secretariat_receiving_dashboard") + "?tab=received")
        self.assertNotContains(dashboard, self.delete_url(receipt))
        self.client.post(self.delete_url(receipt), {"confirmation": "DELETE PERMANENTLY"})
        self.assertTrue(ReferralReceipt.objects.filter(pk=receipt.pk).exists())

    def test_admin_cannot_delete_receipt_with_accession_range(self):
        receipt = self.receipt(ReferralReceipt.Status.LAB_RECEIVED)
        lab_record = LaboratoryReceiving.objects.create(receipt=receipt, recorded_by=self.lab_user)
        accession = LaboratoryAccession.objects.create(
            laboratory_receiving=lab_record, date_referred=date(2026, 11, 19),
            start_number=1, end_number=1, batch_number=1, total_batches=1,
            assigned_by=self.lab_user,
        )
        self.client.force_login(self.admin)
        dashboard = self.client.get(reverse("secretariat_receiving_dashboard") + "?tab=received")
        self.assertNotContains(dashboard, self.delete_url(receipt))
        self.client.post(self.delete_url(receipt), {"confirmation": "DELETE PERMANENTLY"})
        self.assertTrue(ReferralReceipt.objects.filter(pk=receipt.pk).exists())
        self.assertTrue(LaboratoryAccession.objects.filter(pk=accession.pk).exists())

    def test_confirmation_and_role_restrictions_are_enforced_on_post(self):
        receipt = self.receipt()
        self.client.force_login(self.secretariat)
        self.client.post(self.delete_url(receipt), {"confirmation": "DELETE"})
        self.assertTrue(ReferralReceipt.objects.filter(pk=receipt.pk).exists())
        self.client.force_login(self.lab_user)
        self.client.post(self.delete_url(receipt), {"confirmation": "DELETE PERMANENTLY"})
        self.assertTrue(ReferralReceipt.objects.filter(pk=receipt.pk).exists())


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
        Organism_List.objects.create(Whonet_Org_Code="KPN", Organism="Klebsiella pneumoniae")
        Organism_List.objects.create(Whonet_Org_Code="ECO", Organism="Escherichia coli")
        self.client.force_login(self.lab_user)

    def test_main_lab_return_bookmark_moves_to_processing_history(self):
        response = self.client.get(reverse("laboratory_receiving_dashboard") + "?tab=returned")
        self.assertRedirects(response, reverse("laboratory_processing_worklist") + "?history=1&q=", fetch_redirect_response=False)

    def test_main_lab_return_search_is_preserved_in_processing_redirect(self):
        response = self.client.get(reverse("laboratory_receiving_dashboard") + "?tab=returned&q=26ARS_RI0001")
        self.assertRedirects(response, reverse("laboratory_processing_worklist") + "?history=1&q=26ARS_RI0001", fetch_redirect_response=False)

    def test_receiving_queue_does_not_duplicate_processing_history(self):
        response = self.client.get(reverse("laboratory_receiving_dashboard") + "?tab=received")
        self.assertNotContains(response, "Returned from Main Lab")
        self.assertEqual(response.context["page_obj"].paginator.count, 1)

    def test_main_lab_return_tab_not_shown_to_secretariat(self):
        secretariat = User.objects.create_user(username="returns-secretariat", password="test-pass")
        secretariat.groups.add(Group.objects.get_or_create(name=home_permissions.ROLE_SECRETARIAT)[0])
        self.client.force_login(secretariat)
        response = self.client.get(reverse("secretariat_receiving_dashboard"))
        self.assertNotContains(response, "Returned from Main Lab")
        self.assertNotContains(response, "Main Laboratory returned isolate records")
        denied = self.client.get(reverse("laboratory_receiving_dashboard") + "?tab=returned")
        self.assertRedirects(denied, reverse("home"), fetch_redirect_response=False)

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
            reverse("laboratory_receiving_detail", kwargs={"receiving_id": self.receipt.receiving_id})
        )

        self.assertContains(response, "Open laboratory worksheet")
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
            "Step 4: Revival &amp; Processing",
            "Step 5: Batch generation",
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

    def test_batch_creation_rejects_raw_record_with_code_but_null_batch_id(self):
        accession = self.create_pending_accession()
        Referred_Data.objects.create(
            AccessionNo="26ARS_RI0001",
            Batch_Code="OTHER_EXISTING_BATCH",
            Batch_id=None,
        )
        response = self.client.post(
            reverse("laboratory_batch_create", kwargs={"accession_id": accession.pk})
        )
        self.assertEqual(response.status_code, 302)
        accession.refresh_from_db()
        self.assertIsNone(accession.batch_id)
        self.assertEqual(
            Referred_Data.objects.get(AccessionNo="26ARS_RI0001").Batch_Code,
            "OTHER_EXISTING_BATCH",
        )

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

    def _queue_and_endorse_to_main_lab(self, accession):
        self.assertEqual(self.client.post(
            reverse("laboratory_endorse_id", kwargs={"accession_id": accession.pk})
        ).status_code, 302)
        self.assertEqual(
            accession.isolate_records.filter(status=LaboratoryIsolate.Status.ID_PENDING).count(),
            accession.isolate_count,
        )

    def _return_all_cassettes_and_queue_revival(self, accession):
        from apps.receiving.models import LaboratoryIdentification
        for isolate in accession.isolate_records.all():
            isolate.status = LaboratoryIsolate.Status.ID_RETURNED
            isolate.acknowledged_at = timezone.now()
            isolate.returned_at = timezone.now()
            isolate.returned_by = self.lab_user
            isolate.save(update_fields=("status", "acknowledged_at", "returned_at", "returned_by"))
            if not isolate.identifications.filter(cassette_reference__gt="").exists():
                LaboratoryIdentification.objects.create(
                    isolate=isolate, organism_reported="Klebsiella pneumoniae",
                    whonet_organism_code="KPN", cassette_reference="Cassette-001",
                    recorded_by=self.lab_user,
                )
        response = self.client.post(reverse("laboratory_batch_transition", kwargs={
            "accession_id": accession.pk, "action": "queue",
        }))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            accession.isolate_records.filter(status=LaboratoryIsolate.Status.REVIVAL_QUEUED).count(),
            accession.isolate_count,
        )

    def test_id_worklist_groups_isolates_by_accession_batch(self):
        accession = self.create_pending_accession()
        self._queue_and_endorse_to_main_lab(accession)
        response = self.client.get(reverse("laboratory_worklist"))
        self.assertContains(response, "Open Batch")
        self.assertContains(response, reverse("laboratory_batch_detail", kwargs={"accession_id": accession.pk}))
        self.assertEqual(len(response.context["batches"]), 1)
        self.assertEqual(response.context["batches"][0]["count"], 5)
        detail = self.client.get(reverse("laboratory_batch_detail", kwargs={"accession_id": accession.pk}))
        self.assertEqual(detail.status_code, 200)
        for item in accession.isolate_records.all():
            self.assertContains(detail, item.accession_number)

    def test_isolate_detail_navigates_previous_and_next_within_own_batch(self):
        accession = self.create_pending_accession()
        self._queue_and_endorse_to_main_lab(accession)
        first, middle, last = list(accession.isolate_records.order_by("accession_number"))[0], list(accession.isolate_records.order_by("accession_number"))[2], list(accession.isolate_records.order_by("accession_number"))[-1]
        response = self.client.get(reverse("laboratory_isolate_detail", kwargs={"isolate_id": middle.pk}))
        self.assertContains(response, "Previous Isolate")
        self.assertContains(response, "Next Isolate")
        first_response = self.client.get(reverse("laboratory_isolate_detail", kwargs={"isolate_id": first.pk}))
        self.assertNotContains(first_response, "Previous Isolate")
        last_response = self.client.get(reverse("laboratory_isolate_detail", kwargs={"isolate_id": last.pk}))
        self.assertNotContains(last_response, "Next Isolate")

    def test_one_returned_cassette_can_reach_biobank_while_siblings_wait_for_id(self):
        accession = self.create_pending_accession()
        self._queue_and_endorse_to_main_lab(accession)
        isolate = accession.isolate_records.order_by("accession_number").first()
        isolate.status = LaboratoryIsolate.Status.ID_RETURNED
        isolate.returned_at = timezone.now()
        isolate.returned_by = self.lab_user
        isolate.save(update_fields=("status", "returned_at", "returned_by"))
        LaboratoryIdentification.objects.create(
            isolate=isolate, organism_reported="Klebsiella pneumoniae",
            cassette_reference="CASSETTE-1", recorded_by=self.lab_user,
        )
        url = reverse("laboratory_isolate_detail", kwargs={"isolate_id": isolate.pk})
        self.assertContains(self.client.get(url), "Record revival outcome")
        for action, target in (
            ("queue", LaboratoryIsolate.Status.REVIVAL_QUEUED),
            ("start", LaboratoryIsolate.Status.REVIVAL_IN_PROGRESS),
            ("complete", LaboratoryIsolate.Status.REVIVED),
        ):
            self.client.post(reverse("laboratory_isolate_revival", kwargs={
                "isolate_id": isolate.pk, "action": action,
            }))
            isolate.refresh_from_db()
            self.assertEqual(isolate.status, target)
        self.assertEqual(
            accession.isolate_records.filter(status=LaboratoryIsolate.Status.ID_PENDING).count(), 4
        )
        self.assertContains(self.client.get(url), 'name="final_organism_code"')
        self.client.post(reverse("laboratory_review_id", kwargs={"isolate_id": isolate.pk}), {
            "final_organism": "Klebsiella pneumoniae", "final_organism_code": "KPN",
        })
        isolate.refresh_from_db()
        self.assertEqual(isolate.status, LaboratoryIsolate.Status.ID_REVIEWED)
        self.client.post(reverse("laboratory_lyophilization_complete", kwargs={"isolate_id": isolate.pk}), {
            "date_harvested": "2026-11-19",
            "date_lyophilized": "2026-11-19",
            "whonet_code": "KPN", "whonet_organism_name": "Klebsiella pneumoniae",
        })
        isolate.refresh_from_db()
        self.assertEqual(isolate.status, LaboratoryIsolate.Status.READY_FOR_BIOBANK)
        self.assertContains(self.client.get(url), "Save biobank inventory")
        self.client.post(reverse("laboratory_biobank_save", kwargs={"isolate_id": isolate.pk}), {
            "lyo_box_name": "Test storage", "lyo_position": "A1",
        })
        isolate.refresh_from_db()
        self.assertEqual(isolate.status, LaboratoryIsolate.Status.BIOBANKED)
        self.assertEqual(isolate.biobank.lyo_position, "A1")

    def test_per_isolate_revival_is_post_only_and_requires_cassette_and_role(self):
        accession = self.create_pending_accession()
        self._queue_and_endorse_to_main_lab(accession)
        isolate = accession.isolate_records.first()
        target = reverse("laboratory_isolate_revival", kwargs={"isolate_id": isolate.pk, "action": "queue"})
        self.assertEqual(self.client.get(target).status_code, 405)
        self.client.post(target)
        isolate.refresh_from_db()
        self.assertEqual(isolate.status, LaboratoryIsolate.Status.ID_PENDING)
        isolate.status = LaboratoryIsolate.Status.ID_RETURNED
        isolate.save(update_fields=("status",))
        self.client.post(target)
        isolate.refresh_from_db()
        self.assertEqual(isolate.status, LaboratoryIsolate.Status.ID_RETURNED)
        isolate.returned_at = timezone.now()
        isolate.returned_by = self.lab_user
        isolate.save(update_fields=("returned_at", "returned_by"))
        LaboratoryIdentification.objects.create(
            isolate=isolate, organism_reported="Klebsiella pneumoniae",
            cassette_reference="C1", recorded_by=self.lab_user,
        )
        staff = User.objects.create_user(username="per-isolate-staff", password="test-pass")
        staff.groups.add(Group.objects.get_or_create(name=home_permissions.ROLE_LAB_STAFF)[0])
        self.client.force_login(staff)
        self.client.post(target)
        isolate.refresh_from_db()
        self.assertEqual(isolate.status, LaboratoryIsolate.Status.ID_RETURNED)

    def test_create_batch_remains_visible_after_isolates_handed_to_main_lab(self):
        accession = self.create_pending_accession()
        self._queue_and_endorse_to_main_lab(accession)
        response = self.client.get(reverse("laboratory_receiving_workspace", kwargs={
            "receiving_id": self.receipt.receiving_id,
        }))
        self.assertContains(response, "Create Batch")
        self.assertContains(response, reverse("laboratory_batch_create", kwargs={"accession_id": accession.pk}))
        self.client.post(reverse("laboratory_batch_create", kwargs={"accession_id": accession.pk}))
        accession.refresh_from_db()
        self.assertIsNotNone(accession.batch_id)

    def test_id_handoff_and_review_follow_roles(self):
        accession = self.create_pending_accession()
        self._queue_and_endorse_to_main_lab(accession)
        isolate = accession.isolate_records.order_by("accession_number").first()
        staff = User.objects.create_user(username="id-laboratory-staff", password="test-pass")
        staff.groups.add(Group.objects.get_or_create(name=home_permissions.ROLE_LAB_STAFF)[0])
        self.client.force_login(staff)
        self.assertEqual(self.client.get(reverse("laboratory_worklist")).status_code, 200)
        self.assertEqual(self.client.get(reverse("laboratory_isolate_detail", kwargs={"isolate_id": isolate.pk})).status_code, 200)

        denial = self.client.post(reverse("laboratory_accession_add",
            kwargs={"receiving_id": self.receipt.receiving_id}), {})
        self.assertEqual(denial.status_code, 302)

        self.client.post(reverse("laboratory_acknowledge_id", kwargs={"isolate_id": isolate.pk}))
        isolate.refresh_from_db()
        self.assertEqual(isolate.status, LaboratoryIsolate.Status.ID_TESTING)
        self.client.post(reverse("laboratory_record_id", kwargs={"isolate_id": isolate.pk}), {
            "organism_reported": "Klebsiella pneumoniae", "instrument": "ID system",
            "cassette_reference": "Cassette-001",
        })
        self.assertEqual(isolate.identifications.count(), 1)
        self.client.post(reverse("laboratory_return_id", kwargs={"isolate_id": isolate.pk}))
        isolate.refresh_from_db()
        self.assertEqual(isolate.status, LaboratoryIsolate.Status.ID_RETURNED)
        self.client.post(reverse("laboratory_review_id", kwargs={"isolate_id": isolate.pk}),
                         {"final_organism": "Klebsiella pneumoniae"})
        isolate.refresh_from_db()
        self.assertEqual(isolate.status, LaboratoryIsolate.Status.ID_RETURNED)
        self.client.post(reverse("laboratory_biobank_save", kwargs={"isolate_id": isolate.pk}),
                         {"box_position": "B4"})
        self.assertFalse(LaboratoryBiobank.objects.filter(isolate=isolate).exists())

        self.client.force_login(self.lab_user)
        # Every cassette in the accession range must be returned before revival.
        # The first isolate already returned by Main Lab; create the other
        # handoff evidence without modifying its existing result.
        for other in accession.isolate_records.exclude(pk=isolate.pk):
            other.status = LaboratoryIsolate.Status.ID_RETURNED
            other.returned_at = timezone.now()
            other.returned_by = self.lab_user
            other.save(update_fields=("status", "returned_at", "returned_by"))
            LaboratoryIdentification.objects.create(
                isolate=other, organism_reported="Klebsiella pneumoniae",
                cassette_reference="Cassette-002", recorded_by=self.lab_user,
            )
        for action in ("queue", "start", "complete"):
            self.client.post(reverse("laboratory_batch_transition", kwargs={
                "accession_id": accession.pk, "action": action,
            }))
        isolate.refresh_from_db()
        self.assertEqual(isolate.status, LaboratoryIsolate.Status.REVIVED)
        self.client.post(reverse("laboratory_review_id", kwargs={"isolate_id": isolate.pk}),
                         {"final_organism": "Klebsiella pneumoniae"})
        isolate.refresh_from_db()
        self.assertEqual(isolate.status, LaboratoryIsolate.Status.ID_REVIEWED)
        self.client.post(reverse("laboratory_lyophilization_complete", kwargs={"isolate_id": isolate.pk}), {
            "date_harvested": "2026-11-19", "date_lyophilized": "2026-11-19", "whonet_code": "KPN",
        })
        isolate.refresh_from_db()
        self.assertEqual(isolate.status, LaboratoryIsolate.Status.READY_FOR_BIOBANK)
        self.client.force_login(staff)
        self.client.post(reverse("laboratory_biobank_save", kwargs={"isolate_id": isolate.pk}), {
            "storage_location": "Freezer A", "storage_box": "Box 12",
            "box_position": "B4", "container": "Cryovial", "preservation_method": "Freezing",
        })
        isolate.refresh_from_db()
        self.assertEqual(isolate.status, LaboratoryIsolate.Status.BIOBANKED)
        self.assertEqual(isolate.biobank.storage_box, "Box 12")
        sticker = self.client.get(reverse("laboratory_biobank_label", kwargs={"isolate_id": isolate.pk}))
        self.assertEqual(sticker.status_code, 200)
        self.assertContains(sticker, "KPN")
        self.assertContains(sticker, isolate.accession_number)
        self.assertContains(sticker, "11/19/2026")
        self.assertNotContains(sticker, "Klebsiella pneumoniae")
        self.assertNotContains(sticker, "Freezer A")

    def test_revival_failed_retry_and_nonviable_are_audited(self):
        accession = self.create_pending_accession()
        from apps.laboratory.workflow import WorkflowError, retry_revival, complete_revival_isolate
        self.client.post(reverse("laboratory_endorse_id", kwargs={"accession_id": accession.pk}))
        self._return_all_cassettes_and_queue_revival(accession)
        self.client.post(reverse("laboratory_batch_transition",
            kwargs={"accession_id": accession.pk, "action": "start"}))
        first, second = list(accession.isolate_records.order_by("accession_number")[:2])
        failed = reverse("laboratory_revival_failed", kwargs={"isolate_id": first.pk})
        self.client.post(failed, {"status": "revival_failed", "reason": "Contamination"})
        first.refresh_from_db()
        self.assertEqual(first.status, LaboratoryIsolate.Status.REVIVAL_FAILED)
        self.assertIn("Contamination", first.revival_failed_reason)
        self.client.post(reverse("laboratory_batch_transition",
            kwargs={"accession_id": accession.pk, "action": "complete"}))
        self.assertEqual(
            accession.isolate_records.filter(status=LaboratoryIsolate.Status.REVIVED).count(), 0
        )
        self.client.post(reverse("laboratory_revival_retry", kwargs={"isolate_id": first.pk}))
        first.refresh_from_db()
        self.assertEqual(first.revival_retry_count, 1)
        self.assertEqual(first.status, LaboratoryIsolate.Status.REVIVAL_IN_PROGRESS)
        self.client.post(reverse("laboratory_revival_retry_complete", kwargs={"isolate_id": first.pk}))
        first.refresh_from_db()
        self.assertEqual(first.status, LaboratoryIsolate.Status.REVIVED)
        self.assertEqual(first.revival_retry_count, 1)
        # Still-in-progress isolates are completed in one atomic batch update.
        # The previously retried isolate retains its original completion audit.
        previous_completion = first.revival_completed_at
        self.client.post(reverse("laboratory_batch_transition",
            kwargs={"accession_id": accession.pk, "action": "complete"}))
        first.refresh_from_db()
        self.assertEqual(first.revival_completed_at, previous_completion)
        self.assertEqual(
            accession.isolate_records.filter(status=LaboratoryIsolate.Status.REVIVED).count(),
            accession.isolate_count,
        )
        # A separate failed isolate must be tested using a new accession batch.
        second.status = LaboratoryIsolate.Status.REVIVAL_IN_PROGRESS
        second.save(update_fields=("status",))
        self.client.post(reverse("laboratory_revival_failed", kwargs={"isolate_id": second.pk}),
                         {"status": "non_viable", "reason": "Not recoverable"})
        second.refresh_from_db()
        self.assertEqual(second.status, LaboratoryIsolate.Status.NON_VIABLE)
        with self.assertRaises(WorkflowError):
            retry_revival(second.pk, self.lab_user)

    def test_batch_stickers_print_only_complete_isolates_in_accession_order(self):
        accession = self.create_pending_accession()
        self._queue_and_endorse_to_main_lab(accession)
        first, second = list(accession.isolate_records.order_by("accession_number")[:2])
        for isolate, code in ((first, "KPN"), (second, "ECO")):
            isolate.status = LaboratoryIsolate.Status.READY_FOR_BIOBANK
            isolate.final_organism = "Klebsiella pneumoniae" if code == "KPN" else "Escherichia coli"
            isolate.save(update_fields=("status", "final_organism"))
            LaboratoryBiobank.objects.create(
                isolate=isolate, whonet_code=code, date_harvested=date(2026, 11, 19),
                date_lyophilized=date(2026, 11, 19),
            )
        response = self.client.get(reverse("laboratory_batch_labels",
                                           kwargs={"accession_id": accession.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["labels"]), 2)
        self.assertEqual(len(response.context["unready"]), accession.isolate_count - 2)
        self.assertLess(response.content.index(first.accession_number.encode()),
                        response.content.index(second.accession_number.encode()))
        self.assertContains(response, "KPN")
        self.assertContains(response, "ECO")
        self.assertContains(response, "11/19/2026")
        self.assertNotContains(response, "Klebsiella pneumoniae")
        self.assertContains(response, "data:image/png;base64,")
        first.refresh_from_db()
        self.assertEqual(first.status, LaboratoryIsolate.Status.READY_FOR_BIOBANK)

    def test_id_cassette_return_must_precede_revival(self):
        accession = self.create_pending_accession()
        self._queue_and_endorse_to_main_lab(accession)
        isolate = accession.isolate_records.first()
        self.client.post(reverse("laboratory_batch_transition", kwargs={
            "accession_id": accession.pk, "action": "queue",
        }))
        self.assertFalse(accession.isolate_records.filter(
            status=LaboratoryIsolate.Status.REVIVAL_QUEUED).exists())
        self.client.post(reverse("laboratory_batch_transition", kwargs={
            "accession_id": accession.pk, "action": "start",
        }))
        self.assertFalse(accession.isolate_records.filter(
            status=LaboratoryIsolate.Status.REVIVAL_IN_PROGRESS).exists())
        staff = User.objects.create_user(username="cassette-gate-staff", password="test-pass")
        staff.groups.add(Group.objects.get_or_create(name=home_permissions.ROLE_LAB_STAFF)[0])
        self.client.force_login(staff)
        self.client.post(reverse("laboratory_acknowledge_id", kwargs={"isolate_id": isolate.pk}))
        self.client.post(reverse("laboratory_record_id", kwargs={"isolate_id": isolate.pk}), {
            "organism_reported_code": "KPN",
            "organism_reported": "Klebsiella pneumoniae",
            "instrument": "Analyzer",
        })
        self.client.post(reverse("laboratory_return_id", kwargs={"isolate_id": isolate.pk}))
        isolate.refresh_from_db()
        self.assertEqual(isolate.status, LaboratoryIsolate.Status.ID_TESTING)
        self.client.post(reverse("laboratory_record_id", kwargs={"isolate_id": isolate.pk}), {
            "organism_reported_code": "KPN",
            "organism_reported": "Klebsiella pneumoniae",
            "cassette_reference": "Cassette-124",
        })
        self.client.post(reverse("laboratory_return_id", kwargs={"isolate_id": isolate.pk}))
        isolate.refresh_from_db()
        self.assertEqual(isolate.status, LaboratoryIsolate.Status.ID_RETURNED)
        self.client.force_login(self.lab_user)
        self.client.post(reverse("laboratory_batch_transition", kwargs={
            "accession_id": accession.pk, "action": "queue",
        }))
        # All isolates in the batch must have returned cassettes.
        self.assertFalse(accession.isolate_records.filter(
            status=LaboratoryIsolate.Status.REVIVAL_QUEUED).exists())
        self._return_all_cassettes_and_queue_revival(accession)
        self.assertTrue(accession.isolate_records.filter(
            status=LaboratoryIsolate.Status.REVIVAL_QUEUED).exists())

    def test_id_review_is_blocked_until_revival_complete(self):
        accession = self.create_pending_accession()
        self._queue_and_endorse_to_main_lab(accession)
        self._return_all_cassettes_and_queue_revival(accession)
        isolate = accession.isolate_records.first()
        url = reverse("laboratory_review_id", kwargs={"isolate_id": isolate.pk})
        self.client.post(url, {"final_organism_code": "KPN", "final_organism": "Klebsiella pneumoniae"})
        isolate.refresh_from_db()
        self.assertEqual(isolate.status, LaboratoryIsolate.Status.REVIVAL_QUEUED)
        for action in ("start", "complete"):
            self.client.post(reverse("laboratory_batch_transition", kwargs={
                "accession_id": accession.pk, "action": action,
            }))
        self.client.post(url, {"final_organism_code": "KPN", "final_organism": "Klebsiella pneumoniae"})
        isolate.refresh_from_db()
        self.assertEqual(isolate.status, LaboratoryIsolate.Status.ID_REVIEWED)

    def test_early_stage_transitions_are_rejected_without_partial_updates(self):
        accession = self.create_pending_accession()
        for action in ("queue", "start", "complete", "endorse"):
            self.assertEqual(self.client.post(reverse("laboratory_batch_transition", kwargs={
                "accession_id": accession.pk, "action": action,
            })).status_code, 302)
        self.assertEqual(accession.isolate_records.count(), 0)
        self._queue_and_endorse_to_main_lab(accession)
        self.client.post(reverse("laboratory_endorse_id", kwargs={"accession_id": accession.pk}))
        self.assertEqual(accession.isolate_records.count(), accession.isolate_count)
        for action in ("queue", "start", "complete"):
            self.client.post(reverse("laboratory_batch_transition", kwargs={
                "accession_id": accession.pk, "action": action,
            }))
        self.assertEqual(accession.isolate_records.filter(
            status=LaboratoryIsolate.Status.ID_PENDING).count(), accession.isolate_count)
        self._return_all_cassettes_and_queue_revival(accession)
        self.client.post(reverse("laboratory_batch_transition", kwargs={
            "accession_id": accession.pk, "action": "start",
        }))
        self.assertEqual(accession.isolate_records.filter(
            status=LaboratoryIsolate.Status.REVIVAL_IN_PROGRESS).count(), accession.isolate_count)
        self.client.post(reverse("laboratory_batch_transition", kwargs={
            "accession_id": accession.pk, "action": "complete",
        }))
        self.assertEqual(accession.isolate_records.filter(
            status=LaboratoryIsolate.Status.REVIVED).count(), accession.isolate_count)

    def test_revived_and_non_viable_mixed_batch_does_not_review(self):
        accession = self.create_pending_accession()
        self.client.post(reverse("laboratory_endorse_id", kwargs={"accession_id": accession.pk}))
        self._return_all_cassettes_and_queue_revival(accession)
        self.client.post(reverse("laboratory_batch_transition",
            kwargs={"accession_id": accession.pk, "action": "start"}))
        isolate = accession.isolate_records.first()
        self.client.post(reverse("laboratory_revival_failed", kwargs={"isolate_id": isolate.pk}),
                         {"status": "non_viable", "reason": "Lost during transport"})
        self.client.post(reverse("laboratory_batch_transition",
            kwargs={"accession_id": accession.pk, "action": "complete"}))
        self.assertFalse(accession.isolate_records.filter(
            status=LaboratoryIsolate.Status.REVIVED).exists())
        self.assertFalse(accession.isolate_records.filter(
            status=LaboratoryIsolate.Status.REVIVED).exists())

    def test_lab_staff_cannot_perform_revival_or_lyophilization_actions(self):
        accession = self.create_pending_accession()
        self.client.post(reverse("laboratory_endorse_id", kwargs={"accession_id": accession.pk}))
        isolate = accession.isolate_records.first()
        staff = User.objects.create_user(username="revival-read-only", password="test-pass")
        staff.groups.add(Group.objects.get_or_create(name=home_permissions.ROLE_LAB_STAFF)[0])
        self.client.force_login(staff)
        self.assertEqual(self.client.get(reverse("laboratory_receiving_workspace",
            kwargs={"receiving_id": self.receipt.receiving_id})).status_code, 200)
        self.client.post(reverse("laboratory_batch_transition",
            kwargs={"accession_id": accession.pk, "action": "start"}))
        self.client.post(reverse("laboratory_revival_failed", kwargs={"isolate_id": isolate.pk}),
                         {"status": "non_viable", "reason": "Unauthorized"})
        self.client.post(reverse("laboratory_lyophilization_complete", kwargs={"isolate_id": isolate.pk}), {
            "date_harvested": "2026-11-19", "date_lyophilized": "2026-11-19", "whonet_code": "KPN",
        })
        isolate.refresh_from_db()
        self.assertEqual(isolate.status, LaboratoryIsolate.Status.ID_PENDING)
        self.assertEqual(isolate.revival_failed_reason, "")
        self.assertEqual(isolate.lyophilization_completed_at, None)

    def test_all_revival_transitions_require_post(self):
        accession = self.create_pending_accession()
        self.client.post(reverse("laboratory_endorse_id", kwargs={"accession_id": accession.pk}))
        isolate = accession.isolate_records.first()
        urls = [
            reverse("laboratory_endorse_id", kwargs={"accession_id": accession.pk}),
            reverse("laboratory_batch_transition",
                    kwargs={"accession_id": accession.pk, "action": "start"}),
            reverse("laboratory_revival_failed", kwargs={"isolate_id": isolate.pk}),
            reverse("laboratory_revival_retry", kwargs={"isolate_id": isolate.pk}),
            reverse("laboratory_revival_retry_complete", kwargs={"isolate_id": isolate.pk}),
            reverse("laboratory_lyophilization_complete", kwargs={"isolate_id": isolate.pk}),
            reverse("laboratory_biobank_save", kwargs={"isolate_id": isolate.pk}),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 405)

    def test_0008_migrates_only_untouched_pre_id_revival_queue(self):
        from django.apps import apps as django_apps
        from importlib import import_module
        accession = self.create_pending_accession()
        codes = accession.accession_numbers
        untouched = LaboratoryIsolate.objects.create(
            accession_range=accession,
            accession_number=codes[0],
            status=LaboratoryIsolate.Status.REVIVAL_QUEUED,
            revival_queued_at=timezone.now(),
            revival_queued_by=self.lab_user,
        )
        progress = LaboratoryIsolate.objects.create(
            accession_range=accession,
            accession_number=codes[1],
            status=LaboratoryIsolate.Status.REVIVAL_IN_PROGRESS,
        )
        returned = LaboratoryIsolate.objects.create(
            accession_range=accession,
            accession_number=codes[2],
            status=LaboratoryIsolate.Status.REVIVAL_QUEUED,
            returned_at=timezone.now(),
            returned_by=self.lab_user,
        )
        migration = import_module("apps.receiving.migrations.0008_main_lab_id_before_revival")
        schema = SimpleNamespace(connection=SimpleNamespace(alias="default"))
        migration.reconcile_pre_id_revival_queues(django_apps, schema)
        untouched.refresh_from_db()
        progress.refresh_from_db()
        returned.refresh_from_db()
        self.assertEqual(untouched.status, LaboratoryIsolate.Status.ID_PENDING)
        self.assertEqual(untouched.endorsed_by, self.lab_user)
        self.assertIsNotNone(untouched.endorsed_at)
        self.assertEqual(progress.status, LaboratoryIsolate.Status.REVIVAL_IN_PROGRESS)
        self.assertEqual(returned.status, LaboratoryIsolate.Status.REVIVAL_QUEUED)

    def test_batch_queue_rejects_missing_return_audit_or_cassette_reference(self):
        accession = self.create_pending_accession()
        self._queue_and_endorse_to_main_lab(accession)
        for isolate in accession.isolate_records.all():
            isolate.status = LaboratoryIsolate.Status.ID_RETURNED
            isolate.save(update_fields=("status",))
            LaboratoryIdentification.objects.create(
                isolate=isolate,
                organism_reported="Klebsiella pneumoniae",
                cassette_reference="Cassette-1",
            )
        url = reverse("laboratory_batch_transition", kwargs={
            "accession_id": accession.pk, "action": "queue",
        })
        self.client.post(url)
        self.assertFalse(accession.isolate_records.filter(
            status=LaboratoryIsolate.Status.REVIVAL_QUEUED
        ).exists())

    def test_legacy_status_migration_keeps_identification_and_storage_records(self):
        from django.apps import apps as django_apps
        from importlib import import_module
        accession = self.create_pending_accession()
        rows = [
            LaboratoryIsolate.objects.create(
                accession_range=accession, accession_number=number, status=legacy,
                final_organism="Klebsiella pneumoniae" if legacy == "reviewed" else "",
            ) for number, legacy in zip(accession.accession_numbers,
                ("pending", "testing", "returned", "reviewed"))
        ]
        LaboratoryIdentification.objects.create(
            isolate=rows[2], organism_reported="Klebsiella pneumoniae", recorded_by=self.lab_user)
        LaboratoryBiobank.objects.create(
            isolate=rows[3], whonet_code="KPN",
            date_harvested=date(2026, 11, 19), date_lyophilized=date(2026, 11, 19),
            box_position="A1", recorded_by=self.lab_user,
        )
        migration = import_module("apps.receiving.migrations.0006_laboratory_revival_biobank_stages")
        schema = SimpleNamespace(connection=SimpleNamespace(alias="default"))
        migration.migrate_legacy_isolates(django_apps, schema)
        for item, expected in zip(rows, (
            LaboratoryIsolate.Status.ID_PENDING, LaboratoryIsolate.Status.ID_TESTING,
            LaboratoryIsolate.Status.ID_RETURNED, LaboratoryIsolate.Status.BIOBANKED,
        )):
            item.refresh_from_db()
            self.assertEqual(item.status, expected)
        self.assertEqual(rows[2].identifications.count(), 1)
        self.assertEqual(rows[3].biobank.whonet_code, "KPN")
        self.assertEqual(rows[3].biobank.box_position, "A1")

    def test_migration_keeps_ambiguous_reviewed_rows_at_earliest_proven_stage(self):
        from django.apps import apps as django_apps
        from importlib import import_module
        accession = self.create_pending_accession()
        first, second = [
            LaboratoryIsolate.objects.create(
                accession_range=accession,
                accession_number=number,
                status="reviewed",
                final_organism="Klebsiella pneumoniae",
            ) for number in accession.accession_numbers[:2]
        ]
        LaboratoryBiobank.objects.create(
            isolate=second, whonet_code="KPN",
            date_harvested=date(2026, 11, 19),
            date_lyophilized=date(2026, 11, 19),
        )
        migration = import_module("apps.receiving.migrations.0006_laboratory_revival_biobank_stages")
        schema = SimpleNamespace(connection=SimpleNamespace(alias="default"))
        migration.migrate_legacy_isolates(django_apps, schema)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, LaboratoryIsolate.Status.ID_REVIEWED)
        self.assertEqual(second.status, LaboratoryIsolate.Status.READY_FOR_BIOBANK)
        self.assertEqual(second.biobank.whonet_code, "KPN")
        self.assertFalse(second.biobank.box_position)

    def test_id_csv_upload_preview_and_commit(self):
        accession = self.create_pending_accession()
        self._queue_and_endorse_to_main_lab(accession)
        isolate = accession.isolate_records.order_by("accession_number").first()
        staff = User.objects.create_user(username="csv-laboratory-staff", password="test-pass")
        staff.groups.add(Group.objects.get_or_create(
            name=home_permissions.ROLE_LAB_STAFF
        )[0])
        self.client.force_login(staff)
        self.client.post(reverse("laboratory_acknowledge_id", kwargs={"isolate_id": isolate.pk}))

        from django.core.files.uploadedfile import SimpleUploadedFile
        data = (f"accession_number,organism_reported,instrument,cassette_reference\n"
                f"{isolate.accession_number},Klebsiella pneumoniae,ID system,Slot 1\n").encode()
        url = reverse("laboratory_id_upload")
        preview = self.client.post(url, {
            "action": "preview",
            "file": SimpleUploadedFile("id-results.csv", data, content_type="text/csv"),
        })
        self.assertContains(preview, "Validated preview")
        self.assertFalse(isolate.identifications.exists())

        saved = self.client.post(url, {
            "action": "commit",
            "file": SimpleUploadedFile("id-results.csv", data, content_type="text/csv"),
        })
        self.assertEqual(saved.status_code, 302)
        result = isolate.identifications.get()
        self.assertEqual(result.organism_reported, "Klebsiella pneumoniae")
        self.assertEqual(result.whonet_organism_code, "KPN")
        self.assertEqual(result.source, LaboratoryIdentification.Source.CSV)

    def test_id_csv_upload_rejects_mismatched_whonet_code_and_name(self):
        accession = self.create_pending_accession()
        self._queue_and_endorse_to_main_lab(accession)
        isolate = accession.isolate_records.first()
        isolate.status = LaboratoryIsolate.Status.ID_TESTING
        isolate.save(update_fields=("status",))
        staff = User.objects.create_user(username="csv-mismatch-staff", password="test-pass")
        staff.groups.add(Group.objects.get_or_create(name=home_permissions.ROLE_LAB_STAFF)[0])
        self.client.force_login(staff)
        from django.core.files.uploadedfile import SimpleUploadedFile
        data = (
            "accession_number,organism_reported,organism_code,instrument\n"
            f"{isolate.accession_number},Escherichia coli,KPN,ID system\n"
        ).encode("utf-8")
        response = self.client.post(reverse("laboratory_id_upload"), {
            "action": "preview",
            "file": SimpleUploadedFile("mismatched.csv", data, content_type="text/csv"),
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(isolate.identifications.exists())

    def test_biobank_form_records_lyophilization_and_supplementary_storage(self):
        accession = self.create_pending_accession()
        self._queue_and_endorse_to_main_lab(accession)
        isolate = accession.isolate_records.first()
        isolate.final_organism = "Escherichia coli"
        isolate.status = LaboratoryIsolate.Status.ID_REVIEWED
        isolate.save(update_fields=("final_organism", "status"))
        self.client.post(reverse("laboratory_lyophilization_complete",
            kwargs={"isolate_id": isolate.pk}), {
                "date_harvested": "2026-11-19",
                "date_lyophilized": "2026-11-19",
                "whonet_code": "ECO",
            })
        isolate.refresh_from_db()
        self.assertEqual(isolate.status, LaboratoryIsolate.Status.READY_FOR_BIOBANK)
        staff = User.objects.create_user(username="biobank-lab-staff", password="test-pass")
        staff.groups.add(Group.objects.get_or_create(name=home_permissions.ROLE_LAB_STAFF)[0])
        self.client.force_login(staff)
        response = self.client.post(reverse("laboratory_biobank_save",
            kwargs={"isolate_id": isolate.pk}), {
            "whonet_code": "ECO",
            "lyo_box_name": "LAB BOX", "lyo_box_number": "2",
            "lyo_position": "C3", "refrigerator_number": "1",
            "lyo_location": "Room 1",
            "lyo_date_encoded": "2026-11-19",
            "skimmed_milk": "on", "tsbg": "on",
            "supplementary_box_name": "SUPP BOX",
            "supplementary_box_number": "4",
            "supplementary_position": "B2",
            "freezer_number": "3",
            "supplementary_location": "Room 2",
            "supplementary_date_encoded": "2026-11-19",
        })
        self.assertEqual(response.status_code, 302)
        biobank = LaboratoryBiobank.objects.get(isolate=isolate)
        self.assertEqual(biobank.whonet_code, "ECO")
        self.assertEqual(biobank.lyo_box_name, "LAB BOX")
        self.assertEqual(biobank.date_harvested, date(2026, 11, 19))
        self.assertEqual(biobank.date_lyophilized, date(2026, 11, 19))
        self.assertTrue(biobank.skimmed_milk)
        self.assertTrue(biobank.tsbg)
        self.assertEqual(biobank.supplementary_position, "B2")
        isolate.refresh_from_db()
        self.assertEqual(isolate.status, LaboratoryIsolate.Status.BIOBANKED)
        self.assertEqual(self.client.get(reverse("laboratory_biobank_list")).status_code, 200)

    @override_settings(RECEIVING_QR_BASE_URL="https://lab.example.org")
    def test_batch_sticker_qr_encodes_accession_scan_link(self):
        accession = self.create_pending_accession()
        self._queue_and_endorse_to_main_lab(accession)
        first, second = list(accession.isolate_records.order_by("accession_number")[:2])
        for isolate, code in ((first, "KPN"), (second, "ECO")):
            isolate.status = LaboratoryIsolate.Status.READY_FOR_BIOBANK
            isolate.final_organism = "Klebsiella pneumoniae"
            isolate.save(update_fields=("status", "final_organism"))
            LaboratoryBiobank.objects.create(
                isolate=isolate, whonet_code=code,
                date_harvested=date(2026, 11, 19),
                date_lyophilized=date(2026, 11, 19),
            )
        with patch("apps.laboratory.views.render_qr_png",
                   return_value=b"PNG SAMPLE") as qr:
            response = self.client.get(reverse("laboratory_batch_labels",
                kwargs={"accession_id": accession.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(qr.call_count, 2)
        self.assertEqual([call.args[0] for call in qr.call_args_list], [
            "https://lab.example.org" + reverse(
                "laboratory_isolate_scan", args=[isolate.accession_number],
            ) for isolate in (first, second)
        ])
        self.assertEqual(len(response.context["labels"]), 2)

    def test_storage_cannot_rewrite_scientific_dates_or_whonet_code(self):
        from apps.laboratory.workflow import save_biobank, WorkflowError
        accession = self.create_pending_accession()
        self._queue_and_endorse_to_main_lab(accession)
        isolate = accession.isolate_records.first()
        isolate.status = LaboratoryIsolate.Status.READY_FOR_BIOBANK
        isolate.final_organism = "Klebsiella pneumoniae"
        isolate.save(update_fields=("status", "final_organism"))
        bank = LaboratoryBiobank.objects.create(
            isolate=isolate, whonet_code="KPN",
            date_harvested=date(2026, 11, 19),
            date_lyophilized=date(2026, 11, 19),
        )
        with self.assertRaises(WorkflowError):
            save_biobank(isolate.pk, {
                "box_position": "A1", "whonet_code": "ECO",
            }, self.lab_user)
        bank.refresh_from_db()
        isolate.refresh_from_db()
        self.assertEqual(bank.whonet_code, "KPN")
        self.assertEqual(bank.box_position, "")
        self.assertEqual(isolate.status, LaboratoryIsolate.Status.READY_FOR_BIOBANK)

    def test_biobank_save_without_box_position_does_not_advance(self):
        accession = self.create_pending_accession()
        self._queue_and_endorse_to_main_lab(accession)
        isolate = accession.isolate_records.first()
        isolate.status = LaboratoryIsolate.Status.READY_FOR_BIOBANK
        isolate.final_organism = "Klebsiella pneumoniae"
        isolate.save(update_fields=("status", "final_organism"))
        LaboratoryBiobank.objects.create(
            isolate=isolate, whonet_code="KPN",
            date_harvested=date(2026, 11, 19),
            date_lyophilized=date(2026, 11, 19),
        )
        response = self.client.post(reverse("laboratory_biobank_save", kwargs={"isolate_id": isolate.pk}), {
            "storage_box": "Box A", "storage_location": "Room 1",
        })
        self.assertEqual(response.status_code, 302)
        isolate.refresh_from_db()
        self.assertEqual(isolate.status, LaboratoryIsolate.Status.READY_FOR_BIOBANK)
        self.assertEqual(isolate.biobank.storage_box, "")

    def test_lyophilization_rejects_early_future_or_reverse_dates(self):
        accession = self.create_pending_accession()
        self._queue_and_endorse_to_main_lab(accession)
        isolate = accession.isolate_records.first()
        url = reverse("laboratory_lyophilization_complete", kwargs={"isolate_id": isolate.pk})
        self.client.post(url, {"date_harvested": "2026-11-19",
                               "date_lyophilized": "2026-11-19", "whonet_code": "KPN"})
        isolate.refresh_from_db()
        self.assertEqual(isolate.status, LaboratoryIsolate.Status.ID_PENDING)
        isolate.final_organism = "Klebsiella pneumoniae"
        isolate.status = LaboratoryIsolate.Status.ID_REVIEWED
        isolate.save(update_fields=("final_organism", "status"))
        for start, end in (("2026-11-20", "2026-11-19"),
                           ("2099-12-01", "2099-12-02"),
                           ("2026-11-18", "2026-11-19")):
            self.client.post(url, {"date_harvested": start,
                                   "date_lyophilized": end, "whonet_code": "KPN"})
            isolate.refresh_from_db()
            self.assertEqual(isolate.status, LaboratoryIsolate.Status.ID_REVIEWED)
        self.assertFalse(LaboratoryBiobank.objects.filter(isolate=isolate).exists())
    def test_invalid_id_workbook_is_rejected(self):
        staff = User.objects.create_user(username="invalid-excel-staff", password="test-pass")
        staff.groups.add(Group.objects.get_or_create(
            name=home_permissions.ROLE_LAB_STAFF
        )[0])
        self.client.force_login(staff)
        from django.core.files.uploadedfile import SimpleUploadedFile
        response = self.client.post(reverse("laboratory_id_upload"), {
            "action": "preview",
            "file": SimpleUploadedFile("bad.xlsx", b"not a workbook"),
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(LaboratoryIdentification.objects.exists())

    def test_endorsed_accession_is_protected_from_edit_and_delete(self):
        accession = self.create_pending_accession()
        self.client.post(reverse("laboratory_endorse_id", kwargs={"accession_id": accession.pk}))
        self.client.post(reverse("laboratory_accession_edit", kwargs={"accession_id": accession.pk}), {
            "date_referred": "2026-11-19", "start_number": 2,
            "end_number": 5, "batch_number": 1, "total_batches": 1,
        })
        self.client.post(reverse("laboratory_accession_delete", kwargs={"accession_id": accession.pk}), {
            "confirmation": "DELETE",
        })
        accession.refresh_from_db()
        self.assertEqual(accession.reference_range, "0001-0005")
        self.assertEqual(accession.isolate_records.count(), 5)

    def test_organism_lookup_searches_by_whonet_code_or_full_name(self):
        url = reverse("laboratory_organism_search")
        by_code = self.client.get(url, {"q": "kpn"})
        self.assertEqual(by_code.status_code, 200)
        self.assertIn({"code": "KPN", "name": "Klebsiella pneumoniae"}, by_code.json()["results"])
        by_name = self.client.get(url, {"q": "pneumoniae"})
        self.assertIn({"code": "KPN", "name": "Klebsiella pneumoniae"}, by_name.json()["results"])
        self.assertEqual(self.client.get(url).json()["results"], [])

    def test_organism_lookup_endpoint_denies_secretariat(self):
        secretariat = User.objects.create_user(username="organism-secretariat", password="test-pass")
        secretariat.groups.add(Group.objects.get_or_create(name=home_permissions.ROLE_SECRETARIAT)[0])
        self.client.force_login(secretariat)
        self.assertEqual(self.client.get(reverse("laboratory_organism_search"), {"q": "KPN"}).status_code, 302)

    def test_isolate_form_exposes_shared_search_for_id_review_and_lyophilization(self):
        accession = self.create_pending_accession()
        self._queue_and_endorse_to_main_lab(accession)
        isolate = accession.isolate_records.first()
        isolate.status = LaboratoryIsolate.Status.ID_TESTING
        isolate.save(update_fields=("status",))
        url = reverse("laboratory_isolate_detail", kwargs={"isolate_id": isolate.pk})
        # The instrument ID form belongs to Main Laboratory staff, not LAB Encoder.
        staff = User.objects.create_user(username="organism-picker-lab-staff", password="test-pass")
        staff.groups.add(Group.objects.get_or_create(name=home_permissions.ROLE_LAB_STAFF)[0])
        self.client.force_login(staff)
        response = self.client.get(url)
        self.assertContains(response, "laboratory-organism-picker.js")
        self.assertContains(response, "data-organism-picker")
        self.assertContains(response, 'name="organism_reported_code"')
        self.assertContains(response, reverse("laboratory_organism_search"))
        # ID review and lyophilization are LAB Encoder responsibilities.
        self.client.force_login(self.lab_user)
        isolate.status = LaboratoryIsolate.Status.REVIVED
        isolate.save(update_fields=("status",))
        response = self.client.get(url)
        self.assertContains(response, 'name="final_organism_code"')
        isolate.status = LaboratoryIsolate.Status.ID_REVIEWED
        isolate.final_organism = "Klebsiella pneumoniae"
        isolate.save(update_fields=("status", "final_organism"))
        response = self.client.get(url)
        self.assertContains(response, 'name="whonet_code"')
        self.assertContains(response, 'name="whonet_organism_name"')

    def test_review_rejects_mismatched_code_and_name(self):
        accession = self.create_pending_accession()
        self._queue_and_endorse_to_main_lab(accession)
        isolate = accession.isolate_records.first()
        isolate.status = LaboratoryIsolate.Status.REVIVED
        isolate.save(update_fields=("status",))
        url = reverse("laboratory_review_id", kwargs={"isolate_id": isolate.pk})
        self.client.post(url, {
            "final_organism_code": "KPN", "final_organism": "Escherichia coli",
        })
        isolate.refresh_from_db()
        self.assertEqual(isolate.status, LaboratoryIsolate.Status.REVIVED)
        self.client.post(url, {
            "final_organism_code": "kpn", "final_organism": "Klebsiella pneumoniae",
        })
        isolate.refresh_from_db()
        self.assertEqual(isolate.final_organism, "Klebsiella pneumoniae")
        self.assertEqual(isolate.approved_organism_code, "KPN")
        self.assertEqual(isolate.status, LaboratoryIsolate.Status.ID_REVIEWED)

    def test_lyophilization_rejects_mismatched_sticker_code(self):
        accession = self.create_pending_accession()
        self._queue_and_endorse_to_main_lab(accession)
        isolate = accession.isolate_records.first()
        isolate.status = LaboratoryIsolate.Status.ID_REVIEWED
        isolate.final_organism = "Klebsiella pneumoniae"
        isolate.save(update_fields=("status", "final_organism"))
        url = reverse("laboratory_lyophilization_complete", kwargs={"isolate_id": isolate.pk})
        self.client.post(url, {
            "date_harvested": "2026-11-19", "date_lyophilized": "2026-11-19",
            "whonet_code": "ECO", "whonet_organism_name": "Escherichia coli",
        })
        isolate.refresh_from_db()
        self.assertEqual(isolate.status, LaboratoryIsolate.Status.ID_REVIEWED)
        self.assertFalse(LaboratoryBiobank.objects.filter(isolate=isolate).exists())

    def test_manual_id_stores_both_selected_code_and_canonical_name(self):
        accession = self.create_pending_accession()
        self._queue_and_endorse_to_main_lab(accession)
        isolate = accession.isolate_records.first()
        isolate.status = LaboratoryIsolate.Status.ID_TESTING
        isolate.save(update_fields=("status",))
        staff = User.objects.create_user(username="organism-record-staff", password="test-pass")
        staff.groups.add(Group.objects.get_or_create(name=home_permissions.ROLE_LAB_STAFF)[0])
        self.client.force_login(staff)
        url = reverse("laboratory_record_id", kwargs={"isolate_id": isolate.pk})
        self.client.post(url, {
            "organism_reported_code": "KPN",
            "organism_reported": "Klebsiella pneumoniae",
            "instrument": "ID analyzer",
        })
        recorded = isolate.identifications.get()
        self.assertEqual(recorded.whonet_organism_code, "KPN")
        self.assertEqual(recorded.organism_reported, "Klebsiella pneumoniae")
        self.client.post(url, {
            "organism_reported_code": "KPN",
            "organism_reported": "Escherichia coli",
        })
        self.assertEqual(isolate.identifications.count(), 1)

    def test_approved_code_is_carried_to_lyophilization(self):
        accession = self.create_pending_accession()
        self._queue_and_endorse_to_main_lab(accession)
        isolate = accession.isolate_records.first()
        isolate.status = LaboratoryIsolate.Status.REVIVED
        isolate.save(update_fields=("status",))
        self.client.post(reverse("laboratory_review_id", kwargs={"isolate_id": isolate.pk}), {
            "final_organism_code": "KPN",
            "final_organism": "Klebsiella pneumoniae",
        })
        isolate.refresh_from_db()
        self.assertEqual(isolate.approved_organism_code, "KPN")
        page = self.client.get(reverse("laboratory_isolate_detail", kwargs={"isolate_id": isolate.pk}))
        self.assertContains(page, 'value="KPN"')
        self.client.post(reverse("laboratory_lyophilization_complete", kwargs={"isolate_id": isolate.pk}), {
            "date_harvested": "2026-11-19",
            "date_lyophilized": "2026-11-19",
            "whonet_code": "KPN",
            "whonet_organism_name": "Klebsiella pneumoniae",
        })
        isolate.refresh_from_db()
        self.assertEqual(isolate.status, LaboratoryIsolate.Status.READY_FOR_BIOBANK)
        self.assertEqual(isolate.biobank.whonet_code, "KPN")

    def test_0007_backfills_approved_code_from_existing_biobank_record(self):
        from django.apps import apps as django_apps
        from importlib import import_module
        accession = self.create_pending_accession()
        isolate = LaboratoryIsolate.objects.create(
            accession_range=accession, accession_number=accession.accession_numbers[0],
            final_organism="Klebsiella pneumoniae",
            status=LaboratoryIsolate.Status.BIOBANKED,
        )
        LaboratoryBiobank.objects.create(isolate=isolate, whonet_code="kpn")
        migration = import_module("apps.receiving.migrations.0007_laboratory_approved_organism_code")
        schema = SimpleNamespace(connection=SimpleNamespace(alias="default"))
        migration.backfill_approved_codes(django_apps, schema)
        isolate.refresh_from_db()
        self.assertEqual(isolate.approved_organism_code, "KPN")

    def test_workspace_does_not_render_finalize_control(self):
        response = self.client.get(
            reverse("laboratory_receiving_workspace", kwargs={"receiving_id": self.receipt.receiving_id})
        )

        self.assertNotContains(response, "Finalize the accepted/rejected decision")
        self.assertNotContains(response, 'name="is_finalized"')
