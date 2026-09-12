import ElectronicMailShared
import SwiftUI
import UIKit

struct IOSSettingsRootView: View {
    @ObservedObject var inboxStore: InboxStore
    @ObservedObject var aiStore: AIInboxStore
    @ObservedObject var accountStore: GmailAccountSettingsStore
    let authService: IOSGoogleOAuthService
    let onSignOut: () async -> Void
    let onSessionInvalidated: () -> Void

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List(IOSSettingsDestination.allCases) { destination in
                NavigationLink(destination.title, value: destination)
                    .badge(destination == .accounts ? accountStore.response?.accounts.count ?? 0 : 0)
                    .accessibilityLabel(destination.title)
            }
            .navigationTitle("Settings")
            .navigationDestination(for: IOSSettingsDestination.self) { destination in
                IOSSettingsDestinationView(
                    destination: destination,
                    inboxStore: inboxStore,
                    aiStore: aiStore,
                    accountStore: accountStore,
                    authService: authService,
                    onSignOut: onSignOut,
                    onSessionInvalidated: onSessionInvalidated
                )
            }
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
            }
        }
    }
}

struct IOSSettingsDestinationView: View {
    let destination: IOSSettingsDestination
    @ObservedObject var inboxStore: InboxStore
    @ObservedObject var aiStore: AIInboxStore
    @ObservedObject var accountStore: GmailAccountSettingsStore
    let authService: IOSGoogleOAuthService
    let onSignOut: () async -> Void
    let onSessionInvalidated: () -> Void

    var body: some View {
        Group {
            switch destination {
            case .notifications:
                MailNotificationSettingsView()
            case .general:
                IOSGeneralSettingsView()
            case .composing:
                GmailComposingSettingsView(store: accountStore)
            case .accounts:
                GmailAccountSettingsView(
                    store: accountStore,
                    authorizeAccount: { url in
                        try await authService.startGoogleAccountLink(
                            authorizationURL: url,
                            backendBaseURL: inboxStore.backendURL
                        )
                    },
                    reopenAuthorizationURL: { UIApplication.shared.open($0) },
                    showsAIOrganizationSection: false
                )
            case .aiInbox:
                IOSAISettingsView(store: aiStore)
            case .accountAndData:
                IOSAccountDataSettingsView(
                    store: inboxStore,
                    onSignOut: onSignOut,
                    onSessionInvalidated: onSessionInvalidated
                )
            case .about:
                IOSAboutView()
            }
        }
        .navigationTitle(destination.title)
        .navigationBarTitleDisplayMode(.inline)
    }
}

private struct IOSGeneralSettingsView: View {
    @AppStorage("ElectronicMail.iOS.appearance") private var appearance = IOSAppearance.system.rawValue
    @AppStorage(AIReaderSummaryVisibility.storageKey) private var summaryVisibility = AIReaderSummaryVisibility.always.rawValue

