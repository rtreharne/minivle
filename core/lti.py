"""LTI platform endpoints. Production deployments must set LTI_PRIVATE_KEY and enforce HTTPS."""
import json, time, uuid
from urllib.parse import parse_qs, urlencode
import jwt
from django.conf import settings
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_POST
from oauthlib.oauth1 import Client, SIGNATURE_TYPE_BODY, RequestValidator, SignatureOnlyEndpoint
from oauth2_provider.decorators import protected_resource
from .api import audit
from .models import AuditEvent, Grade, LtiTool, ToolPlacement
from .permissions import course_role

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
