from django.contrib import admin

from .models import (
    LaboratoryAccession,
    LaboratoryOrganismCount,
    LaboratoryReceiving,
    ReceivingEvent,
    ReferralReceipt,
)


class ReadOnlyReceivingAdmin(admin.ModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ReferralReceipt)
class ReferralReceiptAdmin(ReadOnlyReceivingAdmin):
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


@admin.register(ReceivingEvent)
class ReceivingEventAdmin(ReadOnlyReceivingAdmin):
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



@admin.register(LaboratoryReceiving)
class LaboratoryReceivingAdmin(ReadOnlyReceivingAdmin):
    list_display = ("receipt", "total_isolates_received", "isolates_rejected", "isolates_accepted")
    list_filter = ("receipt__lab_date_received",)
    search_fields = ("receipt__tracking_number", "receipt__site__SiteCode")


@admin.register(LaboratoryAccession)
class LaboratoryAccessionAdmin(ReadOnlyReceivingAdmin):
    list_display = ("laboratory_receiving", "date_referred", "start_number", "end_number", "batch")
    search_fields = ("laboratory_receiving__receipt__tracking_number", "batch__bat_Batch_Code")


@admin.register(LaboratoryOrganismCount)
class LaboratoryOrganismCountAdmin(ReadOnlyReceivingAdmin):
    list_display = ("accession_range", "organism_code", "count")
    list_filter = ("organism_code",)
