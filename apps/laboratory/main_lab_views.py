"""Main Laboratory identification queue, separate from Bench A custody and processing."""
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from .models import LaboratoryAccession, LaboratoryIsolate
from .permissions import lab_worklist_required, can_manage_id_testing


MAIN_LAB_STAGES = (
    LaboratoryIsolate.Status.ID_PENDING,
    LaboratoryIsolate.Status.ID_TESTING,
    LaboratoryIsolate.Status.ID_RETURNED,
)


@login_required
@lab_worklist_required
def main_lab_dashboard(request):
    """Isolate-level Main Lab metrics: never count Bench A revival as ID testing."""
    search = (request.GET.get("q") or "").strip()
    tab = request.GET.get("tab", "awaiting")
    allowed = {"awaiting", "testing", "returned", "all"}
    if tab not in allowed:
        tab = "awaiting"

    base = LaboratoryIsolate.objects.filter(
        endorsed_at__isnull=False,
    ).select_related("accession_range__laboratory_receiving__receipt__site")
    counts = {
        "awaiting": base.filter(status=LaboratoryIsolate.Status.ID_PENDING).count(),
        "testing": base.filter(status=LaboratoryIsolate.Status.ID_TESTING).count(),
        "returned": base.filter(returned_at__isnull=False).count(),
        "all": base.count(),
    }
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
        "query": search, "can_test": can_manage_id_testing(request.user),
        "area": "main_lab",
    })


@login_required
@lab_worklist_required
def main_lab_isolate_detail(request, isolate_id):
    """ID work only: no revival, Site ID banking, final review or lyophilization controls."""
    isolate = get_object_or_404(
        LaboratoryIsolate.objects.select_related(
            "accession_range__laboratory_receiving__receipt__site"
        ).prefetch_related("identifications__recorded_by"),
        pk=isolate_id, endorsed_at__isnull=False,
    )
    return render(request, "laboratory/main_lab_isolate.html", {
        "isolate": isolate,
        "can_test": can_manage_id_testing(request.user),
        "area": "main_lab",
    })
