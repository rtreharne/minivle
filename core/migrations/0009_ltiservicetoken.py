from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("core", "0008_ltitool_navigation_label")]
    operations = [migrations.CreateModel(
        name="LtiServiceToken",
        fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("token_hash", models.CharField(max_length=64, unique=True)),
            ("assertion_jti", models.CharField(max_length=255, unique=True)),
            ("scopes", models.JSONField(default=list)),
            ("expires_at", models.DateTimeField()),
            ("created_at", models.DateTimeField(auto_now_add=True)),
            ("deployment", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="service_tokens", to="core.ltideployment")),
            ("tool", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="service_tokens", to="core.ltitool")),
        ],
    )]
