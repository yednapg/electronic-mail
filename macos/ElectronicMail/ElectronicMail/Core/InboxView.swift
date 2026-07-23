import SwiftUI

@MainActor
public struct InboxView: View {
    @Environment(\.colorScheme) private var colorScheme
    @ObservedObject private var store: InboxStore
    private let onRespond: (String, MailComposerMode, String) -> Void
    private let onOpenDraft: (String) -> Void
    @Binding private var searchText: String
    @State private var permanentDeleteTarget: MailboxPermanentDeleteTarget?
    @FocusState private var mailboxListIsFocused: Bool

    public init(
        store: InboxStore,
        searchText: Binding<String>,
        onRespond: @escaping (String, MailComposerMode, String) -> Void = { _, _, _ in },
        onOpenDraft: @escaping (String) -> Void = { _ in }
    ) {
        self.store = store
        self._searchText = searchText
        self.onRespond = onRespond
        self.onOpenDraft = onOpenDraft
    }

    public var body: some View {
        GeometryReader { proxy in
            let metrics = InboxLayoutMetrics(windowSize: proxy.size)

            ZStack {
                ElectronicMailDesign.background(for: colorScheme)
                    .ignoresSafeArea()

                if let readerThreadID = store.readerThreadID {
                    EmailReaderView(
                        threadID: readerThreadID,
                        focusedMessageID: store.readerFocusedMessageID,
                        thread: store.readerThread,
                        row: store.readerRow,
                        errorMessage: store.readerError,
                        currentUserDisplayName: store.session?.user.displayName ?? store.session?.user.firstName,
                        currentUserEmail: store.session?.user.email,
                        colorScheme: colorScheme,
                        mailboxLabel: store.activeMailboxLabel,
                        onRetry: {
                            Task {
                                await store.prefetchThread(threadID: readerThreadID, force: true, silent: false)
                            }
                        },
                        onRespond: { mode, messageID in onRespond(readerThreadID, mode, messageID) },
                        onThreadAction: { action, messageID in
                            Task {
                                await store.performReaderThreadAction(action, threadID: readerThreadID, messageID: messageID)
                            }
                        },
                        onOpenAttachment: { attachment, messageID in
                            Task {
                                await store.openAttachment(attachment, messageID: messageID)
                            }
                        }
                    )
                    .onExitCommand {
                        closeReader()
                    }
                } else {
                    let snapshot = InboxRenderSnapshot(store: store)
                    inboxList(snapshot: snapshot, metrics: metrics)
                }

                if store.refreshFailed {
                    ElectronicMailRefreshFailureToast(message: "Inbox could not refresh. Showing last saved state.")
                }
            }
        }
        .task {
            if store.session == nil {
                await store.load()
            }
        }
        .onAppear {
            searchText = store.searchQuery
        }
        .onChange(of: store.activeMailboxLabel) { _, _ in
            searchText = ""
        }
        .task(id: InboxSearchTaskIdentity(query: searchText, mailboxLabel: store.activeMailboxLabel)) {
            await applySearchText()
        }
        .confirmationDialog(
            "Delete this email permanently?",
            isPresented: Binding(
                get: { permanentDeleteTarget != nil },
                set: { isPresented in
                    if !isPresented {
                        permanentDeleteTarget = nil
                    }
                }
            ),
            titleVisibility: .visible
        ) {
            Button("Delete Permanently", role: .destructive) {
                guard let target = permanentDeleteTarget else {
                    return
                }
                permanentDeleteTarget = nil
                Task {
                    await store.performTargetedThreadAction(
                        .deleteForever,
                        threadID: target.threadID,
                        messageID: target.messageID
                    )
                }
            }
            Button("Cancel", role: .cancel) {
                permanentDeleteTarget = nil
            }
        } message: {
            Text("This cannot be undone.")
        }
        .alert(
            "Could Not Open Attachment",
            isPresented: Binding(
                get: { store.attachmentErrorMessage != nil },
                set: { isPresented in
                    if !isPresented {
                        store.dismissAttachmentError()
                    }
                }
            )
        ) {
            Button("OK") { store.dismissAttachmentError() }
        } message: {
            Text(store.attachmentErrorMessage ?? "Try again.")
        }
    }

