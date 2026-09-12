import ElectronicMailShared
import SwiftUI

private enum IOSRootStage: Equatable {
    case restoring
    case signedOut
    case preparing
    case signedIn
    case recovery(String)
}

struct ElectronicMailiOSRootView: View {
    @ObservedObject var inboxStore: InboxStore
    @ObservedObject var aiStore: AIInboxStore
    @ObservedObject var accountStore: GmailAccountSettingsStore
    let tokenStore: KeychainSessionTokenStore
    let authService: IOSGoogleOAuthService
    let demoMode: Bool

    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var stage = IOSRootStage.restoring
    @State private var signingIn = false
    @State private var signInError: String?
    @State private var signOutError: String?

    var body: some View {
        Group {
            switch stage {
            case .restoring:
                IOSLaunchView(title: "Opening Electronic Mail", detail: "Restoring your encrypted mailbox", progress: nil)
                    .task { await restoreSession() }
            case .signedOut:
                IOSSignInView(
                    signingIn: signingIn,
                    errorMessage: signInError ?? ElectronicMailiOSConfiguration.backendWarning,
                    onSignIn: { Task { await signIn() } }
                )
            case .preparing:
                IOSLaunchView(
                    title: preparationTitle,
                    detail: preparationDetail,
                    progress: inboxStore.setupProgress.progressFraction
                )
            case .signedIn:
                IOSAppShell(
                    inboxStore: inboxStore,
                    aiStore: aiStore,
                    accountStore: accountStore,
                    authService: authService,
                    onSignOut: { await signOut() },
                    onSessionInvalidated: { clearLocalSessionForSignIn() }
                )
                .alert("Sign out unavailable", isPresented: Binding(
                    get: { signOutError != nil },
                    set: { if !$0 { signOutError = nil } }
                )) {
                    Button("OK", role: .cancel) {}
                } message: {
                    Text(signOutError ?? "Try again when your queued mail changes can sync.")
                }
            case .recovery(let message):
                IOSRecoveryView(
                    message: message,
                    retry: { Task { await retrySavedSession() } },
                    signInAgain: { clearLocalSessionForSignIn() }
                )
            }
        }
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.3), value: stage)
        .onChange(of: scenePhase) { _, nextPhase in
            guard nextPhase == .active, stage == .signedIn else { return }
            Task {
                if !demoMode { await MailNotificationController.shared.refresh() }
                async let inbox: Void = inboxStore.refresh()
                async let accounts: Void = accountStore.load()
                _ = await (inbox, accounts)
            }
        }
    }

    private var preparationTitle: String {
        inboxStore.setupProgress.initialTargetReady ? "Almost ready" : "Preparing your mailbox"
    }

    private var preparationDetail: String {
        let progress = inboxStore.setupProgress
        if progress.initialTargetCount > 0 {
            return "\(progress.initialBodyReadyCount) of \(progress.initialBodyTargetCount) recent conversations ready"
        }
        return progress.phase.replacingOccurrences(of: "_", with: " ").capitalized
    }

    @MainActor
    private func restoreSession() async {
        if demoMode {
            inboxStore.setSessionToken("demo-session-token")
            stage = .preparing
            await inboxStore.load()
            finishPreparation()
            return
        }

        guard let token = tokenStore.load(), !token.isEmpty else {
            stage = .signedOut
            return
        }

        inboxStore.setSessionToken(token)
        if await inboxStore.restoreLocalCache() {
            stage = .signedIn
            Task { await inboxStore.load() }
            return
        }

        stage = .preparing
        await inboxStore.load()
        finishPreparation()
    }

    @MainActor
    private func retrySavedSession() async {
        stage = .preparing
        await inboxStore.load()
        finishPreparation()
    }

    @MainActor
    private func finishPreparation() {
        switch inboxStore.phase {
        case .loaded:
            stage = .signedIn
        case .failed(let message):
            // Keep the Keychain token and encrypted cache. Only explicit sign
            // out or a new completed sign-in may replace this saved session.
            stage = .recovery(message)
        case .idle, .loading:
            if inboxStore.canEnterWithBuildingDashboard {
                stage = .signedIn
            } else {
                stage = .recovery("Mailbox setup is still in progress. Try again in a moment.")
            }
        }
    }

    @MainActor
    private func signIn() async {
        guard !signingIn else { return }
        signingIn = true
        signInError = nil
        AppHaptics.lightImpact()
        defer { signingIn = false }

        do {
            let grant = try await authService.startGoogleAuthentication(baseURL: inboxStore.backendURL)
            let session = try await inboxStore.exchangeMobileSession(grant: grant)
            try tokenStore.save(session.sessionToken)
            stage = .preparing
            await inboxStore.load()
            finishPreparation()
            if stage == .signedIn { AppHaptics.success() }
        } catch {
            // A cancelled or failed browser session must not erase a previously
            // valid Keychain token. No token has been replaced until exchange succeeds.
            signInError = error.localizedDescription
            stage = .signedOut
            AppHaptics.error()
        }
    }

    @MainActor
    private func signOut() async {
        do {
            try await inboxStore.logoutRemoteSession()
            tokenStore.clear()
            inboxStore.setSessionToken(nil)
            aiStore.resetForAccountChange()
            stage = .signedOut
            AppHaptics.success()
        } catch {
            signOutError = error.localizedDescription
            AppHaptics.error()
        }
    }

    @MainActor
    private func clearLocalSessionForSignIn() {
        tokenStore.clear()
        inboxStore.setSessionToken(nil)
        aiStore.resetForAccountChange()
        signInError = nil
        stage = .signedOut
    }
}

