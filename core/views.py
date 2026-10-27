import json
import secrets
import hashlib
import hmac
from datetime import timedelta
from urllib.request import Request, urlopen
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.db.models import Max, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from .api import audit
from .forms import AssignmentForm, CourseForm, GradeForm, LtiConfigImportForm, LtiDeploymentForm, LtiRegistrationForm, LtiToolForm, PlacementConfigurationForm, ProfileForm, RegistrationForm, SubmissionForm, ToolPlacementForm
from .models import Assignment, AuditEvent, Course, EmailVerificationToken, Enrollment, Grade, LtiDeployment, LtiTool, Submission, ToolPlacement
from .permissions import can_manage, course_role, enrolled_or_404, is_platform_admin, student_preview_active

platform_admin_required = user_passes_test(is_platform_admin, login_url="dashboard")

def sync_tool_deployment(tool, deployment):
    """Register a deployment with a tool's opted-in, signed management endpoint."""
    if not tool.deployment_registration_url:
        return
    if not settings.LTI_DEPLOYMENT_SYNC_SECRET:
        raise ValueError("Deployment sync is not configured on this MiniVLE instance.")
    payload = json.dumps({"issuer": settings.LTI_ISSUER, "client_id": tool.client_id, "deployment_id": deployment.deployment_id}, separators=(",", ":")).encode()
    signature = hmac.new(settings.LTI_DEPLOYMENT_SYNC_SECRET.encode(), payload, hashlib.sha256).hexdigest()
    request = Request(tool.deployment_registration_url, data=payload, method="POST", headers={"Content-Type": "application/json", "X-MiniVLE-Deployment-Signature": signature})
    with urlopen(request, timeout=5) as response:
        if response.status not in (200, 201, 204):
            raise ValueError("The tool rejected deployment registration.")

def send_verification(request, user):
    token = EmailVerificationToken.objects.create(user=user, expires_at=timezone.now() + timedelta(hours=24))
    link = request.build_absolute_uri(f"/verify/{token.token}/")
    send_mail("Verify your MiniVLE account", f"Welcome to MiniVLE. Verify your email within 24 hours: {link}", None, [user.email])

def register(request):
    if request.user.is_authenticated: return redirect("dashboard")
    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save(commit=False); user.is_active = False; user.save()
        send_verification(request, user)
        return render(request, "core/auth/check_email.html", {"email": user.email})
    return render(request, "core/auth/register.html", {"form": form})

def verify_email(request, token):
    item = get_object_or_404(EmailVerificationToken, token=token, used_at__isnull=True)
    if item.expires_at < timezone.now(): return render(request, "core/auth/verification_expired.html", {"email": item.user.email})
    item.used_at = timezone.now(); item.save(update_fields=["used_at"])
    item.user.is_active = True; item.user.save(update_fields=["is_active"])
    item.user.profile.email_verified = True; item.user.profile.save(update_fields=["email_verified", "updated_at"])
    login(request, item.user); messages.success(request, "Email verified. Your MiniVLE account is ready.")
    return redirect("dashboard")

def resend_verification(request):
    if request.method == "POST":
        user = User.objects.filter(email__iexact=request.POST.get("email", ""), is_active=False).first()
        if user: send_verification(request, user)
        messages.success(request, "If that account needs verification, a new link has been sent.")
    return render(request, "core/auth/resend.html")

def lti_developer_docs(request, section="overview"):
    """Public LTI documentation with URLs derived from the active environment."""
    sections = {
        "overview": ("LTI developer guide", "Start here"),
        "getting-started": ("Getting started", "Local development"),
        "registration": ("Tool registration", "Configuration"),
        "launches": ("OIDC and launches", "Authentication"),
        "services": ("LTI services", "Roster and grades"),
        "testing": ("Testing tools", "Verification"),
        "troubleshooting": ("Troubleshooting", "Diagnostics"),
    }
    if section not in sections:
        raise Http404
    origin = f"{request.scheme}://{request.get_host()}"
    return render(request, "core/docs/lti.html", {
        "section": section, "section_title": sections[section][0],
        "section_label": sections[section][1], "origin": origin,
    })

