import ElectronicMailShared
import SwiftUI

private enum RootStage {
    case restoring
    case signedOut
    case importing
    case app
}

struct ElectronicMailiOSRootView: View {
    @ObservedObject var store: DashboardStore
    let tokenStore: KeychainSessionTokenStore
    let authService: IOSGoogleOAuthService

    @Environment(\.scenePhase) private var scenePhase
    @State private var stage: RootStage = .restoring
    @State private var signingIn = false
    @State private var signInError: String?
    @State private var importStartedAt = Date()

    var body: some View {
        Group {
            switch stage {
            case .restoring:
                ProgressView("Opening inbox")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .task {
                        await restoreSession()
                    }
            case .signedOut:
                SignInView(
                    signingIn: signingIn,
                    errorMessage: signInError,
                    onSignIn: {
                        Task { await signIn() }
                    }
                )
            case .importing:
                ImportProgressView(startedAt: importStartedAt)
            case .app:
                DashboardScreen(
                    store: store,
                    onSignOut: {
                        Task { await signOut() }
                    }
                )
            }
        }
        .onChange(of: scenePhase) { _, nextPhase in
            guard nextPhase == .active, stage == .app else {
                return
            }
            Task { await store.refresh() }
        }
    }

    @MainActor
    private func restoreSession() async {
        guard let token = tokenStore.load(), !token.isEmpty else {
            stage = .signedOut
            return
        }

        store.setSessionToken(token)
        await store.load()

        if case .failed(let message) = store.phase {
            tokenStore.clear()
            store.setSessionToken(nil)
            signInError = message
            stage = .signedOut
            return
        }

        stage = .app
    }

    @MainActor
    private func signIn() async {
        guard !signingIn else {
            return
        }

        signingIn = true
        signInError = nil
        AppHaptics.lightImpact()

        do {
            let loginCode = try await authService.startGoogleAuthentication(baseURL: store.backendURL)
            let session = try await store.exchangeMobileSession(loginCode: loginCode)
            try tokenStore.save(session.sessionToken)
            importStartedAt = Date()
            withAnimation(.easeInOut(duration: 0.35)) {
                stage = .importing
            }
            let minimumImportDisplay = Task {
                try? await Task.sleep(nanoseconds: 5_200_000_000)
            }
            await store.load()
            await minimumImportDisplay.value

            if case .failed(let message) = store.phase {
                withAnimation(.easeInOut(duration: 0.25)) {
                    stage = .signedOut
                }
                throw RuntimeError(message)
            }

            AppHaptics.success()
            withAnimation(.easeInOut(duration: 0.45)) {
                stage = .app
            }
        } catch {
            tokenStore.clear()
            store.setSessionToken(nil)
            signInError = error.localizedDescription
            withAnimation(.easeInOut(duration: 0.25)) {
                stage = .signedOut
            }
            AppHaptics.error()
        }

        signingIn = false
    }

    @MainActor
    private func signOut() async {
        AppHaptics.warning()
        await store.logout()
        tokenStore.clear()
        stage = .signedOut
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

private struct SignInView: View {
    @Environment(\.colorScheme) private var colorScheme

    let signingIn: Bool
    let errorMessage: String?
    let onSignIn: () -> Void

    var body: some View {
        VStack(spacing: 42) {
            Spacer()

            VStack(spacing: 28) {
                HStack(spacing: 18) {
                    Text("📥")
                    Image(systemName: "arrow.right")
                        .font(.system(size: 42, weight: .heavy, design: .rounded))
                    Text("✅")
                }
                .font(.system(size: 64))
                .accessibilityHidden(true)

                Text("Electronic Mail")
                    .font(.system(size: 31, weight: .bold, design: .rounded))
                    .multilineTextAlignment(.center)
                    .foregroundStyle(IOSMailSurface.primaryText(for: colorScheme))
                    .minimumScaleFactor(0.72)
                    .lineLimit(2)
            }

            Button(action: onSignIn) {
                HStack(spacing: 12) {
                    if signingIn {
                        ProgressView()
                    } else {
                        GoogleMark()
                    }

                    Text(signingIn ? "Opening Google" : "Sign up with Google")
                        .font(.system(size: 20, weight: .regular, design: .rounded))
                }
                .foregroundStyle(IOSMailSurface.primaryText(for: colorScheme))
                .frame(width: 260)
                .frame(height: 54)
                .background(IOSMailSurface.controlFill(for: colorScheme), in: RoundedRectangle(cornerRadius: 6, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: 6, style: .continuous)
                        .stroke(IOSMailSurface.controlBorder(for: colorScheme), lineWidth: 1)
                )
            }
            .buttonStyle(.plain)
            .disabled(signingIn)

            VStack(spacing: 10) {
                if let errorMessage {
                    Text(errorMessage)
                        .font(.footnote)
                        .foregroundStyle(.red)
                        .multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }

            Spacer()
        }
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(IOSMailSurface.background(for: colorScheme))
    }
}

private struct GoogleMark: View {
    var body: some View {
        Text("G")
            .font(.system(size: 28, weight: .bold, design: .rounded))
            .foregroundStyle(.blue)
            .frame(width: 34, height: 34)
            .accessibilityLabel("Google")
    }
}

private struct ImportProgressView: View {
    @Environment(\.colorScheme) private var colorScheme

    let startedAt: Date

    private let steps = [
        "Importing emails ...",
        "Grouping related emails ...",
        "Writing useful titles ...",
        "Preparing your inbox ...",
        "Almost ready!",
    ]

    var body: some View {
        ZStack {
            IOSMailSurface.background(for: colorScheme)
                .ignoresSafeArea()

            TimelineView(.periodic(from: startedAt, by: 0.25)) { timeline in
                let elapsed = max(0, timeline.date.timeIntervalSince(startedAt))
                let step = min(Int(elapsed / 1.05), steps.count - 1)

                WavyStatusText(text: steps[step])
                    .id(step)
                    .transition(.opacity.combined(with: .scale(scale: 0.985)))
                    .animation(.easeInOut(duration: 0.35), value: step)
            }
            .padding(.horizontal, 26)
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
                        .font(.system(size: 34, weight: .bold, design: .rounded))
                        .foregroundStyle(IOSMailSurface.primaryText(for: colorScheme).opacity(opacity))
                        .minimumScaleFactor(0.58)
                }
            }
            .lineLimit(1)
            .minimumScaleFactor(0.58)
            .accessibilityLabel(text)
        }
    }
}

private enum IOSMailSurface {
    static func background(for colorScheme: ColorScheme) -> Color {
        colorScheme == .dark ? .black : .white
    }

    static func primaryText(for colorScheme: ColorScheme) -> Color {
        colorScheme == .dark ? .white : .black
    }

    static func controlFill(for colorScheme: ColorScheme) -> Color {
        colorScheme == .dark ? Color.white.opacity(0.08) : Color.black.opacity(0.03)
    }

    static func controlBorder(for colorScheme: ColorScheme) -> Color {
        colorScheme == .dark ? Color.white.opacity(0.24) : Color.black.opacity(0.22)
    }
}
