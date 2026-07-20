# Phase 9: Optional Production Hardening

## Purpose

Add operational scale, latency, and assurance features only when a production
deployment has evidence that the simpler POC design is insufficient.

## Scope

- Select an external alert consumer for the vendor-neutral freshness endpoint
  and validate delivery before permission evidence expires.
- Re-evaluate and load-test a 5-minute visibility refresh with 10-minute
  positive-evidence expiry.
- Add Drive change-feed polling only as an accelerator over periodic sweeps.
- Consider HTTPS push channels only when lower latency is contractually needed.
- Reconcile Shared Drive change logs and inherited-folder change fan-out.
- Add scheduled and/or persisted evaluation only if recurring assurance is an
  operational requirement.
- Do not add an embedded monitoring container; external alert delivery remains
  optional and vendor-neutral.

## Tasks

- [ ] Select and configure an external alert destination. Effort: Medium.
- [ ] Run a live alert-delivery drill before tightening evidence expiry.
  Effort: High.
- [ ] Load-test and, if justified, enable the 5-minute/10-minute target.
  Effort: High.
- [ ] Implement Drive change-feed polling with durable cursors, bounded
  coalescing, and periodic reconciliation fallback. Effort: Extra High.
- [ ] Add Shared Drive and inherited-folder change fan-out if required by the
  deployed corpus. Effort: Extra High.
- [ ] Add scheduled/persisted evaluation only if operator-run evaluation is
  insufficient. Effort: High.

## Validation

- [ ] Every adopted feature has measured operational value and a rollback.
- [ ] Change-feed failure never disables periodic reconciliation.
- [ ] Alert delivery is proven before any tighter fail-closed deadline.
- [ ] Scheduled evaluation stores no client questions, answers, identities, or
  source content.

## Completion Status

Deferred. None of these items blocks the POC or Phase 8 handoff.
