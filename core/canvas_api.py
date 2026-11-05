"""Canvas-shaped endpoints backed by launch-bound LTI service tokens."""
import hashlib
import re
import secrets
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.core import signing
from django.core.files.base import ContentFile
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .models import Assignment, CanvasUploadGrant, Course, Enrollment, Grade, LtiServiceToken, Rubric, RubricAssociation, RubricAssessment, RubricCriterion, Submission
from .permissions import can_manage, is_platform_admin


def _token(request, template):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None, _error("unauthorized", 401)
    raw = auth[7:]
    token = LtiServiceToken.objects.select_related("tool", "deployment", "user", "launch_course").filter(
        token_hash=hashlib.sha256(raw.encode()).hexdigest(), expires_at__gt=timezone.now(), user__isnull=False,
        tool__active=True, deployment__active=True,
    ).first()
    if not token:
        return None, _error("invalid_token", 401)
    scope = f"url:{request.method}|{template}"
    if scope not in token.scopes or scope not in (token.tool.api_scopes or []):
        from .models import AuditEvent
        AuditEvent.objects.create(actor=token.user, action="canvas_api.scope_denied", target=request.path,
                                  metadata={"tool": token.tool_id, "deployment": token.deployment.deployment_id,
                                            "method": request.method, "scope": scope})
        return None, _error("insufficient_scope", 401)
    return token, None


def _error(message, status=400):
    return JsonResponse({"errors": [{"message": message}]}, status=status)


def _body(request):
    if request.content_type and "json" in request.content_type:
        import json
        try:
            value = json.loads(request.body or b"{}")
        except (ValueError, UnicodeDecodeError):
            return {}
        return value if isinstance(value, dict) else {}
    result = {}
    for key, value in request.POST.items():
        parts = re.findall(r"[^\[\]]+", key)
        cursor = result
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        if parts:
            cursor[parts[-1]] = value
        else:
            result[key] = value
    return result


def _bool(value):
    return value is True or str(value).lower() in ("true", "1", "yes", "on")


def _json(request, data, status=200, headers=None):
    if "application/json+canvas-string-ids" in request.headers.get("Accept", ""):
        def stringify(value):
            if isinstance(value, list):
                return [stringify(item) for item in value]
            if isinstance(value, dict):
                return {key: (str(item) if isinstance(item, int) and (key == "id" or key.endswith("_id")) else stringify(item)) for key, item in value.items()}
            return value
        data = stringify(data)
    response = JsonResponse(data, safe=not isinstance(data, list), status=status)
    response["Cache-Control"] = "no-store"
    for key, value in (headers or {}).items():
        response[key] = value
    return response


