import AppKit
import CryptoKit
import Security
import SwiftUI
import UniformTypeIdentifiers

enum ElectronicMailShellMetrics {
    static let navTop: CGFloat = 12
    static let navLeading: CGFloat = 20
    static let navIconFrame: CGFloat = 22
    static let navHitFrame: CGFloat = 40
    static let navTitleGap: CGFloat = 8
    static let navHeaderTitleGap: CGFloat = 4
    static let navTextLeading: CGFloat = navLeading + navHitFrame + navHeaderTitleGap
    static let contentTop: CGFloat = navTop + 44
    static let contentMaxWidth: CGFloat = 900

    private static let figmaCanvasWidth: CGFloat = 1_724
    private static let figmaUtilityCenterX: CGFloat = 77
    private static let figmaTextLeadingX: CGFloat = 129

    static func utilityCenter(for width: CGFloat) -> CGFloat {
        width * figmaUtilityCenterX / figmaCanvasWidth
    }

    static func textLeading(for width: CGFloat) -> CGFloat {
        width * figmaTextLeadingX / figmaCanvasWidth
    }
}

struct ComposerRecoveryPurgeOperations: Sendable {
    let markPending: @Sendable () -> Void
    let completePending: @Sendable () async -> Void

    static let live = ComposerRecoveryPurgeOperations(
        markPending: {
            ComposerRecoveryStore.requestDestructivePurge()
        },
        completePending: {
            await ComposerRecoveryWriter.shared.completeDestructivePurge()
        }
    )
}

@MainActor
public final class ElectronicMailComposerShutdownCoordinator {
    public static let shared = ElectronicMailComposerShutdownCoordinator()

    private var registrationID: UUID?
    private var prepareHandler: (() async -> Bool)?
    private let recoveryPurgeOperations: ComposerRecoveryPurgeOperations
    private var recoveryPurgeTask: Task<Void, Never>?

    init(recoveryPurgeOperations: ComposerRecoveryPurgeOperations = .live) {
        self.recoveryPurgeOperations = recoveryPurgeOperations
    }

    public var hasActiveComposer: Bool {
        prepareHandler != nil
    }

    public func prepareForShutdown() async -> Bool {
        guard let prepareHandler else {
            return true
        }
        return await prepareHandler()
    }

    public func clearRecoveryData() {
        // Persist the destructive intent before returning to the sign-out UI.
        // Keychain work is then serialized away from MainActor. If the process
        // exits before it completes, the durable marker makes the next recovery
        // load finish the purge instead of restoring a signed-out draft.
        recoveryPurgeOperations.markPending()
        let previousPurgeTask = recoveryPurgeTask
        let completePending = recoveryPurgeOperations.completePending
        recoveryPurgeTask = Task.detached(priority: .utility) {
            await previousPurgeTask?.value
            await completePending()
        }
    }

    func waitForRecoveryDataPurge() async {
        await recoveryPurgeTask?.value
    }

    fileprivate func register(id: UUID, prepare: @escaping () async -> Bool) {
        registrationID = id
        prepareHandler = prepare
    }

    fileprivate func unregister(id: UUID) {
        guard registrationID == id else {
            return
        }
        registrationID = nil
        prepareHandler = nil
    }
}

public struct SignedInShellView: View {
    @Environment(\.colorScheme) private var colorScheme
    @ObservedObject private var store: InboxStore
    private let onReauthorizeGoogle: () async throws -> Void
    private let onSignOut: () async throws -> Void
    private let onDisconnectGoogle: () async throws -> Void
    private let onDeleteAccount: () async throws -> Void
    @State private var selection: SignedInDestination = .inbox
    @State private var supplementalDestination: ShellSupplementalDestination?
    @State private var navigationOpen = false
    @State private var mailboxSearchOpen = false
    @State private var commandPaletteOpen = false
    @State private var composer: MailComposerPresentation?
    @State private var recoveredComposerSnapshot: ComposerRecoverySnapshot?
    @State private var composerRecoveryLoadGate = MailComposerRecoveryLoadGate<MailComposerPresentation>()
    @State private var pendingCommandThreadID: String?
    @State private var mailboxSearchText = ""

    public init(
        store: InboxStore,
        onReauthorizeGoogle: @escaping () async throws -> Void = {},
        onSignOut: @escaping () async throws -> Void = {},
        onDisconnectGoogle: @escaping () async throws -> Void = {},
        onDeleteAccount: @escaping () async throws -> Void = {}
    ) {
        self.store = store
        self.onReauthorizeGoogle = onReauthorizeGoogle
        self.onSignOut = onSignOut
        self.onDisconnectGoogle = onDisconnectGoogle
        self.onDeleteAccount = onDeleteAccount
    }

    public var body: some View {
        GeometryReader { proxy in
            ZStack(alignment: .topLeading) {
                content
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .allowsHitTesting(!navigationOpen && !mailboxSearchOpen && composer == nil)
                    .accessibilityHidden(navigationOpen || mailboxSearchOpen || composer != nil)

                if !navigationOpen,
                   store.readerThreadID == nil,
                   supplementalDestination != .todos {
                    ShellHeaderTitle(
                        title: headerTitle,
                        width: proxy.size.width,
                        colorScheme: colorScheme
                    )
                    .transition(.opacity)
                    .accessibilityHidden(mailboxSearchOpen || composer != nil)
                    .zIndex(4)
                }

                if navigationOpen {
                    ShellNavigationCanvas(
                        width: proxy.size.width,
                        selection: primaryNavigationSelection,
                        colorScheme: colorScheme,
                        onSelect: selectPrimaryNavigation
                    )
                    .transition(.opacity)
                    .allowsHitTesting(composer == nil)
                    .accessibilityHidden(composer != nil)
                    .zIndex(2)
                }

                fixedNavigationButton(width: proxy.size.width)
                    .allowsHitTesting(!mailboxSearchOpen && composer == nil)
                    .accessibilityHidden(mailboxSearchOpen || composer != nil)
                    .zIndex(6)

                if mailboxSearchOpen {
                    MailboxSearchOverlay(
                        query: $mailboxSearchText,
                        colorScheme: colorScheme,
                        onClose: closeMailboxSearch
                    )
                    .transition(.opacity.combined(with: .scale(scale: 0.985, anchor: .top)))
                    .zIndex(8)
                }

                if commandPaletteOpen {
                    CommandPaletteView(
                        store: store,
                        colorScheme: colorScheme,
                        onRun: runCommand,
                        onClose: closeCommandPalette
                    )
                    .transition(.opacity.combined(with: .scale(scale: 0.985, anchor: .top)))
                    .zIndex(10)
                }

                Group {
                    if let composer {
                        MailComposerOverlay(
                            presentation: composer,
                            recoverySnapshot: recoveredComposerSnapshot,
                            store: store,
                            colorScheme: colorScheme,
                            onReauthorizeGoogle: onReauthorizeGoogle,
                            onClose: closeComposer(preserving:)
                        )
                        .transition(.opacity.combined(with: .scale(scale: 0.985, anchor: .center)))
                        .zIndex(12)
                    }
                }
                .animation(.easeInOut(duration: 0.14), value: composer)

                CommandPaletteKeyboardCapture(
                    isOpen: $commandPaletteOpen
                )
                .frame(width: 1, height: 1)
                .opacity(0.01)

                ShellAccountCommandHandler(
                    onSignOut: onSignOut,
                    onDisconnectGoogle: onDisconnectGoogle,
                    onDeleteAccount: onDeleteAccount
                )
                .frame(width: 1, height: 1)
                .zIndex(20)
            }
        }
        .background(ElectronicMailDesign.background(for: colorScheme))
        .task(id: selection) {
            await applyMailboxSelection()
        }
        .onChange(of: store.activeMailboxLabel) { _, label in
            guard supplementalDestination == nil else {
                return
            }
            let destination = SignedInDestination(mailboxLabel: label)
            if selection != destination {
                selection = destination
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: .electronicMailOpenCommandPalette)) { _ in
            openCommandPalette()
        }
        .onReceive(NotificationCenter.default.publisher(for: .electronicMailOpenComposer)) { _ in
            openComposeComposer()
        }
        .onReceive(NotificationCenter.default.publisher(for: .electronicMailSyncMailbox)) { _ in
            syncNow()
        }
        .onReceive(NotificationCenter.default.publisher(for: .electronicMailToggleNavigation)) { _ in
            toggleNavigation()
        }
        .onReceive(NotificationCenter.default.publisher(for: .electronicMailOpenMailboxSearch)) { _ in
            openMailboxSearch()
        }
        .task {
            await store.setFolderCountPrefetchEnabled(false)
            await restoreRecoveredComposerIfNeeded()
        }
        .onExitCommand {
            if composer != nil {
                NotificationCenter.default.post(name: .electronicMailComposerCloseRequested, object: nil)
            } else if commandPaletteOpen {
                closeCommandPalette()
            } else if mailboxSearchOpen {
                closeMailboxSearch()
            } else if navigationOpen {
                closeNavigation()
            } else if store.readerThreadID != nil {
                closeReader()
            }
        }
    }

    @ViewBuilder
    private var content: some View {
        switch supplementalDestination {
        case .todos:
            TodoHomeView(
                store: store,
                onCompose: openComposeComposer,
                onOpenSource: openTodoSource
            )
        case nil:
            InboxView(
                store: store,
                searchText: $mailboxSearchText,
                onRespond: openResponseComposer,
                onOpenDraft: openDraftComposer
            )
        }
    }

    private var headerTitle: String {
        switch supplementalDestination {
        case .todos:
            return "To-do's"
        case nil:
            return store.mailboxTitle
        }
    }

    private var primaryNavigationSelection: ShellPrimaryNavigationDestination? {
        if let supplementalDestination {
            return supplementalDestination.primaryNavigationDestination
        }
        return ShellPrimaryNavigationDestination(mailboxDestination: selection)
    }

    @ViewBuilder
    private func fixedNavigationButton(width: CGFloat) -> some View {
        if store.readerThreadID != nil, !navigationOpen {
            ShellBackButton(colorScheme: colorScheme, action: closeReader)
                .position(
                    x: ElectronicMailShellMetrics.utilityCenter(for: width),
                    y: ElectronicMailShellMetrics.navTop + ElectronicMailShellMetrics.navHitFrame / 2
                )
        } else {
            ShellMenuButton(
                colorScheme: colorScheme,
                accessibilityLabel: navigationOpen ? "Hide navigation" : "Show navigation",
                action: toggleNavigation
            )
            .position(
                x: ElectronicMailShellMetrics.utilityCenter(for: width),
                y: ElectronicMailShellMetrics.navTop + ElectronicMailShellMetrics.navHitFrame / 2
            )
        }
    }

    private func toggleNavigation() {
        withAnimation(ShellNavigationMotion.screen) {
            mailboxSearchOpen = false
            commandPaletteOpen = false
            navigationOpen.toggle()
        }
    }

    private func closeNavigation() {
        withAnimation(ShellNavigationMotion.screen) {
            navigationOpen = false
        }
    }

    private func openMailboxSearch() {
        store.closeReader()
        withAnimation(.easeInOut(duration: 0.12)) {
            supplementalDestination = nil
            navigationOpen = false
            commandPaletteOpen = false
            mailboxSearchOpen = true
        }
    }

    private func closeMailboxSearch() {
        withAnimation(.easeInOut(duration: 0.12)) {
            mailboxSearchOpen = false
        }
    }

    private func selectPrimaryNavigation(_ destination: ShellPrimaryNavigationDestination) {
        pendingCommandThreadID = nil
        store.closeReader()

        withAnimation(ShellNavigationMotion.screen) {
            navigationOpen = false
            mailboxSearchOpen = false
            supplementalDestination = destination.supplementalDestination
            if let mailboxDestination = destination.mailboxDestination {
                selection = mailboxDestination
            }
        }
    }

    private func openTodoSource(_ entityID: String) {
        pendingCommandThreadID = entityID
        withAnimation(.easeInOut(duration: 0.16)) {
            supplementalDestination = nil
            selection = .inbox
        }
        Task {
            await store.setMailboxLabel(.inbox)
            guard supplementalDestination == nil, selection == .inbox else {
                return
            }
            applyPendingCommandThreadIfNeeded(for: .inbox)
        }
    }

    private func openCommandPalette() {
        withAnimation(.easeInOut(duration: 0.12)) {
            navigationOpen = false
            mailboxSearchOpen = false
            commandPaletteOpen = true
        }
    }

    private func closeCommandPalette() {
        withAnimation(.easeInOut(duration: 0.12)) {
            commandPaletteOpen = false
        }
    }

    private func runCommand(_ command: CommandPaletteItem) {
        closeCommandPalette()

        switch command.action {
        case .navigate(let destination):
            select(destination)
        case .openThread(let threadID):
            pendingCommandThreadID = threadID
            if selection == .inbox, store.activeMailboxLabel == .inbox {
                supplementalDestination = nil
                applyPendingCommandThreadIfNeeded(for: .inbox)
            } else {
                supplementalDestination = nil
                selection = .inbox
            }
        case .compose:
            openComposeComposer()
        }
    }

    private func closeReader() {
        store.closeReader()
    }

    private func select(_ destination: SignedInDestination) {
        pendingCommandThreadID = nil
        supplementalDestination = nil
        navigationOpen = false
        mailboxSearchOpen = false
        selection = destination
    }

    private func openComposeComposer() {
        guard composer == nil else {
            return
        }
        withAnimation(.easeInOut(duration: 0.12)) {
            navigationOpen = false
            mailboxSearchOpen = false
            commandPaletteOpen = false
        }
        presentComposer(
            MailComposerPresentation(mode: .compose, threadID: nil, sourceMessageID: nil, title: "New Message")
        )
    }

    private func syncNow() {
        Task {
            await store.syncNow()
        }
    }

    @MainActor
    private func applyMailboxSelection() async {
        let destination = selection
        if destination != .inbox {
            pendingCommandThreadID = nil
        }
        if store.activeMailboxLabel != destination.mailboxLabel {
            await store.setMailboxLabel(destination.mailboxLabel)
        }
        guard selection == destination, store.activeMailboxLabel == destination.mailboxLabel else {
            return
        }
        applyPendingCommandThreadIfNeeded(for: destination)
    }

