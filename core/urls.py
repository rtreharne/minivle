from django.urls import include, path
from django.contrib.auth import views as auth_views
from rest_framework.routers import DefaultRouter
from . import api, lti, views

router = DefaultRouter()
router.register("courses", api.CourseViewSet, basename="course")
router.register("enrollments", api.EnrollmentViewSet, basename="enrollment")
router.register("assignments", api.AssignmentViewSet, basename="assignment")
router.register("submissions", api.SubmissionViewSet, basename="submission")
router.register("grades", api.GradeViewSet, basename="grade")
router.register("lti-tools", api.LtiToolViewSet, basename="lti-tool")
router.register("placements", api.PlacementViewSet, basename="placement")
router.register("audit-events", api.AuditEventViewSet, basename="audit-event")
router.register("platform/people", api.PlatformUserViewSet, basename="platform-person")

urlpatterns = [
    path("docs/lti/", views.lti_developer_docs, name="lti-docs"),
    path("docs/lti/<slug:section>/", views.lti_developer_docs, name="lti-docs-section"),
    path("login/", auth_views.LoginView.as_view(template_name="core/auth/login.html"), name="login"), path("register/", views.register, name="register"), path("verify/<uuid:token>/", views.verify_email, name="verify-email"), path("verify/resend/", views.resend_verification, name="resend-verification"), path("logout/", views.sign_out, name="logout"), path("account/", views.account, name="account"),
    path("platform/masquerade/<int:user_id>/", views.start_student_masquerade, name="masquerade-start"), path("platform/masquerade/stop/", views.stop_masquerade, name="masquerade-stop"),
    path("", views.dashboard, name="dashboard"), path("courses/<int:course_id>/", views.course_home, name="course-home"), path("courses/<int:course_id>/tools/<int:placement_id>/", views.course_tool, name="course-tool"),
    path("courses/<int:course_id>/student-preview/", views.toggle_student_preview, name="student-preview"),
    path("courses/<int:course_id>/roster/", views.roster, name="roster"), path("courses/<int:course_id>/assignments/", views.assignments, name="assignments"),
    path("courses/<int:course_id>/assignments/<int:assignment_id>/", views.assignment_detail, name="course-assignment-detail"),
    path("courses/<int:course_id>/assignments/<int:assignment_id>/students/<int:student_id>/grade/", views.grade_submission, name="grade-submission"),
    path("courses/<int:course_id>/grades/", views.grades, name="grades"), path("courses/<int:course_id>/lti-tools/", views.lti_tools, name="lti-tools"), path("courses/<int:course_id>/lti-tools/add/", views.add_lti_tool, name="add-lti-tool"), path("courses/<int:course_id>/lti-tools/<int:placement_id>/", views.configure_lti_placement, name="configure-lti-placement"),
    path("platform/", views.platform_dashboard, name="platform-dashboard"), path("platform/lti/", views.platform_lti, name="platform-lti"), path("platform/lti/<int:tool_id>/", views.platform_lti_detail, name="platform-lti-detail"), path("platform/people/", views.platform_people, name="platform-people"), path("platform/people/<int:user_id>/", views.platform_person, name="platform-person"), path("platform/courses/", views.platform_courses, name="platform-courses"), path("platform/courses/<int:course_id>/", views.platform_course, name="platform-course"), path("platform/audit/", views.platform_audit, name="platform-audit"),
    path("api/v1/", include(router.urls)),
    path("api/v1/submission-uploads/presign/", api.presign_submission, name="presign-submission"), path("api/v1/submission-uploads/finalize/", api.finalize_submission, name="finalize-submission"),
    path("lti/jwks/", lti.jwks, name="lti-jwks"), path("lti/oidc/login/", lti.oidc_login, name="lti-oidc-login"), path("lti/authorize/", lti.lti13_authorize, name="lti13-authorize"), path("lti/launch/", lti.lti13_launch, name="lti13-launch"),
    path("lti/placements/<int:placement_id>/launch/", lti.lti13_start, name="lti13-start"),
    path("lti/deep-linking/courses/<int:course_id>/tools/<int:tool_id>/launch/", lti.lti13_deep_link_start, name="lti13-deep-link-start"), path("lti/deep-linking/return/", lti.lti13_deep_link_return, name="lti13-deep-link-return"),
    path("lti/1p1/placements/<int:placement_id>/launch/", lti.lti11_launch, name="lti11-launch"), path("lti/1p1/outcomes/", lti.lti11_outcomes, name="lti11-outcomes"),
    path("lti/nrps/<int:course_id>/memberships", lti.nrps, name="lti-nrps"), path("lti/ags/assignments/<int:assignment_id>/students/<int:student_id>/score", lti.ags_score, name="lti-ags-score"),
]
