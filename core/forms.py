from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm
from .models import Assignment, Course, Grade, LtiDeployment, LtiTool, Submission, ToolPlacement

class RegistrationForm(UserCreationForm):
    email = forms.EmailField()
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150)
    class Meta:
        model = User
        fields = ["first_name", "last_name", "email", "username", "password1", "password2"]
    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email__iexact=email).exists(): raise forms.ValidationError("An account with this email already exists.")
        return email

class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["first_name", "last_name", "email"]

class CourseForm(forms.ModelForm):
    class Meta:
        model = Course
        fields = ["code", "title", "description", "is_published", "is_archived"]

class SubmissionForm(forms.ModelForm):
    class Meta:
        model = Submission
        fields = ["file"]

class GradeForm(forms.ModelForm):
    class Meta:
        model = Grade
        fields = ["score", "feedback"]
        widgets = {"feedback": forms.Textarea(attrs={"rows": 4})}

class AssignmentForm(forms.ModelForm):
    lti_tool = forms.ModelChoiceField(queryset=LtiTool.objects.none(), required=False, label="Tool for Deep Linking")
    class Meta:
        model = Assignment
        fields = ["title", "instructions", "due_at", "points_possible", "published", "submission_type"]
        widgets = {"due_at": forms.DateTimeInput(attrs={"type": "datetime-local"})}

    def __init__(self, *args, course=None, **kwargs):
        super().__init__(*args, **kwargs)
        if course:
            self.fields["lti_tool"].queryset = LtiTool.objects.filter(lti_version="1.3", active=True, deployments__course=course, deployments__active=True).distinct()
        self.fields["lti_tool"].label_from_instance = lambda tool: tool.name

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("submission_type") == Assignment.SubmissionType.LTI_TOOL and not cleaned.get("lti_tool"):
            self.add_error("lti_tool", "Choose an LTI tool for Deep Linking.")
        return cleaned

class LtiToolForm(forms.ModelForm):
    class Meta:
        model = LtiTool
        fields = ["name", "launch_url", "lti_version", "consumer_key", "shared_secret", "issuer", "client_id", "deployment_id", "jwks_url"]

class ToolPlacementForm(forms.ModelForm):
    class Meta:
        model = ToolPlacement
        fields = ["tool", "title", "assignment"]

class PlacementConfigurationForm(forms.ModelForm):
    class Meta:
        model = ToolPlacement
        fields = ["visible_in_course_navigation"]
        labels = {"visible_in_course_navigation": "Show this tool in course navigation"}

class LtiRegistrationForm(forms.ModelForm):
    scopes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3, "placeholder": "One scope per line"}))
    class Meta:
        model = LtiTool
        fields = ["name", "launch_url", "oidc_initiation_url", "jwks_url", "deployment_registration_url", "scopes"]
    def clean_scopes(self):
        value = self.cleaned_data["scopes"]
        return [item.strip() for item in value.splitlines() if item.strip()]

class LtiConfigImportForm(forms.Form):
    config_url = forms.URLField(label="Tool configuration URL")

class LtiDeploymentForm(forms.ModelForm):
    class Meta:
        model = LtiDeployment
        fields = ["course", "active"]