    @MainActor
    private func applyPendingCommandThreadIfNeeded(for destination: SignedInDestination) {
        guard destination == .inbox, let threadID = pendingCommandThreadID else {
            return
        }
        pendingCommandThreadID = nil
        store.select(threadID: threadID, prefetch: true)
    }

    private func openResponseComposer(threadID: String, mode: MailComposerMode, sourceMessageID: String) {
        presentComposer(
            MailComposerPresentation(
                mode: mode,
                threadID: threadID,
                sourceMessageID: sourceMessageID,
                title: store.readerRow?.title ?? mode.title
            )
        )
    }

    private func openDraftComposer(threadID: String) {
        presentComposer(
            MailComposerPresentation(mode: .draft, threadID: threadID, sourceMessageID: nil, title: "Edit Draft")
        )
    }

    private func presentComposer(_ requestedPresentation: MailComposerPresentation) {
        guard let requestedPresentation = composerRecoveryLoadGate.request(requestedPresentation) else {
            return
        }
        presentComposerAfterRecoveryLoad(requestedPresentation)
    }

    private func presentComposerAfterRecoveryLoad(_ requestedPresentation: MailComposerPresentation) {
        if let recovery = recoveredComposerSnapshot,
           MailComposerPolicy.shouldRestorePendingRecovery(
               recoveryAccountUserID: recovery.accountUserID,
               currentAccountUserID: store.session?.user.id
           ) {
            composer = MailComposerPresentation(
                mode: recovery.mode,
                threadID: recovery.threadID,
                sourceMessageID: recovery.sourceMessageID,
                title: recovery.title
            )
            return
        }
        recoveredComposerSnapshot = nil
        composer = requestedPresentation
    }

    private func closeComposer(preserving recovery: ComposerRecoverySnapshot?) {
        recoveredComposerSnapshot = recovery
        withAnimation(.easeInOut(duration: 0.12)) {
            composer = nil
        }
    }

    @MainActor
    private func restoreRecoveredComposerIfNeeded() async {
        let recovery = await ComposerRecoveryWriter.shared.load()
        guard !Task.isCancelled else { return }

        let recoveredPresentation: MailComposerPresentation?
        if let recovery, recovery.belongs(to: store.session?.user.id) {
            recoveredComposerSnapshot = recovery
            recoveredPresentation = MailComposerPresentation(
                mode: recovery.mode,
                threadID: recovery.threadID,
                sourceMessageID: recovery.sourceMessageID,
                title: recovery.title
            )
        } else {
            recoveredPresentation = nil
        }

        let presentation = composerRecoveryLoadGate.complete(
            recoveredPresentation: recoveredPresentation
        )
        guard composer == nil, let presentation else { return }
        presentComposerAfterRecoveryLoad(presentation)
    }
}

private enum ShellNavigationMotion {
    static let screen = Animation.easeInOut(duration: 0.15)
}

enum ShellSupplementalDestination: Equatable {
    case todos

    var primaryNavigationDestination: ShellPrimaryNavigationDestination {
        switch self {
        case .todos:
            return .todos
        }
    }
}

enum ShellPrimaryNavigationDestination: String, CaseIterable, Identifiable {
    case todos = "To-do's"
    case inbox = "Inbox"
    case starred = "Starred"
    case drafts = "Drafts"
    case sent = "Sent"
    case spam = "Spam"
    case trash = "Trash"
    case archive = "Archive"
    case all = "All Mail"

    var id: Self {
        self
    }

    var title: String {
        rawValue
    }

    init?(mailboxDestination: SignedInDestination) {
        switch mailboxDestination {
        case .inbox:
            self = .inbox
        case .starred:
            self = .starred
        case .drafts:
            self = .drafts
        case .sent:
            self = .sent
        case .spam:
            self = .spam
        case .trash:
            self = .trash
        case .archive:
            self = .archive
        case .all:
            self = .all
        }
    }

    var mailboxDestination: SignedInDestination? {
        switch self {
        case .inbox:
            return .inbox
        case .starred:
            return .starred
        case .drafts:
            return .drafts
        case .sent:
            return .sent
        case .spam:
            return .spam
        case .trash:
            return .trash
        case .archive:
            return .archive
        case .all:
            return .all
        case .todos:
            return nil
        }
    }

    var supplementalDestination: ShellSupplementalDestination? {
        switch self {
        case .todos:
            return .todos
        case .inbox, .starred, .drafts, .sent, .spam, .trash, .archive, .all:
            return nil
        }
    }

}

private struct ShellHeaderTitle: View {
    let title: String
    let width: CGFloat
    let colorScheme: ColorScheme

    var body: some View {
        Text(title)
            .font(ElectronicMailType.headerTitle())
            .tracking(ElectronicMailType.titleTracking)
            .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
            .lineLimit(1)
            .frame(height: ElectronicMailShellMetrics.navHitFrame, alignment: .leading)
            .padding(.top, ElectronicMailShellMetrics.navTop)
            .padding(.leading, ElectronicMailShellMetrics.textLeading(for: width))
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .allowsHitTesting(false)
            .accessibilityAddTraits(.isHeader)
    }
}

private struct ShellMenuButton: View {
    let colorScheme: ColorScheme
    let accessibilityLabel: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            ElectronicMailHamburgerIcon()
                .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                .frame(
                    width: ElectronicMailShellMetrics.navIconFrame,
                    height: ElectronicMailShellMetrics.navIconFrame
                )
                .frame(
                    width: ElectronicMailShellMetrics.navHitFrame,
                    height: ElectronicMailShellMetrics.navHitFrame
                )
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .frame(
            width: ElectronicMailShellMetrics.navHitFrame,
            height: ElectronicMailShellMetrics.navHitFrame
        )
        .contentShape(Rectangle())
        .help(accessibilityLabel)
        .accessibilityLabel(accessibilityLabel)
    }
}

private struct ShellBackButton: View {
    let colorScheme: ColorScheme
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: "chevron.backward")
                .font(.system(size: 18, weight: .semibold, design: .rounded))
                .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                .frame(
                    width: ElectronicMailShellMetrics.navHitFrame,
                    height: ElectronicMailShellMetrics.navHitFrame
                )
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .frame(
            width: ElectronicMailShellMetrics.navHitFrame,
            height: ElectronicMailShellMetrics.navHitFrame
        )
        .contentShape(Rectangle())
        .help("Back")
        .accessibilityLabel("Back")
    }
}

private struct ShellNavigationCanvas: View {
    let width: CGFloat
    let selection: ShellPrimaryNavigationDestination?
    let colorScheme: ColorScheme
    let onSelect: (ShellPrimaryNavigationDestination) -> Void

    var body: some View {
        ZStack(alignment: .topLeading) {
            ElectronicMailDesign.background(for: colorScheme)
                .ignoresSafeArea()

            VStack(alignment: .leading, spacing: 4) {
                ForEach(ShellPrimaryNavigationDestination.allCases) { destination in
                    ShellNavigationItem(
                        destination: destination,
                        isSelected: selection == destination,
                        colorScheme: colorScheme,
                        action: { onSelect(destination) }
                    )
                }
            }
            .padding(.top, ElectronicMailShellMetrics.navTop)
            .padding(.leading, ElectronicMailShellMetrics.textLeading(for: width))
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Mailbox navigation")
    }
}

private struct ShellNavigationItem: View {
    let destination: ShellPrimaryNavigationDestination
    let isSelected: Bool
    let colorScheme: ColorScheme
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(destination.title)
                .font(ElectronicMailType.title())
                .tracking(ElectronicMailType.titleTracking)
                .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                .opacity(isSelected ? 1 : 0.78)
                .frame(height: ElectronicMailShellMetrics.navHitFrame)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help(destination.title)
        .accessibilityLabel(destination.title)
        .accessibilityHint(
            isSelected
                ? "Currently selected"
                : "Opens \(destination.title)"
        )
        .accessibilityAddTraits(isSelected ? .isSelected : [])
    }
}

private struct MailboxSearchOverlay: View {
    @Binding var query: String
    let colorScheme: ColorScheme
    let onClose: () -> Void

    var body: some View {
        ZStack(alignment: .top) {
            Button(action: onClose) {
                Rectangle()
                    .fill(
                        ElectronicMailDesign.background(for: colorScheme)
                            .opacity(colorScheme == .dark ? 0.78 : 0.72)
                    )
                    .ignoresSafeArea()
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Close mailbox search")

            HStack(spacing: 10) {
                DebouncedMailboxToolbarSearchField(
                    query: $query,
                    onCancel: onClose
                )
                    .frame(width: 420, height: 30)

                Button("Done", action: onClose)
                    .buttonStyle(.plain)
                    .font(ElectronicMailType.small(weight: .semibold))
                    .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                    .accessibilityHint("Closes the search field and keeps the current results")
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 12)
            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .stroke(ElectronicMailDesign.divider(for: colorScheme), lineWidth: 1)
            }
            .padding(.top, ElectronicMailShellMetrics.navTop)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .onExitCommand(perform: onClose)
    }
}

private struct DebouncedMailboxToolbarSearchField: View {
    @Binding private var query: String
    @State private var fieldText: String
    private let onCancel: () -> Void

    init(query: Binding<String>, onCancel: @escaping () -> Void) {
        self._query = query
        self._fieldText = State(initialValue: query.wrappedValue)
        self.onCancel = onCancel
    }

    var body: some View {
        MailboxToolbarSearchField(text: $fieldText, onCancel: onCancel)
            .task(id: fieldText) {
                if fieldText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    if query != fieldText {
                        query = fieldText
                    }
                    return
                }
                try? await Task.sleep(nanoseconds: 350_000_000)
                guard !Task.isCancelled, query != fieldText else {
                    return
                }
                query = fieldText
            }
            .onChange(of: query) { _, nextQuery in
                if fieldText != nextQuery {
                    fieldText = nextQuery
                }
            }
    }
}

private struct MailboxToolbarSearchField: NSViewRepresentable {
    @Binding var text: String
    let onCancel: () -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(text: $text, onCancel: onCancel)
    }

    func makeNSView(context: Context) -> ElectronicMailSearchField {
        let field = ElectronicMailSearchField()
        field.delegate = context.coordinator
        field.onCancel = context.coordinator.onCancel
        field.placeholderString = "Search Mail"
        field.controlSize = .regular
        field.bezelStyle = .roundedBezel
        field.sendsSearchStringImmediately = true
        field.sendsWholeSearchString = false
        field.setContentCompressionResistancePriority(.defaultHigh, for: .horizontal)
        field.setAccessibilityLabel("Search Mail")
        return field
    }

    func updateNSView(_ field: ElectronicMailSearchField, context: Context) {
        context.coordinator.text = $text
        context.coordinator.onCancel = onCancel
        field.onCancel = context.coordinator.onCancel
        if field.stringValue != text {
            field.stringValue = text
        }
    }

    final class Coordinator: NSObject, NSSearchFieldDelegate {
        var text: Binding<String>
        var onCancel: () -> Void

        init(text: Binding<String>, onCancel: @escaping () -> Void) {
            self.text = text
            self.onCancel = onCancel
        }

        func controlTextDidChange(_ notification: Notification) {
            guard let field = notification.object as? NSSearchField else {
                return
            }
            text.wrappedValue = field.stringValue
        }
    }
}

final class ElectronicMailSearchField: NSSearchField {
    var onCancel: () -> Void = {}

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        guard let window else {
            return
        }
        window.makeFirstResponder(self)
        selectText(nil)
    }

    override func cancelOperation(_ sender: Any?) {
        onCancel()
    }
}

public extension Notification.Name {
    static let electronicMailToggleNavigation = Notification.Name("ElectronicMailToggleNavigation")
    static let electronicMailOpenMailboxSearch = Notification.Name("ElectronicMailOpenMailboxSearch")
    static let electronicMailOpenCommandPalette = Notification.Name("ElectronicMailOpenCommandPalette")
    static let electronicMailOpenComposer = Notification.Name("ElectronicMailOpenComposer")
    static let electronicMailSyncMailbox = Notification.Name("ElectronicMailSyncMailbox")
    static let electronicMailSignOut = Notification.Name("ElectronicMailSignOut")
    static let electronicMailDisconnectGoogle = Notification.Name("ElectronicMailDisconnectGoogle")
    static let electronicMailDeleteAccount = Notification.Name("ElectronicMailDeleteAccount")
    static let electronicMailComposerCloseRequested = Notification.Name("ElectronicMailComposerCloseRequested")
}

private enum CommandPaletteAction: Equatable {
    case navigate(SignedInDestination)
    case openThread(String)
    case compose
}

private struct CommandPaletteItem: Identifiable, Equatable {
    let id: String
    let title: String
    let subtitle: String
    let keywords: [String]
    let priority: Int
    let kind: Kind
    let action: CommandPaletteAction

    enum Kind: Equatable {
        case navigation
        case email
        case action
    }

    var searchText: String {
        CommandPaletteSearch.normalize(([title, subtitle] + keywords).joined(separator: " "))
    }
}

private enum CommandPaletteBuilder {
    static let resultLimit = 8
    static let searchQueryMinLength = 2

    @MainActor
    static func results(
        for query: String,
        store: InboxStore
    ) -> [CommandPaletteItem] {
        let normalizedQuery = CommandPaletteSearch.normalize(query)
        let staticCommands = navigationCommands() + composeCommands()
        let commands = normalizedQuery.count >= searchQueryMinLength
            ? staticCommands + emailCommands(from: store.sections)
            : staticCommands

        return CommandPaletteSearch
            .filter(commands: commands, query: query)
            .prefix(resultLimit)
            .map { $0 }
    }

