# -*- encoding: utf-8 -*-
"""
Copyright (c) 2019 - present AppSeed.us
"""

from django.contrib.auth.models import User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase

from apps.home.models import BreakpointsTable
from apps.home.views import delete_alert_only_breakpoints


class DeleteAlertOnlyBreakpointsTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username="settings-user", password="test")

    def _post(self):
        request = self.factory.post("/breakpoints-delete-alert-only/")
        request.user = self.user
        SessionMiddleware(lambda req: None).process_request(request)
        request.session.save()
        setattr(request, "_messages", FallbackStorage(request))
        return delete_alert_only_breakpoints(request)

    def test_deletes_only_rows_with_alert_value_and_no_breakpoint_values(self):
        alert_only = BreakpointsTable.objects.create(
            Abx_code="POS",
            Whonet_Abx="POS_NM",
            Alert_val="0.25",
        )
        alert_with_breakpoint = BreakpointsTable.objects.create(
            Abx_code="SXT",
            Whonet_Abx="SXT_ND1.25",
            Alert_val="20.0",
            R_val="10",
        )
        no_alert = BreakpointsTable.objects.create(
            Abx_code="AMP",
            Whonet_Abx="AMP",
        )

        response = self._post()

        self.assertEqual(response.status_code, 302)
        self.assertFalse(BreakpointsTable.objects.filter(pk=alert_only.pk).exists())
        self.assertTrue(BreakpointsTable.objects.filter(pk=alert_with_breakpoint.pk).exists())
        self.assertTrue(BreakpointsTable.objects.filter(pk=no_alert.pk).exists())
