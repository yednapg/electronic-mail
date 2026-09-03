import Foundation
import SwiftUI

public enum ElectronicMailAppearance: String, CaseIterable, Identifiable {
    case system
    case light
    case dark

    public static let storageKey = "ElectronicMail.Appearance"

    public var id: Self { self }

    public var title: String {
        switch self {
        case .system: return "System"
        case .light: return "Light"
        case .dark: return "Dark"
        }
    }

    public var colorScheme: ColorScheme? {
        switch self {
        case .system: return nil
        case .light: return .light
        case .dark: return .dark
        }
    }
}

public enum ElectronicMailStartupScreen: String, CaseIterable, Identifiable {
    case inbox
    case aiInbox
    case todos

    public static let storageKey = "ElectronicMail.StartupScreen"

    public var id: Self { self }

    public var title: String {
        switch self {
        case .inbox: return "Inbox"
        case .aiInbox: return "AI Inbox"
        case .todos: return "To-do’s"
        }
    }
}

enum ElectronicMailSettingsDestination: String, CaseIterable, Identifiable {
    case general
    case composing
    case accounts
    case aiInbox
    case accountData
    case about

    static let selectionStorageKey = "ElectronicMail.SelectedSettingsDestination"

    var id: Self { self }

    var title: String {
        switch self {
        case .general: return "General"
        case .composing: return "Composing"
        case .accounts: return "Accounts"
        case .aiInbox: return "AI Inbox"
        case .accountData: return "Account & Data"
        case .about: return "About"
        }
    }

    var systemImage: String {
        switch self {
        case .general: return "gearshape"
        case .composing: return "square.and.pencil"
        case .accounts: return "person.crop.circle"
        case .aiInbox: return "sparkles.rectangle.stack"
        case .accountData: return "externaldrive"
        case .about: return "info.circle"
        }
    }

    var searchKeywords: String {
        switch self {
        case .general:
            return "appearance theme system light dark startup launch inbox to-do todos"
        case .composing:
            return "compose composing send sending sender new message email account"
        case .accounts:
            return "account accounts gmail connected add remove inbox mailbox view sender"
        case .aiInbox:
            return "ai inbox organization grouping style summary summaries reader always show hide off enable disable rebuild data corrections"
        case .accountData:
            return "account data session sign out disconnect google delete privacy"
        case .about:
            return "about version build help legal privacy terms support"
        }
    }
}

public struct ElectronicMailSettingsView: View {
    @ObservedObject private var inboxStore: InboxStore
    @ObservedObject private var accountSettingsStore: GmailAccountSettingsStore
    @StateObject private var aiSettingsStore: AIOrganizationSettingsStore
    @StateObject private var accountLinkOAuthService: GoogleOAuthService
    @AppStorage(ElectronicMailSettingsDestination.selectionStorageKey)
    private var selectedDestinationRawValue = ElectronicMailSettingsDestination.accounts.rawValue
    @State private var searchText = ""

    public init(
        inboxStore: InboxStore,
        accountSettingsStore: GmailAccountSettingsStore
    ) {
        self.inboxStore = inboxStore
        self.accountSettingsStore = accountSettingsStore
        _aiSettingsStore = StateObject(
            wrappedValue: AIOrganizationSettingsStore(client: inboxStore.aiInboxClient)
        )
        _accountLinkOAuthService = StateObject(wrappedValue: GoogleOAuthService())
    }

    public var body: some View {
        NavigationSplitView {
            List(selection: selection) {
                if filteredDestinations.isEmpty {
                    Label("No Settings Found", systemImage: "magnifyingglass")
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(filteredDestinations) { destination in
                        Label(destination.title, systemImage: destination.systemImage)
                            .tag(destination)
                    }
                }
            }
            .listStyle(.sidebar)
            .navigationTitle("Settings")
            .navigationSplitViewColumnWidth(min: 176, ideal: 200, max: 240)
            .toolbar(removing: .sidebarToggle)
        } detail: {
            settingsDetail
        }
        .navigationSplitViewStyle(.balanced)
        .searchable(text: $searchText, placement: .sidebar, prompt: "Search Settings")
        .onChange(of: searchText) { _, _ in
            selectFirstVisibleDestinationIfNeeded()
        }
        .onChange(of: accountSettingsStore.selectedScope) { _, scope in
            guard let accounts = accountSettingsStore.response else { return }
            Task {
                await inboxStore.setMailboxViewScope(scope, accounts: accounts)
            }
        }
        .frame(minWidth: 760, idealWidth: 820, minHeight: 520, idealHeight: 600)
    }

    private var filteredDestinations: [ElectronicMailSettingsDestination] {
        let query = searchText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else {
            return ElectronicMailSettingsDestination.allCases
        }

        return ElectronicMailSettingsDestination.allCases.filter { destination in
            "\(destination.title) \(destination.searchKeywords)"
                .localizedCaseInsensitiveContains(query)
        }
    }