    private static func navigationCommands() -> [CommandPaletteItem] {
        [
            CommandPaletteItem(
                id: "nav:inbox",
                title: "Inbox",
                subtitle: "Open email list",
                keywords: ["gmail", "mail", "email", "threads"],
                priority: 24,
                kind: .navigation,
                action: .navigate(.inbox)
            ),
            CommandPaletteItem(
                id: "nav:starred",
                title: "Starred",
                subtitle: "Open starred mail",
                keywords: ["star", "starred", "favorite", "mail"],
                priority: 25,
                kind: .navigation,
                action: .navigate(.starred)
            ),
            CommandPaletteItem(
                id: "nav:drafts",
                title: "Drafts",
                subtitle: "Open drafts",
                keywords: ["draft", "drafts", "mail"],
                priority: 25,
                kind: .navigation,
                action: .navigate(.drafts)
            ),
            CommandPaletteItem(
                id: "nav:sent",
                title: "Sent",
                subtitle: "Open sent mail",
                keywords: ["sent", "mail"],
                priority: 26,
                kind: .navigation,
                action: .navigate(.sent)
            ),
            CommandPaletteItem(
                id: "nav:spam",
                title: "Spam",
                subtitle: "Open spam",
                keywords: ["spam", "junk", "mail"],
                priority: 27,
                kind: .navigation,
                action: .navigate(.spam)
            ),
            CommandPaletteItem(
                id: "nav:trash",
                title: "Trash",
                subtitle: "Open trash",
                keywords: ["trash", "deleted", "mail"],
                priority: 28,
                kind: .navigation,
                action: .navigate(.trash)
            ),
            CommandPaletteItem(
                id: "nav:archive",
                title: "Archive",
                subtitle: "Open archive",
                keywords: ["archive", "archived mail"],
                priority: 29,
                kind: .navigation,
                action: .navigate(.archive)
            ),
            CommandPaletteItem(
                id: "nav:all",
                title: "All Mail",
                subtitle: "Open all mail",
                keywords: ["all mail", "every email", "mail"],
                priority: 30,
                kind: .navigation,
                action: .navigate(.all)
            ),
        ]
    }

    private static func composeCommands() -> [CommandPaletteItem] {
        [
            CommandPaletteItem(
                id: "action:compose",
                title: "Compose Email",
                subtitle: "Write a new message",
                keywords: ["compose", "new email", "new message", "mail", "send"],
                priority: 12,
                kind: .action,
                action: .compose
            ),
        ]
    }

    private static func emailCommands(from sections: [InboxSectionViewModel]) -> [CommandPaletteItem] {
        var seen = Set<String>()
        var commands: [CommandPaletteItem] = []

        for (sectionIndex, section) in sections.enumerated() {
            for (rowIndex, row) in section.rows.enumerated() {
                guard seen.insert(row.threadID).inserted else {
                    continue
                }

                commands.append(
                    CommandPaletteItem(
                        id: "email:\(row.threadID)",
                        title: "\(compact(row.sender, limit: 56)): \(compact(row.title, limit: 92))",
                        subtitle: "\(section.title) - Open email",
                        keywords: [
                            "gmail",
                            "mail",
                            "inbox",
                            "email",
                            section.title,
                            row.sender,
                            row.title,
                            row.threadID,
                        ],
                        priority: 18 + sectionIndex * 20 + rowIndex,
                        kind: .email,
                        action: .openThread(row.threadID)
                    )
                )
            }
        }

        return commands
    }

    private static func compact(_ value: String, limit: Int) -> String {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.count > limit else {
            return trimmed
        }
        let end = trimmed.index(trimmed.startIndex, offsetBy: max(0, limit - 1))
        return String(trimmed[..<end]) + "..."
    }
}

private enum CommandPaletteSearch {
    static func filter(commands: [CommandPaletteItem], query: String) -> [CommandPaletteItem] {
        let normalizedQuery = normalize(query)
        if normalizedQuery.isEmpty {
            return commands
                .filter { $0.kind == .navigation }
                .sorted(by: compare)
        }

        let tokens = normalizedQuery.split(separator: " ").map(String.init)
        return commands
            .compactMap { command -> (CommandPaletteItem, Int)? in
                let score = score(command: command, normalizedQuery: normalizedQuery, tokens: tokens)
                return score > 0 ? (command, score) : nil
            }
            .sorted { left, right in
                if left.1 != right.1 {
                    return left.1 > right.1
                }
                return compare(left.0, right.0)
            }
            .map(\.0)
    }

    static func normalize(_ value: String) -> String {
        value
            .folding(options: [.diacriticInsensitive, .caseInsensitive], locale: .current)
            .lowercased()
            .replacingOccurrences(of: #"[^a-z0-9]+"#, with: " ", options: .regularExpression)
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private static func score(
        command: CommandPaletteItem,
        normalizedQuery: String,
        tokens: [String]
    ) -> Int {
        guard tokens.allSatisfy({ command.searchText.contains($0) }) else {
            return 0
        }

        let title = normalize(command.title)
        let subtitle = normalize(command.subtitle)
        var score = 10

        if title == normalizedQuery {
            score += 90
        } else if title.hasPrefix(normalizedQuery) {
            score += 70
        } else if title.contains(normalizedQuery) {
            score += 45
        } else if subtitle.contains(normalizedQuery) {
            score += 16
        }

        score += max(0, 32 - command.priority)
        return score
    }

    private static func compare(_ left: CommandPaletteItem, _ right: CommandPaletteItem) -> Bool {
        if left.priority != right.priority {
            return left.priority < right.priority
        }
        return left.title.localizedCaseInsensitiveCompare(right.title) == .orderedAscending
    }
}

private struct CommandPaletteView: View {
    @ObservedObject var store: InboxStore
    let colorScheme: ColorScheme
    let onRun: (CommandPaletteItem) -> Void
    let onClose: () -> Void

    @State private var query = ""
    @State private var selectedIndex = 0
    @FocusState private var searchFocused: Bool

    var body: some View {
        let results = CommandPaletteBuilder.results(for: query, store: store)

        ZStack {
            Button(action: onClose) {
                Rectangle()
                    .fill(backdropFill)
                    .ignoresSafeArea()
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Close command palette")

            VStack(alignment: .leading, spacing: 10) {
                ZStack(alignment: .leading) {
                    if query.isEmpty {
                        Text("Command + K")
                            .font(ElectronicMailType.title())
                            .tracking(ElectronicMailType.titleTracking)
                            .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                    }

                    TextField("", text: $query)
                        .textFieldStyle(.plain)
                        .font(ElectronicMailType.title())
                        .tracking(ElectronicMailType.titleTracking)
                        .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                        .focused($searchFocused)
                }
                .frame(height: ElectronicMailType.bodyLineHeight)

                Rectangle()
                    .fill(ElectronicMailDesign.divider(for: colorScheme).opacity(query.isEmpty ? 0 : 1))
                    .frame(height: 1)

                if !results.isEmpty {
                    VStack(spacing: 2) {
                        ForEach(Array(results.enumerated()), id: \.element.id) { index, command in
                            Button {
                                selectedIndex = index
                                onRun(command)
                            } label: {
                                CommandPaletteResultRow(
                                    command: command,
                                    selected: index == selectedIndex,
                                    colorScheme: colorScheme
                                )
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                            .accessibilityLabel(command.title)
                            .accessibilityHint(command.subtitle)
                        }
                    }
                    .padding(.top, query.isEmpty ? 2 : 4)
                }
            }
            .padding(16)
            .frame(width: 640)
            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .stroke(ElectronicMailDesign.panelBorder(for: colorScheme), lineWidth: 1)
            }
            .shadow(color: Color.black.opacity(colorScheme == .dark ? 0.48 : 0.16), radius: 22, x: 0, y: 10)
            .background(
                CommandPaletteNavigationCapture(
                    selectedIndex: $selectedIndex,
                    resultsCount: results.count,
                    onRun: { runSelectedResult(in: results) },
                    onClose: onClose
                )
            )
        }
        .onAppear {
            DispatchQueue.main.async {
                searchFocused = true
            }
        }
        .onChange(of: query) { _, _ in
            selectedIndex = 0
        }
        .onChange(of: results.count) { _, count in
            selectedIndex = min(selectedIndex, max(0, count - 1))
        }
    }

    private var backdropFill: Color {
        ElectronicMailDesign.background(for: colorScheme).opacity(colorScheme == .dark ? 0.82 : 0.62)
    }

    private func runSelectedResult(in results: [CommandPaletteItem]) {
        guard results.indices.contains(selectedIndex) else {
            return
        }
        onRun(results[selectedIndex])
    }
}

private struct CommandPaletteResultRow: View {
    let command: CommandPaletteItem
    let selected: Bool
    let colorScheme: ColorScheme

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: symbolName)
                .font(ElectronicMailType.detail(weight: .semibold))
                .foregroundStyle(selected ? ElectronicMailDesign.selectedText(for: colorScheme) : ElectronicMailDesign.secondaryText(for: colorScheme))
                .frame(width: 26)

            VStack(alignment: .leading, spacing: 3) {
                Text(command.title)
                    .font(ElectronicMailType.detail(weight: .semibold))
                    .foregroundStyle(selected ? ElectronicMailDesign.selectedText(for: colorScheme) : ElectronicMailDesign.primaryText(for: colorScheme))
                    .lineLimit(1)

                Text(command.subtitle)
                    .font(ElectronicMailType.small())
                    .foregroundStyle(selected ? ElectronicMailDesign.selectedText(for: colorScheme).opacity(0.78) : ElectronicMailDesign.secondaryText(for: colorScheme))
                    .lineLimit(1)
            }

            Spacer(minLength: 16)
        }
        .frame(height: 48)
        .padding(.horizontal, 10)
        .background(
            RoundedRectangle(cornerRadius: 6, style: .continuous)
                .fill(selected ? ElectronicMailDesign.appleBlue : Color.clear)
        )
    }

    private var symbolName: String {
        switch command.kind {
        case .navigation:
            return "arrow.turn.down.right"
        case .email:
            return "envelope"
        case .action:
            return "square.and.pencil"
        }
    }
}

private struct CommandPaletteKeyboardCapture: NSViewRepresentable {
    @Binding var isOpen: Bool

    func makeNSView(context: Context) -> KeyCaptureView {
        let view = KeyCaptureView()
        view.onToggle = {
            withAnimation(.easeInOut(duration: 0.12)) {
                isOpen = true
            }
        }
        view.installMonitorIfNeeded()
        return view
    }

    func updateNSView(_ nsView: KeyCaptureView, context: Context) {
        nsView.onToggle = {
            withAnimation(.easeInOut(duration: 0.12)) {
                isOpen = true
            }
        }
        nsView.installMonitorIfNeeded()
    }

    final class KeyCaptureView: NSView {
        var onToggle: (() -> Void)?
        private var monitor: Any?

        deinit {
            if let monitor {
                NSEvent.removeMonitor(monitor)
            }
        }

        func installMonitorIfNeeded() {
            guard monitor == nil else {
                return
            }

            monitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
                guard
                    event.modifierFlags.intersection(.deviceIndependentFlagsMask).contains(.command),
                    !event.modifierFlags.intersection(.deviceIndependentFlagsMask).contains(.shift),
                    !event.modifierFlags.intersection(.deviceIndependentFlagsMask).contains(.option),
                    event.charactersIgnoringModifiers?.lowercased() == "k"
                else {
                    return event
                }

                self?.onToggle?()
                return nil
            }
        }
    }
}

private struct CommandPaletteNavigationCapture: NSViewRepresentable {
    @Binding var selectedIndex: Int
    let resultsCount: Int
    let onRun: () -> Void
    let onClose: () -> Void

    func makeNSView(context: Context) -> NavigationView {
        let view = NavigationView()
        view.configure(
            selectedIndex: $selectedIndex,
            resultsCount: resultsCount,
            onRun: onRun,
            onClose: onClose
        )
        view.installMonitorIfNeeded()
        return view
    }

    func updateNSView(_ nsView: NavigationView, context: Context) {
        nsView.configure(
            selectedIndex: $selectedIndex,
            resultsCount: resultsCount,
            onRun: onRun,
            onClose: onClose
        )
        nsView.installMonitorIfNeeded()
    }

    final class NavigationView: NSView {
        private var selectedIndex: Binding<Int>?
        private var resultsCount = 0
        private var onRun: (() -> Void)?
        private var onClose: (() -> Void)?
        private var monitor: Any?

        deinit {
            if let monitor {
                NSEvent.removeMonitor(monitor)
            }
        }

        func configure(
            selectedIndex: Binding<Int>,
            resultsCount: Int,
            onRun: @escaping () -> Void,
            onClose: @escaping () -> Void
        ) {
            self.selectedIndex = selectedIndex
            self.resultsCount = resultsCount
            self.onRun = onRun
            self.onClose = onClose
        }

        func installMonitorIfNeeded() {
            guard monitor == nil else {
                return
            }

            monitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
                self?.handle(event) ?? event
            }
        }

        private func handle(_ event: NSEvent) -> NSEvent? {
            switch event.keyCode {
            case 53:
                onClose?()
                return nil
            case 125:
                moveSelection(by: 1)
                return nil
            case 126:
                moveSelection(by: -1)
                return nil
            case 36, 76:
                onRun?()
                return nil
            default:
                return event
            }
        }

        private func moveSelection(by delta: Int) {
            guard resultsCount > 0, let selectedIndex else {
                return
            }

            selectedIndex.wrappedValue = (selectedIndex.wrappedValue + delta + resultsCount) % resultsCount
        }
    }
}

enum SignedInDestination: CaseIterable, Hashable, Identifiable {
    case inbox
    case starred
    case drafts
    case sent
    case spam
    case trash
    case archive
    case all

    var id: Self {
        self
    }

    var title: String {
        switch self {
        case .inbox:
            return "Inbox"
        case .starred:
            return "Starred"
        case .drafts:
            return "Drafts"
        case .sent:
            return "Sent"
        case .spam:
            return "Spam"
        case .trash:
            return "Trash"
        case .archive:
            return "Archive"
        case .all:
            return "All Mail"
        }
    }

