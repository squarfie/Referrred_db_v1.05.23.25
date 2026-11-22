from datetime import date
from io import BytesIO
from types import SimpleNamespace

from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from pypdf import PdfReader

from apps.receiving import tests as fixtures
from apps.receiving.models import LaboratoryBiobank, LaboratoryIsolate


class StickerPDFLayoutTests(SimpleTestCase):
    def test_a4_pagination_and_accession_only_qr(self):
        from apps.laboratory.sticker_pdf import render_sticker_pdf
        from apps.receiving.qr import render_qr_png
        import base64
        labels = [{
            'isolate': SimpleNamespace(accession_number=f'26ARS_RI{i:04d}'),
            'code': 'KPN', 'harvest': '11/19/2026',
            'qr_data_uri': 'data:image/png;base64,' + base64.b64encode(render_qr_png(f'26ARS_RI{i:04d}')).decode('ascii'),
        } for i in range(76)]
        pdf = PdfReader(BytesIO(render_sticker_pdf(labels)))
        self.assertEqual(len(pdf.pages), 2)
        self.assertAlmostEqual(float(pdf.pages[0].mediabox.width), 595.276, places=2)
        self.assertAlmostEqual(float(pdf.pages[0].mediabox.height), 841.89, places=2)
        self.assertIn('26ARS_RI0000', pdf.pages[0].extract_text())
        self.assertNotIn('26ARS_RI0075', pdf.pages[0].extract_text())
        self.assertIn('26ARS_RI0075', pdf.pages[1].extract_text())
        self.assertEqual(len(pdf.pages[0].images), 75)


class BatchStickerPDFTests(TestCase):
    setUp = fixtures.LaboratoryReceivingWorkspaceTests.setUp
    create_pending_accession = fixtures.LaboratoryReceivingWorkspaceTests.create_pending_accession

    def test_batch_pdf_and_visible_print_option_skip_unready_isolates(self):
        accession = self.create_pending_accession()
        for index, number in enumerate(accession.accession_numbers):
            item = LaboratoryIsolate.objects.create(
                accession_range=accession, accession_number=number,
                status='ready_for_biobank' if index == 0 else 'id_pending',
                final_organism='Klebsiella pneumoniae' if index == 0 else '',
            )
            if index == 0:
                LaboratoryBiobank.objects.create(isolate=item, whonet_code='KPN', date_harvested=date(2026, 11, 19))
        url = reverse('laboratory_batch_labels', args=[accession.pk])
        page = self.client.get(reverse('laboratory_batch_detail', args=[accession.pk]))
        self.assertContains(page, 'Print batch stickers')
        preview = self.client.get(url)
        self.assertContains(preview, '?format=pdf')
        response = self.client.get(url, {'format': 'pdf'})
        self.assertEqual(response['Content-Type'], 'application/pdf')
        pdf = PdfReader(BytesIO(response.content))
        self.assertIn(accession.accession_numbers[0], pdf.pages[0].extract_text())
        self.assertNotIn(accession.accession_numbers[1], pdf.pages[0].extract_text())
        self.assertEqual(LaboratoryIsolate.objects.filter(status='ready_for_biobank').count(), 1)


