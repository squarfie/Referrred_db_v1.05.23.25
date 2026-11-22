from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("receiving", "0014_site_id_temporary_banking"),
    ]

    operations = [
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
