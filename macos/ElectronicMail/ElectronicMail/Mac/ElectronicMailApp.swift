import ElectronicMailCore
import AppKit
import SwiftUI

@main
struct ElectronicMailApp: App {
    @NSApplicationDelegateAdaptor(ElectronicMailAppDelegate.self) private var appDelegate

    var body: some Scene {
        Settings {
            EmptyView()
        }
        .commands {
            CommandGroup(after: .appInfo) {
                Button("Open Command Palette") {
                    NotificationCenter.default.post(name: .electronicMailOpenCommandPalette, object: nil)
                }
                .keyboardShortcut("k", modifiers: [.command])

                Button("New Message") {
                    NotificationCenter.default.post(name: .electronicMailOpenComposer, object: nil)
                }
                .keyboardShortcut("n", modifiers: [.command])
            }
        }
    }
}

@MainActor
private final class ElectronicMailAppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    private let store: InboxStore
    private var window: NSWindow?

    override init() {
        let localMailStore = AppClientFactory.makeLocalMailStore()
        self.store = InboxStore(
            client: AppClientFactory.makeDefaultClient(localMailStore: localMailStore),
            localMailStore: localMailStore
        )
        super.init()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        showMainWindow()
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showMainWindow()
        return false
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }

    func windowWillClose(_ notification: Notification) {
        guard notification.object as? NSWindow === window else {
            return
        }
        window = nil
    }

    private func showMainWindow() {
        if let window {
            window.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            return
        }

        let rootView = ElectronicMailRootView(store: store)
            .frame(minWidth: 1100, minHeight: 680)
            .background(WindowTrafficLightOffset())

        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1440, height: 900),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        window.title = "Electronic Mail"
        window.titleVisibility = .hidden
        window.titlebarAppearsTransparent = true
        window.isMovableByWindowBackground = true
        window.minSize = NSSize(width: 1100, height: 680)
        window.contentView = NSHostingView(rootView: rootView)
        window.delegate = self
        window.center()

        self.window = window
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }
}

private struct WindowTrafficLightOffset: NSViewRepresentable {
    func makeNSView(context: Context) -> NSView {
        let view = NSView(frame: .zero)
        DispatchQueue.main.async {
            Self.apply(to: view.window)
        }
        return view
    }

    func updateNSView(_ nsView: NSView, context: Context) {
        DispatchQueue.main.async {
            Self.apply(to: nsView.window)
        }
    }

    private static func apply(to window: NSWindow?) {
        guard let window, !WindowTrafficLightOffsetState.configured.contains(ObjectIdentifier(window)) else {
            return
        }

        for buttonType in [NSWindow.ButtonType.closeButton, .miniaturizeButton, .zoomButton] {
            guard let button = window.standardWindowButton(buttonType) else {
                continue
            }
            button.setFrameOrigin(NSPoint(x: button.frame.origin.x + 7, y: button.frame.origin.y - 7))
        }

        WindowTrafficLightOffsetState.configured.insert(ObjectIdentifier(window))
    }
}

private enum WindowTrafficLightOffsetState {
    static var configured: Set<ObjectIdentifier> = []
}

private enum AppLaunchStage {
    case signIn
    case setup
    case app
}

private struct ElectronicMailRootView: View {
    @ObservedObject var store: InboxStore
    @State private var stage: AppLaunchStage = .signIn
    @State private var setupStartedAt = Date()
    @State private var setupError: String?
    @State private var setupLongWait = false
    @State private var signInInProgress = false
    @State private var signInError: String?

    private let tokenStore = MacSessionTokenStore()

    var body: some View {
        ZStack {
            switch stage {
            case .signIn:
                GoogleSignInView(
                    isSigningIn: signInInProgress,
                    errorMessage: signInError
                ) {
                    Task { await performGoogleSignIn() }
                }
                .transition(.opacity)
            case .setup:
                SetupAnimationView(
                    startedAt: setupStartedAt,
                    readiness: store.currentReadiness,
                    isLongWait: setupLongWait,
                    errorMessage: setupError
                ) {
                    Task { await startSetupFlow(minimumDisplaySeconds: 0, longWaitStatusSeconds: 60) }
                }
                .transition(.opacity)
            case .app:
                SignedInShellView(store: store) {
                    try await performGoogleReauthorization()
                }
                    .onAppear {
                        store.startLiveRefreshLoop()
                    }
                    .transition(.opacity)
            }
        }
        .task {
            await restoreExistingSessionIfAvailable()
        }
        .onOpenURL { url in
            GoogleOAuthService.handleCallbackURL(url)
        }
    }