    private func inboxList(snapshot: InboxRenderSnapshot, metrics: InboxLayoutMetrics) -> some View {
        ScrollViewReader { scrollProxy in
            ScrollView(.vertical) {
                LazyVStack(alignment: .leading, spacing: 0) {
                    if !snapshot.hasRows {
                        InboxEmptyState(
                            title: snapshot.emptyStateTitle,
                            message: snapshot.emptyStateMessage,
                            isLoading: snapshot.emptyStateIsLoading,
                            showsRetry: snapshot.emptyStateShowsRetry,
                            colorScheme: colorScheme,
                            onRetry: {
                                Task {
                                    if snapshot.isSearchActive {
                                        await store.searchMailbox(snapshot.searchQuery)
                                    } else {
                                        await store.syncNow()
                                    }
                                }
                            }
                        )
                        .frame(width: metrics.windowWidth)
                    }

                    ForEach(snapshot.sections) { section in
                        InboxSectionHeader(
                            title: section.title,
                            isFirstSection: section.isFirstSection,
                            metrics: metrics,
                            colorScheme: colorScheme
                        )
                        .equatable()

                        ForEach(section.rows) { row in
                            InboxRowView(
                                row: row,
                                metrics: metrics,
                                colorScheme: colorScheme,
                                actionHint: snapshot.mailboxLabel == .drafts ? "Open draft" : "Open email",
                                onSelect: { activate(row: row) },
                                onToggleExpansion: row.isExpandable ? { store.toggleExpansion(threadID: row.threadID) } : nil
                            )
                            .equatable()
                            .id(row.id)
                        }
                    }

                    if let footer = snapshot.footer {
                        InboxFooterView(
                            text: footer.text,
                            canLoadMore: footer.canLoadMore,
                            isLoading: snapshot.mailboxPageLoading,
                            metrics: metrics,
                            colorScheme: colorScheme,
                            onLoadMore: {
                                Task {
                                    await store.loadMoreMailbox()
                                }
                            }
                        )
                    }
                }
                .frame(width: metrics.windowWidth, alignment: .topLeading)
            }
            .scrollIndicators(.automatic)
            .focusable()
            .focusEffectDisabled()
            .focused($mailboxListIsFocused)
            .onAppear {
                mailboxListIsFocused = true
            }
            .onKeyPress(.upArrow) {
                moveSelection(by: -1, snapshot: snapshot, scrollProxy: scrollProxy)
                return .handled
            }
            .onKeyPress(.downArrow) {
                moveSelection(by: 1, snapshot: snapshot, scrollProxy: scrollProxy)
                return .handled
            }
            .onKeyPress(.return) {
                openActiveSelection()
                return .handled
            }
            .onKeyPress(.delete, phases: .down) { keyPress in
                guard keyPress.modifiers.contains(.command) else {
                    return .ignored
                }
                requestPermanentDelete()
                return .handled
            }
            .onDeleteCommand {
                moveSelectedThreadToTrash()
            }
            .transaction { transaction in
                transaction.disablesAnimations = true
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        }
    }

    private func moveSelection(
        by delta: Int,
        snapshot: InboxRenderSnapshot,
        scrollProxy: ScrollViewProxy
    ) {
        guard !snapshot.flatRows.isEmpty else {
            return
        }
        let currentIndex = snapshot.selectedRowID.flatMap { selectedID in
            snapshot.flatRows.firstIndex(where: { $0.id == selectedID })
        }
        let fallbackIndex = delta > 0 ? -1 : snapshot.flatRows.count
        let nextIndex = min(
            max((currentIndex ?? fallbackIndex) + delta, 0),
            snapshot.flatRows.count - 1
        )
        let nextRow = snapshot.flatRows[nextIndex]
        select(row: nextRow)
        scrollProxy.scrollTo(nextRow.id, anchor: .center)
    }

    private func applySearchText() async {
        let query = searchText.trimmingCharacters(in: .whitespacesAndNewlines)
        if query.isEmpty {
            if store.isSearchActive {
                store.clearSearch()
            }
            return
        }
        try? await Task.sleep(nanoseconds: 350_000_000)
        guard !Task.isCancelled else { return }
        await store.searchMailbox(query)
    }

    private func activate(row: InboxRowViewModel) {
        select(row: row)
        if store.activeMailboxLabel == .drafts {
            onOpenDraft(row.threadID)
            return
        }
        _ = store.openReader(threadID: row.threadID, focusedMessageID: row.focusedMessageID)
    }

    private func closeReader() {
        store.closeReader()
    }

    private func openActiveSelection() {
        if store.activeMailboxLabel == .drafts, let threadID = store.selectedThreadID {
            onOpenDraft(threadID)
            return
        }
        store.openActiveSelection()
    }

    private func moveSelectedThreadToTrash() {
        guard store.activeThreadID != nil else {
            return
        }
        Task {
            await store.performSelectedThreadAction(.moveTrash)
        }
    }

    private func requestPermanentDelete() {
        guard let threadID = store.activeThreadID else {
            return
        }
        permanentDeleteTarget = MailboxPermanentDeleteTarget(
            threadID: threadID,
            messageID: store.activeMessageID
        )
    }

    private func select(row: InboxRowViewModel) {
        var transaction = Transaction()
        transaction.disablesAnimations = true
        withTransaction(transaction) {
            store.select(threadID: row.threadID, focusedMessageID: row.focusedMessageID, prefetch: false)
        }
    }
}

private struct MailboxPermanentDeleteTarget: Equatable {
    let threadID: String
    let messageID: String?
}

@MainActor
private struct InboxRenderSnapshot {
    let sections: [InboxRenderSection]
    let flatRows: [InboxRowViewModel]
    let selectedRowID: String?
    let hasRows: Bool
    let footer: InboxMailboxFooterViewModel?
    let mailboxPageLoading: Bool
    let mailboxLabel: MailboxLabel
    let mailboxTitle: String
    let isSearchActive: Bool
    let searchQuery: String
    let searchError: String?
    let emptyStateIsLoading: Bool
    let mailboxError: String?
    let mailboxRefreshFailed: Bool

