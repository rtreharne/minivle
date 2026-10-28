import os
import boto3
from botocore.exceptions import ClientError
from django.conf import settings
from django.core.files.storage import default_storage
from django.contrib.auth.models import User
from django.db.models import Max
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied
from .models import Assignment, AuditEvent, Course, Enrollment, Grade, LtiTool, Submission, ToolPlacement
from .permissions import can_manage, course_role, is_platform_admin
from .serializers import AssignmentSerializer, AuditEventSerializer, CourseSerializer, EnrollmentSerializer, GradeSerializer, LtiToolSerializer, PlacementSerializer, PlatformUserSerializer, SubmissionSerializer

def audit(request, action, target, **metadata):
    AuditEvent.objects.create(actor=request.user if request.user.is_authenticated else None, action=action, target=target, metadata=metadata)

class CourseViewSet(viewsets.ModelViewSet):
    serializer_class, queryset = CourseSerializer, Course.objects.all()
    def get_queryset(self):
        if self.request.user.is_superuser: return self.queryset
        return self.queryset.filter(enrollments__user=self.request.user, enrollments__active=True).distinct()

class EnrollmentViewSet(viewsets.ModelViewSet):
    serializer_class, queryset = EnrollmentSerializer, Enrollment.objects.select_related("user", "course")
    def get_queryset(self):
        qs = self.queryset
        course = self.request.query_params.get("course")
        return qs.filter(course_id=course) if course else qs.filter(user=self.request.user)
    def perform_create(self, serializer):
        course = serializer.validated_data["course"]
        if not can_manage(self.request.user, course): raise PermissionDenied("Instructor access required")
        obj = serializer.save(); audit(self.request, "enrollment.created", str(obj.id), course=course.id)

class AssignmentViewSet(viewsets.ModelViewSet):
    serializer_class, queryset = AssignmentSerializer, Assignment.objects.select_related("course")
    def get_queryset(self):
        return self.queryset.filter(course__enrollments__user=self.request.user, course__enrollments__active=True).distinct()
    def perform_create(self, serializer):
        course_id = self.request.data.get("course")
        course = Course.objects.get(pk=course_id)
        if not can_manage(self.request.user, course): raise PermissionDenied("Instructor access required")
        obj = serializer.save(course=course); audit(self.request, "assignment.created", str(obj.id))
    def perform_destroy(self, instance):
        if not can_manage(self.request.user, instance.course): raise PermissionDenied("Instructor access required")
        assignment_id = instance.id
        submission_files = list(instance.submissions.exclude(file="").values_list("file", flat=True))
        submission_count = instance.submissions.count()
        instance.delete()
        failed_file_deletions = 0
        for file_name in submission_files:
            try: default_storage.delete(file_name)
            except Exception: failed_file_deletions += 1
        audit(self.request, "assignment.deleted", str(assignment_id), submissions=submission_count, failed_file_deletions=failed_file_deletions)

class SubmissionViewSet(viewsets.ModelViewSet):
    serializer_class, queryset = SubmissionSerializer, Submission.objects.select_related("assignment", "student")
    def get_queryset(self):
        qs = self.queryset.filter(assignment__course__enrollments__user=self.request.user, assignment__course__enrollments__active=True)
        return qs if self.request.user.is_superuser else qs.filter(student=self.request.user)
    def perform_create(self, serializer):
        assignment = Assignment.objects.get(pk=self.request.data["assignment"])
        if course_role(self.request.user, assignment.course) != Enrollment.Role.STUDENT: raise PermissionDenied("Student access required")
        latest = Submission.objects.filter(assignment=assignment, student=self.request.user).aggregate(Max("version"))["version__max"] or 0
        file = serializer.validated_data["file"]
        obj = serializer.save(student=self.request.user, version=latest + 1, filename=file.name)
        audit(self.request, "submission.created", str(obj.id), assignment=assignment.id, version=obj.version)