    var mailboxLabel: MailboxLabel {
        switch self {
        case .inbox:
            return .inbox
        case .starred:
            return .starred
        case .drafts:
            return .drafts
        case .sent:
            return .sent
        case .spam:
            return .spam
        case .trash:
            return .trash
        case .archive:
            return .archive
        case .all:
            return .all
        }
    }

    var systemImage: String {
        switch self {
        case .inbox:
            return "tray.full"
        case .starred:
            return "star"
        case .drafts:
            return "doc"
        case .sent:
            return "paperplane"
        case .spam:
            return "exclamationmark.octagon"
        case .trash:
            return "trash"
        case .archive:
            return "archivebox"
        case .all:
            return "tray.2"
        }
    }

    init(mailboxLabel: MailboxLabel) {
        switch mailboxLabel {
        case .inbox:
            self = .inbox
        case .starred:
            self = .starred
        case .drafts:
            self = .drafts
        case .sent:
            self = .sent
        case .spam:
            self = .spam
        case .trash:
            self = .trash
        case .archive:
            self = .archive
        case .all:
            self = .all
        }
    }
}

private struct MailComposerPresentation: Identifiable, Equatable {
    let id = UUID()
    let mode: MailComposerMode
    let threadID: String?
    let sourceMessageID: String?
    let title: String
}

private struct ComposerRecoverySnapshot: Codable, Equatable, @unchecked Sendable {
    let accountUserID: String
    let mode: MailComposerMode
    let threadID: String?
    let sourceMessageID: String?
    let title: String
    let toText: String
    let ccText: String
    let bccText: String
    let subject: String
    let bodyText: String
    let clientSendID: String
    let serverSendID: String?
    let unresolvedSendAttempt: Bool?
    let clientDraftID: String
    let gmailDraftID: String?
    let gmailThreadID: String?
    let attachments: [ComposerAttachment]
    let existingDraftAttachments: [MailDraftAttachment]
    let preserveExistingDraftAttachments: Bool
    let sourceAttachmentCount: Int
    let includeOriginalAttachments: Bool
    let responseFieldProvenance: MailComposerResponseFieldProvenance?

    func belongs(to userID: String?) -> Bool {
        guard let userID else { return false }
        return accountUserID == userID
    }

    func matches(_ presentation: MailComposerPresentation, accountUserID: String?) -> Bool {
        belongs(to: accountUserID)
            && mode == presentation.mode
            && threadID == presentation.threadID
            && sourceMessageID == presentation.sourceMessageID
    }
}

private enum ComposerRecoveryStore {
    private static let maximumPlaintextBytes = 28 * 1_024 * 1_024
    private static let keychainService = "ElectronicMail"
    private static let keychainAccount = "composer_recovery_key_v1"
    private static let destructivePurgePendingDefaultsKey =
        "ElectronicMail.composer_recovery_destructive_purge_pending_v1"

    static func load() -> ComposerRecoverySnapshot? {
        if destructivePurgePending {
            completeDestructivePurge()
            return nil
        }
        removeLegacyPlaintextFile()
        guard let fileURL,
              let encrypted = try? Data(contentsOf: fileURL),
              encrypted.count <= maximumPlaintextBytes + 1_024,
              let key = loadKey() else {
            return nil
        }
        do {
            let sealedBox = try AES.GCM.SealedBox(combined: encrypted)
            let plaintext = try AES.GCM.open(sealedBox, using: key)
            guard plaintext.count <= maximumPlaintextBytes else {
                requestDestructivePurge()
                completeDestructivePurge()
                return nil
            }
            return try JSONDecoder.backend.decode(ComposerRecoverySnapshot.self, from: plaintext)
        } catch {
            requestDestructivePurge()
            completeDestructivePurge()
            return nil
        }
    }

    @discardableResult
    static func save(_ snapshot: ComposerRecoverySnapshot) -> Bool {
        if destructivePurgePending {
            completeDestructivePurge()
            return false
        }
        removeLegacyPlaintextFile()
        guard let fileURL,
              let plaintext = try? JSONEncoder.backend.encode(snapshot),
              plaintext.count <= maximumPlaintextBytes,
              let key = loadOrCreateKey() else {
            return false
        }
        do {
            let sealedBox = try AES.GCM.seal(plaintext, using: key)
            guard let encrypted = sealedBox.combined else {
                return false
            }
            try FileManager.default.createDirectory(
                at: fileURL.deletingLastPathComponent(),
                withIntermediateDirectories: true,
                attributes: [.posixPermissions: 0o700]
            )
            try encrypted.write(to: fileURL, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: fileURL.path)
            return true
        } catch {
            return false
        }
    }

    static func clear() {
        if let fileURL {
            try? FileManager.default.removeItem(at: fileURL)
        }
        removeLegacyPlaintextFile()
    }

    /// Records a durable purge intent without touching Security.framework.
    /// This is safe to call synchronously from MainActor during sign-out.
    static func requestDestructivePurge() {
        UserDefaults.standard.set(true, forKey: destructivePurgePendingDefaultsKey)
        if let destructivePurgeMarkerURL {
            try? FileManager.default.createDirectory(
                at: destructivePurgeMarkerURL.deletingLastPathComponent(),
                withIntermediateDirectories: true,
                attributes: [.posixPermissions: 0o700]
            )
            try? Data("pending".utf8).write(to: destructivePurgeMarkerURL, options: .atomic)
        }
        clear()
    }

    /// Completes a previously requested destructive purge. Callers must run
    /// this away from MainActor because Keychain deletion can block inside
    /// Security.framework. A transient failure retains the durable intent so
    /// the next recovery load retries instead of exposing signed-out content.
    static func completeDestructivePurge() {
        guard destructivePurgePending else {
            return
        }
        clear()
        let dataProtectionStatus = SecItemDelete(dataProtectionKeychainQuery() as CFDictionary)
        #if os(macOS)
        let classicStatus = SecItemDelete(classicMacKeychainQuery() as CFDictionary)
        let deletionFinished = keyDeletionReachedTerminalState(dataProtectionStatus)
            && keyDeletionReachedTerminalState(classicStatus)
        #else
        let deletionFinished = keyDeletionReachedTerminalState(dataProtectionStatus)
        #endif
        guard deletionFinished else {
            return
        }
        if let destructivePurgeMarkerURL {
            try? FileManager.default.removeItem(at: destructivePurgeMarkerURL)
        }
        UserDefaults.standard.removeObject(forKey: destructivePurgePendingDefaultsKey)
    }

    private static var destructivePurgePending: Bool {
        UserDefaults.standard.bool(forKey: destructivePurgePendingDefaultsKey)
            || destructivePurgeMarkerURL.map {
                FileManager.default.fileExists(atPath: $0.path)
            } == true
    }

    private static func keyDeletionReachedTerminalState(_ status: OSStatus) -> Bool {
        status == errSecSuccess
            || status == errSecItemNotFound
            || status == errSecMissingEntitlement
    }

    private static var fileURL: URL? {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first?
            .appendingPathComponent("ElectronicMail", isDirectory: true)
            .appendingPathComponent("ComposerRecovery.sealed", isDirectory: false)
    }

    private static var legacyPlaintextFileURL: URL? {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first?
            .appendingPathComponent("ElectronicMail", isDirectory: true)
            .appendingPathComponent("ComposerRecovery.json", isDirectory: false)
    }

    private static var destructivePurgeMarkerURL: URL? {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first?
            .appendingPathComponent("ElectronicMail", isDirectory: true)
            .appendingPathComponent("ComposerRecovery.purge-pending", isDirectory: false)
    }

    private static func removeLegacyPlaintextFile() {
        guard let legacyPlaintextFileURL else { return }
        try? FileManager.default.removeItem(at: legacyPlaintextFileURL)
    }

    private static func loadOrCreateKey() -> SymmetricKey? {
        if let existing = loadKey() {
            return existing
        }
        let key = SymmetricKey(size: .bits256)
        let data = key.withUnsafeBytes { Data($0) }
        let status = writeKeyData(
            data,
            query: dataProtectionKeychainQuery(),
            enforcesDeviceOnlyAccessibility: true
        )
        if status == errSecSuccess {
            return key
        }
        if status == errSecDuplicateItem {
            return loadKey()
        }
        #if os(macOS) && (DEBUG || ELECTRONIC_MAIL_LOCAL_BETA)
        if status == errSecMissingEntitlement {
            let classicStatus = writeKeyData(
                data,
                query: classicMacKeychainQuery(),
                enforcesDeviceOnlyAccessibility: false
            )
            if classicStatus == errSecSuccess {
                return key
            }
            if classicStatus == errSecDuplicateItem {
                return loadKey()
            }
        }
        #endif
        return nil
    }

    private static func loadKey() -> SymmetricKey? {
        if let data = readKeyData(query: dataProtectionKeychainQuery()), data.count == 32 {
            return SymmetricKey(data: data)
        }
        #if os(macOS) && (DEBUG || ELECTRONIC_MAIL_LOCAL_BETA)
        if let classicData = readKeyData(query: classicMacKeychainQuery()), classicData.count == 32 {
            if writeKeyData(
                classicData,
                query: dataProtectionKeychainQuery(),
                enforcesDeviceOnlyAccessibility: true
            ) == errSecSuccess {
                _ = SecItemDelete(classicMacKeychainQuery() as CFDictionary)
            }
            // This classic Keychain fallback is compiled only for local Debug or
            // explicitly marked local-beta builds. Production Release builds
            // require the device-only Data Protection Keychain.
            return SymmetricKey(data: classicData)
        }
        #endif
        return nil
    }

    private static func readKeyData(query base: [String: Any]) -> Data? {
        var query = base
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data else {
            return nil
        }
        return data
    }

    private static func writeKeyData(
        _ data: Data,
        query: [String: Any],
        enforcesDeviceOnlyAccessibility: Bool
    ) -> OSStatus {
        var attributes: [String: Any] = [kSecValueData as String: data]
        if enforcesDeviceOnlyAccessibility {
            attributes[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        }
        let updateStatus = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if updateStatus == errSecSuccess {
            return updateStatus
        }
        guard updateStatus == errSecItemNotFound else {
            return updateStatus
        }
        var item = query
        item[kSecValueData as String] = data
        if enforcesDeviceOnlyAccessibility {
            item[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        }
        return SecItemAdd(item as CFDictionary, nil)
    }

    private static func baseKeychainQuery() -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: keychainService,
            kSecAttrAccount as String: keychainAccount,
        ]
    }

    private static func dataProtectionKeychainQuery() -> [String: Any] {
        var query = baseKeychainQuery()
        query[kSecUseDataProtectionKeychain as String] = true
        query[kSecAttrSynchronizable as String] = false
        return query
    }

    #if os(macOS)
    private static func classicMacKeychainQuery() -> [String: Any] {
        var query = baseKeychainQuery()
        query[kSecAttrSynchronizable as String] = false
        return query
    }
    #endif
}

private actor ComposerRecoveryWriter {
    static let shared = ComposerRecoveryWriter()

    func load() -> ComposerRecoverySnapshot? {
        ComposerRecoveryStore.load()
    }

    func save(_ snapshot: ComposerRecoverySnapshot) -> Bool {
        ComposerRecoveryStore.save(snapshot)
    }

    func clear() {
        ComposerRecoveryStore.clear()
    }

    func completeDestructivePurge() {
        ComposerRecoveryStore.completeDestructivePurge()
    }
}

private struct MailComposerOverlay: View {
    let presentation: MailComposerPresentation
    let recoverySnapshot: ComposerRecoverySnapshot?
    @ObservedObject var store: InboxStore
    let colorScheme: ColorScheme
    let onReauthorizeGoogle: () async throws -> Void
    let onClose: (ComposerRecoverySnapshot?) -> Void

    var body: some View {
        GeometryReader { proxy in
            let sheetWidth = min(626, max(0, proxy.size.width - 48))
            let sheetHeight = min(580, max(0, proxy.size.height - 48))

            ZStack {
                Button {
                    NotificationCenter.default.post(name: .electronicMailComposerCloseRequested, object: nil)
                } label: {
                    Rectangle()
                        .fill(Color.black.opacity(colorScheme == .dark ? 0.46 : 0.28))
                        .ignoresSafeArea()
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Close composer")

                MailComposerSheet(
                    presentation: presentation,
                    recoverySnapshot: recoverySnapshot,
                    store: store,
                    colorScheme: colorScheme,
                    onReauthorizeGoogle: onReauthorizeGoogle,
                    onClose: onClose
                )
                .frame(width: sheetWidth, height: sheetHeight)
                .background(ElectronicMailDesign.composerSurface(for: colorScheme))
                .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: 14, style: .continuous)
                        .stroke(ElectronicMailDesign.panelBorder(for: colorScheme), lineWidth: 1)
                )
                .shadow(color: Color.black.opacity(colorScheme == .dark ? 0.52 : 0.18), radius: 26, x: 0, y: 12)
                .accessibilityElement(children: .contain)
                .accessibilityLabel("Message composer")
            }
            .frame(width: proxy.size.width, height: proxy.size.height)
            .background(
                ComposerKeyboardCapture {
                    NotificationCenter.default.post(name: .electronicMailComposerCloseRequested, object: nil)
                }
                .frame(width: 1, height: 1)
                .opacity(0.01)
            )
        }
    }
}

private enum ComposerFocusField: Hashable {
    case to
    case cc
    case bcc
    case subject
    case body
}

private struct MailComposerSheet: View {
    let presentation: MailComposerPresentation
    let recoverySnapshot: ComposerRecoverySnapshot?
    @ObservedObject var store: InboxStore
    let colorScheme: ColorScheme
    let onReauthorizeGoogle: () async throws -> Void
    let onClose: (ComposerRecoverySnapshot?) -> Void

