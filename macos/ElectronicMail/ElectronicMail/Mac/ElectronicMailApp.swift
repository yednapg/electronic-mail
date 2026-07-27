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
        Window("Electronic Mail", id: "main") {
            ElectronicMailRootView(store: store)
                .frame(minWidth: 1100, minHeight: 680)
                .tint(ElectronicMailDesign.appleBlue)
                .background(ElectronicMailWindowSurface())
        }
        .windowStyle(.hiddenTitleBar)
        .defaultSize(width: 1440, height: 900)
        .commands {
            CommandGroup(replacing: .sidebar) {
                Button("Toggle Navigation") {
                    NotificationCenter.default.post(name: .electronicMailToggleNavigation, object: nil)
                }
                .keyboardShortcut("s", modifiers: [.command, .control])
            }

            CommandGroup(after: .newItem) {
                Button("New Message") {
                    NotificationCenter.default.post(name: .electronicMailOpenComposer, object: nil)
                }
                .keyboardShortcut("n", modifiers: [.command])
            }

            CommandMenu("Mailbox") {
                Button("Search Mail…") {
                    NotificationCenter.default.post(name: .electronicMailOpenMailboxSearch, object: nil)
                }
                .keyboardShortcut("f", modifiers: [.command])

                Divider()

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

            CommandMenu("Account") {
                Button("Sign Out") {
                    NotificationCenter.default.post(name: .electronicMailSignOut, object: nil)
                }
                .disabled(!store.hasSessionToken)

                Divider()

                Button("Disconnect Google…") {
                    NotificationCenter.default.post(name: .electronicMailDisconnectGoogle, object: nil)
                }
                .disabled(!store.hasSessionToken)

                Button("Delete Account…") {
                    NotificationCenter.default.post(name: .electronicMailDeleteAccount, object: nil)
                }
                .disabled(!store.hasSessionToken)
            }
        }
    }
}

private struct ElectronicMailWindowSurface: NSViewRepresentable {
    @Environment(\.colorScheme) private var colorScheme

    func makeNSView(context _: Context) -> ElectronicMailWindowSurfaceView {
        let view = ElectronicMailWindowSurfaceView()
        view.surfaceColor = surfaceColor
        return view
    }

    func updateNSView(_ view: ElectronicMailWindowSurfaceView, context _: Context) {
        view.surfaceColor = surfaceColor
    }

    private var surfaceColor: NSColor {
        colorScheme == .dark ? .black : .white
    }
}

