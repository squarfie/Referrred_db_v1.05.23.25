"""Legacy receipt links keep laboratory confirmation in Laboratory Receiving."""
from types import SimpleNamespace
from pathlib import Path
import os
import runpy
from unittest.mock import Mock, patch
from uuid import uuid4

from django.test import RequestFactory, SimpleTestCase
from django.urls import reverse

from apps.receiving.models import ReferralReceipt
from apps.receiving.views import receiving_detail


class QrReceiveActionTests(SimpleTestCase):
    def test_secretariat_landing_keeps_lab_confirmation_in_laboratory_module(self):
        cases = [
            ({"Admin"}, "endorsed", False),
            ({"Secretariat", "LAB Encoder"}, "endorsed", False),
            ({"Secretariat"}, "endorsed", False),
            ({"Admin"}, "received", False),
            ({"Admin"}, "lab_received", False),
        ]
        for roles, status, expected in cases:
            with self.subTest(roles=roles, status=status):
                receipt = Mock(status=status, receiving_id=uuid4())
                request = RequestFactory().get(reverse(
                    "receiving_detail", args=[receipt.receiving_id],
                ))
                request.user = SimpleNamespace(is_authenticated=True)
                with (
                    patch("apps.receiving.views._receipt_or_404", return_value=receipt),
                    patch("apps.receiving.permissions.get_user_roles", return_value=roles),
                    patch("apps.laboratory.permissions.get_user_roles", return_value=roles),
                    patch("apps.receiving.views.can_delete_receipt_safely", return_value=False),
                    patch("apps.receiving.views.render", side_effect=lambda req, name, context: context),
                ):
                    context = receiving_detail(request, receipt.receiving_id)
                self.assertEqual(context["can_confirm_lab"], expected)
                # Opening the scanned URL must not mutate custody or history.
                receipt.save.assert_not_called()
                receipt.events.create.assert_not_called()

    def test_lab_only_scanner_keeps_dedicated_laboratory_destination(self):
        receipt = Mock(status=ReferralReceipt.Status.ENDORSED, receiving_id=uuid4())
        request = RequestFactory().get(reverse("receiving_detail", args=[receipt.receiving_id]))
        request.user = SimpleNamespace(is_authenticated=True)
        with (
            patch("apps.receiving.views._receipt_or_404", return_value=receipt),
            patch("apps.receiving.permissions.get_user_roles", return_value={"LAB Encoder"}),
            patch("apps.laboratory.permissions.get_user_roles", return_value={"LAB Encoder"}),
        ):
            response = receiving_detail(request, receipt.receiving_id)
        self.assertEqual(response.url, reverse("laboratory_receiving_detail", args=[receipt.receiving_id]))


class QrOriginConfigurationTests(SimpleTestCase):
    def test_configured_qr_hostname_is_allowed_without_allowing_arbitrary_hosts(self):
        settings_path = Path(__file__).resolve().parents[2] / "core" / "settings.py"
        with patch.dict(os.environ, {"SERVER": "localhost", "RECEIVING_QR_BASE_URL": "http://192.0.2.15:8000"}):
            configured = runpy.run_path(str(settings_path))
        self.assertIn("192.0.2.15", configured["ALLOWED_HOSTS"])
        self.assertNotIn("*", configured["ALLOWED_HOSTS"])
        self.assertNotIn("unconfigured.example.org", configured["ALLOWED_HOSTS"])
