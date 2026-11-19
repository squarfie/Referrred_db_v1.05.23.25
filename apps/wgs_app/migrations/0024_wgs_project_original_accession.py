from django.db import migrations, models


def backfill_original_accessions(apps, schema_editor):
    Project = apps.get_model("wgs_app", "WGS_Project")
    # The FK targets Final_Data.f_AccessionNo, so its raw id is already
    # the accession string. Preserve existing links without guessing from
    # unrelated sequencing sample identifiers.
    for project in Project.objects.using(schema_editor.connection.alias).filter(
        Ref_Accession__isnull=False
    ).iterator(chunk_size=1000):
        if project.Ref_Accession_id:
            Project.objects.using(schema_editor.connection.alias).filter(
                pk=project.pk, original_accession=""
            ).update(original_accession=project.Ref_Accession_id)


class Migration(migrations.Migration):

    dependencies = [
        ("wgs_app", "0023_alter_wgs_project_ref_accession"),
    ]

    operations = [
        migrations.AddField(
            model_name="wgs_project",
            name="original_accession",
            field=models.CharField(blank=True, db_index=True, default="", max_length=255),
        ),
        migrations.RunPython(backfill_original_accessions, migrations.RunPython.noop),
    ]
