# Laboratory ID testing and biobank inventory schema.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def create_laboratory_staff_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.using(schema_editor.connection.alias).get_or_create(name="Laboratory Staff")


class Migration(migrations.Migration):
    dependencies = [
        ("receiving", "0004_remove_laboratory_finalize"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.CreateModel(
            name="LaboratoryIsolate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("accession_number", models.CharField(db_index=True, max_length=80, unique=True)),
                ("status", models.CharField(
                    choices=[
                        ("pending", "Awaiting ID laboratory"),
                        ("testing", "ID testing"),
                        ("returned", "Results returned"),
                        ("reviewed", "ID reviewed"),
                    ],
                    default="pending", max_length=20,
                )),
                ("endorsed_at", models.DateTimeField(blank=True, null=True)),
                ("acknowledged_at", models.DateTimeField(blank=True, null=True)),
                ("returned_at", models.DateTimeField(blank=True, null=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("final_organism", models.CharField(blank=True, default="", max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("accession_range", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT, related_name="isolate_records",
                    to="receiving.laboratoryaccession",
                )),
                ("endorsed_by", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="isolates_endorsed_for_id", to=settings.AUTH_USER_MODEL,
                )),
                ("acknowledged_by", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="isolates_acknowledged_for_id", to=settings.AUTH_USER_MODEL,
                )),
                ("reviewed_by", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="isolates_id_reviewed", to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={"db_table": "laboratory_isolates", "ordering": ("accession_number",)},
        ),
        migrations.CreateModel(
            name="LaboratoryIdentification",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("organism_reported", models.CharField(max_length=255)),
                ("instrument", models.CharField(blank=True, default="", max_length=100)),
                ("cassette_reference", models.CharField(blank=True, default="", max_length=100)),
                ("tested_at", models.DateTimeField(blank=True, null=True)),
                ("source", models.CharField(
                    choices=[("manual", "Manual transcription"), ("csv", "CSV upload"), ("xlsx", "Excel upload")],
                    default="manual", max_length=10,
                )),
                ("recorded_at", models.DateTimeField(auto_now_add=True)),
                ("isolate", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT, related_name="identifications",
                    to="receiving.laboratoryisolate",
                )),
                ("recorded_by", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="laboratory_id_entries", to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={"db_table": "laboratory_identifications", "ordering": ("-recorded_at", "-id")},
        ),
        migrations.CreateModel(
            name="LaboratoryBiobank",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("date_harvested", models.DateField(blank=True, null=True)),
                ("whonet_code", models.CharField(blank=True, default="", max_length=30)),
                ("date_lyophilized", models.DateField(blank=True, null=True)),
                ("lyo_box_name", models.CharField(blank=True, default="", max_length=100)),
                ("lyo_box_number", models.CharField(blank=True, default="", max_length=50)),
                ("lyo_position", models.CharField(blank=True, default="", max_length=50)),
                ("refrigerator_number", models.CharField(blank=True, default="", max_length=50)),
                ("lyo_location", models.CharField(blank=True, default="", max_length=100)),
                ("lyo_date_encoded", models.DateField(blank=True, null=True)),
                ("skimmed_milk", models.BooleanField(default=False)),
                ("tsbg", models.BooleanField(default=False)),
                ("supplementary_box_name", models.CharField(blank=True, default="", max_length=100)),
                ("supplementary_box_number", models.CharField(blank=True, default="", max_length=50)),
                ("supplementary_position", models.CharField(blank=True, default="", max_length=50)),
                ("freezer_number", models.CharField(blank=True, default="", max_length=50)),
                ("supplementary_location", models.CharField(blank=True, default="", max_length=100)),
                ("supplementary_date_encoded", models.DateField(blank=True, null=True)),
                ("preservation_method", models.CharField(blank=True, default="", max_length=100)),
                ("container", models.CharField(blank=True, default="", max_length=100)),
                ("storage_location", models.CharField(blank=True, default="", max_length=150)),
                ("storage_box", models.CharField(blank=True, default="", max_length=100)),
                ("box_position", models.CharField(blank=True, default="", max_length=30)),
                ("remarks", models.TextField(blank=True, default="")),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("isolate", models.OneToOneField(
                    on_delete=django.db.models.deletion.PROTECT, related_name="biobank",
                    to="receiving.laboratoryisolate",
                )),
                ("recorded_by", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="laboratory_biobank_entries", to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={"db_table": "laboratory_biobank"},
        ),
        migrations.RunPython(create_laboratory_staff_group, migrations.RunPython.noop),
    ]
