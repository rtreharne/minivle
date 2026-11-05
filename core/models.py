import uuid
from django.conf import settings
from django.contrib.auth.models import User
from django.db import models

class Timestamped(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta: abstract = True

class Course(Timestamped):
    code = models.CharField(max_length=32, unique=True)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    is_published = models.BooleanField(default=False)
    is_archived = models.BooleanField(default=False)
    def __str__(self): return f"{self.code} — {self.title}"
    class Meta: ordering = ["code"]

class Enrollment(Timestamped):
    class Role(models.TextChoices): STUDENT = "student", "Student"; INSTRUCTOR = "instructor", "Instructor"; ADMIN = "admin", "Administrator"
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="enrollments")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="enrollments")
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.STUDENT)
    active = models.BooleanField(default=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["course", "user"], name="unique_course_enrollment")]
        indexes = [models.Index(fields=["course", "role", "active"])]

class Assignment(Timestamped):
    class SubmissionType(models.TextChoices):
        FILE_UPLOAD = "upload", "File upload"
        LTI_TOOL = "lti", "External LTI tool"
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="assignments")
    title = models.CharField(max_length=200)
    instructions = models.TextField()
    due_at = models.DateTimeField(null=True, blank=True)
    points_possible = models.DecimalField(max_digits=8, decimal_places=2, default=100)
    published = models.BooleanField(default=False)
    submission_type = models.CharField(max_length=12, choices=SubmissionType.choices, default=SubmissionType.FILE_UPLOAD)
    class Meta: ordering = ["due_at", "id"]

class Submission(Timestamped):
    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name="submissions")
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="submissions")
    version = models.PositiveIntegerField(default=1)
    file = models.FileField(upload_to="submissions/%Y/%m/%d/")
    filename = models.CharField(max_length=255)
    submitted_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ["-version"]
        constraints = [models.UniqueConstraint(fields=["assignment", "student", "version"], name="unique_submission_version")]
        indexes = [models.Index(fields=["assignment", "student"])]

class Grade(Timestamped):
    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name="grades")
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="grades")
    score = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    feedback = models.TextField(blank=True)
    grader = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="graded_work")
    class Meta:
        constraints = [models.UniqueConstraint(fields=["assignment", "student"], name="unique_assignment_grade")]

class LtiTool(Timestamped):
    name = models.CharField(max_length=120)
    navigation_label = models.CharField(max_length=120, blank=True)
    launch_url = models.URLField()
    lti_version = models.CharField(max_length=8, choices=[("1.1", "LTI 1.1"), ("1.3", "LTI 1.3")])
    consumer_key = models.CharField(max_length=255, blank=True)
    shared_secret = models.CharField(max_length=255, blank=True)  # encrypt via a field/KMS in production
    issuer = models.URLField(blank=True)
    client_id = models.CharField(max_length=255, blank=True)
    deployment_id = models.CharField(max_length=255, blank=True)
    jwks_url = models.URLField(blank=True)
    oidc_initiation_url = models.URLField(blank=True)
    deployment_registration_url = models.URLField(blank=True)
    scopes = models.JSONField(default=list, blank=True)
    api_scopes = models.JSONField(default=list, blank=True)
    active = models.BooleanField(default=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    def __str__(self): return self.name

class ToolPlacement(Timestamped):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="tool_placements")
    tool = models.ForeignKey(LtiTool, on_delete=models.CASCADE, related_name="placements")
    title = models.CharField(max_length=120)
    assignment = models.ForeignKey(Assignment, null=True, blank=True, on_delete=models.SET_NULL)
    visible_in_course_navigation = models.BooleanField(default=True)

class LtiDeployment(Timestamped):
    tool = models.ForeignKey(LtiTool, on_delete=models.CASCADE, related_name="deployments")
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="lti_deployments")
    deployment_id = models.CharField(max_length=255, unique=True)
    active = models.BooleanField(default=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["tool", "course"], name="unique_lti_tool_course_deployment")]

class LtiServiceToken(models.Model):
    tool = models.ForeignKey(LtiTool, on_delete=models.CASCADE, related_name="service_tokens")
    deployment = models.ForeignKey(LtiDeployment, on_delete=models.CASCADE, related_name="service_tokens")
    token_hash = models.CharField(max_length=64, unique=True)
    assertion_jti = models.CharField(max_length=255, unique=True)
    launch_jti = models.CharField(max_length=255, unique=True, null=True, blank=True)
    scopes = models.JSONField(default=list)
    course_ids = models.JSONField(default=list)
    is_platform_admin = models.BooleanField(default=False)
    launch_role = models.CharField(max_length=16, default="student")
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="lti_service_tokens")
    launch_course = models.ForeignKey(Course, null=True, blank=True, on_delete=models.CASCADE, related_name="lti_service_tokens")

class Rubric(Timestamped):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="rubrics")
    title = models.CharField(max_length=255)
    free_form_criterion_comments = models.BooleanField(default=False)
    hide_score_total = models.BooleanField(default=False)
    reusable = models.BooleanField(default=False)

class RubricCriterion(models.Model):
    rubric = models.ForeignKey(Rubric, on_delete=models.CASCADE, related_name="criteria")
    criterion_id = models.CharField(max_length=80)
    description = models.CharField(max_length=255)
    long_description = models.TextField(blank=True)
    points = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    ratings = models.JSONField(default=list, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["rubric", "criterion_id"], name="unique_rubric_criterion")]

class RubricAssociation(Timestamped):
    rubric = models.ForeignKey(Rubric, on_delete=models.CASCADE, related_name="associations")
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="rubric_associations")
    assignment = models.ForeignKey(Assignment, null=True, blank=True, on_delete=models.CASCADE, related_name="rubric_associations")
    use_for_grading = models.BooleanField(default=False)
    hide_score_total = models.BooleanField(default=False)
    purpose = models.CharField(max_length=32, default="grading")

class RubricAssessment(Timestamped):
    association = models.ForeignKey(RubricAssociation, on_delete=models.CASCADE, related_name="assessments")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="rubric_assessments")
    assessor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="rubric_assessments_given")
    assessment_type = models.CharField(max_length=32, default="grading")
    score = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    data = models.JSONField(default=dict, blank=True)
    comments = models.TextField(blank=True)
    provisional = models.BooleanField(default=False)
    final = models.BooleanField(default=False)
    graded_anonymously = models.BooleanField(default=False)

class CanvasUploadGrant(models.Model):
    token_hash = models.CharField(max_length=64, unique=True)
    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name="canvas_upload_grants")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="canvas_upload_grants")
    filename = models.CharField(max_length=255)
    size = models.PositiveBigIntegerField()
    content_type = models.CharField(max_length=255, default="application/octet-stream")
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class AuditEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=80)
    target = models.CharField(max_length=255)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: ordering = ["-created_at"]; indexes = [models.Index(fields=["action", "created_at"])]

class UserProfile(Timestamped):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    email_verified = models.BooleanField(default=False)
    is_platform_admin = models.BooleanField(default=False)
    username = models.CharField(max_length=150, blank=True, unique=True, null=True)
    student_id = models.CharField(max_length=9, blank=True, unique=True, null=True)
    sis_login_id = models.CharField(max_length=160, blank=True, unique=True, null=True)
    def __str__(self): return self.user.username

class BrandSettings(models.Model):
    logo = models.FileField(upload_to="branding/", blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self): return "Brand settings"

class EmailVerificationToken(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="verification_tokens")
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
