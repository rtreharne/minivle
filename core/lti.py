"""LTI platform endpoints. Production deployments must set LTI_PRIVATE_KEY and enforce HTTPS."""
import json, time, uuid
from urllib.request import urlopen
from urllib.parse import parse_qs, urlencode
import jwt
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.core import signing
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.csrf import csrf_exempt
from oauthlib.oauth1 import Client, SIGNATURE_TYPE_BODY, RequestValidator, SignatureOnlyEndpoint
from oauth2_provider.decorators import protected_resource
from .api import audit
from .models import Assignment, AuditEvent, Grade, LtiDeployment, LtiTool, ToolPlacement
from .permissions import can_manage, course_role

LTI_VERSION = "https://purl.imsglobal.org/spec/lti/claim/version"
LTI_DEPLOYMENT = "https://purl.imsglobal.org/spec/lti/claim/deployment_id"
LTI_MESSAGE_TYPE = "https://purl.imsglobal.org/spec/lti/claim/message_type"
LTI_CONTEXT = "https://purl.imsglobal.org/spec/lti/claim/context"
LTI_DEEP_LINKING = "https://purl.imsglobal.org/spec/lti-dl/claim/deep_linking_settings"
LTI_CONTENT_ITEMS = "https://purl.imsglobal.org/spec/lti-dl/claim/content_items"

def lti13_hint(payload):
    return signing.dumps(payload, salt="lti13-launch")

def signed_platform_token(tool, deployment, course, user, nonce, message_type, **claims):
    now = int(time.time())
    base = {
        "iss": settings.LTI_ISSUER, "aud": tool.client_id, "sub": str(user.pk), "iat": now, "exp": now + 300, "nonce": nonce,
        LTI_VERSION: "1.3.0", LTI_DEPLOYMENT: deployment.deployment_id, LTI_MESSAGE_TYPE: message_type,
        LTI_CONTEXT: {"id": str(course.id), "label": course.code, "title": course.title},
        "https://purl.imsglobal.org/spec/lti/claim/roles": ["http://purl.imsglobal.org/vocab/lis/v2/membership#" + ("Instructor" if course_role(user, course) in ("instructor", "admin") else "Learner")],
        "name": user.get_full_name() or user.username, "email": user.email,
    }
    base.update(claims)
    return jwt.encode(base, settings.LTI_PRIVATE_KEY, algorithm="RS256", headers={"kid": settings.LTI_KEY_ID})

class ToolValidator(RequestValidator):
    enforce_ssl = False
    client_key_length = (1, 255); nonce_length = (8, 255)
    def get_client_secret(self, client_key, request):
        tool = LtiTool.objects.filter(consumer_key=client_key, lti_version="1.1").first()
        request.tool = tool
        return tool.shared_secret if tool else None
    def validate_client_key(self, client_key, request): return bool(self.get_client_secret(client_key, request))
    def validate_request_token(self, *args): return False
    def validate_access_token(self, *args): return False
    def validate_timestamp_and_nonce(self, client_key, timestamp, nonce, request, *args): return bool(timestamp and nonce)
    def get_rsa_key(self, *args): return None

def lti11_fields(tool, user, placement):
    role = course_role(user, placement.course)
    params = {"lti_version": "LTI-1p0", "lti_message_type": "basic-lti-launch-request", "resource_link_id": f"placement-{placement.id}", "user_id": str(user.id), "roles": "Instructor" if role == "instructor" else "Learner", "context_id": str(placement.course_id), "context_label": placement.course.code, "context_title": placement.course.title, "lis_person_name_full": user.get_full_name() or user.username, "lis_person_contact_email_primary": user.email}
    if placement.assignment_id: params["lis_result_sourcedid"] = f"mini:{placement.assignment_id}:{user.id}"; params["lis_outcome_service_url"] = settings.LTI_ISSUER + "/lti/1p1/outcomes/"
    client = Client(tool.consumer_key, client_secret=tool.shared_secret, signature_type=SIGNATURE_TYPE_BODY)
    _, _, signed = client.sign(tool.launch_url, http_method="POST", body=urlencode(params), headers={"Content-Type": "application/x-www-form-urlencoded"})
    return {key: values[-1] for key, values in parse_qs(signed).items()}