class GradeViewSet(viewsets.ModelViewSet):
    serializer_class, queryset = GradeSerializer, Grade.objects.select_related("assignment", "student")
    def get_queryset(self):
        qs = self.queryset.filter(assignment__course__enrollments__user=self.request.user, assignment__course__enrollments__active=True)
        return qs if self.request.user.is_superuser else qs.filter(student=self.request.user)
    def perform_create(self, serializer): self._save(serializer)
    def perform_update(self, serializer): self._save(serializer)
    def _save(self, serializer):
        assignment = serializer.validated_data.get("assignment", serializer.instance.assignment)
        if not can_manage(self.request.user, assignment.course): raise PermissionDenied("Instructor access required")
        obj = serializer.save(grader=self.request.user); audit(self.request, "grade.updated", str(obj.id), score=str(obj.score))

class LtiToolViewSet(viewsets.ModelViewSet):
    serializer_class, queryset = LtiToolSerializer, LtiTool.objects.all()
    def perform_create(self, serializer):
        if not self.request.user.is_staff and not Enrollment.objects.filter(user=self.request.user, role=Enrollment.Role.INSTRUCTOR, active=True).exists(): raise PermissionDenied("Instructor access required")
        obj = serializer.save(created_by=self.request.user); audit(self.request, "lti.tool_created", str(obj.id))

class PlacementViewSet(viewsets.ModelViewSet):
    serializer_class, queryset = PlacementSerializer, ToolPlacement.objects.select_related("course", "tool")
    def perform_create(self, serializer):
        if not can_manage(self.request.user, serializer.validated_data["course"]): raise PermissionDenied("Instructor access required")
        obj = serializer.save(); audit(self.request, "lti.placement_created", str(obj.id))

class AuditEventViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class, queryset = AuditEventSerializer, AuditEvent.objects.all()
    permission_classes = [permissions.IsAdminUser]

class IsPlatformAdmin(permissions.BasePermission):
    def has_permission(self, request, view): return is_platform_admin(request.user)

class PlatformUserViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = PlatformUserSerializer
    queryset = User.objects.select_related("profile").all().order_by("id")
    permission_classes = [IsPlatformAdmin]

@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
def presign_submission(request):
    """Issue a short-lived direct-to-S3 upload contract; no file bytes touch the web worker."""
    assignment = Assignment.objects.filter(pk=request.data.get("assignment")).select_related("course").first()
    filename = os.path.basename(request.data.get("filename", ""))
    if not assignment or not filename or course_role(request.user, assignment.course) != Enrollment.Role.STUDENT:
        raise PermissionDenied("Valid student assignment and filename required")
    if not os.getenv("AWS_STORAGE_BUCKET_NAME"):
        return Response({"detail": "Object storage is not configured."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    key = f"submissions/{assignment.id}/{request.user.id}/{uuid4_hex()}/{filename}"
    client = boto3.client("s3", endpoint_url=os.getenv("AWS_S3_ENDPOINT_URL"), aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"), aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"), region_name=os.getenv("AWS_S3_REGION_NAME", "us-east-1"))
    bucket = os.environ["AWS_STORAGE_BUCKET_NAME"]
    try: client.head_bucket(Bucket=bucket)
    except ClientError: client.create_bucket(Bucket=bucket)
    post = client.generate_presigned_post(bucket, key, Conditions=[["content-length-range", 1, 100 * 1024 * 1024]], ExpiresIn=900)
    return Response({"key": key, "upload": post, "expires_in": 900})

@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
def finalize_submission(request):
    assignment = Assignment.objects.filter(pk=request.data.get("assignment")).select_related("course").first()
    key = request.data.get("key", "")
    prefix = f"submissions/{getattr(assignment, 'id', '')}/{request.user.id}/"
    if not assignment or not key.startswith(prefix) or course_role(request.user, assignment.course) != Enrollment.Role.STUDENT:
        raise PermissionDenied("Invalid upload completion request")
    latest = Submission.objects.filter(assignment=assignment, student=request.user).aggregate(Max("version"))["version__max"] or 0
    item = Submission(assignment=assignment, student=request.user, version=latest + 1, filename=os.path.basename(key)); item.file.name = key; item.save()
    audit(request, "submission.finalized", str(item.id), assignment=assignment.id, version=item.version)
    return Response(SubmissionSerializer(item).data, status=status.HTTP_201_CREATED)

def uuid4_hex():
    import uuid
    return uuid.uuid4().hex
