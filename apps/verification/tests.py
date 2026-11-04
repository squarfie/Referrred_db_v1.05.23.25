from django.contrib.auth.models import Group, User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from apps.home.models import AntibioticEntry, Batch_Table, Referred_Data, arsStaff_Details
from apps.home.permissions import ROLE_ADMIN, ROLE_CHECKER, ROLE_ENCODER, ROLE_LAB_MANAGER, ROLE_VERIFIER
from apps.home_final.models import Final_Data
from apps.verification.models import VerificationCase, VerificationComment
from apps.verification.workflow import (
    final_encoding_is_editable,
    next_raw_draft_status,
    raw_batch_is_fully_copied,
    raw_encoding_is_editable,
)


class VerificationWorkflowRulesTests(SimpleTestCase):
    def test_raw_endorsements_advance_to_third_draft_and_stop(self):
        self.assertEqual(next_raw_draft_status("Encoding"), "First Draft")
        self.assertEqual(next_raw_draft_status("First Draft"), "Second Draft")
        self.assertEqual(next_raw_draft_status("Second Draft"), "Third Draft")
        self.assertEqual(next_raw_draft_status("Third Draft"), "Third Draft")

    def test_raw_encoding_reopens_only_when_returned_during_raw_review(self):
        self.assertTrue(raw_encoding_is_editable(None, final_records_exist=False))
        self.assertTrue(
            raw_encoding_is_editable(
                VerificationCase.STATUS_RETURNED_TO_ENCODER,
                final_records_exist=False,
            )
        )
        self.assertFalse(
            raw_encoding_is_editable(
                VerificationCase.STATUS_FOR_RAW_VERIFICATION,
                final_records_exist=False,
            )
        )
        self.assertFalse(
            raw_encoding_is_editable(
                VerificationCase.STATUS_FOR_FINAL_ENCODING,
                final_records_exist=True,
            )
        )

    def test_final_copy_does_not_lock_raw_draft_or_returned_encoder(self):
        self.assertTrue(raw_encoding_is_editable(None, final_records_exist=True))
        self.assertTrue(raw_encoding_is_editable("", final_records_exist=True))
        self.assertTrue(
            raw_encoding_is_editable(
                VerificationCase.STATUS_RETURNED_TO_ENCODER,
                final_records_exist=True,
            )
        )

    def test_final_copy_does_not_unlock_active_verification(self):
        for status in (
            VerificationCase.STATUS_FOR_RAW_VERIFICATION,
            VerificationCase.STATUS_FOR_FINAL_ENCODING,
            VerificationCase.STATUS_FOR_FINAL_VERIFICATION,
            VerificationCase.STATUS_RELEASED,
        ):
            self.assertFalse(raw_encoding_is_editable(status, final_records_exist=True))

    def test_final_encoding_is_open_before_endorsement_and_for_final_encoding_status(self):
        self.assertTrue(
            final_encoding_is_editable(
                None,
                final_records_exist=True,
            )
        )
        self.assertTrue(
            final_encoding_is_editable(
                VerificationCase.STATUS_FOR_FINAL_ENCODING,
                final_records_exist=True,
            )
        )
        self.assertFalse(
            final_encoding_is_editable(
                VerificationCase.STATUS_FOR_FINAL_VERIFICATION,
                final_records_exist=True,
            )
        )
        self.assertFalse(
            final_encoding_is_editable(
                VerificationCase.STATUS_FOR_FINAL_PRINTING,
                final_records_exist=True,
            )
        )

    def test_open_corrections_unlock_only_the_matching_encoding_stage(self):
        self.assertTrue(
            raw_encoding_is_editable(
                VerificationCase.STATUS_FOR_RAW_VERIFICATION,
                final_records_exist=False,
                has_open_corrections=True,
            )
        )
        self.assertTrue(
            final_encoding_is_editable(
                VerificationCase.STATUS_FOR_FINAL_VERIFICATION,
                final_records_exist=True,
                has_open_corrections=True,
            )
        )
        self.assertFalse(
            final_encoding_is_editable(
                VerificationCase.STATUS_FOR_FINAL_PRINTING,
                final_records_exist=True,
                has_open_corrections=True,
            )
        )

    def test_raw_workflow_remains_active_until_the_whole_batch_is_copied(self):
        self.assertFalse(raw_batch_is_fully_copied(raw_count=9, final_count=0))
        self.assertFalse(raw_batch_is_fully_copied(raw_count=9, final_count=4))
        self.assertTrue(raw_batch_is_fully_copied(raw_count=9, final_count=9))


