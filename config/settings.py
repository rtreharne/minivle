import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.getenv("SECRET_KEY", "unsafe-development-key-change-me")
DEBUG = os.getenv("DEBUG", "true").lower() == "true"
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",")

INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "rest_framework", "oauth2_provider", "core",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware", "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware", "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware", "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [BASE_DIR / "core/templates"], "APP_DIRS": True,
              "OPTIONS": {"context_processors": ["django.template.context_processors.request", "django.contrib.auth.context_processors.auth", "django.contrib.messages.context_processors.messages", "core.context.preview_context"]}}]
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

if os.getenv("POSTGRES_HOST"):
    DATABASES = {"default": {"ENGINE": "django.db.backends.postgresql", "NAME": os.getenv("POSTGRES_DB", "minivle"), "USER": os.getenv("POSTGRES_USER", "minivle"), "PASSWORD": os.getenv("POSTGRES_PASSWORD", "minivle"), "HOST": os.getenv("POSTGRES_HOST"), "PORT": os.getenv("POSTGRES_PORT", "5432"), "CONN_MAX_AGE": 60}}
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}

AUTH_PASSWORD_VALIDATORS = []
LANGUAGE_CODE, TIME_ZONE, USE_I18N, USE_TZ = "en-gb", "UTC", True, True
STATIC_URL, STATIC_ROOT = "/static/", BASE_DIR / "staticfiles"
if os.getenv("AWS_STORAGE_BUCKET_NAME"):
    STORAGES = {
        "default": {"BACKEND": "storages.backends.s3.S3Storage", "OPTIONS": {"bucket_name": os.environ["AWS_STORAGE_BUCKET_NAME"], "endpoint_url": os.getenv("AWS_S3_ENDPOINT_URL"), "access_key": os.getenv("AWS_ACCESS_KEY_ID"), "secret_key": os.getenv("AWS_SECRET_ACCESS_KEY"), "region_name": os.getenv("AWS_S3_REGION_NAME", "us-east-1"), "default_acl": None, "querystring_auth": True}},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL, LOGIN_REDIRECT_URL = "/login/", "/"
EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend" if DEBUG else "django.core.mail.backends.smtp.EmailBackend")
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "hello@minivle.local")
REST_FRAMEWORK = {"DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication", "oauth2_provider.contrib.rest_framework.OAuth2Authentication"], "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"], "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination", "PAGE_SIZE": 50}
OAUTH2_PROVIDER = {"SCOPES": {"read": "Read LMS data", "write": "Manage LMS data"}}
# In production store the PEM in a secret manager, never in this repository.
LTI_ISSUER = os.getenv("LTI_ISSUER", "http://localhost:8000")
LTI_PRIVATE_KEY = os.getenv("LTI_PRIVATE_KEY", "")
if not LTI_PRIVATE_KEY and os.getenv("LTI_PRIVATE_KEY_FILE"):
    LTI_PRIVATE_KEY = Path(os.environ["LTI_PRIVATE_KEY_FILE"]).read_text()
if not LTI_PRIVATE_KEY and DEBUG:
    # An ephemeral key makes the local test platform usable without placing a
    # private key in source control. Production must always provide a key.
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    LTI_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ).decode()
LTI_KEY_ID = os.getenv("LTI_KEY_ID", "minivle-development-key")
