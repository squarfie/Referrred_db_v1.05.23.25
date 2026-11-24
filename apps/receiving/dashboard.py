"""Shared receipt queue presentation for Secretariat and laboratory custody."""
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import render
from django.utils import timezone
from .models import ReferralReceipt
from .permissions import can_manage_secretariat_receiving
from apps.laboratory.permissions import can_read_laboratory_receiving
from .services import can_delete_receipt_safely

def render_receiving_dashboard(request, queue=None):
    if queue == "laboratory" or (queue is None and can_read_laboratory_receiving(request.user)):
        from apps.laboratory.dashboard import render_laboratory_dashboard
        return render_laboratory_dashboard(request, queue=queue)
    return _render_secretariat_dashboard(request, queue=queue)


def _render_secretariat_dashboard(request, queue=None):
    all_receipts = ReferralReceipt.objects.select_related('site', 'received_by')
    current_year = timezone.localdate().year
    year_param = (request.GET.get('year') or '').strip()
    available_years = sorted(
        {value.year for value in ReferralReceipt.objects.dates('date_received', 'year')} | {current_year},
        reverse=True,
    )
    selected_year = current_year
    if year_param == 'all':
        selected_year = 'all'
    elif year_param.isascii() and year_param.isdecimal():
        requested_year = int(year_param)
        if requested_year in available_years:
            selected_year = requested_year
    if selected_year != 'all':
        all_receipts = all_receipts.filter(date_received__year=selected_year)
    counts = {row['status']: row['total'] for row in all_receipts.values('status').annotate(total=Count('id'))}
    can_manage_secretariat = can_manage_secretariat_receiving(request.user)
    queue_url_name = 'laboratory_receiving_dashboard' if queue == 'laboratory' else 'secretariat_receiving_dashboard' if queue == 'secretariat' else 'receiving_dashboard'
    tab_definitions = [('pending', 'Received', ReferralReceipt.Status.RECEIVED), ('endorsed', 'Endorsed', ReferralReceipt.Status.ENDORSED), ('received', 'Lab Received', ReferralReceipt.Status.LAB_RECEIVED), ('all', 'All Records', None)]
    tab_status = {key: value for key, _label, value in tab_definitions}
    explicit_tab = (request.GET.get('tab') or '').strip()
    legacy_status = (request.GET.get('status') or '').strip()
    valid_statuses = {value for value, _label in ReferralReceipt.Status.choices}
    selected_tab = explicit_tab or 'pending'
    if not explicit_tab and legacy_status in valid_statuses:
        selected_tab = next((key for key, _label, value in tab_definitions if value == legacy_status), 'all')
    if selected_tab not in tab_status:
        selected_tab = 'pending'
    query = (request.GET.get('q') or '').strip()
    receipts = all_receipts
    if query:
        receipts = receipts.filter(Q(tracking_number__icontains=query) | Q(site__SiteCode__icontains=query) | Q(site__SiteName__icontains=query))
    tabs = [{'key': key, 'label': label, 'count': sum(counts.values()) if tab_filter is None else counts.get(tab_filter, 0), 'active': key == selected_tab} for key, label, tab_filter in tab_definitions]
    selected_status = tab_status[selected_tab]
    if not explicit_tab and legacy_status in valid_statuses:
        receipts = receipts.filter(status=legacy_status)
    elif selected_status is not None and selected_tab != 'returned':
        receipts = receipts.filter(status=selected_status)
    allowed_page_sizes = (10, 25, 50)
    per_page_raw = (request.GET.get('per_page') or '').strip()
    per_page = int(per_page_raw) if per_page_raw in ('10', '25', '50') else 25
    page_obj = Paginator(receipts.order_by('-created_at', '-pk'), per_page).get_page(request.GET.get('page'))
    for receipt in page_obj:
        receipt.can_delete_from_queue = can_delete_receipt_safely(receipt, request.user)
    return render(request, 'receiving/dashboard.html', {'page_obj': page_obj, 'q': query, 'active_tab': selected_tab, 'active_tab_label': next((tab['label'] for tab in tabs if tab['active'])), 'receiving_tabs': tabs, 'lab_summary_cards': [], 'received_count': counts.get(ReferralReceipt.Status.RECEIVED, 0), 'endorsed_count': counts.get(ReferralReceipt.Status.ENDORSED, 0), 'lab_received_count': counts.get(ReferralReceipt.Status.LAB_RECEIVED, 0), 'can_manage_secretariat': can_manage_secretariat, 'can_confirm_lab': False, 'can_read_lab': False, 'receiving_queue_url': queue_url_name, 'is_lab_worklist': False, 'show_main_lab_returns': False, 'selected_year': selected_year, 'available_years': available_years, 'per_page': per_page, 'allowed_page_sizes': allowed_page_sizes})
