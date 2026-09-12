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

## iPhone development

`ElectronicMailiOS` is the portrait iPhone client (iOS 17+). Debug Simulator builds use `http://localhost:3001`; the deterministic visual-QA mode uses the in-process demo client and never needs a backend.

Boot an iPhone Simulator in Xcode, then run:

```bash
npm run ios:simulator:build
npm run ios:simulator:run
npm run ios:test
npm run ios:ui:test
```

The run command launches with `-ElectronicMailDemo`. For a signed physical-device build, keep the local backend running and use:

```bash
npm run ios:device:run
```

When exactly one iPhone is connected, the script selects it automatically. It also starts a zero-cost LAN relay from this Mac to the local backend and embeds that address in the build. The iPhone app temporarily receives the Google OAuth localhost callback and forwards it to the LAN backend, so local device sign-in does not require a public tunnel or a second Google OAuth configuration.

You can inspect or stop the relay explicitly:

```bash
npm run ios:lan:status
npm run ios:lan:stop
```

To use a hosted backend instead, provide its canonical HTTPS origin:

```bash
IOS_DEVICE_ID=<udid> \
ELECTRONIC_MAIL_IOS_BACKEND_URL=https://mail-api.example.com \
npm run ios:device:run
```

All iOS build scripts honor `DEVELOPER_DIR` and otherwise select an installed full Xcode, including Xcode beta, without changing global `xcode-select`.

## OAuth

The macOS auth redirect URL is:

```text
electronicmail://auth/callback
```
