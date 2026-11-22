from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.receiving import tests as fixtures
from .models import LaboratoryIsolate, LaboratoryBiobank


class InventoryWorkflowTabsTests(TestCase):
    setUp = fixtures.LaboratoryReceivingWorkspaceTests.setUp
    create_pending_accession = fixtures.LaboratoryReceivingWorkspaceTests.create_pending_accession

    def temporary(self):
        accession = self.create_pending_accession()
        now = timezone.now()
        item = LaboratoryIsolate.objects.create(
            accession_range=accession, accession_number=accession.accession_numbers[0],
            status='site_banked', identification_source='site',
            site_organism_code='KPN', site_organism_name='Klebsiella pneumoniae',
            approved_organism_code='KPN', final_organism='Klebsiella pneumoniae',
            revival_started_at=now-timedelta(days=2), revival_completed_at=now-timedelta(days=1),
            site_banked_at=now, site_banked_by=self.lab_user,
            site_bank_storage={'storage_box': 'TEMP', 'box_position': 'A1'},
        )
        for number in accession.accession_numbers[1:]:
            LaboratoryIsolate.objects.create(accession_range=accession, accession_number=number, status='revival_pending')
        LaboratoryBiobank.objects.create(isolate=item, whonet_code='KPN', storage_box='TEMP', box_position='A1')
        return item

    def page(self, tab, **query):
        return self.client.get(reverse('laboratory_biobank_list'), {'tab': tab, **query})

    def test_tabs_distinguish_temporary_and_verified_final_storage(self):
        item = self.temporary()
        accession = item.accession_range
        final = accession.isolate_records.exclude(pk=item.pk).first()
        now = timezone.now()
        final.status = 'biobanked'
        final.identification_source = 'main_lab'
        final.returned_at = final.reviewed_at = final.lyophilization_completed_at = final.biobanked_at = now
        final.save()
        temporary = self.page('temporary')
        self.assertContains(temporary, item.accession_number)
        self.assertNotContains(temporary, final.accession_number)
        self.assertContains(temporary, 'Queue for endorsement')
        stored = self.page('final')
        self.assertContains(stored, final.accession_number)
        self.assertNotContains(stored, item.accession_number)
        self.assertNotContains(stored, 'Queue for endorsement')

    def test_queued_temporary_storage_remains_visible_without_another_revival(self):
        item = self.temporary()
        original = (item.revival_started_at, item.revival_completed_at, item.site_banked_at, item.site_bank_storage)
        self.client.post(reverse('laboratory_queue_main_lab', args=[item.pk]))
        item.refresh_from_db()
        self.assertEqual(item.status, 'id_queued')
        self.assertIsNone(item.endorsed_at)
        self.assertIsNone(item.biobanked_at)
        self.assertEqual((item.revival_started_at, item.revival_completed_at, item.site_banked_at, item.site_bank_storage), original)
        self.assertEqual(item.biobank.storage_box, 'TEMP')
        self.assertContains(self.page('main_lab'), item.accession_number)
        self.assertNotContains(self.page('temporary'), item.accession_number)
        self.assertNotContains(self.page('final'), item.accession_number)

    def test_final_tab_requires_main_lab_completion_evidence(self):
        item = self.temporary()
        item.status = 'biobanked'
        item.identification_source = 'main_lab'
        item.save()
        self.assertNotContains(self.page('final'), item.accession_number)
        self.assertContains(self.page('all'), item.accession_number)

    def test_search_counts_and_navigation_preserve_selected_tab(self):
        item = self.temporary()
        page = self.page('temporary', q='does-not-match')
        self.assertEqual(page.context['page_obj'].paginator.count, 0)
        self.assertEqual(next(t['count'] for t in page.context['inventory_tabs'] if t['key'] == 'temporary'), 1)
        self.assertContains(page, 'name="tab" value="temporary"')
        self.assertContains(self.page('invalid'), item.accession_number)

    def test_main_lab_staff_cannot_see_bench_a_queue_action(self):
        item = self.temporary()
        staff = User.objects.create_user('inventory-main-lab')
        staff.groups.add(Group.objects.get_or_create(name='Laboratory Staff')[0])
        self.client.force_login(staff)
        self.assertNotContains(self.page('temporary'), 'Queue for endorsement')
        self.client.post(reverse('laboratory_queue_main_lab', args=[item.pk]))
        item.refresh_from_db()
        self.assertEqual(item.status, 'site_banked')
