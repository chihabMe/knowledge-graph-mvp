# Phase 6: Open WebUI Integration

## Purpose

Expose the permission-safe backend through Open WebUI as the main user interface.

## Scope

- Open WebUI configuration.
- Google OAuth/OIDC configuration path.
- Django OpenAI-compatible endpoint.
- User identity propagation.
- OpenRouter model routing.

## Out Of Scope

- Building a custom frontend.
- Replacing Open WebUI.

## Tasks

- [x] Decide Open WebUI integration pattern. Effort: High. (ADR-014 selects a
  thin Django `GET /v1/models` + `POST /v1/chat/completions` adapter over the
  existing `answer_query()` service. Open WebUI uses a separate service bearer
  key and short-lived signed identity JWT. No Pipeline/Function or separate
  Pipelines service is used for the primary retrieval path.)
- [ ] Configure Open WebUI service settings. Effort: High.
- [ ] Configure Google auth path. Effort: Extra High.
- [ ] Pass authenticated user identity to backend. Effort: Extra High.
- [ ] Route model calls through OpenRouter. Effort: High.
- [ ] Test end-to-end chat flow. Effort: Extra High.

Detailed execution plan: `docs/phase-6-implementation-plan.md`.

## Validation

- [ ] User can log in.
- [ ] Backend receives trusted user identity.
- [ ] User can ask questions.
- [ ] Backend returns permission-safe cited answers.
- [ ] Restricted facts remain hidden.

## Completion Status

Planning in progress (2026-07-13). The integration pattern is accepted and
documented; implementation and all live validation remain not started.
