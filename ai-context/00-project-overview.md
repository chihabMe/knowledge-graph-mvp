# Project Overview And Goal

## One-Sentence Purpose

Build a permission-safe AI knowledge layer that lets a company ask questions about its Google Drive content without exposing facts from documents the user is not allowed to see.

## Product Goal

The system should ingest an organization's Google Drive documents, structure their content into a Neo4j knowledge graph, enforce Google Drive-derived permissions through SpiceDB, and answer questions through Open WebUI using only context the requesting user is allowed to access.

## What The Software Should Do

- Connect to a customer's Google Workspace / Google Drive.
- Read supported files from a configured Drive folder or shared drive.
- Extract document text, chunks, entities, relationships, and metadata.
- Store graph facts and source provenance in Neo4j.
- Sync Drive permissions into SpiceDB.
- Let users ask questions through Open WebUI.
- Filter retrieval by SpiceDB before context reaches the LLM.
- Call OpenRouter for model responses.
- Return answers with citations to accessible source documents.
- Refuse or say there is insufficient accessible context when the answer depends on restricted documents.

## Core Promise

If a user cannot access the original document in Google Drive, the AI cannot use facts from that document to answer them.

## Product Boundary

This is not a full SaaS product yet. The first production direction is a single-customer deployment package:

- One customer per VM/deployment.
- No shared datastore across customers.
- Client owns the deployment.
- Monthly maintenance can be sold separately.

## First MVP Success Criteria

- Google Drive files can be ingested.
- Neo4j contains provenance-rich graph data.
- SpiceDB contains synced visibility relationships.
- Retrieval is filtered before calling the LLM.
- Open WebUI can ask questions through the backend.
- Source citations are returned.
- Leak tests pass.

