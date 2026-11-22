from django.test import TestCase
from django.urls import reverse

from apps.receiving import tests as fixtures
from apps.laboratory.models import LaboratoryOrganismCount
from apps.laboratory.reports import REGISTER_CODES, build_register_rows


class InventoryGridRowTests(TestCase):
    setUp = fixtures.LaboratoryReceivingWorkspaceTests.setUp
    create_pending_accession = fixtures.LaboratoryReceivingWorkspaceTests.create_pending_accession

    def test_added_row_accepts_counts_across_all_original_columns(self):
        accession = self.create_pending_accession()
        LaboratoryOrganismCount.objects.create(accession_range=accession, organism_code='ECO', count=1)
        self.client.post(reverse('laboratory_inventory_row_add', args=[accession.pk]),
            {'organism_HA': '1', 'organism_NGO': '1', 'organism_ECO': '1'})
        row = accession.additional_organism_counts.get()
        self.assertEqual(row.count, 3)
        self.assertEqual(row.inventory_counts['HA'], 1)
        self.assertEqual(row.inventory_counts['NGO'], 1)
        self.assertEqual(row.inventory_counts['ECO'], 1)
        _, _, totals = build_register_rows([self.lab_record])
        self.assertEqual(totals['ECO'], 2)
        self.assertEqual(totals['HA'], 1)
        self.client.post(reverse('laboratory_organism_matrix_save', args=[accession.pk]), {'organism_ECO': '3'})
        self.assertEqual(accession.organism_counts.get().count, 1)

    def test_full_row_updates_preserve_other_rows_and_reject_invalid_totals(self):
        accession = self.create_pending_accession()
        url = reverse('laboratory_inventory_row_add', args=[accession.pk])
        self.client.post(url, {'organism_HA': '1'})
        self.client.post(url, {'organism_NGO': '1'})
        first, second = list(accession.additional_organism_counts.order_by('pk'))
        update = reverse('laboratory_inventory_row_save', args=[first.pk])
        self.client.post(update, {'organism_ECO': '4'})
        first.refresh_from_db()
        self.assertEqual(first.count, 4)
        self.client.post(update, {'organism_ECO': '5'})
        self.client.post(update, {'organism_ECO': '-1'})
        self.client.post(update, {'organism_ECO': 'invalid'})
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.count, 4)
        self.assertEqual(second.count, 1)

    def test_multiple_blank_rows_can_be_saved_and_filled_independently(self):
        accession = self.create_pending_accession()
        url = reverse('laboratory_inventory_row_add', args=[accession.pk])
        self.client.post(url, {})
        self.client.post(url, {})
        self.assertEqual(accession.additional_organism_counts.count(), 2)
        self.assertEqual(sum(accession.additional_organism_counts.values_list('count', flat=True)), 0)

    def test_saved_and_new_grid_rows_have_every_organism_column(self):
        accession = self.create_pending_accession()
        self.client.post(reverse('laboratory_inventory_row_add', args=[accession.pk]), {'organism_HA': '1'})
        page = self.client.get(reverse('laboratory_receiving_workspace', args=[self.receipt.receiving_id]))
        for code in REGISTER_CODES:
            self.assertContains(page, f'name="organism_{code}"', count=3)
        self.assertContains(page, 'data-add-inventory-row')
        self.assertNotContains(page, 'name="register_category"')
        self.assertNotContains(page, 'name="whonet_code"')
