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
    class Meta:
        model = Assignment
        fields = ["title", "instructions", "due_at", "points_possible", "published"]
        widgets = {"due_at": forms.DateTimeInput(attrs={"type": "datetime-local"})}

class LtiToolForm(forms.ModelForm):
    class Meta:
        model = LtiTool
        fields = ["name", "launch_url", "lti_version", "consumer_key", "shared_secret", "issuer", "client_id", "deployment_id", "jwks_url"]

class ToolPlacementForm(forms.ModelForm):
    class Meta:
        model = ToolPlacement
        fields = ["tool", "title", "assignment"]

class LtiRegistrationForm(forms.ModelForm):
    scopes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3, "placeholder": "One scope per line"}))
    class Meta:
        model = LtiTool
        fields = ["name", "launch_url", "oidc_initiation_url", "jwks_url", "scopes"]
    def clean_scopes(self):
        value = self.cleaned_data["scopes"]
        return [item.strip() for item in value.splitlines() if item.strip()]

class LtiConfigImportForm(forms.Form):
    config_url = forms.URLField(label="Tool configuration URL")

class LtiDeploymentForm(forms.ModelForm):
    class Meta:
        model = LtiDeployment
        fields = ["course", "active"]
