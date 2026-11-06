# -*- encoding: utf-8 -*-
"""
Copyright (c) 2019 - present AppSeed.us
"""

from django.contrib.auth.models import User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase
from io import BytesIO
from openpyxl import load_workbook

from apps.home.models import AntibioticEntry, BreakpointsTable, Referred_Data
from apps.home.views import export_Antibioticentry
from apps.home_final.models import Final_AntibioticEntry, Final_Data
from apps.home_final.views import export_final_antibiotic_entries
from django.urls import reverse


class DeleteBreakpointsByDateModifiedRangeTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username="settings-user", password="test")

    def _post(self, data):
        from apps.home.views import delete_breakpoints_by_date_modified_range

        request = self.factory.post("/breakpoints-delete-by-date-modified/", data=data)
        request.user = self.user
        SessionMiddleware(lambda req: None).process_request(request)
        request.session.save()
        setattr(request, "_messages", FallbackStorage(request))
        return delete_breakpoints_by_date_modified_range(request)

    def _breakpoint_on(self, abx_code, date_modified, year="2026"):
        breakpoint = BreakpointsTable.objects.create(
            Abx_code=abx_code,
            Whonet_Abx=f"{abx_code}_NM",
            Year=year,
        )
        BreakpointsTable.objects.filter(pk=breakpoint.pk).update(Date_Modified=date_modified)
        breakpoint.refresh_from_db()
        return breakpoint

    def test_deletes_rows_with_date_modified_inside_inclusive_range_when_year_is_blank(self):
        before = self._breakpoint_on("BEF", "2026-09-09", year="2025")
        start = self._breakpoint_on("STA", "2026-09-10", year="2026")
        inside = self._breakpoint_on("MID", "2026-09-11", year="2025")
        end = self._breakpoint_on("END", "2026-09-12", year="2026")
        after = self._breakpoint_on("AFT", "2026-09-13", year="2026")

        response = self._post({
            "date_modified_start": "2026-09-10",
            "date_modified_end": "2026-09-12",
        })

        self.assertEqual(response.status_code, 302)
        self.assertTrue(BreakpointsTable.objects.filter(pk=before.pk).exists())
        self.assertFalse(BreakpointsTable.objects.filter(pk=start.pk).exists())
        self.assertFalse(BreakpointsTable.objects.filter(pk=inside.pk).exists())
        self.assertFalse(BreakpointsTable.objects.filter(pk=end.pk).exists())
        self.assertTrue(BreakpointsTable.objects.filter(pk=after.pk).exists())

    def test_deletes_rows_with_selected_year_inside_inclusive_date_modified_range(self):
        before = self._breakpoint_on("BEF", "2026-09-09")
        start = self._breakpoint_on("STA", "2026-09-10")
        inside = self._breakpoint_on("MID", "2026-09-11")
        end = self._breakpoint_on("END", "2026-09-12")
        after = self._breakpoint_on("AFT", "2026-09-13")
        other_year = self._breakpoint_on("OLD", "2026-09-11", year="2025")

        response = self._post({
            "breakpoint_year": "2026",
            "date_modified_start": "2026-09-10",
            "date_modified_end": "2026-09-12",
        })

        self.assertEqual(response.status_code, 302)
        self.assertTrue(BreakpointsTable.objects.filter(pk=before.pk).exists())
        self.assertFalse(BreakpointsTable.objects.filter(pk=start.pk).exists())
        self.assertFalse(BreakpointsTable.objects.filter(pk=inside.pk).exists())
        self.assertFalse(BreakpointsTable.objects.filter(pk=end.pk).exists())
        self.assertTrue(BreakpointsTable.objects.filter(pk=after.pk).exists())
        self.assertTrue(BreakpointsTable.objects.filter(pk=other_year.pk).exists())

    def test_requires_both_dates(self):
        breakpoint = self._breakpoint_on("KEEP", "2026-09-10")

        response = self._post({
            "date_modified_start": "2026-09-10",
        })

        self.assertEqual(response.status_code, 302)
        self.assertTrue(BreakpointsTable.objects.filter(pk=breakpoint.pk).exists())

    def test_deletes_all_rows_for_selected_year_when_dates_are_blank(self):
        selected_year_first = self._breakpoint_on("Y26A", "2026-09-10", year="2026")
        selected_year_second = self._breakpoint_on("Y26B", "2026-10-10", year="2026")
        other_year = self._breakpoint_on("Y25A", "2026-09-10", year="2025")

        response = self._post({"breakpoint_year": "2026"})

        self.assertEqual(response.status_code, 302)
        self.assertFalse(BreakpointsTable.objects.filter(pk=selected_year_first.pk).exists())
        self.assertFalse(BreakpointsTable.objects.filter(pk=selected_year_second.pk).exists())
        self.assertTrue(BreakpointsTable.objects.filter(pk=other_year.pk).exists())

    def test_requires_year_or_date_range(self):
        breakpoint = self._breakpoint_on("KEEP", "2026-09-10")

        response = self._post({})

        self.assertEqual(response.status_code, 302)
        self.assertTrue(BreakpointsTable.objects.filter(pk=breakpoint.pk).exists())

    def test_rejects_reversed_date_range(self):
        breakpoint = self._breakpoint_on("KEEP", "2026-09-10")

        response = self._post({
            "breakpoint_year": "2026",
            "date_modified_start": "2026-09-12",
            "date_modified_end": "2026-09-10",
        })

        self.assertEqual(response.status_code, 302)
        self.assertTrue(BreakpointsTable.objects.filter(pk=breakpoint.pk).exists())


class AntibioticEntryExportAuditColumnTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username="export-user", password="test")

    def _xlsx_rows(self, response):
        content = b"".join(response.streaming_content)
        workbook = load_workbook(BytesIO(content), read_only=True)
        return list(workbook.active.iter_rows(values_only=True))

    def _request(self, path):
        request = self.factory.get(path)
        request.user = self.user
        return request

    def test_raw_antibiotic_entry_export_includes_parent_audit_columns(self):
        raw = Referred_Data.objects.create(
            AccessionNo="RAW-EXPORT-001",
            arsp_Encoder="Raw Encoder",
            arsp_Checker="Raw Checker",
        )
        AntibioticEntry.objects.create(
            ab_idNum_referred=raw,
            ab_AccessionNo=raw.AccessionNo,
            ab_Antibiotic="Ampicillin",
            ab_Abx_code="AMP",
        )

        response = export_Antibioticentry(self._request("/antibioticentry-export/"))
        rows = self._xlsx_rows(response)
        headers = rows[0]
        row = dict(zip(headers, rows[1]))

        for header in ("Date_of_Entry", "Date_Modified", "arsp_Encoder", "arsp_Checker"):
            self.assertIn(header, headers)
        self.assertEqual(row["arsp_Encoder"], "Raw Encoder")
        self.assertEqual(row["arsp_Checker"], "Raw Checker")
        self.assertIsNotNone(row["Date_of_Entry"])
        self.assertIsNotNone(row["Date_Modified"])

    def test_final_antibiotic_entry_export_includes_parent_audit_columns(self):
        final = Final_Data.objects.create(
            f_AccessionNo="FINAL-EXPORT-001",
            f_arsp_Encoder="Final Encoder",
            f_arsp_Checker="Final Checker",
        )
        Final_AntibioticEntry.objects.create(
            ab_idNum_f_referred=final,
            ab_AccessionNo=final.f_AccessionNo,
            ab_Antibiotic="Ampicillin",
            ab_Abx_code="AMP",
        )

        response = export_final_antibiotic_entries(self._request("/final/download/abx_entries/"))
        rows = self._xlsx_rows(response)
        headers = rows[0]
        row = dict(zip(headers, rows[1]))

        for header in ("f_Date_of_Entry", "f_Date_Modified", "f_arsp_Encoder", "f_arsp_Checker"):
            self.assertIn(header, headers)
        self.assertEqual(row["f_arsp_Encoder"], "Final Encoder")
        self.assertEqual(row["f_arsp_Checker"], "Final Checker")
        self.assertIsNotNone(row["f_Date_of_Entry"])
        self.assertIsNotNone(row["f_Date_Modified"])


class DeleteAllSiteCodesSecurityTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import Group
        from django.urls import reverse

        from apps.home.models import SiteData

        self.url = reverse("delete_all_dropdown")
        self.site = SiteData.objects.create(SiteCode="ABC", SiteName="Test Hospital")
        self.admin_user = User.objects.create_user(username="site-admin", password="test-pass")
        self.admin_user.groups.add(Group.objects.get_or_create(name="Admin")[0])
        self.regular_user = User.objects.create_user(username="site-viewer", password="test-pass")

    def test_anonymous_post_redirects_to_login_without_deleting(self):
        from apps.home.models import SiteData

        response = self.client.post(self.url, {"confirmation": "DELETE ALL SITES"})
        self.assertRedirects(response, "/login/?next=" + self.url)
        self.assertEqual(SiteData.objects.count(), 1)

    def test_non_admin_cannot_delete(self):
        from apps.home.models import SiteData

        self.client.force_login(self.regular_user)
        response = self.client.post(self.url, {"confirmation": "DELETE ALL SITES"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SiteData.objects.count(), 1)

    def test_get_never_deletes_even_for_admin(self):
        from apps.home.models import SiteData

        self.client.force_login(self.admin_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)
        self.assertEqual(SiteData.objects.count(), 1)

    def test_admin_must_type_confirmation(self):
        from apps.home.models import SiteData

        self.client.force_login(self.admin_user)
        response = self.client.post(self.url, {"confirmation": "wrong"})
        self.assertRedirects(response, "/site-view")
        self.assertEqual(SiteData.objects.count(), 1)

    def test_admin_post_with_confirmation_deletes_all(self):
        from apps.home.models import SiteData

        self.client.force_login(self.admin_user)
        response = self.client.post(self.url, {"confirmation": "DELETE ALL SITES"})
        self.assertRedirects(response, "/site-view")
        self.assertFalse(SiteData.objects.exists())


class BatchDeletionSecurityTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import Group
        from django.urls import reverse
        from apps.home.models import Batch_Table, Referred_Data
        self.admin = User.objects.create_user("batch-admin", password="pass")
        self.admin.groups.add(Group.objects.get_or_create(name="Admin")[0])
        self.encoder = User.objects.create_user("batch-encoder", password="pass")
        self.encoder.groups.add(Group.objects.get_or_create(name="DMU Encoder")[0])
        self.viewer = User.objects.create_user("batch-viewer", password="pass")
        self.batch = Batch_Table.objects.create(created_by=self.encoder, bat_Batch_Name="B1", bat_Batch_Code="B1")
        Referred_Data.objects.create(Batch_id=self.batch, Batch_Code="B1", AccessionNo="B1-1")
        self.clean_url = reverse("clean_batch", args=[self.batch.pk])
        self.all_url = reverse("delete_all_batches")
        self.blank_url = reverse("delete_blank_batches")

    def test_anonymous_cannot_delete(self):
        from apps.home.models import Batch_Table
        self.client.post(self.clean_url)
        self.assertTrue(Batch_Table.objects.filter(pk=self.batch.pk).exists())

    def test_unprivileged_user_cannot_delete(self):
        from apps.home.models import Batch_Table
        self.client.force_login(self.viewer)
        self.client.post(self.clean_url)
        self.assertTrue(Batch_Table.objects.filter(pk=self.batch.pk).exists())

    def test_encoder_cannot_delete_others_batch(self):
        from apps.home.models import Batch_Table
        from django.contrib.auth.models import Group
        other = User.objects.create_user("other-encoder", password="pass")
        other.groups.add(Group.objects.get(name="DMU Encoder"))
        self.client.force_login(other)
        self.client.post(self.clean_url)
        self.assertTrue(Batch_Table.objects.filter(pk=self.batch.pk).exists())

    def test_get_is_not_allowed(self):
        from apps.home.models import Batch_Table
        self.client.force_login(self.admin)
        for url in (self.clean_url, self.all_url, self.blank_url):
            self.assertEqual(self.client.get(url).status_code, 405)
        self.assertTrue(Batch_Table.objects.filter(pk=self.batch.pk).exists())

    def test_bulk_requires_admin_and_correct_confirmation(self):
        from apps.home.models import Batch_Table
        self.client.force_login(self.encoder)
        self.client.post(self.all_url, {"confirmation": "DELETE ALL BATCHES"})
        self.client.post(self.blank_url, {"confirmation": "DELETE BLANK BATCHES"})
        self.client.force_login(self.admin)
        self.client.post(self.all_url, {"confirmation": "incorrect"})
        self.client.post(self.blank_url, {"confirmation": "incorrect"})
        self.assertTrue(Batch_Table.objects.filter(pk=self.batch.pk).exists())

    def test_owner_can_delete_unlocked_batch(self):
        from apps.home.models import Batch_Table
        self.client.force_login(self.encoder)
        self.client.post(self.clean_url)
        self.assertFalse(Batch_Table.objects.filter(pk=self.batch.pk).exists())

    def test_blank_deletion_preserves_mixed_batch(self):
        from apps.home.models import Batch_Table, Referred_Data
        Referred_Data.objects.create(Batch_id=self.batch, Batch_Code="B1", AccessionNo="B1-2", Site_Org="eco")
        self.client.force_login(self.admin)
        self.client.post(self.blank_url, {"confirmation": "DELETE BLANK BATCHES"})
        self.assertTrue(Batch_Table.objects.filter(pk=self.batch.pk).exists())

    def test_admin_can_delete_all_after_confirmation(self):
        from apps.home.models import Batch_Table
        self.client.force_login(self.admin)
        self.client.post(self.all_url, {"confirmation": "DELETE ALL BATCHES"})
        self.assertFalse(Batch_Table.objects.filter(pk=self.batch.pk).exists())

    def test_admin_can_delete_fully_blank_batches(self):
        from apps.home.models import Batch_Table
        self.client.force_login(self.admin)
        self.client.post(self.blank_url, {"confirmation": "DELETE BLANK BATCHES"})
        self.assertFalse(Batch_Table.objects.filter(pk=self.batch.pk).exists())

    def test_active_verification_blocks_single_batch_deletion(self):
        from apps.home.models import Batch_Table
        from apps.verification.models import VerificationCase
        VerificationCase.objects.create(
            final_batch=self.batch,
            status=VerificationCase.STATUS_FOR_RAW_VERIFICATION,
        )
        self.client.force_login(self.admin)
        self.client.post(self.clean_url)
        self.assertTrue(Batch_Table.objects.filter(pk=self.batch.pk).exists())

    def test_active_verification_blocks_bulk_deletion(self):
        from apps.home.models import Batch_Table
        from apps.verification.models import VerificationCase
        VerificationCase.objects.create(
            final_batch=self.batch,
            status=VerificationCase.STATUS_FOR_RAW_VERIFICATION,
        )
        self.client.force_login(self.admin)
        self.client.post(self.all_url, {"confirmation": "DELETE ALL BATCHES"})
        self.client.post(self.blank_url, {"confirmation": "DELETE BLANK BATCHES"})
        self.assertTrue(Batch_Table.objects.filter(pk=self.batch.pk).exists())


class ReviewBatchesPaginationTests(TestCase):
    def setUp(self):
        from apps.home.models import Batch_Table
        from django.urls import reverse
        self.user = User.objects.create_user("review-pagination-user", password="test-pass")
        self.client.force_login(self.user)
        self.url = reverse("review_batches")
        for index in range(31):
            Batch_Table.objects.create(
                bat_Batch_Code=f"REVIEW-{index:03d}",
                bat_Batch_Name=f"Review batch {index}",
            )

    def test_default_25_rows_and_second_page(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["batches"]), 25)
        self.assertEqual(response.context["paginator"].count, 31)
        self.assertContains(response, "Go to page:")
        response2 = self.client.get(self.url, {"page": "2"})
        self.assertEqual(len(response2.context["batches"]), 6)

    def test_rows_per_page_selection(self):
        for size in (10, 25, 50, 100):
            response = self.client.get(self.url, {"per_page": size})
            self.assertEqual(response.context["current_per_page"], size)
            self.assertEqual(len(response.context["batches"]), min(size, 31))

    def test_invalid_page_size_defaults_to_25(self):
        for invalid in ("abc", "0", "200"):
            response = self.client.get(self.url, {"per_page": invalid})
            self.assertEqual(response.context["current_per_page"], 25)

    def test_search_preserves_rows_per_page(self):
        # A search returning only one page has no pagination links.
        # Confirm the form retains the selected page size instead.
        response = self.client.get(self.url, {"q": "REVIEW-00", "per_page": "10"})
        self.assertEqual(response.context["paginator"].count, 10)
        self.assertEqual(response.context["current_per_page"], 10)
        self.assertContains(response, 'name="per_page" value="10"')
        self.assertContains(response, '<option value="10" selected>')

    def test_multi_page_navigation_preserves_search_and_page_size(self):
        response = self.client.get(self.url, {"q": "REVIEW-0", "per_page": "10"})
        self.assertEqual(response.context["paginator"].count, 31)
        self.assertContains(response, "page=2&amp;per_page=10&amp;q=REVIEW-0")
        next_page = self.client.get(
            self.url, {"q": "REVIEW-0", "per_page": "10", "page": "2"}
        )
        self.assertEqual(next_page.context["page_obj"].number, 2)
        self.assertEqual(len(next_page.context["batches"]), 10)


