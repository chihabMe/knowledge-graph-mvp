# Runbook: Milestone 2 Live Permission-Safe Retrieval Proof

Purpose: prove, against the live stack and a real Google Workspace tenant,
that permission-safe question answering holds for two real employees with
different Drive access — the client-facing acceptance bar defined in
`output/pdf/workspace-oauth-live-acceptance-plan.pdf`. Local implementation
was already "Ready" per that document; this runbook is what turns "Live
Google Setup" and "Final acceptance" from Pending to Done.

Evidence policy applies: record pass/fail, case IDs, reason codes, counts,
and durations only — never real email addresses, OAuth tokens, secrets,
Drive file IDs, question text, or document content, per the acceptance
plan's redaction rules.

## Scenario

A fictional 2-employee company, "Aster Fabrication Co.", built as 4 Google
Doc folders under an existing Drive folder (`Knowledge Graph`, ID
`1InqYJrKXJkTTU-EoH0qAlZ7nyUYzcer8`) in `admin@chihab.online`'s Drive, using
only the `application/vnd.google-apps.document` MIME type:

| Folder | Docs | Shared with |
|---|---|---|
| `00-Company-Wide` | Employee Handbook, Company Overview, Org Chart | user1 + user2 |
| `01-Engineering` | Project Falcon - Design Doc, CNC-Line-3 Spec, Press-B Maintenance Procedure, Engineering Roadmap 2026 | user1 only |
| `02-Finance` | Project Falcon - Budget, 2026 Annual Budget, Payroll Summary, Vendor Payment Schedule | user2 only |
| `03-Executive-Confidential` | Contoso MSA - Confidential Terms, Board Minutes Q3, Disciplinary Case - J. Doe, Customer Contract - Contoso Manufacturing | admin only (not shared) |

Two entities deliberately cross folder boundaries so the proof tests the
permission-safe *graph* filter, not just file access:

- **Project Falcon** — technical facts (Engineering/user1), budget facts
  (Finance/user2), nothing Executive beyond what's visible. user1 must be
  refused on the budget despite seeing the project from their own doc;
  user2 must be refused on technical detail for the same reason in reverse
  (`data/eval/refusals.yaml` r001/r002).
- **Press-B machine** — a generic, safe maintenance procedure is
  Engineering-visible; the specific disciplinary incident on the same
  machine is Executive-only (r005/r006).

Full question/answer/refusal set: `data/eval/{users,questions,refusals}.yaml`.

This supersedes the earlier 4-persona/Shared-Drive design in
`private/demo-corpus/` and `scripts/demo/seed_demo_drive.py`, which was never
executed — see the superseded-notice comments at the top of those files.

## Preconditions

- Full stack up (`make up-dev` or `make up`).
- Three real Workspace identities: `admin@chihab.online` (owns all 15 docs),
  `user1@chihab.online`, `user2@chihab.online`.
- `QUERY_ANSWER_PROVIDER=openrouter` set on the django service — the default
  `extractive` provider echoes raw text verbatim and will fail every
  positive-case answer-substring check regardless of correctness.

## Steps (human — real Google Workspace admin + browser)

1. **Share the folders per the table above**: `00-Company-Wide` to both
   user1 and user2; `01-Engineering` to user1 only; `02-Finance` to user2
   only; leave `03-Executive-Confidential` unshared.
2. **Share the root folder** (or each shared subfolder) with the ingestion
   service account as a viewer, per ADR-009.
3. **Select the folder as the ingestion root and trigger the sync.** There
   is no web UI for this (Django admin is deliberately not installed) — it's
   normally an authenticated JSON API
   (`GET/POST /api/ingest/drive/roots/` etc.), which needs an admin session
   you'd otherwise have to script by hand. Instead, run the one-off
   management command added for this:
   ```bash
   make demo-select-root ROOT_ID=1InqYJrKXJkTTU-EoH0qAlZ7nyUYzcer8
   ```
   This selects the `Knowledge Graph` folder as the ingestion root and
   immediately queues a sync (equivalent to the two API calls above). Add
   `--no-sync` via `docker compose exec -T django python manage.py
   select_drive_root_and_sync <id> --no-sync` if you want to select the root
   without triggering a sync yet. Requires the ingestion service account to
   already be shared on the folder (step 2) and `make up`/`make up-dev`
   already running. Wait for successful extraction, provenance writes, and
   the content-ready gate before moving on — check with `make logs` or the
   sync run's status.
4. **Complete per-user Drive OAuth** as user1, user2, **and admin** through
   Open WebUI. Admin's OAuth gives the two Executive-Confidential leak
   cases (r003/r004, r005/r006) a real "allowed" comparison instead of two
   bare refusals with nothing to contrast against.
5. **Run the evaluation**: `make demo-eval`. Record the printed
   `SUMMARY passed=... failed=...` line and each case's `PASS`/`FAIL` +
   reason code.
6. **Live chat walkthrough for screenshots**: log into Open WebUI as user1
   and ask "What is the approved budget for Project Falcon?" (expect
   refusal, zero citations); log in as user2 and ask the same question
   (expect an answer citing "Project Falcon - Budget"). Screenshot both.
7. **Fill in** `output/client-handoff/milestone-2-completion-report.md` with
   the real PASS/FAIL summary and screenshots, render to PDF, and update
   `docs/daily-reports/YYYY-MM-DD.md`.

## Reading the results

- A **refusal-side FAIL** (a `denied_user` case that got an answer or a
  citation) is a real permission-wall failure — stop and investigate before
  claiming the milestone.
- A **positive-side FAIL** (`answer_mismatch` or `source_missing` on a
  question, not a refusal case) usually means the model's phrasing didn't
  contain the expected substring, not that permissions leaked. Check the
  actual answer text before concluding anything is broken.
- `unexpected_refusal` on a positive case with the right provider configured
  is worth investigating either way — it means SpiceDB/visibility evidence
  wasn't fresh for that user at query time.

## Exit Criteria

- `make demo-eval` exits 0 (all cases pass), or every failing case is
  understood and explained (see "Reading the results" above) before calling
  the milestone complete.
- r001, r002, r005 (the three graph-path traps) show refusal with zero
  citations for their `denied_user`.
- The completion report contains no real email addresses, OAuth tokens,
  secrets, Drive file IDs, question text, or document content.

If any refusal-side case fails, live acceptance stays open: fix, then rerun
`make demo-eval` from a clean sync.
