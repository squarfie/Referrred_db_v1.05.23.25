"""Regression coverage for the read-only Bench A isolate progress UI."""
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.receiving import tests as receiving_fixtures
from apps.receiving.models import (
    LaboratoryBatchHandoff, LaboratoryIsolate, LaboratoryOrganismCount,
)
from .isolate_progress import isolate_worksheet_steps


class IsolateProgressTests(TestCase):
    setUp = receiving_fixtures.LaboratoryReceivingWorkspaceTests.setUp
    create_pending_accession = receiving_fixtures.LaboratoryReceivingWorkspaceTests.create_pending_accession

    def make_isolate(self, **kwargs):
        accession = self.create_pending_accession()
        return LaboratoryIsolate.objects.create(
            accession_range=accession,
            accession_number=accession.accession_numbers[0],
            **kwargs,
        )

    def step_map(self, isolate):
        return {step["key"]: step for step in isolate_worksheet_steps(isolate)["steps"]}

    def test_pending_isolate_shows_six_steps_without_fabricated_completion(self):
        isolate = self.make_isolate()
        progress = isolate_worksheet_steps(isolate)
        states = self.step_map(isolate)
        self.assertEqual(progress["total"], 6)
        self.assertTrue(states["receiving"]["done"])
        self.assertTrue(states["accessioning"]["done"])
        self.assertFalse(states["organisms"]["done"])
        self.assertTrue(states["organisms"]["current"])
        self.assertFalse(states["revival"]["done"])
        self.assertFalse(states["final"]["done"])
        self.assertFalse(states["dmu"]["done"])

        page = self.client.get(reverse("laboratory_isolate_detail", args=[isolate.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Processing milestones")
        self.assertContains(page, "Revival &amp; Site ID")
        self.assertContains(page, "laboratory-isolate-progress.css")
        self.assertContains(page, "2 / 6")
        self.assertContains(page, "Print accession QR")

    def test_revival_requires_recorded_outcome_and_site_id(self):
        isolate = self.make_isolate()
        LaboratoryOrganismCount.objects.create(
            accession_range=isolate.accession_range,
            organism_code="HA", count=1,
        )
        now = timezone.now()
        isolate.status = LaboratoryIsolate.Status.REVIVED
        isolate.revival_started_at = now - timedelta(hours=1)
        isolate.revival_completed_at = now
        isolate.save(update_fields=("status", "revival_started_at", "revival_completed_at"))
        steps = self.step_map(isolate)
        self.assertTrue(steps["organisms"]["done"])
        self.assertFalse(steps["revival"]["done"])
        self.assertTrue(steps["revival"]["current"])

        isolate.site_organism_code = "KPN"
        isolate.site_organism_name = "Klebsiella pneumoniae"
        isolate.save(update_fields=("site_organism_code", "site_organism_name"))
        steps = self.step_map(isolate)
        self.assertTrue(steps["revival"]["done"])
        self.assertFalse(steps["final"]["done"])

    def test_nonviable_closure_does_not_imply_dmu_endorsement(self):
        isolate = self.make_isolate()
        now = timezone.now()
        isolate.status = LaboratoryIsolate.Status.NON_VIABLE
        isolate.revival_started_at = now - timedelta(hours=1)
        isolate.revival_completed_at = now
        isolate.save(update_fields=("status", "revival_started_at", "revival_completed_at"))
        steps = self.step_map(isolate)
        self.assertTrue(steps["revival"]["done"])
        self.assertTrue(steps["final"]["done"])
        self.assertFalse(steps["dmu"]["done"])

    def test_dmu_stage_reflects_real_range_endorsement_only(self):
        isolate = self.make_isolate()
        self.assertFalse(self.step_map(isolate)["dmu"]["done"])
        LaboratoryBatchHandoff.objects.create(
            accession=isolate.accession_range, endorsed_by=self.lab_user,
        )
        self.assertTrue(self.step_map(isolate)["dmu"]["done"])
        # Stage describes endorsement, not DMU receipt or generated batch.
        self.assertEqual(self.step_map(isolate)["dmu"]["hint"], "Accession endorsed")
