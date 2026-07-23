# POC Client Replication Runbook

This is the supported lightweight path for adding another isolated client. It
is intentionally manual: one config generator, one Coolify project, and a short
Google checklist. Do not add Terraform, DNS automation, or service-account keys
for the POC.

## What is shared and what is unique

Shared across POC clients:

- one Google OAuth project and External consent screen;
- one identity-only OAuth web client for Open WebUI/Django session login;
- one separate Drive-metadata OAuth web client for per-user authorization;
- the published backend image and `infra/compose.coolify.yml`.

Unique for every client:

- GCE VM and Coolify project/volumes;
- Workspace domain and public hostnames;
- dedicated ingestion service account attached to that VM;
- selected Drive folder/Shared Drive;
- generated application secrets, token keyring, refresh tokens, and databases.

## 1. Google setup

In the project hosting the client's GCE VM:

1. Enable the Google Drive API if it is not already enabled.
2. Create a dedicated service account, for example `kg-ingest-client2`. Do not
   use the default `PROJECT_NUMBER-compute@developer.gserviceaccount.com`
   account and do not create a JSON key.
3. Attach the new service account to the client's GCE VM. Give the VM the
   `drive.readonly` OAuth access scope (and its normal Cloud scope if needed).
4. Ask the client admin to share only the company folder or Shared Drive root
   with the exact service-account email as Viewer.
5. Keep the External consent screen in testing mode for the bounded POC and add
   the pilot users as Google test users when Google requires it.

Register these exact callbacks on the shared login OAuth client:

```text
https://CLIENT.PUBLIC_DOMAIN/oauth/google/callback
https://api.CLIENT.PUBLIC_DOMAIN/api/session/google/callback
```

Register this exact callback on the shared Drive OAuth client:

```text
https://api.CLIENT.PUBLIC_DOMAIN/api/drive/oauth/callback
```

The login and Drive clients must remain separate. The Workspace administrator
must approve the Drive app/scopes, and every pilot user still clicks Allow once.

## 2. Generate and check the client config

Run from the repository root:

```bash
scripts/deploy/provision-client.sh generate client2 \
  --domain chihab.online \
  --workspace-domain client-company.example \
  --service-account-email kg-ingest-client2@GCE_PROJECT_ID.iam.gserviceaccount.com
```

Edit `clients/client2/client2.env` (mode 0600) and fill only the remaining
`__FILL_ME__` entries:

- shared login OAuth client ID and secret;
- shared Drive OAuth client ID;
- the compact shared Drive OAuth client JSON in
  `KG_GOOGLE_USER_OAUTH_CLIENT_JSON`;
- the client's OpenRouter key.

Do not paste these values into chat, tickets, logs, or Git. The generated
directory is ignored and must remain local. Then run:

```bash
scripts/deploy/provision-client.sh check client2 --target coolify
```

The check verifies the dedicated service-account address, keyless ADC mode,
Workspace/domain match, exact callback URLs, shared Drive-client JSON, and
generated keyring without printing secret values. An empty
`GOOGLE_DRIVE_ROOT_ID` is correct: the root is selected after deployment.

## 3. Deploy in Coolify

1. Create one new Coolify project/resource for the client using
   `infra/compose.coolify.yml`.
2. Paste the checked environment values as Coolify secrets/config. Set
   `KG_IMAGE` to the tested immutable backend image tag or digest.
3. Route the Open WebUI service to `https://CLIENT.PUBLIC_DOMAIN` and Django to
   `https://api.CLIENT.PUBLIC_DOMAIN`.
4. Create the two DNS records manually in GoDaddy, wait for them to resolve,
   and deploy.
5. Confirm both public URLs use HTTPS and `/api/health/` is healthy.

Coolify is the convenience layer only. The compose file and generated client
configuration remain the reproducible source of truth.

## 4. Select the company root and ingest

Copy the folder ID from the client-admin-approved Drive URL, then run this in
the deployed Django container through Coolify's terminal:

```bash
python manage.py select_drive_root_and_sync DRIVE_FOLDER_ID
```

For a Shared Drive use:

```bash
python manage.py select_drive_root_and_sync SHARED_DRIVE_ID --scope-type shared_drive
```

The command refuses a root the attached service account cannot see. Confirm
there is exactly one enabled `DriveConnection`, its `service_account_email`
matches this client, the selected root is correct, and the sync creates the
expected `SourceDocument` rows.

## 5. POC acceptance before handoff

- A user from the configured Workspace domain can log in to Open WebUI.
- A user from another domain is rejected.
- Each pilot user completes the separate Drive consent once.
- Two users with different Drive access retrieve only their own allowed facts
  and citations.
- Removing a user's Drive share and refreshing visibility denies the removed
  fact without leaking its title or content.
- Restarting/redeploying preserves Postgres, Neo4j, SpiceDB, and Open WebUI data.
- A second generated client config passes the Coolify preflight without using
  client1's service account or generated secrets.

## Troubleshooting

- **Drive returns 403 / no visible roots:** the VM is still using the default
  compute account, lacks the `drive.readonly` access scope, the Drive API is not
  enabled, or the folder was shared with the wrong address.
- **Google redirects to localhost:** one of the three exact callback URLs or
  `WEBUI_URL` is stale. Fix both Google Cloud and the client env, then redeploy.
- **Open WebUI shows email/password login:** production password flags are not
  all false. The POC login is Google-only.
- **Login works but chat asks to connect Drive:** complete the second Drive
  consent; login OAuth and Drive OAuth are intentionally separate.
- **Preflight fails:** fix only the named setting. It deliberately never prints
  secret contents.
