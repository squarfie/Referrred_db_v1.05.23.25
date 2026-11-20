from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def migrate_legacy_isolates(apps, schema_editor):
    Isolate = apps.get_model("receiving", "LaboratoryIsolate")
    Biobank = apps.get_model("receiving", "LaboratoryBiobank")
    alias = schema_editor.connection.alias
    for isolate in Isolate.objects.using(alias).all().iterator():
        mappings = {
            "pending": "id_pending",
            "testing": "id_testing",
            "returned": "id_returned",
            "reviewed": "id_reviewed",
        }
        status = mappings.get(isolate.status)
        if status is None:
            continue
        if isolate.status == "reviewed" and isolate.final_organism:
            record = Biobank.objects.using(alias).filter(isolate_id=isolate.pk).first()
            if record and record.date_harvested and record.date_lyophilized:
                if record.date_harvested <= record.date_lyophilized:
                    status = "ready_for_biobank"
                    if record.box_position or record.lyo_position or record.supplementary_position:
                        status = "biobanked"
        Isolate.objects.using(alias).filter(pk=isolate.pk).update(status=status)


class Migration(migrations.Migration):
    dependencies = [
        ("receiving", "0005_laboratory_isolate_identification_biobank"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(model_name="laboratoryisolate", name="revival_failed_reason", field=models.TextField(blank=True, default="")),
        migrations.AddField(model_name="laboratoryisolate", name="revival_retry_count", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="laboratoryisolate", name="revival_retried_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="laboratoryisolate", name="revival_retried_by", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="lab_revival_retried_isolates", to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name="laboratoryisolate", name="revival_queued_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="laboratoryisolate", name="revival_started_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="laboratoryisolate", name="revival_completed_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="laboratoryisolate", name="lyophilization_completed_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="laboratoryisolate", name="biobanked_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="laboratoryisolate", name="revival_queued_by", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="lab_revival_queued_isolates", to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name="laboratoryisolate", name="revival_started_by", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="lab_revival_started_isolates", to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name="laboratoryisolate", name="revival_completed_by", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="lab_revival_completed_isolates", to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name="laboratoryisolate", name="returned_by", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="lab_id_returned_isolates", to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name="laboratoryisolate", name="lyophilization_completed_by", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="lab_lyophilization_completed_isolates", to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name="laboratoryisolate", name="biobanked_by", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="lab_biobanked_isolates", to=settings.AUTH_USER_MODEL)),
        migrations.AlterField(model_name="laboratoryisolate", name="status", field=models.CharField(max_length=20, default="revival_queued", choices=[
            ("revival_queued", "Queued for revival"),
            ("revival_in_progress", "Revival in progress"),
            ("revived", "Revival completed"),
            ("revival_failed", "Revival failed"),
            ("non_viable", "Non-viable"),
            ("id_pending", "Awaiting Main Laboratory acknowledgment"),
            ("id_testing", "ID testing"),
            ("id_returned", "ID results and cassette returned"),
            ("id_reviewed", "Identification reviewed"),
            ("ready_for_biobank", "Ready for biobank"),
            ("biobanked", "Biobanked"),
        ])),
        migrations.RunPython(migrate_legacy_isolates, migrations.RunPython.noop),
    ]
