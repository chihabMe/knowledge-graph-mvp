# Enabling Drive Permission Sync (Workspace Admin, ~10 minutes)

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

Neither scope can modify, delete, or share anything. Access stays bounded to
the folder or shared drive the admin selects as the ingestion root; the
delegation makes the sharing metadata readable, it does not widen the scope.

## Steps

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

Changes can take a few minutes to propagate.

## What we do next

1. Set the delegated subject (a Workspace user whose identity the service
   account acts as when reading metadata — typically an admin):

   ```bash
   curl -X POST https://<host>/api/integrations/drive/connection/delegated-subject/ \
     -H 'Content-Type: application/json' \
     -d '{"delegated_subject_email": "admin@<client-domain>"}'
   ```

   Changing this invalidates existing retrieval eligibility on purpose: the
   effective reader identity changed, so every document must be re-verified.

2. Confirm sharing lists are now readable:

   ```bash
   curl https://<host>/api/integrations/drive/permissions/check/
   ```

   Expect `unreadable_files: 0`. If files are still unreadable, the delegation
   has not propagated or a scope string does not match exactly.

3. Run a permission sync (it also runs on a schedule) and confirm documents
   reach `retrieval_eligible`.

## Revoking

Delete the entry from the same domain-wide delegation screen, or unshare the
ingestion folder. Either one cuts access; unsharing the folder also removes
file access, deleting the delegation only removes metadata access.
