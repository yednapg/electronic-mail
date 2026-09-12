import ElectronicMailShared
import SwiftUI
import UIKit

struct IOSAppShell: View {
    @ObservedObject var inboxStore: InboxStore
    @ObservedObject var aiStore: AIInboxStore
    @ObservedObject var accountStore: GmailAccountSettingsStore
    let authService: IOSGoogleOAuthService
    let onSignOut: () async -> Void
    let onSessionInvalidated: () -> Void

    @State private var router = IOSRouter()
    @ObservedObject private var notifications = MailNotificationController.shared
    @State private var routingNotification = false
    @AppStorage("ElectronicMail.iOS.appearance") private var appearanceValue = IOSAppearance.system.rawValue
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        @Bindable var router = router

        NavigationStack(path: $router.path) {
            IOSMailboxView(
                store: inboxStore,
                router: router
            )
            .navigationDestination(for: IOSRoute.self) { route in
                destination(route)
            }
        }
        .overlay {
            if router.drawerPresented {
                IOSMailboxDrawer(
                    inboxStore: inboxStore,
                    accountStore: accountStore,
                    router: router,
                    onSignOut: onSignOut
                )
                .transition(.move(edge: .leading).combined(with: .opacity))
                .zIndex(10)
            }
        }
        .animation(reduceMotion ? nil : .snappy(duration: 0.24), value: router.drawerPresented)
        .sheet(item: $router.sheet) { sheet in
            switch sheet {
            case .settings:
                IOSSettingsRootView(
                    inboxStore: inboxStore,
                    aiStore: aiStore,
                    accountStore: accountStore,
                    authService: authService,
                    onSignOut: onSignOut,
                    onSessionInvalidated: onSessionInvalidated
                )
            case .newTask:
                IOSNewTaskView(store: inboxStore)
            case .aiOrganization:
                IOSAIOrganizationView(store: aiStore)
            }
        }
        .fullScreenCover(item: $router.composer) { context in
            IOSComposerView(
                store: inboxStore,
                accountStore: accountStore,
                context: context
            )
        }
        .preferredColorScheme(IOSAppearance(rawValue: appearanceValue)?.colorScheme)
        .onChange(of: accountStore.selectedScope) { oldValue, newValue in
            guard !routingNotification, inboxStore.mailboxViewScope != newValue else { return }
            guard oldValue != newValue, let accounts = accountStore.response else { return }
            router.resetForAccountChange()
            aiStore.resetForAccountChange(accountID: newValue.gmailAccountID)
            Task { await inboxStore.setMailboxViewScope(newValue, accounts: accounts) }
        }
        .task {
            await accountStore.load()
            if notifications.pendingOpen != nil {
                await openPendingNotification()
                return
            }
            if let accounts = accountStore.response {
                aiStore.resetForAccountChange(accountID: accountStore.selectedScope.gmailAccountID)
                await inboxStore.setMailboxViewScope(accountStore.selectedScope, accounts: accounts)
            }
        }
        .onChange(of: notifications.pendingOpen) { _, target in
            if target != nil { Task { await openPendingNotification() } }
        }
    }

    private func openPendingNotification() async {
        guard !routingNotification, let target = notifications.pendingOpen else { return }
        routingNotification = true
        defer { routingNotification = false }
        if accountStore.response == nil { await accountStore.load() }
        guard let accounts = accountStore.response else { return }
        if await inboxStore.openNotification(target, accounts: accounts) {
            accountStore.selectedScope = .gmail(accountID: target.accountID)
            aiStore.resetForAccountChange(accountID: target.accountID)
            router.drawerPresented = false
            router.sheet = nil
            router.path = [.reader(threadID: target.threadID, focusedMessageID: target.messageID)]
        }
        notifications.consume(target)
    }

    @ViewBuilder
    private func destination(_ route: IOSRoute) -> some View {
        switch route {
        case .reader(let threadID, let focusedMessageID):
            IOSReaderView(
                store: inboxStore,
                router: router,
                threadID: threadID,
                focusedMessageID: focusedMessageID
            )
        case .aiMatter(let matterID):
            if matterID == "__inbox__" {
                IOSAIInboxView(store: aiStore, router: router)
            } else {
                IOSAIMatterView(
                    store: aiStore,
                    router: router,
                    gmailAccountID: inboxStore.mailboxViewScope.gmailAccountID,
                    matterID: matterID
                )
            }
        case .todoSource(let threadID):
            if threadID == "__todo__" {
                IOSTodoView(inboxStore: inboxStore, aiStore: aiStore, router: router)
            } else {
                IOSReaderView(store: inboxStore, router: router, threadID: threadID, focusedMessageID: nil)
            }
        case .settings(let destination):
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
    }
}

