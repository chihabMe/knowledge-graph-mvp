# Evaluation Fixture

This directory holds the **evaluation dataset** that proves the core product promise:

> If a user cannot open the original Drive file, the AI cannot use facts from that
> document to answer them — directly or through a graph path.

It is the input to the Phase 7 evaluation runner (`phase-7-change-feed-and-evaluation.md`)
and it backs the Permission Tests, Retrieval Tests, and Leak Tests in
`ai-context/05-test-and-acceptance.md`.

## Why this fixture is on the critical path

Almost every acceptance criterion is unprovable without it:

- **Permission tests** need ≥2 identities with *different* Drive access → `users.yaml`.
- **Answer-quality tests** need real questions with known-good answers → `questions.yaml`.
- **Leak tests** need questions one user should get and another must be refused → `refusals.yaml`.

Only the client can produce this (real questions, real correct answers, real
who-can-see-what). It has the longest lead time of anything in the project, so it is
requested up front rather than in the leak-test week.

## Files

| File | Committed? | What it is |
|---|---|---|
| `users.example.yaml` | yes | Template: test identities and the Drive access each has |
| `questions.example.yaml` | yes | Template: positive Q&A with the expected source document |
| `refusals.example.yaml` | yes | Template: should-refuse / leak-test cases |
| `users.yaml` | **no — gitignored** | Real client identities + access map |
| `questions.yaml` | **no — gitignored** | Real client Q&A |
| `refusals.yaml` | **no — gitignored** | Real client refusal cases |

The `.example.yaml` files are synthetic and safe to share; they define the schema and
are reusable across client deployments. The real (non-`.example`) files hold client
business content and permission structure and are **not** committed.

Treat the real files like the client documents themselves even though they are
gitignored: never paste their contents into issues, prompts, logs, or chat — they
encode who can see what inside the client's organization.

## How to fill it

1. Copy each `*.example.yaml` to the same name without `.example`.
2. Replace the synthetic rows with the client-provided data.
3. The pilot documents themselves go in `data/import/` (also gitignored).

## Target size for the first POC

- ~20 positive questions in `questions.yaml`.
- 3–5 refusal cases in `refusals.yaml`, each with an `allowed_user` and a `denied_user`.
- ≥2 users in `users.yaml` with genuinely different access (so a doc separates them).
