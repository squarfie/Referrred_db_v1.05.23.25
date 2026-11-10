
from django.contrib.auth.models import Group, User
from django.test import TestCase

from apps.home.models import Batch_Table, Referred_Data
from apps.home_final.models import Final_Data
from apps.verification.models import VerificationCase
from apps.wgs_app.views import _referred_bulk_upload_skip_reason


class FourUploadWorkflowSecurityTests(TestCase):
    """Same permission guard is used by four Raw/Final upload paths."""

    def setUp(self):
        self.encoder = User.objects.create_user("four-upload-owner", password="test")
        self.encoder.groups.add(Group.objects.get_or_create(name="DMU Encoder")[0])
        self.other = User.objects.create_user("four-upload-other", password="test")
        self.other.groups.add(Group.objects.get(name="DMU Encoder"))
        self.admin = User.objects.create_user("four-upload-admin", password="test")
        self.admin.groups.add(Group.objects.get_or_create(name="Admin")[0])
        self.batch = Batch_Table.objects.create(
            bat_Batch_Code="FOUR-UP", bat_Batch_Name="Four upload test",
            created_by=self.encoder
        )
        self.raw = Referred_Data.objects.create(
            AccessionNo="FOUR-UP-1", Batch_id=self.batch, Batch_Code="FOUR-UP"
        )
        self.final = Final_Data.objects.create(
            f_AccessionNo="FOUR-UP-1", f_Batch_id=self.batch,
            f_Batch_Code="FOUR-UP"
        )

    def test_raw_draft_with_no_verification_is_eligible(self):
        self.assertIsNone(
            _referred_bulk_upload_skip_reason(self.encoder, self.raw)
        )

    def test_final_draft_with_no_verification_is_eligible(self):
        self.assertIsNone(
            _referred_bulk_upload_skip_reason(self.encoder, self.final, final=True)
        )

    def test_other_encoder_cannot_update_raw_or_final(self):
        self.assertEqual(
            _referred_bulk_upload_skip_reason(self.other, self.raw),
            "another user's batch",
        )
        self.assertEqual(
            _referred_bulk_upload_skip_reason(self.other, self.final, final=True),
            "another user's batch",
        )

    def test_raw_verification_blocks_both_bulk_imports(self):
        VerificationCase.objects.create(
            final_batch=self.batch,
            status=VerificationCase.STATUS_FOR_RAW_VERIFICATION,
        )
        self.assertEqual(
            _referred_bulk_upload_skip_reason(self.encoder, self.raw),
            "verification or release lock",
        )
        self.assertEqual(
            _referred_bulk_upload_skip_reason(self.encoder, self.final, final=True),
            "verification or release lock",
        )

    def test_final_encoding_allows_final_but_not_raw(self):
        VerificationCase.objects.create(
            final_batch=self.batch,
            status=VerificationCase.STATUS_FOR_FINAL_ENCODING,
        )
        self.assertIsNone(
            _referred_bulk_upload_skip_reason(self.encoder, self.final, final=True)
        )
        self.assertEqual(
            _referred_bulk_upload_skip_reason(self.encoder, self.raw),
            "verification or release lock",
        )

    def test_signed_or_released_data_is_never_import_editable(self):
        for status in (
            VerificationCase.STATUS_SIGNED_BY_HEAD,
            VerificationCase.STATUS_RELEASED,
        ):
            case = VerificationCase.objects.create(
                final_batch=self.batch, status=status,
            )
            self.assertEqual(
                _referred_bulk_upload_skip_reason(self.admin, self.raw),
                "verification or release lock",
            )
            self.assertEqual(
                _referred_bulk_upload_skip_reason(self.admin, self.final, final=True),
                "verification or release lock",
            )
            case.delete()

    def test_withdrawn_case_can_be_updated_by_owner(self):
        VerificationCase.objects.create(
            final_batch=self.batch,
            status=VerificationCase.STATUS_WITHDRAWN,
        )
        self.assertIsNone(
            _referred_bulk_upload_skip_reason(self.encoder, self.raw)
        )
        self.assertIsNone(
            _referred_bulk_upload_skip_reason(self.encoder, self.final, final=True)
        )

    def test_more_than_one_case_requires_all_to_be_editable(self):
        VerificationCase.objects.create(
            final_batch=self.batch,
            status=VerificationCase.STATUS_WITHDRAWN,
        )
        VerificationCase.objects.create(
            final_accession=self.final,
            status=VerificationCase.STATUS_FOR_RAW_VERIFICATION,
        )
        self.assertEqual(
            _referred_bulk_upload_skip_reason(self.encoder, self.raw),
            "verification or release lock",
        )

    def test_unbatched_record_only_admin_or_checker_may_change(self):
        self.raw.Batch_id = None
        self.assertEqual(
            _referred_bulk_upload_skip_reason(self.encoder, self.raw),
            "unbatched existing record",
        )
        self.assertIsNone(
            _referred_bulk_upload_skip_reason(self.admin, self.raw)
        )

    def test_final_without_batch_inherits_raw_ownership(self):
        self.final.f_Batch_id = None
        self.assertEqual(
            _referred_bulk_upload_skip_reason(self.other, self.final, final=True),
            "another user's batch",
        )

    def test_raw_antibiotic_upload_skips_protected_accession(self):
        from unittest.mock import patch
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.urls import reverse
        from apps.home.models import Antibiotic_List, AntibioticEntry

        Antibiotic_List.objects.create(
            Whonet_Abx="AMP_ND10", Abx_code="AMP", Antibiotic="Ampicillin"
        )
        VerificationCase.objects.create(
            final_batch=self.batch,
            status=VerificationCase.STATUS_FOR_RAW_VERIFICATION,
        )
        self.client.force_login(self.encoder)
        data = SimpleUploadedFile(
            "abx.csv", b"AccessionNo,AMP_ND10_Val\nFOUR-UP-1,20\n",
            content_type="text/csv"
        )
        with patch("apps.wgs_app.views.reapply_raw_breakpoints_for_batches", return_value=0):
            response = self.client.post(
                reverse("upload_raw_antibiotics"),
                {"RawAntibioticFile": data, "overwrite": "true"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(AntibioticEntry.objects.filter(
            ab_idNum_referred=self.raw
        ).exists())

    def test_final_antibiotic_upload_skips_protected_accession(self):
        from unittest.mock import patch
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.urls import reverse
        from apps.home.models import Antibiotic_List
        from apps.home_final.models import Final_AntibioticEntry

        Antibiotic_List.objects.create(
            Whonet_Abx="AMP_ND10", Abx_code="AMP", Antibiotic="Ampicillin"
        )
        VerificationCase.objects.create(
            final_batch=self.batch,
            status=VerificationCase.STATUS_FOR_VERIFICATION,
        )
        self.client.force_login(self.encoder)
        data = SimpleUploadedFile(
            "abx.csv", b"f_AccessionNo,AMP_ND10_Val\nFOUR-UP-1,20\n",
            content_type="text/csv"
        )
        with patch("apps.wgs_app.views.reapply_final_breakpoints_for_batches", return_value=0):
            response = self.client.post(
                reverse("upload_final_antibiotics"),
                {"FinalAntibioticFile": data, "overwrite": "true"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Final_AntibioticEntry.objects.filter(
            ab_idNum_f_referred=self.final
        ).exists())

    def test_raw_demographic_upload_skips_verified_accession(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.urls import reverse
        VerificationCase.objects.create(
            final_batch=self.batch,
            status=VerificationCase.STATUS_FOR_RAW_VERIFICATION,
        )
        self.client.force_login(self.encoder)
        upload = SimpleUploadedFile(
            "demogs.csv",
            b"AccessionNo,Batch_Code,First_Name\nFOUR-UP-1,FOUR-UP,Changed\n",
            content_type="text/csv",
        )
        response = self.client.post(
            reverse("upload_referred_table"),
            {"DemogsDataFile": upload, "overwrite": "true",
             "demographic_upload_mode": "raw"},
        )
        self.assertEqual(response.status_code, 302)
        self.raw.refresh_from_db()
        self.assertNotEqual(self.raw.First_Name, "Changed")

    def test_final_demographic_upload_skips_verified_accession(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.urls import reverse
        VerificationCase.objects.create(
            final_batch=self.batch,
            status=VerificationCase.STATUS_FOR_VERIFICATION,
        )
        self.client.force_login(self.encoder)
        upload = SimpleUploadedFile(
            "final_demogs.csv",
            b"f_AccessionNo,f_Batch_Code,f_First_Name\nFOUR-UP-1,FOUR-UP,Changed\n",
            content_type="text/csv",
        )
        response = self.client.post(
            reverse("upload_referred_table"),
            {"DemogsDataFile": upload, "overwrite": "true",
             "demographic_upload_mode": "final"},
        )
        self.assertEqual(response.status_code, 302)
        self.final.refresh_from_db()
        self.assertNotEqual(self.final.f_First_Name, "Changed")


class WGSReconciliationTests(TestCase):
    """Final accession links may disappear; WGS result rows must survive."""

    def setUp(self):
        from apps.wgs_app.models import CustomWGSPipeline
        self.pipeline = CustomWGSPipeline.objects.create(
            name="Reconciliation test pipeline",
            slug="reconciliation-test-pipeline",
        )
        self.accession = "26ARS_ZMC0023"
        self.final = Final_Data.objects.create(f_AccessionNo=self.accession)

    def test_final_deletion_preserves_wgs_results_and_reimport_restores_links(self):
        from apps.wgs_app.models import (
            WGS_Project, SampleInformation, CustomWGSPipelineRecord,
        )

        project = WGS_Project.objects.create(
            Ref_Accession=self.final,
            WGS_SampleInfo_Acc=self.accession,
            WGS_SampleInfoSummary=True,
        )
        sample = SampleInformation.objects.create(
            sample_project=project,
            sample_accession=self.accession,
            sample_name="test-run-1",
        )
        record = CustomWGSPipelineRecord.objects.create(
            pipeline=self.pipeline,
            accession=self.accession,
            matched_final_data=self.final,
            match_status="matched",
            match_source="final",
            values_json={"result": "PASS"},
        )
        old_project_pk = project.pk
        old_final_pk = self.final.pk
        self.final.delete()

        project.refresh_from_db()
        self.assertEqual(project.pk, old_project_pk)
        self.assertIsNone(project.Ref_Accession_id)
        self.assertEqual(project.original_accession, self.accession)
        sample.refresh_from_db()
        record.refresh_from_db()
        self.assertEqual(sample.sample_project_id, old_project_pk)
        self.assertIsNone(record.matched_final_data_id)
        self.assertEqual(record.match_status, "unmatched")
        self.assertEqual(record.values_json, {"result": "PASS"})

        replacement = Final_Data.objects.create(f_AccessionNo=self.accession)
        self.assertNotEqual(replacement.pk, old_final_pk)
        sample.refresh_from_db()
        record.refresh_from_db()
        self.assertEqual(record.matched_final_data_id, self.accession)
        self.assertEqual(record.match_status, "matched")
        self.assertEqual(record.values_json, {"result": "PASS"})
        self.assertEqual(sample.sample_project_id, old_project_pk)
        project.refresh_from_db()
        self.assertEqual(project.Ref_Accession_id, self.accession)
        self.assertEqual(WGS_Project.objects.filter(pk=old_project_pk).count(), 1)

    def test_wgs_project_without_module_accessions_retains_original_accession(self):
        from apps.wgs_app.models import WGS_Project
        project = WGS_Project.objects.create(Ref_Accession=self.final)
        project.refresh_from_db()
        self.assertEqual(project.original_accession, self.accession)
        project_id = project.pk
        self.final.delete()
        project.refresh_from_db()
        self.assertIsNone(project.Ref_Accession_id)
        self.assertEqual(project.original_accession, self.accession)
        self.assertEqual(project.pk, project_id)
        Final_Data.objects.create(f_AccessionNo=self.accession)
        project.refresh_from_db()
        self.assertEqual(project.Ref_Accession_id, self.accession)

    def test_reconcile_multiple_projects_keeps_distinct_run_ids(self):
        from apps.wgs_app.models import WGS_Project, SampleInformation
        from apps.wgs_app.reconciliation import reconcile_wgs_matches
        projects = []
        for run in ("run-a", "run-b"):
            project = WGS_Project.objects.create(
                Ref_Accession=self.final,
                WGS_SampleInfo_Acc=self.accession,
            )
            SampleInformation.objects.create(
                sample_project=project,
                sample_accession=self.accession,
                sample_name=run,
            )
            projects.append(project.pk)
        self.final.delete()
        result = reconcile_wgs_matches()
        self.assertEqual(result["projects_relinked"], 0)
        Final_Data.objects.create(f_AccessionNo=self.accession)
        for project_id in projects:
            project = WGS_Project.objects.get(pk=project_id)
            self.assertEqual(project.Ref_Accession_id, self.accession)
            self.assertEqual(project.sample_information_entries.count(), 1)

    def test_custom_pipeline_file_accession_is_relinked(self):
        from apps.wgs_app.models import CustomWGSPipelineRecord
        from apps.wgs_app.reconciliation import reconcile_wgs_matches
        row = CustomWGSPipelineRecord.objects.create(
            pipeline=self.pipeline,
            accession=self.accession + ".fna",
            match_status="unmatched",
        )
        self.assertEqual(reconcile_wgs_matches(include_builtin=False, dry_run=True)["custom_matched"], 1)
        row.refresh_from_db()
        self.assertIsNone(row.matched_final_data_id)

        result = reconcile_wgs_matches(include_builtin=False)
        self.assertEqual(result["custom_matched"], 1)
        row.refresh_from_db()
        self.assertEqual(row.matched_final_data_id, self.accession)
        self.assertEqual(row.match_status, "matched")
        self.assertEqual(reconcile_wgs_matches(include_builtin=False)["custom_matched"], 0)

    def test_orphaned_distinct_wgs_runs_are_not_merged(self):
        from apps.wgs_app.models import SampleInformation, WGS_Project
        from apps.wgs_app.reconciliation import reconcile_wgs_matches
        for run in ("run-1", "run-2"):
            SampleInformation.objects.create(
                sample_accession=self.accession,
                sample_name=run,
            )
        result = reconcile_wgs_matches()
        self.assertEqual(result["builtin_relinked"], 0)
        self.assertEqual(result["ambiguous_or_unmatched"], 2)
        self.assertFalse(WGS_Project.objects.filter(Ref_Accession=self.final).exists())

    def test_deleting_project_keeps_wgs_result_and_relinks_it(self):
        from apps.wgs_app.models import WGS_Project, SampleInformation
        from apps.wgs_app.reconciliation import reconcile_wgs_matches
        project = WGS_Project.objects.create(
            Ref_Accession=self.final,
            WGS_SampleInfo_Acc=self.accession,
        )
        sample = SampleInformation.objects.create(
            sample_project=project, sample_accession=self.accession,
            sample_name="single-run",
        )
        project.delete()
        sample.refresh_from_db()
        self.assertIsNone(sample.sample_project_id)
        result = reconcile_wgs_matches()
        self.assertEqual(result["builtin_relinked"], 1)
        sample.refresh_from_db()
        self.assertEqual(sample.sample_project.Ref_Accession_id, self.accession)

    def test_filename_alias_becomes_unmatched_on_final_deletion(self):
        from apps.wgs_app.models import CustomWGSPipelineRecord
        row = CustomWGSPipelineRecord.objects.create(
            pipeline=self.pipeline,
            accession=self.accession + ".fna",
            matched_final_data=self.final,
            match_status="matched",
            match_source="final",
        )
        self.final.delete()
        row.refresh_from_db()
        self.assertIsNone(row.matched_final_data_id)
        self.assertEqual(row.match_status, "unmatched")

    def test_command_dry_run_preserves_unmatched_custom_record(self):
        from io import StringIO
        from django.core.management import call_command
        from apps.wgs_app.models import CustomWGSPipelineRecord
        row = CustomWGSPipelineRecord.objects.create(
            pipeline=self.pipeline,
            accession=self.accession,
            match_status="unmatched",
        )
        stream = StringIO()
        call_command("reconcile_wgs_matches", "--dry-run", stdout=stream)
        self.assertIn("custom_matched=1", stream.getvalue())
        row.refresh_from_db()
        self.assertIsNone(row.matched_final_data_id)