private final class ElectronicMailWindowSurfaceView: NSView {
    var surfaceColor = NSColor.black {
        didSet {
            guard surfaceColor != oldValue else {
                return
            }
            window?.backgroundColor = surfaceColor
        }
    }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        applyWindowSurface()
        DispatchQueue.main.async { [weak self] in
            self?.applyWindowSurface()
        }
    }

    private func applyWindowSurface() {
        guard let window else {
            return
        }
        window.styleMask.insert(.fullSizeContentView)
        window.titleVisibility = .visible
        window.titlebarAppearsTransparent = true
        window.titlebarSeparatorStyle = .none
        window.backgroundColor = surfaceColor
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
    private static let tokenStore = MacSessionTokenStore()
    private static let asyncTokenStore = AsyncSessionTokenStore(store: tokenStore)

    @ObservedObject var store: InboxStore
    @State private var stage: AppLaunchStage = .resolvingSession
    @State private var setupStartedAt = Date()
    @State private var setupError: String?
    @State private var resolvingError: String?
    @State private var signInInProgress = false
    @State private var signInError: String?

    var body: some View {
        ZStack {
            switch stage {
            case .resolvingSession:
                SessionResolvingView(
                    errorMessage: resolvingError,
                    onRetry: {
                        Task { await restoreExistingSessionIfAvailable() }
                    },
                    onSignInAgain: continueToSignInWithoutDeletingSavedToken
                )
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
                    progress: store.setupProgress,
                    errorMessage: setupError
                ) {
                    Task { await startSetupFlow(minimumDisplaySeconds: 0, maximumWaitSeconds: 30) }
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

        clearSavedSessionTokenInBackground()
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
        let loadResult = await Self.asyncTokenStore.loadWithRetry(
            initialTimeout: 2,
            retryTimeout: 5
        )
        guard stage == .resolvingSession else {
            return
        }
        guard case .loaded(let savedToken) = loadResult else {
            resolvingError = "Electronic Mail could not access your saved sign-in in time. Unlock your Mac and try again, or sign in again. Your saved sign-in was not removed."
            return
        }
        guard let token = savedToken, !token.isEmpty else {
            stage = .signIn
            return
        }

        store.setSessionToken(token)
        if await store.restoreLocalCache() {
            Task { await store.load() }
            withAnimation(.easeInOut(duration: 0.2)) {
                stage = .app
            }
            return
        }
        Task { await store.load() }

        if case .failed(let message) = store.phase {
            if store.hasSessionToken {
                resolvingError = message
            } else {
                clearSavedSessionTokenInBackground()
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

        await startSetupFlow(minimumDisplaySeconds: 0, maximumWaitSeconds: 30)
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
            try await saveSessionToken(session.sessionToken)
            Task { await store.load() }
            await startSetupFlow(minimumDisplaySeconds: 0, maximumWaitSeconds: 30)
        } catch {
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
        try await saveSessionToken(session.sessionToken)
        await store.load()
    }

    @MainActor
    private func performSignOut() async throws {
        try await store.logoutRemoteSession()
        try beginSavedSessionTokenClear()
        ElectronicMailComposerShutdownCoordinator.shared.clearRecoveryData()
        store.setSessionToken(nil)
        signInError = nil
        withAnimation(.easeInOut(duration: 0.25)) {
            stage = .signIn
        }
    }

    @MainActor
    private func performGoogleDisconnect() async throws {
        try await store.disconnectGoogleAndDeleteData()
        try beginSavedSessionTokenClear()
        ElectronicMailComposerShutdownCoordinator.shared.clearRecoveryData()
        store.setSessionToken(nil)
        signInError = "Google was disconnected."
        withAnimation(.easeInOut(duration: 0.25)) {
            stage = .signIn
        }
    }

    @MainActor
    private func performAccountDeletion() async throws {
        try await store.deleteAccountPermanently()
        try beginSavedSessionTokenClear()
        ElectronicMailComposerShutdownCoordinator.shared.clearRecoveryData()
        store.setSessionToken(nil)
        signInError = "Your Electronic Mail account was deleted."
        withAnimation(.easeInOut(duration: 0.25)) {
            stage = .signIn
        }
    }

    @MainActor
    private func continueToSignInWithoutDeletingSavedToken() {
        guard stage == .resolvingSession else {
            return
        }
        resolvingError = nil
        signInError = "Sign in again to reconnect Electronic Mail. Your previous saved sign-in has not been deleted."
        withAnimation(.easeInOut(duration: 0.2)) {
            stage = .signIn
        }
    }

    @MainActor
    private func saveSessionToken(_ token: String) async throws {
        switch await Self.asyncTokenStore.save(token, timeout: 5) {
        case .saved:
            return
        case .failed(let message):
            throw RuntimeError(message)
        case .timedOut:
            throw RuntimeError(
                "Electronic Mail could not save your sign-in in time. Unlock your Mac and try again."
            )
        }
    }

    @MainActor
    private func clearSavedSessionTokenInBackground() {
        do {
            try beginSavedSessionTokenClear()
        } catch {
            signInError = error.localizedDescription
        }
    }

    /// Synchronizes the nonsecret desired tombstone before returning. The
    /// Security.framework write is intentionally launched only afterward on
    /// AsyncSessionTokenStore's credential queue.
    @MainActor
    private func beginSavedSessionTokenClear() throws {
        let preparedIntent = try Self.asyncTokenStore.prepareClearIntent()
        let asyncTokenStore = Self.asyncTokenStore
        Task {
            _ = await asyncTokenStore.clear(preparedIntent: preparedIntent, timeout: 5)
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
            setupError = store.currentReadiness?.errorMessage
                ?? "Electronic Mail could not verify a saved batch within 30 seconds. Check your connection and retry."
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
    let now: () -> Date

    init(store: InboxStore, now: @escaping () -> Date = Date.init) {
        self.store = store
        self.now = now
    }

    func waitForReadiness(minimumDisplaySeconds: TimeInterval, maximumWaitSeconds: TimeInterval) async -> Bool {
        let policy = MailboxSetupDeadlinePolicy(
            startedAt: now(),
            minimumDisplaySeconds: minimumDisplaySeconds,
            maximumWaitSeconds: maximumWaitSeconds
        )
        while !Task.isCancelled {
            let currentDate = now()
            switch policy.decision(
                now: currentDate,
                initialWindowReady: store.isReadyForMainInterface,
                committedBatchReady: store.canEnterWithBuildingDashboard
            ) {
            case .enter:
                return true
            case .retry:
                return false
            case .wait:
                break
            }
            store.beginReadinessRefresh()
            let elapsed = currentDate.timeIntervalSince(policy.startedAt)
            let remaining = max(0.01, maximumWaitSeconds - elapsed)
            let desired = minimumDisplaySeconds - elapsed > 0
                ? minimumDisplaySeconds - elapsed
                : 1
            let sleepSeconds = min(remaining, max(0.05, min(1, desired)))
            try? await Task.sleep(nanoseconds: UInt64(sleepSeconds * 1_000_000_000))
        }
        return false
    }
}

private final class MacSessionTokenStore: SessionTokenStoring, @unchecked Sendable {
    private static let legacyPlaintextTokenKey = "ElectronicMail.debug.email_session"
    private let store: KeychainSessionTokenStore

    init(defaults: UserDefaults = .standard) {
        defaults.removeObject(forKey: Self.legacyPlaintextTokenKey)
        store = KeychainSessionTokenStore(defaults: defaults)
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

    func prepareMutation(_ kind: SessionTokenMutationKind) throws -> SessionTokenMutation? {
        try store.prepareMutation(kind)
    }

    func save(_ token: String, for mutation: SessionTokenMutation?) throws {
        try store.save(token, for: mutation)
    }

    func clear(for mutation: SessionTokenMutation?) {
        store.clear(for: mutation)
    }
}

private struct SessionResolvingView: View {
    @Environment(\.colorScheme) private var colorScheme
    let errorMessage: String?
    let onRetry: () -> Void
    let onSignInAgain: () -> Void

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

                    HStack(spacing: 12) {
                        Button("Try Again", action: onRetry)
                            .buttonStyle(.borderedProminent)

                        Button("Sign In Again", action: onSignInAgain)
                            .buttonStyle(.bordered)
                    }
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
    let progress: MailboxSetupProgressSnapshot
    let errorMessage: String?
    let onRetry: () -> Void

    var body: some View {
        ZStack {
            ElectronicMailDesign.background(for: colorScheme)
                .ignoresSafeArea()

            VStack(spacing: 20) {
                WavyStatusText(text: statusText)
                    .id(statusText)
                    .transition(reduceMotion ? .identity : .opacity.combined(with: .scale(scale: 0.985)))
                    .animation(reduceMotion ? nil : .easeInOut(duration: 0.35), value: statusText)

                ProgressView(value: progress.progressFraction)
                    .progressViewStyle(.linear)
                    .frame(width: 360)
                    .accessibilityLabel("Mailbox setup progress")
                    .accessibilityValue("\(Int(progress.progressFraction * 100)) percent")

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
            .id(startedAt)
        }
        .environment(\.font, .system(.body, design: .rounded))
    }

    private var statusText: String {
        if progress.initialTargetReady || progress.confirmedEmpty {
            return "Opening your mailbox"
        }
        if progress.initialTargetCount > 0,
           progress.initialMetadataCount < progress.initialTargetCount {
            if progress.initialMetadataCount == 0,
               ["starting", "connecting", "discovering_recent"].contains(progress.phase) {
                return "Connecting to Gmail"
            }
            return "Preparing recent mail — \(progress.initialMetadataCount) conversations ready"
        }
        if progress.initialBodyTargetCount > 0,
           progress.initialBodyReadyCount < progress.initialBodyTargetCount {
            return "Preparing message content — \(progress.initialBodyReadyCount) of \(progress.initialBodyTargetCount) ready"
        }
        if readiness?.mailboxReady == true {
            return "Opening your mailbox"
        }
        return "Connecting to Gmail"
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
