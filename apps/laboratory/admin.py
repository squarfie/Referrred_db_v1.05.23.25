from django.contrib import admin
from apps.receiving.admin import ReadOnlyReceivingAdmin
from .models import LaboratoryReceiving, LaboratoryAccession, LaboratoryOrganismCount, LaboratoryIsolate, LaboratoryIdentification, LaboratoryBiobank

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


@admin.register(LaboratoryIsolate)
class LaboratoryIsolateAdmin(ReadOnlyReceivingAdmin):
    list_display = ("accession_number", "status", "final_organism", "endorsed_at", "reviewed_at")
    list_filter = ("status",)
    search_fields = ("accession_number", "final_organism")


@admin.register(LaboratoryIdentification)
class LaboratoryIdentificationAdmin(ReadOnlyReceivingAdmin):
    list_display = ("isolate", "organism_reported", "source", "instrument", "recorded_at")
    list_filter = ("source",)
    search_fields = ("isolate__accession_number", "organism_reported")


@admin.register(LaboratoryBiobank)
class LaboratoryBiobankAdmin(ReadOnlyReceivingAdmin):
    list_display = ("isolate", "preservation_method", "storage_location", "storage_box", "box_position")
    search_fields = ("isolate__accession_number", "storage_location", "storage_box")