@require_GET
def lti11_launch(request, placement_id):
    if not request.user.is_authenticated: return HttpResponseBadRequest("Login required")
    placement = get_object_or_404(ToolPlacement.objects.select_related("tool", "course"), pk=placement_id, tool__lti_version="1.1")
    if not course_role(request.user, placement.course): return HttpResponseBadRequest("User is not enrolled")
    audit(request, "lti11.launch", str(placement.tool_id), course=placement.course_id)
    return render(request, "core/lti_post.html", {"target": placement.tool.launch_url, "fields": lti11_fields(placement.tool, request.user, placement)})

@require_POST
def lti11_outcomes(request):
    endpoint = SignatureOnlyEndpoint(ToolValidator())
    valid, signed_request = endpoint.validate_request(request.build_absolute_uri(), http_method="POST", body=request.body.decode(), headers={k: v for k, v in request.headers.items()})
    if not valid: return HttpResponseBadRequest("Invalid OAuth signature")
    # The sourcedid is issued only on assignment-linked launches and cannot select arbitrary records.
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(request.body); sourcedid = next(e.text for e in root.iter() if e.tag.endswith("sourcedId")); score = next(e.text for e in root.iter() if e.tag.endswith("textString"))
        _, assignment_id, student_id = sourcedid.split(":", 2)
        grade, _ = Grade.objects.update_or_create(assignment_id=assignment_id, student_id=student_id, defaults={"score": score})
    except Exception: return HttpResponseBadRequest("Unsupported Outcomes request")
    AuditEvent.objects.create(action="lti11.outcome", target=str(grade.id), metadata={"tool": signed_request.tool.id})
    return JsonResponse({"status": "success"})

@require_GET
def jwks(request):
    if not settings.LTI_PRIVATE_KEY: return JsonResponse({"keys": []})
    from jwt.algorithms import RSAAlgorithm
    from cryptography.hazmat.primitives import serialization
    key = serialization.load_pem_private_key(settings.LTI_PRIVATE_KEY.encode(), password=None).public_key()
    data = json.loads(RSAAlgorithm.to_jwk(key)); data.update({"kid": settings.LTI_KEY_ID, "use": "sig", "alg": "RS256"})
    return JsonResponse({"keys": [data]})

@login_required
@require_GET
@xframe_options_exempt
def lti13_start(request, placement_id):
    """Begin a standards-shaped third-party initiated OIDC launch."""
    placement = get_object_or_404(ToolPlacement.objects.select_related("tool", "course"), pk=placement_id, tool__lti_version="1.3")
    tool = placement.tool
    deployment = LtiDeployment.objects.filter(tool=tool, course=placement.course, active=True).first()
    if not course_role(request.user, placement.course) or not tool.active or not deployment or not tool.oidc_initiation_url:
        return HttpResponseBadRequest("This LTI 1.3 placement is not available.")
    hint = lti13_hint({"kind": "resource_link", "placement_id": placement.id, "user_id": request.user.id})
    params = {
        "iss": settings.LTI_ISSUER, "client_id": tool.client_id,
        "login_hint": str(request.user.id), "target_link_uri": tool.launch_url,
        "lti_message_hint": hint,
    }
    audit(request, "lti13.login_initiated", str(tool.id), course=placement.course_id, deployment=deployment.deployment_id)
    return redirect(tool.oidc_initiation_url + ("&" if "?" in tool.oidc_initiation_url else "?") + urlencode(params))

