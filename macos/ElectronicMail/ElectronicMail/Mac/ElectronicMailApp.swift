import ElectronicMailCore
import AppKit
import Darwin
import SwiftUI

@main
struct ElectronicMailApp: App {
    @NSApplicationDelegateAdaptor(ElectronicMailApplicationDelegate.self) private var appDelegate
    @StateObject private var store: InboxStore
    private let visualQAMode: Bool
    private let signInVisualQAMode: Bool
    private let visualQAColorScheme: ColorScheme?

    init() {
#if ELECTRONIC_MAIL_LOCAL_BETA
        if CommandLine.arguments.count == 2,
           CommandLine.arguments[1] == "--electronic-mail-beta-launch-smoke" {
            Darwin.exit(EXIT_SUCCESS)
        }
#endif
#if DEBUG
        let arguments = CommandLine.arguments
        signInVisualQAMode = arguments.contains("--electronic-mail-sign-in-visual-qa")
        visualQAMode = arguments.contains("--electronic-mail-visual-qa") || signInVisualQAMode
        if arguments.contains("--electronic-mail-visual-qa-dark") {
            visualQAColorScheme = .dark
        } else if arguments.contains("--electronic-mail-visual-qa-light") {
            visualQAColorScheme = .light
        } else {
            visualQAColorScheme = nil
        }
#else
        visualQAMode = false
        signInVisualQAMode = false
        visualQAColorScheme = nil
#endif
        if visualQAMode {
            _store = StateObject(wrappedValue: InboxStore(client: DemoAppClient()))
        } else {
            let localMailStore = AppClientFactory.makeLocalMailStore()
            _store = StateObject(
                wrappedValue: InboxStore(
                    client: AppClientFactory.makeDefaultClient(localMailStore: localMailStore),
                    localMailStore: localMailStore
                )
            )
        }
    }

    var body: some Scene {
        Window("", id: "main") {
            ElectronicMailRootView(
                store: store,
                visualQAMode: visualQAMode,
                startsAtSignIn: signInVisualQAMode
            )
                .frame(
                    minWidth: ElectronicMailControlMetrics.onboardingWindowWidth,
                    minHeight: ElectronicMailControlMetrics.onboardingWindowHeight
                )
                .tint(ElectronicMailDesign.appleBlue)
                .electronicMailSymbolAppearance()
                .background(ElectronicMailTrafficLightOverlayInstaller())
                .preferredColorScheme(visualQAColorScheme)
        }
        .windowStyle(.hiddenTitleBar)
        .defaultSize(
            width: ElectronicMailControlMetrics.onboardingWindowWidth,
            height: ElectronicMailControlMetrics.onboardingWindowHeight
        )
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

private struct ElectronicMailTrafficLightOverlayInstaller: NSViewRepresentable {
    func makeCoordinator() -> Coordinator {
        Coordinator()
    }

    func makeNSView(context: Context) -> WindowResolvingView {
        let view = WindowResolvingView()
        view.onWindowChange = { [weak coordinator = context.coordinator] window in
            coordinator?.install(in: window)
        }
        return view
    }

    func updateNSView(_ nsView: WindowResolvingView, context: Context) {
        context.coordinator.install(in: nsView.window)
    }

    static func dismantleNSView(_ nsView: WindowResolvingView, coordinator: Coordinator) {
        coordinator.uninstall()
    }

    @MainActor
    final class Coordinator {
        private var controller: TrafficLightOverlayController?

        func install(in window: NSWindow?) {
            guard let window else { return }
            if controller?.window === window {
                controller?.updateGeometry()
                return
            }
            uninstall()
            let controller = TrafficLightOverlayController(window: window)
            self.controller = controller
            controller.install()
        }

        func uninstall() {
            controller?.uninstall()
            controller = nil
        }
    }
}

private final class WindowResolvingView: NSView {
    var onWindowChange: ((NSWindow?) -> Void)?

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        onWindowChange?(window)
    }

    override func hitTest(_ point: NSPoint) -> NSView? {
        nil
    }
}

@MainActor
private final class TrafficLightOverlayController {
    private(set) weak var window: NSWindow?
    private weak var containerView: NSView?
    private var overlayView: TrafficLightMaskView?
    private var buttonMaskViews: [TrafficLightButtonMaskView] = []
    private var observations: [NSObjectProtocol] = []

    init(window: NSWindow) {
        self.window = window
    }

    func install() {
        updateGeometry()
        guard let window else { return }
        let notifications: [Notification.Name] = [
            NSWindow.didResizeNotification,
            NSWindow.didEnterFullScreenNotification,
            NSWindow.didExitFullScreenNotification,
            NSWindow.didBecomeKeyNotification,
            NSWindow.didResignKeyNotification,
        ]
        observations = notifications.map { name in
            NotificationCenter.default.addObserver(
                forName: name,
                object: window,
                queue: .main
            ) { [weak self] _ in
                Task { @MainActor in self?.updateGeometry() }
            }
        }
    }

