"""Main Laboratory identification queue, separate from Bench A custody and processing."""
from django.contrib.auth.decorators import login_required
from django.db.models import Max, Prefetch, Q
from django.urls import reverse
from urllib.parse import urlencode
from django.shortcuts import get_object_or_404, render

from .identification_workflow import final_banking_filter
from .navigation import return_url
from .main_lab_progress import main_lab_progress
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
    tab = request.GET.get("tab", "for_testing")
    # Existing bookmarks for the two older arrival queues open their
    # combined replacement rather than hiding isolates.
    if tab in ("awaiting", "temporary"):
        tab = "for_testing"
    if tab == "returned":
        tab = "completed"
    if tab not in {"for_testing", "testing", "completed", "all"}:
        tab = "for_testing"

    base = main_lab_cases()
    completed = Q(id_completed_at__isnull=False) | Q(returned_at__isnull=False)
    # A temporary Site ID record must have real storage and documented
    # revival before Main Lab may retrieve it for testing.
    temporary = LaboratoryIsolate.objects.filter(
        status="site_banked", identification_source="site",
        site_banked_at__isnull=False, biobank__isnull=False,
        revival_started_at__isnull=False, revival_completed_at__isnull=False,
        retrieved_at__isnull=True,
    )
    # Combine case IDs through a single queryset. Django cannot OR a
    # distinct() Main Lab queryset with a non-distinct() storage queryset.
    # Using PK subqueries also avoids duplicate rows from ID result joins.
    for_testing = LaboratoryIsolate.objects.filter(
        Q(pk__in=base.filter(status=LaboratoryIsolate.Status.ID_PENDING).values("pk"))
        | Q(pk__in=temporary.values("pk"))
    )
    all_cases = LaboratoryIsolate.objects.filter(
        Q(pk__in=base.values("pk")) | Q(pk__in=temporary.values("pk"))
    )
    counts = {
        "awaiting": base.filter(status=LaboratoryIsolate.Status.ID_PENDING).count(),
        "testing": base.filter(status=LaboratoryIsolate.Status.ID_TESTING).count(),
        "completed": base.filter(completed).count(),
        "for_testing": for_testing.count(),
        "all": all_cases.count(),
    }
    tab_labels = (
        ("for_testing", "For Testing"),
        ("testing", "Testing in progress"),
        ("completed", "Completed"),
        ("all", "All Main Lab cases"),
    )
    tabs = [{"key": key, "label": label, "count": counts[key]} for key, label in tab_labels]
    isolates = all_cases
    if tab == "for_testing":
        isolates = for_testing
    elif tab == "testing":
        isolates = base.filter(status=LaboratoryIsolate.Status.ID_TESTING)
    elif tab == "completed":
        isolates = base.filter(completed)
    isolates = isolates.select_related("accession_range__laboratory_receiving__receipt__site")
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
    accessible_isolates = (
        main_lab_cases() | LaboratoryIsolate.objects.filter(
            status="site_banked", identification_source="site", site_banked_at__isnull=False,
            biobank__isnull=False, revival_started_at__isnull=False,
            revival_completed_at__isnull=False,
        ).distinct()
    ).distinct()
    isolate = get_object_or_404(
        accessible_isolates.select_related(
            "accession_range__laboratory_receiving__receipt__site"
        ).prefetch_related("identifications__recorded_by"),
        pk=isolate_id,
    )
    # Navigate only among isolates that can open in Main Lab; otherwise links
    # could lead to a 404 for Bench A-only isolates in the accession batch.
    sibling_ids = list(accessible_isolates.filter(
        accession_range_id=isolate.accession_range_id,
    ).order_by("accession_number", "pk").values_list("pk", flat=True))
    position = sibling_ids.index(isolate.pk)
    origin = return_url(request)
    suffix = "?" + urlencode({"next": origin}) if origin else ""
    previous_isolate_url = (
        reverse("main_lab_isolate_detail", args=[sibling_ids[position - 1]]) + suffix
        if position > 0 else None
    )
    next_isolate_url = (
        reverse("main_lab_isolate_detail", args=[sibling_ids[position + 1]]) + suffix
        if position + 1 < len(sibling_ids) else None
    )
    return render(request, "laboratory/main_lab_isolate.html", {
        "isolate": isolate, "return_to": origin,
        "previous_isolate_url": previous_isolate_url,
        "next_isolate_url": next_isolate_url,
        "main_lab_progress": main_lab_progress(isolate),
        "latest_result": isolate.identifications.first(), "main_lab_back_url": reverse("main_lab_dashboard"),
        "can_test": can_access_main_lab(request.user),
        "area": "main_lab", "origin_url": request.get_full_path(),
    })
