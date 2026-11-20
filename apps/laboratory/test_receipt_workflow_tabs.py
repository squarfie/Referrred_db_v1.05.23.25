from django.test import TestCase
from django.urls import reverse
from apps.receiving import tests as fixtures
from apps.receiving.models import LaboratoryIsolate
from django.contrib.auth.models import Group, User


class ReceiptWorkflowTabsTests(TestCase):
    setUp = fixtures.LaboratoryReceivingWorkspaceTests.setUp
    create_pending_accession = fixtures.LaboratoryReceivingWorkspaceTests.create_pending_accession

    def ids(self, tab):
        response = self.client.get(reverse('laboratory_receiving_dashboard'), {'tab': tab})
        self.assertEqual(response.status_code, 200)
        return [item.pk for item in response.context['page_obj']]

    def test_pending_batch_creation_includes_completed_workflows_until_batch_exists(self):
        from apps.home.models import Batch_Table
        accession = self.create_pending_accession()
        for number in accession.accession_numbers:
            LaboratoryIsolate.objects.create(accession_range=accession, accession_number=number, status='biobanked')
        self.assertIn(self.receipt.pk, self.ids('pending_batch'))
        response = self.client.get(reverse('laboratory_receiving_dashboard'), {'tab': 'pending_batch', 'q': 'no-match'})
        self.assertEqual(next(t['count'] for t in response.context['receiving_tabs'] if t['key'] == 'pending_batch'), 1)
        self.assertEqual(response.context['page_obj'].paginator.count, 0)
        accession.batch = Batch_Table.objects.create(bat_Batch_Code='CREATED-TAB-TEST')
        accession.save()
        self.assertNotIn(self.receipt.pk, self.ids('pending_batch'))

    def test_pending_batch_creation_does_not_include_receipts_without_accessions(self):
        self.assertNotIn(self.receipt.pk, self.ids('pending_batch'))

    def test_dmu_created_matching_batch_is_excluded_without_laboratory_link(self):
        from apps.home.services.batch_creation import create_referred_batch
        accession = self.create_pending_accession()
        dmu = User.objects.create_user('dmu-batch-creator')
        dmu.groups.add(Group.objects.get_or_create(name='DMU Encoder')[0])
        create_referred_batch(
            site_code=self.receipt.site.SiteCode, referral_date=accession.date_referred,
            start_number=accession.start_number, end_number=accession.end_number,
            batch_number=accession.batch_number, total_batches=accession.total_batches,
            created_by=dmu,
        )
        accession.refresh_from_db()
        self.assertIsNone(accession.batch_id)
        self.assertNotIn(self.receipt.pk, self.ids('pending_batch'))

    def test_unrelated_batch_does_not_hide_pending_range(self):
        from apps.home.services.batch_creation import create_referred_batch
        accession = self.create_pending_accession()
        create_referred_batch(
            site_code=self.receipt.site.SiteCode, referral_date=accession.date_referred,
            start_number=6, end_number=10, batch_number=accession.batch_number,
            total_batches=accession.total_batches, created_by=self.lab_user,
        )
        self.assertIn(self.receipt.pk, self.ids('pending_batch'))

    def test_receipt_stays_pending_until_every_range_has_a_batch(self):
        from apps.home.services.batch_creation import create_referred_batch
        from apps.laboratory.models import LaboratoryAccession
        first = self.create_pending_accession()
        self.lab_record.total_isolates_received = 10
        self.lab_record.isolates_accepted = 10
        self.lab_record.save()
        second = LaboratoryAccession.objects.create(
            laboratory_receiving=self.lab_record, date_referred=first.date_referred,
            start_number=6, end_number=10, batch_number=1, total_batches=1,
        )
        for index, accession in enumerate((first, second)):
            create_referred_batch(
                site_code=self.receipt.site.SiteCode, referral_date=accession.date_referred,
                start_number=accession.start_number, end_number=accession.end_number,
                batch_number=accession.batch_number, total_batches=accession.total_batches,
                created_by=self.lab_user,
            )
            self.assertEqual(self.receipt.pk in self.ids('pending_batch'), index == 0)

    def test_receipt_moves_from_handoff_through_id_and_processing_to_complete(self):
        accession = self.create_pending_accession()
        self.assertIn(self.receipt.pk, self.ids('received'))
        items = [LaboratoryIsolate.objects.create(accession_range=accession, accession_number=n, status='id_pending') for n in accession.accession_numbers]
        self.assertNotIn(self.receipt.pk, self.ids('received'))
        self.assertIn(self.receipt.pk, self.ids('main_lab'))
        LaboratoryIsolate.objects.filter(pk__in=[i.pk for i in items]).update(status='id_returned')
        self.assertNotIn(self.receipt.pk, self.ids('main_lab'))
        self.assertIn(self.receipt.pk, self.ids('processing'))
        LaboratoryIsolate.objects.filter(pk__in=[i.pk for i in items]).update(status='biobanked')
        self.assertIn(self.receipt.pk, self.ids('complete'))
        self.assertNotIn(self.receipt.pk, self.ids('processing'))
        self.receipt.refresh_from_db()
        self.assertEqual(self.receipt.status, 'lab_received')

    def test_partial_accessioning_and_failed_revival_are_not_complete(self):
        accession = self.create_pending_accession()
        for number in accession.accession_numbers[:-1]:
            LaboratoryIsolate.objects.create(accession_range=accession, accession_number=number, status='biobanked')
        self.assertNotIn(self.receipt.pk, self.ids('complete'))
        item = LaboratoryIsolate.objects.create(accession_range=accession, accession_number=accession.accession_numbers[-1], status='revival_failed')
        self.assertNotIn(self.receipt.pk, self.ids('complete'))
        item.status = 'non_viable'
        item.save()
        self.assertIn(self.receipt.pk, self.ids('complete'))

    def test_tab_counts_ignore_search_and_completed_badge_is_clear(self):
        accession = self.create_pending_accession()
        for number in accession.accession_numbers:
            LaboratoryIsolate.objects.create(accession_range=accession, accession_number=number, status='biobanked')
        response = self.client.get(reverse('laboratory_receiving_dashboard'), {'tab': 'complete', 'q': 'not-matching'})
        self.assertEqual(next(t['count'] for t in response.context['receiving_tabs'] if t['key'] == 'complete'), 1)
        self.assertEqual(response.context['page_obj'].paginator.count, 0)
        response = self.client.get(reverse('laboratory_receiving_dashboard'), {'tab': 'complete'})
        self.assertContains(response, 'Workflow Complete')

    def test_laboratory_cards_match_tabs_and_secretariat_keeps_courier_cards(self):
        self.create_pending_accession()
        response = self.client.get(reverse('laboratory_receiving_dashboard'))
        cards = response.context['lab_summary_cards']
        self.assertEqual([card['key'] for card in cards], ['pending', 'received', 'main_lab', 'processing', 'pending_batch', 'complete'])
        self.assertEqual(next(card['count'] for card in cards if card['key'] == 'received'), 1)
        self.assertNotContains(response, '<div class="receiving-stat-label">Received by Secretariat</div>')
        user = User.objects.create_user('tab-secretariat')
        user.groups.add(Group.objects.get_or_create(name='Secretariat')[0])
        self.client.force_login(user)
        response = self.client.get(reverse('secretariat_receiving_dashboard'))
        self.assertEqual(response.context['lab_summary_cards'], [])
        self.assertContains(response, '<div class="receiving-stat-label">Received by Secretariat</div>')
        self.assertNotContains(response, 'Pending Main Lab Identification')

    def test_mixed_id_and_returned_receipt_stays_pending_until_last_id_return(self):
        accession = self.create_pending_accession()
        for index, number in enumerate(accession.accession_numbers):
            LaboratoryIsolate.objects.create(accession_range=accession, accession_number=number, status='id_testing' if index == 0 else 'biobanked')
        self.assertIn(self.receipt.pk, self.ids('main_lab'))
        self.assertNotIn(self.receipt.pk, self.ids('complete'))

    def test_all_rejected_receipt_is_complete_but_undecided_receipt_is_not(self):
        self.assertNotIn(self.receipt.pk, self.ids('complete'))
        self.lab_record.total_isolates_received = 2
        self.lab_record.isolates_rejected = 2
        self.lab_record.save()
        self.assertIn(self.receipt.pk, self.ids('complete'))