    func uninstall() {
        observations.forEach(NotificationCenter.default.removeObserver)
        observations.removeAll()
        overlayView?.removeFromSuperview()
        overlayView = nil
        buttonMaskViews.forEach { $0.removeFromSuperview() }
        buttonMaskViews.removeAll()
        containerView = nil
    }

    func updateGeometry() {
        guard let window,
              let close = window.standardWindowButton(.closeButton),
              let minimize = window.standardWindowButton(.miniaturizeButton),
              let zoom = window.standardWindowButton(.zoomButton),
              let container = close.superview,
              minimize.superview === container,
              zoom.superview === container else {
            overlayView?.removeFromSuperview()
            overlayView = nil
            buttonMaskViews.forEach { $0.removeFromSuperview() }
            buttonMaskViews.removeAll()
            containerView = nil
            return
        }

        let buttons = [close, minimize, zoom]
        let groupFrame = buttons
            .map(\.frame)
            .reduce(NSRect.null) { $0.union($1) }
            .insetBy(dx: -1, dy: -1)

        let overlay: TrafficLightMaskView
        if let current = overlayView, containerView === container {
            overlay = current
        } else {
            overlayView?.removeFromSuperview()
            overlay = TrafficLightMaskView(frame: groupFrame)
            container.addSubview(overlay, positioned: .above, relativeTo: nil)
            overlayView = overlay
            containerView = container
        }

        overlay.frame = groupFrame
        overlay.onPointerInsideChange = { [weak self] inside in
            self?.setButtonMasksVisible(!inside)
        }
        overlay.updateTrackingArea()

        if buttonMaskViews.count != buttons.count
            || zip(buttonMaskViews, buttons).contains(where: { pair in
                pair.0.superview !== pair.1
            }) {
            buttonMaskViews.forEach { $0.removeFromSuperview() }
            buttonMaskViews = buttons.map { button in
                let mask = TrafficLightButtonMaskView(frame: button.bounds)
                mask.autoresizingMask = [.width, .height]
                button.addSubview(mask)
                return mask
            }
        }
        for (mask, button) in zip(buttonMaskViews, buttons) {
            mask.frame = button.bounds
            mask.isWindowActive = window.isKeyWindow
            mask.needsDisplay = true
        }
        setButtonMasksVisible(!overlay.pointerInside, animated: false)
    }

    private func setButtonMasksVisible(_ visible: Bool, animated: Bool = true) {
        let changes = {
            self.buttonMaskViews.forEach { $0.alphaValue = visible ? 1 : 0 }
        }
        guard animated, !NSWorkspace.shared.accessibilityDisplayShouldReduceMotion else {
            changes()
            return
        }
        NSAnimationContext.runAnimationGroup { context in
            context.duration = 0.12
            self.buttonMaskViews.forEach { mask in
                mask.animator().alphaValue = visible ? 1 : 0
            }
        }
    }
}

private final class TrafficLightMaskView: NSView {
    var onPointerInsideChange: ((Bool) -> Void)?
    private(set) var pointerInside = false
    private var pointerTrackingArea: NSTrackingArea?

    override var isOpaque: Bool { false }

    override func hitTest(_ point: NSPoint) -> NSView? {
        // The system buttons remain the actual hit targets underneath.
        nil
    }

    func updateTrackingArea() {
        if let pointerTrackingArea {
            removeTrackingArea(pointerTrackingArea)
        }
        let area = NSTrackingArea(
            rect: bounds,
            options: [.mouseEnteredAndExited, .activeAlways],
            owner: self,
            userInfo: nil
        )
        addTrackingArea(area)
        pointerTrackingArea = area
    }

    override func mouseEntered(with event: NSEvent) {
        setPointerInside(true)
    }

    override func mouseExited(with event: NSEvent) {
        setPointerInside(false)
    }

    private func setPointerInside(_ inside: Bool) {
        guard pointerInside != inside else { return }
        pointerInside = inside
        onPointerInsideChange?(inside)
    }
}

private final class TrafficLightButtonMaskView: NSView {
    var isWindowActive = true

    override var isOpaque: Bool { false }

    override func hitTest(_ point: NSPoint) -> NSView? {
        nil
    }

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        // An opaque neutral cover is required here; translucent gray allows the
        // native red/yellow/green fills underneath to bleed through.
        let neutral = NSColor(
            deviceWhite: isWindowActive ? 0.34 : 0.26,
            alpha: 1
        )
        neutral.setFill()
        NSBezierPath(ovalIn: bounds).fill()
    }
}

