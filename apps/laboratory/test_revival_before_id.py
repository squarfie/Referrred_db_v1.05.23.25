from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.receiving import tests as fixtures
from apps.laboratory.models import LaboratoryIsolate, LaboratoryIdentification
from apps.laboratory.guidance import next_action
from apps.laboratory.workflow import (
    WorkflowError, transition_batch, create_id_handoff, record_revival_outcome,
)


class RevivalBeforeIdentificationTests(TestCase):
    setUp = fixtures.LaboratoryReceivingWorkspaceTests.setUp
    create_pending_accession = fixtures.LaboratoryReceivingWorkspaceTests.create_pending_accession

    def prepare(self):
        accession = self.create_pending_accession()
        self.receipt.lab_date_received = timezone.localdate() - timedelta(days=2)
        self.receipt.save()
        with patch('apps.laboratory.workflow.timezone.now', return_value=timezone.now() - timedelta(hours=3)):
            transition_batch(accession.pk, 'prepare', self.lab_user)
        return accession

    def revive(self, item, outcome='revived'):
        now = timezone.now()
        record_revival_outcome(item.pk, outcome, (now - timedelta(hours=2)).isoformat(),
            (now - timedelta(hours=1)).isoformat(), '' if outcome == 'revived' else 'No growth', self.lab_user)
        item.refresh_from_db()

    def test_prepare_creates_isolates_without_queueing_or_inventing_revival_dates(self):
        accession = self.prepare()
        items = list(accession.isolate_records.all())
        self.assertEqual(len(items), accession.isolate_count)
        for item in items:
            self.assertEqual(item.status, 'revival_pending')
            self.assertIsNone(item.revival_queued_at)
            self.assertIsNone(item.revival_started_at)
            self.assertIsNone(item.revival_completed_at)
            self.assertIsNone(item.endorsed_at)
        self.assertFalse(transition_batch(accession.pk, 'prepare', self.lab_user))

    def test_endorsement_without_revival_does_not_create_id_work(self):
        accession = self.create_pending_accession()
        with self.assertRaises(WorkflowError):
            create_id_handoff(accession.pk, self.lab_user)
        self.assertFalse(accession.isolate_records.exists())

    def test_endorsement_is_blocked_until_entire_batch_is_revived_identified_and_queued(self):
        from apps.laboratory.workflow import queue_for_id
        accession = self.prepare()
        first, second, third = list(accession.isolate_records.all())[:3]
        self.revive(first)
        first.site_organism_code = 'KPN'
        first.site_organism_name = 'Klebsiella pneumoniae'
        first.save(update_fields=['site_organism_code', 'site_organism_name'])
        self.assertTrue(queue_for_id(accession.pk, self.lab_user, isolate_id=first.pk))
        with self.assertRaises(WorkflowError):
            create_id_handoff(accession.pk, self.lab_user)
        first.refresh_from_db()
        self.assertEqual(first.status, 'id_queued')
        self.assertIsNone(first.endorsed_at)

        # Every remaining viable isolate must be ready; a single pending one
        # blocks the atomic batch handoff.
        for item in accession.isolate_records.exclude(pk=first.pk):
            self.revive(item)
            item.site_organism_code = 'KPN'
            item.site_organism_name = 'Klebsiella pneumoniae'
            item.save(update_fields=['site_organism_code', 'site_organism_name'])
            queue_for_id(accession.pk, self.lab_user, isolate_id=item.pk)
        self.assertTrue(create_id_handoff(accession.pk, self.lab_user))
        timestamps = set()
        for item in accession.isolate_records.all():
            self.assertEqual(item.status, 'id_pending')
            self.assertIsNotNone(item.endorsed_at)
            timestamps.add(item.endorsed_at)
        self.assertEqual(len(timestamps), 1)
        self.assertFalse(create_id_handoff(accession.pk, self.lab_user))

    def prepare_complete_id_batch(self, accession):
        from apps.laboratory.workflow import queue_for_id
        for item in accession.isolate_records.all():
            if item.status == 'revival_pending':
                self.revive(item)
            item.site_organism_code = 'KPN'
            item.site_organism_name = 'Klebsiella pneumoniae'
            item.save(update_fields=['site_organism_code', 'site_organism_name'])
            if item.status != 'id_queued':
                queue_for_id(accession.pk, self.lab_user, isolate_id=item.pk)
        return create_id_handoff(accession.pk, self.lab_user)

    def test_all_queued_still_requires_recorded_site_id_and_no_per_isolate_endorsement(self):
        from apps.laboratory.workflow import queue_for_id
        accession = self.prepare()
        items = list(accession.isolate_records.all())
        for item in items:
            self.revive(item)
            queue_for_id(accession.pk, self.lab_user, isolate_id=item.pk)
        response = self.client.get(reverse('laboratory_receiving_workspace',
                                           args=[self.receipt.receiving_id]))
        row = next(row for row in response.context['ranges'] if row.pk == accession.pk)
        self.assertFalse(row.can_endorse_id)
        with self.assertRaises(WorkflowError):
            create_id_handoff(accession.pk, self.lab_user)

        self.client.post(reverse('laboratory_endorse_isolate', args=[items[0].pk]))
        items[0].refresh_from_db()
        self.assertEqual(items[0].status, 'id_queued')
        self.assertIsNone(items[0].endorsed_at)

        for item in items:
            item.site_organism_code = 'KPN'
            item.site_organism_name = 'Klebsiella pneumoniae'
            item.save(update_fields=['site_organism_code', 'site_organism_name'])
        response = self.client.get(reverse('laboratory_receiving_workspace',
                                           args=[self.receipt.receiving_id]))
        row = next(row for row in response.context['ranges'] if row.pk == accession.pk)
        self.assertTrue(row.can_endorse_id)
        self.assertTrue(create_id_handoff(accession.pk, self.lab_user))
        self.assertEqual(accession.isolate_records.filter(status='id_pending').count(),
                         accession.isolate_count)

    def test_returned_identification_proceeds_directly_to_review(self):
        accession = self.create_pending_accession()
        item = LaboratoryIsolate.objects.create(accession_range=accession,
            accession_number='RETURNED-ID', status='id_returned', returned_at=timezone.now(), returned_by=self.lab_user,
            revival_started_at=timezone.now() - timedelta(hours=2), revival_completed_at=timezone.now() - timedelta(hours=1))
        LaboratoryIdentification.objects.create(isolate=item, organism_reported='Klebsiella pneumoniae',
            whonet_organism_code='KPN', cassette_reference='C1')
        self.assertEqual(next_action(item, self.lab_user)['section'], 'review')
        self.client.force_login(self.lab_user)
        self.client.post(reverse('laboratory_review_id', args=[item.pk]), {'action': 'accept_reported'})
        item.refresh_from_db()
        self.assertEqual(item.status, 'id_reviewed')
        self.assertIsNotNone(item.revival_completed_at)

    def test_actual_revival_dates_must_follow_laboratory_receipt(self):
        accession = self.prepare()
        item = accession.isolate_records.first()
        with self.assertRaises(WorkflowError):
            record_revival_outcome(item.pk, 'revived',
                (timezone.now() - timedelta(days=4)).isoformat(), timezone.now().isoformat(), '', self.lab_user)

    def test_worksheet_and_worklist_expose_revival_before_handoff(self):
        accession = self.create_pending_accession()
        page = self.client.get(reverse('laboratory_receiving_workspace', args=[self.receipt.receiving_id]))
        self.assertContains(page, 'Record revival')
        self.assertNotContains(page, 'Revival can begin only when Main Laboratory returns')
        self.client.post(reverse('laboratory_batch_transition', args=[accession.pk, 'prepare']))
        page = self.client.get(reverse('laboratory_processing_worklist'))
        self.assertEqual(page.context['batches'][0]['accession'].pk, accession.pk)
        self.assertEqual(len(self.client.get(reverse('laboratory_worklist')).context['batches']), 0)

    def test_historical_completed_revival_with_returned_id_still_goes_to_review(self):
        accession = self.create_pending_accession()
        item = LaboratoryIsolate.objects.create(accession_range=accession,
            accession_number='HISTORICAL', status='revived', returned_at=timezone.now())
        self.assertEqual(next_action(item, self.lab_user)['section'], 'review')

    def test_direct_bank_uses_site_identification_without_main_lab_or_lyophilization(self):
        from apps.home.models import Referred_Data
        from apps.laboratory.workflow import save_biobank
        accession = self.prepare()
        item = accession.isolate_records.first()
        self.revive(item)
        raw = Referred_Data.objects.create(AccessionNo=item.accession_number, SiteCode=self.site.SiteCode,
            Site_Org='KPN', Site_OrgName='Klebsiella pneumoniae')
        response = self.client.post(reverse('laboratory_bank_site_id', args=[item.pk]))
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.identification_source, 'site')
        self.assertEqual(item.final_organism, 'Klebsiella pneumoniae')
        self.assertEqual(item.status, 'ready_for_biobank')
        self.assertIsNone(item.endorsed_at)
        self.assertIsNone(item.reviewed_at)
        self.assertFalse(item.identifications.exists())
        bank = save_biobank(item.pk, {'storage_box': 'SITE-BOX', 'box_position': 'A1'}, self.lab_user)
        item.refresh_from_db()
        self.assertEqual(item.status, 'site_banked')
        self.assertIsNone(item.biobanked_at)
        self.assertIsNotNone(item.site_banked_at)
        self.assertEqual(item.site_bank_storage["storage_box"], "SITE-BOX")
        self.assertIsNone(bank.date_lyophilized)
        raw.Site_Org = 'ECO'
        raw.Site_OrgName = 'Escherichia coli'
        raw.save()
        item.refresh_from_db()
        self.assertEqual(item.final_organism, 'Klebsiella pneumoniae')

    def test_site_banking_remains_pending_and_can_be_queued_for_main_lab_id(self):
        from apps.home.models import Referred_Data
        from apps.laboratory.workflow import save_biobank, queue_for_id
        from apps.laboratory.receiving_views import _laboratory_workflow_receipts
        from apps.receiving.models import ReferralReceipt

        accession = self.prepare()
        first = accession.isolate_records.first()
        self.revive(first)
        Referred_Data.objects.create(
            AccessionNo=first.accession_number, SiteCode=self.site.SiteCode,
            Site_Org='KPN', Site_OrgName='Klebsiella pneumoniae',
        )
        self.client.post(reverse('laboratory_bank_site_id', args=[first.pk]))
        save_biobank(first.pk, {'storage_box': 'SITE-BOX', 'box_position': 'A1'}, self.lab_user)
        first.refresh_from_db()
        self.assertEqual(first.status, 'site_banked')
        receipt = _laboratory_workflow_receipts(ReferralReceipt.objects.filter(pk=self.receipt.pk)).get()
        self.assertEqual(receipt.lab_workflow, 'processing')
        self.assertEqual(next_action(first, self.lab_user)['section'], 'site_banked')
        self.assertContains(self.client.get(reverse('laboratory_isolate_detail', args=[first.pk])),
                            'Queue stored isolate for Main Lab ID')
        self.assertTrue(queue_for_id(accession.pk, self.lab_user, isolate_id=first.pk))
        first.refresh_from_db()
        self.assertEqual(first.status, 'id_queued')
        self.assertEqual(first.identification_source, '')
        self.assertEqual(first.site_organism_code, 'KPN')
        self.assertEqual(first.site_bank_storage['storage_box'], 'SITE-BOX')
        self.assertIsNotNone(first.site_banked_at)
        self.assertFalse(queue_for_id(accession.pk, self.lab_user, isolate_id=first.pk))
        # The batch must wait for the remaining isolates.
        with self.assertRaises(WorkflowError):
            create_id_handoff(accession.pk, self.lab_user, isolate_id=first.pk)
        self.assertTrue(self.prepare_complete_id_batch(accession))
        first.refresh_from_db()
        self.assertEqual(first.status, 'id_pending')
        self.assertIsNotNone(first.endorsed_at)
        self.assertIsNone(first.biobanked_at)

    def test_direct_bank_requires_matching_site_id_and_successful_revival(self):
        from apps.home.models import Referred_Data
        accession = self.prepare()
        item = accession.isolate_records.first()
        url = reverse('laboratory_bank_site_id', args=[item.pk])
        self.client.post(url)
        item.refresh_from_db()
        self.assertEqual(item.status, 'revival_pending')
        self.revive(item)
        Referred_Data.objects.create(AccessionNo=item.accession_number, SiteCode='OTHER', Site_Org='KPN')
        self.client.post(url)
        item.refresh_from_db()
        self.assertEqual(item.status, 'revived')
        self.assertFalse(hasattr(item, 'biobank'))

    def test_queue_targets_only_selected_revived_isolate(self):
        accession = self.prepare()
        first, second = list(accession.isolate_records.all())[:2]
        self.revive(first)
        self.revive(second)
        self.client.post(reverse('laboratory_queue_main_lab', args=[first.pk]))
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, 'id_queued')
        self.assertEqual(second.status, 'revived')
        self.assertIsNone(first.endorsed_at)

    def test_queue_requires_whole_batch_endorsement_before_main_lab_can_receive(self):
        from apps.laboratory.workflow import acknowledge_id_receipt
        accession = self.prepare()
        item = accession.isolate_records.first()
        self.revive(item)
        self.client.post(reverse('laboratory_queue_main_lab', args=[item.pk]))
        item.refresh_from_db()
        self.assertEqual(item.status, 'id_queued')
        queued_at = item.id_queued_at
        from django.contrib.auth.models import User, Group
        main_lab = User.objects.create_user('handoff-main-lab')
        main_lab.groups.add(Group.objects.get_or_create(name='Laboratory Staff')[0])
        _, received = acknowledge_id_receipt(isolate_id=item.pk, user=main_lab)
        self.assertFalse(received)
        with self.assertRaises(WorkflowError):
            create_id_handoff(accession.pk, self.lab_user)
        self.assertTrue(self.prepare_complete_id_batch(accession))
        item.refresh_from_db()
        self.assertEqual(item.status, 'id_pending')
        self.assertEqual(item.id_queued_at, queued_at)
        self.assertIsNotNone(item.endorsed_at)
        self.assertEqual(len(self.client.get(reverse('laboratory_worklist')).context['batches']), 1)

    def test_main_lab_staff_cannot_open_bench_a_queue_controls(self):
        from django.contrib.auth.models import Group
        accession = self.prepare()
        item = accession.isolate_records.first()
        self.revive(item)
        self.lab_user.groups.set([Group.objects.get_or_create(name='Laboratory Staff')[0]])
        url = reverse('laboratory_queue_id_batch', args=[accession.pk])
        page = self.client.get(reverse('laboratory_receiving_workspace', args=[self.receipt.receiving_id]))
        self.assertEqual(page.status_code, 302)
        self.assertIsNone(item.endorsed_at)

    def test_main_lab_path_requires_lyophilization_before_storage(self):
        from apps.laboratory.workflow import save_biobank
        accession = self.prepare()
        item = accession.isolate_records.first()
        self.revive(item)
        original = (item.revival_started_at, item.revival_completed_at)
        self.prepare_complete_id_batch(accession)
        from django.contrib.auth.models import User, Group
        main_lab = User.objects.create_user('final-path-main-lab')
        main_lab.groups.add(Group.objects.get_or_create(name='Laboratory Staff')[0])
        self.client.force_login(main_lab)
        self.client.post(reverse('laboratory_acknowledge_id', args=[item.pk]))
        self.client.post(reverse('laboratory_record_id', args=[item.pk]), {
            'action': 'submit_return', 'organism_reported_code': 'KPN',
            'organism_reported': 'Klebsiella pneumoniae', 'cassette_reference': 'C1',
        })
        self.client.force_login(self.lab_user)
        self.client.post(reverse('laboratory_review_id', args=[item.pk]), {'action': 'accept_reported'})
        item.refresh_from_db()
        self.assertEqual(item.status, 'id_reviewed')
        self.assertEqual(item.identification_source, 'main_lab')
        self.assertEqual((item.revival_started_at, item.revival_completed_at), original)
        with self.assertRaises(WorkflowError):
            save_biobank(item.pk, {'storage_box': 'MAIN-BOX', 'box_position': 'A1'}, self.lab_user)
        day = timezone.localdate().isoformat()
        self.client.post(reverse('laboratory_lyophilization_complete', args=[item.pk]),
            {'date_harvested': day, 'date_lyophilized': day})
        save_biobank(item.pk, {'storage_box': 'MAIN-BOX', 'box_position': 'A1'}, self.lab_user)
        item.refresh_from_db()
        self.assertEqual(item.status, 'biobanked')
        self.assertEqual(item.biobanked_by, self.lab_user)

    def test_harvest_is_recorded_with_lyophilization_and_preserved_at_final_banking(self):
        from apps.home.models import Referred_Data
        accession = self.prepare()
        item = accession.isolate_records.first()
        page = self.client.get(reverse('laboratory_isolate_detail', args=[item.pk]))
        self.assertNotContains(page, 'name="date_harvested"')
        self.revive(item)
        self.assertFalse(hasattr(item, 'biobank'))
        Referred_Data.objects.create(AccessionNo=item.accession_number, SiteCode=self.site.SiteCode,
            Site_Org='KPN', Site_OrgName='Klebsiella pneumoniae')
        self.client.post(reverse('laboratory_bank_site_id', args=[item.pk]))
        day = timezone.localdate().isoformat()
        self.client.post(reverse('laboratory_lyophilization_complete', args=[item.pk]),
            {'date_harvested': day, 'date_lyophilized': day})
        self.client.post(reverse('laboratory_biobank_save', args=[item.pk]),
            {'storage_box': 'SITE-BOX', 'box_position': 'C2', 'date_harvested': '2000-01-01', 'whonet_code': 'ECO'})
        item.refresh_from_db()
        self.assertEqual(item.biobank.date_harvested.isoformat(), day)
        self.assertEqual(item.biobank.whonet_code, 'KPN')
        self.assertEqual(item.biobank.storage_box, 'SITE-BOX')
        self.assertEqual(item.biobank.box_position, 'C2')
        self.assertEqual(item.status, 'site_banked')

    def test_disposition_actions_require_post_and_a_laboratory_role(self):
        from django.contrib.auth.models import Group
        accession = self.prepare()
        item = accession.isolate_records.first()
        self.revive(item)
        bank_url = reverse('laboratory_bank_site_id', args=[item.pk])
        queue_url = reverse('laboratory_queue_main_lab', args=[item.pk])
        for url in (bank_url, queue_url):
            self.assertEqual(self.client.get(url).status_code, 405)
        self.lab_user.groups.set([Group.objects.get_or_create(name='Secretariat')[0]])
        for url in (bank_url, queue_url):
            self.client.post(url)
        item.refresh_from_db()
        self.assertEqual(item.status, 'revived')
        self.assertIsNone(item.endorsed_at)
        self.assertFalse(hasattr(item, 'biobank'))

    def test_historical_return_without_revival_preserves_id_and_records_missing_work(self):
        accession = self.prepare()
        item = accession.isolate_records.first()
        item.status = 'id_returned'
        item.returned_at = timezone.now() - timedelta(hours=3)
        item.returned_by = self.lab_user
        item.save()
        result = LaboratoryIdentification.objects.create(isolate=item, organism_reported='Klebsiella pneumoniae',
            whonet_organism_code='KPN', cassette_reference='C1')
        old_return = item.returned_at
        self.assertEqual(next_action(item, self.lab_user)['section'], 'revival')
        self.client.force_login(self.lab_user)
        self.client.post(reverse('laboratory_review_id', args=[item.pk]), {'action': 'accept_reported'})
        item.refresh_from_db()
        self.assertEqual(item.status, 'id_returned')
        self.revive(item)
        self.assertEqual(item.returned_at, old_return)
        self.assertEqual(item.identifications.get().pk, result.pk)
        self.assertEqual(next_action(item, self.lab_user)['section'], 'review')
        self.client.force_login(self.lab_user)
        self.client.post(reverse('laboratory_review_id', args=[item.pk]), {'action': 'accept_reported'})
        item.refresh_from_db()
        self.assertEqual(item.status, 'id_reviewed')

    def test_final_banking_requires_a_box_and_position_in_the_same_section(self):
        from apps.home.models import Referred_Data
        from apps.laboratory.workflow import save_biobank
        accession = self.prepare()
        item = accession.isolate_records.first()
        self.revive(item)
        Referred_Data.objects.create(AccessionNo=item.accession_number, SiteCode=self.site.SiteCode,
            Site_Org='KPN', Site_OrgName='Klebsiella pneumoniae')
        self.client.post(reverse('laboratory_bank_site_id', args=[item.pk]))
        for details in ({'box_position': 'A1'}, {'storage_box': 'BOX'},
                        {'storage_box': 'BOX', 'supplementary_position': 'A1'}):
            with self.subTest(details=details), self.assertRaises(WorkflowError):
                save_biobank(item.pk, details, self.lab_user)
        item.refresh_from_db()
        self.assertEqual(item.status, 'ready_for_biobank')
        save_biobank(item.pk, {'storage_box': 'BOX', 'box_position': 'A1'}, self.lab_user)
        item.refresh_from_db()
        self.assertEqual(item.status, 'site_banked')
        self.assertEqual(item.site_banked_by, self.lab_user)
        self.assertIsNone(item.biobanked_at)
