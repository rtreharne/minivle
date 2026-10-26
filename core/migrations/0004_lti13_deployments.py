import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0003_user_profile_and_course_lifecycle")]
    operations = [
        migrations.AddField(model_name="ltitool", name="active", field=models.BooleanField(default=True)),
        migrations.AddField(model_name="ltitool", name="oidc_initiation_url", field=models.URLField(blank=True)),
        migrations.AddField(model_name="ltitool", name="scopes", field=models.JSONField(blank=True, default=list)),
        migrations.CreateModel(
            name="LtiDeployment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deployment_id", models.CharField(max_length=255, unique=True)),
                ("active", models.BooleanField(default=True)),
                ("course", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="lti_deployments", to="core.course")),
                ("tool", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="deployments", to="core.ltitool")),
            ],
        ),
        migrations.AddConstraint(model_name="ltideployment", constraint=models.UniqueConstraint(fields=("tool", "course"), name="unique_lti_tool_course_deployment")),
    ]
