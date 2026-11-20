from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("home", "0044_alter_arsstaff_details_staff_role"),
    ]

    operations = [
        migrations.AlterField(
            model_name="arsstaff_details",
            name="Staff_Role",
            field=models.CharField(
                max_length=150,
                blank=True,
                default="",
                choices=[
                    ("", ""),
                    ("DMU Encoder", "DMU Encoder"),
                    ("LAB Encoder", "LAB Encoder"),
                    ("Laboratory Staff", "Laboratory Staff"),
                    ("Secretariat", "Secretariat"),
                    ("Verifier", "Verifier"),
                    ("Checker", "Checker"),
                    ("Manager", "Manager"),
                    ("Admin", "Admin"),
                ],
            ),
        ),
    ]
