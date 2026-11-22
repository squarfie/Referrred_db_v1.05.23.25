from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.receiving import tests as fixtures
from .models import LaboratoryIsolate, LaboratoryIdentification, LaboratoryBiobank
from .workflow import queue_for_id, save_biobank


class LegacyWorkflowReviewTests(TestCase):
    setUp = fixtures.LaboratoryReceivingWorkspaceTests.setUp
    create_pending_accession = fixtures.LaboratoryReceivingWorkspaceTests.create_pending_accession

    def item(self, status):
        return LaboratoryIsolate.objects.create(
            accession_range=self.create_pending_accession(), accession_number='LEGACY-REVIEW',
            status=status,
        )

    def test_historical_main_lab_results_remain_accessible_without_invented_endorsement(self):
        item = self.item('id_testing')
        LaboratoryIdentification.objects.create(isolate=item, organism_reported='Escherichia coli')
        staff = User.objects.create_user('legacy-main-lab')
        staff.groups.add(Group.objects.get_or_create(name='Laboratory Staff')[0])
        self.client.force_login(staff)
        self.assertContains(self.client.get(reverse('main_lab_dashboard'), {'tab': 'testing'}), item.accession_number)
        self.assertEqual(self.client.get(reverse('main_lab_isolate_detail', args=[item.pk])).status_code, 200)
        item.refresh_from_db()
        self.assertIsNone(item.endorsed_at)

    def test_queue_preserves_frozen_site_id_before_clearing_provisional_final_id(self):
        accession = self.create_pending_accession()
        now = timezone.now()
        for number in accession.accession_numbers:
            LaboratoryIsolate.objects.create(
                accession_range=accession, accession_number=number, status='site_banked',
                identification_source='site', approved_organism_code='ECO', final_organism='Escherichia coli',
                revival_started_at=now-timedelta(hours=2), revival_completed_at=now-timedelta(hours=1),
            )
        queue_for_id(accession.pk, self.lab_user)
        item = accession.isolate_records.first()
        self.assertEqual((item.site_organism_code, item.site_organism_name), ('ECO', 'Escherichia coli'))
        self.assertEqual(item.final_organism, '')
        self.assertEqual(item.status, 'id_queued')

    def test_evidence_backed_legacy_final_banking_preserves_audit_dates(self):
        item = self.item('ready_for_biobank')
        now = timezone.now()
        item.returned_at = now-timedelta(days=2)
        item.reviewed_at = now-timedelta(days=1)
        item.lyophilization_completed_at = now
        item.final_organism = 'Escherichia coli'
        item.approved_organism_code = 'ECO'
        item.save()
        LaboratoryIdentification.objects.create(isolate=item, organism_reported='Escherichia coli', whonet_organism_code='ECO')
        LaboratoryBiobank.objects.create(isolate=item, whonet_code='ECO', date_harvested=now.date(), date_lyophilized=now.date())
        save_biobank(item.pk, {'storage_box': 'FINAL', 'box_position': 'A1'}, self.lab_user)
        item.refresh_from_db()
        self.assertEqual(item.identification_source, 'main_lab')
        self.assertEqual(item.status, 'biobanked')
        self.assertEqual(item.reviewed_at, now-timedelta(days=1))

    def test_incomplete_final_banking_does_not_break_processing_worklist(self):
        item = self.item('biobanked')
        item.identification_source = 'main_lab'
        item.save()
        response = self.client.get(reverse('laboratory_worklist'), {'area': 'processing', 'history': '1'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['batches'][0]['completed_count'], 0)
