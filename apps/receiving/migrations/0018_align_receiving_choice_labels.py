"""Align persisted receiving field choices with their current display labels.

No rows change: this migration updates only Django's choice metadata.
"""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("receiving", "0017_batch_custody"),
    ]

    operations = [
        migrations.AlterField(
            model_name="referralreceipt",
            name="status",
            field=models.CharField(
                max_length=20,
                db_index=True,
                default="received",
                choices=[
                    ("received", "Isolates Received"),
                    ("endorsed", "Endorsed to Laboratory"),
                    ("lab_received", "Received by Laboratory"),
                ],
            ),
        ),
        migrations.AlterField(
            model_name="receivingevent",
            name="event_type",
            field=models.CharField(
                max_length=20,
                choices=[
                    ("received", "Isolates Received"),
                    ("endorsed", "Endorsed to Laboratory"),
                    ("lab_received", "Received by Laboratory"),
                    ("id_received", "Received for ID Testing"),
                    ("accession_assigned", "Accession Range Assigned"),
                    ("batch_created", "Laboratory Batch Created"),
                    ("corrected", "Information Corrected"),
                ],
            ),
        ),
    ]