private struct IOSMailboxView: View {
    @ObservedObject var store: InboxStore
    @Bindable var router: IOSRouter

    @Environment(\.colorScheme) private var colorScheme
    @State private var searchText = ""
    @State private var searchPresented = false
    @State private var searchTask: Task<Void, Never>?
    @State private var rowActionError: String?

    private var mailbox: MobileMailboxPresentation { store.mobileMailbox }

    var body: some View {
        Group {
            if mailbox.loading, mailbox.sections.isEmpty {
                ContentUnavailableView {
                    ProgressView()
                } description: {
                    Text(store.mailboxLoadingProgressText ?? "Loading your mailbox…")
                }
            } else if mailbox.isEmpty {
                ContentUnavailableView(
                    mailbox.searchQuery.isEmpty ? "Nothing here" : "No results",
                    systemImage: mailbox.searchQuery.isEmpty ? "tray" : "magnifyingglass",
                    description: Text(emptyDescription)
                )
            } else {
                mailboxList
            }
        }
        .background(IOSMailDesign.canvas(colorScheme))
        .accessibilityIdentifier("mailbox.screen")
        .navigationTitle(mailbox.title)
        .navigationBarTitleDisplayMode(.large)
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                Button {
                    router.drawerPresented = true
                    AppHaptics.lightImpact()
                } label: {
                    Image(systemName: "line.3.horizontal")
                }
                .minimumTouchTarget()
                .accessibilityLabel("Open mailbox menu")
                .accessibilityIdentifier("mailbox.menu")
            }
            ToolbarItemGroup(placement: .topBarTrailing) {
                Button {
                    searchPresented = true
                } label: {
                    Image(systemName: "magnifyingglass")
                }
                .minimumTouchTarget()
                .accessibilityLabel("Search mail")

                Button {
                    router.composer = .compose(gmailAccountID: accountStoreID)
                    AppHaptics.lightImpact()
                } label: {
                    Image(systemName: "square.and.pencil")
                }
                .minimumTouchTarget()
                .accessibilityLabel("Compose email")
                .accessibilityIdentifier("mailbox.compose")
            }
        }
        .searchable(
            text: $searchText,
            isPresented: $searchPresented,
            placement: .navigationBarDrawer(displayMode: .always),
            prompt: "Search \(mailbox.title)"
        )
        .onSubmit(of: .search) { runSearch(immediately: true) }
        .onChange(of: searchText) { _, _ in runSearch(immediately: false) }
        .onDisappear { searchTask?.cancel() }
        .safeAreaInset(edge: .bottom) {
            VStack(spacing: 6) {
                if let rowActionError {
                    IOSRefreshToast(text: rowActionError, isWarning: true) {
                        self.rowActionError = nil
                    }
                }
                if let footerText = mailbox.footerText {
                    IOSRefreshToast(text: footerText, isWarning: mailbox.refreshFailed) {
                        Task { await store.refresh() }
                    }
                }
            }
            .padding(.horizontal, 12)
            .padding(.bottom, 4)
        }
    }

    private var accountStoreID: String? { store.mailboxViewScope.gmailAccountID }

    private var emptyDescription: String {
        if let error = mailbox.searchError { return error }
        return mailbox.searchQuery.isEmpty
            ? "New mail will appear here after the next sync."
            : "Try a sender, subject, or different phrase."
    }

    private var mailboxList: some View {
        List {
            if !mailbox.accountWarnings.isEmpty {
                Section {
                    ForEach(mailbox.accountWarnings, id: \.self) { warning in
                        Label(warning, systemImage: "exclamationmark.triangle.fill")
                            .font(.footnote)
                            .foregroundStyle(.orange)
                    }
                }
            }

            ForEach(mailbox.sections) { section in
                Section(section.title) {
                    ForEach(section.rows) { row in
                        IOSMailboxRow(row: row) {
                            if row.isExpandable {
                                store.toggleExpansion(threadID: row.threadID)
                            }
                        }
                        .contentShape(Rectangle())
                        .onTapGesture {
                            open(row)
                        }
                        .swipeActions(edge: .leading, allowsFullSwipe: true) {
                            Button {
                                Task {
                                    await store.performTargetedThreadAction(
                                        row.unread ? .markRead : .markUnread,
                                        threadID: row.threadID,
                                        messageID: row.focusedMessageID
                                    )
                                }
                            } label: {
                                Label(row.unread ? "Read" : "Unread", systemImage: row.unread ? "envelope.open" : "envelope.badge")
                            }
                            .tint(.blue)

                            Button {
                                Task { await store.performTargetedThreadAction(.star, threadID: row.threadID, messageID: row.focusedMessageID) }
                            } label: {
                                Label("Star", systemImage: "star")
                            }
                            .tint(.yellow)
                        }
                        .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                            trailingActions(row)
                        }
                        .task {
                            if row.id == mailbox.sections.last?.rows.last?.id {
                                await store.loadMoreMailbox(automatic: true)
                            }
                        }
                    }
                }
            }

            if mailbox.loadingMore {
                HStack {
                    Spacer()
                    ProgressView()
                    Spacer()
                }
                .listRowBackground(Color.clear)
            }
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
        .refreshable { await store.refresh() }
    }

    @ViewBuilder
    private func trailingActions(_ row: MobileMailboxRowPresentation) -> some View {
        switch mailbox.label {
        case .trash:
            Button {
                Task { await store.performTargetedThreadAction(.restoreTrash, threadID: row.threadID, messageID: row.focusedMessageID) }
            } label: {
                Label("Restore", systemImage: "arrow.uturn.backward")
            }
            .tint(.green)

            Button(role: .destructive) {
                Task { await store.performTargetedThreadAction(.deleteForever, threadID: row.threadID, messageID: row.focusedMessageID) }
            } label: {
                Label("Delete", systemImage: "trash.slash")
            }
        case .spam:
            Button {
                Task { await store.performTargetedThreadAction(.notSpam, threadID: row.threadID, messageID: row.focusedMessageID) }
            } label: {
                Label("Not Spam", systemImage: "checkmark.shield")
            }
            .tint(.green)
        case .archive, .all:
            Button {
                Task { await store.performTargetedThreadAction(.unarchive, threadID: row.threadID, messageID: row.focusedMessageID) }
            } label: {
                Label("Inbox", systemImage: "tray.and.arrow.down")
            }
            .tint(.blue)
        default:
            Button {
                Task { await store.performTargetedThreadAction(.archive, threadID: row.threadID, messageID: row.focusedMessageID) }
            } label: {
                Label("Archive", systemImage: "archivebox")
            }
            .tint(.green)

            Button(role: .destructive) {
                Task { await store.performTargetedThreadAction(.moveTrash, threadID: row.threadID, messageID: row.focusedMessageID) }
            } label: {
                Label("Trash", systemImage: "trash")
            }
        }
    }

    private func runSearch(immediately: Bool) {
        searchTask?.cancel()
        let query = searchText
        searchTask = Task {
            if !immediately {
                try? await Task.sleep(for: .milliseconds(350))
            }
            guard !Task.isCancelled else { return }
            if query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                store.clearSearch()
            } else {
                await store.searchMailbox(query)
            }
        }
    }

    private func open(_ row: MobileMailboxRowPresentation) {
        rowActionError = nil
        if mailbox.label == .drafts {
            Task {
                do {
                    router.composer = .draft(try await store.loadMobileDraft(mailboxThreadID: row.threadID))
                    AppHaptics.selection()
                } catch {
                    rowActionError = error.localizedDescription
                    AppHaptics.error()
                }
            }
            return
        }

        _ = store.openReader(threadID: row.threadID, focusedMessageID: row.focusedMessageID)
        router.path.append(.reader(threadID: row.threadID, focusedMessageID: row.focusedMessageID))
        AppHaptics.selection()
    }
}

