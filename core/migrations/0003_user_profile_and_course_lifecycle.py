import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models
from django.utils import timezone

def create_profiles(apps, schema_editor):
    User = apps.get_model("auth", "User")
    Profile = apps.get_model("core", "UserProfile")
    for user in User.objects.all():
        Profile.objects.get_or_create(user=user, defaults={"email_verified": user.is_staff or user.is_superuser, "is_platform_admin": user.is_staff or user.is_superuser})

class Migration(migrations.Migration):
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL), ("core", "0002_alter_course_options")]
    operations = [
        migrations.AddField(model_name="course", name="is_archived", field=models.BooleanField(default=False)),
        migrations.CreateModel(name="UserProfile", fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)), ("email_verified", models.BooleanField(default=False)), ("is_platform_admin", models.BooleanField(default=False)), ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="profile", to=settings.AUTH_USER_MODEL))]),
        migrations.CreateModel(name="EmailVerificationToken", fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("token", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)), ("expires_at", models.DateTimeField()), ("used_at", models.DateTimeField(blank=True, null=True)), ("created_at", models.DateTimeField(auto_now_add=True)), ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="verification_tokens", to=settings.AUTH_USER_MODEL))]),
        migrations.RunPython(create_profiles, migrations.RunPython.noop),
    ]
