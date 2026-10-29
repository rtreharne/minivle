from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0009_ltiservicetoken"),
    ]

    operations = [
        migrations.CreateModel(
            name="BrandSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("logo", models.FileField(blank=True, upload_to="branding/")),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
    ]
