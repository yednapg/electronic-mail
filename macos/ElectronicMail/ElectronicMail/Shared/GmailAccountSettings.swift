import Foundation
import SwiftUI

public enum GmailComposingPreferences {
    public static let defaultSenderAccountIDStorageKey = "ElectronicMail.DefaultSenderGmailAccountID"
}

public enum GmailAccountSettingsPhase: Equatable {
    case idle
    case loading
    case loaded
    case failed(String)
}

@MainActor
public final class GmailAccountSettingsStore: ObservableObject {
    @Published public private(set) var phase: GmailAccountSettingsPhase = .idle
    @Published public private(set) var response: GmailAccountsResponse?
    @Published public private(set) var isLinkingAccount = false
    @Published public private(set) var pendingAuthorizationURL: URL?
    @Published public private(set) var linkNotice: String?
    @Published public var selectedScope: MailboxViewScope = .combined
    @Published public var defaultSenderAccountID: String? {
        didSet {
            if let defaultSenderAccountID {
                defaults.set(
                    defaultSenderAccountID,
                    forKey: GmailComposingPreferences.defaultSenderAccountIDStorageKey
                )
            } else {
                defaults.removeObject(
                    forKey: GmailComposingPreferences.defaultSenderAccountIDStorageKey
                )
            }
        }
    }

    private let client: AppClient
    private let defaults: UserDefaults
    private let startAccountLink: (String) async throws -> GmailAccountLinkStartResponse

    public init(
        client: AppClient,
        defaults: UserDefaults = .standard,
        startAccountLink: ((String) async throws -> GmailAccountLinkStartResponse)? = nil
    ) {
        self.client = client
        self.defaults = defaults
        self.startAccountLink = startAccountLink ?? { redirectTo in
            try await client.startGmailAccountLink(redirectTo: redirectTo)
        }
        defaultSenderAccountID = defaults.string(
            forKey: GmailComposingPreferences.defaultSenderAccountIDStorageKey
        )
    }

    public func load() async {
        if response == nil {
            phase = .loading
        }
        do {
            var next = try await client.gmailAccounts()
            let pendingAccounts = next.accounts.filter {
                !$0.isPrimary && ($0.state == .connecting || $0.state == .importing)
            }
            if !pendingAccounts.isEmpty {
                for account in pendingAccounts {
                    _ = try? await client.setupGmailAccount(accountID: account.id)
                }
                next = try await client.gmailAccounts()
            }
            response = next
            if let accountID = selectedScope.gmailAccountID,
               !next.accounts.contains(where: { $0.id == accountID }) {
                selectedScope = .combined
            }
            if let defaultSenderAccountID,
               !next.accounts.contains(where: {
                   $0.id == defaultSenderAccountID && $0.state == .ready
               }) {
                self.defaultSenderAccountID = nil
            }
            client.configureMailboxScope(selectedScope, accounts: next)
            phase = .loaded
        } catch {
            phase = .failed(error.localizedDescription)
        }
    }

    public func addAccount(
        authorize: @escaping (URL) async throws -> String
    ) async {
        guard !isLinkingAccount else { return }
        isLinkingAccount = true
        pendingAuthorizationURL = nil
        linkNotice = nil
        defer {
            pendingAuthorizationURL = nil
            isLinkingAccount = false
        }
        do {
            let start = try await startAccountLink(
                MobileAuthFlow.callbackRedirectURI
            )
            pendingAuthorizationURL = start.authorizationURL
            let linkedAccountID = try await authorize(start.authorizationURL)
            await load()
            guard response?.accounts.contains(where: { $0.id == linkedAccountID }) == true else {
                throw GmailAccountLinkError.accountNotReturned
            }
            linkNotice = "Gmail account added. You can now choose it from the inbox account menu."
        } catch is CancellationError {
            linkNotice = "Adding Gmail account was cancelled. Your current inbox was not changed."
        } catch {
            linkNotice = error.localizedDescription
        }
    }
}

public enum GmailAccountLinkError: LocalizedError, Equatable {
    case missingAccountID
    case failed(String)
    case accountNotReturned

