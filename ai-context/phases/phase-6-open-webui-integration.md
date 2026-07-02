# Phase 6: Open WebUI Integration

## Purpose

Expose the permission-safe backend through Open WebUI as the main user interface.

## Scope

- Open WebUI configuration.
- Google OAuth/OIDC configuration path.
- Backend pipeline or OpenAI-compatible endpoint.
- User identity propagation.
- OpenRouter model routing.

## Out Of Scope

- Building a custom frontend.
- Replacing Open WebUI.

## Tasks

- [ ] Decide Open WebUI integration pattern. Effort: High.
- [ ] Configure Open WebUI service settings. Effort: High.
- [ ] Configure Google auth path. Effort: Extra High.
- [ ] Pass authenticated user identity to backend. Effort: Extra High.
- [ ] Route model calls through OpenRouter. Effort: High.
- [ ] Test end-to-end chat flow. Effort: Extra High.

## Validation

- [ ] User can log in.
- [ ] Backend receives trusted user identity.
- [ ] User can ask questions.
- [ ] Backend returns permission-safe cited answers.
- [ ] Restricted facts remain hidden.

## Completion Status

Not started.