@login_required
def account(request):
    form = ProfileForm(request.POST or None, instance=request.user)
    if request.method == "POST" and form.is_valid(): form.save(); messages.success(request, "Profile updated."); return redirect("account")
    return render(request, "core/account.html", {"form": form})

@login_required
def sign_out(request):
    logout(request); return redirect("login")

@platform_admin_required
def start_student_masquerade(request, user_id):
    """Enter an active learner's account without ever needing their password."""
    if request.method != "POST":
        raise Http404
    actor = request.user
    person = get_object_or_404(User.objects.select_related("profile"), pk=user_id)
    is_student = person.enrollments.filter(role=Enrollment.Role.STUDENT, active=True).exists()
    if person == actor or not person.is_active or not is_student or is_platform_admin(person):
        messages.error(request, "That account is not available for student masquerade.")
        return redirect("platform-person", user_id=person.id)
    AuditEvent.objects.create(
        actor=actor,
        action="platform.masquerade_started",
        target=str(person.id),
        metadata={"student_username": person.username},
    )
    # Changing authenticated users causes Django to rotate/flush the session.
    # Store the authorised origin only after login so it survives that rotation.
    login(request, person)
    request.session["masquerade_original_user_id"] = actor.id
    request.session.pop("student_preview_course", None)
    messages.success(request, f"You are viewing MiniVLE as {person.get_full_name() or person.username}.")
    return redirect("dashboard")

@login_required
def stop_masquerade(request):
    """Restore the original platform administrator account."""
    if request.method != "POST":
        raise Http404
    original_user_id = request.session.get("masquerade_original_user_id")
    original = User.objects.select_related("profile").filter(pk=original_user_id).first()
    if not original or not original.is_active or not is_platform_admin(original):
        logout(request)
        messages.error(request, "Your administrator session is no longer available. Please sign in again.")
        return redirect("login")
    student = request.user
    AuditEvent.objects.create(
        actor=original,
        action="platform.masquerade_ended",
        target=str(student.id),
        metadata={"student_username": student.username},
    )
    login(request, original)
    request.session.pop("masquerade_original_user_id", None)
    request.session.pop("student_preview_course", None)
    messages.success(request, "Platform administrator view restored.")
    return redirect("platform-people")

@login_required
def dashboard(request):
    courses = Course.objects.filter(enrollments__user=request.user, enrollments__active=True).distinct()
    assignments = Assignment.objects.filter(course__in=courses, published=True, due_at__gte=timezone.now()).order_by("due_at")[:6]
    return render(request, "core/dashboard.html", {"courses": courses, "upcoming": assignments, "platform_admin": is_platform_admin(request.user)})

@login_required
def course_home(request, course_id):
    course = enrolled_or_404(request.user, get_object_or_404(Course, pk=course_id))
    return render(request, "core/course_home.html", {"course": course, "assignments": course.assignments.filter(published=True)[:5], "can_manage": can_manage(request.user, course), "preview": student_preview_active(request, course)})

@login_required
def course_tool(request, course_id, placement_id):
    """Keep an LTI 1.3 navigation placement within the course content canvas."""
    course = enrolled_or_404(request.user, get_object_or_404(Course, pk=course_id))
    placement = get_object_or_404(ToolPlacement.objects.select_related("tool"), pk=placement_id, course=course, tool__lti_version="1.3")
    if not placement.tool.active or not LtiDeployment.objects.filter(tool=placement.tool, course=course, active=True).exists():
        raise Http404
    return render(request, "core/course_tool.html", {"course": course, "placement": placement})

@login_required
def toggle_student_preview(request, course_id):
    course = enrolled_or_404(request.user, get_object_or_404(Course, pk=course_id))
    if not can_manage(request.user, course): raise Http404
    if student_preview_active(request, course):
        request.session.pop("student_preview_course", None)
        messages.success(request, "Instructor view restored.")
    else:
        request.session["student_preview_course"] = course.id
        messages.success(request, "Student preview enabled. Any upload you make is recorded as a test submission.")
    return redirect(request.POST.get("next") or "course-home", course_id=course.id)