    @MainActor
    init(store: InboxStore) {
        let sections = store.sections
        var renderSections: [InboxRenderSection] = []
        var flatRows: [InboxRowViewModel] = []
        var isFirstVisibleSection = true

        for section in sections where !section.rows.isEmpty {
            renderSections.append(
                InboxRenderSection(
                    id: section.id,
                    title: section.title,
                    isFirstSection: isFirstVisibleSection,
                    rows: section.rows
                )
            )
            flatRows.append(contentsOf: section.rows)
            isFirstVisibleSection = false
        }

        self.sections = renderSections
        self.flatRows = flatRows
        self.selectedRowID = flatRows.first(where: \.isSelected)?.id
        self.hasRows = !flatRows.isEmpty
        self.footer = store.mailboxFooter
        self.mailboxPageLoading = store.mailboxPageLoading
        self.mailboxLabel = store.activeMailboxLabel
        self.mailboxTitle = store.mailboxTitle
        self.isSearchActive = store.isSearchActive
        self.searchQuery = store.searchQuery
        self.searchError = store.searchError
        self.emptyStateIsLoading = store.searchInProgress || store.phase == .loading
        if case .failed(let message) = store.phase {
            self.mailboxError = message
        } else {
            self.mailboxError = nil
        }
        self.mailboxRefreshFailed = store.refreshFailed
    }

    var emptyStateTitle: String {
        if isSearchActive {
            return searchError == nil ? "No matching email" : "Search unavailable"
        }
        if mailboxError != nil || mailboxRefreshFailed {
            return "Mailbox unavailable"
        }
        return "No email in \(mailboxTitle)"
    }

    var emptyStateMessage: String {
        if let searchError {
            return searchError
        }
        if isSearchActive {
            return "Try a different sender, subject, or phrase."
        }
        if let mailboxError {
            return mailboxError
        }
        if mailboxRefreshFailed {
            return "Could not refresh this mailbox. Try again."
        }
        return "This mailbox is up to date."
    }

    var emptyStateShowsRetry: Bool {
        searchError != nil || mailboxError != nil || mailboxRefreshFailed
    }
}

private struct InboxRenderSection: Identifiable, Equatable {
    let id: String
    let title: String
    let isFirstSection: Bool
    let rows: [InboxRowViewModel]
}

private struct InboxSearchTaskIdentity: Equatable {
    let query: String
    let mailboxLabel: MailboxLabel

    init(query: String, mailboxLabel: MailboxLabel) {
        self.query = query.trimmingCharacters(in: .whitespacesAndNewlines)
        self.mailboxLabel = mailboxLabel
    }
}

private struct InboxEmptyState: View {
    let title: String
    let message: String
    let isLoading: Bool
    let showsRetry: Bool
    let colorScheme: ColorScheme
    let onRetry: () -> Void

    var body: some View {
        VStack(spacing: 12) {
            if isLoading {
                ProgressView().controlSize(.small)
            }
            Text(isLoading ? "Loading mail..." : title)
                .font(ElectronicMailType.sectionTitle())
                .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
            Text(message)
                .font(ElectronicMailType.body())
                .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
            if !isLoading && showsRetry {
                Button("Try Again", action: onRetry)
                    .buttonStyle(.plain)
                    .font(ElectronicMailType.small(weight: .semibold))
                    .foregroundStyle(ElectronicMailDesign.appleBlue)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 48)
    }
}

private struct InboxFooterView: View {
    let text: String
    let canLoadMore: Bool
    let isLoading: Bool
    let metrics: InboxLayoutMetrics
    let colorScheme: ColorScheme
    let onLoadMore: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            if isLoading {
                ProgressView()
                    .controlSize(.small)
                    .frame(width: 18, height: 18)
            }

