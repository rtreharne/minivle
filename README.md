# MiniVLE

MiniVLE is a lightweight, original learning platform for developing and testing LTI tools. It provides courses, rosters, assignments, file submissions, grades, a REST API, native platform administration, and LTI 1.1 / LTI 1.3 development flows.

It is familiar to LMS users without copying the branding, assets, source code, copy, or page designs of any other LMS.

## Run locally

MiniVLE requires Docker Compose.

```sh
docker compose up --build
```

Open [http://localhost:8000](http://localhost:8000).

The web service automatically applies migrations and collects static assets on startup. Local services are:

| Service | Address | Purpose |
| --- | --- | --- |
| MiniVLE | `http://localhost:8000` | Learning platform and LTI platform |
| PostgreSQL | internal | Persistent relational data |
| Redis | internal | Cache and background-job support |
| SeaweedFS S3 | `http://localhost:8333` | S3-compatible submission storage |

Use the native **Platform** area in MiniVLE to manage people, courses, registrations, deployments, and audit activity. Django Admin is retained only for emergency back-office use and is not part of normal product workflows.

## Course tools

Instructors manage visible course tools from **Course → Tools**:

1. A platform administrator registers an LTI 1.3 tool and deploys it to a course.
2. An instructor selects **Add tool**.
3. The instructor chooses a deployed tool and completes its Deep Linking content picker.
4. MiniVLE creates a course placement. Instructors can show or hide it from course navigation, or remove it.

Assignments support two submission modes:

- **File upload** — the default, versioned upload workflow.
- **External LTI tool** — the instructor selects a deployed LTI tool during assignment creation, completes Deep Linking, and MiniVLE binds the returned placement to the assignment.

## LTI 1.3 development

MiniVLE supports a development-focused LTI 1.3 Resource Link and Deep Linking flow:

- Third-party initiated OIDC login
- Signed `LtiResourceLinkRequest` launches
- Platform JWKS
- Signed `LtiDeepLinkingRequest` launches
- Verification of signed `LtiDeepLinkingResponse` messages
- Course placement creation from returned `ltiResourceLink` content items
- Development NRPS and AGS routes

Developer documentation is available at [http://localhost:8000/docs/lti/](http://localhost:8000/docs/lti/).

### Local Counter tool

The example Counter tool is a separate sibling project, `../minivle-counter-tool`, and runs on port 8002:

```sh
cd ../minivle-counter-tool
docker compose up --build
```

It is an LTI 1.3 tool with a course-scoped learner click counter and a Deep Linking content picker. Its configuration document is [http://localhost:8002/lti/config.json](http://localhost:8002/lti/config.json).

### Automatic deployment sync

MiniVLE can register new course deployment IDs with tools automatically. A tool opts in by including `minivle_deployment_registration_url` in its configuration document. When a platform administrator deploys that tool to a course, MiniVLE sends its issuer, client ID, and deployment ID to that endpoint as JSON with an HMAC-SHA256 signature in `X-MiniVLE-Deployment-Signature`.

The local Counter tool persists approved deployments in SQLite. This means new course deployments do not require editing its Compose configuration or restarting the tool.

Both applications must share `LTI_DEPLOYMENT_SYNC_SECRET`. The checked-in Compose values are for local development only; use a unique secret from a secret manager for shared or production environments. The tool must validate the signature, issuer, and client ID before persisting a deployment.

## Platform endpoints

- `GET /api/v1/` — browsable, paginated REST API
- `POST /api/v1/submission-uploads/presign/` and `/finalize/` — direct S3-compatible submission upload flow
- `GET /lti/jwks/` — platform public JWKS
- `GET /lti/authorize/` — LTI 1.3 OIDC authorization response
- `GET /lti/placements/{placement_id}/launch/` — Resource Link launch start
- `GET /lti/deep-linking/courses/{course_id}/tools/{tool_id}/launch/` — instructor-only Deep Linking start
- `POST /lti/deep-linking/return/` — verified Deep Linking response return
- `GET /lti/nrps/{course_id}/memberships` — development roster service
- `POST /lti/ags/assignments/{assignment_id}/students/{student_id}/score` — development score passback

Set `LTI_PRIVATE_KEY` (or `LTI_PRIVATE_KEY_FILE`) to a persistent RSA PEM and set `LTI_ISSUER` to the public HTTPS base URL before production LTI testing. Local Docker mounts a development key at `secrets/lti-development-private.pem`; it is ignored by Git.

## Tests

```sh
docker compose exec web python manage.py test

cd ../minivle-counter-tool
docker compose exec counter-tool pytest
```

## Design provenance

MiniVLE uses system fonts and a CSS-drawn geometric mark. Its component system, layout, labels, and copy are original; no Canvas, Blackboard, Moodle, or other LMS assets or screenshots are included.