    public var errorDescription: String? {
        switch self {
        case .missingAccountID:
            return "Google did not return the linked Gmail account. Please try again."
        case .failed(let message):
            return message
        case .accountNotReturned:
            return "The Gmail account was linked but its status could not be loaded. Pull to refresh."
        }
    }

    public static func linkedAccountID(from callbackURL: URL) throws -> String {
        guard let components = URLComponents(url: callbackURL, resolvingAgainstBaseURL: false) else {
            throw GmailAccountLinkError.missingAccountID
        }
        let items = components.queryItems ?? []
        let status = items.first(where: { $0.name == "status" })?.value?.lowercased()
        if status == "linked",
           let accountID = items.first(where: { $0.name == "gmail_account_id" })?.value,
           !accountID.isEmpty {
            return accountID
        }
        let message = items.first(where: { $0.name == "error" })?.value
        throw GmailAccountLinkError.failed(
            message ?? "Gmail account could not be added. Please try again."
        )
    }
}

public struct GmailAccountSettingsView: View {
    @ObservedObject private var store: GmailAccountSettingsStore
    private let authorizeAccount: ((URL) async throws -> String)?
    private let reopenAuthorizationURL: ((URL) -> Void)?
    private let showsMailboxPreferences: Bool
    private let showsDefaultSenderPreference: Bool
    private let showsAIOrganizationSection: Bool
    @State private var accountLinkTask: Task<Void, Never>?

    public init(
        store: GmailAccountSettingsStore,
        authorizeAccount: ((URL) async throws -> String)? = nil,
        reopenAuthorizationURL: ((URL) -> Void)? = nil,
        showsMailboxPreferences: Bool = true,
        showsDefaultSenderPreference: Bool = true,
        showsAIOrganizationSection: Bool = true
    ) {
        self.store = store
        self.authorizeAccount = authorizeAccount
        self.reopenAuthorizationURL = reopenAuthorizationURL
        self.showsMailboxPreferences = showsMailboxPreferences
        self.showsDefaultSenderPreference = showsDefaultSenderPreference
        self.showsAIOrganizationSection = showsAIOrganizationSection
    }

    public var body: some View {
        Form {
            accountsSection
            if showsMailboxPreferences {
                inboxSection
            }
            if showsAIOrganizationSection {
                aiSection
            }
            preservationSection
        }
        .formStyle(.grouped)
        .task { await store.load() }
        .refreshable { await store.load() }
    }