            Button(action: onLoadMore) {
                Text(text)
                    .font(.system(size: 13, weight: canLoadMore ? .semibold : .regular, design: .rounded))
                    .tracking(ElectronicMailTypography.bodyTracking)
                    .foregroundStyle(canLoadMore ? ElectronicMailDesign.appleBlue : ElectronicMailDesign.secondaryText(for: colorScheme))
                    .lineLimit(1)
                    .truncationMode(.tail)
            }
            .buttonStyle(.plain)
            .disabled(!canLoadMore || isLoading)
            .help(canLoadMore ? "Load more emails" : text)
        }
        .frame(
            width: max(0, metrics.windowWidth - metrics.contentLeading - metrics.dividerTrailing),
            height: ElectronicMailTypography.bodyLineHeight * 2,
            alignment: .leading
        )
        .padding(.leading, metrics.contentLeading)
    }
}

private struct InboxSectionHeader: View, Equatable {
    let title: String
    let isFirstSection: Bool
    let metrics: InboxLayoutMetrics
    let colorScheme: ColorScheme

    var body: some View {
        let topSpacing = metrics.sectionTopSpacing(isFirst: isFirstSection)

        VStack(alignment: .leading, spacing: 0) {
            Text(title)
                .font(ElectronicMailMailboxType.section())
                .foregroundStyle(ElectronicMailDesign.sectionText(for: colorScheme))
                .lineLimit(1)
                .frame(height: metrics.sectionLabelHeight, alignment: .leading)
                .accessibilityAddTraits(.isHeader)

            Divider()
        }
        .padding(.leading, metrics.contentLeading)
        .padding(.trailing, metrics.dividerTrailing)
        .padding(.top, topSpacing)
        .frame(
            width: metrics.windowWidth,
            height: topSpacing + metrics.sectionLabelHeight + 1,
            alignment: .topLeading
        )
    }
}

private struct InboxRowView: View, Equatable {
    let row: InboxRowViewModel
    let metrics: InboxLayoutMetrics
    let colorScheme: ColorScheme
    let actionHint: String
    let onSelect: () -> Void
    let onToggleExpansion: (() -> Void)?

    static func == (lhs: InboxRowView, rhs: InboxRowView) -> Bool {
        lhs.row == rhs.row
            && lhs.metrics == rhs.metrics
            && lhs.colorScheme == rhs.colorScheme
            && lhs.actionHint == rhs.actionHint
    }

