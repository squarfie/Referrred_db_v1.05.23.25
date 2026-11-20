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
        from apps.receiving.sticker_pdf import render_sticker_pdf
        from apps.receiving.qr import render_qr_png
        import base64
        labels = [{
            'isolate': SimpleNamespace(accession_number=f'26ARS_RI{i:04d}'),
            'code': 'KPN', 'harvest': '11/19/2026',
            'qr_data_uri': 'data:image/png;base64,' + base64.b64encode(render_qr_png(f'26ARS_RI{i:04d}')).decode('ascii'),
        } for i in range(49)]
        pdf = PdfReader(BytesIO(render_sticker_pdf(labels)))
        self.assertEqual(len(pdf.pages), 2)
        self.assertAlmostEqual(float(pdf.pages[0].mediabox.width), 595.276, places=2)
        self.assertAlmostEqual(float(pdf.pages[0].mediabox.height), 841.89, places=2)
        self.assertIn('26ARS_RI0000', pdf.pages[0].extract_text())
        self.assertNotIn('26ARS_RI0048', pdf.pages[0].extract_text())
        self.assertIn('26ARS_RI0048', pdf.pages[1].extract_text())
        self.assertEqual(len(pdf.pages[0].images), 48)


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
