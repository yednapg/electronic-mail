# Decision Pipeline iOS

Native SwiftUI client for the Decision Pipeline backend.

The app target is intentionally thin. `DecisionPipelineCore` contains the backend API models, API client, dashboard store, and SwiftUI screens so unit tests can run against the client logic without launching the app as a hosted test target.

## Development

Generate the Xcode project:

```bash
cd ios/DecisionPipeline
tuist generate
```

Build for the iOS simulator:

```bash
xcodebuild -workspace DecisionPipeline.xcworkspace -scheme DecisionPipeline -destination 'platform=iOS Simulator,name=iPhone 17' build
```

Run unit tests:

```bash
xcodebuild test -workspace DecisionPipeline.xcworkspace -scheme DecisionPipeline -destination 'platform=iOS Simulator,name=iPhone 17'
```

The simulator app defaults to `http://localhost:3001`. For a physical device or TestFlight build, point the Settings screen at the hosted HTTPS backend.

## OAuth

The app opens:

```text
/auth/google?redirect_to=decisionpipeline://auth/callback
```

The backend stores Google tokens and redirects back to the app URL scheme after the OAuth callback completes.