def _paged(request, values):
    try:
        per_page = max(1, min(int(request.GET.get("per_page", "10")), 100))
        page = max(1, int(request.GET.get("page", "1")))
    except ValueError:
        return _error("Invalid pagination parameters.")
    count = len(values)
    start = (page - 1) * per_page
    items = values[start:start + per_page]
    links = []
    base = request.build_absolute_uri().split("?", 1)[0]
    for rel, target in (("current", page), ("first", 1), ("last", max(1, (count + per_page - 1) // per_page))):
        query = request.GET.copy(); query["page"] = str(target); query["per_page"] = str(per_page)
        links.append(f'<{base}?{query.urlencode()}>; rel="{rel}"')
    if page > 1:
        query = request.GET.copy(); query["page"] = str(page - 1); query["per_page"] = str(per_page)
        links.append(f'<{base}?{query.urlencode()}>; rel="prev"')
    if start + per_page < count:
        query = request.GET.copy(); query["page"] = str(page + 1); query["per_page"] = str(per_page)
        links.append(f'<{base}?{query.urlencode()}>; rel="next"')
    return _json(request, items, headers={"Link": ", ".join(links)})


def _course(token, course_id):
    return Course.objects.filter(pk=course_id, pk__in=token.course_ids).first()


def _role(token, course):
    if token.is_platform_admin or is_platform_admin(token.user):
        return Enrollment.Role.ADMIN
    if token.launch_role in (Enrollment.Role.INSTRUCTOR, Enrollment.Role.STUDENT):
        return token.launch_role
    return Enrollment.objects.filter(course=course, user=token.user, active=True).values_list("role", flat=True).first()


def _can_read_student(token, course, student_id):
    role = _role(token, course)
    return int(student_id) == token.user_id or role in (Enrollment.Role.INSTRUCTOR, Enrollment.Role.ADMIN)


def _assignment_json(assignment, include=()):
    result = {"id": assignment.id, "name": assignment.title, "description": assignment.instructions, "due_at": assignment.due_at.isoformat() if assignment.due_at else None,
            "points_possible": float(assignment.points_possible), "published": assignment.published, "course_id": assignment.course_id,
            "submission_types": ["online_upload"] if assignment.submission_type == Assignment.SubmissionType.FILE_UPLOAD else ["external_tool"],
            "has_submitted_submissions": assignment.submissions.exists(), "html_url": "", "workflow_state": "published" if assignment.published else "unpublished"}
    if "rubric" in include:
        association = assignment.rubric_associations.select_related("rubric").first()
        if association:
            result["rubric"] = _rubric_json(association.rubric)
            result["rubric_settings"] = {"id": association.id, "title": association.rubric.title, "points_possible": result["rubric"]["points_possible"], "free_form_criterion_comments": association.rubric.free_form_criterion_comments, "hide_score_total": association.hide_score_total}
    return result


def _submission_json(assignment, student, include=()):
    submission = Submission.objects.filter(assignment=assignment, student=student).order_by("-version").first()
    grade = Grade.objects.filter(assignment=assignment, student=student).first()
    attachments = []
    if submission and submission.file:
        try:
            file_size = submission.file.size
        except FileNotFoundError:
            # Keep submission and grade data available if an attachment was
            # removed from object storage independently of its database row.
            file_size = None
        if file_size is not None:
            attachments = [{"id": submission.id, "filename": submission.filename, "display_name": submission.filename, "url": submission.file.url, "size": file_size}]
    result = {"id": submission.id if submission else None, "assignment_id": assignment.id, "user_id": student.id,
            "submission_type": "online_upload" if submission else None, "workflow_state": "graded" if grade and grade.score is not None else ("submitted" if submission else "unsubmitted"),
            "submitted_at": submission.submitted_at.isoformat() if submission else None, "attempt": submission.version if submission else None,
            "score": float(grade.score) if grade and grade.score is not None else None, "grade": str(grade.score) if grade and grade.score is not None else None,
            "grader_id": grade.grader_id if grade else None, "graded_at": grade.updated_at.isoformat() if grade and grade.score is not None else None,
            "grade_matches_current_submission": True, "late": False, "assignment_visible": True, "missing": submission is None,
            "submission_comments": ([{"id": grade.id, "author_id": grade.grader_id, "author_name": grade.grader.get_full_name() if grade and grade.grader else "", "comment": grade.feedback, "created_at": grade.updated_at.isoformat()}] if grade and grade.feedback else []),
            "attachments": attachments}
    if "assignment" in include: result["assignment"] = _assignment_json(assignment)
    if "course" in include: result["course"] = {"id": assignment.course_id, "name": assignment.course.title, "course_code": assignment.course.code}
    if "user" in include: result["user"] = {"id": student.id, "name": student.get_full_name() or student.username}
    if "submission_history" in include:
        result["submission_history"] = [{"assignment_id": assignment.id, "user_id": student.id, "id": item.id, "attempt": item.version,
                                          "submitted_at": item.submitted_at.isoformat(), "submission_type": "online_upload",
                                          "attachments": [{"id": item.id, "filename": item.filename, "display_name": item.filename, "url": item.file.url if item.file else None}]}
                                         for item in Submission.objects.filter(assignment=assignment, student=student).order_by("version")]
    if "rubric_assessment" in include or "full_rubric_assessment" in include:
        assessment = RubricAssessment.objects.filter(association__assignment=assignment, student=student).order_by("-updated_at").first()
        if assessment: result["rubric_assessment"] = _assessment_json(assessment, "full" if "full_rubric_assessment" in include else None)
    return result


def _students(course):
    return get_user_model().objects.filter(enrollments__course=course, enrollments__active=True, enrollments__role=Enrollment.Role.STUDENT).distinct().order_by("id")


def courses(request):
    token, error = _token(request, "/api/v1/courses")
    if error: return error
    values = [{"id": c.id, "name": c.title, "course_code": c.code, "workflow_state": "available" if c.is_published else "unpublished", "account_id": 1,
               "start_at": None, "end_at": None, "default_view": "feed", "public_syllabus": False, "is_favorite": False}
              for c in Course.objects.filter(pk__in=token.course_ids).order_by("code")]
    return _paged(request, values)


def current_user(request):
    token, error = _token(request, "/api/v1/users/self")
    if error: return error
    user = token.user
    return _json(request, {"id": user.id, "name": user.get_full_name() or user.username, "short_name": user.first_name or user.username,
                           "sortable_name": f"{user.last_name}, {user.first_name}".strip(", "), "login_id": user.username, "email": user.email})


def course_student_submissions(request, course_id):
    token, error = _token(request, "/api/v1/courses/:course_id/students/submissions")
    if error: return error
    course = _course(token, course_id)
    if not course: return _error("Not found", 404)
    student_ids = request.GET.getlist("student_ids[]") or request.GET.getlist("student_ids")
    if not student_ids: students = [token.user]
    elif student_ids == ["all"]:
        if _role(token, course) not in (Enrollment.Role.INSTRUCTOR, Enrollment.Role.ADMIN): return _error("Forbidden", 403)
        students = list(_students(course))
    else:
        if _role(token, course) not in (Enrollment.Role.INSTRUCTOR, Enrollment.Role.ADMIN) and any(str(token.user_id) != str(item) for item in student_ids): return _error("Forbidden", 403)
        students = list(_students(course).filter(pk__in=student_ids))
    assignment_ids = request.GET.getlist("assignment_ids[]") or request.GET.getlist("assignment_ids")
    assignments_qs = Assignment.objects.filter(course=course)
    if _role(token, course) == Enrollment.Role.STUDENT: assignments_qs = assignments_qs.filter(published=True)
    if assignment_ids: assignments_qs = assignments_qs.filter(pk__in=assignment_ids)
    assignments_list = list(assignments_qs.order_by("due_at", "id"))
    include = request.GET.getlist("include[]") + request.GET.getlist("include")
    rows = [_submission_json(assignment, student, include) for student in students for assignment in assignments_list]
    if _bool(request.GET.get("grouped", False)):
        grouped = {}
        for row in rows: grouped.setdefault(str(row["user_id"]), []).append(row)
        return _json(request, grouped)
    return _paged(request, rows)


@csrf_exempt
def assignments(request, course_id):
    token, error = _token(request, "/api/v1/courses/:course_id/assignments")
    if error: return error
    course = _course(token, course_id)
    if not course: return _error("Not found", 404)
    if request.method == "GET":
        qs = Assignment.objects.filter(course=course)
        if _role(token, course) == Enrollment.Role.STUDENT: qs = qs.filter(published=True)
        include = request.GET.getlist("include[]") + request.GET.getlist("include")
        values = [_assignment_json(a, include) for a in qs.order_by("due_at", "id")]
        return _paged(request, values)
    if request.method == "POST":
        if not can_manage(token.user, course) and not token.is_platform_admin: return _error("Forbidden", 403)
        body = _body(request); data = body.get("assignment", body)
        try:
            submission_types = data.get("submission_types", ["online_upload"])
            if isinstance(submission_types, str): submission_types = [submission_types]
            submission_type = Assignment.SubmissionType.LTI_TOOL if "external_tool" in submission_types else Assignment.SubmissionType.FILE_UPLOAD
            assignment = Assignment.objects.create(course=course, title=data.get("name", data.get("title", "")), instructions=data.get("description", ""),
                points_possible=Decimal(str(data.get("points_possible", 100))), published=_bool(data.get("published", False)), submission_type=submission_type)
            if data.get("due_at"):
                assignment.due_at = datetime.fromisoformat(str(data["due_at"]).replace("Z", "+00:00")); assignment.save(update_fields=["due_at"])
        except (InvalidOperation, ValueError, TypeError): return _error("Invalid assignment parameters.")
        return _json(request, _assignment_json(assignment), 201)
    return _error("Method not allowed", 405)


@csrf_exempt
def assignment_detail(request, course_id, assignment_id):
    token, error = _token(request, "/api/v1/courses/:course_id/assignments/:id")
    if error: return error
    course = _course(token, course_id)
    assignment = Assignment.objects.filter(pk=assignment_id, course=course).first() if course else None
    if not assignment: return _error("Not found", 404)
    if request.method == "GET":
        if not assignment.published and _role(token, course) == Enrollment.Role.STUDENT: return _error("Not found", 404)
        include = request.GET.getlist("include[]") + request.GET.getlist("include")
        return _json(request, _assignment_json(assignment, include))
    if request.method == "DELETE":
        if not can_manage(token.user, course) and not token.is_platform_admin: return _error("Forbidden", 403)
        response_data = _assignment_json(assignment)
        file_names = list(assignment.submissions.exclude(file="").values_list("file", flat=True))
        assignment.delete()
        for file_name in file_names:
            try: default_storage.delete(file_name)
            except Exception: pass
        return _json(request, response_data)
    if request.method in ("PUT", "PATCH"):
        if not can_manage(token.user, course) and not token.is_platform_admin: return _error("Forbidden", 403)
        body = _body(request); data = body.get("assignment", body)
        for field, attr in (("name", "title"), ("title", "title"), ("description", "instructions"), ("published", "published"), ("points_possible", "points_possible")):
            if field in data:
                value = data[field]
                if attr == "published": value = str(value).lower() in ("true", "1", "yes")
                if attr == "points_possible":
                    try: value = Decimal(str(value))
                    except InvalidOperation: return _error("Invalid points_possible")
                setattr(assignment, attr, value)
        if "submission_types" in data:
            submission_types = data["submission_types"]
            if isinstance(submission_types, str): submission_types = [submission_types]
            assignment.submission_type = Assignment.SubmissionType.LTI_TOOL if "external_tool" in submission_types else Assignment.SubmissionType.FILE_UPLOAD
        if "due_at" in data:
            try: assignment.due_at = datetime.fromisoformat(data["due_at"].replace("Z", "+00:00")) if data["due_at"] else None
            except (ValueError, AttributeError): return _error("Invalid due_at")
        assignment.save(); return _json(request, _assignment_json(assignment))
    return _error("Method not allowed", 405)


@csrf_exempt
def assignment_submissions(request, course_id, assignment_id, user_id=None):
    template = "/api/v1/courses/:course_id/assignments/:assignment_id/submissions" + ("/:user_id" if user_id is not None else "")
    token, error = _token(request, template)
    if error: return error
    course = _course(token, course_id)
    assignment = Assignment.objects.filter(pk=assignment_id, course=course).first() if course else None
    if not assignment: return _error("Not found", 404)
    if user_id is None and request.method == "GET":
        students = _students(course) if _role(token, course) in (Enrollment.Role.INSTRUCTOR, Enrollment.Role.ADMIN) else [token.user]
        include = request.GET.getlist("include[]") + request.GET.getlist("include")
        values = [_submission_json(assignment, student, include) for student in students]
        return _paged(request, values)
    if user_id is None and request.method == "POST":
        if _role(token, course) != Enrollment.Role.STUDENT or token.user_id not in _students(course).values_list("id", flat=True): return _error("Forbidden", 403)
        body = _body(request); file = request.FILES.get("file") or request.FILES.get("submission[file]")
        if not file:
            submission_data = body.get("submission", body)
            file_ids = submission_data.get("file_ids", [])
            if isinstance(file_ids, str): file_ids = [file_ids]
            staged = Submission.objects.filter(assignment=assignment, student=token.user, pk__in=file_ids).order_by("-version").first()
            if staged: return _json(request, _submission_json(assignment, token.user), 201)
            return _error("Submission file is required.")
        latest = Submission.objects.filter(assignment=assignment, student=token.user).order_by("-version").first()
        submission = Submission(assignment=assignment, student=token.user, version=(latest.version + 1 if latest else 1), filename=file.name)
        submission.file.save(file.name, file, save=True)
        return _json(request, _submission_json(assignment, token.user), 201)
    try: requested_student_id = token.user_id if user_id == "self" else int(user_id)
    except (TypeError, ValueError): return _error("Not found", 404)
    student = get_user_model().objects.filter(pk=requested_student_id).first()
    if not student or not _can_read_student(token, course, student.id) or not Enrollment.objects.filter(course=course, user=student, role=Enrollment.Role.STUDENT, active=True).exists(): return _error("Not found", 404)
    if request.method == "GET":
        include = request.GET.getlist("include[]") + request.GET.getlist("include")
        return _json(request, _submission_json(assignment, student, include))
    if request.method in ("PUT", "PATCH"):
        if not can_manage(token.user, course) and not token.is_platform_admin: return _error("Forbidden", 403)
        body = _body(request); data = body.get("submission", body)
        try: score = Decimal(str(data.get("posted_grade", data.get("score")))) if data.get("posted_grade", data.get("score")) is not None else None
        except InvalidOperation: return _error("Invalid score")
        grade, _ = Grade.objects.get_or_create(assignment=assignment, student=student, defaults={"grader": token.user})
        grade.score = score
        comment = body.get("comment", {})
        grade.feedback = data.get("comment", comment.get("text_comment", grade.feedback) if isinstance(comment, dict) else comment)
        grade.grader = token.user
        grade.save()
        return _json(request, _submission_json(assignment, student))
    return _error("Method not allowed", 405)


@csrf_exempt
def submission_file_upload(request, course_id, assignment_id, user_id):
    token, error = _token(request, "/api/v1/courses/:course_id/assignments/:assignment_id/submissions/:user_id/files")
    if error: return error
    course = _course(token, course_id)
    assignment = Assignment.objects.filter(pk=assignment_id, course=course).first() if course else None
    try: requested_user_id = token.user_id if user_id == "self" else int(user_id)
    except (TypeError, ValueError): return _error("Not found", 404)
    if not assignment or requested_user_id != token.user_id or _role(token, course) != Enrollment.Role.STUDENT:
        return _error("Forbidden", 403)
    if not Enrollment.objects.filter(course=course, user=token.user, role=Enrollment.Role.STUDENT, active=True).exists(): return _error("Forbidden", 403)
    file = request.FILES.get("file") or request.FILES.get("uploaded_data")
    if not file:
        body = _body(request)
        name = body.get("name", "")
        try: size = int(body.get("size", 0))
        except (TypeError, ValueError): return _error("Invalid file size")
        if not name or size < 0 or size > 100 * 1024 * 1024: return _error("A valid name and size up to 100 MB are required.")
        nonce = secrets.token_urlsafe(32)
        CanvasUploadGrant.objects.create(token_hash=hashlib.sha256(nonce.encode()).hexdigest(), assignment=assignment, user=token.user,
                                         filename=name, size=size, content_type=body.get("content_type", "application/octet-stream"),
                                         expires_at=timezone.now() + timezone.timedelta(minutes=15))
        upload_token = signing.dumps({"nonce": nonce}, salt="canvas-submission-upload", compress=True)
        upload_url = request.build_absolute_uri(f"/api/v1/canvas-upload/{upload_token}")
        return _json(request, {"upload_url": upload_url, "upload_params": {}})
    return _store_submission_file(request, assignment, token.user, file)


def _store_submission_file(request, assignment, user, file):
    latest = Submission.objects.filter(assignment=assignment, student=user).order_by("-version").first()
    submission = Submission(assignment=assignment, student=user, version=(latest.version + 1 if latest else 1), filename=file.name)
    submission.file.save(file.name, file, save=True)
    return _json(request, {"id": submission.id, "filename": submission.filename, "display_name": submission.filename,
                           "url": submission.file.url, "size": submission.file.size, "content-type": file.content_type or "application/octet-stream"}, 201)


@csrf_exempt
def canvas_upload_receiver(request, upload_token):
    if request.method != "POST": return _error("Method not allowed", 405)
    try: context = signing.loads(upload_token, salt="canvas-submission-upload", max_age=900)
    except signing.BadSignature: return _error("Upload authorization expired.", 403)
    file = request.FILES.get("file")
    if not file: return _error("Invalid upload.", 400)
    if file.size > 100 * 1024 * 1024: return _error("File exceeds the 100 MB limit.", 400)
    try:
        with transaction.atomic():
            grant = CanvasUploadGrant.objects.select_for_update().select_related("assignment", "user").get(
                token_hash=hashlib.sha256(context["nonce"].encode()).hexdigest(), used_at__isnull=True, expires_at__gt=timezone.now(), user__is_active=True)
            if file.size != grant.size: return _error("File size differs from the upload request.", 400)
            file.name = grant.filename
            latest = Submission.objects.filter(assignment=grant.assignment, student=grant.user).order_by("-version").first()
            submission = Submission(assignment=grant.assignment, student=grant.user, version=(latest.version + 1 if latest else 1), filename=file.name)
            submission.file.save(file.name, file, save=True)
            grant.used_at = timezone.now(); grant.save(update_fields=["used_at"])
    except CanvasUploadGrant.DoesNotExist:
        return _error("Upload authorization is invalid, expired, or already used.", 403)
    location = request.build_absolute_uri(f"/api/v1/canvas-upload/{submission.id}/complete")
    from django.http import HttpResponse
    response = HttpResponse(status=303); response["Location"] = location
    return response


def canvas_upload_complete(request, submission_id):
    auth = request.headers.get("Authorization", "")
    token = None
    if auth.startswith("Bearer "):
        token = LtiServiceToken.objects.select_related("user", "tool", "deployment").filter(token_hash=hashlib.sha256(auth[7:].encode()).hexdigest(), expires_at__gt=timezone.now(), user__isnull=False, tool__active=True, deployment__active=True).first()
    submission = Submission.objects.select_related("assignment", "assignment__course").filter(pk=submission_id).first()
    if not token or not submission or (token.user_id != submission.student_id and not token.is_platform_admin): return _error("Not found", 404)
    if submission.assignment.course_id not in token.course_ids: return _error("Not found", 404)
    upload_scope = f"url:POST|/api/v1/courses/:course_id/assignments/:assignment_id/submissions/:user_id/files"
    if upload_scope not in token.scopes or upload_scope not in (token.tool.api_scopes or []): return _error("insufficient_scope", 401)
    return _json(request, {"id": submission.id, "url": submission.file.url if submission.file else None, "content-type": "application/octet-stream",
                           "display_name": submission.filename, "filename": submission.filename, "size": submission.file.size if submission.file else 0})


def submission_history(request, course_id, assignment_id, user_id):
    token, error = _token(request, "/api/v1/courses/:course_id/assignments/:assignment_id/submissions/:user_id/history")
    if error: return error
    course = _course(token, course_id)
    assignment = Assignment.objects.filter(pk=assignment_id, course=course).first() if course else None
    try: requested_student_id = token.user_id if user_id == "self" else int(user_id)
    except (TypeError, ValueError): return _error("Not found", 404)
    student = get_user_model().objects.filter(pk=requested_student_id).first()
    if not assignment or not student or not _can_read_student(token, course, student.id): return _error("Not found", 404)
    rows = []
    for submission in Submission.objects.filter(assignment=assignment, student=student).order_by("version"):
        item = _submission_json(assignment, student)
        item.update({"id": submission.id, "attempt": submission.version, "submitted_at": submission.submitted_at.isoformat(),
                     "attachments": [{"id": submission.id, "filename": submission.filename, "display_name": submission.filename, "url": submission.file.url if submission.file else None}]})
        rows.append(item)
    return _paged(request, rows)


def _rubric_json(rubric, include=()):
    result = {"id": rubric.id, "title": rubric.title, "context_id": rubric.course_id, "context_type": "Course", "points_possible": float(sum((c.points for c in rubric.criteria.all()), Decimal("0"))),
              "reusable": rubric.reusable, "read_only": False, "free_form_criterion_comments": rubric.free_form_criterion_comments,
              "hide_score_total": rubric.hide_score_total, "data": [{"id": c.criterion_id, "description": c.description, "long_description": c.long_description,
              "points": float(c.points), "ratings": c.ratings} for c in rubric.criteria.all()]}
    if "associations" in include:
        result["associations"] = [{"id": a.id, "rubric_id": rubric.id, "association_id": a.assignment_id or a.course_id, "association_type": "Assignment" if a.assignment_id else "Course",
                                    "use_for_grading": a.use_for_grading, "hide_score_total": a.hide_score_total, "purpose": a.purpose} for a in rubric.associations.all()]
    return result


def _save_rubric_criteria(rubric, data):
    criteria = data.get("criteria", data.get("data"))
    if criteria is None: return
    if isinstance(criteria, dict): criteria = list(criteria.values())
    if not isinstance(criteria, list): raise ValueError("criteria must be a list")
    rubric.criteria.all().delete()
    for index, criterion in enumerate(criteria):
        rubric.criteria.create(criterion_id=str(criterion.get("id") or criterion.get("criterion_id") or index + 1), description=criterion.get("description", ""),
                               long_description=criterion.get("long_description", ""), points=Decimal(str(criterion.get("points", 0))), ratings=criterion.get("ratings", []))


@csrf_exempt
def course_rubrics(request, course_id):
    token, error = _token(request, "/api/v1/courses/:course_id/rubrics")
    if error: return error
    course = _course(token, course_id)
    if not course: return _error("Not found", 404)
    if request.method == "GET": return _paged(request, [_rubric_json(r) for r in Rubric.objects.filter(course=course).prefetch_related("criteria")])
    if request.method == "POST":
        if not can_manage(token.user, course) and not token.is_platform_admin: return _error("Forbidden", 403)
        body = _body(request); data = body.get("rubric", body)
        rubric = Rubric.objects.create(course=course, title=data.get("title", "Untitled rubric"), free_form_criterion_comments=_bool(data.get("free_form_criterion_comments", False)), hide_score_total=_bool(data.get("hide_score_total", False)))
        try: _save_rubric_criteria(rubric, data)
        except (ValueError, InvalidOperation, TypeError): rubric.delete(); return _error("Invalid rubric criteria")
        association_data = body.get("rubric_association")
        if association_data:
            association_type = association_data.get("association_type", "Assignment")
            is_assignment = association_type == "Assignment"
            assignment = Assignment.objects.filter(pk=association_data.get("association_id"), course=course).first() if is_assignment else None
            if (is_assignment and not assignment) or association_type not in ("Assignment", "Course") or (association_type == "Course" and str(association_data.get("association_id")) != str(course.id)):
                rubric.delete(); return _error("Invalid rubric association")
            association = RubricAssociation.objects.create(rubric=rubric, course=course, assignment=assignment, use_for_grading=_bool(association_data.get("use_for_grading", False)),
                hide_score_total=_bool(association_data.get("hide_score_total", False)), purpose=association_data.get("purpose", "grading"))
        else: association = None
        return _json(request, {"rubric": _rubric_json(rubric), "rubric_association": _association_json(association) if association else None}, 201)
    return _error("Method not allowed", 405)


@csrf_exempt
def rubric_detail(request, course_id, rubric_id):
    token, error = _token(request, "/api/v1/courses/:course_id/rubrics/:id")
    if error: return error
    course = _course(token, course_id)
    rubric = Rubric.objects.filter(pk=rubric_id, course=course).first() if course else None
    if not rubric: return _error("Not found", 404)
    if request.method == "GET":
        include = request.GET.getlist("include[]") + request.GET.getlist("include")
        result = _rubric_json(rubric, include)
        if any(item.endswith("assessments") for item in include):
            assessments = RubricAssessment.objects.filter(association__rubric=rubric)
            if "graded_assessments" in include: assessments = assessments.filter(assessment_type="grading")
            elif "peer_assessments" in include: assessments = assessments.filter(assessment_type="peer_review")
            style = request.GET.get("style")
            result["assessments"] = [_assessment_json(a, style) for a in assessments]
        return _json(request, result)
    if not can_manage(token.user, course) and not token.is_platform_admin: return _error("Forbidden", 403)
    if request.method in ("PUT", "PATCH"):
        body = _body(request); data = body.get("rubric", body)
        rubric.title = data.get("title", rubric.title)
        rubric.free_form_criterion_comments = _bool(data.get("free_form_criterion_comments", rubric.free_form_criterion_comments))
        rubric.hide_score_total = _bool(data.get("hide_score_total", rubric.hide_score_total)); rubric.save()
        try: _save_rubric_criteria(rubric, data)
        except (ValueError, InvalidOperation, TypeError): return _error("Invalid rubric criteria")
        association_data = body.get("rubric_association")
        association = rubric.associations.filter(pk=body.get("rubric_association_id")).first() if body.get("rubric_association_id") else rubric.associations.first()
        if association and association_data:
            association.use_for_grading = _bool(association_data.get("use_for_grading", association.use_for_grading))
            association.hide_score_total = _bool(association_data.get("hide_score_total", association.hide_score_total))
            association.purpose = association_data.get("purpose", association.purpose); association.save()
            if "association_id" in association_data:
                association_type = association_data.get("association_type", "Assignment")
                assignment = Assignment.objects.filter(pk=association_data["association_id"], course=course).first() if association_type == "Assignment" else None
                if association_type == "Assignment" and not assignment: return _error("Invalid rubric association")
                if association_type not in ("Assignment", "Course"): return _error("Invalid rubric association")
                association.assignment = assignment; association.save(update_fields=["assignment", "updated_at"])
        return _json(request, {"rubric": _rubric_json(rubric), "rubric_association": _association_json(association) if association else None})
    if request.method == "DELETE":
        result = _rubric_json(rubric); rubric.delete(); return _json(request, result)
    return _error("Method not allowed", 405)


def rubric_used_locations(request, course_id, rubric_id):
    token, error = _token(request, "/api/v1/courses/:course_id/rubrics/:id/used_locations")
    if error: return error
    course = _course(token, course_id)
    rubric = Rubric.objects.filter(pk=rubric_id, course=course).first() if course else None
    if not rubric: return _error("Not found", 404)
    return _json(request, [{"course_id": a.course_id, "assignment_id": a.assignment_id} for a in rubric.associations.all()])


def _association_json(association):
    return {"id": association.id, "rubric_id": association.rubric_id, "association_id": association.assignment_id or association.course_id,
            "association_type": "Assignment" if association.assignment_id else "Course", "use_for_grading": association.use_for_grading,
            "summary_data": "", "purpose": association.purpose, "hide_score_total": association.hide_score_total,
            "hide_points": False, "hide_outcome_results": False}


def _assessment_json(assessment, style=None):
    result = {"id": assessment.id, "rubric_id": assessment.association.rubric_id, "rubric_association_id": assessment.association_id,
              "score": float(assessment.score) if assessment.score is not None else None, "artifact_type": "Submission" if assessment.association.assignment_id else "Course",
              "artifact_id": assessment.student_id, "artifact_attempt": 1, "assessment_type": assessment.assessment_type, "assessor_id": assessment.assessor_id}
    if style == "full": result["data"] = assessment.data
    if style == "comments_only": result["comments"] = {key: value.get("comments", "") for key, value in assessment.data.items() if isinstance(value, dict)}
    return result


@csrf_exempt
def rubric_associations(request, course_id, association_id=None):
    template = "/api/v1/courses/:course_id/rubric_associations" + ("/:id" if association_id else "")
    token, error = _token(request, template)
    if error: return error
    course = _course(token, course_id)
    if not course: return _error("Not found", 404)
    if association_id is None:
        if request.method == "POST":
            if not can_manage(token.user, course) and not token.is_platform_admin: return _error("Forbidden", 403)
            body = _body(request); data = body.get("rubric_association", body)
            rubric = Rubric.objects.filter(pk=data.get("rubric_id"), course=course).first()
            assignment_id = data.get("association_id")
            association_type = data.get("association_type", "Assignment")
            assignment = Assignment.objects.filter(pk=assignment_id, course=course).first() if association_type == "Assignment" and assignment_id else None
            if not rubric or association_type not in ("Assignment", "Course") or (association_type == "Assignment" and not assignment) or (association_type == "Course" and str(assignment_id) != str(course.id)):
                return _error("Invalid rubric association")
            association = RubricAssociation.objects.create(rubric=rubric, course=course, assignment=assignment, use_for_grading=_bool(data.get("use_for_grading", False)), hide_score_total=_bool(data.get("hide_score_total", False)), purpose=data.get("purpose", "grading"))
            return _json(request, _association_json(association), 201)
    association = RubricAssociation.objects.filter(pk=association_id, course=course).first()
    if not association: return _error("Not found", 404)
    if not can_manage(token.user, course) and not token.is_platform_admin: return _error("Forbidden", 403)
    if request.method in ("PUT", "PATCH"):
        body = _body(request); data = body.get("rubric_association", body)
        if "rubric_id" in data:
            rubric = Rubric.objects.filter(pk=data["rubric_id"], course=course).first()
            if not rubric: return _error("Invalid rubric association")
            association.rubric = rubric
        if "association_id" in data:
            association_type = data.get("association_type", "Assignment")
            assignment = Assignment.objects.filter(pk=data["association_id"], course=course).first() if association_type == "Assignment" else None
            if association_type == "Assignment" and not assignment: return _error("Invalid rubric association")
            if association_type not in ("Assignment", "Course"): return _error("Invalid rubric association")
            association.assignment = assignment
        for field in ("use_for_grading", "hide_score_total", "purpose"):
            if field in data: setattr(association, field, data[field] if field == "purpose" else _bool(data[field]))
        association.save(); return _json(request, _association_json(association))
    if request.method == "DELETE":
        result = _association_json(association); association.delete(); return _json(request, result)
    return _error("Method not allowed", 405)


@csrf_exempt
def rubric_assessments(request, course_id, association_id, assessment_id=None):
    template = f"/api/v1/courses/:course_id/rubric_associations/:rubric_association_id/rubric_assessments" + ("/:id" if assessment_id else "")
    token, error = _token(request, template)
    if error: return error
    course = _course(token, course_id)
    association = RubricAssociation.objects.filter(pk=association_id, course=course).first() if course else None
    if not association: return _error("Not found", 404)
    if (assessment_id is None and request.method == "POST") or (assessment_id is not None and request.method in ("PUT", "PATCH")):
        if not can_manage(token.user, course) and not token.is_platform_admin: return _error("Forbidden", 403)
        body = _body(request); data = body.get("rubric_assessment", body)
        if assessment_id:
            assessment = RubricAssessment.objects.filter(pk=assessment_id, association=association).first()
            if not assessment: return _error("Not found", 404)
            student_id = data.get("user_id", assessment.student_id)
        else:
            student_id = data.get("user_id")
            assessment = RubricAssessment(association=association, assessor=token.user)
        student = get_user_model().objects.filter(pk=student_id, enrollments__course=course, enrollments__role=Enrollment.Role.STUDENT, enrollments__active=True).first()
        if not student: return _error("Invalid student")
        if not assessment_id: assessment.student = student
        if assessment.student_id != student.id: return _error("Assessment student cannot be changed")
        assessment_type = data.get("assessment_type", assessment.assessment_type)
        if assessment_type not in ("grading", "peer_review", "provisional_grade"): return _error("Invalid assessment_type")
        criterion_data = data.get("data")
        if criterion_data is None:
            criterion_data = {key: value for key, value in data.items() if key not in {"user_id", "score", "assessment_type", "comments", "provisional", "final", "graded_anonymously"}}
        try:
            score = Decimal(str(data["score"])) if data.get("score") is not None else sum((Decimal(str(value.get("points", 0))) for value in criterion_data.values() if isinstance(value, dict)), Decimal("0"))
        except InvalidOperation: return _error("Invalid score")
        assessment.assessment_type = assessment_type
        assessment.score = score; assessment.data = criterion_data; assessment.comments = data.get("comments", "")
        assessment.provisional = _bool(data.get("provisional", False))
        assessment.final = _bool(data.get("final", False))
        assessment.graded_anonymously = _bool(data.get("graded_anonymously", False))
        assessment.save()
        if association.assignment_id and association.use_for_grading and assessment.assessment_type == "grading" and not assessment.provisional:
            Grade.objects.update_or_create(assignment=association.assignment, student=student, defaults={"score": score, "grader": token.user, "feedback": assessment.comments})
        return _json(request, _assessment_json(assessment), 201 if not assessment_id else 200)
    if assessment_id is not None and request.method == "DELETE":
        assessment = RubricAssessment.objects.filter(pk=assessment_id, association=association).first()
        if not assessment: return _error("Not found", 404)
        if not can_manage(token.user, course) and not token.is_platform_admin: return _error("Forbidden", 403)
        result = _assessment_json(assessment); assessment.delete(); return _json(request, result)
    return _error("Method not allowed", 405)