private struct IOSMailboxRow: View {
    @Environment(\.colorScheme) private var colorScheme
    let row: MobileMailboxRowPresentation
    let onToggleExpansion: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Circle()
                .fill(row.unread ? IOSMailDesign.accent : Color.clear)
                .frame(width: 8, height: 8)
                .padding(.top, 7)
                .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: 4) {
                HStack(alignment: .firstTextBaseline) {
                    Text(row.sender)
                        .font(.system(.subheadline, design: .rounded, weight: row.unread ? .bold : .semibold))
                        .lineLimit(1)
                    Spacer(minLength: 8)
                    Text(row.timeLabel)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                Text(row.title)
                    .font(.system(.body, design: .rounded, weight: row.unread ? .semibold : .regular))
                    .lineLimit(2)

                if let summary = row.summary, !summary.isEmpty {
                    Text(summary)
                        .font(.subheadline)
                        .foregroundStyle(IOSMailDesign.secondaryText(colorScheme))
                        .lineLimit(2)
                }

                HStack(spacing: 10) {
                    if row.messageCount > 1 {
                        Label("\(row.messageCount)", systemImage: "bubble.left.and.bubble.right")
                    }
                    if row.hasAttachments {
                        Image(systemName: "paperclip")
                    }
                    if let status = row.status, !status.isEmpty {
                        Text(status)
                    }
                }
                .font(.caption2)
                .foregroundStyle(.secondary)
            }

            if row.isExpandable {
                Button(action: onToggleExpansion) {
                    Image(systemName: row.isExpanded ? "chevron.up" : "chevron.down")
                }
                .buttonStyle(.plain)
                .minimumTouchTarget()
                .accessibilityLabel(row.isExpanded ? "Collapse conversation" : "Expand conversation")
            }
        }
        .padding(.leading, row.isChild ? 22 : 0)
        .padding(.vertical, 7)
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("mailbox.row.\(row.id)")
        .accessibilityHint("Opens email")
    }
}

