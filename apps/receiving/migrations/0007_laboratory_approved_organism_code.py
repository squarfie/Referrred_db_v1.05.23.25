from django.db import migrations, models


def backfill_approved_codes(apps, schema_editor):
    """Preserve reviewed identification codes from existing biobank records when present."""
    Isolate = apps.get_model("receiving", "LaboratoryIsolate")
    Biobank = apps.get_model("receiving", "LaboratoryBiobank")
    alias = schema_editor.connection.alias
    banks = Biobank.objects.using(alias).exclude(whonet_code="").values_list("isolate_id", "whonet_code")
    for isolate_id, code in banks.iterator():
        Isolate.objects.using(alias).filter(pk=isolate_id, approved_organism_code="").update(
            approved_organism_code=(code or "").strip().upper()[:20]
        )


class Migration(migrations.Migration):
    dependencies = [("receiving", "0006_laboratory_revival_biobank_stages")]

    operations = [
        migrations.AddField(
            model_name="laboratoryidentification",
            name="whonet_organism_code",
            field=models.CharField(max_length=20, blank=True, default=""),
        ),
        migrations.AddField(
            model_name="laboratoryisolate",
            name="approved_organism_code",
            field=models.CharField(max_length=20, blank=True, default=""),
        ),
        migrations.RunPython(backfill_approved_codes, migrations.RunPython.noop),
    ]
