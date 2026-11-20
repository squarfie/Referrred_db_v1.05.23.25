from django.apps import apps
from django.template.loader import get_template
from django.test import SimpleTestCase
from django.urls import resolve, reverse


class LaboratoryAppExtractionTests(SimpleTestCase):
    def test_laboratory_is_registered_without_relabeling_existing_models(self):
        self.assertTrue(apps.is_installed('apps.laboratory'))
        from apps.laboratory.models import LaboratoryIsolate
        self.assertIs(LaboratoryIsolate, apps.get_model('receiving', 'LaboratoryIsolate'))
        self.assertEqual(LaboratoryIsolate._meta.db_table, 'laboratory_isolates')

    def test_laboratory_routes_resolve_to_new_app_with_existing_names(self):
        for name, args in (
            ('laboratory_receiving_dashboard', []),
            ('laboratory_worklist', []), ('laboratory_processing_worklist', []),
            ('laboratory_biobank_list', []), ('laboratory_isolate_detail', [1]),
            ('laboratory_batch_labels', [1]), ('laboratory_revival_outcome', [1]),
            ('laboratory_batch_create', [1]), ('receiving_lab_confirm', ['00000000-0000-0000-0000-000000000001']),
        ):
            with self.subTest(route=name):
                url = reverse(name, args=args)
                self.assertTrue(url.startswith('/receiving/'))
                self.assertTrue(resolve(url).func.__module__.startswith('apps.laboratory.'), resolve(url).func.__module__)
        self.assertEqual(resolve(reverse('secretariat_receiving_dashboard')).func.__module__, 'apps.receiving.views')

    def test_laboratory_templates_have_new_home(self):
        for name in ('dashboard', 'receipt_detail', 'lab_worklist', 'lab_isolate_detail', 'laboratory_workspace', 'lab_biobank_label', 'lab_receipt_form'):
            with self.subTest(template=name):
                get_template(f'laboratory/{name}.html')