    @State private var activeMode: MailComposerMode
    @State private var toText = ""
    @State private var ccText = ""
    @State private var bccText = ""
    @State private var subject = ""
    @State private var bodyText = ""
    @State private var statusText: String?
    @State private var sending = false
    @State private var savingDraft = false
    @State private var loadingDraft = false
    @State private var draftLoadError: String?
    @State private var reauthorizing = false
    @State private var didLoadInitialValues = false
    @State private var clientSendID = UUID().uuidString
    @State private var serverSendID: String?
    @State private var unresolvedSendAttempt = false
    @State private var clientDraftID = UUID().uuidString
    @State private var gmailDraftID: String?
    @State private var gmailThreadID: String?
    @State private var attachments: [ComposerAttachment] = []
    @State private var existingDraftAttachments: [MailDraftAttachment] = []
    @State private var preserveExistingDraftAttachments = true
    @State private var sourceAttachmentCount = 0
    @State private var includeOriginalAttachments = false
    @State private var draftForkWarning: String?
    @State private var autosaveTask: Task<Void, Never>?
    @State private var attachmentLoadTask: Task<Void, Never>?
    @State private var sendTask: Task<Void, Never>?
    @State private var loadingAttachments = false
    @State private var responseFieldProvenance = MailComposerResponseFieldProvenance()
    @State private var autosaveRevision: UInt64 = 0
    @State private var draftChangeTracker = MailComposerDraftChangeTracker()
    @State private var confirmSendWithoutSubject = false
    @State private var confirmDeleteDraft = false
    @State private var initialResponseFingerprint: String?
    @State private var restoredFromRecovery = false
    @State private var composerResolved = false
    @State private var shutdownRegistrationID = UUID()
    @FocusState private var focusedField: ComposerFocusField?

    init(
        presentation: MailComposerPresentation,
        recoverySnapshot: ComposerRecoverySnapshot?,
        store: InboxStore,
        colorScheme: ColorScheme,
        onReauthorizeGoogle: @escaping () async throws -> Void,
        onClose: @escaping (ComposerRecoverySnapshot?) -> Void
    ) {
        self.presentation = presentation
        self.recoverySnapshot = recoverySnapshot
        self._store = ObservedObject(wrappedValue: store)
        self.colorScheme = colorScheme
        self.onReauthorizeGoogle = onReauthorizeGoogle
        self.onClose = onClose
        self._activeMode = State(initialValue: presentation.mode)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            composerHeader

            Rectangle()
                .fill(ElectronicMailDesign.divider(for: colorScheme))
                .frame(height: 1)
                .padding(.top, 12)

            if loadingDraft {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text("Loading draft…")
                        .font(ElectronicMailComposerType.status())
                        .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                }
                .padding(.vertical, 8)
            }

