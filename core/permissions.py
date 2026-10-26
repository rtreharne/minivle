from django.shortcuts import get_object_or_404
from .models import Enrollment

def course_role(user, course):
    if user.is_superuser: return Enrollment.Role.ADMIN
    enrollment = Enrollment.objects.filter(course=course, user=user, active=True).first()
    return enrollment.role if enrollment else None

def can_manage(user, course):
    return course_role(user, course) in (Enrollment.Role.INSTRUCTOR, Enrollment.Role.ADMIN)

def is_platform_admin(user):
    return bool(user.is_authenticated and (user.is_superuser or getattr(getattr(user, "profile", None), "is_platform_admin", False)))

def student_preview_active(request, course):
    return request.session.get("student_preview_course") == course.id

def enrolled_or_404(user, course):
    if not course_role(user, course):
        from django.http import Http404
        raise Http404("Course not found")
    return course
