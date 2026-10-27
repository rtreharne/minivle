from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0006_toolplacement_navigation_visibility")]
    operations = [migrations.AddField(model_name="ltitool", name="deployment_registration_url", field=models.URLField(blank=True))]
