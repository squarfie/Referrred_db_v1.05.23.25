from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("receiving", "0014_site_id_temporary_banking"),
    ]

    operations = [
        migrations.AlterField(
            model_name="referralreceipt",
            name="status",
            field=models.CharField(
                choices=[
                    ("received", "Isolates Received"),
                    ("endorsed", "Endorsed to Laboratory Receiving"),
                    ("lab_received", "Received by Laboratory Receiving"),
                ],
                db_index=True, default="received", max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="receivingevent",
            name="event_type",
            field=models.CharField(
                choices=[
                    ("received", "Isolates Received"),
                    ("endorsed", "Endorsed to Laboratory Receiving"),
                    ("lab_received", "Received by Laboratory Receiving"),
                    ("id_received", "Received for ID Testing"),
                    ("accession_assigned", "Accession Range Assigned"),
                    ("batch_created", "Laboratory Batch Created"),
                    ("corrected", "Information Corrected"),
                ], max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="referralreceipt",
            name="date_received",
            field=models.DateField(verbose_name="Date Received"),
        ),
        migrations.AlterField(
            model_name="referralreceipt",
            name="time_received",
            field=models.TimeField(verbose_name="Time Received"),
        ),
    ]
