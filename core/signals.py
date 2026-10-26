from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import UserProfile

@receiver(post_save, sender=User)
def create_profile(sender, instance, created, **kwargs):
    profile, _ = UserProfile.objects.get_or_create(user=instance)
    if instance.is_superuser or instance.is_staff:
        if not profile.is_platform_admin or not profile.email_verified:
            profile.is_platform_admin = True
            profile.email_verified = True
            profile.save(update_fields=["is_platform_admin", "email_verified", "updated_at"])