class RawEndorsementTests(TestCase):
    def setUp(self):
        self.encoder = User.objects.create_user("raw_encoder", password="test-pass")
        self.encoder.groups.add(Group.objects.create(name=ROLE_ENCODER))
        self.batch = Batch_Table.objects.create(
            created_by=self.encoder,
            bat_Batch_Name="RAW-001",
            bat_Batch_Code="RAW-001",
            bat_Status="Encoding",
        )
        self.client.force_login(self.encoder)

    def test_raw_encoder_endorses_without_selecting_a_verifier(self):
        response = self.client.post(
            reverse("send_batch_to_verifier", args=[self.batch.pk]),
            {"next": reverse("show_data")},
        )

        self.assertEqual(response.status_code, 302)
        case = VerificationCase.objects.get(final_batch=self.batch)
        self.assertEqual(case.status, VerificationCase.STATUS_FOR_RAW_VERIFICATION)
        self.assertIsNone(case.assigned_verifier)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.bat_Status, "First Draft")

    def test_raw_send_uses_batch_confirmation_modal(self):
        Referred_Data.objects.create(
            Batch_id=self.batch,
            Batch_Code="RAW-001",
            AccessionNo="RAW-MODAL-1",
        )

        response = self.client.get(reverse("show_data"), {"q": "RAW-001"})

        self.assertContains(response, "Send Raw Batch")
        self.assertContains(response, 'id="sendRawVerificationModal"')
        self.assertContains(response, 'class="batch-workflow-btn send-raw-verification-btn"')
        self.assertNotContains(response, "Endorse RAW-001 for verification?")

    def test_each_raw_reendorsement_advances_the_draft_label(self):
        url = reverse("send_batch_to_verifier", args=[self.batch.pk])
        self.client.post(url)
        case = VerificationCase.objects.get(final_batch=self.batch)

        for expected in ("Second Draft", "Third Draft", "Third Draft"):
            case.status = VerificationCase.STATUS_RETURNED_TO_ENCODER
            case.save(update_fields=["status", "updated_at"])
            self.client.post(url)
            self.batch.refresh_from_db()
            case.refresh_from_db()
            self.assertEqual(self.batch.bat_Status, expected)
            self.assertEqual(case.status, VerificationCase.STATUS_FOR_RAW_VERIFICATION)

    def test_all_verifiers_can_see_an_unassigned_raw_endorsement(self):
        self.client.post(reverse("send_batch_to_verifier", args=[self.batch.pk]))
        verifier = User.objects.create_user("verifier", password="test-pass")
        verifier.groups.add(Group.objects.create(name=ROLE_VERIFIER))
        self.client.force_login(verifier)

        response = self.client.get(reverse("verification_dashboard"))

        self.assertContains(response, "RAW-001")

    def test_partial_final_copy_keeps_raw_send_available_until_batch_is_complete(self):
        Referred_Data.objects.create(
            Batch_id=self.batch,
            Batch_Code="RAW-001",
            AccessionNo="RAW-001-1",
        )
        Referred_Data.objects.create(
            Batch_id=self.batch,
            Batch_Code="RAW-001",
            AccessionNo="RAW-001-2",
        )
        Final_Data.objects.create(
            f_Batch_id=self.batch,
            f_AccessionNo="RAW-001-1",
        )

        partial_response = self.client.get(reverse("show_data"), {"q": "RAW-001"})
        self.assertContains(partial_response, "Endorse raw data for all verifiers")

        Final_Data.objects.create(
            f_Batch_id=self.batch,
            f_AccessionNo="RAW-001-2",
        )
        complete_response = self.client.get(reverse("show_data"), {"q": "RAW-001"})
        self.assertNotContains(complete_response, "Endorse raw data for all verifiers")

    def test_copy_batch_action_is_available_before_raw_endorsement(self):
        Referred_Data.objects.create(
            Batch_id=self.batch,
            Batch_Code="RAW-001",
            AccessionNo="RAW-COPY-1",
        )

        response = self.client.get(
            reverse("raw_batch_rows"),
            {"batch_id": self.batch.pk, "target": "raw-test"},
        )

        self.assertContains(response, "Copy entire batch to Final")

    def test_copy_batch_succeeds_before_raw_endorsement(self):
        Referred_Data.objects.create(
            Batch_id=self.batch,
            Batch_Code="RAW-001",
            AccessionNo="RAW-DIRECT-COPY-1",
        )

        response = self.client.post(reverse("copy_batch_to_final", args=[self.batch.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Final_Data.objects.filter(
                f_Batch_id=self.batch,
                f_AccessionNo="RAW-DIRECT-COPY-1",
            ).exists()
        )

    def test_copy_actions_are_disabled_while_raw_verification_is_pending(self):
        raw = Referred_Data.objects.create(
            Batch_id=self.batch,
            Batch_Code="RAW-001",
            AccessionNo="RAW-LOCKED-COPY-1",
        )
        self.client.post(reverse("send_batch_to_verifier", args=[self.batch.pk]))

        rows_response = self.client.get(
            reverse("raw_batch_rows"),
            {"batch_id": self.batch.pk, "target": "raw-test"},
        )
        self.assertContains(rows_response, "Copy to Final is disabled while Raw verification is pending")
        self.assertNotContains(rows_response, reverse("copy_data_to_final", args=[raw.pk]))
        self.assertNotContains(rows_response, reverse("copy_batch_to_final", args=[self.batch.pk]))

        self.client.post(reverse("copy_data_to_final", args=[raw.pk]))
        self.client.post(reverse("copy_batch_to_final", args=[self.batch.pk]))

        self.assertFalse(Final_Data.objects.filter(f_Batch_id=self.batch).exists())

    def test_raw_encoder_can_withdraw_an_endorsed_batch(self):
        Referred_Data.objects.create(
            Batch_id=self.batch,
            Batch_Code="RAW-001",
            AccessionNo="RAW-WITHDRAW-1",
        )
        self.client.post(reverse("send_batch_to_verifier", args=[self.batch.pk]))
        case = VerificationCase.objects.get(final_batch=self.batch)

        table_response = self.client.get(reverse("show_data"), {"q": "RAW-001"})
        self.assertContains(table_response, "Withdraw raw endorsement")

        self.client.post(
            reverse("withdraw_verification_case", args=[case.pk]),
            {"next": reverse("show_data")},
        )

        case.refresh_from_db()
        self.assertEqual(case.status, VerificationCase.STATUS_WITHDRAWN)

    def test_raw_verification_case_displays_raw_record_details_and_accepts_comments(self):
        raw = Referred_Data.objects.create(
            Batch_id=self.batch,
            Batch_Code="RAW-001",
            AccessionNo="RAW-DETAIL-1",
            Patient_ID="PAT-001",
            First_Name="Maria",
            Last_Name="Santos",
            Site_OrgName="Escherichia coli",
            ars_OrgName="Escherichia coli",
        )
        batch_peer = Referred_Data.objects.create(
            Batch_id=self.batch,
            Batch_Code="RAW-001",
            AccessionNo="RAW-DETAIL-2",
            Patient_ID="PAT-002",
            First_Name="Ana",
            Last_Name="Reyes",
        )
        AntibioticEntry.objects.create(
            ab_idNum_referred=raw,
            ab_Antibiotic="Ampicillin",
            ab_Abx_code="AMP",
            ab_Disk_value="10",
            ab_Disk_RIS="R",
        )
        self.client.post(reverse("send_batch_to_verifier", args=[self.batch.pk]))
        case = VerificationCase.objects.get(final_batch=self.batch)
        Final_Data.objects.create(
            f_Batch_id=self.batch,
            f_AccessionNo="PARTIAL-FINAL-COPY",
        )
        verifier = User.objects.create_user("raw_detail_verifier", password="test-pass")
        verifier.groups.add(Group.objects.create(name=ROLE_VERIFIER))
        self.client.force_login(verifier)

        response = self.client.get(reverse("verification_case_detail", args=[case.pk]))

        self.assertContains(response, "RAW-DETAIL-1")
        self.assertContains(response, "Maria Santos")
        self.assertContains(response, "Escherichia coli")
        self.assertContains(response, "Ampicillin")
        self.assertContains(response, "10 R")
        self.assertNotContains(response, "No final records found for this verification case")

        self.client.post(
            reverse("verification_case_detail", args=[case.pk]),
            {
                "action": "comment",
                "raw_accession_id": raw.pk,
                "field_name": "f_Patient_ID",
                "field_label": "Patient ID:",
                "field_value_snapshot": "PAT-001",
                "decision": VerificationComment.DECISION_NEEDS_CORRECTION,
                "comment": "Please verify the patient ID.",
            },
        )

        comment = VerificationComment.objects.get(verification_case=case)
        self.assertEqual(comment.raw_accession_ref_sequence, raw)

        self.client.force_login(self.encoder)
        rows_response = self.client.get(
            reverse("raw_batch_rows"),
            {"batch_id": self.batch.pk, "target": "raw-test"},
        )
        self.assertContains(rows_response, "Needs correction")
        self.assertContains(rows_response, reverse("edit_data", args=[raw.pk]))
        self.assertContains(rows_response, reverse("edit_data", args=[batch_peer.pk]))
        self.assertContains(rows_response, reverse("batch_edit_view", args=[self.batch.pk]))
        self.assertEqual(self.client.get(reverse("edit_data", args=[batch_peer.pk])).status_code, 200)
        edit_response = self.client.get(reverse("edit_data", args=[raw.pk]))
        self.assertEqual(edit_response.status_code, 200)
        self.assertContains(edit_response, 'id="verification-comment-map"')
        self.assertContains(edit_response, "Please verify the patient ID.")
        self.assertContains(edit_response, '"Patient_ID"')

        table_response = self.client.get(reverse("show_data"), {"q": "RAW-001"})
        self.assertContains(table_response, "batch-comment-alert")
        self.assertContains(table_response, "accession comment(s) need correction")

        applied_response = self.client.post(
            reverse("raw_comment_action", args=[raw.pk, comment.pk]),
            {"comment_action": "mark_handled"},
        )
        self.assertEqual(applied_response.status_code, 302)
        self.assertEqual(applied_response.url, reverse("edit_data", args=[raw.pk]))
        comment.refresh_from_db()
        self.assertTrue(comment.is_resolved)
        self.assertEqual(comment.resolution_status, VerificationComment.RESOLUTION_HANDLED)

    def test_raw_table_keeps_showing_current_status_after_copy_to_final(self):
        raw = Referred_Data.objects.create(
            Batch_id=self.batch,
            Batch_Code="RAW-001",
            AccessionNo="RAW-STATUS-1",
        )
        Final_Data.objects.create(
            f_Batch_id=self.batch,
            f_AccessionNo=raw.AccessionNo,
        )
        case = VerificationCase.objects.create(
            final_batch=self.batch,
            status=VerificationCase.STATUS_FOR_FINAL_ENCODING,
        )
        self.client.force_login(self.encoder)

        response = self.client.get(reverse("show_data"), {"q": "RAW-001"})

        self.assertEqual(
            response.context["page_groups"][0]["verification_status"],
            case.get_status_display(),
        )


class FinalRoutingTests(TestCase):
    def setUp(self):
        self.batch = Batch_Table.objects.create(
            bat_Batch_Name="FINAL-001",
            bat_Batch_Code="FINAL-001",
            bat_Status="First Draft",
        )
        self.verifier = User.objects.create_user("verifier", password="test-pass")
        self.verifier.groups.add(Group.objects.create(name=ROLE_VERIFIER))
        self.verifier_staff = arsStaff_Details.objects.create(
            User_Account=self.verifier,
            Staff_Name="Verifier",
            Staff_Role=ROLE_VERIFIER,
        )
        self.manager = User.objects.create_user("manager", password="test-pass")
        self.manager.groups.add(Group.objects.create(name=ROLE_LAB_MANAGER))
        self.manager_staff = arsStaff_Details.objects.create(
            User_Account=self.manager,
            Staff_Name="Lab Manager",
            Staff_Role=ROLE_LAB_MANAGER,
        )
        self.head = User.objects.create_user("head", password="test-pass")
        self.head_staff = arsStaff_Details.objects.create(
            User_Account=self.head,
            Staff_Name="ARSRL Head",
            Is_Default_Head=True,
        )
        self.case = VerificationCase.objects.create(
            final_batch=self.batch,
            assigned_verifier=self.verifier,
            status=VerificationCase.STATUS_FOR_FINAL_VERIFICATION,
        )
        self.url = reverse("verification_case_detail", args=[self.case.pk])

    def test_verifier_can_return_final_review_for_final_encoding(self):
        self.client.force_login(self.verifier)

        self.client.post(self.url, {"action": "return_final_encoding"})

        self.case.refresh_from_db()
        self.assertEqual(self.case.status, VerificationCase.STATUS_FOR_FINAL_ENCODING)

    def test_final_record_with_open_correction_is_editable_during_review(self):
        final_record = Final_Data.objects.create(
            f_Batch_id=self.batch,
            f_AccessionNo="FINAL-CORRECTION-1",
        )
        batch_peer = Final_Data.objects.create(
            f_Batch_id=self.batch,
            f_AccessionNo="FINAL-CORRECTION-2",
        )
        VerificationComment.objects.create(
            verification_case=self.case,
            final_accession_ref_sequence=final_record,
            field_name="f_Patient_ID",
            field_label="Patient ID:",
            comment="Please correct this value.",
            decision=VerificationComment.DECISION_NEEDS_CORRECTION,
            created_by=self.verifier,
        )
        final_encoder = User.objects.create_user("final_correction_encoder", password="test-pass")
        final_encoder.groups.add(Group.objects.create(name=ROLE_ADMIN))
        self.client.force_login(final_encoder)

        rows_response = self.client.get(
            reverse("final_batch_rows"),
            {"batch_id": self.batch.pk, "target": "final-test"},
        )

        self.assertContains(rows_response, "Needs correction")
        self.assertContains(rows_response, reverse("edit_final_data", args=[final_record.pk]))
        self.assertContains(rows_response, reverse("edit_final_data", args=[batch_peer.pk]))
        self.assertContains(rows_response, reverse("batch_edit_view", args=[self.batch.pk]))
        self.assertEqual(
            self.client.get(reverse("edit_final_data", args=[final_record.pk])).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(reverse("edit_final_data", args=[batch_peer.pk])).status_code,
            200,
        )

    def test_final_batch_not_endorsed_for_verification_is_editable(self):
        self.case.delete()
        final_record = Final_Data.objects.create(
            f_Batch_id=self.batch,
            f_AccessionNo="FINAL-NOT-ENDORSED-1",
        )
        final_encoder = User.objects.create_user("final_draft_encoder", password="test-pass")
        final_encoder.groups.add(Group.objects.create(name=ROLE_ADMIN))
        self.client.force_login(final_encoder)

        rows_response = self.client.get(
            reverse("final_batch_rows"),
            {"batch_id": self.batch.pk, "target": "final-test"},
        )

        self.assertContains(rows_response, reverse("edit_final_data", args=[final_record.pk]))
        self.assertEqual(
            self.client.get(reverse("edit_final_data", args=[final_record.pk])).status_code,
            200,
        )

    def test_verifier_endorses_final_review_to_lab_manager(self):
        self.client.force_login(self.verifier)

        self.client.post(
            self.url,
            {"action": "send_lab_manager", "lab_manager_staff": self.manager_staff.pk},
        )

        self.case.refresh_from_db()
        self.assertEqual(self.case.status, VerificationCase.STATUS_FOR_LAB_MANAGER)
        self.assertEqual(self.case.assigned_lab_manager, self.manager)

    def test_lab_manager_can_route_checked_final_data_to_final_printing(self):
        self.case.status = VerificationCase.STATUS_FOR_LAB_MANAGER
        self.case.assigned_lab_manager = self.manager
        self.case.save(update_fields=["status", "assigned_lab_manager", "updated_at"])
        self.client.force_login(self.manager)

        self.client.post(
            self.url,
            {"action": "send_final_printing", "head_staff": self.head_staff.pk},
        )

        self.case.refresh_from_db()
        self.assertEqual(self.case.status, VerificationCase.STATUS_FOR_FINAL_PRINTING)
        self.assertEqual(self.case.assigned_head, self.head)

    def test_lab_manager_cannot_skip_verifier_endorsement(self):
        self.client.force_login(self.manager)

        self.client.post(
            self.url,
            {"action": "send_final_printing", "head_staff": self.head_staff.pk},
        )

        self.case.refresh_from_db()
        self.assertEqual(self.case.status, VerificationCase.STATUS_FOR_FINAL_VERIFICATION)

    def test_head_endorsement_is_the_ready_for_release_transition(self):
        self.case.status = VerificationCase.STATUS_FOR_FINAL_PRINTING
        self.case.assigned_head = self.head
        self.case.save(update_fields=["status", "assigned_head", "updated_at"])
        self.client.force_login(self.head)

        self.client.post(self.url, {"action": "head_approve"})

        self.case.refresh_from_db()
        self.assertEqual(self.case.status, VerificationCase.STATUS_SIGNED_BY_HEAD)
        self.assertEqual(self.case.signed_by_head, self.head)


class FinalEndorsementPermissionTests(TestCase):
    def setUp(self):
        self.raw_encoder = User.objects.create_user("raw_encoder", password="test-pass")
        self.raw_encoder.groups.add(Group.objects.create(name=ROLE_ENCODER))
        self.final_encoder = User.objects.create_user("final_encoder", password="test-pass")
        self.final_encoder.groups.add(Group.objects.create(name=ROLE_CHECKER))
        self.verifier = User.objects.create_user("verifier", password="test-pass")
        self.verifier_staff = arsStaff_Details.objects.create(
            User_Account=self.verifier,
            Staff_Name="Verifier",
            Staff_Role=ROLE_VERIFIER,
        )
        self.batch = Batch_Table.objects.create(
            created_by=self.raw_encoder,
            bat_Batch_Name="FINAL-SEND-001",
            bat_Batch_Code="FINAL-SEND-001",
            bat_Status="First Draft",
        )
        Final_Data.objects.create(
            f_Batch_id=self.batch,
            f_AccessionNo="FINAL-SEND-001-1",
        )
        self.case = VerificationCase.objects.create(
            final_batch=self.batch,
            status=VerificationCase.STATUS_FOR_FINAL_ENCODING,
        )
        self.url = reverse("send_batch_to_verifier", args=[self.batch.pk])

    def test_raw_encoder_cannot_use_the_final_send_action(self):
        self.client.force_login(self.raw_encoder)

        self.client.post(self.url, {"verifier_staff": self.verifier_staff.pk})

        self.case.refresh_from_db()
        self.assertEqual(self.case.status, VerificationCase.STATUS_FOR_FINAL_ENCODING)

    def test_final_encoder_retains_the_final_send_action(self):
        self.client.force_login(self.final_encoder)

        self.client.post(self.url, {"verifier_staff": self.verifier_staff.pk})

        self.case.refresh_from_db()
        self.assertEqual(self.case.status, VerificationCase.STATUS_FOR_FINAL_VERIFICATION)
        self.assertEqual(self.case.assigned_checker, self.final_encoder)
        self.assertEqual(self.case.assigned_verifier, self.verifier)

    def test_unendorsed_final_batch_shows_send_and_can_start_verification(self):
        self.case.delete()
        self.client.force_login(self.final_encoder)

        response = self.client.get(reverse("show_final_table"))
        self.assertContains(response, "Endorse final encoding to verifier")

        self.client.post(self.url, {"verifier_staff": self.verifier_staff.pk})

        case = VerificationCase.objects.get(final_batch=self.batch)
        self.assertEqual(case.status, VerificationCase.STATUS_FOR_FINAL_VERIFICATION)
        self.assertEqual(case.assigned_checker, self.final_encoder)
        self.assertEqual(case.assigned_verifier, self.verifier)


class RawEditAfterCopyFlagTests(TestCase):
    def setUp(self):
        from apps.home_final.models import Final_Data
        self.encoder = User.objects.create_user("raw-change-encoder", password="test-pass")
        self.batch = Batch_Table.objects.create(
            created_by=self.encoder, bat_Batch_Name="RAW-AUDIT", bat_Batch_Code="RAW-AUDIT"
        )
        self.raw = Referred_Data.objects.create(
            Batch_id=self.batch, AccessionNo="RAW-AUDIT-001", Batch_Code="RAW-AUDIT"
        )
        self.final = Final_Data.objects.create(
            f_Batch_id=self.batch, f_AccessionNo="RAW-AUDIT-001",
            f_Batch_Code="RAW-AUDIT", f_Site_Name="Reviewed Final Site"
        )

    def test_raw_edit_flags_final_but_preserves_final_values_and_audits_editor(self):
        from django.contrib.admin.models import LogEntry
        from apps.home.views import _flag_final_copy_after_raw_edit
        changed = _flag_final_copy_after_raw_edit(
            self.raw, self.encoder, ["Site_Name"]
        )
        self.assertEqual(changed, 1)
        self.final.refresh_from_db()
        self.assertTrue(self.final.raw_updated_after_copy)
        self.assertEqual(self.final.raw_updated_after_copy_by, self.encoder)
        self.assertIsNotNone(self.final.raw_updated_after_copy_at)
        self.assertEqual(self.final.raw_updated_after_copy_fields, ["Site_Name"])
        self.assertEqual(self.final.f_Site_Name, "Reviewed Final Site")
        self.assertTrue(
            LogEntry.objects.filter(
                user=self.encoder, object_id=str(self.raw.pk),
                change_message__contains="raw_edit_after_final_copy",
            ).exists()
        )

    def test_original_final_still_flagged_when_raw_accession_changes(self):
        from apps.home.views import _flag_final_copy_after_raw_edit
        self.raw.AccessionNo = "RAW-AUDIT-002"
        self.raw.save(update_fields=["AccessionNo"])
        changed = _flag_final_copy_after_raw_edit(
            self.raw, self.encoder, ["AccessionNo"],
            previous_accession_no="RAW-AUDIT-001",
        )
        self.assertEqual(changed, 1)
        self.final.refresh_from_db()
        self.assertTrue(self.final.raw_updated_after_copy)
        self.assertEqual(self.final.f_AccessionNo, "RAW-AUDIT-001")

    def test_no_final_copy_produces_no_flag_or_audit(self):
        from django.contrib.admin.models import LogEntry
        from apps.home_final.models import Final_Data
        from apps.home.views import _flag_final_copy_after_raw_edit
        Final_Data.objects.filter(pk=self.final.pk).delete()
        changed = _flag_final_copy_after_raw_edit(
            self.raw, self.encoder, ["Spec_Type"]
        )
        self.assertEqual(changed, 0)
        self.assertFalse(LogEntry.objects.filter(object_id=str(self.raw.pk)).exists())
