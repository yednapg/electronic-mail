import ElectronicMailCore
import AppKit
import Darwin
import SwiftUI

@main
struct ElectronicMailApp: App {
    @NSApplicationDelegateAdaptor(ElectronicMailApplicationDelegate.self) private var appDelegate
    @StateObject private var store: InboxStore

    init() {
#if ELECTRONIC_MAIL_LOCAL_BETA
        if CommandLine.arguments.count == 2,
           CommandLine.arguments[1] == "--electronic-mail-beta-launch-smoke" {
            Darwin.exit(EXIT_SUCCESS)
        }
#endif
        let localMailStore = AppClientFactory.makeLocalMailStore()
        _store = StateObject(
            wrappedValue: InboxStore(
                client: AppClientFactory.makeDefaultClient(localMailStore: localMailStore),
                localMailStore: localMailStore
            )
        )
    }

    var body: some Scene {
        WindowGroup {
            ElectronicMailRootView(store: store)
                .frame(minWidth: 1100, minHeight: 680)
                .tint(ElectronicMailDesign.appleBlue)
        }
        .windowToolbarStyle(.unifiedCompact(showsTitle: true))
        .defaultSize(width: 1440, height: 900)
        .commands {
            SidebarCommands()

            CommandGroup(after: .newItem) {
                Button("New Message") {
                    NotificationCenter.default.post(name: .electronicMailOpenComposer, object: nil)
                }
                .keyboardShortcut("n", modifiers: [.command])
            }

            CommandMenu("Mailbox") {
                Button("Get New Mail") {
                    NotificationCenter.default.post(name: .electronicMailSyncMailbox, object: nil)
                }
                .keyboardShortcut("r", modifiers: [.command])

                Divider()

                Button("Open Command Palette") {
                    NotificationCenter.default.post(name: .electronicMailOpenCommandPalette, object: nil)
                }
                .keyboardShortcut("k", modifiers: [.command])
            }
        }
    }
}

@MainActor
private final class ElectronicMailApplicationDelegate: NSObject, NSApplicationDelegate {
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard ElectronicMailComposerShutdownCoordinator.shared.hasActiveComposer else {
            return .terminateNow
        }
        Task { @MainActor in
            let canTerminate = await ElectronicMailComposerShutdownCoordinator.shared.prepareForShutdown()
            sender.reply(toApplicationShouldTerminate: canTerminate)
        }
        return .terminateLater
    }
}

private enum AppLaunchStage {
    case resolvingSession
    case signIn
    case setup
    case app
}

private struct ElectronicMailRootView: View {
    @ObservedObject var store: InboxStore
    @State private var stage: AppLaunchStage = .resolvingSession
    @State private var setupStartedAt = Date()
    @State private var setupError: String?
    @State private var resolvingError: String?
    @State private var signInInProgress = false
    @State private var signInError: String?

    private let tokenStore = MacSessionTokenStore()