private struct IOSSignInView: View {
    @Environment(\.colorScheme) private var colorScheme
    let signingIn: Bool
    let errorMessage: String?
    let onSignIn: () -> Void

    var body: some View {
        ScrollView {
            VStack(spacing: 32) {
                Spacer(minLength: 70)
                Image(systemName: "envelope.badge")
                    .font(.system(size: 70, weight: .medium))
                    .foregroundStyle(IOSMailDesign.accent)
                    .accessibilityHidden(true)
                VStack(spacing: 12) {
                    Text("Electronic Mail")
                        .font(.system(.largeTitle, design: .rounded, weight: .bold))
                    Text("Turn email into a clear inbox, organized matters, and to-do's — without changing how Gmail works.")
                        .font(.title3)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                }
                .padding(.horizontal, 12)

                VStack(alignment: .leading, spacing: 14) {
                    benefit("tray.full", "Every Gmail folder and action")
                    benefit("sparkles.rectangle.stack", "AI organization you control")
                    benefit("lock.shield", "Encrypted, account-isolated offline mail")
                }
                .padding(20)
                .mailCard()

                Button(action: onSignIn) {
                    HStack(spacing: 12) {
                        if signingIn { ProgressView() }
                        else { Image(systemName: "person.crop.circle.badge.checkmark") }
                        Text(signingIn ? "Opening Google…" : "Continue with Google")
                            .font(.system(.headline, design: .rounded))
                    }
                    .frame(maxWidth: .infinity)
                    .frame(minHeight: 52)
                }
                .buttonStyle(.borderedProminent)
                .disabled(signingIn)

                if let errorMessage {
                    Label(errorMessage, systemImage: "exclamationmark.triangle")
                        .font(.footnote)
                        .foregroundStyle(.red)
                        .multilineTextAlignment(.center)
                }
                Text("Google sign-in opens in a secure system browser. Electronic Mail never receives your Google password.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
            }
            .padding(24)
        }
        .background(IOSMailDesign.canvas(colorScheme).ignoresSafeArea())
    }

    private func benefit(_ symbol: String, _ text: String) -> some View {
        Label(text, systemImage: symbol)
            .font(.system(.body, design: .rounded, weight: .medium))
            .symbolRenderingMode(.hierarchical)
    }
}

private struct IOSLaunchView: View {
    @Environment(\.colorScheme) private var colorScheme
    let title: String
    let detail: String
    let progress: Double?

    var body: some View {
        VStack(spacing: 22) {
            Image(systemName: "envelope.badge")
                .font(.system(size: 56, weight: .medium))
                .foregroundStyle(IOSMailDesign.accent)
                .symbolEffect(.pulse, options: .repeating)
            VStack(spacing: 8) {
                Text(title).font(.system(.title2, design: .rounded, weight: .bold))
                Text(detail).font(.subheadline).foregroundStyle(.secondary).multilineTextAlignment(.center)
            }
            if let progress {
                ProgressView(value: progress)
                    .frame(maxWidth: 280)
                    .accessibilityLabel("Mailbox preparation")
                    .accessibilityValue("\(Int(progress * 100)) percent")
            } else {
                ProgressView()
            }
        }
        .padding(28)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(IOSMailDesign.canvas(colorScheme).ignoresSafeArea())
    }
}

private struct IOSRecoveryView: View {
    let message: String
    let retry: () -> Void
    let signInAgain: () -> Void

    var body: some View {
        ContentUnavailableView {
            Label("Mailbox unavailable", systemImage: "wifi.exclamationmark")
        } description: {
            Text("\(message)\n\nYour saved session and encrypted mailbox have not been deleted.")
        } actions: {
            Button("Try Again", action: retry).buttonStyle(.borderedProminent)
            Button("Use Another Sign-in", role: .destructive, action: signInAgain)
        }
    }
}
