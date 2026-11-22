from datetime import timedelta
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth.models import Group
from apps.receiving import tests as receiving_test_fixtures
from apps.receiving.models import LaboratoryIsolate, LaboratoryIdentification


class WorkflowUXTests(TestCase):
    setUp = receiving_test_fixtures.LaboratoryReceivingWorkspaceTests.setUp
    create_pending_accession = receiving_test_fixtures.LaboratoryReceivingWorkspaceTests.create_pending_accession

    def isolate(self, status='id_testing'):
        accession = self.create_pending_accession()
        return LaboratoryIsolate.objects.create(accession_range=accession, accession_number='UX001', status=status)

    def staff(self):
        self.lab_user.groups.add(Group.objects.get_or_create(name='Laboratory Staff')[0])

    def test_history_scope_survives_batch_and_isolate_navigation(self):
        item = self.isolate('biobanked')
        query = '?area=processing&history=1'
        batch_url = reverse('laboratory_batch_detail', args=[item.accession_range_id])
        isolate_url = reverse('laboratory_isolate_detail', args=[item.pk])
        response = self.client.get(batch_url + query)
        self.assertContains(response, isolate_url + query)
        response = self.client.get(isolate_url + query)
        self.assertContains(response, batch_url + query)

    def test_combined_submit_requires_cassette_without_saving_result(self):
        self.staff()
        item = self.isolate()
        self.client.post(reverse('laboratory_record_id', args=[item.pk]), {'action':'submit_return', 'organism_reported':'Klebsiella pneumoniae', 'organism_reported_code':'KPN'})
        self.assertEqual(item.identifications.count(), 0)

    def test_combined_submit_returns_cassette(self):
        self.lab_user.is_superuser = True
        self.lab_user.save()
        item = self.isolate()
        self.client.post(reverse('laboratory_record_id', args=[item.pk]), {'action':'submit_return', 'organism_reported':'Klebsiella pneumoniae', 'organism_reported_code':'KPN','cassette_reference':'C1'})
        item.refresh_from_db()
        self.assertEqual(item.status, 'id_returned')
        self.assertEqual(item.returned_by_id, self.lab_user.pk)

    def test_guidance_is_role_aware(self):
        from apps.laboratory.guidance import next_action
        item = self.isolate()
        guidance = next_action(item, self.lab_user)
        self.assertEqual(guidance['section'], 'identification')
        self.assertTrue(guidance['allowed'])

    def test_both_laboratory_roles_can_receive_record_and_return_id_results(self):
        accession = self.create_pending_accession()
        for index, role in enumerate(('LAB Encoder', 'Laboratory Staff')):
            with self.subTest(role=role):
                self.lab_user.groups.set([Group.objects.get_or_create(name=role)[0]])
                item = LaboratoryIsolate.objects.create(accession_range=accession,
                    accession_number=f'ROLE-ID-{index}', status='id_pending')
                self.client.post(reverse('laboratory_acknowledge_id', args=[item.pk]))
                item.refresh_from_db()
                self.assertEqual(item.status, 'id_testing')
                page = self.client.get(reverse('laboratory_isolate_detail', args=[item.pk]))
                self.assertContains(page, 'Save result and return cassette')
                self.client.post(reverse('laboratory_record_id', args=[item.pk]), {
                    'organism_reported': 'Klebsiella pneumoniae',
                    'organism_reported_code': 'KPN', 'cassette_reference': 'C1',
                })
                self.assertEqual(item.identifications.get().recorded_by_id, self.lab_user.pk)
                self.client.post(reverse('laboratory_return_id', args=[item.pk]))
                item.refresh_from_db()
                self.assertEqual(item.status, 'id_returned')

    def test_worklists_separate_active_and_history(self):
        item = self.isolate('revived')
        self.assertEqual(len(self.client.get(reverse('laboratory_worklist')).context['batches']), 0)
        response = self.client.get(reverse('laboratory_processing_worklist'))
        self.assertEqual(response.context['area'], 'processing')
        self.assertEqual(response.context['batches'][0]['awaiting_disposition_count'], 1)
        self.assertEqual(len(self.client.get(reverse('laboratory_worklist')+'?history=1').context['batches']), 1)

    def test_record_revival_actual_dates_and_preserve_start(self):
        from apps.laboratory.workflow import record_revival_outcome
        item = self.isolate('revival_pending')
        now = timezone.now()
        item.returned_at = now - timedelta(days=3)
        item.returned_by = self.lab_user
        item.save()
        LaboratoryIdentification.objects.create(isolate=item, organism_reported='Klebsiella pneumoniae', cassette_reference='C1')
        started = now - timedelta(days=2)
        completed = now - timedelta(days=1)
        record_revival_outcome(item.pk, 'revived', started.isoformat(), completed.isoformat(), '', self.lab_user)
        item.refresh_from_db()
        self.assertEqual(item.revival_started_at, started)
        self.assertEqual(item.revival_completed_at, completed)
        self.assertEqual(item.status, 'revived')

    def test_accept_reported_reuses_latest_validated_result(self):
        item = self.isolate('id_returned')
        item.revival_completed_at = timezone.now() - timedelta(hours=1)
        item.save(update_fields=['revival_completed_at'])
        LaboratoryIdentification.objects.create(isolate=item, organism_reported='Klebsiella pneumoniae', whonet_organism_code='KPN')
        self.client.post(reverse('laboratory_review_id', args=[item.pk]), {'action':'accept_reported'})
        item.refresh_from_db()
        self.assertEqual(item.approved_organism_code, 'KPN')
        self.assertEqual(item.status, 'id_reviewed')

    def test_lyophilization_uses_approved_code_without_reselection(self):
        item = self.isolate('id_reviewed')
        item.final_organism = 'Klebsiella pneumoniae'
        item.approved_organism_code = 'KPN'
        item.save()
        day = timezone.localdate().isoformat()
        self.client.post(reverse('laboratory_lyophilization_complete', args=[item.pk]), {'date_harvested':day,'date_lyophilized':day})
        item.refresh_from_db()
        self.assertEqual(item.status, 'ready_for_biobank')
    def test_revival_rejects_unreturned_and_invalid_dates(self):
        from apps.laboratory.workflow import record_revival_outcome, WorkflowError
        item = self.isolate('id_returned')
        now = timezone.now()
        for start, end in [(now.isoformat(), now.isoformat()), ('bad', now.isoformat()), (now.isoformat(), (now-timedelta(days=1)).isoformat())]:
            with self.assertRaises(WorkflowError):
                record_revival_outcome(item.pk, 'revived', start, end, '', self.lab_user)
        item.refresh_from_db()
        self.assertEqual(item.status, 'id_returned')

    def test_revival_retry_preserves_original_and_records_actual_attempt(self):
        from apps.laboratory.workflow import record_revival_outcome
        item = self.isolate('revival_in_progress')
        now = timezone.now()
        item.returned_at = now-timedelta(days=5)
        item.returned_by = self.lab_user
        item.revival_started_at = now-timedelta(days=4)
        item.revival_completed_at = now-timedelta(days=3)
        item.revival_retried_at = now-timedelta(days=2)
        item.revival_retry_count = 1
        item.revival_failed_reason = 'Original failure'
        item.save()
        LaboratoryIdentification.objects.create(isolate=item, organism_reported='Klebsiella pneumoniae', cassette_reference='C1')
        old_start, old_end = item.revival_started_at, item.revival_completed_at
        start, end = now-timedelta(days=1), now-timedelta(hours=1)
        record_revival_outcome(item.pk,'revived',start.isoformat(),end.isoformat(),'',self.lab_user)
        item.refresh_from_db()
        self.assertEqual(item.revival_started_at, old_start)
        self.assertEqual(item.revival_completed_at, old_end)
        self.assertIn(start.isoformat(), item.revival_failed_reason)
        self.assertIn(end.isoformat(), item.revival_failed_reason)
        self.assertIn('Original failure', item.revival_failed_reason)

    def test_wrong_role_cannot_record_revival(self):
        item = self.isolate('id_returned')
        self.lab_user.groups.clear()
        self.client.post(reverse('laboratory_revival_outcome',args=[item.pk]), {'status':'revived','started_at':'2026-01-01T08:00','completed_at':'2026-01-01T09:00'})
        item.refresh_from_db()
        self.assertEqual(item.status,'id_returned')
    def test_retry_form_start_is_not_before_retry_timestamp(self):
        item = self.isolate('revival_in_progress')
        item.revival_started_at = timezone.now() - timedelta(days=2)
        item.revival_retried_at = timezone.now() - timedelta(hours=1)
        item.revival_retry_count = 1
        item.save()
        response = self.client.get(reverse('laboratory_isolate_detail', args=[item.pk]))
        start = response.context['revival_attempt_start']
        self.assertGreaterEqual(start, item.revival_retried_at)
        self.assertEqual(start.microsecond, 0)