            if let draftLoadError {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Draft could not be loaded")
                        .font(ElectronicMailComposerType.value())
                        .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                    Text(draftLoadError)
                        .font(ElectronicMailComposerType.status())
                        .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                        .lineLimit(2)
                    Button("Retry") {
                        Task {
                            await loadInitialValues()
                            if editorReady {
                                focusedField = initialFocusField
                            }
                        }
                    }
                    .buttonStyle(.plain)
                    .font(ElectronicMailComposerType.control(weight: .semibold))
                    .foregroundStyle(ElectronicMailDesign.appleBlue)
                    .disabled(loadingDraft)
                }
                .padding(.vertical, 8)
            }

            composerField(
                "To",
                placeholder: "Add recipients",
                text: $toText,
                focus: .to,
                usesTokenFill: true,
                responseField: .to
            )
            composerField(
                "Cc",
                placeholder: "Add recipients",
                text: $ccText,
                focus: .cc,
                usesTokenFill: true,
                responseField: .cc
            )
            composerField("Bcc", placeholder: "Add hidden recipients", text: $bccText, focus: .bcc, usesTokenFill: true)
            composerSubjectField

            if effectiveMode == .forward, sourceAttachmentCount > 0 {
                Toggle(
                    "Include \(sourceAttachmentCount) original attachment\(sourceAttachmentCount == 1 ? "" : "s")",
                    isOn: $includeOriginalAttachments
                )
                .toggleStyle(.checkbox)
                .font(ElectronicMailComposerType.status())
                .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                .padding(.vertical, 7)
                .disabled(composerControlsDisabled)
            }

            if !attachments.isEmpty || !existingDraftAttachments.isEmpty {
                VStack(alignment: .leading, spacing: 5) {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 7) {
                            ForEach(existingDraftAttachments) { attachment in
                                composerAttachmentChip(
                                    name: attachment.filename,
                                    detail: "Saved attachment",
                                    onRemove: {
                                        existingDraftAttachments.removeAll { $0.id == attachment.id }
                                        preserveExistingDraftAttachments = false
                                        statusText = "Attachment will be removed on the next save."
                                    }
                                )
                            }
                            ForEach(attachments) { attachment in
                                composerAttachmentChip(
                                    name: attachment.upload.filename,
                                    detail: ByteCountFormatter.string(
                                        fromByteCount: Int64(attachment.byteCount),
                                        countStyle: .file
                                    ),
                                    onRemove: { attachments.removeAll { $0.id == attachment.id } }
                                )
                            }
                        }
                    }
                    .frame(height: 38)

                    if !existingDraftAttachments.isEmpty {
                        Button("Remove saved attachments", role: .destructive) {
                            existingDraftAttachments = []
                            preserveExistingDraftAttachments = false
                            statusText = "Saved attachments will be removed on the next save."
                        }
                        .buttonStyle(.plain)
                        .font(ElectronicMailComposerType.status())
                        .foregroundStyle(Color.red)
                        .disabled(savingDraft || sending)
                    }
                }
                .padding(.vertical, 6)
            }

            composerEditor
                .padding(.top, 1)

            composerFooter
                .padding(.top, 10)
        }
        .padding(.horizontal, 14)
        .padding(.top, 16)
        .padding(.bottom, 10)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("\(effectiveMode.title) composer")
        .task {
            await loadInitialValues()
            guard editorReady else { return }
            focusedField = initialFocusField
        }
        .onAppear {
            ElectronicMailComposerShutdownCoordinator.shared.register(id: shutdownRegistrationID) {
                await prepareForShutdown()
            }
        }
        .onChange(of: toText) { _, _ in scheduleAutosave() }
        .onChange(of: ccText) { _, _ in scheduleAutosave() }
        .onChange(of: bccText) { _, _ in scheduleAutosave() }
        .onChange(of: subject) { _, _ in scheduleAutosave() }
        .onChange(of: bodyText) { _, _ in scheduleAutosave() }
        .onChange(of: attachments.map(\.id)) { _, _ in scheduleAutosave() }
        .onChange(of: existingDraftAttachments.map(\.id)) { _, _ in scheduleAutosave() }
        .onChange(of: preserveExistingDraftAttachments) { _, _ in scheduleAutosave() }
        .onChange(of: includeOriginalAttachments) { _, _ in scheduleAutosave() }
        .onChange(of: activeMode) { _, _ in scheduleAutosave() }
        .onDisappear {
            autosaveTask?.cancel()
            attachmentLoadTask?.cancel()
            sendTask?.cancel()
            sendTask = nil
            ElectronicMailComposerShutdownCoordinator.shared.unregister(id: shutdownRegistrationID)
            guard !composerResolved else { return }
            Task {
                _ = await persistRecoverySnapshotIfNeeded(force: unresolvedSendAttempt)
                guard !unresolvedSendAttempt, shouldPersistGmailDraft else { return }
                _ = await saveDraftIfNeeded(force: true)
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: .electronicMailComposerCloseRequested)) { _ in
            Task { await requestClose() }
        }
        .confirmationDialog(
            "Send without a subject?",
            isPresented: $confirmSendWithoutSubject,
            titleVisibility: .visible
        ) {
            Button("Send Anyway") { startSend(allowEmptySubject: true) }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("You can add a subject before sending, or send this email without one.")
        }
        .confirmationDialog(
            "Delete this draft?",
            isPresented: $confirmDeleteDraft,
            titleVisibility: .visible
        ) {
            Button("Delete Draft", role: .destructive) { Task { await deleteDraft() } }
            Button("Keep Draft", role: .cancel) {}
        } message: {
            Text("This removes the draft from Electronic Mail and Gmail.")
        }
    }

    private var composerHeader: some View {
        HStack(spacing: 10) {
            if isResponseComposer {
                Picker("Response type", selection: responseModeSelection) {
                    ForEach(MailComposerResponseTransitionPolicy.modes, id: \.rawValue) { mode in
                        Text(mode.title).tag(mode.rawValue)
                    }
                }
                .labelsHidden()
                .pickerStyle(.segmented)
                .font(ElectronicMailComposerType.mode())
                .frame(minWidth: 0, idealWidth: 264, maxWidth: 264, minHeight: 31, maxHeight: 31)
                .disabled(responseModeControlsDisabled)
                .transaction { transaction in
                    transaction.animation = nil
                }
                .accessibilityLabel("Response type")
                .help(
                    unresolvedSendAttempt
                        ? "Response type cannot change while delivery is being confirmed."
                        : "Response type"
                )
            } else {
                Text(effectiveMode.title)
                    .font(ElectronicMailComposerType.value())
                    .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                    .accessibilityAddTraits(.isHeader)
            }

            Spacer(minLength: 8)

            if gmailDraftID != nil {
                Menu {
                    Button("Delete Draft", role: .destructive) {
                        confirmDeleteDraft = true
                    }
                } label: {
                    Image(systemName: "ellipsis")
                        .frame(width: 32, height: 28)
                        .contentShape(Rectangle())
                }
                .menuStyle(.borderlessButton)
                .menuIndicator(.hidden)
                .help("Draft actions")
                .accessibilityLabel("Draft actions")
                .disabled(composerControlsDisabled || savingDraft || loadingDraft)
            }
        }
        .frame(height: 31)
    }

    private var composerEditor: some View {
        ZStack(alignment: .topLeading) {
            if bodyText.isEmpty {
                Text(editorPlaceholder)
                    .font(ElectronicMailComposerType.body())
                    .foregroundStyle(ElectronicMailDesign.tertiaryText(for: colorScheme))
                    // TextEditor adds five points of native line-fragment
                    // padding inside our seven-point horizontal inset. Match
                    // that insertion origin so the placeholder and caret share
                    // the same first baseline.
                    .padding(.horizontal, ElectronicMailComposerEditorLayout.placeholderHorizontalInset)
                    .padding(.vertical, ElectronicMailComposerEditorLayout.placeholderVerticalInset)
                    .allowsHitTesting(false)
                    .accessibilityHidden(true)
            }

            TextEditor(text: $bodyText)
                .focused($focusedField, equals: .body)
                .font(ElectronicMailComposerType.body())
                .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                .scrollContentBackground(.hidden)
                .padding(.horizontal, ElectronicMailComposerEditorLayout.textEditorHorizontalInset)
                .padding(.vertical, ElectronicMailComposerEditorLayout.textEditorVerticalInset)
                .accessibilityLabel("Message body")
        }
        .frame(minHeight: 150, maxHeight: .infinity)
        .background(
            RoundedRectangle(cornerRadius: 7, style: .continuous)
                .fill(ElectronicMailDesign.composerEditorSurface(for: colorScheme))
        )
        .overlay {
            RoundedRectangle(cornerRadius: 7, style: .continuous)
                .stroke(
                    focusedField == .body
                        ? ElectronicMailDesign.appleBlue
                        : ElectronicMailDesign.panelBorder(for: colorScheme),
                    lineWidth: focusedField == .body ? 1.25 : 1
                )
        }
        .disabled(composerControlsDisabled)
    }

    private var composerFooter: some View {
        HStack(spacing: 12) {
            Button {
                chooseAttachments()
            } label: {
                HStack(spacing: 5) {
                    if loadingAttachments {
                        ProgressView().controlSize(.small)
                    } else {
                        Image(systemName: "paperclip")
                    }
                    Text(loadingAttachments ? "Preparing" : "Attach")
                }
            }
            .buttonStyle(.plain)
            .font(ElectronicMailComposerType.control(weight: .semibold))
            .foregroundStyle(ElectronicMailDesign.appleBlue)
            .frame(minWidth: 68, minHeight: 36, alignment: .leading)
            .contentShape(Rectangle())
            .disabled(composerControlsDisabled || loadingAttachments)
            .accessibilityLabel(loadingAttachments ? "Preparing files" : "Attach files")

            if let footerStatusText {
                Text(footerStatusText)
                    .font(ElectronicMailComposerType.status())
                    .foregroundStyle(ElectronicMailDesign.tertiaryText(for: colorScheme))
                    .lineLimit(1)
                    .truncationMode(.tail)
                    .frame(maxWidth: 180, alignment: .leading)
                    .help(footerStatusText)
                    .accessibilityLabel(footerStatusText)
            }

            if statusText?.localizedCaseInsensitiveContains("permission") == true {
                Button {
                    Task { await reauthorize() }
                } label: {
                    if reauthorizing {
                        ProgressView().controlSize(.small)
                    } else {
                        Text("Grant permission")
                    }
                }
                .buttonStyle(.plain)
                .font(ElectronicMailComposerType.control(weight: .semibold))
                .foregroundStyle(ElectronicMailDesign.appleBlue)
                .disabled(reauthorizing)
            }

            Spacer(minLength: 8)

            Button("Cancel") {
                Task { await requestClose() }
            }
            .buttonStyle(.plain)
            .font(ElectronicMailComposerType.control(weight: .semibold))
            .foregroundStyle(ElectronicMailDesign.appleBlue)
            .frame(minWidth: 54, minHeight: 36)
            .contentShape(Rectangle())
            .disabled(sending)

            Button {
                startSend(allowEmptySubject: false)
            } label: {
                if sending {
                    ProgressView().controlSize(.small)
                } else {
                    Text("Send")
                }
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.regular)
            .font(ElectronicMailComposerType.control(weight: .semibold))
            .frame(minWidth: 60)
            .disabled(!canSend)
            .keyboardShortcut(.return, modifiers: [.command])
        }
        .frame(height: 40)
    }

    private var effectiveMode: MailComposerMode {
        isResponseComposer ? activeMode : presentation.mode
    }

    private var isResponseComposer: Bool {
        MailComposerResponseTransitionPolicy.isResponseMode(presentation.mode)
    }

    private var composerControlsDisabled: Bool {
        !editorReady || sending
    }

    private var responseModeControlsDisabled: Bool {
        composerControlsDisabled
            || unresolvedSendAttempt
    }

    private var footerStatusText: String? {
        if savingDraft {
            return "Saving…"
        }
        if let statusText, !statusText.isEmpty {
            return statusText
        }
        if gmailDraftID != nil {
            return "Draft saved"
        }
        return nil
    }

    private var editorPlaceholder: String {
        switch effectiveMode {
        case .reply, .replyAll:
            return "Write a reply…"
        case .compose, .draft, .forward:
            return "Write a message…"
        }
    }

    private var initialFocusField: ComposerFocusField {
        switch effectiveMode {
        case .reply, .replyAll:
            return .body
        case .compose, .forward:
            return .to
        case .draft:
            return parsedAddresses(toText).isEmpty ? .to : .body
        }
    }

    private var responseModeSelection: Binding<String> {
        Binding(
            get: { effectiveMode.rawValue },
            set: { rawValue in
                guard let mode = MailComposerMode(rawValue: rawValue) else { return }
                requestResponseModeChange(mode)
            }
        )
    }

    private var canSend: Bool {
        !sending
            && !savingDraft
            && !loadingDraft
            && editorReady
            && !parsedAddresses(toText).isEmpty
    }

    private var editorReady: Bool {
        presentation.mode != .draft || (didLoadInitialValues && draftLoadError == nil)
    }

    private var draftFingerprint: String {
        [
            effectiveMode.rawValue,
            responseFieldProvenance.userEditedFields.map(\.rawValue).sorted().joined(separator: ","),
            toText,
            ccText,
            bccText,
            subject,
            bodyText,
            attachments.map { "\($0.upload.filename):\($0.byteCount)" }.joined(separator: "|"),
            existingDraftAttachments.map(\.attachmentID).joined(separator: "|"),
            preserveExistingDraftAttachments ? "preserve" : "replace",
            includeOriginalAttachments ? "include-original" : "exclude-original",
        ]
            .joined(separator: "\u{1f}")
    }

    private var hasDraftContent: Bool {
        MailComposerPolicy.hasDraftContent(
            textFields: [toText, ccText, bccText, subject, bodyText],
            attachmentCount: attachments.count + existingDraftAttachments.count
        )
    }

    private var responseDraftChanged: Bool {
        guard MailComposerPolicy.responseMode(for: effectiveMode) != nil else {
            return false
        }
        return restoredFromRecovery
            || initialResponseFingerprint.map { draftFingerprint != $0 } == true
    }

    private var shouldPersistGmailDraft: Bool {
        MailComposerPolicy.shouldPersistDraft(
            mode: effectiveMode,
            hasContent: hasDraftContent,
            responseChanged: responseDraftChanged,
            hasGmailDraft: gmailDraftID?.isEmpty == false
        )
    }

    private func composerField(
        _ label: String,
        placeholder: String,
        text: Binding<String>,
        focus: ComposerFocusField,
        usesTokenFill: Bool = false,
        responseField: MailComposerResponseField? = nil
    ) -> some View {
        HStack(alignment: .center, spacing: 10) {
            Text(label)
                .font(ElectronicMailComposerType.label())
                .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                .lineLimit(1)
                .frame(width: 60, alignment: .leading)
            TextField(placeholder, text: composerFieldBinding(text, responseField: responseField))
                .textFieldStyle(.plain)
                .font(ElectronicMailComposerType.value())
                .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                .padding(.horizontal, usesTokenFill ? 9 : 0)
                .frame(minWidth: 0, maxWidth: .infinity, minHeight: 28, maxHeight: 28, alignment: .leading)
                .layoutPriority(1)
                .background(alignment: .leading) {
                    if usesTokenFill, !text.wrappedValue.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                        GeometryReader { proxy in
                            RoundedRectangle(cornerRadius: 6, style: .continuous)
                                .fill(ElectronicMailDesign.composerTokenFill(for: colorScheme))
                                .frame(
                                    width: min(
                                        recipientFieldWidth(text: text.wrappedValue, placeholder: placeholder),
                                        proxy.size.width
                                    )
                                )
                        }
                    }
                }
                .accessibilityLabel(label)
                .focused($focusedField, equals: focus)
                .onSubmit { advanceFocus(after: focus) }
        }
        .frame(height: 44)
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(ElectronicMailDesign.divider(for: colorScheme))
                .frame(height: 1)
        }
        .disabled(composerControlsDisabled)
    }

    private func composerFieldBinding(
        _ text: Binding<String>,
        responseField: MailComposerResponseField?
    ) -> Binding<String> {
        Binding(
            get: { text.wrappedValue },
            set: { value in
                let changed = value != text.wrappedValue
                text.wrappedValue = value
                if changed,
                   didLoadInitialValues,
                   isResponseComposer,
                   let responseField {
                    responseFieldProvenance.markUserEdited(responseField)
                }
            }
        )
    }

    private var composerSubjectField: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text("Subject")
                .font(ElectronicMailComposerType.label())
                .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                .lineLimit(1)
                .frame(width: 60, alignment: .leading)

            TextField("Subject", text: responsiveSubjectBinding, axis: .vertical)
                .textFieldStyle(.plain)
                .font(ElectronicMailComposerType.value())
                .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                .lineLimit(1...2)
                .frame(minWidth: 0, maxWidth: .infinity, minHeight: 28, alignment: .leading)
                .layoutPriority(1)
                .accessibilityLabel("Subject")
                .focused($focusedField, equals: .subject)
                .onSubmit { advanceFocus(after: .subject) }
        }
        .padding(.vertical, 8)
        .frame(minHeight: 44)
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(ElectronicMailDesign.divider(for: colorScheme))
                .frame(height: 1)
        }
        .disabled(composerControlsDisabled)
    }

    private var responsiveSubjectBinding: Binding<String> {
        Binding(
            get: { subject },
            set: { value in
                let sanitizedValue = MailComposerPolicy.sanitizedSubject(value)
                let changed = sanitizedValue != subject
                subject = sanitizedValue
                if changed, didLoadInitialValues, isResponseComposer {
                    responseFieldProvenance.markUserEdited(.subject)
                }
            }
        )
    }

    private func recipientFieldWidth(text: String, placeholder: String) -> CGFloat {
        let source = text.isEmpty ? placeholder : text
        return min(max(CGFloat(source.count) * 7.4 + 24, 150), 460)
    }

    private func advanceFocus(after field: ComposerFocusField) {
        switch field {
        case .to:
            focusedField = .cc
        case .cc:
            focusedField = .bcc
        case .bcc:
            focusedField = .subject
        case .subject:
            focusedField = .body
        case .body:
            break
        }
    }

    private func composerAttachmentChip(name: String, detail: String, onRemove: (() -> Void)?) -> some View {
        HStack(spacing: 6) {
            Image(systemName: "paperclip")
            VStack(alignment: .leading, spacing: 1) {
                Text(name).lineLimit(1)
                Text(detail)
                    .font(ElectronicMailComposerType.status())
                    .foregroundStyle(ElectronicMailDesign.tertiaryText(for: colorScheme))
            }
            if let onRemove {
                Button(action: onRemove) {
                    Image(systemName: "xmark.circle.fill")
                }
                .buttonStyle(.plain)
                .help("Remove \(name)")
                .disabled(savingDraft || sending)
            }
        }
        .font(ElectronicMailComposerType.control())
        .padding(.horizontal, 9)
        .padding(.vertical, 5)
        .background(
            RoundedRectangle(cornerRadius: 6, style: .continuous)
                .fill(ElectronicMailDesign.composerTokenFill(for: colorScheme))
        )
    }

    private struct ResponsePrefill {
        let toText: String
        let ccText: String
        let subject: String
        let gmailThreadID: String?
        let sourceAttachmentCount: Int
    }

    @MainActor
    private func requestResponseModeChange(_ mode: MailComposerMode) {
        guard isResponseComposer,
              MailComposerResponseTransitionPolicy.isResponseMode(mode),
              mode != effectiveMode,
              !responseModeControlsDisabled else {
            return
        }
        changeResponseMode(to: mode)
    }

    @MainActor
    private func changeResponseMode(to nextMode: MailComposerMode) {
        let previousMode = effectiveMode
        guard previousMode != nextMode,
              !unresolvedSendAttempt,
              let nextPrefill = responsePrefill(for: nextMode) else {
            return
        }

        // A response-mode selection is a local presentation change. Provider
        // draft reconciliation is deliberately left to the debounced autosave
        // path so Keychain, disk, and network latency never block this control.
        autosaveTask?.cancel()
        autosaveTask = nil

        withTransaction(Transaction(animation: nil)) {
            activeMode = nextMode
            gmailThreadID = nextPrefill.gmailThreadID
            sourceAttachmentCount = nextPrefill.sourceAttachmentCount
            toText = responseFieldProvenance.transitionedValue(
                for: .to,
                current: toText,
                nextDefault: nextPrefill.toText
            )
            ccText = responseFieldProvenance.transitionedValue(
                for: .cc,
                current: ccText,
                nextDefault: nextPrefill.ccText
            )
            subject = responseFieldProvenance.transitionedValue(
                for: .subject,
                current: subject,
                nextDefault: nextPrefill.subject
            )
            subject = MailComposerPolicy.sanitizedSubject(subject)
        }

        statusText = "Changed to \(nextMode.title)."
        scheduleAutosave()
        let focusAfterTransition: ComposerFocusField =
            nextMode == .forward && parsedAddresses(toText).isEmpty ? .to : .body
        Task { @MainActor in
            await Task.yield()
            guard !composerResolved, effectiveMode == nextMode else { return }
            focusedField = focusAfterTransition
        }
    }

    @MainActor
    private func responsePrefill(for mode: MailComposerMode) -> ResponsePrefill? {
        let messages = store.readerThread?.messages ?? []
        guard let message = messages.first(where: { $0.id == presentation.sourceMessageID })
            ?? messages.max(by: { $0.receivedAt < $1.receivedAt }) else {
            return nil
        }
        let recipients = MailReplyPrefillPolicy.recipients(
            mode: mode,
            currentUser: store.session?.user.email,
            senderHeader: message.replyTo ?? message.fromAddress,
            originalToHeader: message.to,
            originalCCHeader: message.cc
        )
        let sourceSubject = MailComposerPolicy.sanitizedSubject(message.subject ?? presentation.title)
        switch mode {
        case .reply, .replyAll:
            return ResponsePrefill(
                toText: recipients.to.joined(separator: ", "),
                ccText: recipients.cc.joined(separator: ", "),
                subject: replySubject(sourceSubject),
                gmailThreadID: message.threadID ?? store.readerThread?.gmailThreadID,
                sourceAttachmentCount: message.attachments.count
            )
        case .forward:
            return ResponsePrefill(
                toText: "",
                ccText: "",
                subject: forwardSubject(sourceSubject),
                gmailThreadID: message.threadID ?? store.readerThread?.gmailThreadID,
                sourceAttachmentCount: message.attachments.count
            )
        case .compose, .draft:
            return nil
        }
    }

    @MainActor
    private func loadInitialValues() async {
        guard !loadingDraft else { return }
        if let recovery = recoverySnapshot,
           recovery.matches(presentation, accountUserID: store.session?.user.id) {
            apply(recovery)
            draftChangeTracker.synchronize(fingerprint: draftFingerprint)
            didLoadInitialValues = true
            restoredFromRecovery = true
            statusText = "Recovered your unsent message."
            return
        }
        switch effectiveMode {
        case .compose:
            draftChangeTracker.synchronize(fingerprint: draftFingerprint)
            didLoadInitialValues = true
            return
        case .draft:
            guard let threadID = presentation.threadID else {
                draftLoadError = "The draft could not be found. Refresh Drafts and try again."
                didLoadInitialValues = false
                return
            }
            loadingDraft = true
            draftLoadError = nil
            defer { loadingDraft = false }
            do {
                let draft = try await store.draft(mailboxThreadID: threadID)
                clientDraftID = draft.clientDraftID
                gmailDraftID = draft.gmailDraftID
                gmailThreadID = draft.gmailThreadID
                toText = draft.to.joined(separator: ", ")
                ccText = draft.cc.joined(separator: ", ")
                bccText = draft.bcc.joined(separator: ", ")
                subject = MailComposerPolicy.sanitizedSubject(draft.subject)
                bodyText = draft.bodyText
                existingDraftAttachments = draft.attachments
                statusText = "Draft loaded."
                draftChangeTracker.synchronize(fingerprint: draftFingerprint)
                didLoadInitialValues = true
            } catch {
                draftLoadError = error.localizedDescription
                statusText = nil
                didLoadInitialValues = false
            }
        case .reply, .replyAll, .forward:
            prefillResponse(for: effectiveMode)
            initialResponseFingerprint = draftFingerprint
            draftChangeTracker.synchronize(fingerprint: draftFingerprint)
            didLoadInitialValues = true
        }
    }

    @MainActor
    private func prefillResponse(for mode: MailComposerMode) {
        guard let prefill = responsePrefill(for: mode) else {
            statusText = "Email content is still loading."
            return
        }
        responseFieldProvenance = MailComposerResponseFieldProvenance()
        gmailThreadID = prefill.gmailThreadID
        sourceAttachmentCount = prefill.sourceAttachmentCount
        toText = prefill.toText
        ccText = prefill.ccText
        subject = MailComposerPolicy.sanitizedSubject(prefill.subject)
    }

    @MainActor
    private func apply(_ recovery: ComposerRecoverySnapshot) {
        activeMode = recovery.mode
        toText = recovery.toText
        ccText = recovery.ccText
        bccText = recovery.bccText
        subject = MailComposerPolicy.sanitizedSubject(recovery.subject)
        bodyText = recovery.bodyText
        clientSendID = recovery.clientSendID
        serverSendID = recovery.serverSendID
        unresolvedSendAttempt = recovery.unresolvedSendAttempt ?? false
        clientDraftID = recovery.clientDraftID
        gmailDraftID = recovery.gmailDraftID
        gmailThreadID = recovery.gmailThreadID
        attachments = recovery.attachments
        existingDraftAttachments = recovery.existingDraftAttachments
        preserveExistingDraftAttachments = recovery.preserveExistingDraftAttachments
        sourceAttachmentCount = recovery.sourceAttachmentCount
        includeOriginalAttachments = recovery.includeOriginalAttachments
        if MailComposerResponseTransitionPolicy.isResponseMode(recovery.mode) {
            responseFieldProvenance = recovery.responseFieldProvenance
                ?? MailComposerResponseFieldProvenance(userEditedFields: Set(MailComposerResponseField.allCases))
        } else {
            responseFieldProvenance = MailComposerResponseFieldProvenance()
        }
    }

    @MainActor
    @discardableResult
    private func persistRecoverySnapshotIfNeeded(force: Bool = false) async -> Bool {
        guard didLoadInitialValues else {
            return presentation.mode == .draft && !didLoadInitialValues
        }
        guard hasDraftContent || gmailDraftID?.isEmpty == false || unresolvedSendAttempt else {
            await ComposerRecoveryWriter.shared.clear()
            return true
        }
        if MailComposerPolicy.shouldClearUnchangedResponseRecovery(
            force: force,
            hasUnresolvedSendAttempt: unresolvedSendAttempt,
            mode: effectiveMode,
            restoredFromRecovery: restoredFromRecovery,
            responseIsUnchanged: !responseDraftChanged
        ) {
            await ComposerRecoveryWriter.shared.clear()
            return true
        }
        guard let snapshot = currentRecoverySnapshot() else {
            return false
        }
        return await ComposerRecoveryWriter.shared.save(snapshot)
    }

    @MainActor
    private func currentRecoverySnapshot() -> ComposerRecoverySnapshot? {
        guard let accountUserID = store.session?.user.id else {
            return nil
        }
        return ComposerRecoverySnapshot(
            accountUserID: accountUserID,
            mode: effectiveMode,
            threadID: presentation.threadID,
            sourceMessageID: presentation.sourceMessageID,
            title: presentation.title,
            toText: toText,
            ccText: ccText,
            bccText: bccText,
            subject: MailComposerPolicy.sanitizedSubject(subject),
            bodyText: bodyText,
            clientSendID: clientSendID,
            serverSendID: serverSendID,
            unresolvedSendAttempt: unresolvedSendAttempt,
            clientDraftID: clientDraftID,
            gmailDraftID: gmailDraftID,
            gmailThreadID: gmailThreadID,
            attachments: attachments,
            existingDraftAttachments: existingDraftAttachments,
            preserveExistingDraftAttachments: preserveExistingDraftAttachments,
            sourceAttachmentCount: sourceAttachmentCount,
            includeOriginalAttachments: includeOriginalAttachments,
            responseFieldProvenance: isResponseComposer ? responseFieldProvenance : nil
        )
    }

    private func scheduleAutosave(rearm: Bool = false) {
        guard didLoadInitialValues, !sending else {
            return
        }
        if rearm {
            draftChangeTracker.synchronize(fingerprint: draftFingerprint)
        } else {
            guard draftChangeTracker.shouldHandleChange(fingerprint: draftFingerprint) else {
                return
            }
        }
        let contentChangeDecision = MailComposerPolicy.contentChangeDecision(
            hasUnresolvedSendAttempt: unresolvedSendAttempt,
            existingDraftAttachmentCount: existingDraftAttachments.count
        )
        if case .forkDraftForNewSendAttempt(let discardExistingDraftAttachments) = contentChangeDecision {
            let discardedAttachmentCount = existingDraftAttachments.count
            unresolvedSendAttempt = false
            serverSendID = nil
            clientDraftID = UUID().uuidString
            clientSendID = UUID().uuidString
            gmailDraftID = nil
            // Keep gmailThreadID so the replacement reply/draft stays in the
            // same conversation without reusing the ambiguous provider draft.
            existingDraftAttachments = []
            preserveExistingDraftAttachments = false
            if discardExistingDraftAttachments {
                let noun = discardedAttachmentCount == 1 ? "attachment" : "attachments"
                draftForkWarning = "Message changed. A new draft was created; \(discardedAttachmentCount) saved \(noun) could not be copied and must be attached again."
                statusText = draftForkWarning
            } else {
                statusText = "Message changed. A new draft and delivery attempt were created."
            }
            // The fork mutates attachment-backed fingerprint fields internally;
            // keep those changes from being mistaken for another user edit.
            draftChangeTracker.synchronize(fingerprint: draftFingerprint)
        }
        autosaveRevision &+= 1
        let revision = autosaveRevision
        autosaveTask?.cancel()
        autosaveTask = Task {
            try? await Task.sleep(nanoseconds: 800_000_000)
            guard !Task.isCancelled, revision == autosaveRevision else { return }
            _ = await persistRecoverySnapshotIfNeeded()
            guard !Task.isCancelled, revision == autosaveRevision else { return }

            // Mode changes remain interactive while an older provider save is
            // winding down. Serialize the next save here and coalesce rapid
            // selections to the newest revision instead of blocking the UI.
            while savingDraft {
                try? await Task.sleep(nanoseconds: 25_000_000)
                guard !Task.isCancelled, revision == autosaveRevision else { return }
            }
            _ = await saveDraftIfNeeded(force: false, expectedRevision: revision)
        }
    }

    @MainActor
    @discardableResult
    private func saveDraftIfNeeded(force: Bool, expectedRevision: UInt64? = nil) async -> MailDraftResponse? {
        guard force || shouldPersistGmailDraft else { return nil }
        savingDraft = true
        if !sending { statusText = "Saving draft..." }
        defer { savingDraft = false }
        do {
            let submittedAttachmentIDs = Set(attachments.map(\.id))
            let responseMode = MailComposerPolicy.responseMode(for: effectiveMode)
            let response = try await store.saveDraft(
                MailDraftSaveRequest(
                    clientDraftID: clientDraftID,
                    gmailDraftID: gmailDraftID,
                    gmailThreadID: gmailThreadID,
                    to: parsedAddresses(toText),
                    cc: parsedAddresses(ccText),
                    bcc: parsedAddresses(bccText),
                    subject: MailComposerPolicy.sanitizedSubject(subject),
                    bodyText: bodyText,
                    bodyHTML: nil,
                    attachments: draftAttachmentPayload,
                    retainedAttachmentIDs: retainedDraftAttachmentIDs,
                    responseMode: responseMode,
                    mailboxThreadID: responseMode == nil ? nil : presentation.threadID,
                    sourceMessageID: responseMode == nil ? nil : presentation.sourceMessageID,
                    includeQuotedOriginal: true,
                    includeOriginalAttachments: responseMode == .forward && includeOriginalAttachments,
                    createdAt: ISO8601DateFormatter().string(from: Date())
                )
            )
            guard MailComposerPolicy.shouldApplyDraftSaveResponse(state: response.state) else {
                if !sending {
                    statusText = response.error ?? "Draft save failed."
                }
                return response
            }
            gmailDraftID = response.gmailDraftID ?? gmailDraftID
            gmailThreadID = response.gmailThreadID ?? gmailThreadID
            if let expectedRevision, expectedRevision != autosaveRevision {
                return response
            }
            existingDraftAttachments = response.attachments
            attachments.removeAll { submittedAttachmentIDs.contains($0.id) }
            preserveExistingDraftAttachments = true
            if unresolvedSendAttempt {
                _ = await persistRecoverySnapshotIfNeeded(force: true)
            } else {
                await ComposerRecoveryWriter.shared.clear()
            }
            if !sending { statusText = draftForkWarning ?? "Draft saved." }
            return response
        } catch is CancellationError {
            return nil
        } catch {
            guard !Task.isCancelled else { return nil }
            statusText = "Draft was not saved: \(error.localizedDescription)"
            return nil
        }
    }

    @MainActor
    private func startSend(allowEmptySubject: Bool) {
        guard sendTask == nil else { return }
        sendTask = Task { @MainActor in
            await send(allowEmptySubject: allowEmptySubject)
            sendTask = nil
        }
    }

    @MainActor
    private func send(allowEmptySubject: Bool) async {
        let invalid = (parsedAddresses(toText) + parsedAddresses(ccText) + parsedAddresses(bccText)).filter { !isValidEmail($0) }
        guard invalid.isEmpty else {
            statusText = "Check this email address: \(invalid[0])"
            return
        }
        if MailComposerPolicy.requiresEmptySubjectConfirmation(subject), !allowEmptySubject {
            confirmSendWithoutSubject = true
            return
        }
        autosaveTask?.cancel()
        autosaveTask = nil
        let wasUnresolved = unresolvedSendAttempt
        unresolvedSendAttempt = true
        sending = true
        statusText = "Sending..."
        defer { sending = false }

        guard await persistRecoverySnapshotIfNeeded(force: true) else {
            unresolvedSendAttempt = wasUnresolved
            statusText = "Your message could not be preserved. Keep this window open and try again."
            return
        }

        do {
            if let serverSendID {
                let response = try await store.retrySend(serverSendID: serverSendID)
                await handle(response, expectedServerSendID: serverSendID)
                return
            }

            if MailComposerPolicy.responseMode(for: effectiveMode) != nil,
               presentation.threadID == nil {
                statusText = "Email thread not found."
                return
            }

            let draftSendPreparation = MailComposerPolicy.draftSendPreparation(
                hasUnchangedUnresolvedSendAttempt: wasUnresolved,
                gmailDraftID: gmailDraftID,
                clientSendID: clientSendID
            )
            if case .retryExistingDraft(let draftID, let retryClientSendID) = draftSendPreparation {
                let response = try await store.sendDraft(
                    gmailDraftID: draftID,
                    clientDraftID: clientDraftID,
                    clientSendID: retryClientSendID
                )
                await handle(response)
                return
            }

            guard let draft = await saveDraftIfNeeded(force: true),
                  draft.state == .saved,
                  let draftID = draft.gmailDraftID ?? gmailDraftID else {
                if statusText == "Sending..." { statusText = "Draft must be saved before sending." }
                return
            }
            let response = try await store.sendDraft(
                gmailDraftID: draftID,
                clientDraftID: clientDraftID,
                clientSendID: clientSendID
            )
            await handle(response)
        } catch is CancellationError {
            return
        } catch {
            guard !Task.isCancelled else { return }
            statusText = "Send failed: \(error.localizedDescription). You can retry safely."
        }
    }

    @MainActor
    private func deleteDraft() async {
        guard !savingDraft,
              !loadingDraft,
              !sending,
              let gmailDraftID else {
            return
        }
        do {
            try await store.deleteDraft(gmailDraftID: gmailDraftID)
            finishComposer()
        } catch {
            statusText = "Could not delete draft: \(error.localizedDescription)"
        }
    }

    @MainActor
    private func chooseAttachments() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.allowsMultipleSelection = true
        panel.prompt = "Attach"
        guard panel.runModal() == .OK else { return }
        let urls = panel.urls
        let existingByteCount = attachments.reduce(0) { $0 + $1.byteCount }
        attachmentLoadTask?.cancel()
        loadingAttachments = true
        statusText = "Preparing attachments…"
        attachmentLoadTask = Task {
            let work = Task.detached(priority: .userInitiated) {
                ComposerAttachmentLoader.load(urls: urls, existingByteCount: existingByteCount)
            }
            let results = await withTaskCancellationHandler {
                await work.value
            } onCancel: {
                work.cancel()
            }
            guard !Task.isCancelled else {
                loadingAttachments = false
                return
            }

            var loadedCount = 0
            var lastMessage: String?
            for result in results {
                switch result {
                case .attachment(let attachment):
                    attachments.append(attachment)
                    loadedCount += 1
                case .warning(let message):
                    lastMessage = message
                }
            }
            loadingAttachments = false
            statusText = lastMessage ?? (loadedCount == 0 ? nil : "Attached \(loadedCount) file\(loadedCount == 1 ? "" : "s").")
        }
    }

    @MainActor
    private func reauthorize() async {
        reauthorizing = true
        do {
            try await onReauthorizeGoogle()
            statusText = "Permission granted. Send again."
        } catch {
            statusText = error.localizedDescription
        }
        reauthorizing = false
    }

    @MainActor
    private func requestClose() async {
        autosaveTask?.cancel()
        guard !sending else {
            statusText = "Waiting for delivery confirmation..."
            return
        }

        let recoveryPersisted = await persistRecoverySnapshotIfNeeded(force: unresolvedSendAttempt)
        guard recoveryPersisted else {
            statusText = "Your message could not be preserved. Keep this window open and try again."
            return
        }
        if unresolvedSendAttempt {
            guard let recovery = currentRecoverySnapshot() else {
                statusText = "Your message could not be preserved. Keep this window open and try again."
                return
            }
            composerResolved = true
            onClose(recovery)
            return
        }

        let requiresGmailDraftSave = shouldPersistGmailDraft
        let gmailDraftSaveState = requiresGmailDraftSave
            ? await saveDraftIfNeeded(force: true)?.state
            : nil
        switch MailComposerPolicy.closeDecision(
            recoveryPersisted: recoveryPersisted,
            requiresGmailDraftSave: requiresGmailDraftSave,
            gmailDraftSaveState: gmailDraftSaveState
        ) {
        case .block:
            statusText = "Your message could not be preserved. Keep this window open and try again."
        case .finishAndClearRecovery:
            finishComposer()
        case .finishPreservingRecovery:
            guard let recovery = currentRecoverySnapshot() else {
                statusText = "Your message could not be preserved. Keep this window open and try again."
                return
            }
            composerResolved = true
            onClose(recovery)
        }
    }

    @MainActor
    private func prepareForShutdown() async -> Bool {
        autosaveTask?.cancel()
        let recoveryPersisted = await persistRecoverySnapshotIfNeeded(force: unresolvedSendAttempt)
        guard recoveryPersisted else {
            statusText = "Your unsent message could not be preserved. Keep the app open and try again."
            return false
        }
        if unresolvedSendAttempt {
            composerResolved = true
            return true
        }
        guard editorReady else {
            composerResolved = true
            return true
        }

        let requiresGmailDraftSave = shouldPersistGmailDraft
        let gmailDraftSaveState = requiresGmailDraftSave
            ? await saveDraftIfNeeded(force: true)?.state
            : nil
        switch MailComposerPolicy.shutdownDecision(
            recoveryPersisted: recoveryPersisted,
            requiresGmailDraftSave: requiresGmailDraftSave,
            gmailDraftSaveState: gmailDraftSaveState
        ) {
        case .block:
            statusText = "Your unsent message could not be preserved. Keep the app open and try again."
            return false
        case .finishAndClearRecovery:
            ComposerRecoveryStore.clear()
        case .finishPreservingRecovery:
            break
        }
        composerResolved = true
        return true
    }

    @MainActor
    private func finishComposer() {
        unresolvedSendAttempt = false
        serverSendID = nil
        composerResolved = true
        ComposerRecoveryStore.clear()
        onClose(nil)
    }

    @MainActor
    private func handle(
        _ response: MailSendResponse,
        expectedServerSendID: String? = nil
    ) async {
        guard DurableSendConfirmationPolicy.matches(
            response,
            expectedClientSendID: clientSendID,
            expectedServerSendID: expectedServerSendID
        ) else {
            await preserveUnconfirmedSend(
                message: "Delivery confirmation did not match this message. Your content is preserved; retry safely."
            )
            return
        }

        switch DurableSendConfirmationPolicy.decision(for: response) {
        case .confirmedSent:
            unresolvedSendAttempt = false
            serverSendID = nil
            finishComposer()
        case .poll(let serverSendID):
            self.serverSendID = serverSendID
            unresolvedSendAttempt = true
            statusText = response.state == .sending
                ? "Sending. Confirming delivery..."
                : "Queued to send. Confirming delivery..."
            guard await persistRecoverySnapshotIfNeeded(force: true) else {
                statusText = "Delivery is pending, but recovery could not be updated. Keep this window open."
                return
            }
            await pollForSendConfirmation(serverSendID: serverSendID)
        case .definiteFailure(let message):
            // The backend only emits `failed` once Gmail conclusively rejected
            // the send. The saved provider draft is still valid, so keep its
            // IDs and attachments and stop treating edits as an ambiguous-send
            // fork. A retry may safely use the same draft.
            unresolvedSendAttempt = false
            serverSendID = nil
            statusText = message
            _ = await persistRecoverySnapshotIfNeeded(force: true)
        case .preserveForRetry(let message):
            if let responseServerSendID = response.serverSendID, !responseServerSendID.isEmpty {
                serverSendID = responseServerSendID
            }
            await preserveUnconfirmedSend(message: message)
        }
    }

    @MainActor
    private func pollForSendConfirmation(serverSendID: String) async {
        var confirmationFailed = false

        for delay in DurableSendConfirmationPolicy.pollDelayNanoseconds {
            do {
                try await Task.sleep(nanoseconds: delay)
            } catch is CancellationError {
                return
            } catch {
                return
            }
            guard !Task.isCancelled else { return }

            do {
                let response = try await store.sendStatus(serverSendID: serverSendID)
                guard DurableSendConfirmationPolicy.matches(
                    response,
                    expectedClientSendID: clientSendID,
                    expectedServerSendID: serverSendID
                ) else {
                    await preserveUnconfirmedSend(
                        message: "Delivery confirmation did not match this message. Your content is preserved; retry safely."
                    )
                    return
                }

                switch DurableSendConfirmationPolicy.decision(for: response) {
                case .confirmedSent:
                    unresolvedSendAttempt = false
                    self.serverSendID = nil
                    finishComposer()
                    return
                case .poll:
                    statusText = response.state == .sending
                        ? "Sending. Confirming delivery..."
                        : "Queued to send. Confirming delivery..."
                case .definiteFailure(let message):
                    unresolvedSendAttempt = false
                    self.serverSendID = nil
                    statusText = message
                    _ = await persistRecoverySnapshotIfNeeded(force: true)
                    return
                case .preserveForRetry(let message):
                    await preserveUnconfirmedSend(message: message)
                    return
                }
            } catch is CancellationError {
                return
            } catch {
                guard !Task.isCancelled else { return }
                confirmationFailed = true
                if !store.hasSessionToken {
                    await preserveUnconfirmedSend(
                        message: "Sign in again to confirm delivery. Your message is preserved."
                    )
                    return
                }
            }
        }

        let message = confirmationFailed
            ? "Delivery confirmation is temporarily unavailable. Your message is preserved; retry safely."
            : "Delivery has not been confirmed yet. Your message is preserved; retry safely."
        await preserveUnconfirmedSend(message: message)
    }

    @MainActor
    private func preserveUnconfirmedSend(message: String) async {
        unresolvedSendAttempt = true
        statusText = message
        _ = await persistRecoverySnapshotIfNeeded(force: true)
    }

    @MainActor
    private func handle(_ response: MailDraftResponse) {
        switch response.state {
        case .sent:
            finishComposer()
        case .reauthRequired:
            statusText = response.error ?? "Google needs permission to send mail."
        case .failed:
            statusText = response.error ?? "Send failed. You can retry safely."
        case .saved:
            statusText = "Draft saved."
        case .deleted:
            finishComposer()
        }
    }

    private func parsedAddresses(_ value: String) -> [String] {
        MailAddressParser.addresses(in: value)
    }

    private var draftAttachmentPayload: [MailAttachmentUpload]? {
        if !attachments.isEmpty {
            return attachments.map(\.upload)
        }
        return !preserveExistingDraftAttachments && existingDraftAttachments.isEmpty ? [] : nil
    }

    private var retainedDraftAttachmentIDs: [String]? {
        if preserveExistingDraftAttachments && attachments.isEmpty {
            return nil
        }
        return existingDraftAttachments.map(\.attachmentID)
    }

    private func normalizedAddress(_ value: String?) -> String? {
        MailAddressParser.firstAddress(in: value)
    }

    private func isValidEmail(_ value: String) -> Bool {
        value.range(of: #"^[^\s@]+@[^\s@]+\.[^\s@]+$"#, options: .regularExpression) != nil
    }

    private func replySubject(_ value: String) -> String {
        value.lowercased().hasPrefix("re:") ? value : "Re: \(value)"
    }

    private func forwardSubject(_ value: String) -> String {
        value.lowercased().hasPrefix("fwd:") ? value : "Fwd: \(value)"
    }

}

