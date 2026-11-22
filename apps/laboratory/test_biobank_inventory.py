from datetime import date
from uuid import uuid4

from django.test import TestCase
from django.urls import reverse

from apps.receiving import tests as fixtures
from apps.home.models import SiteData
from apps.receiving.models import LaboratoryAccession, LaboratoryIsolate, LaboratoryReceiving


class BiobankInventoryTests(TestCase):
    setUp = fixtures.LaboratoryReceivingWorkspaceTests.setUp
    create_pending_accession = fixtures.LaboratoryReceivingWorkspaceTests.create_pending_accession

    def test_site_search_finds_complete_batch_beyond_old_record_limit(self):
        accession = self.create_pending_accession()
        LaboratoryIsolate.objects.bulk_create([
            LaboratoryIsolate(accession_range=accession, accession_number=f'26ARS_RI{i:04d}', status='biobanked')
            for i in range(1, 252)
        ])
        response = self.client.get(reverse('laboratory_biobank_list'), {'q': 'RI'})
        self.assertContains(response, '26ARS_RI0251')
        self.assertContains(response, '<details', count=1)
        self.assertContains(response, reverse('laboratory_batch_labels', args=[accession.pk]))

    def test_range_search_keeps_batches_distinct_and_printing_scoped(self):
        first = self.create_pending_accession()
        second = LaboratoryAccession.objects.create(
            laboratory_receiving=self.lab_record, date_referred=date(2026, 11, 20),
            start_number=100, end_number=101, batch_number=2, total_batches=2,
        )
        for accession in (first, second):
            LaboratoryIsolate.objects.create(accession_range=accession,
                accession_number=accession.accession_numbers[0], status='biobanked')
        response = self.client.get(reverse('laboratory_biobank_list'), {'q': '0100-0101'})
        self.assertContains(response, second.accession_numbers[0])
        self.assertNotContains(response, first.accession_numbers[0])
        self.assertContains(response, reverse('laboratory_batch_labels', args=[second.pk]))
        self.assertNotContains(response, reverse('laboratory_batch_labels', args=[first.pk]))

    def test_pagination_is_by_batch(self):
        for number in range(26):
            accession = LaboratoryAccession.objects.create(
                laboratory_receiving=self.lab_record, date_referred=date(2026, 11, 20),
                start_number=number + 1, end_number=number + 1,
            )
            LaboratoryIsolate.objects.create(accession_range=accession,
                accession_number=accession.accession_numbers[0], status='biobanked')
        response = self.client.get(reverse('laboratory_biobank_list'), {'q': 'RI', 'page': 2})
        self.assertEqual(response.context['page_obj'].paginator.count, 26)
        self.assertEqual(len(response.context['inventory_batches']), 1)
        self.assertContains(response, 'q=RI')

    def test_identical_ranges_at_different_sites_have_separate_print_targets(self):
        first = self.create_pending_accession()
        receipt = type(self.receipt).objects.get(pk=self.receipt.pk)
        receipt.pk = None
        receipt.receiving_id = uuid4()
        receipt.tracking_number = 'OTHER-SITE'
        receipt.site = SiteData.objects.create(SiteCode='APM', SiteName='Other site')
        receipt.save()
        receiving = LaboratoryReceiving.objects.create(receipt=receipt, recorded_by=self.lab_user)
        second = LaboratoryAccession.objects.create(
            laboratory_receiving=receiving, date_referred=first.date_referred,
            start_number=first.start_number, end_number=first.end_number,
        )
        for accession in (first, second):
            LaboratoryIsolate.objects.create(accession_range=accession,
                accession_number=accession.accession_numbers[0], status='biobanked')
        response = self.client.get(reverse('laboratory_biobank_list'))
        self.assertContains(response, '<details', count=2)
        self.assertContains(response, f'Print batch stickers for RI {first.reference_range}')
        self.assertContains(response, f'Print batch stickers for APM {second.reference_range}')
        preview = self.client.get(reverse('laboratory_batch_labels', args=[second.pk]))
        self.assertEqual(preview.context['unready'], [second.accession_numbers[0]])
        filtered = self.client.get(reverse('laboratory_biobank_list'), {'q': 'APM'})
        self.assertContains(filtered, second.accession_numbers[0])
        self.assertNotContains(filtered, first.accession_numbers[0])
