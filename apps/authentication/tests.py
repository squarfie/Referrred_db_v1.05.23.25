# Regression tests for the secure email-based password-reset flow.
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="no-reply@example.org",
    PASSWORD_RESET_TIMEOUT=3600,
)
class PasswordResetSecurityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="staff-member",
            email="staff@example.org",
            password="OldVeryStrongPassword123!",
            is_active=True,
        )
        self.forgot = reverse("forgot_password")

    def _request_link(self, address="staff@example.org"):
        return self.client.post(self.forgot, {"email": address})

    def _get_reset_path(self):
        import re
        from urllib.parse import urlparse
        self.assertEqual(len(mail.outbox), 1)
        match = re.search(r"https?://[^\s]+/reset_password/[^\s]+", mail.outbox[0].body)
        self.assertIsNotNone(match, mail.outbox[0].body)
        return urlparse(match.group(0)).path

    def test_old_reset_url_never_changes_password(self):
        response = self.client.post(
            reverse("reset_password"),
            {"password1": "NewVeryStrongPassword123!", "password2": "NewVeryStrongPassword123!"},
        )
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("OldVeryStrongPassword123!"))

    def test_unknown_and_inactive_users_have_indistinguishable_success(self):
        inactive = User.objects.create_user(
            username="pending-user", email="pending@example.org", password="AnotherStrongPassword123!",
            is_active=False,
        )
        for email in ("unknown@example.org", inactive.email):
            response = self._request_link(email)
            self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 0)

    def test_valid_email_sends_only_reset_link_without_session_authorization(self):
        response = self._request_link()
        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertNotIn("reset_user_id", self.client.session)
        path = self._get_reset_path()
        self.assertIn("/reset_password/", path)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("OldVeryStrongPassword123!"))

    def test_invalid_token_cannot_reset_password(self):
        from django.utils.http import urlsafe_base64_encode
        from django.utils.encoding import force_bytes
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        path = reverse("password_reset_confirm", kwargs={"uidb64": uid, "token": "not-a-valid-token"})
        response = self.client.post(path, {
            "new_password1": "NewVeryStrongPassword123!", "new_password2": "NewVeryStrongPassword123!",
        })
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("OldVeryStrongPassword123!"))

    def test_valid_link_resets_once_and_reuse_fails(self):
        self._request_link()
        path = self._get_reset_path()
        response = self.client.get(path)
        self.assertRedirects(response, path.rsplit("/", 2)[0] + "/set-password/")
        confirmed = response.url
        response = self.client.post(confirmed, {
            "new_password1": "NewVeryStrongPassword123!",
            "new_password2": "NewVeryStrongPassword123!",
        })
        self.assertRedirects(response, reverse("password_reset_complete"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("NewVeryStrongPassword123!"))
        response = self.client.get(path, follow=True)
        self.assertContains(response, "invalid or has expired")
        self.assertEqual(self.client.post(confirmed, {
            "new_password1": "DifferentStrongPassword123!",
            "new_password2": "DifferentStrongPassword123!",
        }).status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("NewVeryStrongPassword123!"))

    def test_expired_link_is_rejected(self):
        from django.contrib.auth.tokens import PasswordResetTokenGenerator
        current = timezone.now()
        with patch.object(PasswordResetTokenGenerator, "_now", return_value=current - timedelta(hours=2)):
            self._request_link()
        path = self._get_reset_path()
        response = self.client.get(path, follow=True)
        self.assertContains(response, "invalid or has expired")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("OldVeryStrongPassword123!"))