    @ViewBuilder
    private var accountsSection: some View {
        Section("Gmail Accounts") {
            switch store.phase {
            case .idle, .loading:
                if store.response == nil {
                    ProgressView("Loading accounts")
                }
            case .failed(let message) where store.response == nil:
                VStack(alignment: .leading, spacing: 8) {
                    Text("Accounts unavailable")
                    Text(message).font(.caption).foregroundStyle(.secondary)
                    Button("Try Again") { Task { await store.load() } }
                }
            default:
                if let response = store.response {
                    ForEach(response.accounts) { account in
                        GmailAccountSettingsRow(account: account)
                    }

                    Button {
                        guard let authorizeAccount else { return }
                        accountLinkTask = Task {
                            await store.addAccount(authorize: authorizeAccount)
                            accountLinkTask = nil
                        }
                    } label: {
                        if store.isLinkingAccount {
                            Label("Adding Gmail Account…", systemImage: "hourglass")
                        } else {
                            Label("Add Gmail Account", systemImage: "plus.circle")
                        }
                    }
                    .disabled(
                        !response.multiAccountEnabled
                            || response.accounts.count >= response.maxAccounts
                            || authorizeAccount == nil
                            || store.isLinkingAccount
                    )

                    if store.isLinkingAccount,
                       let authorizationURL = store.pendingAuthorizationURL {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("Finish signing in with Google in your browser. If the consent page stays blank or disabled, open the same secure sign-in in Safari.")
                                .font(.caption)
                                .foregroundStyle(.secondary)

                            HStack {
                                if let reopenAuthorizationURL {
                                    Button("Open in Safari") {
                                        reopenAuthorizationURL(authorizationURL)
                                    }
                                }

                                Button("Cancel", role: .cancel) {
                                    accountLinkTask?.cancel()
                                    accountLinkTask = nil
                                }
                            }
                        }
                    }

                    if let linkNotice = store.linkNotice {
                        Text(linkNotice)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }

                    if !response.multiAccountEnabled {
                        Text("Adding another Gmail stays unavailable until the existing inbox passes every preservation check and the rollout is enabled.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private var inboxSection: some View {
        if let response = store.response {
            Section("Inbox") {
                Picker("Inbox view", selection: $store.selectedScope) {
                    Text("Combined").tag(MailboxViewScope.combined)
                    ForEach(response.accounts.filter { $0.state.isMailboxReadable }) { account in
                        Text(account.email)
                            .tag(MailboxViewScope.gmail(accountID: account.id))
                    }
                }

                if showsDefaultSenderPreference {
                    Picker("New message sender", selection: $store.defaultSenderAccountID) {
                        Text("Ask Every Time").tag(String?.none)
                        ForEach(response.accounts.filter { $0.state == .ready }) { account in
                            Text(account.email).tag(String?.some(account.id))
                        }
                    }
                }

                Text("Combined changes only what you see. Each Gmail account is still fetched, synced, and changed separately.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    @ViewBuilder
    private var aiSection: some View {
        if let response = store.response {
            Section("AI Organization") {
                ForEach(response.accounts) { account in
                    LabeledContent(account.email) {
                        Text(account.state == .ready ? "Account-isolated" : "Unavailable")
                            .foregroundStyle(.secondary)
                    }
                }
                Text("AI context, matters, suggestions, and corrections never cross from one Gmail account into another.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    @ViewBuilder
    private var preservationSection: some View {
        if let response = store.response {
            Section("Data Preservation") {
                LabeledContent("Existing inbox migration") {
                    if response.migrationVerified {
                        Label("Verified", systemImage: "checkmark.seal.fill")
                            .foregroundStyle(.green)
                    } else {
                        Label("Not enabled", systemImage: "lock.fill")
                            .foregroundStyle(.secondary)
                    }
                }
                Text("Electronic Mail will not enable multiple accounts unless existing messages, threads, drafts, actions, sync state, and AI identifiers match.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }
}

public struct GmailComposingSettingsView: View {
    @ObservedObject private var store: GmailAccountSettingsStore

    public init(store: GmailAccountSettingsStore) {
        self.store = store
    }

    public var body: some View {
        Form {
            Section("Sending") {
                if let response = store.response {
                    Picker("New message sender", selection: $store.defaultSenderAccountID) {
                        Text("Ask Every Time").tag(String?.none)
                        ForEach(response.accounts.filter { $0.state == .ready }) { account in
                            Text(account.isPrimary ? "\(account.email) (Primary)" : account.email)
                                .tag(String?.some(account.id))
                        }
                    }

                    Text("New-message account choices only include Gmail accounts whose separate inbox setup is ready. Linked accounts waiting for setup are never selected.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else if case .failed(let message) = store.phase {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Sending accounts unavailable")
                        Text(message)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        Button("Try Again") { Task { await store.load() } }
                    }
                } else {
                    ProgressView("Loading sending accounts")
                }
            }
        }
        .formStyle(.grouped)
        .task { await store.load() }
    }
}

private struct GmailAccountSettingsRow: View {
    let account: GmailAccount

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: account.state == .ready ? "envelope.circle.fill" : "exclamationmark.circle")
                .font(.title2)
                .foregroundStyle(account.state == .ready ? .blue : .orange)

            VStack(alignment: .leading, spacing: 2) {
                Text(account.email)
                Text(statusText)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Spacer()

            if account.isPrimary {
                Text("Primary")
                    .font(.caption.weight(.semibold))
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)
                    .background(.quaternary, in: Capsule())
            }
        }
    }

    private var statusText: String {
        switch account.state {
        case .connecting: return "Linked — inbox setup pending"
        case .importing: return "Preparing inbox"
        case .ready: return "Ready"
        case .reauthRequired: return "Sign in again"
        case .disconnected: return "Disconnected"
        case .deleting: return "Removing"
        }
    }
}
