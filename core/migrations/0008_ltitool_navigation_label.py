from django.db import migrations, models


def seed_navigation_labels(apps, schema_editor):
    LtiTool = apps.get_model("core", "LtiTool")
    for tool in LtiTool.objects.filter(navigation_label=""):
        tool.navigation_label = tool.name
        tool.save(update_fields=["navigation_label"])


class Migration(migrations.Migration):
    dependencies = [("core", "0007_ltitool_deployment_registration_url")]

    operations = [
        migrations.AddField(
            model_name="ltitool",
            name="navigation_label",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.RunPython(seed_navigation_labels, migrations.RunPython.noop),
    ]
