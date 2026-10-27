from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0005_assignment_submission_type")]
    operations = [migrations.AddField(model_name="toolplacement", name="visible_in_course_navigation", field=models.BooleanField(default=True))]
