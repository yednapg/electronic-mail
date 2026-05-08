# Agent Guide

This repo is the Decision Pipeline monorepo: a FastAPI backend, a Next.js web dashboard, and a SwiftUI iOS client for an email-underneath, work-on-the-surface personal dashboard.

## Non-Negotiables

- The backend is the canonical source of truth. Keep Google OAuth, Gmail/Calendar sync, SQLite persistence, entity grouping, lifecycle/state derivation, AI judgment, dashboard/feed generation, trace replay, Gmail mutations, manual tasks, drafts, and entity outcomes in `backend/`.
- Web and iOS are thin clients. They can render, format, navigate, collect explicit user input, and call backend endpoints. They must not reimplement grouping, ranking, feed projection, lifecycle rules, Gmail sync, or AI behavior.
- Contract changes must move together across `backend/app/schemas/domain.py`, `packages/types/src/index.ts`, `ios/DecisionPipeline/DecisionPipeline/Core/Models.swift`, `contracts/fixtures/`, and the matching backend/web/iOS tests.
- OpenAI keys and model calls stay backend-only. Native clients and web UI do not hold model credentials.
- Preserve the user's uncommitted work. Start with `git status --short`, understand dirty files before editing them, and never revert unrelated changes.

## Repo Map

- `backend/`: FastAPI app, SQLite repository, Google integration, AI/heuristic services, feed pipeline, and Python tests.
- `web/`: Next.js app router dashboard, demo flow, debug views, CSS, web tests.
- `ios/DecisionPipeline/`: Tuist-generated SwiftUI app and `DecisionPipelineCore` framework.
- `packages/types/`: shared TypeScript contracts consumed by web and fixtures.
- `contracts/fixtures/`: JSON API fixtures used by backend, web, and iOS contract tests.
- `docs/`: architecture and environment notes.
- `scripts/`: bootstrap and lightweight iOS run helpers.
- `output/`, `.playwright-cli/`, `web/font/`: screenshots/videos/logs/fonts/assets. Treat as artifacts unless the task is explicitly about visual proof or fonts.

## Local Stack

Run setup once:

```bash
npm run setup
```

Run the backend from `backend/`:

```bash
../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 3001
```

Run the web app from the repo root:

```bash
npm run dev:web
```

Important: root `npm run dev` only starts the web app on `5173`. A usable live dashboard also needs the backend on `3001`. If the web app shows `TypeError: fetch failed` or `ECONNREFUSED`, check `http://127.0.0.1:3001/health` before editing UI code.

Useful URLs:

- Web: `http://localhost:5173`
- Backend: `http://localhost:3001`
- Docs: `http://localhost:3001/docs`
- Health: `http://localhost:3001/health`
- Readiness: `http://localhost:3001/ready`

## Verification

Primary gate:

```bash
npm run verify
```

That runs backend unit tests, backend compile, web typecheck, and web tests.

Individual checks:

```bash
npm run backend:test
npm run backend:compile
npm run web:typecheck
npm run web:test
npm run ios:generate
npm run ios:fast-run
npm run ios:typecheck
npm run ios:build
npm run ios:test
```

iOS requires Tuist, Xcode, and a simulator. `scripts/ios-fast-run.sh` expects a booted simulator or `IOS_SIMULATOR_ID`.

## Backend Boundaries

Entry and config:

- `backend/app/main.py` creates the FastAPI app, initializes SQLite, applies CORS, and includes all routers.
- `backend/app/core/config.py` loads `backend/.env`, resolves the SQLite path, validates production-like readiness, and exposes OpenAI/Google settings.

Routes:

- `system.py`: `/`, `/health`, `/ready`.
- `auth_google.py`: `/auth/google`, `/auth/google/callback`, including web and iOS redirect handling.
- `dashboard.py`: `/dashboard` and `/v1/dashboard/prepare`.
- `feed.py`: `/feed`, `/raw-feed`, `/raw-gmail-api`, `/raw-gmail-api-clean`, `/rebuild-memory`.
- `gmail.py`: explicit Gmail thread mutations and Gmail draft create/update/send/delete.
- `tasks.py`: backend-owned manual task CRUD.
- `entities.py`: complete, snooze, dismiss, and thread-reader endpoints.
- `trace.py`: `/trace/{entity_id}` replay.
- `ai.py`: HTTP wrappers around in-process AI helpers.

Persistence:

- `backend/app/db/repository.py` owns all SQLite schema and access helpers. There are no migrations yet; schema evolution currently happens in `initialize_database()` plus small compatibility helpers like `_ensure_column`.
- `manual_tasks` project into the same entity/feed machinery as Gmail-backed work through `_write_manual_task_projection()`.
- `entity_outcomes` suppress completed, snoozed, and dismissed entities during feed construction.
- `trace_records` are append-only replay evidence for grouping, state derivation, decisioning, action selection, timing, ranking, and output.

Pipeline:

- Google records enter through `backend/app/services/integrations/google.py`.
- `hydrate_persistent_memory()` in `backend/app/services/feed/memory_pipeline.py` resolves records to entities, reconciles cross-thread entities, derives state, and checks count invariants.
- Entity grouping belongs in `backend/app/services/entities/entity_resolver.py` and cross-entity merge logic belongs in `entity_reconciler.py`.
- State derivation belongs in `derive_entity_state.py`; state vocabulary is `open`, `waiting`, `done`.
- AI and deterministic fallback logic belong in `backend/app/services/ai/decision.py`.
- Final section projection and ranking belong in `backend/app/services/feed/build_feed.py`.

## V1 Product Rules

Use the pipeline contract in `PLAN.md` as the product invariant:

- Main sections are `Now`, `Today`, and `Later`/`Worth Knowing`.
- `need_type` is only `decision` or `awareness`; never introduce `review` as a need type.
- Timing and ranking are separate: timing chooses the section, ranking only orders inside it.
- One entity can appear only once.
- Important uncertain items should not be silently hidden.
- Every visible item should explain why it is there and expose trace evidence.

## Web Boundaries

- `web/lib/api.ts` is the main server-side backend API helper. Demo mode is enabled unless `NEXT_PUBLIC_DEMO_MODE=false`.
- `web/app/page.tsx` is the sign-in/landing screen.
- `web/app/post-login/*` runs the animated preparation flow and calls `/api/dashboard/prepare` in live mode.
- `web/app/dashboard/page.tsx` fetches the dashboard and builds view models. Keep it as presentation shaping, not backend logic.
- `web/components/dashboard/*` renders the digest UI, inline section composer, local demo interactions, Gmail CTAs, and expandable details.
- `web/app/raw-feed/page.tsx` and `web/app/trace/[entityId]/page.tsx` are debug evidence surfaces.
- `web/lib/demo-dashboard.ts` and `web/lib/demo-evidence.ts` are demo fixtures only.
- `web/app/globals.css` owns the visual system. Follow `UI.md`: one centered digest column, SF Pro Rounded, no decorative imagery, no gradients, no card-grid product UI.

When adding live backend calls in web, prefer using `getBackendURL()` and the shared contract types. Keep direct browser-only mutations explicit and user-triggered.

## iOS Boundaries

- `ios/DecisionPipeline/Project.swift` defines `DecisionPipelineCore`, the app target, and tests.
- `DecisionPipeline/App/DecisionPipelineApp.swift` is only the app shell.
- `Core/AppConfiguration.swift` defaults the simulator backend to `http://localhost:3001` and defines `decisionpipeline://auth/callback`.
- `Core/Models.swift` mirrors backend/shared contracts.
- `Core/DashboardAPIClient.swift` owns HTTP calls.
- `Core/DashboardStore.swift` owns refresh, OAuth start, backend URL persistence, and explicit item actions.
- `Core/DashboardViews.swift` renders the digest, agenda, sections, and action text.
- `Core/OAuthService.swift` uses `ASWebAuthenticationSession`.
- `Core/TraceAndSettingsViews.swift` owns trace replay and backend URL settings.

Keep iOS presentation aligned with web but do not move product logic into Swift.

## Contract Change Checklist

When adding or changing an API field:

1. Update Pydantic schema in `backend/app/schemas/domain.py`.
2. Update persistence or service output in `backend/app/db/repository.py` and backend services.
3. Update `packages/types/src/index.ts`.
4. Update `ios/DecisionPipeline/DecisionPipeline/Core/Models.swift`.
5. Update `contracts/fixtures/*.json`.
6. Update tests in `backend/tests/`, `web/app/dashboard/contract-fixtures.test.ts`, and `ios/DecisionPipeline/DecisionPipeline/Tests/ModelDecodingTests.swift`.
7. Run the narrow tests first, then `npm run verify`.

## Environment And Secrets

- Local backend defaults are in `backend/.env.example`; local web defaults are in `web/.env.example`.
- SQLite uses `backend/dev.db` by default.
- Google OAuth local artifacts are `backend/.google-oauth.json`, `backend/.google-oauth-session.json`, and `backend/.google-account.json`.
- Dashboard briefing cache is `backend/.dashboard-briefing.json`.
- Do not commit secrets, local DB files, generated caches, `.next`, `__pycache__`, or Xcode user state.

## Testing Patterns

- Backend tests use `unittest`, FastAPI `TestClient`, temporary SQLite databases, and route-module `settings` patching because routers keep module-level `settings = load_settings()`.
- Web tests use Node's built-in test runner through `tsx --test`.
- iOS tests decode shared fixtures and mock `DashboardAPIProviding`.
- For pipeline bugs, add tests near the owning layer: entity resolution/reconciliation, state derivation/feed, Google sync normalization, dashboard briefing, backend-owned work, or contract fixtures.

## Implementation Heuristics

- New source ingestion belongs in the backend integration layer, then persistence, then memory hydration.
- New dashboard work should usually become an `AttentionItem` field or backend endpoint first, then web/iOS render it.
- Manual tasks should stay backend-owned and feed-visible through entity projection.
- Gmail writes must be explicit user actions. Avoid background archive/send behavior unless the user asks for it and tests cover it.
- Use trace rows for explainability when changing grouping, state, decision, timing, ranking, or output.
- Keep product copy plain and personal: "work first, emails underneath." Avoid generic AI-email/productivity language.
