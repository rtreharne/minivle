import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0011_student_identifiers"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField("ltitool", "api_scopes", models.JSONField(blank=True, default=list)),
        migrations.AddField("ltiservicetoken", "course_ids", models.JSONField(default=list)),
        migrations.AddField("ltiservicetoken", "is_platform_admin", models.BooleanField(default=False)),
        migrations.AddField("ltiservicetoken", "launch_role", models.CharField(default="student", max_length=16)),
        migrations.AddField("ltiservicetoken", "launch_jti", models.CharField(blank=True, max_length=255, null=True, unique=True)),
        migrations.AddField("ltiservicetoken", "launch_course", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="lti_service_tokens", to="core.course")),
        migrations.AddField("ltiservicetoken", "user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="lti_service_tokens", to=settings.AUTH_USER_MODEL)),
        migrations.CreateModel(
            name="Rubric",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
                ("title", models.CharField(max_length=255)), ("free_form_criterion_comments", models.BooleanField(default=False)),
                ("hide_score_total", models.BooleanField(default=False)), ("reusable", models.BooleanField(default=False)),
                ("course", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="rubrics", to="core.course")),
            ],
        ),
        migrations.CreateModel(
            name="RubricAssociation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
                ("use_for_grading", models.BooleanField(default=False)), ("hide_score_total", models.BooleanField(default=False)),
                ("purpose", models.CharField(default="grading", max_length=32)),
                ("assignment", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="rubric_associations", to="core.assignment")),
                ("course", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="rubric_associations", to="core.course")),
                ("rubric", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="associations", to="core.rubric")),
            ],
        ),
        migrations.CreateModel(
            name="RubricAssessment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
                ("assessment_type", models.CharField(default="grading", max_length=32)), ("score", models.DecimalField(blank=True, decimal_places=2, max_digits=8, null=True)),
                ("data", models.JSONField(blank=True, default=dict)), ("comments", models.TextField(blank=True)), ("provisional", models.BooleanField(default=False)),
                ("final", models.BooleanField(default=False)), ("graded_anonymously", models.BooleanField(default=False)),
                ("assessor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="rubric_assessments_given", to=settings.AUTH_USER_MODEL)),
                ("student", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="rubric_assessments", to=settings.AUTH_USER_MODEL)),
                ("association", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="assessments", to="core.rubricassociation")),
            ],
        ),
        migrations.CreateModel(
            name="RubricCriterion",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("criterion_id", models.CharField(max_length=80)), ("description", models.CharField(max_length=255)), ("long_description", models.TextField(blank=True)),
                ("points", models.DecimalField(decimal_places=2, default=0, max_digits=8)), ("ratings", models.JSONField(blank=True, default=list)),
                ("rubric", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="criteria", to="core.rubric")),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("rubric", "criterion_id"), name="unique_rubric_criterion")]},
        ),
    ]