private extension View {
    @ViewBuilder
    func electronicMailSymbolAppearance() -> some View {
        if #available(macOS 26.0, *) {
            symbolRenderingMode(.multicolor)
                .symbolColorRenderingMode(.gradient)
        } else {
            symbolRenderingMode(.multicolor)
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
    private static let tokenStore = MacSessionTokenStore()
    private static let asyncTokenStore = AsyncSessionTokenStore(store: tokenStore)

    @ObservedObject var store: InboxStore
    private let visualQAMode: Bool
    @State private var stage: AppLaunchStage
    @State private var setupStartedAt = Date()
    @State private var setupError: String?
    @State private var resolvingError: String?
    @State private var signInInProgress = false
    @State private var signInError: String?

    init(store: InboxStore, visualQAMode: Bool = false, startsAtSignIn: Bool = false) {
        self.store = store
        self.visualQAMode = visualQAMode
        _stage = State(initialValue: startsAtSignIn ? .signIn : (visualQAMode ? .app : .resolvingSession))
    }

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
            guard !visualQAMode else { return }
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
                    .font(.system(size: 28, weight: .medium))
                    .symbolRenderingMode(.monochrome)
                    .foregroundStyle(ElectronicMailDesign.appleBlue)

                Text("Electronic Mail")
                    .font(ElectronicMailType.title(weight: .semibold))
                    .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))

                if let errorMessage {
                    Text(errorMessage)
                        .font(ElectronicMailType.small())
                        .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                        .multilineTextAlignment(.center)
                        .frame(maxWidth: 520)

                    HStack(spacing: 12) {
                        Button(action: onRetry) {
                            Text("Try Again")
                                .padding(.horizontal, 18)
                                .frame(minHeight: ElectronicMailControlMetrics.actionHeight)
                                .contentShape(Capsule())
                        }
                        .font(ElectronicMailType.body(weight: .semibold))
                        .electronicMailGlassButton(role: .prominent, shape: .capsule)

                        Button(action: onSignInAgain) {
                            Text("Sign In Again")
                                .padding(.horizontal, 18)
                                .frame(minHeight: ElectronicMailControlMetrics.actionHeight)
                                .contentShape(Capsule())
                        }
                        .font(ElectronicMailType.body(weight: .semibold))
                        .electronicMailGlassButton(role: .standard, shape: .capsule)
                    }
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
        GeometryReader { proxy in
            ZStack(alignment: .bottomTrailing) {
                ElectronicMailDesign.background(for: colorScheme)
                    .ignoresSafeArea()

                welcomeContent
                    .frame(maxWidth: 480)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .center)

                signInArea
                    .padding(.trailing, 24)
                    .padding(.bottom, 24)
            }
            .frame(width: proxy.size.width, height: proxy.size.height)
        }
        .background(ElectronicMailSignInWindowSizer())
    }

    private var welcomeContent: some View {
        VStack(spacing: 0) {
            ElectronicMailWelcomeAppIcon()
                .padding(.bottom, 20)

            Text("Welcome to Electronic Mail")
                .font(ElectronicMailType.heroTitle(weight: .semibold))
                .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                .multilineTextAlignment(.center)
                .padding(.bottom, 22)

            GoogleSignInFeatureRow()
                .padding(.bottom, 22)

            Text("A focused home for the email that matters.\nConnect your Google account to get started.")
                .font(ElectronicMailType.welcomeBody())
                .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                .multilineTextAlignment(.center)
                .lineSpacing(3)
                .fixedSize(horizontal: false, vertical: true)
        }
        .accessibilityElement(children: .contain)
    }

    private var signInArea: some View {
        VStack(alignment: .trailing, spacing: 14) {
            if let errorMessage {
                Text(errorMessage)
                    .font(ElectronicMailType.body())
                    .foregroundStyle(Color.red.opacity(colorScheme == .dark ? 0.92 : 0.82))
                    .multilineTextAlignment(.trailing)
                    .frame(maxWidth: 440, alignment: .trailing)
                    .transition(.opacity)
            }

            Button(action: onSignIn) {
                Group {
                    if isSigningIn {
                        ProgressView()
                            .controlSize(.small)
                            .tint(.white)
                            .frame(minWidth: 126)
                    } else {
                        Text("Sign in with Google")
                    }
                }
                .padding(.horizontal, 16)
                .frame(minHeight: 38)
                .contentShape(Capsule())
            }
            .font(ElectronicMailType.small(weight: .medium))
            .electronicMailGlassButton(role: .prominent, shape: .capsule)
            .disabled(isSigningIn)
            .help("Sign in with Google")
            .accessibilityLabel(isSigningIn ? "Signing in with Google" : "Sign in with Google")
            .accessibilityValue(isSigningIn ? "In progress" : "Ready")
        }
    }
}

private struct ElectronicMailSignInWindowSizer: NSViewRepresentable {
    func makeCoordinator() -> Coordinator {
        Coordinator()
    }

