# Development Google Drive OAuth

This is a local-only workaround for testing real Drive content with a personal
Google account while Workspace domain-wide delegation is unavailable. It does
not replace the production service-account path and is rejected when
`DJANGO_DEBUG` is false.

## Files and configuration

Keep the downloaded OAuth client JSON and the generated token outside the
repository, for example:

```dotenv
GOOGLE_DRIVE_AUTH_MODE=oauth_dev
GOOGLE_OAUTH_CLIENT_SECRET_FILE=/home/user/secrets/google-oauth-client.json
GOOGLE_OAUTH_TOKEN_FILE=/home/user/secrets/google-oauth-token.json
```

The OAuth consent screen should use the `drive.readonly` scope and list the
developer's Google account as a test user. Do not use production/client data.

Root discovery includes folders shared with the authorized account and folders
owned by that account. Production service-account mode continues to expose
only explicitly shared folders and shared drives.

Create the token file before mounting it into Docker:

```bash
touch /home/user/secrets/google-oauth-token.json
chmod 600 /home/user/secrets/google-oauth-token.json
```

## Interactive authorization

Start the local stack after setting the environment variables, then run the
one-off command with the OAuth callback port published:

```bash
make up
docker compose --env-file .env \
  -f infra/compose.infrastructure.yml \
  -f infra/compose.app.yml \
  run --rm --user "$(id -u):$(id -g)" -p 8765:8765 django \
  python manage.py drive_oauth_login
```

Open the printed Google authorization URL, consent with the configured test
user, and wait for the command to report that the token was saved. The token
is then mounted read/write for the Django and Celery containers; it is never
logged or passed through a Celery task payload.

The development Compose overlay runs those two processes with the host uid/gid
(default `1000:1000`) so the mode-`0600` token remains readable and refreshable
without weakening its filesystem permissions. Override `LOCAL_UID` and
`LOCAL_GID` in `.env` when the host account uses different numeric IDs.

## Scope and limitations

The development OAuth user must own the test folder/files for reliable ACL
metadata access. This validates real content, direct permissions, hashing,
graph extraction, and provenance. It does not validate Workspace groups,
nested groups, shared-drive inheritance, or domain-wide delegation. Production
must use `GOOGLE_DRIVE_AUTH_MODE=service_account`.
