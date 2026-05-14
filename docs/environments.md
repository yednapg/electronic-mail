# Environments

Local development still uses SQLite by default. Beta/staging uses hosted Postgres through Supabase, with Alembic
migrations and production readiness checks. Production-like configuration fails closed unless app-session, encryption,
Google, allowlist, Pub/Sub, and Postgres database settings are present.

## Backend Variables

| Variable | Local default | Notes |
| --- | --- | --- |
| `APP_ENV` | `local` | One of `local`, `staging`, or `production`. |
| `APP_USER_ID` | `local-user` | Local-dev fallback owner only. Request paths should use the authenticated app user. |
| `PORT` | `3001` | Backend HTTP port. |
| `DATABASE_URL` | `file:./dev.db` | SQLite file URL locally. Must be a Supabase `postgres://` or `postgresql://` URL outside local development. |
| `CORS_ORIGIN` | `http://localhost:5173` | Web origin allowed by CORS. Must be HTTPS outside local development. |
| `WEB_APP_URL` | `CORS_ORIGIN` | Web redirect target after OAuth. Must be HTTPS outside local development. |
| `APP_SESSION_SECRET` | local dev secret | Secret material used to hash opaque web/iOS app session tokens. Required outside local development. |
| `SESSION_COOKIE_DOMAIN` | empty | Shared hosted cookie domain, for example `.example.com`, so the Railway API and Vercel web app can share `dp_session`. Required outside local development. |
| `SESSION_COOKIE_SAMESITE` | `lax` | Use `none` outside local development when web/backend are on separate hosted subdomains. |
| `APP_ENCRYPTION_KEY` | local dev secret | Secret material used to encrypt per-user Google OAuth tokens. Required outside local development. |
| `BETA_ALLOWED_EMAILS` | empty | Comma-separated Google email allowlist. Required outside local development. |
| `GMAIL_SYNC_SCOPE` | `recent` | Supported values: `full`, `recent`. |
| `GMAIL_RECENT_DAYS` | `90` | Used when recent sync is enabled. |
| `GMAIL_PUBSUB_TOPIC` | empty | Cloud Pub/Sub topic for Gmail `users.watch` notifications. Required outside local development. |
| `GMAIL_PUBSUB_SUBSCRIPTION` | empty | Cloud Pub/Sub subscription pulled by the mailbox sync worker. Required outside local development. |
| `GMAIL_WATCH_RENEWAL_HOURS` | `24` | Target cadence for renewing Gmail watches before their 7-day expiration. |
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
| `NEXT_PUBLIC_DECISION_PIPELINE_BACKEND_URL` | `http://localhost:3001` | Backend API origin used by browser-side Dashboard/Gmail cache refreshes. |

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

Use Supabase for Postgres, Railway for the backend, and Vercel for the web app. In Railway, set the backend root
directory to `backend`, use `backend/railway.json`, and run migrations before pointing traffic at the service:

```bash
cd backend
alembic upgrade head
```

Production-like readiness requires:

- Postgres-shaped `DATABASE_URL`
- configured `APP_SESSION_SECRET`
- configured `SESSION_COOKIE_DOMAIN`
- `SESSION_COOKIE_SAMESITE=none`
- configured `APP_ENCRYPTION_KEY`
- configured `BETA_ALLOWED_EMAILS`
- HTTPS `CORS_ORIGIN`
- HTTPS `WEB_APP_URL`
- HTTPS `GOOGLE_REDIRECT_URI`
- configured `GOOGLE_CLIENT_ID`
- configured `GOOGLE_CLIENT_SECRET`
- configured `GMAIL_PUBSUB_TOPIC`
- configured `GMAIL_PUBSUB_SUBSCRIPTION`
- configured `OPENAI_API_KEY` when `OPENAI_REQUIRED=true`
- configured `MOBILE_REDIRECT_URI`

`backend/railway.json` runs `python -m app.deploy_check` before starting Uvicorn, so a Railway deployment fails before
serving traffic if required beta variables are missing or if `DATABASE_URL` is not Postgres.

Use `/health` for liveness and `/ready` for deployment readiness. `/ready` checks required config, database
connectivity, the Alembic baseline revision, and the expected schema. In staging/prod it reports
`"database": "postgres"` only after the Supabase database is reachable and migrated.

For Gmail Pub/Sub, either configure Pub/Sub push to `POST /v1/mailbox/pubsub` or run a scheduled worker that calls
`pull_pubsub_notifications(settings)`. A scheduled worker should also call `renew_expiring_gmail_watches(settings)` so
inactive beta users keep their Gmail watches alive.

For Vercel, set:

- `DECISION_PIPELINE_BACKEND_URL` to the Railway backend URL
- `NEXT_PUBLIC_DECISION_PIPELINE_BACKEND_URL` to the Railway backend URL, unless the web app is later switched to a same-origin API proxy only

## iOS

The simulator defaults to `http://localhost:3001`. Physical devices and TestFlight builds should point Settings at the hosted HTTPS backend. Google OAuth should redirect back to:

```text
decisionpipeline://auth/callback
```

iOS does not call OpenAI directly. It receives canonical dashboard, trace, and Gmail action responses from the backend so the web and native clients stay consistent.
