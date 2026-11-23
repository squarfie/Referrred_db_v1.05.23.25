"""Main Laboratory identification queue, separate from Bench A custody and processing."""
from django.contrib.auth.decorators import login_required
from django.db.models import Max, Prefetch, Q
from django.urls import reverse
from django.shortcuts import get_object_or_404, render

from .identification_workflow import final_banking_filter
from .navigation import return_url
from .models import LaboratoryAccession, LaboratoryIsolate
from .permissions import main_lab_required, can_access_main_lab


MAIN_LAB_STAGES = (
    LaboratoryIsolate.Status.ID_PENDING,
    LaboratoryIsolate.Status.ID_TESTING,
    LaboratoryIsolate.Status.ID_RETURNED,
)


def main_lab_cases():
    """Retain historical ID work without inventing missing endorsement dates."""
    return LaboratoryIsolate.objects.filter(
        Q(endorsed_at__isnull=False) | Q(retrieved_at__isnull=False) | Q(id_completed_at__isnull=False) |
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
    allowed = {"awaiting", "testing", "completed", "returned", "temporary", "all"}
    if tab not in allowed:
        tab = "awaiting"

    if tab == "returned":
        tab = "completed"
    base = main_lab_cases().select_related("accession_range__laboratory_receiving__receipt__site")
    completed = Q(id_completed_at__isnull=False) | Q(returned_at__isnull=False)
    # Only physically stored, successfully revived isolates are retrievable.
    # A provisional label alone is not proof of an available storage position.
    temporary = LaboratoryIsolate.objects.filter(
        status="site_banked", identification_source="site",
        site_banked_at__isnull=False, biobank__isnull=False,
        revival_started_at__isnull=False, revival_completed_at__isnull=False,
    )
    counts = {
        "awaiting": base.filter(status=LaboratoryIsolate.Status.ID_PENDING).count(),
        "testing": base.filter(status=LaboratoryIsolate.Status.ID_TESTING).count(),
        "returned": base.filter(completed).count(),
        "completed": base.filter(completed).count(),
        "temporary": temporary.count(),
        "all": base.count(),
    }
    tab_labels = (("awaiting", "Awaiting acknowledgment"), ("testing", "Testing in progress"),
                  ("completed", "Completed"), ("temporary", "Temporary storage / retrieve"), ("all", "All Main Lab cases"))
    tabs = [{"key": key, "label": label, "count": counts[key]} for key, label in tab_labels]
    isolates = base
    if tab == "awaiting":
        isolates = isolates.filter(status=LaboratoryIsolate.Status.ID_PENDING)
    elif tab == "testing":
        isolates = isolates.filter(status=LaboratoryIsolate.Status.ID_TESTING)
    elif tab == "completed":
        isolates = isolates.filter(completed)
    elif tab == "temporary":
        isolates = temporary.select_related("accession_range__laboratory_receiving__receipt__site")
    if search:
        isolates = isolates.filter(
            Q(accession_number__icontains=search) |
            Q(accession_range__laboratory_receiving__receipt__tracking_number__icontains=search) |
            Q(accession_range__laboratory_receiving__receipt__site__SiteCode__icontains=search)
        )
    from django.core.paginator import Paginator
    batches = LaboratoryAccession.objects.filter(
        pk__in=isolates.values("accession_range_id")
    ).select_related("laboratory_receiving__receipt__site").annotate(
        latest_endorsement=Max("isolate_records__endorsed_at")
    ).order_by("-latest_endorsement", "-date_referred", "-pk")
    page = Paginator(batches, 25).get_page(
        request.GET.get("page")
    )
    page.object_list = page.object_list.prefetch_related(Prefetch(
        "isolate_records", queryset=isolates.order_by("accession_number", "pk"),
        to_attr="main_lab_isolates",
    ))
    return render(request, "laboratory/main_lab_dashboard.html", {
        "page_obj": page, "main_lab_batches": page.object_list,
        "counts": counts, "selected_tab": tab,
        "tab_labels": tab_labels, "main_lab_tabs": tabs,
        "query": search, "can_test": can_access_main_lab(request.user),
        "area": "main_lab", "origin_url": request.get_full_path(),
    })


@login_required
@main_lab_required
def main_lab_isolate_detail(request, isolate_id):
    """ID work only: no revival, Site ID banking, final review or lyophilization controls."""
    isolate = get_object_or_404(
        (main_lab_cases() | LaboratoryIsolate.objects.filter(
            status="site_banked", identification_source="site", site_banked_at__isnull=False,
            biobank__isnull=False, revival_started_at__isnull=False,
            revival_completed_at__isnull=False,
        ).distinct()).select_related(
            "accession_range__laboratory_receiving__receipt__site"
        ).prefetch_related("identifications__recorded_by"),
        pk=isolate_id,
    )
    return render(request, "laboratory/main_lab_isolate.html", {
        "isolate": isolate, "return_to": return_url(request),
        "latest_result": isolate.identifications.first(), "main_lab_back_url": reverse("main_lab_dashboard"),
        "can_test": can_access_main_lab(request.user),
        "area": "main_lab", "origin_url": request.get_full_path(),
    })
