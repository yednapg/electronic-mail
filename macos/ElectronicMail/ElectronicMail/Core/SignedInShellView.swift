import AppKit
import CryptoKit
import Security
import SwiftUI
import UniformTypeIdentifiers

enum ElectronicMailShellMetrics {
    static let navTop: CGFloat = 12
    static let navLeading: CGFloat = 20
    static let navIconFrame = ElectronicMailControlMetrics.headerSymbolSize
    static let navHitFrame = ElectronicMailControlMetrics.headerControlSize
    static let navTitleGap = ElectronicMailControlMetrics.headerTitleGap
    static let navHeaderTitleGap = ElectronicMailControlMetrics.headerTitleGap
    static let navTextLeading = ElectronicMailControlMetrics.headerTitleLeading
    static let contentTop: CGFloat = 8
    static let contentMaxWidth: CGFloat = 900

    static func utilityCenter(for width: CGFloat) -> CGFloat {
        ElectronicMailLayoutMetrics(width: width).utilityCenter
    }

    static func headerUtilityCenter(for _: CGFloat) -> CGFloat {
        ElectronicMailControlMetrics.headerLeadingControlCenter
    }

    static func textLeading(for _: CGFloat) -> CGFloat {
        ElectronicMailControlMetrics.headerTitleLeading
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
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @ObservedObject private var store: InboxStore
    @StateObject private var aiInboxStore: AIInboxStore
    private let onReauthorizeGoogle: () async throws -> Void
    private let onSignOut: () async throws -> Void
    private let onDisconnectGoogle: () async throws -> Void
    private let onDeleteAccount: () async throws -> Void
    @State private var selection: SignedInDestination = .inbox
    @State private var supplementalDestination: ShellSupplementalDestination?
    @State private var navigationOpen = false
    @State private var mailboxSearchOpen = false
    @State private var mailboxSearchFocusRequested = false
    @State private var aiInboxSettingsOpen = false
    @State private var aiInboxOrganizeOpen = false
    @State private var commandPaletteOpen = false
    @State private var composer: MailComposerPresentation?
    @State private var recoveredComposerSnapshot: ComposerRecoverySnapshot?
    @State private var pendingComposerPresentation: MailComposerPresentation?
    @State private var composerRecoveryLoadGate = MailComposerRecoveryLoadGate<MailComposerPresentation>()
    @State private var pendingCommandThreadID: String?
    @State private var mailboxSearchText = ""
    @State private var confirmPermanentReaderDelete = false
    @State private var confirmAIMatterTrash = false
    @State private var contactPhotoAuthorizationRunning = false
    @State private var aiReaderChromeProgress: CGFloat = 0
    @AppStorage("ElectronicMailContactPhotoPromptDismissed") private var contactPhotoPromptDismissed = false

    public init(
        store: InboxStore,
        onReauthorizeGoogle: @escaping () async throws -> Void = {},
        onSignOut: @escaping () async throws -> Void = {},
        onDisconnectGoogle: @escaping () async throws -> Void = {},
        onDeleteAccount: @escaping () async throws -> Void = {}
    ) {
        self.store = store
        _aiInboxStore = StateObject(wrappedValue: AIInboxStore(client: store.aiInboxClient))
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
                    .allowsHitTesting(!navigationOpen && composer == nil)
                    .accessibilityHidden(navigationOpen || composer != nil)

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

                    ShellMenuButton(
                        colorScheme: colorScheme,
                        accessibilityLabel: "Hide Navigation",
                        action: toggleNavigation
                    )
                    .position(
                        x: ElectronicMailControlMetrics.headerLeadingControlCenter,
                        y: ElectronicMailControlMetrics.headerCenterY
                    )
                    .allowsHitTesting(composer == nil)
                    .accessibilityHidden(composer != nil)
                    .zIndex(6)
                }

                if showsContactPhotoPermissionPrompt {
                    contactPhotoPermissionPrompt
                        .frame(width: proxy.size.width, height: proxy.size.height, alignment: .bottom)
                        .padding(.bottom, 24)
                        .transition(.opacity.combined(with: .move(edge: .bottom)))
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
                        .transition(.opacity)
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
        .background {
            ElectronicMailDesign.background(for: colorScheme)
                .ignoresSafeArea()
        }
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
            } else if store.readerThreadID != nil || activeAIMatter != nil {
                closeActiveReader()
            }
        }
        .confirmationDialog(
            "Delete this email permanently?",
            isPresented: $confirmPermanentReaderDelete,
            titleVisibility: .visible
        ) {
            Button("Delete Permanently", role: .destructive) {
                performReaderAction(.deleteForever)
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("This cannot be undone.")
        }
        .alert("Move this matter to Trash?", isPresented: $confirmAIMatterTrash) {
            Button("Cancel", role: .cancel) {}
            Button("Move to Trash", role: .destructive) {
                guard let matter = activeAIMatter else { return }
                Task { await performAIMatterAction(.moveTrash, matter: matter, confirmTrash: true) }
            }
        } message: {
            if let matter = activeAIMatter {
                let threadCount = Set(matter.messages.compactMap(\.threadID)).count
                Text("This changes exactly \(matter.totalMessages) messages across \(threadCount) Gmail \(threadCount == 1 ? "thread" : "threads").")
            }
        }
        .confirmationDialog(
            "Resume your unsent message?",
            isPresented: composerRecoveryConflictPresented,
            titleVisibility: .visible
        ) {
            if let recovery = recoveredComposerSnapshot {
                Button("Resume \(recovery.mode.title)") {
                    resumeRecoveredComposer()
                }
            }
            if let requestedPresentation = pendingComposerPresentation {
                Button("Discard and \(requestedPresentation.actionTitle)", role: .destructive) {
                    discardRecoveryAndPresentRequestedComposer()
                }
            }
            Button("Cancel", role: .cancel) {
                pendingComposerPresentation = nil
            }
        } message: {
            Text("A different unsent message is waiting. Resume it, or discard it before starting another message.")
        }
    }

    private var composerRecoveryConflictPresented: Binding<Bool> {
        Binding(
            get: { pendingComposerPresentation != nil },
            set: { isPresented in
                if !isPresented {
                    pendingComposerPresentation = nil
                }
            }
        )
    }

    private var showsContactPhotoPermissionPrompt: Bool {
        !contactPhotoPromptDismissed
            && !store.contactPhotosAvailable
            && !store.missingOptionalGoogleScopes.isEmpty
            && !navigationOpen
            && composer == nil
            && !commandPaletteOpen
    }

    private var contactPhotoPermissionPrompt: some View {
        HStack(spacing: 12) {
            Image(systemName: ElectronicMailSymbols.contacts)
                .font(.system(size: 16, weight: .medium))

            Text("Show photos from your saved Google Contacts?")
                .font(ElectronicMailType.body(weight: .medium))

            Button("Not now") {
                contactPhotoPromptDismissed = true
            }
            .buttonStyle(.plain)
            .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))

