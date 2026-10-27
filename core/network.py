"""Networking helpers for local Docker development."""

from urllib.parse import urlsplit, urlunsplit

from django.conf import settings


def server_fetch_url(url):
    """Make host-local development URLs reachable from the web container.

    Browser-facing LTI URLs stay as ``localhost``.  Only requests made by the
    MiniVLE server are routed through Docker Desktop's host gateway.
    """
    if not settings.DEBUG:
        return url
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"localhost", "127.0.0.1"}:
        return url
    port = f":{parsed.port}" if parsed.port else ""
    return urlunsplit(parsed._replace(netloc=f"host.docker.internal{port}"))
