from django.contrib import admin

from .models import VerificationCase, VerificationComment


class VerificationCommentInline(admin.TabularInline):
    model = VerificationComment
    extra = 0
    fields = ("raw_accession_ref_sequence", "final_accession_ref_sequence", "field_label", "decision", "is_resolved", "created_by", "created_at")
    readonly_fields = ("created_at",)


@admin.register(VerificationCase)
class VerificationCaseAdmin(admin.ModelAdmin):
    list_display = ("id", "final_batch", "final_accession", "assigned_verifier", "status", "updated_at")
    list_filter = ("status", "assigned_checker", "assigned_verifier")
    search_fields = (
        "final_batch__bat_Batch_Code",
        "final_batch__bat_Batch_Name",
        "final_accession__f_AccessionNo",
        "note",
    )
    readonly_fields = ("qr_token", "created_at", "updated_at")
    inlines = (VerificationCommentInline,)


@admin.register(VerificationComment)
class VerificationCommentAdmin(admin.ModelAdmin):
    list_display = ("id", "verification_case", "raw_accession_ref_sequence", "final_accession_ref_sequence", "field_label", "decision", "created_by", "created_at")
    list_filter = ("decision", "is_resolved", "created_at")
    search_fields = ("comment", "field_name", "field_label", "raw_accession_ref_sequence__AccessionNo", "final_accession_ref_sequence__f_AccessionNo")