private struct ComposerAttachment: Identifiable, Codable, Equatable, @unchecked Sendable {
    let id: UUID
    let upload: MailAttachmentUpload
    let byteCount: Int

    init(id: UUID = UUID(), upload: MailAttachmentUpload, byteCount: Int) {
        self.id = id
        self.upload = upload
        self.byteCount = byteCount
    }
}

private enum ComposerAttachmentLoadResult: @unchecked Sendable {
    case attachment(ComposerAttachment)
    case warning(String)
}

private enum ComposerAttachmentLoader {
    static func load(urls: [URL], existingByteCount: Int) -> [ComposerAttachmentLoadResult] {
        var results: [ComposerAttachmentLoadResult] = []
        var totalByteCount = existingByteCount

        for url in urls {
            guard !Task.isCancelled else { break }
            let accessed = url.startAccessingSecurityScopedResource()
            let result: ComposerAttachmentLoadResult
            do {
                defer {
                    if accessed { url.stopAccessingSecurityScopedResource() }
                }
                if let fileSize = try url.resourceValues(forKeys: [.fileSizeKey]).fileSize,
                   fileSize > MailComposerPolicy.maximumAttachmentBytes {
                    result = .warning("\(url.lastPathComponent) is larger than the 10 MB per-file limit.")
                } else {
                    let data = try Data(contentsOf: url, options: .mappedIfSafe)
                    guard data.count <= MailComposerPolicy.maximumAttachmentBytes else {
                        results.append(.warning("\(url.lastPathComponent) is larger than the 10 MB per-file limit."))
                        continue
                    }
                    guard totalByteCount + data.count <= MailComposerPolicy.maximumTotalAttachmentBytes else {
                        results.append(.warning("Attachments must be 18 MB or less in total."))
                        break
                    }
                    guard !Task.isCancelled else { break }
                    let mimeType = UTType(filenameExtension: url.pathExtension)?.preferredMIMEType
                        ?? "application/octet-stream"
                    result = .attachment(
                        ComposerAttachment(
                            upload: MailAttachmentUpload(
                                filename: url.lastPathComponent,
                                mimeType: mimeType,
                                dataBase64: data.base64EncodedString()
                            ),
                            byteCount: data.count
                        )
                    )
                    totalByteCount += data.count
                }
            } catch {
                result = .warning("Could not attach \(url.lastPathComponent): \(error.localizedDescription)")
            }
            results.append(result)
        }

        return results
    }
}

