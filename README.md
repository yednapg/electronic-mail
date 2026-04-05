# Decision Pipeline System

Repo with a TypeScript frontend and one Python backend.

## Structure

```text
.
├── backend       # Unified FastAPI backend: API routes, AI services, integrations, SQLite
├── web           # Next.js frontend
├── packages
│   └── types    # shared frontend TypeScript contracts
├── scripts
│   └── bootstrap.sh
├── package.json
└── package-lock.json
```

## Prerequisites

- Node.js 20+
- npm 10+
- Python 3.11+

## Frontend

From repo root:

```bash
npm run setup
npm run dev
```

This starts the Next.js UI on `http://localhost:5173`.

## Setup (manual / one-time)

If you prefer to run setup separately:

```bash
npm run setup
```

What `setup` does:

- Installs Node dependencies (`npm install`) if `node_modules` is missing.
- Creates `.env` files in:
  - `backend/.env`
  - `web/.env`
- Creates root `.venv` (if missing).
- Installs Python dependencies for `backend` into root `.venv`.
- Initializes the backend SQLite database with Python.

## Frontend-only npm commands

```bash
npm run dev
npm run test
npm run typecheck
```

## Backend Python commands

```bash
cd backend
../.venv/bin/python -m uvicorn app.main:app --reload --port 3001
```

Test backend:

```bash
cd backend
../.venv/bin/python -m unittest discover -s tests -t . -p 'test_*.py'
```

Typecheck backend imports:

```bash
cd backend
../.venv/bin/python -m compileall -q app tests db_init.py reset_data.py
```

Initialize backend DB:

```bash
cd backend
../.venv/bin/python db_init.py
```

Reset backend data:

```bash
cd backend
../.venv/bin/python reset_data.py
```

## Useful endpoints

- Web: `http://localhost:5173`
- Backend health: `http://localhost:3001/health`
- Backend trace replay: `http://localhost:3001/trace/<entity_id>`
- Backend docs: `http://localhost:3001/docs`

Use the shared root `.venv` created by `npm run setup`.