    private func selectFirstVisibleDestinationIfNeeded() {
        guard
            let firstDestination = filteredDestinations.first,
            let selectedDestination = ElectronicMailSettingsDestination(
                rawValue: selectedDestinationRawValue
            ),
            !filteredDestinations.contains(selectedDestination)
        else {
            return
        }

        selectedDestinationRawValue = firstDestination.rawValue
    }

    private var selection: Binding<ElectronicMailSettingsDestination?> {
        Binding(
            get: {
                ElectronicMailSettingsDestination(rawValue: selectedDestinationRawValue) ?? .accounts
            },
            set: { destination in
                if let destination {
                    selectedDestinationRawValue = destination.rawValue
                }
            }
        )
    }

    @ViewBuilder
    private var settingsDetail: some View {
        switch ElectronicMailSettingsDestination(rawValue: selectedDestinationRawValue) ?? .accounts {
        case .general:
            ElectronicMailGeneralSettingsView()
                .navigationTitle("General")
        case .composing:
            GmailComposingSettingsView(store: accountSettingsStore)
                .navigationTitle("Composing")
        case .accounts:
            GmailAccountSettingsView(
                store: accountSettingsStore,
                authorizeAccount: { authorizationURL in
                    try await accountLinkOAuthService.startGoogleAccountLink(
                        authorizationURL: authorizationURL
                    )
                },
                reopenAuthorizationURL: { authorizationURL in
                    accountLinkOAuthService.openGoogleAccountLinkInSafari(
                        authorizationURL
                    )
                },
                showsMailboxPreferences: true,
                showsDefaultSenderPreference: false,
                showsAIOrganizationSection: false
            )
            .navigationTitle("Accounts")
        case .aiInbox:
            AIOrganizationSettingsView(store: aiSettingsStore)
                .navigationTitle("AI Inbox")
        case .accountData:
            ElectronicMailAccountDataSettingsView(hasSessionToken: inboxStore.hasSessionToken)
                .navigationTitle("Account & Data")
        case .about:
            ElectronicMailAboutSettingsView()
                .navigationTitle("About")
        }
    }
}

private struct ElectronicMailGeneralSettingsView: View {
    @AppStorage(ElectronicMailAppearance.storageKey)
    private var appearanceRawValue = ElectronicMailAppearance.system.rawValue
    @AppStorage(ElectronicMailStartupScreen.storageKey)
    private var startupScreenRawValue = ElectronicMailStartupScreen.inbox.rawValue

    var body: some View {
        Form {
            Section("Appearance") {
                Picker("Appearance", selection: $appearanceRawValue) {
                    ForEach(ElectronicMailAppearance.allCases) { appearance in
                        Text(appearance.title).tag(appearance.rawValue)
                    }
                }
            }

            Section("Startup") {
                Picker("Open at launch", selection: $startupScreenRawValue) {
                    ForEach(ElectronicMailStartupScreen.allCases) { screen in
                        Text(screen.title).tag(screen.rawValue)
                    }
                }

                Text("The startup screen is used the next time you open Electronic Mail. You can always return to Inbox from the navigation menu.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
    }
}

private struct ElectronicMailAccountDataSettingsView: View {
    let hasSessionToken: Bool

    var body: some View {
        Form {
            Section("Session") {
                Button("Sign Out") {
                    NotificationCenter.default.post(name: .electronicMailSignOut, object: nil)
                }
                .disabled(!hasSessionToken)
            }

            Section("Google Connection") {
                Button("Disconnect Google…", role: .destructive) {
                    NotificationCenter.default.post(
                        name: .electronicMailDisconnectGoogle,
                        object: nil
                    )
                }
                .disabled(!hasSessionToken)

                Text("Disconnecting removes Google access, synced mail data, and app sessions from Electronic Mail. It does not delete messages from Gmail.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section("Electronic Mail Account") {
                Button("Delete Account…", role: .destructive) {
                    NotificationCenter.default.post(
                        name: .electronicMailDeleteAccount,
                        object: nil
                    )
                }
                .disabled(!hasSessionToken)

                Text("Account deletion requires typing DELETE in a separate confirmation. Gmail messages remain in Gmail.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
    }
}

private struct ElectronicMailAboutSettingsView: View {
    private var version: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "—"
    }

    private var build: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "—"
    }

    var body: some View {
        Form {
            Section {
                VStack(alignment: .leading, spacing: 8) {
                    Label("Electronic Mail", systemImage: "envelope.fill")
                        .font(.title2.weight(.semibold))
                    Text("A focused Gmail client with account-isolated AI organization.")
                        .foregroundStyle(.secondary)
                }
                .padding(.vertical, 8)

                LabeledContent("Version", value: version)
                LabeledContent("Build", value: build)
            }

            Section("Help & Legal") {
                Link("Privacy", destination: URL(string: "https://electronicmail.app/privacy")!)
                Link("Terms", destination: URL(string: "https://electronicmail.app/terms")!)
                Link("Support", destination: URL(string: "https://electronicmail.app/support")!)
            }
        }
        .formStyle(.grouped)
    }
}