    var body: some View {
        ZStack(alignment: .leading) {
            Button(action: onSelect) {
                HStack(spacing: 0) {
                    Color.clear.frame(width: metrics.contentLeading)
                    Color.clear.frame(width: metrics.disclosureWidth)
                    Color.clear.frame(width: metrics.disclosureSenderGap)

                    rowText(
                        row.sender,
                        alignment: .leading,
                        font: ElectronicMailMailboxType.sender(unread: row.isUnread),
                        color: senderTextColor
                    )
                    .padding(.leading, row.isChild ? metrics.childIndent : 0)
                    .frame(width: metrics.senderWidth, alignment: .leading)
                    .clipped()

                    Color.clear.frame(width: metrics.senderSubjectGap)

                    rowText(
                        row.title,
                        alignment: .leading,
                        font: ElectronicMailMailboxType.subject(unread: row.isUnread),
                        color: subjectTextColor
                    )
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .clipped()

                    Color.clear.frame(width: metrics.subjectAttachmentGap)

                    Group {
                        if row.hasAttachments {
                            Image(systemName: "paperclip")
                                .font(.system(size: metrics.attachmentIconSize, weight: .regular))
                                .symbolRenderingMode(.monochrome)
                                .foregroundStyle(attachmentIconColor)
                        } else {
                            Color.clear
                        }
                    }
                    .frame(width: metrics.attachmentWidth, alignment: .center)

                    Color.clear.frame(width: metrics.attachmentTimeGap)

                    rowText(
                        row.timeLabel,
                        alignment: .trailing,
                        font: ElectronicMailMailboxType.metadata(unread: row.isUnread),
                        color: metadataTextColor
                    )
                    .frame(width: metrics.timeWidth, alignment: .trailing)
                    .clipped()

                    Color.clear.frame(width: metrics.trailingInset)
                }
                .frame(width: metrics.windowWidth, height: ElectronicMailTypography.bodyLineHeight)
                .background(row.isSelected ? ElectronicMailDesign.appleBlue : Color.clear)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel(accessibilityLabel)
            .accessibilityHint(actionHint)
            .accessibilityAddTraits(row.isSelected ? .isSelected : [])

            if row.isExpandable, let onToggleExpansion {
                Button(action: onToggleExpansion) {
                    Image(systemName: row.isExpanded ? "chevron.down" : "chevron.right")
                        .font(.system(size: 11, weight: .semibold))
                        .symbolRenderingMode(.monochrome)
                        .foregroundStyle(
                            row.isSelected
                                ? ElectronicMailDesign.selectedText(for: colorScheme)
                                : ElectronicMailDesign.appleBlue
                        )
                        .frame(width: metrics.disclosureWidth, height: ElectronicMailTypography.bodyLineHeight)
                }
                .buttonStyle(.plain)
                .offset(x: metrics.contentLeading)
                .accessibilityLabel(row.isExpanded ? "Collapse conversation" : "Expand conversation")
                .accessibilityHint("Shows or hides messages in this conversation")
            }
        }
        .frame(width: metrics.windowWidth, height: ElectronicMailTypography.bodyLineHeight, alignment: .topLeading)
    }

    private var accessibilityLabel: String {
        var parts = [row.isUnread ? "Unread" : "Read", row.sender, row.title, row.timeLabel]
        if row.hasAttachments {
            parts.append("Has attachments")
        }
        if row.messageCount > 1 {
            parts.append("\(row.messageCount) messages")
        }
        if row.isExpandable {
            parts.append(row.isExpanded ? "Expanded" : "Collapsed")
        }
        return parts.joined(separator: ", ")
    }

    private func rowText(_ value: String, alignment: Alignment, font: Font, color: Color) -> some View {
        Text(value)
            .font(font)
            .tracking(ElectronicMailTypography.bodyTracking)
            .foregroundStyle(color)
            .lineLimit(1)
            .truncationMode(.tail)
            .multilineTextAlignment(alignment == .trailing ? .trailing : .leading)
    }

    private var senderTextColor: Color {
        if row.isSelected {
            return ElectronicMailDesign.selectedText(for: colorScheme)
        }
        return row.isUnread
            ? ElectronicMailDesign.unreadText(for: colorScheme)
            : ElectronicMailDesign.readText(for: colorScheme)
    }

    private var subjectTextColor: Color {
        if row.isSelected {
            return ElectronicMailDesign.selectedText(for: colorScheme)
        }
        return row.isUnread
            ? ElectronicMailDesign.unreadText(for: colorScheme)
            : ElectronicMailDesign.readText(for: colorScheme)
    }

    private var metadataTextColor: Color {
        if row.isSelected {
            return ElectronicMailDesign.selectedText(for: colorScheme).opacity(0.82)
        }
        return ElectronicMailDesign.tertiaryText(for: colorScheme)
    }

    private var attachmentIconColor: Color {
        if row.isSelected {
            return ElectronicMailDesign.selectedText(for: colorScheme).opacity(0.86)
        }

        return ElectronicMailDesign.secondaryText(for: colorScheme).opacity(0.72)
    }
}

private enum ElectronicMailTypography {
    static let bodyTracking: CGFloat = 0.05
    static let bodyLineHeight = ElectronicMailMailboxType.rowHeight
}

private struct InboxLayoutMetrics: Equatable {
    let windowWidth: CGFloat

    let contentLeading: CGFloat = 28
    let disclosureWidth: CGFloat = 18
    let disclosureSenderGap: CGFloat = 8
    let senderWidth: CGFloat = 260
    let senderSubjectGap: CGFloat = 20
    let subjectAttachmentGap: CGFloat = 12
    let attachmentWidth: CGFloat = 16
    let attachmentIconSize: CGFloat = 13
    let attachmentTimeGap: CGFloat = 12
    let timeWidth: CGFloat = 72
    let trailingInset: CGFloat = 28
    let dividerTrailing: CGFloat = 28
    let childIndent: CGFloat = 14
    let sectionLabelHeight: CGFloat = 20

    init(windowSize: CGSize) {
        windowWidth = windowSize.width
    }

    func sectionTopSpacing(isFirst: Bool) -> CGFloat {
        isFirst ? 10 : 18
    }

}

#if DEBUG
#Preview {
    InboxView(store: InboxStore(client: DemoAppClient()), searchText: .constant(""))
        .frame(width: 1440, height: 900)
}
#endif
