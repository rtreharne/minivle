from django.db import migrations, models
import secrets


def populate_student_identifiers(apps, schema_editor):
    User = apps.get_model("auth", "User")
    Enrollment = apps.get_model("core", "Enrollment")
    Profile = apps.get_model("core", "UserProfile")
    db = schema_editor.connection.alias
    student_ids = set(Profile.objects.using(db).exclude(student_id__isnull=True).values_list("student_id", flat=True))
    sis_ids = set(Profile.objects.using(db).exclude(sis_login_id__isnull=True).values_list("sis_login_id", flat=True))
    students = User.objects.using(db).filter(enrollments__role="student").exclude(
        enrollments__role__in=["instructor", "admin"]
    ).distinct().order_by("id")
    for user in students.iterator():
        profile, _ = Profile.objects.using(db).get_or_create(user_id=user.id)
        username = user.username
        # Retain the existing login name as the unique student username.
        student_id = profile.student_id
        if not student_id:
            while True:
                candidate = "20" + "".join(str(secrets.randbelow(10)) for _ in range(7))
                if candidate not in student_ids:
                    student_id = candidate
                    student_ids.add(candidate)
                    break
        sis_login_id = profile.sis_login_id
        if not sis_login_id:
            base = f"{username}{student_id}"
            sis_login_id = base
            suffix = 1
            while sis_login_id in sis_ids:
                sis_login_id = f"{base}{suffix}"
                suffix += 1
            sis_ids.add(sis_login_id)
        Profile.objects.using(db).filter(pk=profile.pk).update(
            username=username, student_id=student_id, sis_login_id=sis_login_id
        )


class Migration(migrations.Migration):
    dependencies = [("core", "0010_brandsettings")]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="username",
            field=models.CharField(blank=True, max_length=150, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="student_id",
            field=models.CharField(blank=True, max_length=9, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="sis_login_id",
            field=models.CharField(blank=True, max_length=160, null=True, unique=True),
        ),
        migrations.RunPython(populate_student_identifiers, migrations.RunPython.noop),
    ]
