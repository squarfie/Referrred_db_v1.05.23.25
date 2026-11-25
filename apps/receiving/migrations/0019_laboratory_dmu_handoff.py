from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("receiving", "0018_align_receiving_choice_labels"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="LaboratoryBatchHandoff",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("pending", "Awaiting DMU receipt"), ("confirmed", "Received by DMU"), ("created", "Batch created by DMU")], db_index=True, default="pending", max_length=16)),
                ("endorsed_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("confirmed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(blank=True, null=True)),
                ("accession", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="dmu_handoff", to="receiving.laboratoryaccession")),
                ("endorsed_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="laboratory_dmu_endorsements", to=settings.AUTH_USER_MODEL)),
                ("confirmed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="laboratory_dmu_confirmations", to=settings.AUTH_USER_MODEL)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="laboratory_dmu_batch_creations", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ("-endorsed_at", "-pk")},
        ),
        migrations.AddIndex(
            model_name="laboratorybatchhandoff",
            index=models.Index(fields=["status", "endorsed_at"], name="receiving_dmu_status_idx"),
        ),
        migrations.CreateModel(
            name="LaboratoryBatchHandoffEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("action", models.CharField(choices=[("endorsed", "Endorsed to DMU"), ("confirmed", "DMU receipt confirmed"), ("batch_created", "Raw Data batch created by DMU")], max_length=20)),
                ("performed_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("remarks", models.TextField(blank=True, default="")),
                ("handoff", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="events", to="receiving.laboratorybatchhandoff")),
                ("performed_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="laboratory_dmu_handoff_events", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ("performed_at", "pk")},
        ),
    ]
