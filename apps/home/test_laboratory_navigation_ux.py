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
        "receiving": "Receiving &amp; Accessioning",
        "identification": "Identification Testing",
        "processing": "Revival &amp; Processing",
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
            ROLE_ADMIN: set(self.areas),
            ROLE_LAB_ENCODER: {"receiving", "identification", "processing", "biobank"},
            ROLE_LAB_STAFF: {"identification", "biobank"},
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

    def test_batch_and_isolate_pages_keep_area_active(self):
        for area, route in (
            ("identification", "laboratory_worklist"),
            ("processing", "laboratory_processing_worklist"),
            ("biobank", "laboratory_biobank_list"),
        ):
            with self.subTest(area=area):
                rendered = self.render_sidebar(ROLE_ADMIN, area, "laboratory_isolate_detail")
                self.assertRegex(rendered, r'class="nav-link active" href="' + reverse(route) + '"')

    def test_laboratory_templates_compile(self):
        for template in (
            "includes/sidenav.html", "laboratory/dashboard.html",
            "laboratory/laboratory_workspace.html", "laboratory/lab_worklist.html",
            "laboratory/lab_batch_detail.html", "laboratory/lab_biobank_list.html",
        ):
            with self.subTest(template=template):
                get_template(template)
