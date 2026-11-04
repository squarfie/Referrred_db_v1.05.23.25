from types import SimpleNamespace

from django.test import SimpleTestCase

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