    var body: some View {
        ZStack {
            switch stage {
            case .resolvingSession:
                SessionResolvingView(errorMessage: resolvingError) {
                    Task { await restoreExistingSessionIfAvailable() }
                }
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
                    errorMessage: setupError
                ) {
                    Task { await startSetupFlow(minimumDisplaySeconds: 0, maximumWaitSeconds: 60) }
                }
                .transition(.opacity)
            case .app:
                SignedInShellView(
                    store: store,
                    onReauthorizeGoogle: { try await performGoogleReauthorization() },
                    onSignOut: { try await performSignOut() },
                    onDisconnectGoogle: { try await performGoogleDisconnect() },
                    onDeleteAccount: { try await performAccountDeletion() }
                )
                    .onAppear {
                        store.startLiveRefreshLoop()
                    }
                    .transition(.opacity)
            }
        }
        .task {
            await restoreExistingSessionIfAvailable()
        }
        .onChange(of: store.hasSessionToken) { hadSession, hasSession in
            guard hadSession, !hasSession else {
                return
            }
            handleUnexpectedSessionLoss()
        }
        .onOpenURL { url in
            GoogleOAuthService.handleCallbackURL(url)
        }
    }

    @MainActor
    private func handleUnexpectedSessionLoss() {
        guard stage != .signIn else {
            return
        }

        tokenStore.clear()
        signInInProgress = false
        setupError = nil
        resolvingError = nil
        signInError = "Sign in with Google to load your mailbox."
        withAnimation(.easeInOut(duration: 0.25)) {
            stage = .signIn
        }
    }

    @MainActor
    private func restoreExistingSessionIfAvailable() async {
        guard stage == .resolvingSession else {
            return
        }
        resolvingError = nil
        guard let token = tokenStore.load(), !token.isEmpty else {
            stage = .signIn
            return
        }

        store.setSessionToken(token)
        await store.load()

        if case .failed(let message) = store.phase {
            if store.hasSessionToken {
                resolvingError = message
            } else {
                tokenStore.clear()
                signInError = message
                stage = .signIn
            }
            return
        }

        if store.isReadyForMainInterface || store.canEnterWithBuildingDashboard {
            withAnimation(.easeInOut(duration: 0.35)) {
                stage = .app
            }
            return
        }

        await startSetupFlow(minimumDisplaySeconds: 0, maximumWaitSeconds: 60)
    }

    @MainActor
    private func performGoogleSignIn() async {
        guard !signInInProgress else {
            return
        }

        signInInProgress = true
        signInError = nil

        do {
            let grant = try await GoogleOAuthService().startGoogleAuthenticationWithBrowserHandoff(
                baseURL: store.backendURL
            )
            let session = try await store.exchangeMobileSession(grant: grant)
            try tokenStore.save(session.sessionToken)
            await store.load()

            if case .failed(let message) = store.phase {
                throw RuntimeError(message)
            }

            await startSetupFlow(minimumDisplaySeconds: 0, maximumWaitSeconds: 60)
        } catch {
            tokenStore.clear()
            store.setSessionToken(nil)
            signInError = error.localizedDescription
        }

        signInInProgress = false
    }

    @MainActor
    private func performGoogleReauthorization() async throws {
        let grant = try await GoogleOAuthService().startGoogleAuthenticationWithBrowserHandoff(
            baseURL: store.backendURL
        )
        let session = try await store.exchangeMobileSession(grant: grant)
        try tokenStore.save(session.sessionToken)
        await store.load()
    }

    @MainActor
    private func performSignOut() async throws {
        try await store.logoutRemoteSession()
        ElectronicMailComposerShutdownCoordinator.shared.clearRecoveryData()
        tokenStore.clear()
        store.setSessionToken(nil)
        signInError = nil
        withAnimation(.easeInOut(duration: 0.25)) {
            stage = .signIn
        }
    }

    @MainActor
    private func performGoogleDisconnect() async throws {
        try await store.disconnectGoogleAndDeleteData()
        ElectronicMailComposerShutdownCoordinator.shared.clearRecoveryData()
        tokenStore.clear()
        store.setSessionToken(nil)
        signInError = "Google was disconnected."
        withAnimation(.easeInOut(duration: 0.25)) {
            stage = .signIn
        }
    }

    @MainActor
    private func performAccountDeletion() async throws {
        try await store.deleteAccountPermanently()
        ElectronicMailComposerShutdownCoordinator.shared.clearRecoveryData()
        tokenStore.clear()
        store.setSessionToken(nil)
        signInError = "Your Electronic Mail account was deleted."
        withAnimation(.easeInOut(duration: 0.25)) {
            stage = .signIn
        }
    }

    @MainActor
    private func startSetupFlow(minimumDisplaySeconds: TimeInterval, maximumWaitSeconds: TimeInterval) async {
        setupStartedAt = Date()
        setupError = nil

        withAnimation(.easeInOut(duration: 0.35)) {
            stage = .setup
        }

        let ready = await PostLoginCoordinator(store: store).waitForReadiness(
            minimumDisplaySeconds: minimumDisplaySeconds,
            maximumWaitSeconds: maximumWaitSeconds
        )

        guard ready else {
            setupError = store.currentReadiness?.errorMessage ?? "Still syncing Gmail. Try again in a moment."
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

    func waitForReadiness(minimumDisplaySeconds: TimeInterval, maximumWaitSeconds: TimeInterval) async -> Bool {
        let startedAt = Date()
        while !Task.isCancelled {
            await store.refreshForReadiness()
            let elapsed = Date().timeIntervalSince(startedAt)
            if elapsed >= minimumDisplaySeconds, store.isReadyForMainInterface {
                return true
            }
            if elapsed >= maximumWaitSeconds, store.canEnterWithBuildingDashboard {
                return true
            }
            let sleepSeconds = max(0.25, min(2, minimumDisplaySeconds - elapsed > 0 ? minimumDisplaySeconds - elapsed : 2))
            try? await Task.sleep(nanoseconds: UInt64(sleepSeconds * 1_000_000_000))
        }
        return false
    }
}

private final class MacSessionTokenStore: SessionTokenStoring {
    private static let legacyPlaintextTokenKey = "ElectronicMail.debug.email_session"
    private let store = KeychainSessionTokenStore()

    init(defaults: UserDefaults = .standard) {
        defaults.removeObject(forKey: Self.legacyPlaintextTokenKey)
    }

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

private struct SessionResolvingView: View {
    @Environment(\.colorScheme) private var colorScheme
    let errorMessage: String?
    let onRetry: () -> Void

    var body: some View {
        ZStack {
            ElectronicMailDesign.background(for: colorScheme)
                .ignoresSafeArea()

            VStack(spacing: 18) {
                Image(systemName: "envelope.fill")
                    .font(.system(size: 34, weight: .semibold, design: .rounded))
                    .symbolRenderingMode(.monochrome)
                    .foregroundStyle(ElectronicMailDesign.appleBlue)

                Text("Electronic Mail")
                    .font(ElectronicMailType.title())
                    .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))

                if let errorMessage {
                    Text(errorMessage)
                        .font(ElectronicMailType.small())
                        .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                        .multilineTextAlignment(.center)
                        .frame(maxWidth: 520)

                    Button("Try Again", action: onRetry)
                        .buttonStyle(.borderedProminent)
                        .controlSize(.large)
                } else {
                    ProgressView()
                        .controlSize(.small)
                        .tint(ElectronicMailDesign.appleBlue)
                        .accessibilityLabel("Opening your mailbox")

                    Text("Opening your mailbox…")
                        .font(ElectronicMailType.small())
                        .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                }
            }
            .padding(40)
        }
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

                Text("A focused home for your email.")
                    .font(ElectronicMailType.sectionTitle())
                    .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))

                Button(action: onSignIn) {
                    if isSigningIn {
                        ProgressView()
                            .controlSize(.small)
                    } else {
                        Text("Sign in with Google")
                            .font(ElectronicMailType.body())
                    }
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
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
    }
}

