
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
