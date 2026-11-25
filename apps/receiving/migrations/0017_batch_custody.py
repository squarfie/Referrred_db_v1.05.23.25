"""Add independent batch custody state and append-only handoff events."""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import F, Q
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("receiving", "0016_simplified_laboratory_identification"),
        ("home", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="BatchCustody",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("current_role", models.CharField(max_length=40)),
                ("pending_role", models.CharField(blank=True, default="", max_length=40)),
                ("endorsed_at", models.DateTimeField(blank=True, null=True)),
                ("confirmed_at", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("batch", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="receiving_custody", to="home.batch_table")),
                ("endorsed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="batch_custody_endorsements", to=settings.AUTH_USER_MODEL)),
                ("confirmed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="batch_custody_confirmations", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ("-updated_at", "-pk")},
        ),
        migrations.CreateModel(
            name="BatchCustodyEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("action", models.CharField(choices=[("initialized", "Custody Initialized"), ("endorsed", "Endorsed"), ("confirmed", "Receipt Confirmed"), ("undone", "Endorsement Recalled")], max_length=20)),
                ("from_role", models.CharField(max_length=40)),
                ("to_role", models.CharField(blank=True, default="", max_length=40)),
                ("performed_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("remarks", models.TextField(blank=True, default="")),
                ("custody", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="events", to="receiving.batchcustody")),
                ("performed_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="batch_custody_events", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ("performed_at", "pk")},
        ),
        migrations.AddConstraint(
            model_name="batchcustody",
            constraint=models.CheckConstraint(condition=~Q(current_role=""), name="receiving_custody_current_role_required"),
        ),
        migrations.AddConstraint(
            model_name="batchcustody",
            constraint=models.CheckConstraint(condition=Q(pending_role="") | ~Q(current_role=F("pending_role")), name="receiving_custody_no_self_endorsement"),
        ),
    ]
