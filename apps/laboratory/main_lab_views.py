"""Main Laboratory identification queue, separate from Bench A custody and processing."""
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, render

from .models import LaboratoryIsolate
from .permissions import main_lab_required, can_access_main_lab


MAIN_LAB_STAGES = (
    LaboratoryIsolate.Status.ID_PENDING,
    LaboratoryIsolate.Status.ID_TESTING,
    LaboratoryIsolate.Status.ID_RETURNED,
)


def main_lab_cases():
    """Retain historical ID work without inventing missing endorsement dates."""
    return LaboratoryIsolate.objects.filter(
        Q(endorsed_at__isnull=False) |
        (Q(status__in=('id_pending', 'id_testing', 'id_returned', 'id_reviewed',
                       'ready_for_biobank', 'biobanked')) &
         (Q(acknowledged_at__isnull=False) | Q(returned_at__isnull=False) |
          Q(identifications__isnull=False)))
    ).distinct()


@login_required
@main_lab_required
def main_lab_dashboard(request):
    """Isolate-level Main Lab metrics: never count Bench A revival as ID testing."""
    search = (request.GET.get("q") or "").strip()
    tab = request.GET.get("tab", "awaiting")
    allowed = {"awaiting", "testing", "returned", "all"}
    if tab not in allowed:
        tab = "awaiting"

    base = main_lab_cases().select_related("accession_range__laboratory_receiving__receipt__site")
    counts = {
        "awaiting": base.filter(status=LaboratoryIsolate.Status.ID_PENDING).count(),
        "testing": base.filter(status=LaboratoryIsolate.Status.ID_TESTING).count(),
        "returned": base.filter(returned_at__isnull=False).count(),
        "all": base.count(),
    }
    tab_labels = (("awaiting", "Awaiting acknowledgment"), ("testing", "Testing in progress"),
                  ("returned", "Returned to Bench A"), ("all", "All endorsed"))
    tabs = [{"key": key, "label": label, "count": counts[key]} for key, label in tab_labels]
    isolates = base
    if tab == "awaiting":
        isolates = isolates.filter(status=LaboratoryIsolate.Status.ID_PENDING)
    elif tab == "testing":
        isolates = isolates.filter(status=LaboratoryIsolate.Status.ID_TESTING)
    elif tab == "returned":
        isolates = isolates.filter(returned_at__isnull=False)
    if search:
        isolates = isolates.filter(
            Q(accession_number__icontains=search) |
            Q(accession_range__laboratory_receiving__receipt__tracking_number__icontains=search) |
            Q(accession_range__laboratory_receiving__receipt__site__SiteCode__icontains=search)
        )
    from django.core.paginator import Paginator
    page = Paginator(isolates.order_by("-endorsed_at", "accession_number"), 30).get_page(
        request.GET.get("page")
    )
    return render(request, "laboratory/main_lab_dashboard.html", {
        "page_obj": page, "counts": counts, "selected_tab": tab,
        "tab_labels": tab_labels, "main_lab_tabs": tabs,
        "query": search, "can_test": can_access_main_lab(request.user),
        "area": "main_lab",
    })


@login_required
@main_lab_required
def main_lab_isolate_detail(request, isolate_id):
    """ID work only: no revival, Site ID banking, final review or lyophilization controls."""
    isolate = get_object_or_404(
        main_lab_cases().select_related(
            "accession_range__laboratory_receiving__receipt__site"
        ).prefetch_related("identifications__recorded_by"),
        pk=isolate_id,
    )
    return render(request, "laboratory/main_lab_isolate.html", {
        "isolate": isolate,
        "can_test": can_access_main_lab(request.user),
        "area": "main_lab",
    })
