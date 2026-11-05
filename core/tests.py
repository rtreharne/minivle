from django.contrib.auth.models import User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from urllib.parse import parse_qs, urlparse
import jwt
from .models import Assignment, AuditEvent, Course, EmailVerificationToken, Enrollment, Grade, LtiDeployment, LtiServiceToken, LtiTool, Rubric, RubricAssociation, RubricAssessment, Submission, ToolPlacement
import hashlib
import secrets
import io
import json
from django.utils import timezone
from datetime import timedelta
from unittest.mock import patch
from django.conf import settings
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from jwt.algorithms import RSAAlgorithm
from .network import server_fetch_url
from django.test import override_settings


class LocalDevelopmentNetworkingTests(TestCase):
    @override_settings(DEBUG=True)
    def test_server_fetch_routes_localhost_through_docker_host_gateway(self):
        self.assertEqual(
            server_fetch_url("http://localhost:8002/lti/config.json"),
            "http://host.docker.internal:8002/lti/config.json",
        )

    @override_settings(DEBUG=False)
    def test_server_fetch_preserves_localhost_outside_development(self):
        url = "http://localhost:8002/lti/config.json"
        self.assertEqual(server_fetch_url(url), url)

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

    def test_platform_admin_can_delete_lti13_registration_and_related_records(self):
        self.instructor.profile.is_platform_admin = True
        self.instructor.profile.save()
        tool = LtiTool.objects.create(name="Disposable Counter", launch_url="https://tool.example/launch", oidc_initiation_url="https://tool.example/login", jwks_url="https://tool.example/jwks", lti_version="1.3", client_id="disposable-client")
        deployment = LtiDeployment.objects.create(tool=tool, course=self.course, deployment_id="disposable-deployment")
        placement = ToolPlacement.objects.create(course=self.course, tool=tool, title="Disposable Counter")
        self.client.login(username="teacher", password="pass")
        detail = reverse("platform-lti-detail", args=[tool.id])
        response = self.client.post(detail, {"action": "delete"})
        self.assertRedirects(response, detail)
        self.assertTrue(LtiTool.objects.filter(pk=tool.id).exists())
        response = self.client.post(detail, {"action": "delete", "confirm_delete": "on"})
        self.assertRedirects(response, reverse("platform-lti"))
        self.assertFalse(LtiTool.objects.filter(pk=tool.id).exists())
        self.assertFalse(LtiDeployment.objects.filter(pk=deployment.id).exists())
        self.assertFalse(ToolPlacement.objects.filter(pk=placement.id).exists())
        self.assertTrue(AuditEvent.objects.filter(action="lti13.registration_deleted", target=str(tool.id)).exists())

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

    def test_scoped_api_reference_is_public_and_complete(self):
        response = self.client.get("/docs/api/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "url:GET|/api/v1/courses")
        self.assertContains(response, "/rubric_assessments")
        self.assertContains(response, "launch_token")

    def test_lti13_course_placement_creates_a_signed_resource_link_launch(self):
        tool = LtiTool.objects.create(name="Counter", launch_url="http://localhost:8002/lti/launch", oidc_initiation_url="http://localhost:8002/oidc/login", jwks_url="http://localhost:8002/.well-known/jwks.json", lti_version="1.3", client_id="counter-client", active=True)
        LtiDeployment.objects.create(tool=tool, course=self.course, deployment_id="counter-deployment")
        placement = ToolPlacement.objects.create(course=self.course, tool=tool, title="Counter")
        self.client.login(username="learner", password="pass")
        response = self.client.get(reverse("lti13-start", args=[placement.id]))
        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlparse(response["Location"]).query)
        self.assertEqual(query["client_id"], ["counter-client"])
        response = self.client.get(reverse("lti13-authorize"), {"client_id": "counter-client", "redirect_uri": tool.launch_url, "state": "state", "nonce": "nonce", "lti_message_hint": query["lti_message_hint"][0]})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "id_token")
        self.assertTrue(AuditEvent.objects.filter(action="lti13.launch", target=str(tool.id)).exists())

    def test_lti13_placement_is_available_inside_the_course_canvas(self):
        tool = LtiTool.objects.create(name="Counter", launch_url="http://localhost:8002/lti/launch", oidc_initiation_url="http://localhost:8002/oidc/login", jwks_url="http://localhost:8002/.well-known/jwks.json", lti_version="1.3", client_id="counter-embedded", active=True)
        LtiDeployment.objects.create(tool=tool, course=self.course, deployment_id="counter-embedded-deployment")
        placement = ToolPlacement.objects.create(course=self.course, tool=tool, title="Counter")
        self.client.login(username="learner", password="pass")
        response = self.client.get(reverse("course-tool", args=[self.course.id, placement.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "lti-tool-frame")
        self.assertContains(response, reverse("lti13-start", args=[placement.id]))

    def test_lti13_deep_linking_request_is_instructor_only_and_signed(self):
        tool = LtiTool.objects.create(name="Counter", launch_url="http://localhost:8002/lti/launch", oidc_initiation_url="http://localhost:8002/oidc/login", jwks_url="http://localhost:8002/.well-known/jwks.json", lti_version="1.3", client_id="counter-deep-link", active=True)
        LtiDeployment.objects.create(tool=tool, course=self.course, deployment_id="counter-deep-link-deployment")
        self.client.login(username="learner", password="pass")
        self.assertEqual(self.client.get(reverse("lti13-deep-link-start", args=[self.course.id, tool.id])).status_code, 400)
        self.client.login(username="teacher", password="pass")
        start = self.client.get(reverse("lti13-deep-link-start", args=[self.course.id, tool.id]))
        query = parse_qs(urlparse(start["Location"]).query)
        response = self.client.get(reverse("lti13-authorize"), {"client_id": tool.client_id, "redirect_uri": tool.launch_url, "state": "state", "nonce": "nonce", "lti_message_hint": query["lti_message_hint"][0]})
        token = response.content.decode().split('name="id_token" value="', 1)[1].split('"', 1)[0]
        claims = jwt.decode(token, options={"verify_signature": False})
        self.assertEqual(claims["https://purl.imsglobal.org/spec/lti/claim/message_type"], "LtiDeepLinkingRequest")
        self.assertEqual(claims["https://purl.imsglobal.org/spec/lti-dl/claim/deep_linking_settings"]["accept_types"], ["ltiResourceLink"])


class CanvasCompatibleApiTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user("canvas-learner", password="pass")
        self.teacher = User.objects.create_user("canvas-teacher", password="pass")
        self.course = Course.objects.create(code="CAN101", title="Canvas API")
        self.other_course = Course.objects.create(code="CAN102", title="Connected course")
        Enrollment.objects.create(course=self.course, user=self.student, role="student")
        Enrollment.objects.create(course=self.course, user=self.teacher, role="instructor")
        Enrollment.objects.create(course=self.other_course, user=self.student, role="student")
        self.assignment = Assignment.objects.create(course=self.other_course, title="Essay", instructions="Write", published=True)
        self.token = secrets.token_urlsafe(32)
        api_scopes = ["url:GET|/api/v1/courses", "url:GET|/api/v1/users/self", "url:GET|/api/v1/courses/:course_id/assignments",
                      "url:GET|/api/v1/courses/:course_id/assignments/:assignment_id/submissions",
                      "url:GET|/api/v1/courses/:course_id/assignments/:assignment_id/submissions/:user_id",
                      "url:GET|/api/v1/courses/:course_id/students/submissions",
                      "url:POST|/api/v1/courses/:course_id/assignments/:assignment_id/submissions/:user_id/files",
                      "url:POST|/api/v1/courses/:course_id/assignments/:assignment_id/submissions"]
        self.tool = LtiTool.objects.create(name="Canvas API", launch_url="https://tool.test/launch", lti_version="1.3", client_id="canvas-api-client", active=True, api_scopes=api_scopes)
        self.deployment = LtiDeployment.objects.create(tool=self.tool, course=self.course, deployment_id="canvas-deployment")
        LtiServiceToken.objects.create(tool=self.tool, deployment=self.deployment, user=self.student, launch_course=self.course,
            token_hash=hashlib.sha256(self.token.encode()).hexdigest(), assertion_jti="test-assertion", launch_jti="test-launch",
            scopes=api_scopes,
            course_ids=[self.course.id, self.other_course.id], expires_at=timezone.now() + timedelta(minutes=5))

    def get_api(self, path):
        return self.client.get(path, HTTP_AUTHORIZATION=f"Bearer {self.token}")

    def test_token_exchange_binds_launch_user_and_transitive_courses_and_rejects_replay(self):
        bridge_user = User.objects.create_user("canvas-bridge", password="pass")
        third_course = Course.objects.create(code="CAN103", title="Three hops")
        Enrollment.objects.create(course=self.other_course, user=bridge_user, role="instructor")
        Enrollment.objects.create(course=third_course, user=bridge_user, role="student")
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private_pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        public_jwk = json.loads(RSAAlgorithm.to_jwk(private.public_key()))
        public_jwk["kid"] = "tool-key"
        self.tool.jwks_url = "https://tool.test/jwks"
        self.tool.api_scopes = ["url:GET|/api/v1/courses", "url:GET|/api/v1/users/self"]
        self.tool.save(update_fields=["jwks_url", "api_scopes"])
        now = int(timezone.now().timestamp())
        launch_claims = {"iss": settings.LTI_ISSUER, "aud": self.tool.client_id, "sub": str(self.student.id), "iat": now, "exp": now + 180,
                         "jti": "launch-exchange-test", "https://purl.imsglobal.org/spec/lti/claim/deployment_id": self.deployment.deployment_id,
                         "https://purl.imsglobal.org/spec/lti/claim/message_type": "LtiResourceLinkRequest",
                         "https://purl.imsglobal.org/spec/lti/claim/context": {"id": str(self.course.id)}}
        launch_token = jwt.encode(launch_claims, settings.LTI_PRIVATE_KEY, algorithm="RS256")
        def assertion(jti):
            return jwt.encode({"iss": self.tool.client_id, "sub": self.tool.client_id, "aud": "http://testserver/lti/token/", "iat": now,
                               "exp": now + 60, "jti": jti,
                               "https://purl.imsglobal.org/spec/lti/claim/deployment_id": self.deployment.deployment_id}, private_pem,
                              algorithm="RS256", headers={"kid": "tool-key"})
        with patch("core.lti.urlopen", return_value=io.BytesIO(json.dumps({"keys": [public_jwk]}).encode())):
            response = self.client.post("/lti/token/", {"grant_type": "client_credentials", "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                "client_assertion": assertion("assertion-first"), "launch_token": launch_token, "scope": "url:GET|/api/v1/courses url:GET|/api/v1/users/self"})
            self.assertEqual(response.status_code, 200, response.content)
            issued = LtiServiceToken.objects.get(launch_jti="launch-exchange-test")
            self.assertEqual(issued.user, self.student)
            self.assertEqual(set(issued.course_ids), {self.course.id, self.other_course.id, third_course.id})
            replay = self.client.post("/lti/token/", {"grant_type": "client_credentials", "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                "client_assertion": assertion("assertion-second"), "launch_token": launch_token, "scope": "url:GET|/api/v1/courses"})
            self.assertEqual(replay.status_code, 401)

    def test_scoped_token_reads_current_user_and_reachable_course_assignments(self):
        self.assertEqual(self.get_api("/api/v1/users/self").json()["id"], self.student.id)
        courses = self.get_api("/api/v1/courses").json()
        self.assertEqual({course["id"] for course in courses}, {self.course.id, self.other_course.id})
        assignments = self.get_api(f"/api/v1/courses/{self.other_course.id}/assignments").json()
        self.assertEqual(assignments[0]["name"], "Essay")

    def test_scope_and_course_set_are_enforced(self):
        self.assertEqual(self.client.get(f"/api/v1/courses/{self.other_course.id}/assignments", HTTP_AUTHORIZATION=f"Bearer {self.token}").status_code, 200)
        unrelated = Course.objects.create(code="CAN999", title="Unrelated")
        self.assertEqual(self.get_api(f"/api/v1/courses/{unrelated.id}/assignments").status_code, 404)
        self.assertEqual(self.client.post("/api/v1/courses", HTTP_AUTHORIZATION=f"Bearer {self.token}").status_code, 401)
        self.tool.api_scopes = ["url:GET|/api/v1/users/self"]
        self.tool.save(update_fields=["api_scopes"])
        self.assertEqual(self.get_api(f"/api/v1/courses/{self.other_course.id}/assignments").status_code, 401)

    def test_student_token_cannot_read_another_student_submission(self):
        other = User.objects.create_user("canvas-other", password="pass")
        Enrollment.objects.create(course=self.other_course, user=other, role="student")
        response = self.get_api(f"/api/v1/courses/{self.other_course.id}/assignments/{self.assignment.id}/submissions/{other.id}")
        self.assertEqual(response.status_code, 404)

    def test_platform_admin_token_can_list_courses_outside_the_launch_graph(self):
        unrelated = Course.objects.create(code="CAN999", title="Admin-only course")
        self.student.profile.is_platform_admin = True
        self.student.profile.save(update_fields=["is_platform_admin", "updated_at"])
        LtiServiceToken.objects.filter(launch_jti="test-launch").update(is_platform_admin=True, course_ids=[self.course.id, self.other_course.id, unrelated.id])
        response = self.get_api("/api/v1/courses")
        self.assertEqual(response.status_code, 200)
        self.assertIn(unrelated.id, {course["id"] for course in response.json()})

    def test_canvas_students_submissions_lists_grades_across_assignments(self):
        second = Assignment.objects.create(course=self.other_course, title="Quiz", instructions="", published=True)
        Grade.objects.create(assignment=second, student=self.student, score=8)
        response = self.get_api(f"/api/v1/courses/{self.other_course.id}/students/submissions?assignment_ids%5B%5D={second.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)
        self.assertEqual(response.json()[0]["score"], 8.0)

    def test_canvas_multipart_submission_file_can_be_finalized_with_file_id(self):
        upload = self.client.post(f"/api/v1/courses/{self.other_course.id}/assignments/{self.assignment.id}/submissions/{self.student.id}/files",
            {"file": SimpleUploadedFile("essay.txt", b"work")}, HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertEqual(upload.status_code, 201)
        submitted = self.client.post(f"/api/v1/courses/{self.other_course.id}/assignments/{self.assignment.id}/submissions",
            {"submission": {"file_ids": [upload.json()["id"]]}}, content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertEqual(submitted.status_code, 201)
        self.assertEqual(Submission.objects.filter(assignment=self.assignment, student=self.student).count(), 1)

    def test_canvas_submission_file_upload_handshake(self):
        from urllib.parse import urlparse
        init = self.client.post(f"/api/v1/courses/{self.other_course.id}/assignments/{self.assignment.id}/submissions/self/files",
            {"name": "handshake.txt", "size": "4", "content_type": "text/plain"}, HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertEqual(init.status_code, 200)
        upload_path = urlparse(init.json()["upload_url"]).path
        uploaded = self.client.post(upload_path, {"file": SimpleUploadedFile("handshake.txt", b"work")})
        self.assertEqual(uploaded.status_code, 303)
        complete = self.client.get(uploaded["Location"], HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertEqual(complete.status_code, 200)
        self.assertEqual(complete.json()["filename"], "handshake.txt")
        replay = self.client.post(upload_path, {"file": SimpleUploadedFile("handshake.txt", b"work")})
        self.assertEqual(replay.status_code, 403)

    def test_instructor_launch_can_read_students_across_reachable_courses(self):
        self.token = secrets.token_urlsafe(32)
        scope = "url:GET|/api/v1/courses/:course_id/assignments/:assignment_id/submissions"
        self.tool.api_scopes = [scope]
        self.tool.save(update_fields=["api_scopes"])
        LtiServiceToken.objects.filter(launch_jti="test-launch").update(user=self.teacher, launch_role="instructor",
            token_hash=hashlib.sha256(self.token.encode()).hexdigest(), scopes=[scope])
        response = self.get_api(f"/api/v1/courses/{self.other_course.id}/assignments/{self.assignment.id}/submissions")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["user_id"], self.student.id)

    def test_rubric_assessment_uses_criterion_data(self):
        self.tool.api_scopes = ["url:POST|/api/v1/courses/:course_id/rubrics", "url:POST|/api/v1/courses/:course_id/rubric_associations/:rubric_association_id/rubric_assessments"]
        self.tool.save(update_fields=["api_scopes"])
        LtiServiceToken.objects.filter(launch_jti="test-launch").update(scopes=self.tool.api_scopes)
        self.token = secrets.token_urlsafe(32)
        LtiServiceToken.objects.filter(launch_jti="test-launch").update(token_hash=hashlib.sha256(self.token.encode()).hexdigest(), user=self.teacher, launch_role="instructor")
        rubric_response = self.client.post(f"/api/v1/courses/{self.course.id}/rubrics", {"rubric": {"title": "Essay rubric", "criteria": [{"id": "clarity", "description": "Clarity", "points": 5, "ratings": []}]}}, content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertEqual(rubric_response.status_code, 201)
        rubric = Rubric.objects.get()
        association = RubricAssociation.objects.create(rubric=rubric, course=self.course, assignment=Assignment.objects.create(course=self.course, title="Short", instructions="", published=True))
        response = self.client.post(f"/api/v1/courses/{self.course.id}/rubric_associations/{association.id}/rubric_assessments", {"rubric_assessment": {"user_id": self.student.id, "score": 4, "data": {"clarity": {"points": 4, "comments": "Clear"}}}}, content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertEqual(response.status_code, 201)
        assessment = RubricAssessment.objects.get()
        self.assertEqual(assessment.data["clarity"]["points"], 4)
