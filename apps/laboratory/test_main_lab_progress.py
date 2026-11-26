"""Read-only Main Lab milestone and compact history regression checks."""
from django.utils import timezone
from django.urls import reverse
from django.test import TestCase
from apps.laboratory.models import LaboratoryIsolate, LaboratoryIdentification, LaboratoryIdentificationCorrection
from apps.laboratory.main_lab_progress import main_lab_progress
from apps.laboratory.test_main_lab_separation import MainLabSeparationTests


class MainLabProgressTests(TestCase):
    # Share fixtures without inheriting (and rerunning) the separation tests.
    setUp = MainLabSeparationTests.setUp
    create_pending_accession = MainLabSeparationTests.create_pending_accession
    main_lab_user = MainLabSeparationTests.main_lab_user
    endorsed_isolate = MainLabSeparationTests.endorsed_isolate
    def progress_by_key(self, item):
        return {step["key"]: step for step in main_lab_progress(item)["steps"]}

    def test_endorsed_isolate_waits_for_acknowledgment(self):
        item = self.endorsed_isolate()
        steps = self.progress_by_key(item)
        self.assertTrue(steps["handoff"]["done"])
        self.assertFalse(steps["received"]["done"])
        self.assertTrue(steps["received"]["current"])
        self.assertFalse(steps["testing"]["done"])
        self.assertFalse(steps["final"]["done"])
        self.assertFalse(steps["return"]["done"])
        self.client.force_login(self.main_lab_user())
        page = self.client.get(reverse("main_lab_isolate_detail", args=[item.pk]))
        self.assertContains(page, "Identification milestones")
        self.assertContains(page, "Cassette returned")
        self.assertContains(page, "1 / 5")
        self.assertContains(page, "main-lab-progress.css")

    def test_id_completion_does_not_imply_physical_return(self):
        item = self.endorsed_isolate()
        item.status = LaboratoryIsolate.Status.ID_COMPLETE
        item.acknowledged_at = timezone.now()
        item.id_completed_at = timezone.now()
        item.final_identified_at = timezone.now()
        item.approved_organism_code = "KPN"
        item.final_organism = "Klebsiella pneumoniae"
        item.identification_source = "main_lab"
        item.save()
        LaboratoryIdentification.objects.create(
            isolate=item, organism_reported="Klebsiella pneumoniae",
            whonet_organism_code="KPN", recorded_by=self.lab_user,
        )
        steps = self.progress_by_key(item)
        self.assertTrue(steps["received"]["done"])
        self.assertTrue(steps["testing"]["done"])
        self.assertTrue(steps["final"]["done"])
        self.assertFalse(steps["return"]["done"])
        self.assertTrue(steps["return"]["current"])

        self.client.force_login(self.main_lab_user())
        page = self.client.get(reverse("main_lab_isolate_detail", args=[item.pk]))
        self.assertContains(page, "4 / 5")
        self.assertNotContains(page, "Identification correction history")
        item.returned_at = timezone.now()
        item.save(update_fields=("returned_at",))
        self.assertEqual(main_lab_progress(item)["completed"], 5)

    def test_actual_corrections_remain_visible_in_history(self):
        item = self.endorsed_isolate()
        item.status = LaboratoryIsolate.Status.ID_COMPLETE
        item.id_completed_at = timezone.now()
        item.approved_organism_code = "KPN"
        item.final_organism = "Klebsiella pneumoniae"
        item.identification_source = "main_lab"
        item.save()
        LaboratoryIdentificationCorrection.objects.create(
            isolate=item,
            kind=LaboratoryIdentificationCorrection.Kind.FINAL,
            previous_code="ECO", previous_name="Escherichia coli",
            new_code="KPN", new_name="Klebsiella pneumoniae",
            reason="Confirmatory ID verified",
            performed_by=self.lab_user,
        )
        self.client.force_login(self.main_lab_user())
        page = self.client.get(reverse("main_lab_isolate_detail", args=[item.pk]))
        self.assertContains(page, "Identification correction history")
        self.assertContains(page, "Escherichia coli")
