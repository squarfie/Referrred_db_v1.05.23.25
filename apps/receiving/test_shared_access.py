from django.contrib.auth.models import AnonymousUser
from django.test import TestCase
from django.urls import reverse

from apps.home import permissions as home_permissions
from apps.receiving import permissions
from apps.receiving.models import ReceivingEvent, ReferralReceipt
from apps.receiving import tests as fixtures


class SharedReceivingAccessTests(TestCase):
    setUp = fixtures.ReceivingTemplateTests.setUp
    create_user = fixtures.ReceivingTemplateTests.create_user
    create_receipt = fixtures.ReceivingTemplateTests.create_receipt

    def test_every_role_can_open_receiving_and_read_existing_custody_history(self):
        receipt = self.create_receipt()
        event = ReceivingEvent.objects.create(
            receipt=receipt, event_type=ReceivingEvent.EventType.RECEIVED,
            performed_by=self.secretariat, remarks="Shared custody history entry",
        )
        roles = [home_permissions.ROLE_ADMIN, home_permissions.ROLE_SECRETARIAT,
                 home_permissions.ROLE_LAB_ENCODER, home_permissions.ROLE_LAB_STAFF,
                 home_permissions.ROLE_CHECKER, home_permissions.ROLE_ENCODER,
                 home_permissions.ROLE_VERIFIER, home_permissions.ROLE_LAB_MANAGER]
        for index, role in enumerate(roles):
            with self.subTest(role=role):
                user = self.create_user(f"shared-role-{index}", role)
                self.client.force_login(user)
                self.assertTrue(permissions.can_read_receiving(user))
                flags = home_permissions.role_flags(user)
                self.assertTrue(flags["can_view_receiving"])
                self.assertTrue(flags["nav_can_secretariat_receiving"])
                queue = self.client.get(reverse("secretariat_receiving_dashboard"))
                self.assertContains(queue, receipt.tracking_number)
                self.assertContains(queue, '<span class="nav-link-text">Receiving</span>')
                self.assertContains(queue, f'href="{reverse("secretariat_receiving_dashboard")}"')
                self.assertEqual(self.client.get(reverse("receiving_dashboard")).status_code, 200)
                detail = self.client.get(reverse("receiving_detail", args=[receipt.receiving_id]), follow=True)
                self.assertContains(detail, "Shared custody history entry")
                self.assertContains(detail, self.secretariat.username)
        self.assertEqual(ReceivingEvent.objects.get(pk=event.pk).remarks, "Shared custody history entry")

    def test_new_readers_cannot_create_endorse_delete_or_confirm_receipts(self):
        receipt = self.create_receipt()
        for index, role in enumerate([home_permissions.ROLE_CHECKER, home_permissions.ROLE_ENCODER,
                                      home_permissions.ROLE_VERIFIER, home_permissions.ROLE_LAB_MANAGER]):
            with self.subTest(role=role):
                self.client.force_login(self.create_user(f"read-only-{index}", role))
                queue = self.client.get(reverse("secretariat_receiving_dashboard"))
                self.assertNotContains(queue, "New receipt")
                detail = self.client.get(reverse("receiving_detail", args=[receipt.receiving_id]))
                self.assertNotContains(detail, "Endorse to laboratory")
                self.assertNotContains(detail, "Delete permanently")
                self.assertNotContains(detail, "Confirm laboratory receipt")
                self.assertEqual(self.client.post(reverse("receiving_create"), {}).status_code, 302)
                self.assertEqual(self.client.post(reverse("receiving_endorse", args=[receipt.receiving_id])).status_code, 302)
                self.assertEqual(self.client.post(reverse("receiving_delete_permanently", args=[receipt.receiving_id]), {
                    "confirmation": "DELETE PERMANENTLY",
                }).status_code, 302)
                self.assertEqual(self.client.post(reverse("receiving_lab_confirm", args=[receipt.receiving_id]), {}).status_code, 302)
                self.assertEqual(self.client.get(reverse("laboratory_receiving_dashboard")).status_code, 302)
        receipt.refresh_from_db()
        self.assertEqual(receipt.status, ReferralReceipt.Status.RECEIVED)
        self.assertEqual(ReferralReceipt.objects.count(), 1)
        self.assertFalse(receipt.events.exists())

    def test_admin_and_superuser_keep_management_access(self):
        for user in [self.admin, self.create_user("shared-admin", home_permissions.ROLE_ADMIN)]:
            with self.subTest(username=user.username):
                self.assertTrue(permissions.can_read_receiving(user))
                self.assertTrue(permissions.can_manage_secretariat_receiving(user))
                self.client.force_login(user)
                self.assertContains(self.client.get(reverse("secretariat_receiving_dashboard")), "New receipt")

    def test_signed_in_accounts_without_assigned_roles_can_read(self):
        user = self.create_user("shared-unassigned")
        self.client.force_login(user)
        self.assertTrue(permissions.can_read_receiving(user))
        self.assertEqual(self.client.get(reverse("secretariat_receiving_dashboard")).status_code, 200)
        self.assertFalse(permissions.can_manage_secretariat_receiving(user))

    def test_anonymous_visitors_still_need_to_sign_in(self):
        self.assertFalse(permissions.can_read_receiving(AnonymousUser()))
        self.assertFalse(home_permissions.role_flags(AnonymousUser())["can_view_receiving"])
        receipt = self.create_receipt()
        for url in [reverse("receiving_dashboard"), reverse("secretariat_receiving_dashboard"),
                    reverse("receiving_detail", args=[receipt.receiving_id])]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertIn("next=", response.url)
