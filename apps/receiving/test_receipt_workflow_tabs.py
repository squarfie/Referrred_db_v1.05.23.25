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
        self.assertEqual([card['key'] for card in cards], ['pending', 'received', 'main_lab', 'processing', 'complete'])
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
