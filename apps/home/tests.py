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
        response = self.client.get(self.url, {"q": "REVIEW-00", "per_page": "10"})
        self.assertEqual(response.context["paginator"].count, 10)
        self.assertContains(response, "per_page=10")