    @MainActor
    private func restoreExistingSessionIfAvailable() async {
        guard stage == .signIn, let token = tokenStore.load(), !token.isEmpty else {
            return
        }

        store.setSessionToken(token)
        await store.load()

        if case .failed(let message) = store.phase {
            tokenStore.clear()
            store.setSessionToken(nil)
            signInError = message
            return
        }

        if store.isReadyForMainInterface {
            withAnimation(.easeInOut(duration: 0.35)) {
                stage = .app
            }
            return
        }

        await startSetupFlow(minimumDisplaySeconds: 0, longWaitStatusSeconds: 60)
    }

    @MainActor
    private func performGoogleSignIn() async {
        guard !signInInProgress else {
            return
        }

        signInInProgress = true
        signInError = nil

        do {
            let loginCode = try await GoogleOAuthService().startGoogleAuthentication(
                baseURL: store.backendURL,
                authRedirectURI: AppConfiguration.authRedirectURI
            )
            let session = try await store.exchangeMobileSession(loginCode: loginCode)
            try tokenStore.save(session.sessionToken)
            await store.load()

            if case .failed(let message) = store.phase {
                throw RuntimeError(message)
            }

            await startSetupFlow(minimumDisplaySeconds: 0, longWaitStatusSeconds: 60)
        } catch {
            tokenStore.clear()
            store.setSessionToken(nil)
            signInError = error.localizedDescription
        }

        signInInProgress = false
    }

    @MainActor
    private func performGoogleReauthorization() async throws {
        let loginCode = try await GoogleOAuthService().startGoogleAuthentication(
            baseURL: store.backendURL,
            authRedirectURI: AppConfiguration.authRedirectURI
        )
        let session = try await store.exchangeMobileSession(loginCode: loginCode)
        try tokenStore.save(session.sessionToken)
        await store.load()
    }

    @MainActor
    private func startSetupFlow(minimumDisplaySeconds: TimeInterval, longWaitStatusSeconds: TimeInterval) async {
        setupStartedAt = Date()
        setupError = nil
        setupLongWait = false

        withAnimation(.easeInOut(duration: 0.35)) {
            stage = .setup
        }

        let ready = await PostLoginCoordinator(store: store).waitForReadiness(
            minimumDisplaySeconds: minimumDisplaySeconds,
            longWaitStatusSeconds: longWaitStatusSeconds
        ) {
            setupLongWait = true
        }

        guard ready else {
            setupError = store.currentReadiness?.errorMessage ?? "Inbox setup failed. Try again in a moment."
            return
        }

        guard stage == .setup else {
            return
        }
        withAnimation(.easeInOut(duration: 0.45)) {
            stage = .app
        }
    }
}

@MainActor
private struct PostLoginCoordinator {
    let store: InboxStore

    func waitForReadiness(
        minimumDisplaySeconds: TimeInterval,
        longWaitStatusSeconds: TimeInterval,
        onLongWait: () -> Void
    ) async -> Bool {
        let startedAt = Date()
        var longWaitReported = false
        while !Task.isCancelled {
            await store.refreshForReadiness()
            let elapsed = Date().timeIntervalSince(startedAt)
            if elapsed >= minimumDisplaySeconds, store.isReadyForMainInterface {
                return true
            }
            if store.currentReadiness?.stage == "failed" {
                return false
            }
            if !longWaitReported, elapsed >= longWaitStatusSeconds {
                longWaitReported = true
                onLongWait()
            }
            let sleepSeconds = max(0.25, min(2, minimumDisplaySeconds - elapsed > 0 ? minimumDisplaySeconds - elapsed : 2))
            try? await Task.sleep(nanoseconds: UInt64(sleepSeconds * 1_000_000_000))
        }
        return false
    }
}

private final class MacSessionTokenStore: SessionTokenStoring {
    #if DEBUG
    private let store = UserDefaultsSessionTokenStore()
    #else
    private let store = KeychainSessionTokenStore()
    #endif

    func load() -> String? {
        store.load()
    }

    func save(_ token: String) throws {
        try store.save(token)
    }

    func clear() {
        store.clear()
    }
}

private struct GoogleSignInView: View {
    @Environment(\.colorScheme) private var colorScheme
    let isSigningIn: Bool
    let errorMessage: String?
    let onSignIn: () -> Void