@login_required
def roster(request, course_id):
    course = enrolled_or_404(request.user, get_object_or_404(Course, pk=course_id))
    role = request.GET.get("role", "")
    enrollments = course.enrollments.select_related("user").filter(active=True)
    if role: enrollments = enrollments.filter(role=role)
    return render(request, "core/roster.html", {"course": course, "enrollments": enrollments, "selected_role": role, "can_manage": can_manage(request.user, course) and not student_preview_active(request, course)})

@login_required
def assignments(request, course_id):
    course = enrolled_or_404(request.user, get_object_or_404(Course, pk=course_id))
    if request.method == "POST":
        if not can_manage(request.user, course) or student_preview_active(request, course): raise Http404
        form = AssignmentForm(request.POST, course=course)
        if form.is_valid():
            tool = form.cleaned_data.get("lti_tool")
            assignment = form.save(commit=False); assignment.course = course; assignment.save()
            audit(request, "assignment.created", str(assignment.id)); messages.success(request, "Assignment published to your workspace.")
            if assignment.submission_type == Assignment.SubmissionType.LTI_TOOL:
                return redirect(f"{reverse('lti13-deep-link-start', args=[course.id, tool.id])}?assignment={assignment.id}")
            return redirect("assignments", course_id=course.id)
    else: form = AssignmentForm(course=course)
    return render(request, "core/assignments.html", {"course": course, "assignments": course.assignments.all(), "form": form, "can_manage": can_manage(request.user, course) and not student_preview_active(request, course)})

@login_required
def assignment_detail(request, course_id, assignment_id):
    course = enrolled_or_404(request.user, get_object_or_404(Course, pk=course_id))
    assignment = get_object_or_404(Assignment, pk=assignment_id, course=course)
    preview = student_preview_active(request, course)
    manager = can_manage(request.user, course) and not preview
    if request.method == "POST" and not manager:
        form = SubmissionForm(request.POST, request.FILES)
        if form.is_valid():
            latest = Submission.objects.filter(assignment=assignment, student=request.user).aggregate(Max("version"))["version__max"] or 0
            item = form.save(commit=False); item.assignment = assignment; item.student = request.user; item.version = latest + 1; item.filename = item.file.name; item.save()
            audit(request, "submission.test_created" if preview else "submission.created", str(item.id), assignment=assignment.id, version=item.version)
            messages.success(request, "Test submission uploaded." if preview else "Your file has been submitted."); return redirect(request.path)
    else: form = SubmissionForm()
    submissions = assignment.submissions.filter(student=request.user) if not manager else assignment.submissions.select_related("student")
    grade = Grade.objects.filter(assignment=assignment, student=request.user).first()
    lti_placement = ToolPlacement.objects.filter(course=course, assignment=assignment, tool__lti_version="1.3", tool__active=True).first()
    return render(request, "core/assignment_detail.html", {"course": course, "assignment": assignment, "form": form, "submissions": submissions, "grade": grade, "can_manage": manager, "preview": preview, "lti_placement": lti_placement})

@login_required
def grades(request, course_id):
    course = enrolled_or_404(request.user, get_object_or_404(Course, pk=course_id))
    manager = can_manage(request.user, course) and not student_preview_active(request, course)
    students = course.enrollments.filter(role=Enrollment.Role.STUDENT, active=True).select_related("user")
    assignment_list = list(course.assignments.all())
    if not manager: students = students.filter(user=request.user)
    grid = []
    for enrollment in students:
        values = {g.assignment_id: g for g in Grade.objects.filter(assignment__course=course, student=enrollment.user)}
        grid.append((enrollment, [(a, values.get(a.id)) for a in assignment_list]))
    return render(request, "core/grades.html", {"course": course, "assignments": assignment_list, "grid": grid, "can_manage": manager})

