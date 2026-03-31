# Decision Pipeline System

Minimal monorepo scaffold for the V1 decision pipeline system.

## Structure

```text
.
├── apps
│   ├── api
│   └── web
├── packages
│   └── types
├── services
│   └── decision
├── .eslintrc.cjs
├── .prettierrc.json
├── package.json
└── tsconfig.base.json
```

## Prerequisites

- Node.js 20+
- npm 10+
- Python 3.11+

## Setup

1. Install Node dependencies:

```bash
npm install
```

2. Install Python dependencies:

```bash
python3 -m pip install -r services/decision/requirements.txt
```

3. Create env files:

```bash
cp apps/api/.env.example apps/api/.env
cp apps/web/.env.example apps/web/.env
```

4. Generate the Prisma client:

```bash
npm run db:generate
```

5. Create the local SQLite database:

```bash
npm run db:push
```

The API uses a local SQLite database file at `apps/api/dev.db`.

## Run

Run all services:

```bash
npm run dev
```

Individual services:

```bash
npm run dev:web
npm run dev:api
npm run dev:decision
```

## Endpoints

- Web: `http://localhost:5173`
- API health: `http://localhost:3001/health`
- Decision service health: `http://localhost:8001/health`