private struct IOSRefreshToast: View {
    let text: String
    let isWarning: Bool
    let retry: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: isWarning ? "exclamationmark.arrow.triangle.2.circlepath" : "arrow.triangle.2.circlepath")
            Text(text)
                .font(.caption)
                .lineLimit(2)
            Spacer(minLength: 4)
            if isWarning {
                Button("Retry", action: retry)
                    .font(.caption.weight(.semibold))
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(.regularMaterial, in: Capsule())
        .shadow(color: .black.opacity(0.12), radius: 12, y: 5)
    }
}

private struct IOSMailboxDrawer: View {
    @ObservedObject var inboxStore: InboxStore
    @ObservedObject var accountStore: GmailAccountSettingsStore
    @Bindable var router: IOSRouter
    let onSignOut: () async -> Void

    @Environment(\.colorScheme) private var colorScheme

    private let destinations: [(String, String, MailboxLabel?)] = [
        ("Inbox", "tray", .inbox),
        ("AI Inbox", "sparkles", nil),
        ("Starred", "star", .starred),
        ("Drafts", "doc", .drafts),
        ("Sent", "paperplane", .sent),
        ("Spam", "exclamationmark.shield", .spam),
        ("Trash", "trash", .trash),
        ("Archive", "archivebox", .archive),
        ("All Mail", "tray.full", .all),
        ("To-do's", "checklist", nil),
    ]