@login_required
@require_GET
@xframe_options_exempt
def lti13_deep_link_start(request, course_id, tool_id):
    """Start an instructor-only LTI Deep Linking request for one deployed tool."""
    course = get_object_or_404(LtiDeployment.objects.select_related("course"), course_id=course_id, tool_id=tool_id, active=True).course
    tool = get_object_or_404(LtiTool, pk=tool_id, lti_version="1.3", active=True)
    deployment = get_object_or_404(LtiDeployment, course=course, tool=tool, active=True)
    if not can_manage(request.user, course) or not tool.oidc_initiation_url:
        return HttpResponseBadRequest("Only course instructors can add LTI content.")
    assignment_id = request.GET.get("assignment")
    if assignment_id and not Assignment.objects.filter(pk=assignment_id, course=course, submission_type=Assignment.SubmissionType.LTI_TOOL).exists():
        return HttpResponseBadRequest("The LTI assignment is not available.")
    hint = lti13_hint({"kind": "deep_linking", "course_id": course.id, "tool_id": tool.id, "user_id": request.user.id, "assignment_id": assignment_id})
    params = {"iss": settings.LTI_ISSUER, "client_id": tool.client_id, "login_hint": str(request.user.id), "target_link_uri": tool.launch_url, "lti_message_hint": hint}
    audit(request, "lti13.deep_linking_initiated", str(tool.id), course=course.id, deployment=deployment.deployment_id)
    return redirect(tool.oidc_initiation_url + ("&" if "?" in tool.oidc_initiation_url else "?") + urlencode(params))

@login_required
@require_GET
@xframe_options_exempt
def lti13_authorize(request):
    """Return a signed LTI Resource Link or Deep Linking id_token to a registered tool."""
    client_id, redirect_uri = request.GET.get("client_id", ""), request.GET.get("redirect_uri", "")
    tool = LtiTool.objects.filter(lti_version="1.3", client_id=client_id, active=True).first()
    if not tool or redirect_uri != tool.launch_url or not settings.LTI_PRIVATE_KEY:
        return HttpResponseBadRequest("Unknown LTI client or redirect URI.")
    try:
        hint = signing.loads(request.GET.get("lti_message_hint", ""), salt="lti13-launch", max_age=300)
    except signing.BadSignature:
        return HttpResponseBadRequest("Invalid or expired LTI launch hint.")
    if hint.get("user_id") != request.user.id:
        return HttpResponseBadRequest("The LTI launch does not belong to this user.")
    kind = hint.get("kind", "resource_link")
    placement = None
    if kind == "resource_link":
        placement = ToolPlacement.objects.select_related("course").filter(pk=hint.get("placement_id"), tool=tool).first()
        course = placement and placement.course
        permitted = course and course_role(request.user, course)
    elif kind == "deep_linking":
        course = get_object_or_404(LtiDeployment, course_id=hint.get("course_id"), tool=tool, active=True).course
        permitted = can_manage(request.user, course)
    else:
        return HttpResponseBadRequest("Unsupported LTI launch type.")
    deployment = course and LtiDeployment.objects.filter(tool=tool, course=course, active=True).first()
    if not deployment or not permitted:
        return HttpResponseBadRequest("This LTI deployment is unavailable.")
    nonce, state = request.GET.get("nonce", ""), request.GET.get("state", "")
    if not nonce or not state:
        return HttpResponseBadRequest("LTI state and nonce are required.")
    if kind == "deep_linking":
        data = lti13_hint({"kind": "deep_linking_response", "course_id": course.id, "tool_id": tool.id, "user_id": request.user.id, "assignment_id": hint.get("assignment_id")})
        token = signed_platform_token(tool, deployment, course, request.user, nonce, "LtiDeepLinkingRequest", **{
            "https://purl.imsglobal.org/spec/lti/claim/target_link_uri": tool.launch_url,
            LTI_DEEP_LINKING: {
                "deep_link_return_url": request.build_absolute_uri("/lti/deep-linking/return/"),
                "accept_types": ["ltiResourceLink"], "accept_presentation_document_targets": ["iframe", "window"],
                "accept_media_types": "*/*", "auto_create": False, "data": data,
            },
        })
        audit(request, "lti13.deep_linking_launch", str(tool.id), course=course.id, deployment=deployment.deployment_id)
    else:
        token = signed_platform_token(tool, deployment, course, request.user, nonce, "LtiResourceLinkRequest", **{
            "https://purl.imsglobal.org/spec/lti/claim/target_link_uri": tool.launch_url,
            "https://purl.imsglobal.org/spec/lti/claim/resource_link": {"id": f"placement-{placement.id}", "title": placement.title},
        })
        audit(request, "lti13.launch", str(tool.id), course=course.id, deployment=deployment.deployment_id)
    return render(request, "core/lti_post.html", {"target": redirect_uri, "id_token": token, "state": state})

