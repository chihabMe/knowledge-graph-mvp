# CLAUDE.md

This is a permission-safe AI knowledge layer POC: Google Drive content is
structured into a Neo4j knowledge graph, Drive sharing permissions are synced
into SpiceDB, and users ask questions through Open WebUI. Retrieval is filtered
by SpiceDB **before** any context reaches the LLM.

## Non-Negotiable Invariants

1. **Permissions before retrieval.** Never send graph, chunk, or document
   context to an LLM unless SpiceDB confirmed the requesting user may see every
   source document it was derived from. A fact one graph-hop away from a
   restricted file is still restricted.
2. **Provenance or exclusion.** Every Neo4j node, relationship, and chunk must
   carry source-document provenance. Anything missing provenance is excluded
   from retrieval. Fail closed.
3. **No ad hoc permission logic.** SpiceDB is the only authorization engine.
   Do not replace or shortcut it with PostgreSQL checks or prompt instructions.

## Before Making Changes

Read `AGENTS.md` — it is the entry point for agents and defines the required
reading order, manual audit policy, and working rules.
For Drive onboarding scope, follow the canonical brief and ADR-009 rather than
assuming manual root IDs are the client-facing path.

## Backend Code Navigation

For questions about backend code or architecture, first query the local
Graphify graph from `apps/backend/` with a narrow budget, for example:

```bash
graphify query "how does Drive sync reach graph extraction?" --budget 800
```

Use the graph for orientation, then read the specific source files needed to
verify or modify code. The graph is local-only at `apps/backend/graphify-out/`;
do not build a full-repository semantic graph or treat Graphify as a runtime
dependency.

## End Of Session

Before the final commit of a work session, write/update the daily report in
`docs/daily-reports/YYYY-MM-DD.md` — format and rules are in `AGENTS.md`.

## Pointers (do not duplicate facts here)

- Current phase status: `ai-context/phases/` trackers and `README.md`.
- Canonical project brief (scope, stack, rules): `AGENT_PROJECT_BRIEF.md`.
- Commands (`make up`, `make test`, `make lint`, `make health`): `Makefile`
  and `README.md`.
