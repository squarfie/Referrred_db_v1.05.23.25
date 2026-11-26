from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        ("receiving", "0019_laboratory_dmu_handoff"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="receivingevent",
            name="receiving_one_canonical_event",
        ),
        migrations.AddConstraint(
            model_name="receivingevent",
            constraint=models.UniqueConstraint(
                fields=("receipt", "event_type"),
                condition=Q(event_type__in=("received", "lab_received")),
                name="receiving_one_canonical_event",
            ),
        ),
        migrations.AlterField(
            model_name="receivingevent",
            name="event_type",
            field=models.CharField(max_length=20, choices=[
                ("received", "Isolates Received"),
                ("endorsed", "Endorsed to Laboratory"),
                ("lab_received", "Received by Laboratory"),
                ("id_received", "Received for ID Testing"),
                ("accession_assigned", "Accession Range Assigned"),
                ("batch_created", "Laboratory Batch Created"),
                ("corrected", "Information Corrected"),
                ("endorsement_recalled", "Courier endorsement recalled"),
            ]),
        ),
        migrations.AlterField(
            model_name="laboratorybatchhandoff",
            name="status",
            field=models.CharField(
                max_length=16, db_index=True, default="pending",
                choices=[
                    ("pending", "Awaiting DMU receipt"),
                    ("confirmed", "Received by DMU"),
                    ("created", "Batch created by DMU"),
                    ("recalled", "Endorsement recalled by Bench A"),
                ],
            ),
        ),
        migrations.AlterField(
            model_name="laboratorybatchhandoffevent",
            name="action",
            field=models.CharField(max_length=20, choices=[
                ("endorsed", "Endorsed to DMU"),
                ("confirmed", "DMU receipt confirmed"),
                ("batch_created", "Raw Data batch created by DMU"),
                ("recalled", "Endorsement recalled by Bench A"),
            ]),
        ),
    ]
