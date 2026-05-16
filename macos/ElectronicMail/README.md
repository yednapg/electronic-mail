# Electronic Mail macOS

Native SwiftUI macOS client for Electronic Mail.

This project is macOS-only. It does not build mobile, Catalyst, or simulator targets. The default run mode talks to FastAPI at `http://localhost:3001`.

## Development

Generate the Xcode project:

```bash
cd macos/ElectronicMail
tuist generate
```

Build with the low-memory MacBook workflow:

```bash
xcodebuild build \
  -workspace macos/ElectronicMail/ElectronicMail.xcworkspace \
  -scheme ElectronicMail \
  -destination 'platform=macOS' \
  COMPILER_INDEX_STORE_ENABLE=NO \
  -jobs 2 \
  -derivedDataPath /tmp/ElectronicMailDerivedData \
  CODE_SIGNING_ALLOWED=NO
```

Open the built app:

```bash
open /tmp/ElectronicMailDerivedData/Build/Products/Debug/ElectronicMail.app
```

Run unit tests:

```bash
xcodebuild test \
  -workspace macos/ElectronicMail/ElectronicMail.xcworkspace \
  -scheme ElectronicMail \
  -destination 'platform=macOS' \
  COMPILER_INDEX_STORE_ENABLE=NO \
  -jobs 2 \
  -derivedDataPath /tmp/ElectronicMailDerivedData \
  CODE_SIGNING_ALLOWED=NO
```

From the repo root, the same workflow is available as:

```bash
npm run macos:generate
npm run macos:build
npm run macos:open
npm run macos:test
```

## Backend Mode

The macOS app runs against the local FastAPI backend by default:

```bash
open /tmp/ElectronicMailDerivedData/Build/Products/Debug/ElectronicMail.app
```

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