class DateRangeStickerTests(TestCase):
    setUp = fixtures.LaboratoryReceivingWorkspaceTests.setUp
    create_pending_accession = fixtures.LaboratoryReceivingWorkspaceTests.create_pending_accession

    def label_isolate(self, accession, number, harvested, **changes):
        item = LaboratoryIsolate.objects.create(
            accession_range=accession, accession_number=number,
            status=changes.get('status', 'biobanked'),
            final_organism='Klebsiella pneumoniae',
        )
        LaboratoryBiobank.objects.create(
            isolate=item, date_harvested=harvested,
            date_lyophilized=date(2026, 11, 21),
            whonet_code=changes.get('code', 'KPN'),
        )
        return item

    def test_harvest_dates_include_both_boundaries_across_batches(self):
        from apps.receiving.models import LaboratoryAccession
        first = self.create_pending_accession()
        second = LaboratoryAccession.objects.create(
            laboratory_receiving=self.lab_record, date_referred=date(2026, 11, 19),
            start_number=100, end_number=101, batch_number=2, total_batches=2,
        )
        self.label_isolate(first, 'DATE-START', date(2026, 11, 18))
        self.label_isolate(second, 'DATE-END', date(2026, 11, 19))
        self.label_isolate(first, 'DATE-OUTSIDE', date(2026, 11, 20))
        self.label_isolate(second, 'DATE-INCOMPLETE', date(2026, 11, 19), code='')
        self.label_isolate(second, 'DATE-PENDING', date(2026, 11, 19), status='id_pending')
        url = reverse('laboratory_date_labels')
        params = {'from_date': '2026-11-18', 'to_date': '2026-11-19'}
        preview = self.client.get(url, params)
        self.assertEqual([label['isolate'].accession_number for label in preview.context['labels']], ['DATE-START', 'DATE-END'])
        self.assertContains(preview, 'from_date=2026-11-18&amp;to_date=2026-11-19&amp;format=pdf')
        self.assertNotContains(preview, 'Print whole batch')
        response = self.client.get(url, {**params, 'format': 'pdf'})
        self.assertEqual(response['Content-Type'], 'application/pdf')
        text = PdfReader(BytesIO(response.content)).pages[0].extract_text()
        for accession in ['DATE-START', 'DATE-END']:
            self.assertIn(accession, text)
        for accession in ['DATE-OUTSIDE', 'DATE-INCOMPLETE', 'DATE-PENDING']:
            self.assertNotIn(accession, text)

    def test_invalid_dates_show_errors_without_printing_all_records(self):
        url = reverse('laboratory_date_labels')
        for params in [{}, {'from_date': 'bad', 'to_date': '2026-11-19'},
                       {'from_date': '2026-11-20', 'to_date': '2026-11-19'}]:
            with self.subTest(params=params):
                response = self.client.get(url, {**params, 'format': 'pdf'})
                self.assertEqual(response.status_code, 400)
                self.assertTrue(response.context['sticker_date_form'].errors)
                self.assertNotEqual(response['Content-Type'], 'application/pdf')

    def test_empty_range_and_same_day_range(self):
        params = {'from_date': '2026-11-19', 'to_date': '2026-11-19'}
        url = reverse('laboratory_date_labels')
        self.assertContains(self.client.get(url, params), 'No isolates are eligible')
        response = self.client.get(url, {**params, 'format': 'pdf'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('from_date=2026-11-19', response.url)
        item = self.label_isolate(self.create_pending_accession(), 'SAME-DAY', date(2026, 11, 19))
        response = self.client.get(url, params)
        self.assertEqual([label['isolate'].pk for label in response.context['labels']], [item.pk])

    def test_inventory_has_date_print_form_and_endpoint_requires_lab_access(self):
        url = reverse('laboratory_date_labels')
        response = self.client.get(reverse('laboratory_biobank_list'))
        self.assertContains(response, f'action="{url}"')
        self.assertContains(response, 'name="from_date"')
        self.assertContains(response, 'name="to_date"')
        self.assertContains(response, 'Harvest date')
        self.lab_user.groups.clear()
        self.assertRedirects(self.client.get(url), reverse('home'), fetch_redirect_response=False)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_print_range_is_not_limited_to_inventory_display_limit(self):
        accession = self.create_pending_accession()
        items = LaboratoryIsolate.objects.bulk_create([
            LaboratoryIsolate(accession_range=accession, accession_number=f'ALL-{i:04d}',
                              status='biobanked', final_organism='Klebsiella pneumoniae')
            for i in range(251)
        ])
        LaboratoryBiobank.objects.bulk_create([
            LaboratoryBiobank(isolate=item, date_harvested=date(2026, 11, 19), whonet_code='KPN')
            for item in items
        ])
        response = self.client.get(reverse('laboratory_date_labels'), {
            'from_date': '2026-11-19', 'to_date': '2026-11-19',
        })
        self.assertEqual(len(response.context['labels']), 251)