    var body: some View {
        ZStack(alignment: .leading) {
            Color.black.opacity(0.35)
                .ignoresSafeArea()
                .onTapGesture { router.drawerPresented = false }
                .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: 0) {
                drawerHeader
                ScrollView {
                    VStack(spacing: 3) {
                        ForEach(destinations, id: \.0) { item in
                            drawerButton(title: item.0, symbol: item.1, label: item.2)
                        }
                    }
                    .padding(12)
                }

                Divider()
                HStack {
                    Button {
                        router.drawerPresented = false
                        router.sheet = .settings
                    } label: {
                        Label("Settings", systemImage: "gearshape")
                    }
                    .minimumTouchTarget()
                    Spacer()
                    Button(role: .destructive) {
                        router.resetForSignOut()
                        Task { await onSignOut() }
                    } label: {
                        Image(systemName: "rectangle.portrait.and.arrow.right")
                    }
                    .minimumTouchTarget()
                    .accessibilityLabel("Sign out")
                }
                .padding(.horizontal, 18)
                .padding(.vertical, 10)
            }
            .frame(width: min(UIScreen.main.bounds.width * 0.86, 370))
            .frame(maxHeight: .infinity)
            .background(IOSMailDesign.surface(colorScheme).ignoresSafeArea())
            .shadow(color: .black.opacity(0.28), radius: 24, x: 10)
            .accessibilityIdentifier("mailbox.drawer")
        }
    }

    private var drawerHeader: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Image(systemName: "envelope.badge")
                    .font(.title2)
                    .foregroundStyle(IOSMailDesign.accent)
                Text("Electronic Mail")
                    .font(.system(.title3, design: .rounded, weight: .bold))
                Spacer()
                Button { router.drawerPresented = false } label: {
                    Image(systemName: "xmark")
                }
                .minimumTouchTarget()
                .accessibilityLabel("Close mailbox menu")
            }

            if let accounts = accountStore.response, accounts.accounts.count > 1 {
                Picker("Mailbox account", selection: $accountStore.selectedScope) {
                    Text("All accounts").tag(MailboxViewScope.combined)
                    ForEach(accounts.accounts.filter { $0.state.isMailboxReadable }) { account in
                        Text(account.email).tag(MailboxViewScope.gmail(accountID: account.id))
                    }
                }
                .pickerStyle(.menu)
            } else {
                Text(inboxStore.mobileUserDisplayName ?? "Your mailbox")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.horizontal, 18)
        .padding(.top, 12)
        .padding(.bottom, 16)
    }

    private func drawerButton(title: String, symbol: String, label: MailboxLabel?) -> some View {
        Button {
            router.drawerPresented = false
            router.path.removeAll()
            if let label {
                Task { await inboxStore.setMailboxLabel(label) }
            } else if title == "AI Inbox" {
                router.path.append(.aiMatter("__inbox__"))
            } else {
                router.path.append(.todoSource("__todo__"))
            }
        } label: {
            HStack(spacing: 14) {
                Image(systemName: symbol)
                    .frame(width: 24)
                Text(title)
                    .font(.system(.body, design: .rounded, weight: selected(title: title, label: label) ? .semibold : .regular))
                Spacer()
                if let label,
                   let count = count(for: label),
                   count > 0 {
                    Text("\(count)")
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
            }
            .padding(.horizontal, 10)
            .frame(minHeight: 46)
            .background(selected(title: title, label: label) ? IOSMailDesign.accent.opacity(0.14) : .clear, in: RoundedRectangle(cornerRadius: 11))
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("drawer.\(title.lowercased().replacingOccurrences(of: " ", with: "-").replacingOccurrences(of: "'", with: ""))")
        .accessibilityAddTraits(selected(title: title, label: label) ? .isSelected : [])
    }

    private func selected(title: String, label: MailboxLabel?) -> Bool {
        label == inboxStore.mobileMailbox.label && router.path.isEmpty && title != "AI Inbox" && title != "To-do's"
    }

    private func count(for label: MailboxLabel) -> Int? {
        label == inboxStore.mobileMailbox.label ? inboxStore.mobileMailbox.unread : nil
    }
}
