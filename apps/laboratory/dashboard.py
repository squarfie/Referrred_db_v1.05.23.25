"""Laboratory receipt workflow tabs, counters and dashboard presentation."""
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import render
from django.utils import timezone
from .models import ReferralReceipt
from apps.receiving.permissions import can_manage_secretariat_receiving
from .permissions import can_confirm_lab_receipt, can_read_laboratory_receiving
from apps.receiving.services import can_delete_receipt_safely
from .batch_status import pending_batch_accessions

def render_laboratory_dashboard(request, queue=None):
    all_receipts = ReferralReceipt.objects.select_related("site", "received_by")
    current_year = timezone.localdate().year
    year_param = (request.GET.get("year") or "").strip()
    available_years = sorted(
        {value.year for value in ReferralReceipt.objects.dates("date_received", "year")}
        | {current_year},
        reverse=True,
    )
    selected_year = current_year
    if year_param == "all":
        selected_year = "all"
    elif year_param.isascii() and year_param.isdecimal():
        requested_year = int(year_param)
        if requested_year in available_years:
            selected_year = requested_year
    if selected_year != "all":
        all_receipts = all_receipts.filter(date_received__year=selected_year)

    per_page_raw = (request.GET.get("per_page") or "").strip()
    allowed_page_sizes = (10, 25, 50)
    per_page = int(per_page_raw) if per_page_raw in ("10", "25", "50") else 25
    counts = {
        row["status"]: row["total"]
        for row in all_receipts.values("status").annotate(total=Count("id"))
    }

    can_manage_secretariat = can_manage_secretariat_receiving(request.user)
    can_confirm_lab = can_confirm_lab_receipt(request.user)
    # Prefer the laboratory queue for users who have both responsibilities.
    is_lab_worklist = (queue == "laboratory") if queue else can_read_laboratory_receiving(request.user)
    queue_url_name = (
        "laboratory_receiving_dashboard" if queue == "laboratory"
        else "secretariat_receiving_dashboard" if queue == "secretariat"
        else "receiving_dashboard"
    )
    if is_lab_worklist:
        from apps.laboratory.receiving_views import _laboratory_workflow_receipts
        # Only acknowledged physical receipts become Bench A work. Pending
        # acknowledgment remains exclusively in centralized Receiving.
        all_receipts = _laboratory_workflow_receipts(
            all_receipts.filter(status=ReferralReceipt.Status.LAB_RECEIVED)
        )
        tab_definitions = [
            ("action", "Needs Action", "action"),
            ("main_lab", "Awaiting Main Lab", "main_lab"),
            ("complete", "Completed", "complete"),
            ("all", "All Records", None),
        ]
    else:
        tab_definitions = [
            ("pending", "Pending Endorsement", ReferralReceipt.Status.RECEIVED),
            ("endorsed", "Endorsed", ReferralReceipt.Status.ENDORSED),
            ("received", "Lab Received", ReferralReceipt.Status.LAB_RECEIVED),
            ("all", "All Records", None),
        ]

    tab_status = {key: value for key, _label, value in tab_definitions}
    explicit_tab = (request.GET.get("tab") or "").strip()
    legacy_status = (request.GET.get("status") or "").strip()
    valid_statuses = {value for value, _label in ReferralReceipt.Status.choices}

    if is_lab_worklist:
        # Existing bookmarks remain usable without restoring seven visible tabs.
        legacy_tabs = {
            "pending": ("action", "accessioning"),
            "received": ("action", "accessioning"),
            "id_complete": ("action", "lyophilization"),
            "processing": ("action", "processing"),
            "pending_batch": ("action", "batch_creation"),
        }
        selected_tab, legacy_filter = legacy_tabs.get(explicit_tab, (explicit_tab, ""))
        selected_tab = selected_tab if selected_tab in tab_status else "action"
        if not explicit_tab and legacy_status == ReferralReceipt.Status.ENDORSED:
            selected_tab, legacy_filter = "action", "accessioning"
        elif not explicit_tab and legacy_status == ReferralReceipt.Status.LAB_RECEIVED:
            selected_tab, legacy_filter = "all", ""
        stage = (request.GET.get("stage") or legacy_filter).strip()
        valid_stages = {"accessioning", "processing", "lyophilization", "batch_creation"}
        stage = stage if selected_tab == "action" and stage in valid_stages else ""
    else:
        selected_tab = explicit_tab or "pending"
        if not explicit_tab and legacy_status in valid_statuses:
            selected_tab = next(
                (key for key, _label, value in tab_definitions if value == legacy_status),
                "all",
            )
        if selected_tab not in tab_status:
            selected_tab = "pending"
        stage = ""

    query = (request.GET.get("q") or "").strip()
    receipts = all_receipts
    if query:
        receipts = receipts.filter(
            Q(tracking_number__icontains=query)
            | Q(site__SiteCode__icontains=query)
            | Q(site__SiteName__icontains=query)
        )

    if is_lab_worklist:
        action_filter = Q(
            status=ReferralReceipt.Status.LAB_RECEIVED,
            lab_workflow__in=("received", "processing", "id_complete"),
        )
        main_lab_filter = Q(status=ReferralReceipt.Status.LAB_RECEIVED, lab_workflow="main_lab")
        completed_filter = Q(status=ReferralReceipt.Status.LAB_RECEIVED, lab_workflow="complete")
        pending_batch_filter = Q(
            status=ReferralReceipt.Status.LAB_RECEIVED,
            pk__in=pending_batch_accessions().values("laboratory_receiving__receipt_id"),
        )
        tab_filters = {
            "action": action_filter,
            "main_lab": main_lab_filter,
            "complete": completed_filter,
        }
        stage_filters = {
            "accessioning": Q(status=ReferralReceipt.Status.LAB_RECEIVED, lab_workflow="received"),
            "processing": Q(status=ReferralReceipt.Status.LAB_RECEIVED, lab_workflow="processing"),
            "lyophilization": Q(status=ReferralReceipt.Status.LAB_RECEIVED, lab_workflow="id_complete"),
            # Raw Data batch creation is an independent administrative task,
            # so it may overlap any scientific workflow stage.
            "batch_creation": pending_batch_filter,
        }
        # Count the full queue, not the user's search results or the current
        # page. Each task badge uses the same predicate as its task filter.
        stage_counts = {
            key: all_receipts.filter(task_filter).distinct().count()
            for key, task_filter in stage_filters.items()
        }
        pending_batch_count = stage_counts["batch_creation"]
        lab_counts = {
            "action": all_receipts.filter(action_filter).count(),
            "main_lab": all_receipts.filter(main_lab_filter).count(),
            "complete": all_receipts.filter(completed_filter).count(),
        }
        lab_summary_cards = [
            {"key": "pending_receipt", "label": "Pending Accessioning",
             "count": stage_counts["accessioning"],
             "tab": "action", "stage": "accessioning"},
            {"key": "action", "label": "Needs Action", "count": lab_counts["action"],
             "tab": "action", "stage": ""},
            {"key": "main_lab", "label": "With Main Lab", "count": lab_counts["main_lab"],
             "tab": "main_lab", "stage": ""},
            {"key": "complete", "label": "Completed", "count": lab_counts["complete"],
             "tab": "complete", "stage": ""},
        ]
        if selected_tab in tab_filters:
            receipts = receipts.filter(tab_filters[selected_tab])
        if selected_tab == "action" and stage:
            # Batch creation is intentionally independent of the primary
            # workflow tab, including scientifically completed receipts.
            if stage == "batch_creation":
                receipts = all_receipts.filter(pending_batch_filter)
                if query:
                    receipts = receipts.filter(
                        Q(tracking_number__icontains=query)
                        | Q(site__SiteCode__icontains=query)
                        | Q(site__SiteName__icontains=query)
                    )
            else:
                receipts = receipts.filter(stage_filters[stage])
    else:
        pending_batch_count = 0
        stage_counts = {}
        lab_summary_cards = []
        selected_status = tab_status[selected_tab]
        if not explicit_tab and legacy_status in valid_statuses:
            receipts = receipts.filter(status=legacy_status)
        elif selected_status is not None:
            receipts = receipts.filter(status=selected_status)

    tabs = [
        {
            "key": key,
            "label": label,
            "count": (
                lab_counts[key] if is_lab_worklist and key in lab_counts
                else all_receipts.count() if is_lab_worklist
                else sum(counts.values()) if tab_filter is None
                else counts.get(tab_filter, 0)
            ),
            "active": key == selected_tab,
        }
        for key, label, tab_filter in tab_definitions
    ]
    stage_labels = {
        "accessioning": "Accessioning",
        "processing": "Processing",
        "lyophilization": "Lyophilization",
        "batch_creation": "Batch Creation",
    }
    page_obj = Paginator(
        receipts.order_by("-created_at", "-pk"),
        per_page,
    ).get_page(request.GET.get("page"))
    for receipt in page_obj:
        receipt.can_delete_from_queue = (
            not is_lab_worklist and can_delete_receipt_safely(receipt, request.user)
        )
    return render(
        request,
        "laboratory/dashboard.html" if is_lab_worklist else "receiving/dashboard.html",
        {
            "page_obj": page_obj,
            "q": query, "origin_url": request.get_full_path(),
            "active_tab": selected_tab,
            "active_tab_label": (
                stage_labels[stage] if is_lab_worklist and stage
                else next(tab["label"] for tab in tabs if tab["active"])
            ),
            "receiving_tabs": tabs,
            "lab_summary_cards": lab_summary_cards,
            "pending_batch_count": pending_batch_count,
            "active_stage": stage,
            "stage_filters": [
                {"key": key, "label": label, "count": stage_counts[key]}
                for key, label in (
                    ("accessioning", "Accessioning"),
                    ("processing", "Processing"),
                    ("lyophilization", "Lyophilization"),
                )
            ] if is_lab_worklist else [],
            "received_count": counts.get(ReferralReceipt.Status.RECEIVED, 0),
            "endorsed_count": counts.get(ReferralReceipt.Status.ENDORSED, 0),
            "lab_received_count": counts.get(ReferralReceipt.Status.LAB_RECEIVED, 0),
            "can_manage_secretariat": can_manage_secretariat and not is_lab_worklist,
            "can_confirm_lab": can_confirm_lab and is_lab_worklist,
            "can_read_lab": can_read_laboratory_receiving(request.user) and is_lab_worklist,
            "receiving_queue_url": queue_url_name,
            "is_lab_worklist": is_lab_worklist,
            "show_main_lab_returns": False,
            "selected_year": selected_year,
            "available_years": available_years,
            "per_page": per_page,
            "allowed_page_sizes": allowed_page_sizes,
        },
    )
