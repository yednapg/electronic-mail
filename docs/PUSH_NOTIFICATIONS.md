# New-mail push notifications

The Mac and iPhone apps use Apple Push Notification service (APNs). Enable notifications in **Settings → Notifications** on each device. Sound is on by default; sender, subject, and snippet previews are off by default. The active app suppresses banners because its mailbox is already updating.

## Apple setup

An Apple Developer Program membership, an APNs signing key, and provisioning profiles with Push Notifications enabled are required for delivery. Unsigned and self-signed local builds cannot register with APNs. Simulator or unsigned build success alone does not verify push delivery.

1. Enable Push Notifications on both App IDs: `app.electronicmail.mac` and `app.electronicmail.ios`. Regenerate their provisioning profiles. Use the bundle IDs of your signed builds if they differ.
2. [Create an Apple Push Notification service key](https://developer.apple.com/help/account/keys/create-a-private-key/) in the developer account. Its allowed topics must cover both app bundle IDs, and its environment must match the builds being tested (Sandbox for Debug; Production for Release). This backend uses one signing key, so serving both environments requires a key permitted for both. Store its `.p8` file outside this repository, with access limited to the backend process. Record its Key ID and Team ID. Never place this key in the app or commit it.
3. Configure the backend environment:

   ```dotenv
   PUSH_NOTIFICATIONS_ENABLED=true
   APNS_TEAM_ID=your-team-id
   APNS_KEY_ID=your-key-id
   APNS_PRIVATE_KEY_PATH=/absolute/private/path/AuthKey.p8
   APNS_MAC_TOPIC=app.electronicmail.mac
   APNS_IOS_TOPIC=app.electronicmail.ios
   ```

4. Install the hashed backend dependencies, apply `npm run db:migrate` using the database migration role, and restart `npm run backend:dev`. If the runtime uses a separate restricted role, grant it SELECT, INSERT, UPDATE, and DELETE on `push_devices` and `push_deliveries`; it does not need schema-creation privileges. The existing default worker delivers push jobs; no extra service is needed. A backend enabled for push must have the key mounted in both the API and worker environments so readiness checks pass.
5. Build with Apple signing and provisioning enabled. `ELECTRONIC_MAIL_APNS_ENVIRONMENT` is `development` for Debug and `production` for Release; it supplies both the entitlement and `APNSEnvironment` in the app's Info.plist. Match this value to the provisioning profile. When using a custom packaging script or Info.plist, retain both settings and the platform's push entitlement. The existing self-signed beta packaging paths do not support APNs.
6. Keep the backend reachable by the iPhone for login, registration, and opening mail. A local Mac backend must remain running and awake to detect new messages and send notifications. Use the existing Gmail Pub/Sub watch configuration for prompt detection; local polling normally detects mail within 30 seconds.

For the Mac release or local Developer ID install scripts, supply `MACOS_PROVISIONING_PROFILE_SPECIFIER` with the name or UUID of the installed push-enabled provisioning profile. Release verification checks the production APNs entitlement. Self-signed beta builds keep their original minimal entitlements and do not support push.

Apple references: [register an app with APNs](https://developer.apple.com/documentation/usernotifications/registering-your-app-with-apns), [send APNs requests](https://developer.apple.com/documentation/usernotifications/sending-notification-requests-to-apns), and [Mac push entitlement](https://developer.apple.com/documentation/bundleresources/entitlements/com.apple.developer.aps-environment).

## Behavior and verification

Only Gmail `messagesAdded` history events can create alerts. The hydrated message must be unread, in Inbox, outside Sent/Drafts/Spam/Trash, and received within the last 15 minutes. Initial imports, historical backfills, label changes, and old mail recovered after extended downtime remain quiet. Each account/message/device combination has one durable delivery record; queue insertion occurs before the history checkpoint. The worker rechecks the current message, account, session, and preferences before sending. Device tokens are encrypted at rest. Revoked/expired sessions and disabled devices do not receive queued alerts. APNs-invalid tokens are disabled, while temporary failures use the existing job retry policy. An ambiguous network failure can be retried; APNs collapse identifiers reduce duplicates but cannot guarantee exactly-once presentation.

Tap an alert to open its account, thread, and message. Routing checks the signed-in user and connected account before opening it. Alerts contain opaque routing identifiers; email content is included only when previews are enabled on that device.

Disabling notifications removes the device registration from the server. If the device is offline, the app shows a retry status and reconciles the saved preference when it next connects, including after relaunch. Until the server receives that change, it may still send alerts. Revoking notification permission in system settings also removes the server registration on the next app refresh.

For a signed-device test, enable notifications, background the app, and send a new message from a separate email account. Check a banner on both devices, tap through to the correct conversation, then repeat with previews and sound off. Verify that reading the message before delivery, signing out, disabling notifications, and disconnecting the Gmail account prevent queued alerts. Also test relaunching from a notification and a second Gmail account. No real email is sent by the automated tests.

Run backend unit coverage with `PYTHONPATH=backend .venv/bin/python -m unittest backend.tests.test_push_notifications`. Its Postgres tests additionally require `PUSH_TEST_DATABASE_URL` pointing at a separate, migrated loopback database whose name starts with `push_test_`. Tests create and remove their own user fixtures. Swift routing tests are the three `testNotification…` methods in `InboxStoreTests`; `MailNotificationControllerTests` covers offline disable/relaunch, revoked permission, and signed-out behavior without contacting APNs.