@login_required
def grade_submission(request, course_id, assignment_id, student_id):
    course = enrolled_or_404(request.user, get_object_or_404(Course, pk=course_id))
    if not can_manage(request.user, course) or student_preview_active(request, course): raise Http404
    assignment = get_object_or_404(Assignment, pk=assignment_id, course=course)
    student_enrollment = get_object_or_404(Enrollment, course=course, user_id=student_id, role=Enrollment.Role.STUDENT)
    grade, _ = Grade.objects.get_or_create(assignment=assignment, student=student_enrollment.user, defaults={"grader": request.user})
    if request.method == "POST":
        form = GradeForm(request.POST, instance=grade)
        if form.is_valid():
            grade = form.save(commit=False); grade.grader = request.user; grade.save(); audit(request, "grade.updated", str(grade.id), score=str(grade.score))
            messages.success(request, "Grade saved."); return redirect("grades", course_id=course.id)
    else: form = GradeForm(instance=grade)
    return render(request, "core/grade_submission.html", {"course": course, "assignment": assignment, "student": student_enrollment.user, "form": form, "submissions": assignment.submissions.filter(student=student_enrollment.user)})

@login_required
def lti_tools(request, course_id):
    course = enrolled_or_404(request.user, get_object_or_404(Course, pk=course_id))
    manager = can_manage(request.user, course) and not student_preview_active(request, course)
    if request.method == "POST" and manager:
        action = request.POST.get("action")
        if action == "remove-placement":
            placement = get_object_or_404(ToolPlacement, pk=request.POST.get("placement_id"), course=course)
            title, placement_id = placement.title, placement.id
            placement.delete()
            audit(request, "lti.placement_removed", str(placement_id), course=course.id)
            messages.success(request, f"Removed {title} from this course.")
            return redirect(request.path)
        if action == "register":
            tool_form, placement_form = LtiToolForm(request.POST), ToolPlacementForm()
            if tool_form.is_valid():
                tool = tool_form.save(commit=False); tool.created_by = request.user; tool.save()
                audit(request, "lti.tool_created", str(tool.id)); messages.success(request, "Tool registered. Add it to this course below."); return redirect(request.path)
        else:
            tool_form, placement_form = LtiToolForm(), ToolPlacementForm(request.POST)
            placement_form.fields["assignment"].queryset = course.assignments.all()
            if placement_form.is_valid():
                placement = placement_form.save(commit=False); placement.course = course
                if placement.tool.lti_version == "1.3" and not LtiDeployment.objects.filter(tool=placement.tool, course=course, active=True).exists():
                    placement_form.add_error("tool", "This LTI 1.3 registration must be deployed to the course by a platform administrator first.")
                    return render(request, "core/lti_tools.html", {"course": course, "placements": course.tool_placements.select_related("tool"), "tools": LtiTool.objects.all(), "can_manage": manager, "tool_form": tool_form, "placement_form": placement_form})
                placement.save()
                audit(request, "lti.placement_created", str(placement.id)); messages.success(request, "Tool added to course."); return redirect(request.path)
    else:
        tool_form, placement_form = LtiToolForm(), ToolPlacementForm()
        placement_form.fields["assignment"].queryset = course.assignments.all()
    deep_link_tools = LtiTool.objects.filter(lti_version="1.3", active=True, deployments__course=course, deployments__active=True).distinct() if manager else []
    return render(request, "core/lti_tools.html", {"course": course, "placements": course.tool_placements.select_related("tool"), "tools": LtiTool.objects.all() if manager else [], "deep_link_tools": deep_link_tools, "can_manage": manager, "tool_form": tool_form, "placement_form": placement_form})

