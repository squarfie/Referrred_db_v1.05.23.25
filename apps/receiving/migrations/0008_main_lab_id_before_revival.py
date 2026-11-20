from django.db import migrations, models


def reconcile_pre_id_revival_queues(apps, schema_editor):
    """Older releases created revival queues before Main Lab ID.

    Only untouched queues are returned to the ID-first stage. In-progress
    and completed revival records are preserved for explicit reconciliation.
    """
    Isolate = apps.get_model("receiving", "LaboratoryIsolate")
    alias = schema_editor.connection.alias
    qs = Isolate.objects.using(alias).filter(
        status="revival_queued",
        returned_at__isnull=True,
        acknowledged_at__isnull=True,
    )
    for isolate in qs.iterator():
        Isolate.objects.using(alias).filter(pk=isolate.pk).update(
            status="id_pending",
            endorsed_at=isolate.endorsed_at or isolate.revival_queued_at or isolate.created_at,
            endorsed_by_id=isolate.endorsed_by_id or isolate.revival_queued_by_id,
        )


class Migration(migrations.Migration):
    dependencies = [("receiving", "0007_laboratory_approved_organism_code")]

    operations = [
        migrations.AlterField(
            model_name="laboratoryisolate",
            name="status",
            field=models.CharField(max_length=20, default="id_pending", choices=[
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
            ]),
        ),
        migrations.RunPython(reconcile_pre_id_revival_queues, migrations.RunPython.noop),
    ]
