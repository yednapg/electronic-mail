# Decision Pipeline System

Production-oriented monorepo for the Decision Pipeline backend, web dashboard, and native iOS client.

The FastAPI backend is the canonical source of truth. Web and iOS render backend output and trigger backend actions; they do not duplicate Gmail sync, grouping, memory, AI judgment, dashboard generation, trace replay, or Gmail mutation logic.

## Structure

```text
backend/              FastAPI API, Gmail integration, SQLite persistence, AI/feed pipeline
web/                  Next.js dashboard client
ios/DecisionPipeline/ SwiftUI iOS client
packages/types/       Shared TypeScript contracts for web-side code
contracts/fixtures/   Shared JSON API contract fixtures
docs/                 Architecture and environment notes
scripts/              Local setup helpers
```

## Setup

Prerequisites:

- Node.js 20+
- npm 10+
- Python 3.11+
- Tuist and Xcode for iOS work

```bash
npm run setup
```

This installs Node and Python dependencies, creates `backend/.env` and `web/.env` from examples when missing, and initializes local SQLite.

## Development

Run the backend:

```bash
cd backend
../.venv/bin/python -m uvicorn app.main:app --reload --port 3001
```

Run the web dashboard:

```bash
npm run dev:web
```

Generate the iOS project:

```bash
npm run ios:generate
```

The local URLs are:

- Web: `http://localhost:5173`
- Backend: `http://localhost:3001`
- Backend docs: `http://localhost:3001/docs`
- Backend health: `http://localhost:3001/health`
- Backend readiness: `http://localhost:3001/ready`

## Verification

Run backend and web checks:

```bash
npm run verify
```

Run iOS checks once an iOS simulator runtime is installed:

```bash
npm run verify:ios
```

Individual commands:

```bash
npm run backend:test
npm run backend:compile
npm run web:typecheck
npm run web:test
npm run ios:typecheck
npm run ios:build
npm run ios:test
```

## Environment

Local defaults use SQLite and local OAuth redirects. Production or TestFlight should use a hosted HTTPS backend.
The AI pipeline uses OpenAI from the backend via `OPENAI_API_KEY` and `OPENAI_MODEL`; native clients do not hold model credentials or run canonical feed logic.

See [architecture](docs/architecture.md) and [environments](docs/environments.md).
