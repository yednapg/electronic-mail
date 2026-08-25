# Private macOS source beta

This is the shortest supported path for trusted testers. The app, API, workers, Gmail poller, and Postgres database all run on the tester's Mac. It is not TestFlight and does not require an Apple Developer account, Railway, a public domain, signing, or notarization.

## 1. Install the local tools

Use macOS 14 or newer and install:

- full Xcode from the App Store; open it once to finish installation;
- Homebrew and nvm.

Clone the private repository and check out the beta commit supplied by the maintainer. From the repository root, run:

```bash
npm run beta:setup -- your-gmail-address@gmail.com
```

The command selects or installs Node `22.22.0` through nvm, selects or installs Python `3.12.13`, installs locked dependencies, creates and migrates the local Postgres database, creates `backend/.env.local` with mode `0600`, generates separate local session/encryption secrets, and fixes the local callback and no-AI settings. It may install `uv` and PostgreSQL 16 through Homebrew when they are missing.

## 2. Create a Google test project

Each tester must use a separate Google Cloud project. Do not share the maintainer's client secret.

1. In Google Cloud, create a project such as **Electronic Mail Private Test**.
2. Enable the **Gmail API**.
3. Open **Google Auth Platform** and complete **Branding** with an app name, support email, and developer email.
4. Under **Audience**, select **External**, leave publishing status as **Testing**, and add the same Gmail address passed to `beta:setup` as a test user.
5. Under **Data Access**, add exactly these scopes:
   - `openid`
   - `https://www.googleapis.com/auth/userinfo.email`
   - `https://www.googleapis.com/auth/userinfo.profile`
   - `https://mail.google.com/`
6. Under **Clients**, create an **OAuth client ID** with application type **Web application**.
7. Add this single authorized redirect URI exactly:

   ```text
   http://localhost:3001/auth/google/callback
   ```

No authorized JavaScript origin or authorized domain is needed for this local callback. Do not add the old `loca.lt` or Railway addresses.

Download the client's JSON file and import it without printing either credential:

```bash
.venv/bin/python scripts/private_beta_env.py import-google-client \
  --credentials-json "$HOME/Downloads/client_secret_....json"
```

The importer rejects non-web clients and clients missing the exact local callback. Delete the downloaded JSON after a successful import; the protected, ignored `backend/.env.local` is the only local copy the app needs.

Alternatively, add the client values directly to `backend/.env.local`:

```dotenv
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret
```

Never paste these values into GitHub, screenshots, chat, or issue reports.

## 3. Check and start

```bash
npm run beta:check
npm run beta:start
```

`beta:check` validates the Mac toolchain, local configuration, Postgres schema, backend port, full Gmail scope, and no-AI contract without printing credentials. `beta:start` runs that check, starts the API and background processes, builds the native app, and opens it. Keep its Terminal window open while using the app; press Control-C there to stop the local backend.

On first sign-in, Google may display an unverified/testing warning because the project requests full Gmail access. Confirm that the screen names the tester's own project and account, then use Google's **Continue** action. Testing-mode refresh grants can expire, so a later request to sign in again is expected.

## Sign-in troubleshooting

- **Something went wrong before returning to the app:** confirm Gmail API is enabled, the account is listed under Audience test users, all four scopes are configured, and the client is a Web application.
- **Redirect mismatch:** the Google client and `backend/.env.local` must both use `http://localhost:3001/auth/google/callback` exactly.
- **The app keeps waiting:** close other Electronic Mail builds that may own the `electronicmail://` callback, stop the beta with Control-C, and run `npm run beta:start` again.
- **Configuration changed:** restart the complete beta runtime; changing `.env.local` does not update an already-running backend.

For a private GitHub issue, include the macOS version, the short commit printed by `beta:check`, the failing step, and redacted Terminal output. Never include OAuth codes, client secrets, access/refresh tokens, email subjects, message bodies, recipients, or attachments.