    func makeNSView(context: Context) -> WindowResolvingView {
        let view = WindowResolvingView()
        view.onWindowChange = { [weak coordinator = context.coordinator] window in
            coordinator?.applyDefaultSize(to: window)
        }
        return view
    }

    func updateNSView(_ nsView: WindowResolvingView, context: Context) {
        context.coordinator.applyDefaultSize(to: nsView.window)
    }

    @MainActor
    final class Coordinator {
        private weak var sizedWindow: NSWindow?

        func applyDefaultSize(to window: NSWindow?) {
            guard let window,
                  sizedWindow !== window,
                  !window.styleMask.contains(.fullScreen) else {
                return
            }

            sizedWindow = window
            window.setContentSize(
                NSSize(
                    width: ElectronicMailControlMetrics.onboardingWindowWidth,
                    height: ElectronicMailControlMetrics.onboardingWindowHeight
                )
            )
            window.center()
        }
    }
}

private struct ElectronicMailWelcomeAppIcon: View {
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        iconArtwork
        .frame(width: 68, height: 68)
        .shadow(
            color: Color.black.opacity(colorScheme == .dark ? 0.34 : 0.16),
            radius: 6,
            y: 3
        )
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Electronic Mail")
    }

    @ViewBuilder
    private var iconArtwork: some View {
        if colorScheme == .dark {
            Image(nsImage: NSApplication.shared.applicationIconImage)
                .resizable()
                .interpolation(.high)
                .scaledToFit()
        } else {
            ZStack {
                RoundedRectangle(cornerRadius: 17, style: .continuous)
                    .fill(ElectronicMailDesign.appleBlue)

                Image(systemName: "envelope.fill")
                    .symbolRenderingMode(.hierarchical)
                    .font(.system(size: 32, weight: .medium))
                    .foregroundStyle(.white)
            }
        }
    }
}

private struct GoogleSignInFeatureRow: View {
    private let features: [GoogleSignInFeature] = [
        GoogleSignInFeature(
            symbol: ElectronicMailSymbols.search,
            label: "Search",
            tint: ElectronicMailDesign.featureSearch
        ),
        GoogleSignInFeature(
            symbol: ElectronicMailSymbols.contacts,
            label: "Contacts",
            tint: ElectronicMailDesign.featureContacts
        ),
        GoogleSignInFeature(
            symbol: ElectronicMailSymbols.moveToInbox,
            label: "Inbox",
            tint: ElectronicMailDesign.featureInbox
        ),
        GoogleSignInFeature(
            symbol: ElectronicMailSymbols.starredFilled,
            label: "Starred",
            tint: ElectronicMailDesign.featureStarred
        ),
        GoogleSignInFeature(
            symbol: ElectronicMailSymbols.reply,
            label: "Reply",
            tint: ElectronicMailDesign.featureReply
        ),
    ]

    var body: some View {
        ElectronicMailGlassGroup(spacing: 14) {
            HStack(spacing: 14) {
                ForEach(features) { feature in
                    GoogleSignInFeatureIcon(feature: feature)
                }
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Electronic Mail features")
    }
}

private struct GoogleSignInFeature: Identifiable {
    let symbol: String
    let label: String
    let tint: Color

    var id: String { label }
}

private struct GoogleSignInFeatureIcon: View {
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @Environment(\.colorScheme) private var colorScheme
    let feature: GoogleSignInFeature

    @ViewBuilder
    var body: some View {
        if #available(macOS 26.0, *), !reduceTransparency {
            icon
                .glassEffect(
                    .regular.tint(feature.tint.opacity(colorScheme == .dark ? 0.28 : 0.18)),
                    in: Circle()
                )
        } else {
            icon
                .background {
                    Circle()
                        .fill(reduceTransparency ? feature.tint.opacity(0.14) : .clear)
                        .background(.regularMaterial, in: Circle())
                }
                .overlay {
                    Circle()
                        .strokeBorder(
                            feature.tint.opacity(colorScheme == .dark ? 0.30 : 0.20),
                            lineWidth: 1
                        )
                }
        }
    }

    private var icon: some View {
        Image(systemName: feature.symbol)
            .symbolRenderingMode(.monochrome)
            .font(.system(size: 19, weight: .medium))
            .foregroundStyle(feature.tint)
            .frame(width: 42, height: 42)
            .contentShape(Circle())
            .accessibilityLabel(feature.label)
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
                        Button(action: onRetry) {
                            Text("Retry")
                                .padding(.horizontal, 18)
                                .frame(minHeight: ElectronicMailControlMetrics.actionHeight)
                                .contentShape(Capsule())
                        }
                        .font(ElectronicMailType.body(weight: .semibold))
                        .electronicMailGlassButton(role: .standard, shape: .capsule)
                    }
                }
            }
            .id(startedAt)
        }
        .environment(\.font, .body)
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
