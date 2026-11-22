from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.receiving import tests as fixtures
from .models import LaboratoryIsolate


class DashboardTabCountTests(TestCase):
    setUp = fixtures.LaboratoryReceivingWorkspaceTests.setUp
    create_pending_accession = fixtures.LaboratoryReceivingWorkspaceTests.create_pending_accession

    def test_main_lab_tabs_show_full_queue_isolate_counts_independent_of_search(self):
        accession = self.create_pending_accession()
        now = timezone.now()
        for number, status in zip(accession.accession_numbers, ('id_pending', 'id_testing', 'id_testing', 'id_returned')):
            LaboratoryIsolate.objects.create(
                accession_range=accession, accession_number=number, status=status,
                endorsed_at=now, returned_at=now if status == 'id_returned' else None,
            )
        self.lab_user.groups.add(Group.objects.get_or_create(name='Laboratory Staff')[0])
        page = self.client.get(reverse('main_lab_dashboard'), {'tab': 'testing', 'q': 'no-match'})
        self.assertEqual(page.context['page_obj'].paginator.count, 0)
        self.assertEqual({tab['key']: tab['count'] for tab in page.context['main_lab_tabs']},
                         {'awaiting': 1, 'testing': 2, 'returned': 1, 'all': 4})
        self.assertContains(page, '2 isolates')
        self.assertContains(page, 'class="receiving-tab-count"', count=4)

    def test_processing_scope_counts_are_isolates_and_ignore_search(self):
        accession = self.create_pending_accession()
        for number, status in zip(accession.accession_numbers, ('revival_pending', 'id_returned', 'biobanked')):
            LaboratoryIsolate.objects.create(
                accession_range=accession, accession_number=number, status=status,
                returned_at=timezone.now() if status != 'revival_pending' else None,
            )
        page = self.client.get(reverse('laboratory_processing_worklist'), {'q': 'no-match'})
        self.assertEqual(page.context['batches'], [])
        self.assertEqual(page.context['scope_counts'], {'active': 2, 'history': 2})
        self.assertContains(page, 'class="receiving-tab-count"', count=2)

    def test_empty_main_lab_tabs_keep_zero_count_badges(self):
        self.lab_user.groups.add(Group.objects.get_or_create(name='Laboratory Staff')[0])
        page = self.client.get(reverse('main_lab_dashboard'))
        self.assertContains(page, 'class="receiving-tab-count"', count=4)
        self.assertTrue(all(tab['count'] == 0 for tab in page.context['main_lab_tabs']))