private extension MailComposerMode {
    var title: String {
        switch self {
        case .compose: return "Compose"
        case .reply: return "Reply"
        case .replyAll: return "Reply All"
        case .forward: return "Forward"
        case .draft: return "Edit Draft"
        }
    }

}

private struct ComposerKeyboardCapture: NSViewRepresentable {
    let onClose: () -> Void

    func makeNSView(context: Context) -> EscapeView {
        let view = EscapeView()
        view.onClose = onClose
        view.installMonitorIfNeeded()
        return view
    }

    func updateNSView(_ nsView: EscapeView, context: Context) {
        nsView.onClose = onClose
        nsView.installMonitorIfNeeded()
    }

    final class EscapeView: NSView {
        var onClose: (() -> Void)?
        private var monitor: Any?

        deinit {
            if let monitor {
                NSEvent.removeMonitor(monitor)
            }
        }

        func installMonitorIfNeeded() {
            guard monitor == nil else {
                return
            }

            monitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
                guard let self,
                      event.keyCode == 53,
                      event.window === self.window,
                      self.window?.isKeyWindow == true else {
                    return event
                }
                self.onClose?()
                return nil
            }
        }
    }
}

private struct ShellAccountCommandHandler: View {
    let onSignOut: () async throws -> Void
    let onDisconnectGoogle: () async throws -> Void
    let onDeleteAccount: () async throws -> Void

    @State private var confirmDisconnect = false
    @State private var confirmDeleteAccount = false
    @State private var deleteAccountConfirmation = ""
    @State private var deletingAccount = false
    @State private var deleteAccountError: String?
    @State private var signingOut = false
    @State private var signOutError: String?
    @State private var disconnecting = false
    @State private var disconnectError: String?

    var body: some View {
        Color.clear
            .onReceive(NotificationCenter.default.publisher(for: .electronicMailSignOut)) { _ in
                signOut()
            }
            .onReceive(NotificationCenter.default.publisher(for: .electronicMailDisconnectGoogle)) { _ in
                guard !disconnecting else { return }
                disconnectError = nil
                confirmDisconnect = true
            }
            .onReceive(NotificationCenter.default.publisher(for: .electronicMailDeleteAccount)) { _ in
                guard !deletingAccount else { return }
                deleteAccountConfirmation = ""
                deleteAccountError = nil
                confirmDeleteAccount = true
            }
        .alert(
            "Could Not Sign Out",
            isPresented: Binding(
                get: { signOutError != nil },
                set: { if !$0 { signOutError = nil } }
            )
        ) {
            Button("OK") { signOutError = nil }
        } message: {
            Text(signOutError ?? "Try again.")
        }
        .confirmationDialog(
            "Disconnect Google?",
            isPresented: $confirmDisconnect,
            titleVisibility: .visible
        ) {
            Button("Disconnect and Delete Local Data", role: .destructive) {
                disconnectGoogle()
            }
            .disabled(disconnecting)
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Google access, synced mail data, and app sessions will be removed from Electronic Mail.")
        }
        .alert(
            "Could Not Disconnect Google",
            isPresented: Binding(
                get: { disconnectError != nil },
                set: { if !$0 { disconnectError = nil } }
            )
        ) {
            Button("OK") { disconnectError = nil }
        } message: {
            Text(disconnectError ?? "Try again.")
        }
        .sheet(isPresented: $confirmDeleteAccount) {
            VStack(alignment: .leading, spacing: 16) {
                Text("Permanently delete your account?")
                    .font(.title3.weight(.semibold))
                Text("This removes your Electronic Mail account, synced email data, drafts stored by the app, and active sessions. Your messages in Gmail are not deleted.")
                    .foregroundStyle(.secondary)
                Text("Type DELETE to confirm.")
                    .font(.callout.weight(.medium))
                TextField("DELETE", text: $deleteAccountConfirmation)
                    .textFieldStyle(.roundedBorder)
                    .disabled(deletingAccount)
                if let deleteAccountError {
                    Text(deleteAccountError)
                        .font(.callout)
                        .foregroundStyle(.red)
                }
                HStack {
                    Spacer()
                    Button("Cancel", role: .cancel) {
                        confirmDeleteAccount = false
                    }
                    .disabled(deletingAccount)
                    Button("Delete Account", role: .destructive) {
                        deleteAccount()
                    }
                    .disabled(
                        deletingAccount
                            || deleteAccountConfirmation.trimmingCharacters(in: .whitespacesAndNewlines) != "DELETE"
                    )
                }
            }
            .padding(24)
            .frame(width: 460)
        }
    }

    private func signOut() {
        guard !signingOut else { return }
        Task {
            signingOut = true
            signOutError = nil
            do {
                try await onSignOut()
            } catch {
                signOutError = error.localizedDescription
            }
            signingOut = false
        }
    }

    private func disconnectGoogle() {
        guard !disconnecting else { return }
        Task {
            disconnecting = true
            disconnectError = nil
            do {
                try await onDisconnectGoogle()
            } catch {
                disconnectError = error.localizedDescription
            }
            disconnecting = false
        }
    }

    private func deleteAccount() {
        guard !deletingAccount else { return }
        Task {
            deletingAccount = true
            deleteAccountError = nil
            do {
                try await onDeleteAccount()
                confirmDeleteAccount = false
            } catch {
                deleteAccountError = "Could not delete your account: \(error.localizedDescription)"
            }
            deletingAccount = false
        }
    }
}
