# MiniVLE

An original, lightweight learning platform for developing and testing LTI tools. It is workflow-familiar to established LMS products but has no copied branding, assets, source code, or page designs.

## Run locally

```sh
docker compose up --build
docker compose exec web python manage.py createsuperuser
```

Open `http://localhost:8000/admin/` to create courses, enrollments, assignments, and LTI tool registrations. The learner and instructor workspace is at `http://localhost:8000/`.

## Platform endpoints

- `GET /api/v1/` — browsable, paginated REST API
- `POST /api/v1/submission-uploads/presign/` and `/finalize/` — direct S3-compatible submission upload flow
- `GET /lti/jwks/` — LTI 1.3 platform JWKS
- `GET /lti/oidc/login/` — LTI 1.3 OIDC initiation
- `POST /lti/launch/` — signed LTI 1.3 launch
- `GET /lti/nrps/{course_id}/memberships` — roster service
- `POST /lti/ags/assignments/{assignment_id}/students/{student_id}/score` — score passback

Set `LTI_PRIVATE_KEY` to an RSA PEM and `LTI_ISSUER` to the public HTTPS base URL before LTI 1.3 testing. Store tool secrets in a secret manager / encrypted field before production. Local Docker uses SeaweedFS, an S3-compatible object store, instead of MinIO because the previously referenced MinIO images are no longer publicly pullable.

NRPS requires an OAuth2 token with `read` scope and AGS requires `write` scope. Create a confidential OAuth application in the Django admin for an external tool client. LTI 1.1 Outcomes requests are OAuth 1.0a signed against the registered tool's consumer key and secret.

## Design provenance

MiniVLE uses only system fonts and a CSS-drawn geometric mark. The component system, layout, labels, and copy are original; no Canvas assets, source, or screenshots are included.
