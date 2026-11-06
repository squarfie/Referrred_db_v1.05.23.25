
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


class AntibioticDeletionSecurityTests(TestCase):
    """Direct HTTP deletion must respect parent accession permissions."""

    def setUp(self):
        from apps.home.models import AntibioticEntry
        from apps.home_final.models import Final_AntibioticEntry

        self.owner = User.objects.create_user("abx-delete-owner", password="test")
        self.other = User.objects.create_user("abx-delete-other", password="test")
        self.viewer = User.objects.create_user("abx-delete-viewer", password="test")
        self.admin = User.objects.create_user("abx-delete-admin", password="test")
        encoder_group, _ = Group.objects.get_or_create(name="DMU Encoder")
        self.owner.groups.add(encoder_group)
        self.other.groups.add(encoder_group)
        self.admin.groups.add(Group.objects.get_or_create(name="Admin")[0])
        self.batch = Batch_Table.objects.create(
            bat_Batch_Code="ABX-DELETE", bat_Batch_Name="Antibiotic delete",
            created_by=self.owner,
        )
        self.raw = Referred_Data.objects.create(
            AccessionNo="ABX-DELETE-1", Batch_id=self.batch,
            Batch_Code="ABX-DELETE",
        )
        self.final = Final_Data.objects.create(
            f_AccessionNo="ABX-DELETE-1", f_Batch_id=self.batch,
            f_Batch_Code="ABX-DELETE",
        )
        self.raw_entries = [
            AntibioticEntry.objects.create(
                ab_idNum_referred=self.raw, ab_Abx_code=code,
            )
            for code in ("AMP", "GEN")
        ]
        self.final_entries = [
            Final_AntibioticEntry.objects.create(
                ab_idNum_f_referred=self.final, ab_Abx_code=code,
            )
            for code in ("AMP", "GEN")
        ]

    def _attempt(self, *, final, user, method="post"):
        from django.urls import reverse
        name = "delete_final_antibiotic" if final else "delete_raw_antibiotic"
        entry = self.final_entries[0] if final else self.raw_entries[0]
        if user is None:
            self.client.logout()
        else:
            self.client.force_login(user)
        url = reverse(name, args=[entry.pk])
        response = getattr(self.client, method)(url)
        return response

    def _assert_entry_count(self, *, final, count):
        from apps.home.models import AntibioticEntry
        from apps.home_final.models import Final_AntibioticEntry
        qs = (
            Final_AntibioticEntry.objects.filter(ab_idNum_f_referred=self.final)
            if final else AntibioticEntry.objects.filter(ab_idNum_referred=self.raw)
        )
        self.assertEqual(qs.count(), count)

    def test_anonymous_viewer_and_other_encoder_cannot_delete(self):
        for final in (False, True):
            for user in (None, self.viewer, self.other):
                with self.subTest(final=final, user=getattr(user, "username", "anonymous")):
                    response = self._attempt(final=final, user=user)
                    self.assertIn(response.status_code, (302, 403))
                    self._assert_entry_count(final=final, count=2)

    def test_get_cannot_delete(self):
        for final in (False, True):
            with self.subTest(final=final):
                response = self._attempt(final=final, user=self.owner, method="get")
                self.assertEqual(response.status_code, 405)
                self._assert_entry_count(final=final, count=2)

    def test_active_verification_blocks_both(self):
        VerificationCase.objects.create(
            final_batch=self.batch,
            status=VerificationCase.STATUS_FOR_RAW_VERIFICATION,
        )
        for final in (False, True):
            self._attempt(final=final, user=self.owner)
            self._assert_entry_count(final=final, count=2)

    def test_signed_and_released_block_non_admin(self):
        for status in (VerificationCase.STATUS_SIGNED_BY_HEAD, VerificationCase.STATUS_RELEASED):
            case = VerificationCase.objects.create(final_batch=self.batch, status=status)
            for final in (False, True):
                with self.subTest(status=status, final=final):
                    self._attempt(final=final, user=self.owner)
                    self._assert_entry_count(final=final, count=2)
            case.delete()

    def test_admin_can_delete_signed_or_released_results(self):
        for final, status in (
            (False, VerificationCase.STATUS_SIGNED_BY_HEAD),
            (True, VerificationCase.STATUS_RELEASED),
        ):
            with self.subTest(final=final, status=status):
                case = VerificationCase.objects.create(final_batch=self.batch, status=status)
                self._attempt(final=final, user=self.admin)
                self._assert_entry_count(final=final, count=0)
                case.delete()
                # Restore the deleted results for the other iteration.
                if not final:
                    from apps.home.models import AntibioticEntry
                    self.raw_entries = [
                        AntibioticEntry.objects.create(ab_idNum_referred=self.raw, ab_Abx_code=code)
                        for code in ("AMP", "GEN")
                    ]

    def test_final_encoding_allows_final_but_not_raw(self):
        VerificationCase.objects.create(
            final_batch=self.batch,
            status=VerificationCase.STATUS_FOR_FINAL_ENCODING,
        )
        self._attempt(final=False, user=self.owner)
        self._assert_entry_count(final=False, count=2)
        self._attempt(final=True, user=self.owner)
        self._assert_entry_count(final=True, count=0)

    def test_owner_can_delete_draft_results(self):
        for final in (False, True):
            with self.subTest(final=final):
                self._attempt(final=final, user=self.owner)
                self._assert_entry_count(final=final, count=0)

    def test_unbatched_encoder_is_blocked(self):
        self.raw.Batch_id = None
        self.raw.save(update_fields=["Batch_id"])
        self.final.f_Batch_id = None
        self.final.save(update_fields=["f_Batch_id"])
        for final in (False, True):
            with self.subTest(final=final):
                self._attempt(final=final, user=self.other)
                self._assert_entry_count(final=final, count=2)
