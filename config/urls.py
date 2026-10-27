from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path

urlpatterns = [path("admin/", admin.site.urls), path("", include("core.urls")), path("o/", include("oauth2_provider.urls", namespace="oauth2_provider"))]

# Docker Compose is a development environment and runs Gunicorn without a
# reverse proxy. Serve collected assets here; production should serve
# STATIC_ROOT directly from a CDN or web server.
if settings.DEBUG:
    # In local Compose development this directory is bind-mounted, so CSS and
    # JavaScript edits are available on refresh without rebuilding the image.
    urlpatterns += static(settings.STATIC_URL, document_root=settings.BASE_DIR / "core" / "static")