    var body: some View {
        ZStack {
            ElectronicMailDesign.background(for: colorScheme)
                .ignoresSafeArea()

            VStack(spacing: 32) {
                HStack(spacing: 16) {
                    Image(systemName: "envelope.open.fill")
                        .symbolRenderingMode(.palette)
                        .foregroundStyle(ElectronicMailDesign.selectedText(for: colorScheme), ElectronicMailDesign.appleBlue)

                    Image(systemName: "arrow.right")
                        .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))

                    Image(systemName: "checkmark.square.fill")
                        .symbolRenderingMode(.palette)
                        .foregroundStyle(.white, Color.green)
                }
                .font(ElectronicMailType.sectionTitle())

                Text("Electronic Mail")
                    .font(ElectronicMailType.sectionTitle())
                    .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                    .tracking(ElectronicMailType.bodyTracking)

                Button(action: onSignIn) {
                    Text(isSigningIn ? "Signing in ..." : "Sign in with Google")
                        .font(ElectronicMailType.body())
                        .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                        .padding(.horizontal, 20)
                        .frame(height: 50)
                    .background(
                        RoundedRectangle(cornerRadius: 7, style: .continuous)
                            .fill(ElectronicMailDesign.controlFill(for: colorScheme))
                    )
                    .overlay(
                        RoundedRectangle(cornerRadius: 7, style: .continuous)
                            .stroke(ElectronicMailDesign.panelBorder(for: colorScheme), lineWidth: 1)
                    )
                }
                .buttonStyle(.plain)
                .disabled(isSigningIn)
                .help("Sign in with Google")

                if let errorMessage {
                    Text(errorMessage)
                        .font(ElectronicMailType.small())
                        .foregroundStyle(Color.red.opacity(0.82))
                        .multilineTextAlignment(.center)
                        .frame(maxWidth: 560)
                }
            }
            .padding(.top, 18)
        }
        .environment(\.font, .system(.body, design: .rounded))
    }
}

private struct SetupAnimationView: View {
    @Environment(\.colorScheme) private var colorScheme

    let startedAt: Date
    let readiness: PostLoginReadinessResponse?
    let isLongWait: Bool
    let errorMessage: String?
    let onRetry: () -> Void

    private let steps = [
        "Importing emails ...",
        "Grouping related emails ...",
        "Writing useful titles ...",
        "Preparing your inbox ...",
        "Almost ready!"
    ]

    var body: some View {
        ZStack {
            ElectronicMailDesign.background(for: colorScheme)
                .ignoresSafeArea()

            TimelineView(.periodic(from: startedAt, by: 0.25)) { timeline in
                let elapsed = max(0, timeline.date.timeIntervalSince(startedAt))
                let step = min(Int(elapsed / 5.0), steps.count - 1)

                VStack(spacing: 18) {
                    WavyStatusText(text: statusText(fallbackStep: step))
                        .id(statusText(fallbackStep: step))
                        .transition(.opacity.combined(with: .scale(scale: 0.985)))
                        .animation(.easeInOut(duration: 0.35), value: statusText(fallbackStep: step))

                    if let errorMessage {
                        VStack(spacing: 12) {
                            Text(errorMessage)
                                .font(ElectronicMailType.body())
                                .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                            Button("Retry") {
                                onRetry()
                            }
                            .buttonStyle(.plain)
                            .foregroundStyle(ElectronicMailDesign.appleBlue)
                        }
                    }
                }
            }
        }
        .environment(\.font, .system(.body, design: .rounded))
    }

    private func statusText(fallbackStep: Int) -> String {
        if isLongWait {
            return "Still setting things up, keep this open ..."
        }
        guard let readiness else {
            return steps[fallbackStep]
        }
        switch readiness.stage {
        case "starting_full_import", "importing_recent_gmail":
            return "Importing recent email ..."
        case "grouping_threads":
            return "Grouping related emails ..."
        case "writing_titles":
            return "Writing useful titles ..."
        case "preparing_inbox":
            return "Preparing your inbox ..."
        case "ready", "welcome_back":
            return "Almost ready!"
        default:
            return steps[fallbackStep]
        }
    }
}

private struct WavyStatusText: View {
    @Environment(\.colorScheme) private var colorScheme
    let text: String

    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 60.0)) { timeline in
            let elapsed = timeline.date.timeIntervalSinceReferenceDate

            HStack(spacing: 0) {
                ForEach(Array(text.enumerated()), id: \.offset) { index, character in
                    let phase = elapsed * 4.0 - Double(index) * 0.52
                    let crest = (sin(phase) + 1.0) / 2.0
                    let opacity = 0.34 + (crest * crest * 0.66)

                    Text(String(character))
                        .font(ElectronicMailType.sectionTitle())
                        .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme).opacity(opacity))
                }
            }
            .accessibilityLabel(text)
        }
    }
}

private enum AppClientFactory {
    static func makeDefaultClient(localMailStore: LocalMailStore) -> AppClient {
        return OfflineFirstAppClient(
            backend: LiveBackendAppClient(baseURL: AppConfiguration.defaultBackendURL),
            localMailStore: localMailStore
        )
    }

    static func makeLocalMailStore() -> LocalMailStore {
        SQLiteLocalMailStore() ?? NoopLocalMailStore()
    }
}

private struct RuntimeError: LocalizedError {
    let message: String

    init(_ message: String) {
        self.message = message
    }

    var errorDescription: String? {
        message
    }
}
