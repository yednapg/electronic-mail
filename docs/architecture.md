# Architecture

Decision Pipeline is a monorepo with one canonical backend and multiple clients.

## Ownership

- `backend/` owns Google OAuth, Gmail and Calendar sync, persistence, grouping, memory, AI decisions, dashboard/feed generation, trace replay, and Gmail archive/unarchive actions.
- `web/` owns the Next.js dashboard rendering and calls the backend API.
- `ios/DecisionPipeline/` owns the SwiftUI native client and calls the backend API. The app target is a thin shell over `DecisionPipelineCore`, where API models, the client, store logic, and SwiftUI screens live.
- `contracts/fixtures/` stores shared JSON payloads that protect the API contract across backend, web, and iOS.

Clients must not duplicate backend intelligence logic. They can format, render, navigate, and trigger explicit backend mutations.

## Data Flow

```text
Gmail / Calendar
      |
      v
FastAPI backend
  - OAuth tokens
  - Sync and normalization
  - Entity grouping and memory
  - AI judgment and dashboard briefing
  - Trace records
  - Gmail archive/unarchive mutations
      |
      v
SQLite database for this pass
      |
      +--> Web dashboard
      |
      +--> SwiftUI iOS app
```

## API Contract

The backend response models are canonical. Web TypeScript types and Swift models must decode the same JSON contract fixtures:

- `dashboard.json`
- `google-auth-state.json`
- `trace.json`
- `gmail-thread-archive.json`
- `gmail-thread-unarchive.json`

OpenAPI-generated clients are intentionally deferred. Shared fixtures are the v1 guardrail because they are simple, readable, and fast for one developer.

## Branching

Use folders for platform ownership and Git branches for work:

- `main`
- `swift`
- `feature/...`
- `fix/...`
- `release/...`

Do not create permanent `backend`, `web`, or `ios` branches.
