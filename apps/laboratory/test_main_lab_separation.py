"""Regression tests for the distinct Bench A and Main Laboratory workspaces."""
from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.receiving import tests as receiving_fixtures
from apps.laboratory.models import LaboratoryIsolate


class MainLabSeparationTests(TestCase):
    setUp = receiving_fixtures.LaboratoryReceivingWorkspaceTests.setUp
    create_pending_accession = receiving_fixtures.LaboratoryReceivingWorkspaceTests.create_pending_accession

    def main_lab_user(self):
        user = User.objects.create_user("main-lab-isolate-tester", password="test-pass")
        user.groups.add(Group.objects.get_or_create(name="Laboratory Staff")[0])
        return user

    def endorsed_isolate(self):
        accession = self.create_pending_accession()
        return LaboratoryIsolate.objects.create(
            accession_range=accession,
            accession_number=accession.accession_numbers[0],
            status=LaboratoryIsolate.Status.ID_PENDING,
            endorsed_at=timezone.now(),
            endorsed_by=self.lab_user,
            site_organism_code="KPN",
            site_organism_name="Klebsiella pneumoniae",
        )

    def test_bench_a_encoder_only_has_bench_a_dashboard(self):
        item = self.endorsed_isolate()
        self.client.force_login(self.lab_user)
        self.assertEqual(self.client.get(reverse("laboratory_receiving_dashboard")).status_code, 200)
        self.assertEqual(self.client.get(reverse("main_lab_dashboard")).status_code, 302)
        self.assertEqual(self.client.get(reverse("main_lab_isolate_detail", args=[item.pk])).status_code, 302)

    def test_main_lab_staff_has_own_dashboard_and_case_without_bench_a_actions(self):
        item = self.endorsed_isolate()
        self.client.force_login(self.main_lab_user())
        dashboard = self.client.get(reverse("main_lab_dashboard"))
        self.assertEqual(dashboard.status_code, 200)
        self.assertContains(dashboard, item.accession_number)
        self.assertContains(dashboard, "Awaiting ID acknowledgment")
        detail = self.client.get(reverse("main_lab_isolate_detail", args=[item.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Acknowledge ID testing receipt")
        self.assertNotContains(detail, "Record revival outcome")
        self.assertNotContains(detail, "Temporarily bank using Site ID")
        self.assertEqual(self.client.get(reverse("laboratory_receiving_dashboard")).status_code, 302)

    def test_non_endorsed_isolate_never_enters_main_lab_queue(self):
        accession = self.create_pending_accession()
        item = LaboratoryIsolate.objects.create(
            accession_range=accession, accession_number=accession.accession_numbers[0],
            status=LaboratoryIsolate.Status.ID_QUEUED,
            site_organism_code="KPN", site_organism_name="Klebsiella pneumoniae",
        )
        self.client.force_login(self.main_lab_user())
        dashboard = self.client.get(reverse("main_lab_dashboard"))
        self.assertNotContains(dashboard, item.accession_number)
        self.assertEqual(self.client.get(reverse("main_lab_isolate_detail", args=[item.pk])).status_code, 404)