    var body: some View {
        Form {
            Section("Appearance") {
                Picker("Theme", selection: $appearance) {
                    ForEach(IOSAppearance.allCases) { option in
                        Text(option.title).tag(option.rawValue)
                    }
                }
                Picker("AI summaries", selection: $summaryVisibility) {
                    ForEach(AIReaderSummaryVisibility.allCases) { option in
                        Text(option.title).tag(option.rawValue)
                    }
                }
                Text("Text follows your iPhone's Dynamic Type setting, including accessibility sizes.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Section("Interaction") {
                Label("Single tap opens mail", systemImage: "hand.tap")
                Label("Swipe for common mailbox actions", systemImage: "hand.draw")
                Text("Reduce Motion and VoiceOver settings are respected automatically.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }
}

private struct IOSAISettingsView: View {
    @ObservedObject var store: AIInboxStore
    @State private var style = AIGroupingStyle.focused
    @State private var rebuild = false
    @State private var confirmDisable = false
    @State private var confirmDelete = false

    var body: some View {
        Form {
            Section("Organization") {
                Toggle("AI Inbox", isOn: Binding(
                    get: { store.profile?.enabled == true },
                    set: { enabled in
                        Task {
                            if enabled { await store.enable(style: style) }
                            else { _ = await store.disableAIInbox() }
                        }
                    }
                ))
                Picker("Grouping", selection: $style) {
                    ForEach(AIGroupingStyle.allCases) { option in Text(option.title).tag(option) }
                }
                Toggle("Rebuild existing matters", isOn: $rebuild)
                Button("Apply") {
                    Task { _ = await store.updateGroupingStyle(style, rebuildExisting: rebuild) }
                }
                .disabled(store.mutationInProgress)
            }
            Section("Privacy") {
                Text("AI matters, evidence, suggestions, and corrections are isolated by Gmail account.")
                    .font(.caption)
                Button("Disable AI Inbox", role: .destructive) { confirmDisable = true }
                Button("Delete AI organization data", role: .destructive) { confirmDelete = true }
            }
            if let error = store.errorMessage {
                Section { Text(error).foregroundStyle(.red) }
            }
        }
        .task {
            await store.refresh()
            style = store.profile?.groupingStyle ?? .focused
        }
        .confirmationDialog("Disable AI Inbox?", isPresented: $confirmDisable) {
            Button("Disable", role: .destructive) { Task { _ = await store.disableAIInbox() } }
        }
        .confirmationDialog("Delete AI organization data?", isPresented: $confirmDelete) {
            Button("Delete AI Data", role: .destructive) { Task { _ = await store.deleteAIData() } }
        } message: {
            Text("Your normal mailbox data is not deleted.")
        }
    }
}

private struct IOSAccountDataSettingsView: View {
    @ObservedObject var store: InboxStore
    let onSignOut: () async -> Void
    let onSessionInvalidated: () -> Void

    @State private var working = false
    @State private var message: String?
    @State private var confirmation: Confirmation?

    private enum Confirmation: String, Identifiable {
        case signOut
        case deleteSyncedData
        case disconnectGoogle
        case deleteAccount
        var id: Self { self }
    }

    var body: some View {
        Form {
            Section("Offline") {
                LabeledContent("Queued mail changes", value: "\(store.pendingLocalActionCount)")
                Text("Saved mailbox content is encrypted per account and is purged when that account is removed.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Section("Session") {
                Button("Sign Out", role: .destructive) { confirmation = .signOut }
            }
            Section("Data") {
                Button("Delete synced Google data", role: .destructive) { confirmation = .deleteSyncedData }
                Button("Disconnect Google and delete data", role: .destructive) { confirmation = .disconnectGoogle }
                Button("Delete Electronic Mail account", role: .destructive) { confirmation = .deleteAccount }
            }
            if working { Section { ProgressView("Applying change…") } }
            if let message { Section { Text(message).foregroundStyle(.secondary) } }
        }
        .confirmationDialog("This action cannot be undone", item: $confirmation) { action in
            Button(buttonTitle(action), role: .destructive) { Task { await perform(action) } }
            Button("Cancel", role: .cancel) {}
        }
    }

    private func buttonTitle(_ action: Confirmation) -> String {
        switch action {
        case .signOut: "Sign Out"
        case .deleteSyncedData: "Delete Synced Data"
        case .disconnectGoogle: "Disconnect and Delete"
        case .deleteAccount: "Delete Account"
        }
    }

    @MainActor
    private func perform(_ action: Confirmation) async {
        working = true
        defer { working = false }
        do {
            switch action {
            case .signOut:
                await onSignOut()
            case .deleteSyncedData:
                try await store.deleteSyncedGoogleData()
                message = "Synced Google data was deleted."
            case .disconnectGoogle:
                try await store.disconnectGoogleAndDeleteData()
                onSessionInvalidated()
            case .deleteAccount:
                try await store.deleteAccountPermanently()
                onSessionInvalidated()
            }
        } catch {
            message = error.localizedDescription
            AppHaptics.error()
        }
    }
}

private struct IOSAboutView: View {
    var body: some View {
        List {
            Section {
                VStack(spacing: 12) {
                    Image(systemName: "envelope.badge")
                        .font(.system(size: 54, weight: .medium))
                        .foregroundStyle(IOSMailDesign.accent)
                    Text("Electronic Mail")
                        .font(.system(.title2, design: .rounded, weight: .bold))
                    Text("Version \(version)")
                        .foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 20)
            }
            Section("About") {
                Text("A focused mail client that turns email into clear matters and to-do's while preserving the underlying Gmail mailbox.")
            }
            Section("Legal") {
                NavigationLink("Privacy") {
                    ScrollView { Text(privacyText).padding().textSelection(.enabled) }
                        .navigationTitle("Privacy")
                }
                NavigationLink("Terms") {
                    ScrollView { Text(termsText).padding().textSelection(.enabled) }
                        .navigationTitle("Terms")
                }
            }
        }
    }

    private var version: String {
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "1.0"
        let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "1"
        return "\(version) (\(build))"
    }

    private let privacyText = "Electronic Mail stores an encrypted offline copy of your mail on this device. Account-scoped encryption keys are held in Keychain. AI organization data remains isolated per Gmail account. Removing an account purges its local cache and key."
    private let termsText = "Electronic Mail is provided for internal evaluation. Gmail actions are applied to the connected account. Review recipients and attachments before sending, and confirm destructive operations when prompted."
}
