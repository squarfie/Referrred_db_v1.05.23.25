from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def reclassify_site_banking(apps, schema_editor):
    Isolate = apps.get_model("receiving", "LaboratoryIsolate")
    Biobank = apps.get_model("receiving", "LaboratoryBiobank")
    for item in Isolate.objects.filter(identification_source="site", status="biobanked").iterator():
        bank = Biobank.objects.filter(isolate_id=item.pk).first()
        snapshot = {}
        if bank:
            snapshot = {
                "storage_box": bank.storage_box, "box_position": bank.box_position,
                "lyo_box_name": bank.lyo_box_name, "lyo_box_number": bank.lyo_box_number,
                "lyo_position": bank.lyo_position,
                "supplementary_box_name": bank.supplementary_box_name,
                "supplementary_box_number": bank.supplementary_box_number,
                "supplementary_position": bank.supplementary_position,
            }
        item.site_banked_at = item.biobanked_at
        item.site_banked_by_id = item.biobanked_by_id
        item.site_bank_storage = snapshot
        item.status = "site_banked"
        item.biobanked_at = None
        item.biobanked_by_id = None
        item.save(update_fields=[
            "site_banked_at", "site_banked_by", "site_bank_storage",
            "status", "biobanked_at", "biobanked_by",
        ])


class Migration(migrations.Migration):
    dependencies = [
        ("receiving", "0013_full_inventory_grid_rows"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="laboratoryisolate",
            name="site_banked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="laboratoryisolate",
            name="site_banked_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="lab_site_banked_isolates", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name="laboratoryisolate",
            name="site_bank_storage",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AlterField(
            model_name="laboratoryisolate",
            name="status",
            field=models.CharField(choices=[
                ("revival_pending", "Awaiting revival"),
                ("revival_queued", "Queued for revival"),
                ("revival_in_progress", "Revival in progress"),
                ("revived", "Revival completed"),
                ("revival_failed", "Revival failed"),
                ("non_viable", "Non-viable"),
                ("id_queued", "Queued for Main Laboratory endorsement"),
                ("id_pending", "Awaiting Main Laboratory acknowledgment"),
                ("id_testing", "ID testing"),
                ("id_returned", "ID results and cassette returned"),
                ("id_reviewed", "Identification reviewed"),
                ("ready_for_biobank", "Ready for biobank"),
                ("site_banked", "Temporarily banked using Site ID"),
                ("biobanked", "Final biobank complete"),
            ], default="revival_pending", max_length=20),
        ),
        migrations.RunPython(reclassify_site_banking, migrations.RunPython.noop),
    ]
