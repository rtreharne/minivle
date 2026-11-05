import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0012_scoped_canvas_api"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CanvasUploadGrant",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token_hash", models.CharField(max_length=64, unique=True)),
                ("filename", models.CharField(max_length=255)),
                ("size", models.PositiveBigIntegerField()),
                ("content_type", models.CharField(default="application/octet-stream", max_length=255)),
                ("expires_at", models.DateTimeField()),
                ("used_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("assignment", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="canvas_upload_grants", to="core.assignment")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="canvas_upload_grants", to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