@require_POST
@csrf_exempt
def lti13_deep_link_return(request):
    """Verify a tool's signed Deep Linking response and create safe course placements."""
    response_token = request.POST.get("JWT", "")
    try:
        unverified = jwt.decode(response_token, options={"verify_signature": False})
        data = signing.loads(unverified.get(LTI_DEEP_LINKING, {}).get("data", ""), salt="lti13-launch", max_age=300)
        if data.get("kind") != "deep_linking_response":
            raise ValueError("Invalid Deep Linking response data.")
        tool = LtiTool.objects.get(pk=data["tool_id"], lti_version="1.3", active=True)
        deployment = LtiDeployment.objects.get(tool=tool, course_id=data["course_id"], active=True)
        header = jwt.get_unverified_header(response_token)
        with urlopen(tool.jwks_url, timeout=5) as response:
            keys = json.load(response).get("keys", [])
        jwk = next((key for key in keys if key.get("kid") == header.get("kid")), None)
        if not jwk:
            raise ValueError("The tool signing key was not found in its JWKS.")
        claims = jwt.decode(response_token, jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk)), algorithms=["RS256"], audience=settings.LTI_ISSUER, options={"require": ["exp", "iat", "iss", "aud"]})
        if claims.get(LTI_VERSION) != "1.3.0" or claims.get(LTI_DEPLOYMENT) != deployment.deployment_id or claims.get(LTI_MESSAGE_TYPE) != "LtiDeepLinkingResponse":
            raise ValueError("The Deep Linking response is not valid for this deployment.")
        if claims.get(LTI_DEEP_LINKING, {}).get("data") != unverified.get(LTI_DEEP_LINKING, {}).get("data"):
            raise ValueError("The Deep Linking response data did not match.")
        course = deployment.course
        user_id = data.get("user_id")
        user = get_user_model().objects.filter(pk=user_id, is_active=True).first()
        if not user or not can_manage(user, course):
            raise ValueError("The initiating user is no longer enrolled.")
        items = claims.get(LTI_CONTENT_ITEMS, [])
        if not isinstance(items, list) or not 1 <= len(items) <= 20:
            raise ValueError("Select between one and twenty LTI resource links.")
        assignment = None
        if data.get("assignment_id"):
            assignment = Assignment.objects.filter(pk=data["assignment_id"], course=course, submission_type=Assignment.SubmissionType.LTI_TOOL).first()
            if not assignment:
                raise ValueError("The linked assignment is no longer available.")
        placements = []
        for item in items:
            if not isinstance(item, dict) or item.get("type") != "ltiResourceLink" or item.get("url") != tool.launch_url:
                raise ValueError("The tool returned an unsupported content item.")
            title = str(item.get("title") or tool.name).strip()[:120]
            if not title:
                raise ValueError("Every selected content item needs a title.")
            placements.append(ToolPlacement.objects.create(course=course, tool=tool, title=title, assignment=assignment))
        AuditEvent.objects.create(actor_id=user_id, action="lti13.deep_linking_placed", target=str(tool.id), metadata={"course": course.id, "placements": [item.id for item in placements]})
        from django.contrib import messages
        messages.success(request, f"Added {len(placements)} tool {'link' if len(placements) == 1 else 'links'} to {course.code}.")
        return redirect("course-assignment-detail", course_id=course.id, assignment_id=assignment.id) if assignment else redirect("lti-tools", course_id=course.id)
    except (LtiTool.DoesNotExist, LtiDeployment.DoesNotExist, ValueError, KeyError, jwt.PyJWTError, OSError, json.JSONDecodeError) as error:
        return HttpResponseBadRequest(f"Invalid LTI Deep Linking response: {error}")

