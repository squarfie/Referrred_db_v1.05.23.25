"""Laboratory navigation regressions that do not require a database."""
from types import SimpleNamespace
from unittest.mock import patch

from django.template.loader import get_template, render_to_string
from django.test import RequestFactory, SimpleTestCase
from django.urls import reverse

from apps.home.permissions import (
    ROLE_ADMIN, ROLE_LAB_ENCODER, ROLE_LAB_STAFF, ROLE_SECRETARIAT, role_flags,
)


class LaboratoryNavigationUXTests(SimpleTestCase):
    areas = {
        "receiving": "Bench A / Laboratory Receiving",
        "main_lab": "Main Laboratory",
        "biobank": "Biobank Inventory",
    }

    def render_sidebar(self, role, area="", url_name="home"):
        user = SimpleNamespace(is_authenticated=True)
        with patch("apps.home.permissions.get_user_roles", return_value={role}):
            access = role_flags(user)
        request = RequestFactory().get("/")
        request.user = user
        request.resolver_match = SimpleNamespace(url_name=url_name)
        return render_to_string("includes/sidenav.html", {
            "request": request, "user_access": access, "area": area,
        })

    def test_role_oriented_laboratory_areas(self):
        cases = {
            ROLE_ADMIN: {"receiving", "main_lab", "biobank"},
            ROLE_LAB_ENCODER: {"receiving", "biobank"},
            ROLE_LAB_STAFF: {"main_lab", "biobank"},
            ROLE_SECRETARIAT: set(),
        }
        for role, visible in cases.items():
            with self.subTest(role=role):
                rendered = self.render_sidebar(role)
                for area, label in self.areas.items():
                    if area in visible:
                        self.assertIn(label, rendered)
                    else:
                        self.assertNotIn(label, rendered)
                self.assertNotIn(">Identification Testing</span>", rendered)
                self.assertNotIn(">Revival &amp; Processing</span>", rendered)
                if role == ROLE_LAB_STAFF:
                    self.assertNotIn("Bench A / Laboratory Receiving", rendered)

    def test_stage_pages_keep_single_laboratory_navigation_active(self):
        for route in ("laboratory_worklist", "laboratory_processing_worklist",
                      "laboratory_batch_detail", "laboratory_isolate_detail"):
            with self.subTest(route=route):
                rendered = self.render_sidebar(ROLE_ADMIN, "processing", route)
                self.assertIn("Bench A / Laboratory Receiving", rendered)
                self.assertRegex(
                    rendered,
                    r'class="nav-link active" href="' + reverse("laboratory_receiving_dashboard") + '"',
                )

    def test_laboratory_templates_compile(self):
        for template in (
            "includes/sidenav.html", "laboratory/dashboard.html",
            "laboratory/laboratory_workspace.html", "laboratory/lab_worklist.html",
            "laboratory/lab_batch_detail.html", "laboratory/lab_biobank_list.html",
            "laboratory/main_lab_dashboard.html", "laboratory/main_lab_isolate.html",
        ):
            with self.subTest(template=template):
                get_template(template)
