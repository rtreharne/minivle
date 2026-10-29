from django.contrib.auth.models import User
from .models import BrandSettings, Enrollment


def preview_context(request):
    """Expose clearly scoped session-only viewing modes to templates."""
    original_user_id = request.session.get("masquerade_original_user_id")
    original_user = None
    if original_user_id:
        original_user = User.objects.filter(pk=original_user_id).only(
            "id", "username", "first_name", "last_name"
        ).first()
    return {
        "preview_course_id": request.session.get("student_preview_course"),
        "masquerade_original_user": original_user,
        "brand_settings": BrandSettings.objects.first(),
        "is_instructor": request.user.is_authenticated and Enrollment.objects.filter(user=request.user, role=Enrollment.Role.INSTRUCTOR, active=True).exists(),
    }
