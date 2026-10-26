from rest_framework import serializers
from django.contrib.auth.models import User
from .models import Assignment, AuditEvent, Course, Enrollment, Grade, LtiTool, Submission, ToolPlacement

class CourseSerializer(serializers.ModelSerializer):
    class Meta: model = Course; fields = ["id", "code", "title", "description", "is_published", "created_at"]
class EnrollmentSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source="user.get_full_name", read_only=True)
    user_email = serializers.EmailField(source="user.email", read_only=True)
    class Meta: model = Enrollment; fields = ["id", "course", "user", "user_name", "user_email", "role", "active"]
class AssignmentSerializer(serializers.ModelSerializer):
    class Meta: model = Assignment; fields = "__all__"; read_only_fields = ["course"]
class SubmissionSerializer(serializers.ModelSerializer):
    class Meta: model = Submission; fields = "__all__"; read_only_fields = ["student", "version", "filename"]
class GradeSerializer(serializers.ModelSerializer):
    class Meta: model = Grade; fields = "__all__"; read_only_fields = ["grader"]
class LtiToolSerializer(serializers.ModelSerializer):
    class Meta: model = LtiTool; exclude = ["shared_secret"]
class PlacementSerializer(serializers.ModelSerializer):
    class Meta: model = ToolPlacement; fields = "__all__"
class AuditEventSerializer(serializers.ModelSerializer):
    class Meta: model = AuditEvent; fields = "__all__"

class PlatformUserSerializer(serializers.ModelSerializer):
    email_verified = serializers.BooleanField(source="profile.email_verified", read_only=True)
    is_platform_admin = serializers.BooleanField(source="profile.is_platform_admin", read_only=True)
    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name", "email", "is_active", "email_verified", "is_platform_admin", "date_joined"]
