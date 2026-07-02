# Phase 7: Change Feed And Evaluation

## Purpose

Keep graph data and permissions current, then prove answer quality and leak safety with repeatable tests.

## Scope

- Google Drive change feed.
- Incremental content re-indexing.
- Permission-only sync.
- Evaluation dataset.
- Leak tests.
- Scheduled evaluation jobs.

## Out Of Scope

- Enterprise monitoring dashboards.
- Multi-customer shared deployment.

## Tasks

- [ ] Implement Drive change feed polling. Effort: High.
- [ ] Separate content changes from permission-only changes. Effort: Extra High.
- [ ] Re-index changed content. Effort: High.
- [ ] Refresh permissions without re-embedding. Effort: Extra High.
- [ ] Create evaluation question set. Effort: Medium.
- [ ] Add answer quality tests. Effort: High.
- [ ] Add mandatory leak tests. Effort: Extra High.
- [ ] Add scheduled evaluation task. Effort: High.

## Validation

- [ ] Edited files update graph content.
- [ ] Permission changes update SpiceDB.
- [ ] Restricted answers fail leak tests.
- [ ] Evaluation runner produces useful pass/fail output.

## Completion Status

Not started.

