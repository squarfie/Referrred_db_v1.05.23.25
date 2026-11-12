from django.contrib import admin

from .models import ReceivingEvent, ReferralReceipt


@admin.register(ReferralReceipt)
class ReferralReceiptAdmin(admin.ModelAdmin):
    list_display = (
        "tracking_number",
        "site",
        "date_received",
        "status",
        "received_by",
        "lab_received_by",
    )
    list_filter = ("status", "date_received", "site")
    search_fields = ("tracking_number", "receiving_id", "site__SiteCode", "site__SiteName")
    readonly_fields = ("receiving_id", "created_at", "updated_at")


@admin.register(ReceivingEvent)
class ReceivingEventAdmin(admin.ModelAdmin):
    list_display = ("receipt", "event_type", "performed_by", "occurred_at", "recorded_at")
    list_filter = ("event_type", "occurred_at", "recorded_at")
    search_fields = ("receipt__tracking_number", "receipt__receiving_id")
    readonly_fields = (
        "receipt",
        "event_type",
        "performed_by",
        "occurred_at",
        "recorded_at",
        "remarks",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
