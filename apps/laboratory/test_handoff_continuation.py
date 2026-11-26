from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.receiving import tests as fixtures
from .models import LaboratoryIsolate
from .workflow import create_id_handoff, WorkflowError


class HandoffContinuationTests(TestCase):
    setUp = fixtures.LaboratoryReceivingWorkspaceTests.setUp
    create_pending_accession = fixtures.LaboratoryReceivingWorkspaceTests.create_pending_accession

    def mixed_batch(self):
        accession = self.create_pending_accession()
        now = timezone.now()
        items = [LaboratoryIsolate.objects.create(
            accession_range=accession, accession_number=number, status='id_queued',
            site_organism_code='KPN', site_organism_name='Klebsiella pneumoniae',
            revival_started_at=now-timedelta(days=2), revival_completed_at=now-timedelta(days=1),
        ) for number in accession.accession_numbers]
        done = items[0]
        done.status = 'biobanked'
        done.identification_source = 'main_lab'
        done.endorsed_at = done.returned_at = done.reviewed_at = done.lyophilization_completed_at = done.biobanked_at = now
        done.save()
        return accession, done, items[1:]

    def test_remaining_ready_isolates_can_be_endorsed_without_rewriting_completed_sibling(self):
        accession, done, queued = self.mixed_batch()
        before = LaboratoryIsolate.objects.values().get(pk=done.pk)
        self.assertTrue(create_id_handoff(accession.pk, self.lab_user))
        self.assertEqual(LaboratoryIsolate.objects.values().get(pk=done.pk), before)
        for item in queued:
            item.refresh_from_db()
            self.assertEqual(item.status, 'id_pending')
            self.assertIsNotNone(item.endorsed_at)
        self.assertFalse(create_id_handoff(accession.pk, self.lab_user))

    def test_isolate_stays_queued_and_batch_page_endorsement_reaches_main_lab(self):
        accession, done, queued = self.mixed_batch()
        action = reverse('laboratory_endorse_id', args=[accession.pk])
        page = self.client.get(reverse('laboratory_isolate_detail', args=[queued[0].pk]))
        self.assertNotContains(page, f'action="{action}"')
        self.assertNotContains(page, 'Endorse whole batch to Main Lab')
        self.assertContains(page, 'Go to batch')
        self.assertContains(page, 'Queued—awaiting batch endorsement')
        self.assertNotContains(page, f'action="{reverse("laboratory_queue_main_lab", args=[queued[0].pk])}"')
        batch_page = self.client.get(reverse('laboratory_batch_detail', args=[accession.pk]))
        self.assertContains(batch_page, f'action="{action}"')
        workspace = self.client.get(reverse('laboratory_receiving_workspace', args=[self.receipt.receiving_id]))
        self.assertContains(workspace, f'action="{action}"')
        self.client.post(action)
        staff = User.objects.create_user('continuation-main-lab')
        staff.groups.add(Group.objects.get_or_create(name='Laboratory Staff')[0])
        self.client.force_login(staff)
        dashboard = self.client.get(reverse('main_lab_dashboard'))
        for item in queued:
            self.assertContains(dashboard, item.accession_number)
        self.assertNotContains(dashboard, done.accession_number)

    def test_unprepared_sibling_still_blocks_endorsement_atomically(self):
        accession, done, queued = self.mixed_batch()
        queued[-1].status = 'revival_pending'
        queued[-1].save()
        with self.assertRaises(WorkflowError):
            create_id_handoff(accession.pk, self.lab_user)
        self.assertFalse(accession.isolate_records.filter(status='id_pending').exists())
        page = self.client.get(reverse('laboratory_isolate_detail', args=[queued[0].pk]))
        self.assertNotContains(page, 'Endorse whole batch to Main Lab')
        self.assertContains(page, queued[-1].accession_number)
        batch_page = self.client.get(reverse('laboratory_batch_detail', args=[accession.pk]))
        self.assertNotContains(batch_page, 'Endorse whole batch to Main Lab')

    def test_direct_endorsement_replaces_queue_confirmation(self):
        accession, done, queued = self.mixed_batch()
        item = queued[0]
        item.status = 'revived'
        item.save()
        page = self.client.get(reverse('laboratory_isolate_detail', args=[item.pk]))
        self.assertContains(page, 'Endorse to Main Lab')
        self.assertNotContains(page, f'action="{reverse("laboratory_queue_main_lab", args=[item.pk])}"')
        batch = self.client.get(reverse('laboratory_batch_detail', args=[accession.pk]))
        self.assertContains(batch, 'Endorse whole batch to Main Lab')
        self.client.post(reverse('laboratory_endorse_id', args=[accession.pk]))
        item.refresh_from_db()
        self.assertEqual(item.status, 'id_pending')
        self.assertIsNotNone(item.endorsed_at)
        self.assertIsNone(item.id_queued_at)


    def test_legacy_main_lab_evidence_does_not_block_remaining_queue(self):
        accession, done, queued = self.mixed_batch()
        done.endorsed_at = None
        done.save()
        before = LaboratoryIsolate.objects.values().get(pk=done.pk)
        self.assertTrue(create_id_handoff(accession.pk, self.lab_user))
        self.assertEqual(LaboratoryIsolate.objects.values().get(pk=done.pk), before)

    def test_fresh_unendorsed_pending_sibling_is_not_mistaken_for_prior_handoff(self):
        accession, done, queued = self.mixed_batch()
        done.status = 'id_pending'
        done.endorsed_at = done.returned_at = done.reviewed_at = done.lyophilization_completed_at = done.biobanked_at = None
        done.save()
        with self.assertRaises(WorkflowError):
            create_id_handoff(accession.pk, self.lab_user)
        self.assertFalse(accession.isolate_records.filter(endorsed_by=self.lab_user).exists())
