"""RIS-only encoding must survive saving and reach laboratory reports."""
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase
from django.template.loader import get_template, render_to_string

from apps.home import views as raw_views
from apps.home.models import AntibioticEntry, Antibiotic_List, Batch_Table, Referred_Data
from apps.home_final import views as final_views
from apps.home_final.models import Final_AntibioticEntry, Final_Data


class RisOnlyTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user("ris-checker")
        self.user.groups.add(Group.objects.get_or_create(name="Checker")[0])
        self.batch = Batch_Table.objects.create(bat_Batch_Code="RIS", created_by=self.user)
        self.raw = Referred_Data.objects.create(
            AccessionNo="RIS-RAW", Batch_id=self.batch, Site_Org="eco", ars_OrgCode="eco",
        )
        self.final = Final_Data.objects.create(
            f_AccessionNo="RIS-FINAL", f_Site_Org="eco", f_ars_OrgCode="eco",
        )
        self.antibiotics = [
            Antibiotic_List.objects.create(
                Whonet_Abx="AMC_ND20", Abx_code="AMC", Antibiotic="Amoxicillin/clavulanic acid",
                Disk_Abx=True,
            ),
            Antibiotic_List.objects.create(
                Whonet_Abx="AMK_NM", Abx_code="AMK", Antibiotic="Amikacin", Disk_Abx=False,
            ),
        ]

    def request(self, data=None):
        request = self.factory.post("/", data or {})
        request.user = self.user
        request.session = {}
        request._messages = FallbackStorage(request)
        return request

    def test_encoding_saves_ris_without_values(self):
        cases = (
            (raw_views, "raw_data", self.raw, AntibioticEntry, "ab_idNum_referred", "Referred_Form", "_raw_antibiotics_for_panel"),
            (raw_views, "edit_data", self.raw, AntibioticEntry, "ab_idNum_referred", "Referred_Form", "_raw_antibiotics_for_panel"),
            (final_views, "edit_final_data", self.final, Final_AntibioticEntry, "ab_idNum_f_referred", "FinalReferred_Form", "_final_antibiotics_for_panel"),
        )
        data = {
            "disk_enris_AMC_ND20": "r", "mic_enris_AMK_NM": "s",
            "retest_disk_enris_AMC_ND20": "i", "retest_mic_enris_AMK_NM": "r",
        }
        for module, view, isolate, model, fk, form_name, panel_name in cases:
            with self.subTest(view=view):
                model.objects.all().delete()
                # Keep demographic validation separate; exercise real antibiotic persistence.
                with patch.object(module, form_name) as form, patch.object(
                    module, panel_name, return_value=self.antibiotics,
                ):
                    form.return_value.is_valid.return_value = True
                    form.return_value.save.return_value = isolate
                    form.return_value.changed_data = []
                    response = getattr(module, view)(self.request(data), isolate.pk)
                self.assertEqual(response.status_code, 302)
                for code, main_field, retest_field, expected in (
                    ("AMC_ND20", "ab_Disk_enRIS", "ab_Retest_Disk_enRIS", ("R", "I")),
                    ("AMK_NM", "ab_MIC_enRIS", "ab_Retest_MIC_enRIS", ("S", "R")),
                ):
                    self.assertTrue(model.objects.filter(**{fk: isolate, "ab_Abx_code": code}).exists())
                    main = model.objects.get(**{fk: isolate, "ab_Abx_code": code})
                    retest = model.objects.get(**{fk: isolate, "ab_Retest_Abx_code": code})
                    self.assertEqual(getattr(main, main_field), expected[0])
                    self.assertEqual(getattr(retest, retest_field), expected[1])
                    self.assertIsNone(main.ab_Disk_value)
                    self.assertIsNone(main.ab_MIC_value)
                    self.assertIsNone(retest.ab_Retest_DiskValue)
                    self.assertIsNone(retest.ab_Retest_MICValue)

                # Repeat a save to ensure an existing RIS-only row is retained.
                with patch.object(module, form_name) as form, patch.object(
                    module, panel_name, return_value=self.antibiotics,
                ):
                    form.return_value.is_valid.return_value = True
                    form.return_value.save.return_value = isolate
                    form.return_value.changed_data = []
                    response = getattr(module, view)(self.request(data), isolate.pk)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(model.objects.filter(**{fk: isolate, "ab_Abx_code": "AMC_ND20"}).count(), 1)

    def test_results_tables_show_ris_only_entries_without_none(self):
        for model, fk, isolate, template in (
            (AntibioticEntry, "ab_idNum_referred", self.raw, "home/partials/raw_batch_rows.html"),
            (Final_AntibioticEntry, "ab_idNum_f_referred", self.final, "home_final/partials/final_batch_rows.html"),
        ):
            with self.subTest(template=template):
                model.objects.create(**{
                    fk: isolate, "ab_Antibiotic": "RIS-only sentinel",
                    "ab_Disk_enRIS": "R", "ab_MIC_enRIS": "S",
                    "ab_Retest_Antibiotic": "RIS-only retest",
                    "ab_Retest_Disk_enRIS": "I", "ab_Retest_MIC_enRIS": "R",
                })
                html = render_to_string(template, {"records": [isolate]})
                self.assertIn("RIS-only sentinel", html)
                self.assertIn("RIS-only retest", html)
                self.assertIn('enris-badge">R</strong>', html)
                self.assertIn('enris-badge">S</strong>', html)
                self.assertIn("Retest Disk:", html)
                self.assertIn("Retest MIC:", html)
                self.assertNotRegex(html, r"(?:Disk|MIC):\s*<strong>None")

    def test_printing_includes_ris_only_disk_and_mic(self):
        for model, fk, isolate in (
            (AntibioticEntry, "ab_idNum_referred", self.raw),
            (Final_AntibioticEntry, "ab_idNum_f_referred", self.final),
        ):
            model.objects.create(**{
                fk: isolate, "ab_Abx_code": "AMC_ND20", "ab_Disk_enRIS": "R",
                "ab_Retest_Abx_code": "AMC_ND20", "ab_Retest_Disk_enRIS": "I",
            })
            model.objects.create(**{
                fk: isolate, "ab_Abx_code": "AMK_NM", "ab_MIC_enRIS": "S",
                "ab_Retest_Abx_code": "AMK_NM", "ab_Retest_MIC_enRIS": "R",
            })
        self.final.f_Batch_id = self.batch
        self.final.save()
        for module, view_name, pk in (
            (raw_views, "generate_pdf", self.raw.pk),
            (raw_views, "generate_batch_pdf", self.batch.pk),
            (raw_views, "generate_batch_pdf_panel_old", self.batch.pk),
            (raw_views, "generate_batch_pdf_panel_gram_pos", self.batch.pk),
            (final_views, "generate_final_batch_pdf", self.batch.pk),
            (final_views, "generate_final_batch_pdf_panel_old", self.batch.pk),
        ):
            with self.subTest(view=view_name):
                template_contexts = []
                rendered_reports = []
                def capture_template(name):
                    template = get_template(name)
                    class CapturingTemplate:
                        def render(self, context):
                            template_contexts.append(context)
                            html = template.render(context)
                            rendered_reports.append(html)
                            return html
                    return CapturingTemplate()
                with patch.object(module, "get_template", side_effect=capture_template), patch.object(module.pisa, "CreatePDF"):
                    response = getattr(module, view_name)(self.request(), pk)
                self.assertEqual(response.status_code, 200)
                context = template_contexts[0]
                report = context["pages"][0][0] if "pages" in context else context
                site = dict(item for row in report["grouped_rows"] for item in row)
                retest = dict(item for row in report["grouped_ars_rows"] for item in row)
                self.assertIsNotNone(site["AMC"]["disk"])
                self.assertIsNotNone(site["AMK"]["mic"])
                self.assertIsNotNone(retest["AMC"]["disk"])
                self.assertIsNotNone(retest["AMK"]["mic"])
                self.assertRegex(rendered_reports[0], r">\s*I\s*<")
