# Decision Pipeline System

Minimal monorepo scaffold for the V1 decision pipeline system.

## Structure

```text
.
├── apps
│   ├── api
│   ├── ai
│   └── web
├── packages
│   └── types
├── scripts
│   └── bootstrap.sh
├── package.json
└── package-lock.json
```

## Prerequisites

- Node.js 20+
- npm 10+
- Python 3.11+

## Run the complete app (recommended)

From repo root:

```bash
npm run start
```

This runs:

1. `npm run setup` (idempotent)
2. starts:
   - UI: `http://localhost:5173`
   - API: `http://localhost:3001`
   - AI service: `http://localhost:8001`

## Setup (manual / one-time)

If you prefer to run setup separately:

```bash
npm run setup
```

What `setup` does:

- Installs Node dependencies (`npm install`) if `node_modules` is missing.
- Creates `.env` files in:
  - `apps/api/.env`
  - `apps/web/.env`
  - `apps/ai/.env`
- Creates root `.venv` (if missing).
- Installs Python dependencies for the AI service into root `.venv`.
- Runs `prisma generate` and `prisma db push` for the API DB.

## Individual services

```bash
npm run dev:web
npm run dev:api
npm run dev:ai
```

## Useful endpoints

- Web: `http://localhost:5173`
- API health: `http://localhost:3001/health`
- AI service health: `http://localhost:8001/health`
- AI service docs: `http://localhost:8001/docs`

If you run only `npm run dev`, ensure `.venv` exists and was used to install `apps/ai/requirements.txt`.