private struct SetupAnimationView: View {
    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    let startedAt: Date
    let readiness: PostLoginReadinessResponse?
    let errorMessage: String?
    let onRetry: () -> Void

    private let steps = [
        "Importing emails ...",
        "Indexing conversations ...",
        "Syncing mailbox folders ...",
        "Preparing your mailbox ...",
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
                        .transition(reduceMotion ? .identity : .opacity.combined(with: .scale(scale: 0.985)))
                        .animation(reduceMotion ? nil : .easeInOut(duration: 0.35), value: statusText(fallbackStep: step))

                    if let errorMessage {
                        VStack(spacing: 12) {
                            Text(errorMessage)
                                .font(ElectronicMailType.body())
                                .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                            Button("Retry") {
                                onRetry()
                            }
                            .buttonStyle(.plain)
                            .font(ElectronicMailType.small(weight: .semibold))
                            .foregroundStyle(ElectronicMailDesign.appleBlue)
                        }
                    }
                }
            }
        }
        .environment(\.font, .system(.body, design: .rounded))
    }

    private func statusText(fallbackStep: Int) -> String {
        guard let readiness else {
            return steps[fallbackStep]
        }
        switch readiness.stage {
        case "starting_full_import", "importing_recent_gmail":
            return "Importing 90 days of email ..."
        case "grouping_threads":
            return "Preparing your inbox ..."
        case "writing_titles":
            return "Preparing your inbox ..."
        case "building_dashboard":
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
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    let text: String

    @ViewBuilder
    var body: some View {
        if reduceMotion {
            Text(text)
                .font(ElectronicMailType.sectionTitle())
                .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                .accessibilityLabel(text)
        } else {
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
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(text)
            }
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
