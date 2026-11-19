from types import SimpleNamespace
from django.test import SimpleTestCase
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth.models import User, Group

from apps.home_final.models import (
    ConcordanceOptions,
    ConcordanceReport,
)

from apps.home_final.services.concordance import (
    build_id_stats,
    calculate_isolate_concordance,
    classify_id_concordance,
    resolve_ars_concordance_identification,
)


class _EmptyRelatedManager:
    def all(self):
        return []


class ConcordanceIdentificationTests(SimpleTestCase):
    def test_genus_concordant_when_site_genus_has_trailing_punctuation(self):
        genus_con, species_con = classify_id_concordance(
            "Streptococcus, beta hemolytic",
            "",
            "Streptococcus agalactiae",
        )

        self.assertEqual(genus_con, "G")
        self.assertEqual(species_con, "X")

    def test_genus_concordant_when_species_differs_within_same_genus(self):
        genus_con, species_con = classify_id_concordance(
            "Streptococcus beta hemolytic",
            "",
            "Streptococcus agalactiae",
        )

        self.assertEqual(genus_con, "G")
        self.assertEqual(species_con, "X")

    def test_appends_nv_code_to_ars_identification_prefix(self):
        isolate = SimpleNamespace(
            f_ars_Pre_ed="",
            f_ars_Pre="No Raoultella planticola isolated",
            f_ars_OrgName="Not viable",
        )

        identification = resolve_ars_concordance_identification(isolate)

        self.assertEqual(
            identification,
            "No Raoultella planticola isolated (NV)",
        )

    def test_nonviable_organism_with_discordant_pre_counts_as_both(self):
        isolate = SimpleNamespace(
            id=1,
            f_AccessionNo="26ARS_SHM0001",
            f_RefNo="0001",
            f_bat_seq=1,
            f_Site_Org="rau",
            f_Site_OrgName="Raoultella planticola",
            f_ars_OrgCode="nv",
            f_ars_OrgName="Not viable",
            f_ars_Pre_ed="",
            f_ars_Pre="No Raoultella planticola isolated",
            f_ars_Post="",
            final_entries=_EmptyRelatedManager(),
        )

        result = calculate_isolate_concordance(isolate)

        self.assertEqual(result["genus_con"], "X")
        self.assertEqual(result["species_con"], "X")
        self.assertEqual(result["nonviable"], 1)
        self.assertEqual(result["different_org"], 1)

        stats = build_id_stats([isolate], options_data=[])

        self.assertEqual(stats["nonviable_count"], 1)
        self.assertEqual(stats["different_org"], 1)
        self.assertEqual(stats["viable_pure"], 0)
        self.assertEqual(
            stats["discordant_rows"][0]["ars_org"],
            "No Raoultella planticola isolated (NV)",
        )



class ConcordanceRuleDeletionSecurityTests(TestCase):

    def setUp(self):
        self.admin = User.objects.create_user(
            username="concordance-delete-admin",
            password="test-password",
        )
        self.admin.groups.add(
            Group.objects.get_or_create(name="Admin")[0]
        )

        self.rule = ConcordanceOptions.objects.create(
            report=None,
            applied_org="ECOL",
            is_active=True,
        )

        self.other_rule = ConcordanceOptions.objects.create(
            report=None,
            applied_org="KPNE",
            is_active=True,
        )

        self.report = ConcordanceReport.objects.create(
            created_by=self.admin,
        )

        self.report_options = ConcordanceOptions.objects.create(
            report=self.report,
            applied_org="ECOL",
            is_active=True,
        )

        self.url = reverse(
            "delete_concordance_setting",
            args=[self.rule.pk],
        )

    def test_get_does_not_delete_rule(self):
        self.client.force_login(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 405)
        self.assertTrue(
            ConcordanceOptions.objects.filter(
                pk=self.rule.pk
            ).exists()
        )

    def test_deleting_rule_preserves_report_options(self):
        self.client.force_login(self.admin)

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 302)

        # Selected global rule is deleted.
        self.assertFalse(
            ConcordanceOptions.objects.filter(
                pk=self.rule.pk
            ).exists()
        )

        # Other global rules remain.
        self.assertTrue(
            ConcordanceOptions.objects.filter(
                pk=self.other_rule.pk
            ).exists()
        )

        # Existing report remains.
        self.assertTrue(
            ConcordanceReport.objects.filter(
                pk=self.report.pk
            ).exists()
        )

        # Historical report options remain.
        self.assertTrue(
            ConcordanceOptions.objects.filter(
                pk=self.report_options.pk,
                report=self.report,
            ).exists()
        )

    def test_cannot_delete_report_specific_options(self):
        self.client.force_login(self.admin)

        url = reverse(
            "delete_concordance_setting",
            args=[self.report_options.pk],
        )

        response = self.client.post(url)

        self.assertEqual(response.status_code, 404)

        self.assertTrue(
            ConcordanceOptions.objects.filter(
                pk=self.report_options.pk
            ).exists()
        )
