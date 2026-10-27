from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0004_lti13_deployments")]
    operations = [migrations.AddField(model_name="assignment", name="submission_type", field=models.CharField(choices=[("upload", "File upload"), ("lti", "External LTI tool")], default="upload", max_length=12))]
