from django.test import SimpleTestCase

from apps.home_final.services.concordance import classify_id_concordance


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
