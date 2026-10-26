from django.contrib.auth.models import User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from .models import Assignment, AuditEvent, Course, EmailVerificationToken, Enrollment, Grade, LtiTool, Submission, ToolPlacement

class LearningWorkflowTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user("learner", password="pass")
        self.instructor = User.objects.create_user("teacher", password="pass")
        self.course = Course.objects.create(code="MINI101", title="MiniVLE foundations", is_published=True)
        Enrollment.objects.create(course=self.course, user=self.student, role="student")
        Enrollment.objects.create(course=self.course, user=self.instructor, role="instructor")
        self.assignment = Assignment.objects.create(course=self.course, title="First file", instructions="Upload a PDF", published=True)

    def test_student_can_submit_versions_and_audit_is_created(self):
        self.client.login(username="learner", password="pass")
        url = reverse("course-assignment-detail", args=[self.course.id, self.assignment.id])
        response = self.client.post(url, {"file": SimpleUploadedFile("answer.txt", b"first")})
        self.assertRedirects(response, url)
        self.client.post(url, {"file": SimpleUploadedFile("answer.txt", b"second")})
        self.assertEqual(list(Submission.objects.values_list("version", flat=True)), [2, 1])
        self.assertTrue(AuditEvent.objects.filter(action="submission.created").exists())

    def test_instructor_can_grade_and_student_cannot(self):
        self.client.login(username="teacher", password="pass")
        url = reverse("grade-submission", args=[self.course.id, self.assignment.id, self.student.id])
        response = self.client.post(url, {"score": "82.5", "feedback": "Well structured"})
        self.assertRedirects(response, reverse("grades", args=[self.course.id]))
        self.assertEqual(Grade.objects.get().score, 82.5)
        self.client.login(username="learner", password="pass")
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_api_lists_only_enrolled_courses(self):
        self.client.login(username="learner", password="pass")
        response = self.client.get("/api/v1/courses/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 1)

    def test_lti11_launch_is_signed_form_post(self):
        tool = LtiTool.objects.create(name="Legacy tool", launch_url="https://tool.example/launch", lti_version="1.1", consumer_key="key", shared_secret="secret")
        placement = ToolPlacement.objects.create(course=self.course, tool=tool, title="Legacy")
        self.client.login(username="learner", password="pass")
        response = self.client.get(reverse("lti11-launch", args=[placement.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "oauth_signature")

    def test_registration_requires_email_verification(self):
        response = self.client.post("/register/", {"first_name": "New", "last_name": "Learner", "email": "new@example.test", "username": "newlearner", "password1": "Strong-pass-123", "password2": "Strong-pass-123"})
        self.assertEqual(response.status_code, 200)
        user = User.objects.get(username="newlearner")
        self.assertFalse(user.is_active)
        self.assertEqual(len(mail.outbox), 1)
        token = EmailVerificationToken.objects.get(user=user)
        response = self.client.get(f"/verify/{token.token}/")
        self.assertRedirects(response, "/")
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertTrue(user.profile.email_verified)

    def test_platform_dashboard_is_admin_only(self):
        self.client.login(username="learner", password="pass")
        self.assertEqual(self.client.get("/platform/").status_code, 302)
        self.instructor.profile.is_platform_admin = True
        self.instructor.profile.save()
        self.client.login(username="teacher", password="pass")
        self.assertEqual(self.client.get("/platform/").status_code, 200)

    def test_instructor_student_preview_can_create_test_submission(self):
        self.client.login(username="teacher", password="pass")
        preview_url = f"/courses/{self.course.id}/student-preview/"
        self.client.post(preview_url, {"next": f"/courses/{self.course.id}/"})
        page = self.client.get(f"/courses/{self.course.id}/assignments/{self.assignment.id}/")
        self.assertContains(page, "Student preview")
        self.assertContains(page, "Upload test file")
        self.client.post(f"/courses/{self.course.id}/assignments/{self.assignment.id}/", {"file": SimpleUploadedFile("test.txt", b"test")})
        self.assertTrue(Submission.objects.filter(assignment=self.assignment, student=self.instructor).exists())
        self.assertTrue(AuditEvent.objects.filter(action="submission.test_created").exists())

    def test_platform_admin_can_masquerade_as_active_student_and_restore(self):
        self.instructor.profile.is_platform_admin = True
        self.instructor.profile.save()
        self.client.login(username="teacher", password="pass")
        start = reverse("masquerade-start", args=[self.student.id])
        response = self.client.post(start)
        self.assertRedirects(response, reverse("dashboard"))
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "Viewing as")
        self.assertEqual(str(self.client.session["_auth_user_id"]), str(self.student.id))
        self.assertTrue(AuditEvent.objects.filter(action="platform.masquerade_started", actor=self.instructor, target=str(self.student.id)).exists())

        response = self.client.post(reverse("masquerade-stop"))
        self.assertRedirects(response, reverse("platform-people"))
        self.assertEqual(str(self.client.session["_auth_user_id"]), str(self.instructor.id))
        self.assertTrue(AuditEvent.objects.filter(action="platform.masquerade_ended", actor=self.instructor, target=str(self.student.id)).exists())

    def test_masquerade_rejects_inactive_or_non_student_accounts(self):
        self.instructor.profile.is_platform_admin = True
        self.instructor.profile.save()
        self.student.is_active = False
        self.student.save()
        self.client.login(username="teacher", password="pass")
        response = self.client.post(reverse("masquerade-start", args=[self.student.id]))
        self.assertRedirects(response, reverse("platform-person", args=[self.student.id]))
        self.assertEqual(str(self.client.session["_auth_user_id"]), str(self.instructor.id))

    def test_public_lti_developer_docs_and_documented_routes_are_available(self):
        for section in ("", "getting-started/", "registration/", "launches/", "services/", "testing/", "troubleshooting/"):
            response = self.client.get(f"/docs/lti/{section}")
            self.assertEqual(response.status_code, 200)
        response = self.client.get("/docs/lti/getting-started/")
        self.assertContains(response, reverse("lti-jwks"))
        self.assertContains(response, reverse("lti-oidc-login"))
        self.assertContains(response, reverse("lti13-launch"))
