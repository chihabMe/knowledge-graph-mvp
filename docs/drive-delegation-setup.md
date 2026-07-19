# Enabling Drive Permission Sync

> **Legacy optional mode:** ADR-015 replaces domain-wide delegation as the
> default POC permission authority. Use this guide only when a client explicitly
> selects delegated ACL/group synchronization. The active Phase 6 path is
> `docs/phase-6-pre-authorized-oauth-completion-plan.md`.

The knowledge layer only answers from documents a user is allowed to see. To
know who is allowed, it must read each file's sharing list ("who has access")
and expand any Google Groups those lists reference.

Sharing a folder with the service account is not enough for this: Google
returns `403 insufficientFilePermissions` when a service account tries to read
the sharing list of a file it does not own, even when the folder is shared with
it at Editor level. The supported way to grant this is **domain-wide
delegation**, authorized once by a Workspace admin.

Until this is done the system fails closed: documents are ingested but marked
`permission_metadata_incomplete` and excluded from every answer.

## What is being granted

Two **read-only** scopes, and nothing else:

| Scope | What it allows |
| --- | --- |
| `https://www.googleapis.com/auth/drive.readonly` | Read Drive files and their sharing lists |
| `https://www.googleapis.com/auth/admin.directory.group.member.readonly` | Read Google Group membership (to expand group-based access) |

Neither scope can modify, delete, or share anything. Domain-wide delegation
authorizes the service account to impersonate a Workspace user within those
scopes. Google limits access by the impersonated user's permissions and the
authorized scopes; Google does **not** enforce the ingestion root selected in
this application. The backend enforces that root when it scans Drive.

Use a dedicated, least-privileged delegated subject with only the Workspace
roles and Drive access required for this deployment. Protect and rotate the
service-account key as a high-impact credential.

Reference: [Google's server-to-server OAuth and domain-wide delegation
guide](https://developers.google.com/identity/protocols/oauth2/service-account).

## Workspace administrator steps (~10 minutes)

1. Sign in to the Google Admin console (`admin.google.com`) as a Workspace
   super admin.
2. Go to **Security → Access and data control → API controls**.
3. Under *Domain-wide delegation*, click **Manage domain-wide delegation**.
4. Click **Add new** and fill in:
   - **Client ID:** the numeric `client_id` from the service-account key JSON
     (sent separately — it is not published in this repository).
   - **OAuth scopes** (comma-separated, exactly as written):
     ```
     https://www.googleapis.com/auth/drive.readonly,https://www.googleapis.com/auth/admin.directory.group.member.readonly
     ```
5. Click **Authorize**.

Use the numeric **Client ID**, not the service-account email address — the
delegation screen only accepts the numeric ID, and the scope strings must match
character for character or the API returns `unauthorized_client`.

Changes usually take a few minutes to propagate, but Google notes that they may
take up to 24 hours in some cases.

## Deployment operator steps

The endpoints below are admin-only. The current backend uses Django session
authentication and does not yet expose the Phase 6 Google/OIDC login flow. The
operator must therefore use an existing authenticated staff session. Every
POST also requires the session's CSRF cookie and matching header.

Set these shell variables from a controlled authenticated operator session;
the cookie values are secrets and must never be committed, pasted into tickets,
or stored in shell history:

```bash
BASE_URL="https://api.example.com"
SESSION_ID="<authenticated-staff-sessionid>"
CSRF_TOKEN="<matching-csrf-token>"
ADMIN_COOKIE="sessionid=${SESSION_ID}; csrftoken=${CSRF_TOKEN}"
```

If no controlled staff session exists, stop. Do not temporarily expose these
endpoints or add a shared bearer key; implement the planned authenticated
operator path first.

1. Set the delegated subject (a Workspace user whose identity the service
   account acts as when reading metadata — typically an admin):

   ```bash
   curl --fail-with-body \
     --request POST \
     "${BASE_URL}/api/ingest/drive/connection/delegated-subject/" \
     --cookie "${ADMIN_COOKIE}" \
     --header "X-CSRFToken: ${CSRF_TOKEN}" \
     --header "Content-Type: application/json" \
     --data '{"delegated_subject_email": "workspace-operator@example.com"}'
   ```

   Changing this invalidates existing retrieval eligibility on purpose: the
   effective reader identity changed, so every document must be re-verified.

2. Confirm sharing lists are now readable:

   ```bash
   curl --fail-with-body \
     "${BASE_URL}/api/ingest/drive/permissions/check/" \
     --cookie "${ADMIN_COOKIE}"
   ```

   Expect `permission_metadata_access: "ok"`, `permissions_unreadable: 0`,
   and `folder_listing_errors: 0`. If files are still unreadable, confirm the
   delegated subject, API enablement, root selection, scope strings, and
   delegation propagation.

3. Run a permission sync (it also runs on a schedule):

   ```bash
   curl --fail-with-body \
     --request POST \
     "${BASE_URL}/api/permissions/sync/" \
     --cookie "${ADMIN_COOKIE}" \
     --header "X-CSRFToken: ${CSRF_TOKEN}" \
     --header "Content-Type: application/json" \
     --data '{}'
   ```

   Poll the returned run ID with
   `GET /api/permissions/sync/{run_id}/`. A `succeeded` status means every
   in-scope document passed the permission model; `partial` means one or more
   documents were deliberately excluded. Confirm `documents_verified` and
   `documents_excluded` before treating the connection as ready.

## Revoking

Deleting the domain-wide delegation entry, disabling the service-account key,
or unsharing the source folder blocks future Google API access according to
the chosen action. It does not by itself delete content already stored in
PostgreSQL/Neo4j or guarantee immediate removal of existing SpiceDB tuples.

For immediate containment, stop the client-facing deployment or otherwise
disable retrieval, revoke the Google-side access, and keep retrieval disabled
until a successful complete permission scan or a documented purge has removed
the stale authorization/data state. Phase 7 change-feed work and Phase 8
operations documentation must define and test the normal automated revocation
and purge procedure.