@login_required
def configure_lti_placement(request, course_id, placement_id):
    course = enrolled_or_404(request.user, get_object_or_404(Course, pk=course_id))
    if not can_manage(request.user, course) or student_preview_active(request, course):
        raise Http404
    placement = get_object_or_404(ToolPlacement.objects.select_related("tool"), pk=placement_id, course=course)
    form = PlacementConfigurationForm(request.POST or None, instance=placement)
    if request.method == "POST" and form.is_valid():
        form.save()
        audit(request, "lti.placement_configured", str(placement.id), course=course.id, visible=placement.visible_in_course_navigation)
        messages.success(request, f"Updated {placement.title}.")
        return redirect("lti-tools", course_id=course.id)
    return render(request, "core/lti_placement_configure.html", {"course": course, "placement": placement, "form": form})

@login_required
def add_lti_tool(request, course_id):
    """Choose a registered, deployed tool before starting its content-selection flow."""
    course = enrolled_or_404(request.user, get_object_or_404(Course, pk=course_id))
    if not can_manage(request.user, course) or student_preview_active(request, course):
        raise Http404
    tools = LtiTool.objects.filter(lti_version="1.3", active=True, deployments__course=course, deployments__active=True).distinct()
    return render(request, "core/lti_tool_picker.html", {"course": course, "tools": tools})

@platform_admin_required
def platform_dashboard(request):
    return render(request, "core/platform/dashboard.html", {"people_count": User.objects.count(), "course_count": Course.objects.count(), "tool_count": LtiTool.objects.count(), "pending_count": User.objects.filter(is_active=False).count(), "events": AuditEvent.objects.select_related("actor")[:8]})

@platform_admin_required
def platform_lti(request):
    registration_form, import_form = LtiRegistrationForm(), LtiConfigImportForm()
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "manual":
            registration_form = LtiRegistrationForm(request.POST)
            if registration_form.is_valid():
                tool = registration_form.save(commit=False)
                tool.lti_version, tool.client_id, tool.created_by, tool.active = "1.3", f"minivle-{secrets.token_urlsafe(18)}", request.user, True
                tool.save(); audit(request, "lti13.registration_created", str(tool.id), client_id=tool.client_id)
                messages.success(request, "LTI 1.3 registration created.")
                return redirect("platform-lti-detail", tool_id=tool.id)
        elif action == "import":
            import_form = LtiConfigImportForm(request.POST)
            if import_form.is_valid():
                try:
                    with urlopen(import_form.cleaned_data["config_url"], timeout=5) as response:
                        config = json.load(response)
                    required = ("title", "oidc_initiation_url", "target_link_uri", "public_jwk_url")
                    if any(not config.get(field) for field in required): raise ValueError("Configuration is missing one or more required LTI 1.3 fields.")
                    tool = LtiTool.objects.create(
                        name=config["title"], launch_url=config["target_link_uri"], oidc_initiation_url=config["oidc_initiation_url"],
                        jwks_url=config["public_jwk_url"], deployment_registration_url=config.get("minivle_deployment_registration_url", ""), scopes=config.get("scopes", []), lti_version="1.3",
                        client_id=f"minivle-{secrets.token_urlsafe(18)}", created_by=request.user, active=True,
                    )
                    audit(request, "lti13.registration_imported", str(tool.id), source=import_form.cleaned_data["config_url"], client_id=tool.client_id)
                    messages.success(request, "LTI 1.3 configuration imported.")
                    return redirect("platform-lti-detail", tool_id=tool.id)
                except (OSError, ValueError, json.JSONDecodeError) as error:
                    import_form.add_error("config_url", f"Could not import configuration: {error}")
    return render(request, "core/platform/lti.html", {"tools": LtiTool.objects.filter(lti_version="1.3").prefetch_related("deployments"), "registration_form": registration_form, "import_form": import_form})