            Button {
                Task { await authorizeContactPhotos() }
            } label: {
                if contactPhotoAuthorizationRunning {
                    ProgressView()
                        .controlSize(.small)
                } else {
                    Text("Allow contact photos")
                }
            }
            .disabled(contactPhotoAuthorizationRunning)
            .electronicMailGlassButton(role: .prominent, shape: .capsule)
        }
        .padding(.leading, 16)
        .padding(.trailing, 8)
        .frame(height: ElectronicMailControlMetrics.actionHeight + 8)
        .background(.regularMaterial, in: Capsule())
        .overlay(Capsule().strokeBorder(.white.opacity(colorScheme == .dark ? 0.14 : 0.24)))
        .accessibilityElement(children: .contain)
    }

    @MainActor
    private func authorizeContactPhotos() async {
        contactPhotoAuthorizationRunning = true
        defer { contactPhotoAuthorizationRunning = false }
        do {
            try await onReauthorizeGoogle()
        } catch {
            // Contacts are optional; keep the prompt available without
            // interrupting mailbox navigation or presenting a blocking error.
        }
    }

    @ViewBuilder
    private var content: some View {
        GeometryReader { proxy in
            VStack(alignment: .leading, spacing: 0) {
                shellContentHeader(width: proxy.size.width)

                destinationContent
            }
            .frame(width: proxy.size.width, height: proxy.size.height, alignment: .topLeading)
        }
    }

    private func shellContentHeader(width: CGFloat) -> some View {
        let defaultSearchWidth = min(360, max(220, width * 0.22))
        let searchWidth = max(
            180,
            defaultSearchWidth
                - (ElectronicMailControlMetrics.mailboxHeaderTrailingInset
                    - ElectronicMailControlMetrics.trailingInset)
                + (ElectronicMailControlMetrics.headerControlGap
                    - ElectronicMailControlMetrics.mailboxHeaderControlGap)
        )
        let showsReader = store.readerThreadID != nil || activeAIMatter != nil
        let showsMailboxControls = supplementalDestination == nil || supplementalDestination == .aiInbox
        let fixedTrailingControlCount: CGFloat = supplementalDestination == .aiInbox ? 2 : 1
        let readerTitleLeading = ElectronicMailControlMetrics.centeredContentLeading(
            containerWidth: width,
            maxContentWidth: ElectronicMailControlMetrics.readerMaxWidth,
            horizontalPadding: EmailReaderHeaderLayout.horizontalPadding
        )
        let readerActionControlCount = activeAIMatter == nil ? 5 : 6
        let trailingControlsWidth: CGFloat = if showsReader {
            readerTitleLeading
                + ElectronicMailControlMetrics.readerActionRailWidth(
                    controlCount: readerActionControlCount
                )
                + ElectronicMailControlMetrics.readerActionGap
        } else if showsMailboxControls {
            ElectronicMailControlMetrics.headerControlSize * fixedTrailingControlCount
                + ElectronicMailControlMetrics.mailboxHeaderControlGap * fixedTrailingControlCount
                + (mailboxSearchOpen ? searchWidth : ElectronicMailControlMetrics.headerControlSize)
                + ElectronicMailControlMetrics.mailboxHeaderTrailingInset
        } else {
            ElectronicMailControlMetrics.headerControlSize + ElectronicMailControlMetrics.trailingInset
        }

        return VStack(alignment: .leading, spacing: 0) {
            ElectronicMailShellHeader(
                width: width,
                titleLeading: showsReader
                    ? readerTitleLeading
                    : ElectronicMailControlMetrics.headerTitleLeading,
                titleTrailingReservation: trailingControlsWidth,
                trailingSpacing: showsMailboxControls && !showsReader
                    ? ElectronicMailControlMetrics.mailboxHeaderControlGap
                    : ElectronicMailControlMetrics.headerControlGap,
                trailingInset: showsMailboxControls && !showsReader
                    ? ElectronicMailControlMetrics.mailboxHeaderTrailingInset
                    : ElectronicMailControlMetrics.trailingInset,
                leading: {
                    if showsReader {
                        ShellBackButton(colorScheme: colorScheme, action: closeActiveReader)
                    } else {
                        ShellMenuButton(
                            colorScheme: colorScheme,
                            accessibilityLabel: navigationOpen ? "Hide Navigation" : "Show Navigation",
                            action: toggleNavigation
                        )
                    }
                },
                title: {
                    if showsReader {
                        VStack(
                            alignment: .leading,
                            spacing: ElectronicMailControlMetrics.readerTwoLineGap
                        ) {
                            Group {
                                Text(readerHeaderTitle)
                                    .font(ElectronicMailReaderType.title())
                                    .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                                    .lineLimit(ElectronicMailControlMetrics.readerSubjectLineLimit)
                                    .truncationMode(.tail)
                                    .fixedSize(horizontal: false, vertical: true)
                                    .layoutPriority(1)

                                Text(readerHeaderMetadata)
                                    .font(ElectronicMailReaderType.metadata())
                                    .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                                    .lineLimit(1)
                            }
                        }
                        .offset(y: ElectronicMailControlMetrics.readerHeaderContentOffsetY)
                        .modifier(
                            AIReaderSubjectScrollEffect(
                                progress: activeAIMatter == nil ? 0 : aiReaderChromeProgress
                            )
                        )
                        .help(readerHeaderTitle)
                        .accessibilityElement(children: .combine)
                        .accessibilityAddTraits(.isHeader)
                    } else if showsInboxModeSwitch {
                        ShellInboxModeSwitch(
                            selection: supplementalDestination == .aiInbox ? .aiInbox : .inbox,
                            colorScheme: colorScheme,
                            onSelect: selectPrimaryNavigation
                        )
                    } else {
                        Text(headerTitle)
                            .font(ElectronicMailType.mailboxHeader())
                            .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                            .lineLimit(1)
                            .truncationMode(.tail)
                            .help(headerTitle)
                            .accessibilityAddTraits(.isHeader)
                    }
                },
                trailing: {
                    if showsReader {
                        readerHeaderActions
                            .padding(
                                .trailing,
                                max(0, readerTitleLeading - ElectronicMailControlMetrics.trailingInset)
                            )
                    } else {
                        if supplementalDestination == .aiInbox {
                            ShellAIInboxSettingsButton(
                                colorScheme: colorScheme,
                                action: { aiInboxSettingsOpen = true }
                            )
                        }

                        ShellComposeButton(colorScheme: colorScheme, action: openComposeComposer)

                        if showsMailboxControls {
                            if mailboxSearchOpen {
                                DebouncedMailboxToolbarSearchField(
                                    query: $mailboxSearchText,
                                    focusRequested: mailboxSearchFocusRequested,
                                    onCancel: closeMailboxSearch,
                                    onFocusLost: closeMailboxSearch
                                )
                                .padding(.horizontal, 11)
                                .frame(width: searchWidth, height: ElectronicMailControlMetrics.headerSearchHeight)
                                .electronicMailGlassPanel(shape: .capsule)
                                .transition(searchFieldTransition)
                            } else {
                                ShellSearchButton(colorScheme: colorScheme, action: openMailboxSearch)
                                    .transition(searchButtonTransition)
                            }
                        }
                    }
                }
            )
            .padding(.bottom, showsReader ? ElectronicMailControlMetrics.readerHeaderContentOffsetY : 0)
        }
        .animation(
            searchAnimation,
            value: mailboxSearchOpen
        )
    }

    private enum EmailReaderHeaderLayout {
        static let horizontalPadding: CGFloat = 36
    }

    @ViewBuilder
    private var readerHeaderActions: some View {
        if let matter = activeAIMatter {
            aiMatterHeaderActions(matter)
        } else {
            ElectronicMailGlassGroup(spacing: ElectronicMailControlMetrics.readerActionGap) {
                HStack(spacing: ElectronicMailControlMetrics.readerActionGap) {
                    ElectronicMailIconControl(
                        symbol: readerPrimaryActionSymbol,
                        accessibilityLabel: readerPrimaryActionTitle,
                        action: performReaderPrimaryAction
                    )

                    ElectronicMailIconControl(
                        symbol: readerTrashActionSymbol,
                        accessibilityLabel: readerTrashActionTitle,
                        role: store.activeMailboxLabel == .trash ? .destructive : .standard,
                        action: performReaderTrashAction
                    )

                    ElectronicMailIconControl(
                        symbol: readerIsStarred ? ElectronicMailSymbols.starredFilled : ElectronicMailSymbols.starred,
                        accessibilityLabel: readerIsStarred ? "Unstar Conversation" : "Star Conversation",
                        action: {
                            performReaderAction(readerIsStarred ? .unstar : .star)
                        }
                    )

                    ElectronicMailIconControl(
                        symbol: readerIsUnread ? "envelope.open.fill" : "envelope.fill",
                        accessibilityLabel: readerIsUnread ? "Mark as Read" : "Mark as Unread",
                        action: {
                            performReaderAction(readerIsUnread ? .markRead : .markUnread)
                        }
                    )

                    ElectronicMailIconControl(
                        symbol: store.activeMailboxLabel == .spam
                            ? "checkmark.shield.fill"
                            : "exclamationmark.octagon.fill",
                        accessibilityLabel: store.activeMailboxLabel == .spam ? "Not Spam" : "Mark as Spam",
                        action: {
                            performReaderAction(store.activeMailboxLabel == .spam ? .notSpam : .markSpam)
                        }
                    )
                    .disabled(store.activeMailboxLabel == .trash)
                }
            }
        }
    }

    private func aiMatterHeaderActions(_ matter: AIMatterDetail) -> some View {
        let replyTarget = aiMatterReplyTarget(matter)
        let isStarred = aiMatterIsStarred(matter)

        return ElectronicMailGlassGroup(spacing: ElectronicMailControlMetrics.readerActionGap) {
            HStack(spacing: ElectronicMailControlMetrics.readerActionGap) {
                ElectronicMailIconControl(
                    symbol: "square.grid.2x2",
                    accessibilityLabel: "Organize Matter",
                    action: { aiInboxOrganizeOpen = true }
                )

                ElectronicMailIconControl(
                    symbol: ElectronicMailSymbols.reply,
                    accessibilityLabel: "Reply",
                    action: {
                        guard let replyTarget else { return }
                        openResponseComposer(
                            threadID: replyTarget.threadID,
                            mode: .reply,
                            sourceMessageID: replyTarget.messageID
                        )
                    }
                )
                .disabled(replyTarget == nil)

                ElectronicMailIconControl(
                    symbol: "archivebox.fill",
                    accessibilityLabel: "Archive Matter",
                    action: {
                        Task { await performAIMatterAction(.archive, matter: matter) }
                    }
                )

                ElectronicMailIconControl(
                    symbol: "envelope.open.fill",
                    accessibilityLabel: "Mark Matter as Read",
                    action: {
                        Task { await performAIMatterAction(.markRead, matter: matter) }
                    }
                )

                ElectronicMailIconControl(
                    symbol: isStarred ? ElectronicMailSymbols.starredFilled : ElectronicMailSymbols.starred,
                    accessibilityLabel: isStarred ? "Unstar Matter" : "Star Matter",
                    action: {
                        Task {
                            await performAIMatterAction(isStarred ? .unstar : .star, matter: matter)
                        }
                    }
                )

                ElectronicMailIconControl(
                    symbol: "trash.fill",
                    accessibilityLabel: "Move Matter to Trash",
                    role: .destructive,
                    action: { confirmAIMatterTrash = true }
                )
            }
        }
    }

    private var searchFieldTransition: AnyTransition {
        guard !reduceMotion else { return .opacity }
        return .asymmetric(
            insertion: .move(edge: .trailing)
                .combined(with: .scale(scale: 0.96, anchor: .trailing))
                .combined(with: .opacity),
            removal: .scale(scale: 0.88, anchor: .trailing)
                .combined(with: .opacity)
        )
    }

    private var searchButtonTransition: AnyTransition {
        guard !reduceMotion else { return .opacity }
        return .asymmetric(
            insertion: .scale(scale: 0.82).combined(with: .opacity),
            removal: .scale(scale: 0.90).combined(with: .opacity)
        )
    }

    private var searchAnimation: Animation? {
        reduceMotion
            ? nil
            : .spring(response: 0.34, dampingFraction: 0.84, blendDuration: 0.08)
    }

    @ViewBuilder
    private var destinationContent: some View {
        switch supplementalDestination {
        case .aiInbox:
            AIInboxView(
                store: aiInboxStore,
                searchText: $mailboxSearchText,
                showSettings: $aiInboxSettingsOpen,
                showOrganize: $aiInboxOrganizeOpen,
                currentUserDisplayName: store.session?.readiness.userDisplayName
                    ?? store.session?.dashboard.profile?.displayName
                    ?? store.session?.user.displayName
                    ?? store.session?.user.firstName,
                currentUserEmail: store.session?.user.email,
                onRespond: openResponseComposer,
                onGmailMutation: { await store.refresh() },
                onOpenAttachment: { attachment, messageID in
                    Task { await store.openAttachment(attachment, messageID: messageID) }
                },
                isAttachmentDownloading: { attachment, messageID in
                    store.isAttachmentDownloading(attachment, messageID: messageID)
                },
                onReaderChromeProgressChange: { progress in
                    aiReaderChromeProgress = progress
                }
            )
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
        case .aiInbox:
            return "AI Inbox"
        case .todos:
            return "To-do's"
        case nil:
            return store.mailboxTitle
        }
    }

    private var showsInboxModeSwitch: Bool {
        if supplementalDestination == .aiInbox {
            return true
        }
        return supplementalDestination == nil && selection == .inbox
    }

    private var primaryNavigationSelection: ShellPrimaryNavigationDestination? {
        if let supplementalDestination {
            return supplementalDestination.primaryNavigationDestination
        }
        return ShellPrimaryNavigationDestination(mailboxDestination: selection)
    }

    private var readerMessage: ThreadMessage? {
        store.readerThread?.messages.last
    }

    private var activeAIMatter: AIMatterDetail? {
        guard supplementalDestination == .aiInbox else { return nil }
        return aiInboxStore.detail
    }

    private func aiMatterReplyTarget(
        _ matter: AIMatterDetail
    ) -> (threadID: String, messageID: String)? {
        let messages = EmailThreadPresentation.orderedMessages(matter.messages)
        if let latestReplyableMessageID = matter.latestReplyableMessageID,
           let message = messages.first(where: { $0.id == latestReplyableMessageID }),
           let threadID = nonEmpty(message.threadID) {
            return (threadID, message.id)
        }
        guard let message = messages.reversed().first(where: { nonEmpty($0.threadID) != nil }),
              let threadID = nonEmpty(message.threadID) else {
            return nil
        }
        return (threadID, message.id)
    }

    private func aiMatterIsStarred(_ matter: AIMatterDetail) -> Bool {
        aiInboxStore.response?.matters.first(where: { $0.id == matter.id })?.starred == true
    }

    private var readerHeaderTitle: String {
        if let matter = activeAIMatter {
            return matter.title
        }
        return nonEmpty(store.readerThread?.title)
            ?? nonEmpty(store.readerThread?.subject)
            ?? nonEmpty(store.readerRow?.title)
            ?? "Email"
    }

    private var readerHeaderMetadata: String {
        if let matter = activeAIMatter {
            let messages = EmailThreadPresentation.orderedMessages(matter.messages)
            let count = max(matter.totalMessages, messages.count)
            let countLabel = count == 1 ? "1 message" : "\(count) messages"
            guard let receivedAt = messages.last?.receivedAt else {
                return countLabel
            }
            return "\(countLabel) · \(EmailReaderText.dayGrouping(receivedAt))"
        }

        let messages = store.readerThread?.messages ?? []
        let count = max(1, max(messages.count, store.readerRow?.messageCount ?? 1))
        let countLabel = count == 1 ? "1 message" : "\(count) messages"

        guard let receivedAt = messages.last?.receivedAt else {
            return countLabel
        }
        return "\(countLabel) · \(EmailReaderText.dayGrouping(receivedAt))"
    }

    private func nonEmpty(_ value: String?) -> String? {
        let trimmed = value?.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed?.isEmpty == false ? trimmed : nil
    }

    private var readerIsUnread: Bool {
        readerMessage?.labelIDs.contains(where: { $0.uppercased() == "UNREAD" }) == true
    }

    private var readerIsStarred: Bool {
        readerMessage?.labelIDs.contains(where: { $0.uppercased() == "STARRED" }) == true
    }

    private var readerPrimaryActionSymbol: String {
        switch store.activeMailboxLabel {
        case .archive: return ElectronicMailSymbols.moveToInbox
        case .spam: return "checkmark.shield.fill"
        case .trash: return "arrow.uturn.backward.circle.fill"
        default: return "archivebox.fill"
        }
    }

    private var readerPrimaryActionTitle: String {
        switch store.activeMailboxLabel {
        case .archive: return "Move to Inbox"
        case .spam: return "Not Spam"
        case .trash: return "Restore from Trash"
        default: return "Archive Conversation"
        }
    }

    private var readerTrashActionSymbol: String {
        store.activeMailboxLabel == .trash ? "trash.slash.fill" : "trash.fill"
    }

    private var readerTrashActionTitle: String {
        store.activeMailboxLabel == .trash ? "Delete Permanently" : "Move to Trash"
    }

    private func performReaderPrimaryAction() {
        switch store.activeMailboxLabel {
        case .archive: performReaderAction(.unarchive)
        case .spam: performReaderAction(.notSpam)
        case .trash: performReaderAction(.restoreTrash)
        default: performReaderAction(.archive)
        }
    }

    private func performReaderTrashAction() {
        if store.activeMailboxLabel == .trash {
            confirmPermanentReaderDelete = true
        } else {
            performReaderAction(.moveTrash)
        }
    }

    private func performReaderAction(_ action: GmailThreadAction) {
        guard let threadID = store.readerThreadID else { return }
        Task {
            await store.performReaderThreadAction(action, threadID: threadID, messageID: nil)
        }
    }

    @MainActor
    private func performAIMatterAction(
        _ action: GmailThreadAction,
        matter: AIMatterDetail,
        confirmTrash: Bool = false
    ) async {
        let applied = await aiInboxStore.perform(
            action,
            matter: matter,
            confirmMultiThreadTrash: confirmTrash
        )
        if applied {
            await store.refresh()
        }
    }

    private func toggleNavigation() {
        guard composer == nil else { return }
        withAnimation(reduceMotion ? nil : ShellNavigationMotion.screen) {
            mailboxSearchFocusRequested = false
            commandPaletteOpen = false
            navigationOpen.toggle()
        }
    }

    private func closeNavigation() {
        withAnimation(reduceMotion ? nil : ShellNavigationMotion.screen) {
            navigationOpen = false
        }
    }

    private func openMailboxSearch() {
        store.closeReader()
        aiInboxStore.closeDetail()
        withAnimation(searchAnimation) {
            if supplementalDestination == .todos {
                supplementalDestination = nil
            }
            navigationOpen = false
            commandPaletteOpen = false
            mailboxSearchOpen = true
            mailboxSearchFocusRequested = true
        }
    }

    private func closeMailboxSearch() {
        withAnimation(searchAnimation) {
            mailboxSearchOpen = false
            mailboxSearchFocusRequested = false
        }
    }

    private func selectPrimaryNavigation(_ destination: ShellPrimaryNavigationDestination) {
        pendingCommandThreadID = nil
        store.closeReader()
        aiInboxStore.closeDetail()
        mailboxSearchText = ""

        withAnimation(reduceMotion ? nil : ShellNavigationMotion.screen) {
            navigationOpen = false
            mailboxSearchOpen = false
            mailboxSearchFocusRequested = false
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
            mailboxSearchFocusRequested = false
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

    private func closeActiveReader() {
        if activeAIMatter != nil {
            aiReaderChromeProgress = 0
            aiInboxOrganizeOpen = false
            aiInboxStore.closeDetail()
        } else {
            store.closeReader()
        }
    }

    private func select(_ destination: SignedInDestination) {
        pendingCommandThreadID = nil
        aiReaderChromeProgress = 0
        aiInboxStore.closeDetail()
        supplementalDestination = nil
        navigationOpen = false
        mailboxSearchFocusRequested = false
        selection = destination
    }

    private func openComposeComposer() {
        guard composer == nil else {
            return
        }
        withAnimation(.easeInOut(duration: 0.12)) {
            navigationOpen = false
            mailboxSearchFocusRequested = false
            commandPaletteOpen = false
        }
        presentComposer(
            MailComposerPresentation(mode: .compose, threadID: nil, sourceMessageID: nil, title: "New message")
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
                title: activeAIMatter?.title ?? store.readerRow?.title ?? mode.title
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
            if recovery.matches(requestedPresentation, accountUserID: store.session?.user.id) {
                composer = recovery.presentation
            } else {
                pendingComposerPresentation = requestedPresentation
            }
            return
        }
        recoveredComposerSnapshot = nil
        composer = requestedPresentation
    }

    private func resumeRecoveredComposer() {
        guard let recovery = recoveredComposerSnapshot,
              recovery.belongs(to: store.session?.user.id) else {
            pendingComposerPresentation = nil
            return
        }
        pendingComposerPresentation = nil
        composer = recovery.presentation
    }

    private func discardRecoveryAndPresentRequestedComposer() {
        guard let requestedPresentation = pendingComposerPresentation else {
            return
        }
        ComposerRecoveryStore.clear()
        recoveredComposerSnapshot = nil
        pendingComposerPresentation = nil
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

        if let recovery,
           recovery.belongs(to: store.session?.user.id),
           recovery.shouldRestore {
            recoveredComposerSnapshot = recovery
        } else {
            if recovery?.belongs(to: store.session?.user.id) == true {
                await ComposerRecoveryWriter.shared.clear()
            }
            recoveredComposerSnapshot = nil
        }

        let presentation = composerRecoveryLoadGate.complete()
        guard composer == nil, let presentation else { return }
        presentComposerAfterRecoveryLoad(presentation)
    }
}

private enum ShellNavigationMotion {
    static let screen = Animation.easeInOut(duration: 0.15)
}

enum ShellSupplementalDestination: Equatable {
    case aiInbox
    case todos

    var primaryNavigationDestination: ShellPrimaryNavigationDestination {
        switch self {
        case .aiInbox:
            return .aiInbox
        case .todos:
            return .todos
        }
    }
}

enum ShellPrimaryNavigationDestination: String, CaseIterable, Identifiable {
    case inbox = "Inbox"
    case aiInbox = "AI Inbox"
    case starred = "Starred"
    case drafts = "Drafts"
    case sent = "Sent"
    case spam = "Spam"
    case trash = "Trash"
    case archive = "Archive"
    case all = "All Mail"
    case todos = "To-do's"

    var id: Self {
        self
    }

    var title: String {
        rawValue
    }

    var symbolName: String {
        switch self {
        case .inbox: return "tray.full"
        case .aiInbox: return "sparkles"
        case .starred: return "star"
        case .drafts: return "doc.text"
        case .sent: return "paperplane"
        case .spam: return "exclamationmark.octagon"
        case .trash: return "trash"
        case .archive: return "archivebox"
        case .all: return "tray.2"
        case .todos: return "checkmark.circle"
        }
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
        case .aiInbox, .todos:
            return nil
        }
    }

    var supplementalDestination: ShellSupplementalDestination? {
        switch self {
        case .aiInbox:
            return .aiInbox
        case .todos:
            return .todos
        case .inbox, .starred, .drafts, .sent, .spam, .trash, .archive, .all:
            return nil
        }
    }

}