@require_GET
def oidc_login(request):
    """LTI 1.3 third-party initiated login endpoint."""
    target = request.GET.get("target_link_uri")
    issuer, client_id = request.GET.get("iss"), request.GET.get("client_id")
    tool = LtiTool.objects.filter(lti_version="1.3", issuer=issuer, client_id=client_id).first()
    if not tool or not target: return HttpResponseBadRequest("Unknown tool or missing target link URI")
    state = uuid.uuid4().hex
    request.session[f"lti:{state}"] = {"tool": tool.id, "target": target, "nonce": uuid.uuid4().hex, "at": time.time()}
    # The tool must redirect its login request to this endpoint; this post is the platform launch.
    return render(request, "core/lti_post.html", {"target": target, "state": state, "tool": tool})

@require_POST
def lti13_launch(request):
    state = request.POST.get("state"); data = request.session.pop(f"lti:{state}", None)
    if not data or time.time() - data["at"] > 300: return HttpResponseBadRequest("Expired or invalid LTI state")
    if not request.user.is_authenticated or not settings.LTI_PRIVATE_KEY: return HttpResponseBadRequest("Login required and LTI signing key must be configured")
    tool = get_object_or_404(LtiTool, pk=data["tool"])
    placement = ToolPlacement.objects.filter(tool=tool).first()
    if not placement: return HttpResponseBadRequest("Tool is not placed in a course")
    role = course_role(request.user, placement.course)
    if not role: return HttpResponseBadRequest("User is not enrolled")
    now = int(time.time())
    claims = {"iss": settings.LTI_ISSUER, "aud": tool.client_id, "sub": str(request.user.pk), "iat": now, "exp": now + 300, "nonce": data["nonce"],
        "https://purl.imsglobal.org/spec/lti/claim/version": "1.3.0", "https://purl.imsglobal.org/spec/lti/claim/deployment_id": tool.deployment_id,
        "https://purl.imsglobal.org/spec/lti/claim/message_type": "LtiResourceLinkRequest", "https://purl.imsglobal.org/spec/lti/claim/target_link_uri": data["target"],
        "https://purl.imsglobal.org/spec/lti/claim/context": {"id": str(placement.course_id), "label": placement.course.code, "title": placement.course.title},
        "https://purl.imsglobal.org/spec/lti/claim/roles": ["http://purl.imsglobal.org/vocab/lis/v2/membership#" + ("Instructor" if role == "instructor" else "Learner")],
        "name": request.user.get_full_name() or request.user.username, "email": request.user.email}
    token = jwt.encode(claims, settings.LTI_PRIVATE_KEY, algorithm="RS256", headers={"kid": settings.LTI_KEY_ID})
    audit(request, "lti13.launch", str(tool.id), course=placement.course_id)
    return render(request, "core/lti_post.html", {"target": data["target"], "id_token": token, "state": state})

@require_GET
@protected_resource(scopes=["read"])
def nrps(request, course_id):
    course = get_object_or_404(ToolPlacement, course_id=course_id).course
    members = [{"user_id": str(e.user_id), "name": e.user.get_full_name() or e.user.username, "email": e.user.email, "roles": [e.role]} for e in course.enrollments.select_related("user").filter(active=True)]
    return JsonResponse({"id": str(course.id), "context": {"label": course.code, "title": course.title}, "members": members})

@require_POST
@protected_resource(scopes=["write"])
def ags_score(request, assignment_id, student_id):
    assignment = get_object_or_404(ToolPlacement, assignment_id=assignment_id).assignment
    body = json.loads(request.body or "{}")
    score = body.get("scoreGiven")
    if score is None: return HttpResponseBadRequest("scoreGiven is required")
    grade, _ = Grade.objects.update_or_create(assignment=assignment, student_id=student_id, defaults={"score": score, "feedback": body.get("comment", "")})
    audit(request, "lti13.ags_score", str(grade.id), assignment=assignment.id)
    return JsonResponse({"status": "ok"}, status=201)
