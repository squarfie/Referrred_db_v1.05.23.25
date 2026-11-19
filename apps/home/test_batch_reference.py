"""Regression tests for generated four-digit batch reference ranges."""

from django.test import TestCase

from apps.home.forms import BatchTable_form


class BatchReferenceNumberTests(TestCase):
    def _form(self, batch_no, total_batch, submitted_reference="9999-9999"):
        return BatchTable_form(data={
            "bat_BatchNo": batch_no,
            "bat_Total_batch": total_batch,
            "bat_RefNo": submitted_reference,
        })

    def test_single_batch_has_four_digit_start_and_end(self):
        form = self._form("1", "1")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["bat_RefNo"], "0001-0001")

    def test_reference_is_generated_from_batch_and_total(self):
        form = self._form("2", "15")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["bat_RefNo"], "0002-0015")

    def test_leading_zero_inputs_are_normalized(self):
        form = self._form("0007", "0010")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["bat_RefNo"], "0007-0010")

    def test_submitted_reference_is_not_trusted(self):
        form = self._form("1", "2", "1234-9999")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["bat_RefNo"], "0001-0002")

    def test_limit_of_four_digits(self):
        form = self._form("9999", "9999")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["bat_RefNo"], "9999-9999")

    def test_rejects_reversed_range(self):
        form = self._form("3", "2")
        self.assertFalse(form.is_valid())

    def test_rejects_zero(self):
        form = self._form("0", "1")
        self.assertFalse(form.is_valid())

    def test_rejects_more_than_four_digits(self):
        form = self._form("1", "10000")
        self.assertFalse(form.is_valid())

    def test_rejects_non_integer_values(self):
        form = self._form("1.5", "2")
        self.assertFalse(form.is_valid())
