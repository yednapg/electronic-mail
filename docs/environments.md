# Environments

This pass keeps SQLite as the runtime database and makes configuration environment-aware. Postgres and migrations are deferred.

## Backend Variables

| Variable | Local default | Notes |
| --- | --- | --- |
| `APP_ENV` | `local` | One of `local`, `staging`, or `production`. |
| `PORT` | `3001` | Backend HTTP port. |
| `DATABASE_URL` | `file:./dev.db` | SQLite file URL for this pass. |
| `CORS_ORIGIN` | `http://localhost:5173` | Web origin allowed by CORS. Must be HTTPS outside local development. |
| `GMAIL_SYNC_SCOPE` | `recent` | Supported values: `full`, `recent`. |
| `GMAIL_RECENT_DAYS` | `90` | Used when recent sync is enabled. |
| `GOOGLE_CLIENT_ID` | empty | Required for Google OAuth. |
| `GOOGLE_CLIENT_SECRET` | empty | Required for Google OAuth. |
| `GOOGLE_REDIRECT_URI` | `http://localhost:3001/auth/google/callback` | Must be registered with Google. Must be HTTPS outside local development. |
| `MOBILE_REDIRECT_URI` | `decisionpipeline://auth/callback` | iOS app callback URI. |
| `OPENAI_API_KEY` | empty | Enables backend AI judgments, grouping titles, summaries, and feed generation. Keep this server-side only. |
| `OPENAI_MODEL` | `gpt-5.4` | Backend OpenAI model used by the AI pipeline. |
| `OPENAI_REQUIRED` | `false` | Set to `true` when startup/readiness should fail if `OPENAI_API_KEY` is missing. Recommended for staging/production. |
| `OPENAI_DEBUG_LOGS` | `false` | Enables backend AI debug logging for local troubleshooting. |

## Web Variables

| Variable | Local default | Notes |
| --- | --- | --- |
| `DECISION_PIPELINE_BACKEND_URL` | `http://localhost:3001` | Backend API origin used by server-rendered web pages. |

## Local

Use:

```bash
npm run setup
```

Then run backend and web separately:

```bash
cd backend
../.venv/bin/python -m uvicorn app.main:app --reload --port 3001
```

```bash
npm run dev:web
```

## Staging and Production

Set `APP_ENV=staging` or `APP_ENV=production`.

Production-like readiness requires:

- valid `DATABASE_URL`
- HTTPS `CORS_ORIGIN`
- HTTPS `GOOGLE_REDIRECT_URI`
- configured `GOOGLE_CLIENT_ID`
- configured `GOOGLE_CLIENT_SECRET`
- configured `OPENAI_API_KEY` when `OPENAI_REQUIRED=true`
- configured `MOBILE_REDIRECT_URI`

Use `/health` for liveness and `/ready` for deployment readiness. `/ready` checks required config and SQLite schema availability.

## iOS

The simulator defaults to `http://localhost:3001`. Physical devices and TestFlight builds should point Settings at the hosted HTTPS backend. Google OAuth should redirect back to:

```text
decisionpipeline://auth/callback
```

iOS does not call OpenAI directly. It receives canonical dashboard, trace, and Gmail action responses from the backend so the web and native clients stay consistent.