private struct ShellInboxModeSwitch: View {
    let selection: ShellPrimaryNavigationDestination
    let colorScheme: ColorScheme
    let onSelect: (ShellPrimaryNavigationDestination) -> Void

    private let destinations: [ShellPrimaryNavigationDestination] = [.inbox, .aiInbox]

    var body: some View {
        HStack(spacing: 20) {
            ForEach(destinations) { destination in
                let isSelected = selection == destination
                Button {
                    onSelect(destination)
                } label: {
                    HStack(spacing: 8) {
                        Image(systemName: destination.symbolName)
                            .font(.system(size: 16, weight: .medium))
                            .frame(width: 19)
                            .accessibilityHidden(true)

                        Text(destination.title)
                            .font(ElectronicMailType.mailboxHeader(weight: isSelected ? .semibold : .medium))
                    }
                    .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                    .opacity(isSelected ? 1 : 0.5)
                    .frame(width: 112)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .help("Open \(destination.title)")
                .accessibilityLabel(destination.title)
                .accessibilityHint(isSelected ? "Currently selected" : "Switches to \(destination.title)")
                .accessibilityAddTraits(isSelected ? .isSelected : [])
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Inbox mode")
    }
}

private struct AIReaderSubjectScrollEffect: ViewModifier {
    let progress: CGFloat

    func body(content: Content) -> some View {
        let resolvedProgress = min(1, max(0, progress))
        content
            .offset(y: -18 * resolvedProgress)
            .opacity(1 - resolvedProgress)
            .mask {
                LinearGradient(
                    colors: [
                        Color.black.opacity(1 - resolvedProgress),
                        Color.black,
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )
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
                .opacity(ElectronicMailControlMetrics.mailboxHeaderIconOpacity)
                .frame(
                    width: ElectronicMailShellMetrics.navIconFrame,
                    height: ElectronicMailShellMetrics.navIconFrame
                )
                .frame(
                    width: ElectronicMailShellMetrics.navHitFrame,
                    height: ElectronicMailShellMetrics.navHitFrame
                )
                .contentShape(Circle())
        }
        .electronicMailGlassButton(role: .standard, shape: .circle)
        .frame(
            width: ElectronicMailShellMetrics.navHitFrame,
            height: ElectronicMailShellMetrics.navHitFrame
        )
        .contentShape(Circle())
        .help(accessibilityLabel)
        .accessibilityLabel(accessibilityLabel)
    }
}

private struct ShellBackButton: View {
    let colorScheme: ColorScheme
    let action: () -> Void

    var body: some View {
        ElectronicMailIconControl(
            symbol: "chevron.backward",
            accessibilityLabel: "Back",
            action: action
        )
    }
}

private struct ShellComposeButton: View {
    let colorScheme: ColorScheme
    let action: () -> Void

    var body: some View {
        ElectronicMailIconControl(
            symbol: "square.and.pencil",
            accessibilityLabel: "New message",
            symbolOpacity: ElectronicMailControlMetrics.mailboxHeaderIconOpacity,
            action: action
        )
    }
}

private struct ShellAIInboxSettingsButton: View {
    let colorScheme: ColorScheme
    let action: () -> Void

    var body: some View {
        ElectronicMailIconControl(
            symbol: "gearshape",
            accessibilityLabel: "AI Inbox Settings",
            symbolOpacity: ElectronicMailControlMetrics.mailboxHeaderIconOpacity,
            action: action
        )
    }
}

private struct ShellSearchButton: View {
    let colorScheme: ColorScheme
    let action: () -> Void

    var body: some View {
        ElectronicMailIconControl(
            symbol: ElectronicMailSymbols.search,
            accessibilityLabel: "Search Mail",
            symbolOpacity: ElectronicMailControlMetrics.mailboxHeaderIconOpacity,
            action: action
        )
        .accessibilityHint("Expands the mailbox search field")
    }
}

private struct ShellNavigationCanvas: View {
    let width: CGFloat
    let selection: ShellPrimaryNavigationDestination?
    let colorScheme: ColorScheme
    let onSelect: (ShellPrimaryNavigationDestination) -> Void

    var body: some View {
        let firstRowTop = ElectronicMailControlMetrics.headerCenterY
            - ElectronicMailMailboxType.navigationRowHeight / 2

        ZStack(alignment: .topLeading) {
            ElectronicMailDesign.background(for: colorScheme)
                .ignoresSafeArea()

            VStack(alignment: .leading, spacing: 0) {
                ForEach(ShellPrimaryNavigationDestination.allCases) { destination in
                    ShellNavigationItem(
                        destination: destination,
                        isSelected: selection == destination,
                        colorScheme: colorScheme,
                        action: { onSelect(destination) }
                    )
                }
            }
            .padding(.top, firstRowTop)
            .padding(.leading, ElectronicMailControlMetrics.headerTitleLeading)
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
            HStack(spacing: 12) {
                Image(systemName: destination.symbolName)
                    .font(.system(size: 17, weight: .medium))
                    .frame(width: 24, alignment: .center)
                    .accessibilityHidden(true)

                Text(destination.title)
                    .font(ElectronicMailMailboxType.navigationItem())
            }
            .foregroundStyle(
                isSelected
                    ? ElectronicMailDesign.appleBlue
                    : ElectronicMailDesign.primaryText(for: colorScheme)
            )
            .opacity(isSelected ? 1 : 0.82)
            .frame(width: 280, height: ElectronicMailMailboxType.navigationRowHeight, alignment: .leading)
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
                    focusRequested: true,
                    onCancel: onClose,
                    onFocusLost: onClose
                )
                .frame(width: 420, height: 30)

                Button("Done", action: onClose)
                    .padding(.horizontal, 12)
                    .frame(minHeight: 32)
                    .buttonStyle(.bordered)
                    .buttonBorderShape(.capsule)
                    .font(ElectronicMailType.small(weight: .semibold))
                    .accessibilityHint("Closes the search field and keeps the current results")
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 12)
            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
            .padding(.top, ElectronicMailShellMetrics.navTop)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .onExitCommand(perform: onClose)
    }
}

private struct DebouncedMailboxToolbarSearchField: View {
    @Binding private var query: String
    @State private var fieldText: String
    @FocusState private var isFocused: Bool
    private let focusRequested: Bool
    private let onCancel: () -> Void
    private let onFocusLost: () -> Void

    init(
        query: Binding<String>,
        focusRequested: Bool,
        onCancel: @escaping () -> Void,
        onFocusLost: @escaping () -> Void
    ) {
        self._query = query
        self._fieldText = State(initialValue: query.wrappedValue)
        self.focusRequested = focusRequested
        self.onCancel = onCancel
        self.onFocusLost = onFocusLost
    }

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "magnifyingglass")
                .font(.system(size: 14, weight: .medium))
                .foregroundStyle(.secondary)
                .opacity(ElectronicMailControlMetrics.mailboxHeaderIconOpacity)
                .accessibilityHidden(true)

            TextField("Search Mail", text: $fieldText)
                .textFieldStyle(.plain)
                .font(ElectronicMailType.body())
                .focused($isFocused)
                .onExitCommand(perform: onCancel)
                .accessibilityLabel("Search Mail")

            if !fieldText.isEmpty {
                Button {
                    fieldText = ""
                    query = ""
                    isFocused = true
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .font(.system(size: 13, weight: .medium))
                        .foregroundStyle(.tertiary)
                }
                .buttonStyle(.plain)
                .help("Clear Search")
                .accessibilityLabel("Clear Search")
            }
        }
            .task(id: fieldText) {
                if fieldText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    if query != fieldText { query = fieldText }
                    return
                }
                try? await Task.sleep(nanoseconds: 350_000_000)
                guard !Task.isCancelled, query != fieldText else { return }
                query = fieldText
            }
            .onChange(of: query) { _, nextQuery in
                if fieldText != nextQuery { fieldText = nextQuery }
            }
            .task(id: focusRequested) {
                // A visible TextField can become the window's first responder while
                // SwiftUI installs it. Re-apply the requested state on the next run
                // loop so an icon-triggered expansion reliably receives typing.
                await Task.yield()
                isFocused = focusRequested
            }
            .task(id: isFocused) {
                guard !isFocused else { return }
                try? await Task.sleep(nanoseconds: 140_000_000)
                guard !Task.isCancelled, !isFocused else { return }
                onFocusLost()
            }
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
                            .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                    }

                    TextField("", text: $query)
                        .textFieldStyle(.plain)
                        .font(ElectronicMailType.title())
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
            .frame(minWidth: 320, maxWidth: ElectronicMailControlMetrics.paletteMaxWidth)
            .electronicMailGlassPanel(shape: .panel(radius: 16))
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
        HStack(spacing: ElectronicMailControlMetrics.headerControlGap) {
            Image(systemName: symbolName)
                .font(.system(size: ElectronicMailControlMetrics.headerSymbolSize, weight: .medium))
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
        .frame(height: ElectronicMailControlMetrics.paletteRowHeight)
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
            return ElectronicMailSymbols.inbox
        case .starred:
            return ElectronicMailSymbols.starred
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

    var actionTitle: String {
        switch mode {
        case .compose:
            return "Compose"
        case .reply:
            return "Reply"
        case .replyAll:
            return "Reply All"
        case .forward:
            return "Forward"
        case .draft:
            return "Open Draft"
        }
    }
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
    let bodyHTML: String?
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

    var presentation: MailComposerPresentation {
        MailComposerPresentation(
            mode: mode,
            threadID: threadID,
            sourceMessageID: sourceMessageID,
            title: title
        )
    }

    var shouldRestore: Bool {
        let isResponse = MailComposerPolicy.responseMode(for: mode) != nil
        let hasEditedResponseField = isResponse
            && (responseFieldProvenance.map { !$0.userEditedFields.isEmpty }
                ?? legacyResponseFieldsMayContainEdits)
        let authoredTextFields = isResponse
            ? [bccText, bodyText]
            : [toText, ccText, bccText, subject, bodyText]
        return MailComposerPolicy.shouldRestoreRecovery(
            mode: mode,
            hasUnresolvedSendAttempt: unresolvedSendAttempt ?? false,
            hasGmailDraft: gmailDraftID?.isEmpty == false,
            hasEditedResponseField: hasEditedResponseField,
            authoredTextFields: authoredTextFields,
            attachmentCount: attachments.count + existingDraftAttachments.count
        )
    }

    private var legacyResponseFieldsMayContainEdits: Bool {
        switch mode {
        case .forward:
            return [toText, ccText].contains {
                !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            }
        case .reply, .replyAll:
            return true
        case .compose, .draft:
            return false
        }
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
            ZStack(alignment: .top) {
                ElectronicMailDesign.background(for: colorScheme)
                    .ignoresSafeArea()

                MailComposerSheet(
                    presentation: presentation,
                    recoverySnapshot: recoverySnapshot,
                    store: store,
                    colorScheme: colorScheme,
                    onReauthorizeGoogle: onReauthorizeGoogle,
                    onClose: onClose
                )
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

private struct ComposerFormattingCommand: Equatable {
    let id = UUID()
    let action: Action

    enum Action: Equatable {
        case fontName(String)
        case fontSize(CGFloat)
        case toggleBold
        case toggleItalic
        case toggleUnderline
        case alignment(ComposerTextAlignment)
        case bulletList
    }
}

private enum ComposerTextAlignment: Equatable {
    case left
    case center
    case right
}

private enum MailComposerLayout {
    static let contentLeadingInset: CGFloat = 52
    static let canvasHorizontalInset: CGFloat = 28
    static let fieldLabelWidth: CGFloat = 86
    static let fieldSpacing: CGFloat = 10
    static let valueLeadingInset: CGFloat = fieldLabelWidth + fieldSpacing
    static let toolbarControlGap: CGFloat = 10
    static let footerBottomInset: CGFloat = 20
    static let footerReservedHeight: CGFloat = 88
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
    @State private var showsCopyFields = false
    @State private var subject = ""
    @State private var bodyText = ""
    @State private var bodyHTML: String?
    @State private var statusText: String?
    @State private var draftFailureToast: String?
    @State private var draftFailureToastTask: Task<Void, Never>?
    @State private var formattingCommand: ComposerFormattingCommand?
    @State private var selectedFontName = "System"
    @State private var selectedFontSize = 15
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
        GeometryReader { proxy in
            VStack(alignment: .leading, spacing: 0) {
                composerHeader(width: proxy.size.width)

                VStack(alignment: .leading, spacing: 0) {
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
                        .padding(.horizontal, 14)
                        .frame(minHeight: ElectronicMailControlMetrics.actionHeight)
                        .buttonStyle(.bordered)
                        .buttonBorderShape(.capsule)
                        .font(ElectronicMailComposerType.control(weight: .semibold))
                        .disabled(loadingDraft)
                    }
                    .padding(.vertical, 8)
                }

                composerFromField
                composerToField
                if showsCopyFields || !ccText.isEmpty || !bccText.isEmpty {
                    composerField(
                        "Cc",
                        placeholder: "Add recipients",
                        text: $ccText,
                        focus: .cc,
                        responseField: .cc
                    )
                    composerField(
                        "Bcc",
                        placeholder: "Add hidden recipients",
                        text: $bccText,
                        focus: .bcc
                    )
                }
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

                composerEditor
                    .padding(.leading, MailComposerLayout.valueLeadingInset)

                composerAttachments
                    .padding(.leading, MailComposerLayout.valueLeadingInset)
                }
                .padding(.leading, MailComposerLayout.contentLeadingInset)
                .padding(.horizontal, MailComposerLayout.canvasHorizontalInset)
                .padding(.top, 18)
                .padding(.bottom, MailComposerLayout.footerReservedHeight)
                .frame(
                    maxWidth: ElectronicMailControlMetrics.composerMaxWidth,
                    maxHeight: .infinity,
                    alignment: .topLeading
                )
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
            }
            .frame(width: proxy.size.width, height: proxy.size.height, alignment: .topLeading)
            .overlay(alignment: .bottom) {
                composerFooter
                    .padding(.leading, MailComposerLayout.contentLeadingInset)
                    .padding(.horizontal, MailComposerLayout.canvasHorizontalInset)
                    .frame(maxWidth: ElectronicMailControlMetrics.composerMaxWidth)
                    .padding(.bottom, MailComposerLayout.footerBottomInset)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .overlay {
            if let draftFailureToast {
                ElectronicMailRefreshFailureToast(message: draftFailureToast)
                    .padding(.bottom, 66)
                    .transition(.opacity.combined(with: .move(edge: .bottom)))
            }
        }
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
        .onChange(of: ccText) { _, value in
            if !value.isEmpty { showsCopyFields = true }
            scheduleAutosave()
        }
        .onChange(of: bccText) { _, value in
            if !value.isEmpty { showsCopyFields = true }
            scheduleAutosave()
        }
        .onChange(of: subject) { _, _ in scheduleAutosave() }
        .onChange(of: bodyText) { _, _ in scheduleAutosave() }
        .onChange(of: bodyHTML) { _, _ in scheduleAutosave() }
        .onChange(of: attachments.map(\.id)) { _, _ in scheduleAutosave() }
        .onChange(of: existingDraftAttachments.map(\.id)) { _, _ in scheduleAutosave() }
        .onChange(of: preserveExistingDraftAttachments) { _, _ in scheduleAutosave() }
        .onChange(of: includeOriginalAttachments) { _, _ in scheduleAutosave() }
        .onChange(of: activeMode) { _, _ in scheduleAutosave() }
        .onDisappear {
            autosaveTask?.cancel()
            attachmentLoadTask?.cancel()
            sendTask?.cancel()
            draftFailureToastTask?.cancel()
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
            Button("Delete Draft", role: .destructive) { Task { await deleteOrDiscardDraft() } }
            Button("Keep Draft", role: .cancel) {}
        } message: {
            Text("This removes the draft from Electronic Mail and Gmail.")
        }
    }

    private func composerHeader(width: CGFloat) -> some View {
        let titleLeading = ElectronicMailControlMetrics.centeredContentLeading(
            containerWidth: width,
            maxContentWidth: ElectronicMailControlMetrics.composerMaxWidth,
            contentInset: MailComposerLayout.contentLeadingInset
                + MailComposerLayout.canvasHorizontalInset
        )

        return ElectronicMailShellHeader(
            width: width,
            titleLeading: titleLeading,
            titleTrailingReservation: ElectronicMailControlMetrics.headerControlSize
                + ElectronicMailControlMetrics.trailingInset,
            leading: {
                ElectronicMailIconControl(
                    symbol: "chevron.backward",
                    accessibilityLabel: "Back",
                    action: {
                        NotificationCenter.default.post(
                            name: .electronicMailComposerCloseRequested,
                            object: nil
                        )
                    }
                )
            },
            title: {
                Text(composerDisplayTitle)
                    .font(.system(size: ElectronicMailComposerType.modeSize, weight: .semibold))
                    .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                    .lineLimit(1)
                    .truncationMode(.tail)
                    .accessibilityAddTraits(.isHeader)
            },
            trailing: {
                ElectronicMailIconControl(
                    symbol: "trash.fill",
                    accessibilityLabel: "Delete draft",
                    role: .destructive,
                    action: { confirmDeleteDraft = true }
                )
                .disabled(composerControlsDisabled || savingDraft || loadingDraft)
            }
        )
        .frame(height: ElectronicMailControlMetrics.headerHeight, alignment: .top)
    }

    private var composerDisplayTitle: String {
        let typedSubject = subject.trimmingCharacters(in: .whitespacesAndNewlines)
        return typedSubject.isEmpty ? "New Mail 1" : typedSubject
    }

    private var composerFromField: some View {
        HStack(spacing: MailComposerLayout.fieldSpacing) {
            Text("From")
                .font(ElectronicMailComposerType.label())
                .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                .frame(width: MailComposerLayout.fieldLabelWidth, alignment: .leading)

            Text(currentSenderName)
                .font(ElectronicMailComposerType.value())
                .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))

            if let email = store.session?.user.email, !email.isEmpty {
                Text(email)
                    .font(ElectronicMailComposerType.value())
                    .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                    .lineLimit(1)
                    .truncationMode(.middle)
            }

            Spacer(minLength: 0)
        }
        .frame(height: ElectronicMailControlMetrics.composerFieldHeight)
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(ElectronicMailDesign.divider(for: colorScheme))
                .frame(height: 1)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("From \(currentSenderName), \(store.session?.user.email ?? "")")
    }

    private var composerToField: some View {
        HStack(alignment: .center, spacing: MailComposerLayout.fieldSpacing) {
            Text("To")
                .font(ElectronicMailComposerType.label())
                .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                .lineLimit(1)
                .frame(width: MailComposerLayout.fieldLabelWidth, alignment: .leading)

            TextField("Add recipients", text: composerFieldBinding($toText, responseField: .to))
                .textFieldStyle(.plain)
                .font(ElectronicMailComposerType.value())
                .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                .frame(minWidth: 0, maxWidth: .infinity, minHeight: 30, maxHeight: 30, alignment: .leading)
                .layoutPriority(1)
                .accessibilityLabel("To")
                .focused($focusedField, equals: .to)
                .onSubmit { advanceFocus(after: .to) }

            Button("Cc/Bcc") {
                withAnimation(.easeInOut(duration: 0.12)) {
                    showsCopyFields.toggle()
                }
                if showsCopyFields {
                    focusedField = ccText.isEmpty ? .cc : .bcc
                }
            }
            .font(ElectronicMailComposerType.control())
            .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
            .padding(.horizontal, 8)
            .frame(minHeight: 32)
            .fixedSize(horizontal: true, vertical: false)
            .contentShape(Rectangle())
            .buttonStyle(.plain)
            .help(showsCopyFields ? "Hide Cc and Bcc" : "Show Cc and Bcc")
        }
        .frame(height: ElectronicMailControlMetrics.composerFieldHeight)
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(ElectronicMailDesign.divider(for: colorScheme))
                .frame(height: 1)
        }
        .disabled(composerControlsDisabled)
    }

    @ViewBuilder
    private var composerAttachments: some View {
        if !attachments.isEmpty || !existingDraftAttachments.isEmpty {
            VStack(alignment: .leading, spacing: 6) {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
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
                .frame(height: 42)

                Text(attachmentSummary)
                    .font(ElectronicMailComposerType.status())
                    .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
            }
            .padding(.top, 10)
        }
    }

    private var currentSenderName: String {
        store.session?.user.displayName
            ?? store.session?.user.firstName
            ?? store.session?.user.email
            ?? "You"
    }

    private var attachmentSummary: String {
        let count = attachments.count + existingDraftAttachments.count
        let totalBytes = attachments.reduce(0) { $0 + $1.byteCount }
        let size = totalBytes > 0
            ? ByteCountFormatter.string(fromByteCount: Int64(totalBytes), countStyle: .file)
            : "saved"
        return "\(count) attachment\(count == 1 ? "" : "s") · \(size)"
    }

    private var composerEditor: some View {
        ZStack(alignment: .topLeading) {
            if bodyText.isEmpty {
                Text(editorPlaceholder)
                    .font(ElectronicMailComposerType.body())
                    .foregroundStyle(ElectronicMailDesign.tertiaryText(for: colorScheme))
                    .padding(.horizontal, ElectronicMailComposerEditorLayout.placeholderHorizontalInset)
                    .padding(.vertical, ElectronicMailComposerEditorLayout.placeholderVerticalInset)
                    .allowsHitTesting(false)
                    .accessibilityHidden(true)
            }

            ComposerRichTextEditor(
                text: $bodyText,
                html: $bodyHTML,
                command: formattingCommand,
                shouldFocus: focusedField == .body,
                colorScheme: colorScheme,
                onFocus: { focusedField = .body }
            )
                .accessibilityLabel("Message body")
        }
        .frame(minHeight: 220, maxHeight: .infinity)
        .padding(.top, 12)
        .disabled(composerControlsDisabled)
    }

    private var composerFooter: some View {
        HStack(spacing: 12) {
            composerFormattingToolbar

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
                .padding(.horizontal, 16)
                .frame(minHeight: ElectronicMailControlMetrics.actionHeight)
                .font(ElectronicMailComposerType.control(weight: .semibold))
                .electronicMailGlassButton(role: .standard, shape: .capsule)
                .disabled(reauthorizing)
            }

            Spacer(minLength: 12)

            ElectronicMailGlassGroup(spacing: ElectronicMailControlMetrics.actionGap) {
                HStack(spacing: ElectronicMailControlMetrics.actionGap) {
                    Menu {
                        Button("Send now") {
                            startSend(allowEmptySubject: false)
                        }
                        .keyboardShortcut(.return, modifiers: [.command])

                        Divider()

                        Button("Schedule for later") {
                            showDraftFailureToast("Scheduled sending is not available yet. Your draft remains saved.")
                        }
                    } label: {
                        if sending {
                            ProgressView().controlSize(.small)
                                .frame(minWidth: 88, minHeight: ElectronicMailControlMetrics.actionHeight)
                        } else {
                            HStack(spacing: 8) {
                                Text("Send")
                                Image(systemName: "chevron.down")
                                    .font(.system(size: 10, weight: .semibold))
                            }
                            .padding(.horizontal, 18)
                            .frame(minWidth: 96, minHeight: ElectronicMailControlMetrics.actionHeight)
                            .contentShape(Capsule())
                        }
                    }
                    .menuIndicator(.hidden)
                    .font(ElectronicMailComposerType.control(weight: .semibold))
                    .electronicMailGlassButton(role: .prominent, shape: .capsule)
                    .disabled(!canSend)
                }
            }
        }
        .padding(.top, 12)
        .frame(minHeight: 54)
        .overlay(alignment: .top) {
            Rectangle()
                .fill(ElectronicMailDesign.divider(for: colorScheme))
                .frame(height: 1)
        }
    }

    private var composerFormattingToolbar: some View {
        ElectronicMailGlassGroup(spacing: MailComposerLayout.toolbarControlGap) {
            HStack(spacing: MailComposerLayout.toolbarControlGap) {
                Menu {
                    ForEach(["System", "Helvetica Neue", "Arial", "Georgia", "Courier New"], id: \.self) { name in
                        Button {
                            selectedFontName = name
                            issueFormattingCommand(.fontName(name))
                        } label: {
                            Label(name, systemImage: name == selectedFontName ? "checkmark" : "textformat")
                        }
                    }
                } label: {
                    HStack(spacing: 8) {
                        Text(selectedFontName == "Helvetica Neue" ? "Helvetica" : selectedFontName)
                            .lineLimit(1)

                        Image(systemName: "chevron.down")
                            .font(.system(size: 10, weight: .semibold))
                    }
                    .font(.system(size: 13, weight: .semibold))
                    .padding(.horizontal, 14)
                    .frame(minWidth: 96, minHeight: ElectronicMailControlMetrics.actionHeight)
                    .contentShape(Capsule())
                }
                .menuIndicator(.hidden)
                .electronicMailGlassControlSurface(shape: .capsule)
                .help("Font family")
                .accessibilityLabel("Font family, \(selectedFontName)")

                Menu {
                    ForEach([12, 14, 15, 16, 18, 24], id: \.self) { size in
                        Button {
                            selectedFontSize = size
                            issueFormattingCommand(.fontSize(CGFloat(size)))
                        } label: {
                            Label("\(size) pt", systemImage: size == selectedFontSize ? "checkmark" : "textformat.size")
                        }
                    }
                } label: {
                    HStack(alignment: .firstTextBaseline, spacing: 0) {
                        Text("A")
                            .font(.system(size: composerToolbarSymbolSize, weight: .semibold))
                        Text("a")
                            .font(.system(size: composerToolbarSymbolSize - 6, weight: .semibold))
                    }
                    .frame(
                        width: ElectronicMailControlMetrics.actionHeight,
                        height: ElectronicMailControlMetrics.actionHeight
                    )
                    .contentShape(Circle())
                }
                .menuIndicator(.hidden)
                .electronicMailGlassControlSurface(shape: .circle)
                .help("Font size: \(selectedFontSize) points")
                .accessibilityLabel("Font size, \(selectedFontSize) points")

                formattingButton(symbol: "bold", label: "Bold", action: .toggleBold)
                formattingButton(symbol: "italic", label: "Italic", action: .toggleItalic)
                formattingButton(symbol: "underline", label: "Underline", action: .toggleUnderline)

                Menu {
                    Button("Align Left") { issueFormattingCommand(.alignment(.left)) }
                    Button("Align Center") { issueFormattingCommand(.alignment(.center)) }
                    Button("Align Right") { issueFormattingCommand(.alignment(.right)) }
                } label: {
                    Image(systemName: "text.alignleft")
                        .font(.system(size: composerToolbarSymbolSize, weight: .semibold))
                        .symbolRenderingMode(.monochrome)
                        .frame(
                            width: ElectronicMailControlMetrics.actionHeight,
                            height: ElectronicMailControlMetrics.actionHeight
                        )
                        .contentShape(Circle())
                }
                .menuIndicator(.hidden)
                .electronicMailGlassControlSurface(shape: .circle)
                .help("Alignment")
                .accessibilityLabel("Text alignment")

                formattingButton(symbol: "list.bullet", label: "Bulleted list", action: .bulletList)

                Button {
                    chooseAttachments()
                } label: {
                    ZStack {
                        if loadingAttachments {
                            ProgressView().controlSize(.small)
                        } else {
                            Image(systemName: "paperclip")
                                .font(.system(size: composerToolbarSymbolSize, weight: .semibold))
                                .symbolRenderingMode(.monochrome)
                        }
                    }
                    .frame(
                        width: ElectronicMailControlMetrics.actionHeight,
                        height: ElectronicMailControlMetrics.actionHeight
                    )
                    .contentShape(Circle())
                }
                .electronicMailGlassControlSurface(shape: .circle)
                .disabled(composerControlsDisabled || loadingAttachments)
                .help("Attach files")
                .accessibilityLabel(loadingAttachments ? "Preparing files" : "Attach files")
            }
        }
        .disabled(composerControlsDisabled)
    }

    private var composerToolbarSymbolSize: CGFloat {
        ElectronicMailControlMetrics.headerSymbolSize
    }

    private func formattingButton(
        symbol: String,
        label: String,
        action: ComposerFormattingCommand.Action
    ) -> some View {
        Button {
            issueFormattingCommand(action)
        } label: {
            Image(systemName: symbol)
                .font(.system(size: composerToolbarSymbolSize, weight: .semibold))
                .symbolRenderingMode(.monochrome)
                .frame(
                    width: ElectronicMailControlMetrics.actionHeight,
                    height: ElectronicMailControlMetrics.actionHeight
                )
                .contentShape(Circle())
        }
        .electronicMailGlassControlSurface(shape: .circle)
        .help(label)
        .accessibilityLabel(label)
    }

    private func issueFormattingCommand(_ action: ComposerFormattingCommand.Action) {
        formattingCommand = ComposerFormattingCommand(action: action)
        focusedField = .body
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
            bodyHTML ?? "",
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
        responseField: MailComposerResponseField? = nil
    ) -> some View {
        HStack(alignment: .center, spacing: MailComposerLayout.fieldSpacing) {
            Text(label)
                .font(ElectronicMailComposerType.label())
                .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                .lineLimit(1)
                .frame(width: MailComposerLayout.fieldLabelWidth, alignment: .leading)
            TextField(placeholder, text: composerFieldBinding(text, responseField: responseField))
                .textFieldStyle(.plain)
                .font(ElectronicMailComposerType.value())
                .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                .frame(minWidth: 0, maxWidth: .infinity, minHeight: 28, maxHeight: 28, alignment: .leading)
                .layoutPriority(1)
                .accessibilityLabel(label)
                .focused($focusedField, equals: focus)
                .onSubmit { advanceFocus(after: focus) }
        }
        .frame(height: ElectronicMailControlMetrics.composerFieldHeight)
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
        HStack(alignment: .firstTextBaseline, spacing: MailComposerLayout.fieldSpacing) {
            Text("Subject")
                .font(ElectronicMailComposerType.label())
                .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                .lineLimit(1)
                .frame(width: MailComposerLayout.fieldLabelWidth, alignment: .leading)

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
                bodyHTML = draft.bodyHTML
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
        bodyHTML = recovery.bodyHTML
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
            bodyHTML: bodyHTML,
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
                    bodyHTML: bodyHTML,
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
                    let message = response.error ?? "Draft could not be saved."
                    statusText = message
                    if response.state == .failed {
                        showDraftFailureToast(message)
                    }
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
            let message = "Draft could not be saved. \(error.localizedDescription)"
            statusText = message
            showDraftFailureToast(message)
            return nil
        }
    }

    @MainActor
    private func showDraftFailureToast(_ message: String) {
        draftFailureToastTask?.cancel()
        withAnimation(.easeInOut(duration: 0.15)) {
            draftFailureToast = message
        }
        draftFailureToastTask = Task { @MainActor in
            try? await Task.sleep(for: .seconds(8))
            guard !Task.isCancelled, draftFailureToast == message else { return }
            withAnimation(.easeInOut(duration: 0.15)) {
                draftFailureToast = nil
            }
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
    private func deleteOrDiscardDraft() async {
        guard !savingDraft, !loadingDraft, !sending else {
            return
        }
        guard gmailDraftID != nil else {
            finishComposer()
            return
        }
        await deleteDraft()
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

    var menuSymbol: String {
        switch self {
        case .reply: return "arrowshape.turn.up.left"
        case .replyAll: return "arrowshape.turn.up.left.2"
        case .forward: return "arrowshape.turn.up.right"
        case .compose, .draft: return "square.and.pencil"
        }
    }

}

private struct ComposerRichTextEditor: NSViewRepresentable {
    @Binding var text: String
    @Binding var html: String?
    let command: ComposerFormattingCommand?
    let shouldFocus: Bool
    let colorScheme: ColorScheme
    let onFocus: () -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(parent: self)
    }

    func makeNSView(context: Context) -> NSScrollView {
        let scrollView = NSScrollView()
        scrollView.drawsBackground = false
        scrollView.borderType = .noBorder
        scrollView.hasVerticalScroller = true
        scrollView.autohidesScrollers = true

        let textView = NSTextView()
        textView.delegate = context.coordinator
        textView.isRichText = true
        textView.importsGraphics = false
        textView.allowsUndo = true
        textView.drawsBackground = false
        textView.isHorizontallyResizable = false
        textView.isVerticallyResizable = true
        textView.autoresizingMask = [.width]
        textView.minSize = NSSize(width: 0, height: 0)
        textView.maxSize = NSSize(width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude)
        textView.textContainerInset = NSSize(
            width: ElectronicMailComposerEditorLayout.placeholderHorizontalInset,
            height: ElectronicMailComposerEditorLayout.placeholderVerticalInset
        )
        textView.textContainer?.lineFragmentPadding = 0
        textView.textContainer?.widthTracksTextView = true
        textView.font = NSFont.systemFont(ofSize: ElectronicMailComposerType.bodySize)
        textView.textColor = .textColor
        textView.insertionPointColor = .textColor

        scrollView.documentView = textView
        context.coordinator.textView = textView
        context.coordinator.loadModel(text: text, html: html)
        return scrollView
    }

    func updateNSView(_ scrollView: NSScrollView, context: Context) {
        guard let textView = scrollView.documentView as? NSTextView else { return }
        context.coordinator.parent = self
        textView.textColor = .textColor
        textView.insertionPointColor = .textColor

        let signature = Coordinator.ModelSignature(text: text, html: html)
        if signature != context.coordinator.modelSignature {
            context.coordinator.loadModel(text: text, html: html)
        }

        if let command,
           command.id != context.coordinator.lastCommandID {
            context.coordinator.lastCommandID = command.id
            context.coordinator.apply(command.action)
        }

        if shouldFocus, textView.window?.firstResponder !== textView {
            Task { @MainActor in
                textView.window?.makeFirstResponder(textView)
            }
        }
    }

    final class Coordinator: NSObject, NSTextViewDelegate {
        struct ModelSignature: Equatable {
            let text: String
            let html: String?
        }

        var parent: ComposerRichTextEditor
        weak var textView: NSTextView?
        var modelSignature: ModelSignature?
        var lastCommandID: UUID?
        private var applyingModel = false

        init(parent: ComposerRichTextEditor) {
            self.parent = parent
        }

        func loadModel(text: String, html: String?) {
            guard let textView else { return }
            applyingModel = true
            defer { applyingModel = false }

            let attributedText: NSAttributedString
            if let html,
               !html.isEmpty,
               let data = html.data(using: .utf8),
               let decoded = try? NSAttributedString(
                   data: data,
                   options: [
                       .documentType: NSAttributedString.DocumentType.html,
                       .characterEncoding: String.Encoding.utf8.rawValue,
                   ],
                   documentAttributes: nil
               ) {
                attributedText = decoded
            } else {
                attributedText = NSAttributedString(
                    string: text,
                    attributes: [.font: NSFont.systemFont(ofSize: ElectronicMailComposerType.bodySize)]
                )
            }

            let displayText = NSMutableAttributedString(attributedString: attributedText)
            if displayText.length > 0 {
                let fullRange = NSRange(location: 0, length: displayText.length)
                displayText.removeAttribute(.foregroundColor, range: fullRange)
                displayText.addAttribute(.foregroundColor, value: NSColor.textColor, range: fullRange)
            }
            textView.textStorage?.setAttributedString(displayText)
            textView.typingAttributes[.font] = NSFont.systemFont(ofSize: ElectronicMailComposerType.bodySize)
            textView.setSelectedRange(NSRange(location: attributedText.length, length: 0))
            modelSignature = ModelSignature(text: text, html: html)
        }

        func textDidBeginEditing(_ notification: Notification) {
            parent.onFocus()
        }

        func textDidChange(_ notification: Notification) {
            guard !applyingModel else { return }
            synchronizeModel()
        }

        func apply(_ action: ComposerFormattingCommand.Action) {
            guard let textView else { return }
            textView.window?.makeFirstResponder(textView)

            switch action {
            case .fontName(let name):
                transformFonts { font in
                    if name == "System" {
                        return NSFont.systemFont(ofSize: font.pointSize)
                    }
                    return NSFont(name: name, size: font.pointSize) ?? font
                }
            case .fontSize(let size):
                transformFonts { font in
                    NSFontManager.shared.convert(font, toSize: size)
                }
            case .toggleBold:
                toggleFontTrait(.boldFontMask)
            case .toggleItalic:
                toggleFontTrait(.italicFontMask)
            case .toggleUnderline:
                toggleUnderline()
            case .alignment(let alignment):
                switch alignment {
                case .left:
                    textView.alignLeft(nil)
                case .center:
                    textView.alignCenter(nil)
                case .right:
                    textView.alignRight(nil)
                }
            case .bulletList:
                toggleBulletList()
            }
            synchronizeModel()
        }

        private func transformFonts(_ transform: (NSFont) -> NSFont) {
            guard let textView, let storage = textView.textStorage else { return }
            let selectedRange = textView.selectedRange()
            if selectedRange.length == 0 {
                let current = textView.typingAttributes[.font] as? NSFont
                    ?? textView.font
                    ?? NSFont.systemFont(ofSize: ElectronicMailComposerType.bodySize)
                textView.typingAttributes[.font] = transform(current)
                return
            }

            var updates: [(NSRange, NSFont)] = []
            storage.enumerateAttribute(.font, in: selectedRange) { value, range, _ in
                let font = value as? NSFont
                    ?? NSFont.systemFont(ofSize: ElectronicMailComposerType.bodySize)
                updates.append((range, transform(font)))
            }
            storage.beginEditing()
            for (range, font) in updates {
                storage.addAttribute(.font, value: font, range: range)
            }
            storage.endEditing()
        }

        private func toggleFontTrait(_ trait: NSFontTraitMask) {
            let manager = NSFontManager.shared
            transformFonts { font in
                if manager.traits(of: font).contains(trait) {
                    return manager.convert(font, toNotHaveTrait: trait)
                }
                return manager.convert(font, toHaveTrait: trait)
            }
        }

        private func toggleUnderline() {
            guard let textView, let storage = textView.textStorage else { return }
            let selectedRange = textView.selectedRange()
            if selectedRange.length == 0 {
                let current = textView.typingAttributes[.underlineStyle] as? Int ?? 0
                textView.typingAttributes[.underlineStyle] = current == 0
                    ? NSUnderlineStyle.single.rawValue
                    : 0
                return
            }
            let current = storage.attribute(
                .underlineStyle,
                at: selectedRange.location,
                effectiveRange: nil
            ) as? Int ?? 0
            storage.addAttribute(
                .underlineStyle,
                value: current == 0 ? NSUnderlineStyle.single.rawValue : 0,
                range: selectedRange
            )
        }

        private func toggleBulletList() {
            guard let textView else { return }
            let source = textView.string as NSString
            let selection = textView.selectedRange()
            let paragraphRange = source.paragraphRange(for: selection)
            let paragraph = source.substring(with: paragraphRange)
            let lines = paragraph.split(separator: "\n", omittingEmptySubsequences: false)
            let allBulleted = lines.allSatisfy { $0.hasPrefix("• ") || $0.isEmpty }
            let replacement = lines.map { line -> String in
                if line.isEmpty { return "" }
                if allBulleted { return String(line.dropFirst(2)) }
                return line.hasPrefix("• ") ? String(line) : "• \(line)"
            }
            .joined(separator: "\n")
            textView.insertText(replacement, replacementRange: paragraphRange)
        }

        private func synchronizeModel() {
            guard let textView else { return }
            let plainText = textView.string
            let html = exportedHTML(from: textView.attributedString())
            modelSignature = ModelSignature(text: plainText, html: html)
            if parent.text != plainText {
                parent.text = plainText
            }
            if parent.html != html {
                parent.html = html
            }
        }

        private func exportedHTML(from attributedString: NSAttributedString) -> String? {
            guard attributedString.length > 0 else { return nil }
            let exportCopy = NSMutableAttributedString(attributedString: attributedString)
            exportCopy.removeAttribute(
                .foregroundColor,
                range: NSRange(location: 0, length: exportCopy.length)
            )
            guard let data = try? exportCopy.data(
                      from: NSRange(location: 0, length: exportCopy.length),
                      documentAttributes: [
                          .documentType: NSAttributedString.DocumentType.html,
                          .characterEncoding: String.Encoding.utf8.rawValue,
                      ]
                  ) else {
                return nil
            }
            return String(data: data, encoding: .utf8)
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
                    .font(ElectronicMailType.headerTitle(weight: .semibold))
                Text("This removes your Electronic Mail account, synced email data, drafts stored by the app, and active sessions. Your messages in Gmail are not deleted.")
                    .font(ElectronicMailType.body())
                    .foregroundStyle(.secondary)
                Text("Type DELETE to confirm.")
                    .font(ElectronicMailType.detail(weight: .medium))
                TextField("DELETE", text: $deleteAccountConfirmation)
                    .textFieldStyle(.roundedBorder)
                    .font(ElectronicMailType.body())
                    .disabled(deletingAccount)
                if let deleteAccountError {
                    Text(deleteAccountError)
                        .font(ElectronicMailType.detail())
                        .foregroundStyle(.red)
                }
                HStack {
                    Spacer()
                    Button("Cancel", role: .cancel) {
                        confirmDeleteAccount = false
                    }
                    .frame(minHeight: ElectronicMailControlMetrics.actionHeight)
                    .disabled(deletingAccount)
                    Button("Delete Account", role: .destructive) {
                        deleteAccount()
                    }
                    .frame(minHeight: ElectronicMailControlMetrics.actionHeight)
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
