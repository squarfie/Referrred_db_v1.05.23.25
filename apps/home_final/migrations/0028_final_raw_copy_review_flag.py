from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("home_final", "0027_concordanceoptions_is_active"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="final_data",
            name="raw_updated_after_copy",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="final_data",
            name="raw_updated_after_copy_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="final_data",
            name="raw_updated_after_copy_by",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="final_copies_with_raw_updates", to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="final_data",
            name="raw_updated_after_copy_fields",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
