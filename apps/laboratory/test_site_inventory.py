from datetime import timedelta

from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.receiving import tests as fixtures
from apps.laboratory.models import LaboratoryIsolate, LaboratoryOrganismCount
from apps.laboratory.workflow import prepare_revival, site_identification


class SiteInventoryTests(TestCase):
    setUp = fixtures.LaboratoryReceivingWorkspaceTests.setUp
    create_pending_accession = fixtures.LaboratoryReceivingWorkspaceTests.create_pending_accession

    def item(self):
        accession = self.create_pending_accession()
        prepare_revival(accession.pk, self.lab_user)
        return accession.isolate_records.first()

    def test_inventory_has_inline_add_row_within_existing_range(self):
        self.create_pending_accession()
        page = self.client.get(reverse('laboratory_receiving_workspace', args=[self.receipt.receiving_id]))
        self.assertContains(page, 'Add row')

    def test_additional_rows_share_the_range_limit_and_survive_matrix_saves(self):
        accession = self.create_pending_accession()
        LaboratoryOrganismCount.objects.create(accession_range=accession, organism_code='ECO', count=2)
        url = reverse('laboratory_extra_organism_add', args=[accession.pk])
        self.client.post(url, {'whonet_code': 'KPN', 'register_category': 'KLE', 'count': 2})
        self.assertEqual(accession.additional_organism_counts.get().count, 2)
        self.client.post(url, {'whonet_code': 'ECO', 'register_category': 'ECO', 'count': 2})
        self.assertEqual(accession.additional_organism_counts.count(), 1)
        self.client.post(reverse('laboratory_organism_matrix_save', args=[accession.pk]), {'organism_HA': '4'})
        self.assertEqual(accession.organism_counts.get().organism_code, 'ECO')
        self.client.post(reverse('laboratory_organism_matrix_save', args=[accession.pk]), {'organism_HA': '1'})
        self.assertEqual(accession.organism_counts.get().count, 1)
        self.assertEqual(accession.additional_organism_counts.get().count, 2)

    def test_added_rows_are_editable_and_included_in_register_totals(self):
        from apps.laboratory.reports import build_register_rows
        accession = self.create_pending_accession()
        self.client.post(reverse('laboratory_extra_organism_add', args=[accession.pk]),
            {'whonet_code': 'KPN', 'register_category': 'KLE', 'count': 2})
        row = accession.additional_organism_counts.get()
        self.client.post(reverse('laboratory_extra_organism_save', args=[row.pk]),
            {'whonet_code': 'KPN', 'register_category': 'KLE', 'count': 3})
        row.refresh_from_db()
        self.assertEqual(row.count, 3)
        _, _, totals = build_register_rows([self.lab_record])
        self.assertEqual(totals['KLE'], 3)

    def test_site_identification_is_saved_during_revival_and_shown_to_main_lab(self):
        item = self.item()
        now = timezone.now()
        self.client.post(reverse('laboratory_revival_outcome', args=[item.pk]), {
            'status': 'revived', 'started_at': (now - timedelta(hours=2)).isoformat(),
            'completed_at': (now - timedelta(hours=1)).isoformat(),
            'site_organism_code': 'KPN', 'site_organism_name': 'Klebsiella pneumoniae',
        })
        item.refresh_from_db()
        self.assertEqual(item.site_organism_code, 'KPN')
        self.assertEqual(site_identification(item), ('KPN', 'Klebsiella pneumoniae'))
        self.assertFalse(item.identifications.exists())
        self.client.post(reverse('laboratory_queue_main_lab', args=[item.pk]))
        from apps.laboratory.workflow import record_revival_outcome, record_site_identification, queue_for_id, create_id_handoff
        for sibling in item.accession_range.isolate_records.exclude(pk=item.pk):
            record_revival_outcome(sibling.pk, 'revived', (now-timedelta(hours=2)).isoformat(), (now-timedelta(hours=1)).isoformat(), '', self.lab_user)
            record_site_identification(sibling.pk, 'KPN', 'Klebsiella pneumoniae', self.lab_user)
        queue_for_id(item.accession_range_id, self.lab_user)
        create_id_handoff(item.accession_range_id, self.lab_user)
        self.lab_user.groups.set([Group.objects.get_or_create(name='Laboratory Staff')[0]])
        page = self.client.get(reverse('main_lab_isolate_detail', args=[item.pk]))
        self.assertContains(page, 'Site-reported organism')
        self.assertContains(page, 'Klebsiella pneumoniae')
        self.assertContains(page, 'KPN')
        self.assertNotContains(page, 'name="site_organism_code"')
        self.client.post(reverse('laboratory_acknowledge_id', args=[item.pk]))
        self.client.post(reverse('laboratory_record_id', args=[item.pk]), {
            'organism_reported_code': 'ECO', 'organism_reported': 'Escherichia coli', 'cassette_reference': 'C1'})
        item.refresh_from_db()
        self.assertEqual(item.site_organism_code, 'KPN')
        self.assertEqual(item.identifications.get().whonet_organism_code, 'ECO')

    def test_invalid_revival_does_not_partially_save_site_id(self):
        item = self.item()
        self.client.post(reverse('laboratory_revival_outcome', args=[item.pk]), {
            'status': 'revived', 'started_at': 'bad', 'completed_at': 'bad',
            'site_organism_code': 'KPN', 'site_organism_name': 'Klebsiella pneumoniae',
        })
        item.refresh_from_db()
        self.assertEqual(item.site_organism_code, '')

    def test_site_id_cannot_be_changed_after_endorsement(self):
        item = self.item()
        item.status = 'id_pending'
        item.site_organism_code, item.site_organism_name = 'KPN', 'Klebsiella pneumoniae'
        item.endorsed_at = timezone.now()
        item.save()
        self.client.post(reverse('laboratory_site_id_save', args=[item.pk]),
            {'site_organism_code': 'ECO', 'site_organism_name': 'Escherichia coli'})
        item.refresh_from_db()
        self.assertEqual(item.site_organism_code, 'KPN')

    def test_direct_banking_uses_site_id_recorded_at_revival_without_raw_batch(self):
        item = self.item()
        now = timezone.now()
        self.client.post(reverse('laboratory_revival_outcome', args=[item.pk]), {
            'status': 'revived', 'started_at': (now - timedelta(hours=2)).isoformat(),
            'completed_at': (now - timedelta(hours=1)).isoformat(),
            'site_organism_code': 'KPN', 'site_organism_name': 'Klebsiella pneumoniae',
        })
        self.client.post(reverse('laboratory_bank_site_id', args=[item.pk]))
        item.refresh_from_db()
        self.assertEqual(item.status, 'ready_for_biobank')
        self.assertEqual(item.final_organism, 'Klebsiella pneumoniae')
        self.assertEqual(item.identification_source, 'site')

    def test_accession_cannot_shrink_below_additional_organism_counts(self):
        accession = self.create_pending_accession()
        self.client.post(reverse('laboratory_extra_organism_add', args=[accession.pk]),
            {'whonet_code': 'KPN', 'register_category': 'KLE', 'count': 4})
        self.client.post(reverse('laboratory_accession_edit', args=[accession.pk]), {
            'date_referred': accession.date_referred.isoformat(), 'start_number': 1,
            'end_number': 2, 'batch_number': 1, 'total_batches': 1,
        })
        accession.refresh_from_db()
        self.assertEqual(accession.end_number, 5)

    def test_long_site_name_is_rejected_without_a_server_error(self):
        item = self.item()
        response = self.client.post(reverse('laboratory_site_id_save', args=[item.pk]),
            {'site_organism_name': 'A' * 256})
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.site_organism_name, '')
