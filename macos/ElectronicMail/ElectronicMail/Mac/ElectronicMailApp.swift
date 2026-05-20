import ElectronicMailCore
import AppKit
import SwiftUI

@main
struct ElectronicMailApp: App {
    @StateObject private var store = InboxStore(client: AppClientFactory.makeDefaultClient())

    var body: some Scene {
        WindowGroup {
            ElectronicMailRootView(store: store)
                .frame(minWidth: 1100, minHeight: 680)
                .background(WindowTrafficLightOffset())
        }
        .windowStyle(.hiddenTitleBar)
        .defaultSize(width: 1440, height: 900)
        .commands {
            CommandGroup(after: .appInfo) {
                Button("Open Command Palette") {
                    NotificationCenter.default.post(name: .electronicMailOpenCommandPalette, object: nil)
                }
                .keyboardShortcut("k", modifiers: [.command])
            }
        }
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
    @State private var setupCompletionTimer: Timer?
    @State private var setupStartedAt = Date()
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
                SetupAnimationView(startedAt: setupStartedAt)
                .transition(.opacity)
            case .app:
                SignedInShellView(store: store)
                    .transition(.opacity)
            }
        }
        .onDisappear {
            setupCompletionTimer?.invalidate()
        }
        .task {
            await restoreExistingSessionIfAvailable()
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

        withAnimation(.easeInOut(duration: 0.35)) {
            stage = .app
        }
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

            startSetupFlow()
        } catch {
            tokenStore.clear()
            store.setSessionToken(nil)
            signInError = error.localizedDescription
        }

        signInInProgress = false
    }

    @MainActor
    private func startSetupFlow() {
        setupCompletionTimer?.invalidate()
        setupStartedAt = Date()

        withAnimation(.easeInOut(duration: 0.35)) {
            stage = .setup
        }

        let timer = Timer(timeInterval: 25, repeats: false) { _ in
            guard stage == .setup else {
                return
            }
            withAnimation(.easeInOut(duration: 0.45)) {
                stage = .app
            }
        }
        setupCompletionTimer = timer
        RunLoop.main.add(timer, forMode: .common)
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

                Text("Turn your emails into to-do's!")
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

    private let steps = [
        "Importing emails ...",
        "Grouping related emails ...",
        "Finding to-do items ...",
        "Building dashboard ...",
        "Almost ready!"
    ]

    var body: some View {
        ZStack {
            ElectronicMailDesign.background(for: colorScheme)
                .ignoresSafeArea()

            TimelineView(.periodic(from: startedAt, by: 0.25)) { timeline in
                let elapsed = max(0, timeline.date.timeIntervalSince(startedAt))
                let step = min(Int(elapsed / 5.0), steps.count - 1)

                WavyStatusText(text: steps[step])
                    .id(step)
                    .transition(.opacity.combined(with: .scale(scale: 0.985)))
                    .animation(.easeInOut(duration: 0.35), value: step)
            }
        }
        .environment(\.font, .system(.body, design: .rounded))
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
    static func makeDefaultClient() -> AppClient {
        return LiveBackendAppClient(baseURL: AppConfiguration.defaultBackendURL)
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
