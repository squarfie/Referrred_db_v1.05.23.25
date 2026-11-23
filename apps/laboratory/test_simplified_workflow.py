from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.receiving import tests as fixtures
from .models import LaboratoryIsolate, LaboratoryIdentification, LaboratoryBiobank
from .workflow import WorkflowError, create_id_handoff, save_biobank, complete_lyophilization


class SimplifiedWorkflowTests(TestCase):
    create_pending_accession = fixtures.LaboratoryReceivingWorkspaceTests.create_pending_accession

    def setUp(self):
        fixtures.LaboratoryReceivingWorkspaceTests.setUp(self)
        self.main_user = User.objects.create_user('simplified-main-lab')
        self.main_user.groups.add(Group.objects.get_or_create(name='Laboratory Staff')[0])
        self.accession = self.create_pending_accession()
        self.now = timezone.now()

    def isolate(self, status='revived', **extra):
        values = dict(accession_range=self.accession,
                      accession_number=self.accession.accession_numbers[0], status=status,
                      revival_started_at=self.now - timedelta(hours=2),
                      revival_completed_at=self.now - timedelta(hours=1),
                      site_organism_code='KPN', site_organism_name='Klebsiella pneumoniae')
        values.update(extra)
        return LaboratoryIsolate.objects.create(**values)

    def services(self):
        from . import identification_workflow
        return identification_workflow

    def test_batch_endorses_revived_isolates_without_queue_step(self):
        for number in self.accession.accession_numbers:
            LaboratoryIsolate.objects.create(accession_range=self.accession,
                accession_number=number, status='revived', revival_started_at=self.now-timedelta(hours=2),
                revival_completed_at=self.now-timedelta(hours=1),
                site_organism_code='KPN', site_organism_name='Klebsiella pneumoniae')
        self.assertTrue(create_id_handoff(self.accession.pk, self.lab_user))
        self.assertEqual(self.accession.isolate_records.filter(status='id_pending').count(), 5)
        self.assertFalse(self.accession.isolate_records.filter(id_queued_at__isnull=False).exists())
        self.assertFalse(create_id_handoff(self.accession.pk, self.lab_user))

    def test_result_completion_does_not_record_physical_return_and_is_idempotent(self):
        item = self.isolate('id_testing', endorsed_at=self.now, acknowledged_at=self.now)
        service = self.services()
        result = service.record_identification(item.pk, 'KPN', 'Klebsiella pneumoniae', self.main_user)
        again = service.record_identification(item.pk, 'KPN', 'Klebsiella pneumoniae', self.main_user)
        self.assertEqual(result.pk, again.pk)
        item.refresh_from_db()
        self.assertEqual(item.status, 'id_complete')
        self.assertIsNotNone(item.id_completed_at)
        self.assertIsNone(item.returned_at)
        self.assertIsNone(item.reviewed_at)
        self.assertEqual(item.identifications.count(), 1)

    def test_endorsement_alone_does_not_allow_main_lab_result_before_acknowledgment(self):
        item = self.isolate('id_testing', endorsed_at=self.now)
        with self.assertRaises(WorkflowError):
            self.services().record_identification(
                item.pk, 'KPN', 'Klebsiella pneumoniae', self.main_user)
        item.refresh_from_db()
        self.assertEqual(item.status, 'id_testing')
        self.assertIsNone(item.id_completed_at)
        self.assertFalse(item.identifications.exists())

    def test_physical_cassette_return_does_not_complete_an_unfinished_id(self):
        from .models import LaboratoryIdentification
        item = self.isolate('id_testing', endorsed_at=self.now, acknowledged_at=self.now)
        LaboratoryIdentification.objects.create(
            isolate=item, organism_reported='Klebsiella pneumoniae',
            whonet_organism_code='KPN', cassette_reference='C1', recorded_by=self.main_user)
        with self.assertRaises(WorkflowError):
            self.services().record_physical_return(item.pk, self.main_user)
        item.refresh_from_db()
        self.assertEqual(item.status, 'id_testing')
        self.assertIsNone(item.id_completed_at)
        self.assertIsNone(item.returned_at)

    def test_actual_cassette_return_does_not_change_completed_workflow_status(self):
        item = self.isolate('id_testing', endorsed_at=self.now, acknowledged_at=self.now)
        self.services().record_identification(
            item.pk, 'KPN', 'Klebsiella pneumoniae', self.main_user,
            cassette_reference='C1')
        self.services().record_physical_return(item.pk, self.main_user)
        item.refresh_from_db()
        self.assertEqual(item.status, 'id_complete')
        self.assertIsNotNone(item.id_completed_at)
        self.assertIsNotNone(item.returned_at)
        self.assertIsNone(item.reviewed_at)

    def test_retrieval_preserves_storage_and_has_no_invented_acknowledgment(self):
        item = self.isolate('site_banked', identification_source='site',
            approved_organism_code='KPN', final_organism='Klebsiella pneumoniae',
            site_banked_at=self.now, site_bank_storage={'storage_box': 'Temp', 'box_position': 'A1'})
        bank = LaboratoryBiobank.objects.create(isolate=item, storage_box='Temp', box_position='A1', whonet_code='KPN')
        service = self.services()
        with self.assertRaises(WorkflowError):
            service.retrieve_for_testing(item.pk, self.lab_user)
        service.retrieve_for_testing(item.pk, self.main_user)
        item.refresh_from_db()
        stamp = item.retrieved_at
        self.assertEqual(item.status, 'id_testing')
        self.assertIsNone(item.acknowledged_at)
        self.assertIsNone(item.endorsed_at)
        self.assertEqual(item.retrieved_storage['storage_box'], 'Temp')
        self.assertEqual(item.site_bank_storage['storage_box'], 'Temp')
        service.retrieve_for_testing(item.pk, self.main_user)
        item.refresh_from_db()
        self.assertEqual(item.retrieved_at, stamp)
        bank.refresh_from_db()
        self.assertEqual(bank.box_position, 'A1')

    def test_final_banking_without_return_or_review_requires_bench_a_confirmation(self):
        item = self.isolate('id_testing', endorsed_at=self.now, acknowledged_at=self.now)
        service = self.services()
        service.record_identification(item.pk, 'KPN', 'Klebsiella pneumoniae', self.main_user)
        service.proceed_to_lyophilization(item.pk, self.lab_user)
        item.refresh_from_db()
        self.assertEqual(item.final_organism, '')
        complete_lyophilization(item.pk, timezone.localdate().isoformat(), timezone.localdate().isoformat(), self.lab_user)
        with self.assertRaises(WorkflowError):
            save_biobank(item.pk, {'storage_box': 'Final', 'box_position': 'B1'}, self.lab_user)
        service.finalize_banking(item.pk, {'storage_box': 'Final', 'box_position': 'B1'},
                                 'KPN', 'Klebsiella pneumoniae', self.lab_user)
        item.refresh_from_db()
        self.assertEqual(item.status, 'biobanked')
        self.assertIsNone(item.returned_at)
        self.assertIsNone(item.reviewed_at)
        self.assertIsNotNone(item.final_identified_at)

    def test_final_id_correction_is_audited_without_reopening_banking(self):
        item = self.isolate('biobanked', identification_source='main_lab', final_organism='Klebsiella pneumoniae',
            approved_organism_code='KPN', biobanked_at=self.now)
        LaboratoryBiobank.objects.create(isolate=item, whonet_code='KPN', storage_box='Final', box_position='B1')
        service = self.services()
        for user, reason in ((self.main_user, 'Correction'), (self.lab_user, '')):
            with self.assertRaises(WorkflowError):
                service.correct_identification(item.pk, 'final', 'ECO', 'Escherichia coli', reason, user)
        service.correct_identification(item.pk, 'final', 'ECO', 'Escherichia coli', 'Verified transcription error', self.lab_user)
        item.refresh_from_db()
        self.assertEqual(item.approved_organism_code.upper(), 'ECO')
        self.assertEqual(item.biobank.whonet_code.upper(), 'ECO')
        self.assertEqual(item.biobanked_at, self.now)
        self.assertEqual(item.status, 'biobanked')
        correction = item.identification_corrections.get()
        self.assertEqual(correction.previous_code, 'KPN')
        self.assertEqual(correction.new_code.upper(), 'ECO')
        self.assertEqual(correction.performed_by, self.lab_user)

    def test_main_lab_correction_preserves_original_and_final_id(self):
        item = self.isolate('biobanked', final_organism='Klebsiella pneumoniae', approved_organism_code='KPN')
        original = LaboratoryIdentification.objects.create(isolate=item,
            organism_reported='Klebsiella pneumoniae', whonet_organism_code='KPN', recorded_by=self.main_user)
        self.services().correct_identification(item.pk, 'main_lab', 'ECO', 'Escherichia coli',
            'Instrument amendment', self.main_user, result_id=original.pk)
        original.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(original.whonet_organism_code, 'KPN')
        self.assertEqual(item.approved_organism_code, 'KPN')
        self.assertEqual(item.identifications.count(), 2)
        self.assertEqual(item.identification_corrections.get().original_result_id, original.pk)

    def test_site_correction_after_endorsement_preserves_main_lab_and_has_reason(self):
        item = self.isolate('id_testing', endorsed_at=self.now)
        self.services().correct_identification(item.pk, 'site', 'ECO', 'Escherichia coli',
            'Site corrected its submission', self.lab_user)
        item.refresh_from_db()
        self.assertEqual(item.site_organism_code.upper(), 'ECO')
        self.assertEqual(item.status, 'id_testing')
        self.assertEqual(item.endorsed_at, self.now)

    def test_invalid_organism_correction_rolls_back(self):
        item = self.isolate()
        with self.assertRaises(WorkflowError):
            self.services().correct_identification(item.pk, 'site', 'KPN', 'Escherichia coli', 'Mismatch', self.lab_user)
        item.refresh_from_db()
        self.assertEqual(item.site_organism_code, 'KPN')

    def test_save_endpoint_completes_testing_and_bench_a_sees_id_complete(self):
        item = self.isolate('id_testing', endorsed_at=self.now, acknowledged_at=self.now)
        self.client.force_login(self.main_user)
        response = self.client.post(reverse('laboratory_record_id', args=[item.pk]),
            {'organism_reported_code': 'KPN', 'organism_reported': 'Klebsiella pneumoniae'}, follow=True)
        self.assertEqual(response.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.status, 'id_complete')
        self.assertIsNone(item.returned_at)
        dashboard = self.client.get(reverse('main_lab_dashboard'), {'tab': 'completed'})
        self.assertContains(dashboard, item.accession_number)
        self.client.force_login(self.lab_user)
        dashboard = self.client.get(reverse('laboratory_receiving_dashboard'), {'tab': 'id_complete'})
        self.assertContains(dashboard, self.receipt.tracking_number)
        detail = self.client.get(reverse('laboratory_isolate_detail', args=[item.pk]))
        self.assertContains(detail, 'Proceed to lyophilization')
        self.assertNotContains(detail, 'Accept reported identification')

    def test_main_lab_inventory_offers_retrieval_and_no_bench_a_menu(self):
        item = self.isolate('site_banked', identification_source='site', site_banked_at=self.now,
                            final_organism='Klebsiella pneumoniae', approved_organism_code='KPN')
        LaboratoryBiobank.objects.create(isolate=item, storage_box='Temporary', box_position='A1')
        self.client.force_login(self.main_user)
        response = self.client.get(reverse('laboratory_biobank_list'), {'tab': 'temporary', 'q': 'RI'})
        self.assertContains(response, 'Retrieve for ID testing')
        response = self.client.post(reverse('laboratory_retrieve_for_testing', args=[item.pk]),
            {'next': 'https://untrusted.invalid/'})
        self.assertRedirects(response, reverse('main_lab_isolate_detail', args=[item.pk]), fetch_redirect_response=False)

    def test_correction_endpoint_keeps_reason_and_history_visible(self):
        item = self.isolate('id_testing', endorsed_at=self.now)
        response = self.client.post(reverse('laboratory_identification_correct', args=[item.pk]),
            {'kind': 'site', 'organism_code': 'ECO', 'organism_name': 'Escherichia coli',
             'reason': 'Verified referral amendment'}, follow=True)
        self.assertContains(response, 'Verified referral amendment')
        self.assertContains(response, 'Klebsiella pneumoniae')
        self.assertContains(response, 'Escherichia coli')

    def test_main_lab_completion_import_is_atomic_and_does_not_return_cassettes(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        item = self.isolate('id_testing', endorsed_at=self.now, acknowledged_at=self.now)
        data = ('accession_number,organism_reported,instrument,cassette_reference,organism_code\n'
                f'{item.accession_number},Klebsiella pneumoniae,Vitek,,KPN\n').encode()
        self.client.force_login(self.main_user)
        url = reverse('laboratory_id_upload')
        self.client.post(url, {'file': SimpleUploadedFile('ids.csv', data)})
        response = self.client.post(url, {'file': SimpleUploadedFile('ids.csv', data), 'action': 'commit'})
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.status, 'id_complete')
        self.assertIsNone(item.returned_at)

    def test_failed_multirow_import_rolls_back_completed_id_and_results(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        item = self.isolate('id_testing', endorsed_at=self.now, acknowledged_at=self.now)
        # Two conflicting results for one accession in a single import must not
        # leave a partly completed isolate when the second row is rejected.
        header = 'accession_number,organism_reported,instrument,cassette_reference,organism_code\n'
        row = f'{item.accession_number},Klebsiella pneumoniae,Vitek,C1,KPN\n'
        different_row = row.replace('Vitek,C1', 'Phoenix,C2')
        data = (header + row + different_row).encode()
        self.client.force_login(self.main_user)
        url = reverse('laboratory_id_upload')
        preview = self.client.post(url, {'file': SimpleUploadedFile('ids.csv', data),
                                         'action': 'preview'})
        self.assertEqual(preview.status_code, 200)
        response = self.client.post(url, {'file': SimpleUploadedFile('ids.csv', data),
                                          'action': 'commit'})
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.status, 'id_testing')
        self.assertIsNone(item.id_completed_at)
        self.assertEqual(item.identifications.count(), 0)

    def test_query_context_returns_to_filtered_inventory(self):
        item = self.isolate('biobanked', identification_source='main_lab',
                            final_organism='Klebsiella pneumoniae', approved_organism_code='KPN')
        target = reverse('laboratory_biobank_list') + '?tab=final&q=RI&page=2'
        self.client.get(reverse('laboratory_isolate_detail', args=[item.pk]), {'next': target})
        response = self.client.post(reverse('laboratory_identification_correct', args=[item.pk]),
            {'kind': 'final', 'organism_code': 'ECO', 'organism_name': 'Escherichia coli',
             'reason': 'Correction', 'next': target})
        self.assertRedirects(response, target, fetch_redirect_response=False)

    def test_unlisted_result_allows_independent_validated_lyophilization_organism(self):
        item = self.isolate('id_testing', endorsed_at=self.now, acknowledged_at=self.now)
        service = self.services()
        service.record_identification(item.pk, '', 'Unlisted instrument identification', self.main_user)
        service.proceed_to_lyophilization(item.pk, self.lab_user)
        day = timezone.localdate().isoformat()
        complete_lyophilization(item.pk, day, day, self.lab_user, whonet_code='ECO')
        item.refresh_from_db()
        self.assertEqual(item.status, 'ready_for_biobank')
        self.assertEqual(item.biobank.whonet_code, 'ECO')
        self.assertEqual(item.final_organism, '')
        self.assertEqual(item.identifications.get().organism_reported, 'Unlisted instrument identification')

    def test_main_lab_can_correct_completed_storage_without_changing_final_id(self):
        item = self.isolate('biobanked', identification_source='main_lab', final_organism='Klebsiella pneumoniae',
            approved_organism_code='KPN', endorsed_at=self.now, biobanked_at=self.now,
            returned_at=self.now, reviewed_at=self.now, lyophilization_completed_at=self.now)
        LaboratoryBiobank.objects.create(isolate=item, whonet_code='KPN', storage_box='Old', box_position='A1',
                                         supplementary_box_name='Historic', supplementary_position='D7', remarks='Preserve this note')
        self.client.force_login(self.main_user)
        page = self.client.get(reverse('laboratory_storage_detail', args=[item.pk]))
        self.assertContains(page, 'name="storage_box"')
        self.assertNotContains(page, 'name="final_organism_code"')
        response = self.client.post(reverse('laboratory_biobank_save', args=[item.pk]),
            {'storage_box': 'New', 'box_position': 'C2'})
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.biobank.storage_box, 'New')
        self.assertEqual(item.biobank.supplementary_box_name, 'Historic')
        self.assertEqual(item.biobank.supplementary_position, 'D7')
        self.assertEqual(item.biobank.remarks, 'Preserve this note')
        self.assertEqual(item.approved_organism_code, 'KPN')
        self.assertEqual(item.biobanked_at, self.now)

    def test_corrected_final_id_is_used_by_all_sticker_entry_points(self):
        from io import BytesIO
        from pypdf import PdfReader
        item = self.isolate('biobanked', identification_source='main_lab', final_organism='Klebsiella pneumoniae',
            approved_organism_code='KPN', biobanked_at=self.now)
        day = timezone.localdate()
        LaboratoryBiobank.objects.create(isolate=item, whonet_code='KPN', date_harvested=day,
            date_lyophilized=day, storage_box='Final', box_position='B1')
        self.services().correct_identification(item.pk, 'final', 'ECO', 'Escherichia coli',
                                               'Confirmed correction', self.lab_user)
        single = self.client.get(reverse('laboratory_biobank_label', args=[item.pk]))
        self.assertContains(single, 'ECO')
        self.assertNotContains(single, 'KPN')
        for route, args, query in (
            ('laboratory_batch_labels', [self.accession.pk], {'format': 'pdf'}),
            ('laboratory_date_labels', [], {'from_date': day.isoformat(), 'to_date': day.isoformat(), 'format': 'pdf'}),
        ):
            response = self.client.get(reverse(route, args=args), query)
            self.assertEqual(response.status_code, 200)
            pdf_text = ''.join(page.extract_text() for page in PdfReader(BytesIO(response.content)).pages)
            self.assertIn('ECO', pdf_text)
            self.assertNotIn('KPN', pdf_text)