class DestructiveBatchActionsSecurityTests(TestCase):
    """Regression coverage for GET, authorization, confirmation and CSRF safety."""

    def setUp(self):
        from django.contrib.auth.models import Group
        from django.urls import reverse
        from apps.home.models import Batch_Table
        from apps.home_final.models import Final_Data

        self.owner = User.objects.create_user("action-owner", password="test-password")
        self.owner.groups.add(Group.objects.get_or_create(name="DMU Encoder")[0])
        self.other = User.objects.create_user("action-other", password="test-password")
        self.other.groups.add(Group.objects.get(name="DMU Encoder"))
        self.viewer = User.objects.create_user("action-viewer", password="test-password")
        self.batch = Batch_Table.objects.create(
            created_by=self.owner, bat_Batch_Name="ACTIONS", bat_Batch_Code="ACTIONS"
        )
        self.raw = Referred_Data.objects.create(
            Batch_id=self.batch, Batch_Code="ACTIONS", AccessionNo="ACTIONS-1"
        )
        self.final = Final_Data.objects.create(
            f_Batch_id=self.batch, f_Batch_Code="ACTIONS", f_AccessionNo="ACTIONS-1"
        )
        self.batch_url = reverse("delete_batch", args=[self.batch.pk])
        self.record_url = reverse("delete_record_in_batch", args=[self.raw.pk])
        self.undo_url = reverse("undo_copy_batch_to_final", args=[self.batch.pk])

    def _all_records_remain(self):
        from apps.home.models import Batch_Table
        from apps.home_final.models import Final_Data

        self.assertTrue(Batch_Table.objects.filter(pk=self.batch.pk).exists())
        self.assertTrue(Referred_Data.objects.filter(pk=self.raw.pk).exists())
        self.assertTrue(Final_Data.objects.filter(pk=self.final.pk).exists())


    
    def test_signed_batch_cannot_be_deleted_by_admin(self):
        from django.contrib.auth.models import Group
        from apps.verification.models import VerificationCase

        admin = User.objects.create_user(
            username="signed-batch-admin",
            password="test-password",
        )
        admin.groups.add(
            Group.objects.get_or_create(name="Admin")[0]
        )

        VerificationCase.objects.create(
            final_batch=self.batch,
            status=VerificationCase.STATUS_SIGNED_BY_HEAD,
        )

        self.client.force_login(admin)

        actions = [
            (reverse("clean_batch", args=[self.batch.pk]), {}),
            (
                self.batch_url,
                {"confirmation": "DELETE BATCH"},
            ),
            (
                reverse("delete_all_batches"),
                {"confirmation": "DELETE ALL BATCHES"},
            ),
            (
                reverse("delete_blank_batches"),
                {"confirmation": "DELETE BLANK BATCHES"},
            ),
        ]

        for url, payload in actions:
            with self.subTest(url=url):
                self.client.post(url, payload)
                self._all_records_remain()

    def test_released_batch_cannot_be_deleted_by_admin(self):
        from django.contrib.auth.models import Group
        from apps.verification.models import VerificationCase

        admin = User.objects.create_user(
            username="released-batch-admin",
            password="test-password",
        )
        admin.groups.add(
            Group.objects.get_or_create(name="Admin")[0]
        )

        VerificationCase.objects.create(
            final_batch=self.batch,
            status=VerificationCase.STATUS_RELEASED,
        )

        self.client.force_login(admin)

        actions = [
            (reverse("clean_batch", args=[self.batch.pk]), {}),
            (
                self.batch_url,
                {"confirmation": "DELETE BATCH"},
            ),
            (
                reverse("delete_all_batches"),
                {"confirmation": "DELETE ALL BATCHES"},
            ),
        ]

        for url, payload in actions:
            with self.subTest(url=url):
                self.client.post(url, payload)
                self._all_records_remain()

    
    def test_batch_deletion_does_not_delete_another_batch_with_same_code(self):
        from apps.home.models import Batch_Table, Referred_Data
        from apps.home_final.models import Final_Data
        from apps.verification.models import VerificationCase

        # Two batches deliberately share the same text code.
        other_batch = Batch_Table.objects.create(
            created_by=self.owner,
            bat_Batch_Name="OTHER BATCH",
            bat_Batch_Code="ACTIONS",
        )

        other_raw = Referred_Data.objects.create(
            Batch_id=other_batch,
            Batch_Code="ACTIONS",
            AccessionNo="OTHER-ACCESSION-001",
        )

        other_final = Final_Data.objects.create(
            f_Batch_id=other_batch,
            f_Batch_Code="ACTIONS",
            f_AccessionNo="OTHER-ACCESSION-001",
        )

        # The other batch has a released verification case.
        verification = VerificationCase.objects.create(
            final_batch=other_batch,
            status=VerificationCase.STATUS_RELEASED,
        )

        self.client.force_login(self.owner)

        response = self.client.post(
            self.batch_url,
            {"confirmation": "DELETE BATCH"},
        )

        self.assertEqual(response.status_code, 302)

        # The selected, eligible batch was deleted.
        self.assertFalse(
            Batch_Table.objects.filter(pk=self.batch.pk).exists()
        )
        self.assertFalse(
            Referred_Data.objects.filter(pk=self.raw.pk).exists()
        )
        self.assertFalse(
            Final_Data.objects.filter(pk=self.final.pk).exists()
        )

        # The other batch and its protected records survive.
        self.assertTrue(
            Batch_Table.objects.filter(pk=other_batch.pk).exists()
        )
        self.assertTrue(
            Referred_Data.objects.filter(pk=other_raw.pk).exists()
        )
        self.assertTrue(
            Final_Data.objects.filter(pk=other_final.pk).exists()
        )
        self.assertTrue(
            VerificationCase.objects.filter(pk=verification.pk).exists()
        )

    def test_batch_deletion_uses_foreign_keys_not_text_codes(self):
        from apps.home.models import Batch_Table, Referred_Data
        from apps.home_final.models import Final_Data

        # Text codes intentionally differ from their parent batch.
        self.raw.Batch_Code = "LEGACY-RAW-CODE"
        self.raw.save(update_fields=["Batch_Code"])

        self.final.f_Batch_Code = "LEGACY-FINAL-CODE"
        self.final.save(update_fields=["f_Batch_Code"])

        self.client.force_login(self.owner)

        response = self.client.post(
            self.batch_url,
            {"confirmation": "DELETE BATCH"},
        )

        self.assertEqual(response.status_code, 302)

        # Actual FK relationships determine deletion.
        self.assertFalse(
            Batch_Table.objects.filter(pk=self.batch.pk).exists()
        )
        self.assertFalse(
            Referred_Data.objects.filter(pk=self.raw.pk).exists()
        )
        self.assertFalse(
            Final_Data.objects.filter(pk=self.final.pk).exists()
        )



    def test_released_accession_cannot_be_deleted(self):
        from django.contrib.auth.models import Group
        from apps.verification.models import VerificationCase

        admin = User.objects.create_user(
            username="released-accession-admin",
            password="test-password",
        )
        admin.groups.add(
            Group.objects.get_or_create(name="Admin")[0]
        )

        VerificationCase.objects.create(
            final_accession=self.final,
            status=VerificationCase.STATUS_RELEASED,
        )

        self.client.force_login(admin)

        actions = [
            (
                self.record_url,
                {"confirmation": "DELETE RECORD"},
            ),
            (
                reverse("delete_data", args=[self.raw.pk]),
                {},
            ),
        ]

        for url, payload in actions:
            with self.subTest(url=url):
                self.client.post(url, payload)
                self._all_records_remain()

    def test_signed_batch_cannot_undo_final_copy(self):
        from apps.verification.models import VerificationCase

        VerificationCase.objects.create(
            final_batch=self.batch,
            status=VerificationCase.STATUS_SIGNED_BY_HEAD,
        )

        self.client.force_login(self.owner)

        self.client.post(
            self.undo_url,
            {"confirmation": "UNDO FINAL COPY"},
        )

        self._all_records_remain()


    def test_get_never_deletes_anything(self):
        self.client.force_login(self.owner)
        for url in (self.batch_url, self.record_url, self.undo_url):
            self.assertEqual(self.client.get(url).status_code, 405)
            self._all_records_remain()

    def test_confirmation_is_required_server_side(self):
        self.client.force_login(self.owner)
        for url in (self.batch_url, self.record_url, self.undo_url):
            response = self.client.post(url)
            self.assertEqual(response.status_code, 302)
            self._all_records_remain()

    def test_anonymous_and_viewer_post_cannot_delete(self):
        for url, confirmation in (
            (self.batch_url, "DELETE BATCH"),
            (self.record_url, "DELETE RECORD"),
            (self.undo_url, "UNDO FINAL COPY"),
        ):
            self.client.logout()
            self.client.post(url, {"confirmation": confirmation})
            self._all_records_remain()
            self.client.force_login(self.viewer)
            self.client.post(url, {"confirmation": confirmation})
            self._all_records_remain()

    def test_another_encoder_cannot_delete_owned_records(self):
        self.client.force_login(self.other)
        for url, confirmation in (
            (self.batch_url, "DELETE BATCH"),
            (self.record_url, "DELETE RECORD"),
            (self.undo_url, "UNDO FINAL COPY"),
        ):
            self.client.post(url, {"confirmation": confirmation})
            self._all_records_remain()

    def test_owner_can_undo_copy_using_post_confirmation(self):
        from apps.home_final.models import Final_Data
        self.client.force_login(self.owner)
        response = self.client.post(self.undo_url, {"confirmation": "UNDO FINAL COPY"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Final_Data.objects.filter(pk=self.final.pk).exists())
        self.assertTrue(Referred_Data.objects.filter(pk=self.raw.pk).exists())

    def test_owner_can_delete_single_record_using_post_confirmation(self):
        self.client.force_login(self.owner)
        response = self.client.post(self.record_url, {"confirmation": "DELETE RECORD"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Referred_Data.objects.filter(pk=self.raw.pk).exists())

    def test_owner_can_delete_batch_using_post_confirmation(self):
        from apps.home.models import Batch_Table
        self.client.force_login(self.owner)
        response = self.client.post(self.batch_url, {"confirmation": "DELETE BATCH"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Batch_Table.objects.filter(pk=self.batch.pk).exists())

    def test_active_verification_blocks_undo(self):
        from apps.verification.models import VerificationCase
        from apps.home_final.models import Final_Data
        VerificationCase.objects.create(
            final_batch=self.batch,
            status=VerificationCase.STATUS_FOR_RAW_VERIFICATION,
        )
        self.client.force_login(self.owner)
        response = self.client.post(self.undo_url, {"confirmation": "UNDO FINAL COPY"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Final_Data.objects.filter(pk=self.final.pk).exists())

    def test_csrf_required_for_each_destructive_post(self):
        from django.test import Client
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.owner)
        for url, confirmation in (
            (self.batch_url, "DELETE BATCH"),
            (self.record_url, "DELETE RECORD"),
            (self.undo_url, "UNDO FINAL COPY"),
        ):
            self.assertEqual(
                csrf_client.post(url, {"confirmation": confirmation}).status_code, 403
            )
            self._all_records_remain()


class ReferredUploadSecurityTests(TestCase):
    """Upload authorization, data-integrity and multi-row regressions."""

    def setUp(self):
        from django.contrib.auth.models import Group
        from django.urls import reverse
        from apps.home.models import Batch_Table
        self.url = reverse("upload_combined_table")
        self.encoder = User.objects.create_user("upload-encoder", password="test")
        self.encoder.groups.add(Group.objects.get_or_create(name="DMU Encoder")[0])
        self.other = User.objects.create_user("other-upload-encoder", password="test")
        self.other.groups.add(Group.objects.get(name="DMU Encoder"))
        self.viewer = User.objects.create_user("upload-viewer", password="test")
        self.batch = Batch_Table.objects.create(
            created_by=self.encoder, bat_Batch_Name="UP-B", bat_Batch_Code="UP-B"
        )
        self.owned = Referred_Data.objects.create(
            Batch_id=self.batch, Batch_Code="UP-B",
            AccessionNo="UP-EXIST", First_Name="Original"
        )

    def _upload(self, content, name="referred.csv", client=None):
        from django.core.files.uploadedfile import SimpleUploadedFile
        return (client or self.client).post(
            self.url, {
                "ReferredDataFile": SimpleUploadedFile(
                    name, content.encode("utf-8"), content_type="text/csv"
                )
            }
        )

    def test_anonymous_and_viewer_cannot_import(self):
        self._upload("AccessionNo,First_Name\nNEW-1,Test\n")
        self.assertFalse(Referred_Data.objects.filter(AccessionNo="NEW-1").exists())
        self.client.force_login(self.viewer)
        self._upload("AccessionNo,First_Name\nNEW-1,Test\n")
        self.assertFalse(Referred_Data.objects.filter(AccessionNo="NEW-1").exists())

    def test_multiple_rows_are_imported(self):
        self.client.force_login(self.encoder)
        self._upload("AccessionNo,First_Name\nNEW-A,Alice\nNEW-B,Bob\n")
        self.assertEqual(
            list(Referred_Data.objects.filter(
                AccessionNo__in=["NEW-A", "NEW-B"]
            ).order_by("AccessionNo").values_list("First_Name", flat=True)),
            ["Alice", "Bob"],
        )

    def test_existing_owned_record_can_update(self):
        self.client.force_login(self.encoder)
        self._upload("AccessionNo,First_Name\nUP-EXIST,Updated\n")
        self.owned.refresh_from_db()
        self.assertEqual(self.owned.First_Name, "Updated")

    def test_foreign_batch_update_rejected(self):
        self.client.force_login(self.other)
        self._upload("AccessionNo,First_Name\nUP-EXIST,Unauthorized\n")
        self.owned.refresh_from_db()
        self.assertEqual(self.owned.First_Name, "Original")

    def test_failure_rolls_back_earlier_rows(self):
        self.client.force_login(self.encoder)
        self._upload(
            "AccessionNo,First_Name\nNEW-OK,Earlier\nUP-EXIST," + "X" * 300 + "\n"
        )
        self.assertFalse(Referred_Data.objects.filter(AccessionNo="NEW-OK").exists())
        self.owned.refresh_from_db()
        self.assertEqual(self.owned.First_Name, "Original")

    def test_protected_field_is_rejected(self):
        self.client.force_login(self.encoder)
        self._upload("AccessionNo,Copy_data\nUP-EXIST,True\n")
        self.owned.refresh_from_db()
        self.assertFalse(self.owned.Copy_data)

    def test_unsupported_extension_is_rejected(self):
        self.client.force_login(self.encoder)
        self._upload("AccessionNo,First_Name\nNEW-X,Test\n", name="referred.exe")
        self.assertFalse(Referred_Data.objects.filter(AccessionNo="NEW-X").exists())

    def test_oversized_file_is_rejected(self):
        self.client.force_login(self.encoder)
        self._upload("AccessionNo,First_Name\nNEW-X," + "X" * (10 * 1024 * 1024) + "\n")
        self.assertFalse(Referred_Data.objects.filter(AccessionNo="NEW-X").exists())

    def test_active_verification_prevents_import_overwrite(self):
        from apps.verification.models import VerificationCase
        VerificationCase.objects.create(
            final_batch=self.batch,
            status=VerificationCase.STATUS_FOR_RAW_VERIFICATION,
        )
        self.client.force_login(self.encoder)
        self._upload("AccessionNo,First_Name\nUP-EXIST,Unapproved\n")
        self.owned.refresh_from_db()
        self.assertEqual(self.owned.First_Name, "Original")

    def test_mixed_import_skips_foreign_batch_but_imports_eligible_rows(self):
        self.client.force_login(self.other)
        response = self._upload(
            "AccessionNo,First_Name\n"
            "NEW-MIX,Alice\n"
            "UP-EXIST,ShouldNotChange\n"
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            Referred_Data.objects.get(AccessionNo="NEW-MIX").First_Name, "Alice"
        )
        self.owned.refresh_from_db()
        self.assertEqual(self.owned.First_Name, "Original")
        details = " ".join(str(message) for message in list(response.wsgi_request._messages))
        self.assertIn("1 protected records skipped", details)
        self.assertIn("UP-EXIST", details)

    def test_mixed_import_skips_verified_batch_and_updates_unlocked_record(self):
        from apps.verification.models import VerificationCase
        VerificationCase.objects.create(
            final_batch=self.batch,
            status=VerificationCase.STATUS_FOR_RAW_VERIFICATION,
        )
        self.client.force_login(self.encoder)
        response = self._upload(
            "AccessionNo,First_Name\n"
            "UP-EXIST,ShouldNotChange\n"
            "NEW-MIX,Allowed\n"
        )
        self.assertEqual(response.status_code, 302)
        self.owned.refresh_from_db()
        self.assertEqual(self.owned.First_Name, "Original")
        self.assertEqual(
            Referred_Data.objects.get(AccessionNo="NEW-MIX").First_Name, "Allowed"
        )

    def test_invalid_row_still_rolls_back_otherwise_eligible_rows_and_skips(self):
        self.client.force_login(self.other)
        response = self._upload(
            "AccessionNo,First_Name\n"
            "NEW-ROLLBACK,ShouldRollback\n"
            "UP-EXIST,Protected\n"
            "NEW-BAD," + ("X" * 300) + "\n"
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            Referred_Data.objects.filter(AccessionNo="NEW-ROLLBACK").exists()
        )
        self.owned.refresh_from_db()
        self.assertEqual(self.owned.First_Name, "Original")

    def test_csrf_required_for_post_upload(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.encoder)
        response = client.post(
            self.url, {
                "ReferredDataFile": SimpleUploadedFile(
                    "raw.csv",
                    b"AccessionNo,First_Name\nNEW-1,Test\n",
                    content_type="text/csv",
                )
            }
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Referred_Data.objects.filter(AccessionNo="NEW-1").exists())