@platform_admin_required
def platform_lti_detail(request, tool_id):
    tool = get_object_or_404(LtiTool, pk=tool_id, lti_version="1.3")
    deployment_form = LtiDeploymentForm()
    if request.method == "POST":
        if request.POST.get("action") == "toggle":
            tool.active = not tool.active; tool.save(update_fields=["active", "updated_at"])
            audit(request, "lti13.registration_toggled", str(tool.id), active=tool.active)
            return redirect(request.path)
        deployment_form = LtiDeploymentForm(request.POST)
        if deployment_form.is_valid():
            deployment, created = LtiDeployment.objects.get_or_create(
                tool=tool, course=deployment_form.cleaned_data["course"],
                defaults={"deployment_id": f"dep-{secrets.token_urlsafe(18)}", "active": deployment_form.cleaned_data["active"]},
            )
            if not created:
                deployment.active = deployment_form.cleaned_data["active"]; deployment.save(update_fields=["active", "updated_at"])
            try:
                sync_tool_deployment(tool, deployment)
            except (OSError, ValueError) as error:
                if created: deployment.delete()
                deployment_form.add_error(None, f"Could not register this deployment with the tool: {error}")
                return render(request, "core/platform/lti_detail.html", {"tool": tool, "deployments": tool.deployments.select_related("course"), "deployment_form": deployment_form})
            audit(request, "lti13.deployment_created", str(deployment.id), course=deployment.course_id, deployment_id=deployment.deployment_id)
            messages.success(request, "LTI deployment is ready. Add a course placement from the course LTI tools page.")
            return redirect(request.path)
    deployment_form.fields["course"].queryset = Course.objects.filter(is_archived=False)
    return render(request, "core/platform/lti_detail.html", {"tool": tool, "deployments": tool.deployments.select_related("course"), "deployment_form": deployment_form})

@platform_admin_required
def platform_people(request):
    query, state = request.GET.get("q", ""), request.GET.get("state", "")
    people = User.objects.select_related("profile").order_by("first_name", "username")
    if query: people = people.filter(Q(username__icontains=query) | Q(email__icontains=query) | Q(first_name__icontains=query) | Q(last_name__icontains=query))
    if state == "pending": people = people.filter(is_active=False)
    if state == "admins": people = people.filter(profile__is_platform_admin=True)
    return render(request, "core/platform/people.html", {"people": people, "query": query, "state": state})

@platform_admin_required
def platform_person(request, user_id):
    person = get_object_or_404(User.objects.select_related("profile"), pk=user_id)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "toggle-active": person.is_active = not person.is_active; person.save(update_fields=["is_active"])
        elif action == "toggle-admin": person.profile.is_platform_admin = not person.profile.is_platform_admin; person.profile.save(update_fields=["is_platform_admin", "updated_at"])
        elif action == "enrol": Enrollment.objects.update_or_create(course_id=request.POST.get("course"), user=person, defaults={"role": request.POST.get("role", "student"), "active": True})
        audit(request, "platform.person_updated", str(person.id), operation=action); return redirect(request.path)
    enrolments = person.enrollments.select_related("course")
    can_masquerade = (
        person != request.user
        and person.is_active
        and not is_platform_admin(person)
        and enrolments.filter(role=Enrollment.Role.STUDENT, active=True).exists()
    )
    return render(request, "core/platform/person.html", {"person": person, "courses": Course.objects.filter(is_archived=False), "enrolments": enrolments, "can_masquerade": can_masquerade})

@platform_admin_required
def platform_courses(request):
    form = CourseForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        course = form.save(); audit(request, "platform.course_created", str(course.id)); messages.success(request, "Course created."); return redirect("platform-courses")
    return render(request, "core/platform/courses.html", {"courses": Course.objects.prefetch_related("enrollments").all(), "form": form})

@platform_admin_required
def platform_course(request, course_id):
    course = get_object_or_404(Course, pk=course_id); form = CourseForm(request.POST or None, instance=course)
    if request.method == "POST" and form.is_valid(): form.save(); audit(request, "platform.course_updated", str(course.id)); messages.success(request, "Course updated."); return redirect(request.path)
    return render(request, "core/platform/course.html", {"course": course, "form": form, "enrolments": course.enrollments.select_related("user")})

@platform_admin_required
def platform_audit(request):
    query = request.GET.get("q", "")
    events = AuditEvent.objects.select_related("actor")
    if query: events = events.filter(Q(action__icontains=query) | Q(target__icontains=query))
    return render(request, "core/platform/audit.html", {"events": events[:150], "query": query})
