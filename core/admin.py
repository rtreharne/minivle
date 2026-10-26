from django.contrib import admin
from .models import Assignment, AuditEvent, Course, Enrollment, Grade, LtiTool, Submission, ToolPlacement
admin.site.register([Course, Enrollment, Assignment, Submission, Grade, LtiTool, ToolPlacement, AuditEvent])
