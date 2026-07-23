# Electronic Mail macOS

Native SwiftUI macOS client for Electronic Mail.

The launch target is the native macOS app. The default Debug run mode talks to FastAPI at `http://localhost:3001`.

## Development

After the one-time `npm run setup`, use the Mac-first development command:

```bash
npm run dev
```

It starts the local API, workers, and Gmail poller when they are not already running, waits for the API, then builds and opens the native app. The command keeps the backend alive until you press Control-C.

The backend can still be run separately when needed:

```bash
npm run dev:backend
```

The focused native commands are:

```bash
npm run macos:generate
npm run macos:build
npm run macos:open
npm run macos:test
npm run verify
```

Before any production signing attempt, run the credential-free universal Release preflight:

```bash
npm run release:macos:preflight
```

The supporting web surface is only for OAuth/legal/support pages and remains explicit:

```bash
npm run dev:web
```

## Backend Mode

The macOS app runs against the local FastAPI backend by default:

The macOS app should never connect directly to Postgres. The live path is:

```text
macOS app -> FastAPI on localhost:3001 -> dev Postgres
```

Demo fixtures are still available for tests and previews, but the shipped app does not fall back to demo data.

## OAuth

The macOS auth redirect URL is:

```text
electronicmail://auth/callback
```
