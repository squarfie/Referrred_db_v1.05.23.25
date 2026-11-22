from datetime import timedelta
from uuid import uuid4

from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.receiving import tests as fixtures
from .models import LaboratoryAccession, LaboratoryIsolate, LaboratoryReceiving
from apps.home.models import SiteData


class MainLabBatchAccordionTests(TestCase):
    setUp = fixtures.LaboratoryReceivingWorkspaceTests.setUp
    create_pending_accession = fixtures.LaboratoryReceivingWorkspaceTests.create_pending_accession

    def login_main_lab(self):
        self.lab_user.groups.add(Group.objects.get_or_create(name='Laboratory Staff')[0])

    def item(self, accession, number, status='id_testing'):
        return LaboratoryIsolate.objects.create(
            accession_range=accession, accession_number=number, status=status, endorsed_at=timezone.now(),
        )

    def test_complete_large_batch_is_never_split_between_pages(self):
        accession = self.create_pending_accession()
        for number in range(35):
            self.item(accession, f'GROUP-{number:04d}')
        self.login_main_lab()
        page = self.client.get(reverse('main_lab_dashboard'), {'tab': 'testing'})
        self.assertEqual(page.context['page_obj'].paginator.count, 1)
        self.assertContains(page, '<details', count=1)
        self.assertContains(page, 'GROUP-0034')
        self.assertEqual(page.context['counts']['testing'], 35)

    def test_same_ranges_from_different_sites_stay_separate(self):
        first = self.create_pending_accession()
        receipt = type(self.receipt).objects.get(pk=self.receipt.pk)
        receipt.pk = None
        receipt.receiving_id = uuid4()
        receipt.tracking_number = 'MAIN-LAB-OTHER-SITE'
        receipt.site = SiteData.objects.create(SiteCode='APM', SiteName='Other site')
        receipt.save()
        receiving = LaboratoryReceiving.objects.create(receipt=receipt, recorded_by=self.lab_user)
        second = LaboratoryAccession.objects.create(
            laboratory_receiving=receiving, date_referred=first.date_referred,
            start_number=first.start_number, end_number=first.end_number,
        )
        self.item(first, 'RI-GROUP-1')
        self.item(second, 'APM-GROUP-1')
        self.login_main_lab()
        page = self.client.get(reverse('main_lab_dashboard'), {'tab': 'testing'})
        self.assertEqual(page.context['page_obj'].paginator.count, 2)
        self.assertContains(page, '<details', count=2)

    def test_search_and_tabs_filter_accessions_inside_the_batch(self):
        accession = self.create_pending_accession()
        testing = self.item(accession, 'FIND-TESTING')
        pending = self.item(accession, 'FIND-PENDING', status='id_pending')
        self.login_main_lab()
        page = self.client.get(reverse('main_lab_dashboard'), {'tab': 'testing', 'q': 'FIND'})
        self.assertContains(page, testing.accession_number)
        self.assertNotContains(page, pending.accession_number)
        self.assertContains(page, reverse('main_lab_isolate_detail', args=[testing.pk]))
        self.assertContains(page, ' open')
        self.assertEqual(page.context['counts']['all'], 2)

    def test_pagination_preserves_tab_and_search_and_counts_all_isolates(self):
        first = self.create_pending_accession()
        for index in range(26):
            accession = LaboratoryAccession.objects.create(
                laboratory_receiving=self.lab_record,
                date_referred=first.date_referred-timedelta(days=index+1),
                start_number=index+100, end_number=index+100,
            )
            self.item(accession, f'PAGE-{index:04d}')
        self.login_main_lab()
        page = self.client.get(reverse('main_lab_dashboard'), {'tab': 'testing', 'q': 'PAGE', 'page': 2})
        self.assertEqual(page.context['page_obj'].paginator.count, 26)
        self.assertEqual(len(page.context['main_lab_batches']), 1)
        self.assertEqual(page.context['counts']['testing'], 26)
        self.assertContains(page, 'tab=testing')
        self.assertContains(page, 'q=PAGE')
