"""Laboratory receipt workflow tabs, counters and dashboard presentation."""
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import render
from .models import ReferralReceipt
from apps.receiving.permissions import can_manage_secretariat_receiving
from .permissions import can_confirm_lab_receipt, can_read_laboratory_receiving
from apps.receiving.services import can_delete_receipt_safely
from .batch_status import pending_batch_accessions

def render_laboratory_dashboard(request, queue=None):
    all_receipts = ReferralReceipt.objects.select_related("site", "received_by")
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
        all_receipts = _laboratory_workflow_receipts(all_receipts)
        tab_definitions = [
            ("pending", "Pending Lab Receipt", ReferralReceipt.Status.ENDORSED),
            ("received", "Received by Laboratory", ReferralReceipt.Status.LAB_RECEIVED),
            ("main_lab", "Pending Main Lab ID", "main_lab"),
            ("processing", "Revival & Processing", "processing"),
            ("pending_batch", "Pending Batch Creation", "pending_batch"),
            ("complete", "Workflow Complete", "complete"),
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

    # Keep old ?status=... links working. Explicit ?tab=... takes precedence.
    # A legacy status without a corresponding role tab uses All Records.
    selected_tab = explicit_tab or "pending"
    if not explicit_tab and legacy_status in valid_statuses:
        selected_tab = next(
            (key for key, _label, value in tab_definitions if value == legacy_status),
            "all",
        )
    if selected_tab not in tab_status:
        selected_tab = "pending"

    query = (request.GET.get("q") or "").strip()
    receipts = all_receipts
    if query:
        receipts = receipts.filter(
            Q(tracking_number__icontains=query)
            | Q(site__SiteCode__icontains=query)
            | Q(site__SiteName__icontains=query)
        )

    # Counts are for the full queue and do not change with search or pagination.
    lab_counts = {
        key: all_receipts.filter(status=ReferralReceipt.Status.LAB_RECEIVED, lab_workflow=key).count()
        for key in ("received", "main_lab", "processing", "complete")
    } if is_lab_worklist else {}
    if is_lab_worklist:
        # Independent task queue: a scientifically completed receipt may still
        # have an accession range whose Raw Data batch has not been generated.
        pending_batch_filter = Q(
            status=ReferralReceipt.Status.LAB_RECEIVED,
            pk__in=pending_batch_accessions().values("laboratory_receiving__receipt_id"),
        )
        lab_counts["pending_batch"] = all_receipts.filter(pending_batch_filter).distinct().count()
    tabs = [
        {
            "key": key,
            "label": label,
            "count": lab_counts[key] if key in lab_counts else (sum(counts.values()) if tab_filter is None else counts.get(tab_filter, 0)),
            "active": key == selected_tab,
        }
        for key, label, tab_filter in tab_definitions
    ]
    selected_status = tab_status[selected_tab]
    if is_lab_worklist and selected_tab == "pending_batch":
        receipts = receipts.filter(pending_batch_filter).distinct()
    elif is_lab_worklist and selected_tab in lab_counts and not (not explicit_tab and legacy_status in valid_statuses):
        receipts = receipts.filter(status=ReferralReceipt.Status.LAB_RECEIVED, lab_workflow=selected_tab)
    elif not explicit_tab and legacy_status in valid_statuses:
        receipts = receipts.filter(status=legacy_status)
    elif selected_status is not None and selected_tab != "returned":
        receipts = receipts.filter(status=selected_status)

    page_obj = Paginator(
        receipts.order_by("-created_at", "-pk"),
        25,
    ).get_page(request.GET.get("page"))
    if not (is_lab_worklist and selected_tab == "returned"):
        for receipt in page_obj:
            receipt.can_delete_from_queue = (
                not is_lab_worklist and can_delete_receipt_safely(receipt, request.user)
            )
    return render(
        request,
        "laboratory/dashboard.html" if is_lab_worklist else "receiving/dashboard.html",
        {
            "page_obj": page_obj,
            "q": query,
            "active_tab": selected_tab,
            "active_tab_label": next(tab["label"] for tab in tabs if tab["active"]),
            "receiving_tabs": tabs,
            "lab_summary_cards": [tab for tab in tabs if tab["key"] != "all"] if is_lab_worklist else [],
            "received_count": counts.get(ReferralReceipt.Status.RECEIVED, 0),
            "endorsed_count": counts.get(ReferralReceipt.Status.ENDORSED, 0),
            "lab_received_count": counts.get(ReferralReceipt.Status.LAB_RECEIVED, 0),
            "can_manage_secretariat": can_manage_secretariat and not is_lab_worklist,
            "can_confirm_lab": can_confirm_lab and is_lab_worklist,
            "can_read_lab": can_read_laboratory_receiving(request.user) and is_lab_worklist,
            "receiving_queue_url": queue_url_name,
            "is_lab_worklist": is_lab_worklist,
            "show_main_lab_returns": False,
        },
    )
