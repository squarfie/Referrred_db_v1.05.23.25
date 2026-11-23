from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.receiving import tests as fixtures
from apps.receiving.models import LaboratoryIsolate
from django.contrib.auth.models import Group, User


class ReceiptWorkflowTabsTests(TestCase):
    setUp = fixtures.LaboratoryReceivingWorkspaceTests.setUp
    create_pending_accession = fixtures.LaboratoryReceivingWorkspaceTests.create_pending_accession

    def completed_fields(self):
        now = timezone.now()
        return {
            'status': 'biobanked', 'identification_source': 'main_lab',
            'returned_at': now, 'reviewed_at': now,
            'lyophilization_completed_at': now, 'biobanked_at': now,
        }

    def ids(self, tab):
        response = self.client.get(reverse('laboratory_receiving_dashboard'), {'tab': tab})
        self.assertEqual(response.status_code, 200)
        return [item.pk for item in response.context['page_obj']]

    def test_pending_batch_creation_includes_completed_workflows_until_batch_exists(self):
        from apps.home.models import Batch_Table
        accession = self.create_pending_accession()
        for number in accession.accession_numbers:
            LaboratoryIsolate.objects.create(accession_range=accession, accession_number=number, **self.completed_fields())
        self.assertIn(self.receipt.pk, self.ids('pending_batch'))
        response = self.client.get(reverse('laboratory_receiving_dashboard'), {'tab': 'pending_batch', 'q': 'no-match'})
        self.assertEqual(response.context['pending_batch_count'], 1)
        self.assertEqual(response.context['active_stage'], 'batch_creation')
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
        self.assertIn(self.receipt.pk, self.ids('id_complete'))
        self.assertNotIn(self.receipt.pk, self.ids('processing'))
        LaboratoryIsolate.objects.filter(pk__in=[i.pk for i in items]).update(**self.completed_fields())
        self.assertIn(self.receipt.pk, self.ids('complete'))
        self.assertNotIn(self.receipt.pk, self.ids('processing'))
        self.receipt.refresh_from_db()
        self.assertEqual(self.receipt.status, 'lab_received')

    def test_partial_accessioning_and_failed_revival_are_not_complete(self):
        accession = self.create_pending_accession()
        for number in accession.accession_numbers[:-1]:
            LaboratoryIsolate.objects.create(accession_range=accession, accession_number=number, **self.completed_fields())
        self.assertNotIn(self.receipt.pk, self.ids('complete'))
        item = LaboratoryIsolate.objects.create(accession_range=accession, accession_number=accession.accession_numbers[-1], status='revival_failed')
        self.assertNotIn(self.receipt.pk, self.ids('complete'))
        item.status = 'non_viable'
        item.save()
        self.assertIn(self.receipt.pk, self.ids('complete'))

    def test_tab_counts_ignore_search_and_completed_badge_is_clear(self):
        accession = self.create_pending_accession()
        for number in accession.accession_numbers:
            LaboratoryIsolate.objects.create(accession_range=accession, accession_number=number, **self.completed_fields())
        response = self.client.get(reverse('laboratory_receiving_dashboard'), {'tab': 'complete', 'q': 'not-matching'})
        self.assertEqual(next(t['count'] for t in response.context['receiving_tabs'] if t['key'] == 'complete'), 1)
        self.assertEqual(response.context['page_obj'].paginator.count, 0)
        response = self.client.get(reverse('laboratory_receiving_dashboard'), {'tab': 'complete'})
        self.assertContains(response, 'Completed')

    def test_laboratory_cards_match_tabs_and_secretariat_keeps_courier_cards(self):
        self.create_pending_accession()
        response = self.client.get(reverse('laboratory_receiving_dashboard'))
        cards = response.context['lab_summary_cards']
        self.assertEqual([card['key'] for card in cards], ['pending_receipt', 'action', 'main_lab', 'complete'])
        self.assertEqual([tab['key'] for tab in response.context['receiving_tabs']],
                         ['action', 'main_lab', 'complete', 'all'])
        self.assertEqual(next(card['count'] for card in cards if card['key'] == 'action'), 1)
        self.assertNotContains(response, '<div class="receiving-stat-label">Isolates Received</div>')
        user = User.objects.create_user('tab-secretariat')
        user.groups.add(Group.objects.get_or_create(name='Secretariat')[0])
        self.client.force_login(user)
        response = self.client.get(reverse('secretariat_receiving_dashboard'))
        self.assertEqual(response.context['lab_summary_cards'], [])
        self.assertContains(response, '<div class="receiving-stat-label">Isolates Received</div>')
        self.assertNotContains(response, 'Pending Main Lab Identification')

    def test_four_tabs_and_task_filters_keep_workflow_receipts_distinct(self):
        accession = self.create_pending_accession()
        url = reverse('laboratory_receiving_dashboard')
        response = self.client.get(url)
        self.assertEqual(response.context['active_tab'], 'action')
        self.assertIn(self.receipt.pk, [r.pk for r in response.context['page_obj']])
        self.assertEqual(len(response.context['receiving_tabs']), 4)
        self.assertIn(self.receipt.pk, self.ids('action'))
        self.assertIn(self.receipt.pk, self.ids('received'))  # legacy bookmark
        self.assertNotIn(self.receipt.pk, self.ids('main_lab'))
        self.assertNotIn(self.receipt.pk, self.ids('complete'))
        for number in accession.accession_numbers:
            LaboratoryIsolate.objects.create(
                accession_range=accession, accession_number=number, status='id_testing')
        self.assertIn(self.receipt.pk, self.ids('main_lab'))
        self.assertNotIn(self.receipt.pk, self.ids('action'))
        self.assertEqual(self.client.get(url, {'tab': 'action', 'stage': 'processing'})
                         .context['page_obj'].paginator.count, 0)

    def test_batch_creation_is_a_separate_task_not_a_fifth_tab(self):
        accession = self.create_pending_accession()
        for number in accession.accession_numbers:
            LaboratoryIsolate.objects.create(
                accession_range=accession, accession_number=number, **self.completed_fields())
        url = reverse('laboratory_receiving_dashboard')
        completed = self.client.get(url, {'tab': 'complete'})
        self.assertIn(self.receipt.pk, [r.pk for r in completed.context['page_obj']])
        self.assertEqual(completed.context['pending_batch_count'], 1)
        self.assertEqual(len(completed.context['receiving_tabs']), 4)
        task = self.client.get(url, {'tab': 'action', 'stage': 'batch_creation'})
        self.assertEqual(task.context['active_stage'], 'batch_creation')
        self.assertIn(self.receipt.pk, [r.pk for r in task.context['page_obj']])
        self.assertContains(task, 'Create Batch')

    def test_subfilters_are_preserved_on_search_and_pagination(self):
        self.create_pending_accession()
        url = reverse('laboratory_receiving_dashboard')
        response = self.client.get(url, {
            'tab': 'action', 'stage': 'accessioning', 'q': self.receipt.tracking_number})
        self.assertEqual(response.context['active_stage'], 'accessioning')
        self.assertIn(self.receipt.pk, [r.pk for r in response.context['page_obj']])
        self.assertContains(response, 'name="stage" value="accessioning"')
        self.assertContains(response, 'tab=action&amp;stage=accessioning')

    def test_bench_a_stage_badges_match_tasks_and_ignore_search(self):
        accession = self.create_pending_accession()
        url = reverse('laboratory_receiving_dashboard')
        response = self.client.get(url, {'tab': 'action', 'q': 'no-matching-receipts'})
        self.assertEqual(response.status_code, 200)
        counts = {task['key']: task['count'] for task in response.context['stage_filters']}
        self.assertEqual(counts, {
            'receipt': 0,
            'accessioning': 1,
            'processing': 0,
            'lyophilization': 0,
        })
        self.assertContains(response, 'aria-label="1 pending receipt"')
        self.assertEqual(response.context['page_obj'].paginator.count, 0)
        for number in accession.accession_numbers:
            LaboratoryIsolate.objects.create(
                accession_range=accession, accession_number=number, status='id_complete')
        response = self.client.get(url, {'tab': 'action', 'stage': 'lyophilization'})
        counts = {task['key']: task['count'] for task in response.context['stage_filters']}
        self.assertEqual(counts['accessioning'], 0)
        self.assertEqual(counts['lyophilization'], 1)
        self.assertEqual(response.context['page_obj'].paginator.count, 1)

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
