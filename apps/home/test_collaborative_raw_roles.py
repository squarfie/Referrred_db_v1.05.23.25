"""Role policy regression tests: collaborative Raw work, protected privileged actions."""

from types import SimpleNamespace

from django.contrib.auth.models import Group, User
from django.test import RequestFactory, TestCase

from apps.home.permissions import (
    can_edit_raw_batch,
    can_manage_batch,
    can_view_batch,
    role_flags,
)
from apps.home.views import _raw_records_base_queryset
from apps.home_final.views import _final_records_base_queryset
from apps.home.models import Batch_Table, Referred_Data
from apps.home_final.models import Final_Data


class CollaborativeDMURoleTests(TestCase):
    def setUp(self):
        self.encoder = User.objects.create_user("collab-encoder")
        self.checker = User.objects.create_user("collab-checker")
        self.other = User.objects.create_user("collab-other")
        self.admin = User.objects.create_user("collab-admin")
        self.encoder.groups.add(Group.objects.get_or_create(name="DMU Encoder")[0])
        self.other.groups.add(Group.objects.get(name="DMU Encoder"))
        self.checker.groups.add(Group.objects.get_or_create(name="Checker")[0])
        self.admin.groups.add(Group.objects.get_or_create(name="Admin")[0])
        self.batch = Batch_Table.objects.create(
            bat_Batch_Code="COLLAB-A", bat_Batch_Name="COLLAB-A",
            created_by=self.encoder,
        )
        self.raw = Referred_Data.objects.create(
            AccessionNo="COLLAB-A-1", Batch_id=self.batch, Batch_Code="COLLAB-A",
        )
        self.final = Final_Data.objects.create(
            f_AccessionNo="COLLAB-A-1", f_Batch_id=self.batch,
            f_Batch_Code="COLLAB-A",
        )

    def test_encoder_and_checker_can_view_any_batch(self):
        for user in (self.encoder, self.other, self.checker, self.admin):
            with self.subTest(username=user.username):
                self.assertTrue(can_view_batch(user, self.batch))
                self.assertTrue(role_flags(user)["can_view_all"])

    def test_encoder_and_checker_can_edit_raw_batch_created_by_another_user(self):
        self.assertTrue(can_edit_raw_batch(self.other, self.batch))
        self.assertTrue(can_edit_raw_batch(self.checker, self.batch))
        self.assertTrue(can_edit_raw_batch(self.admin, self.batch))

    def test_cross_batch_raw_edit_does_not_grant_delete_permission(self):
        self.assertFalse(can_manage_batch(self.other, self.batch))
        self.assertTrue(can_manage_batch(self.encoder, self.batch))
        self.assertTrue(can_manage_batch(self.checker, self.batch))
        self.assertTrue(can_manage_batch(self.admin, self.batch))

    def test_encoder_cannot_manage_settings_but_checker_can(self):
        self.assertFalse(role_flags(self.encoder)["can_manage_settings"])
        self.assertTrue(role_flags(self.checker)["can_manage_settings"])
        self.assertTrue(role_flags(self.admin)["can_manage_settings"])

    def test_encoder_can_view_other_users_raw_and_final_records(self):
        request = RequestFactory().get("/")
        request.user = self.other
        self.assertTrue(_raw_records_base_queryset(request).filter(pk=self.raw.pk).exists())
        self.assertTrue(_final_records_base_queryset(request).filter(pk=self.final.pk).exists())
