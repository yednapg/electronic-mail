import AppKit
import Combine
import Foundation
import SwiftUI

@MainActor
public struct InboxView: View {
    @Environment(\.colorScheme) private var colorScheme
    @ObservedObject private var store: InboxStore
    private let onRespond: (String, MailComposerMode, String) -> Void
    private let onOpenDraft: (String) -> Void
    @Binding private var searchText: String
    @State private var permanentDeleteTarget: MailboxPermanentDeleteTarget?
    @State private var offlineStorageWarningMessage: String?

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
                        },
                        isAttachmentDownloading: { attachment, messageID in
                            store.isAttachmentDownloading(attachment, messageID: messageID)
                        }
                    )
                    .onExitCommand {
                        closeReader()
                    }
                } else {
                    let snapshot = InboxRenderSnapshot(store: store)
                    inboxList(snapshot: snapshot, metrics: metrics)
                }

                if let offlineStorageWarningMessage {
                    ElectronicMailRefreshFailureToast(message: offlineStorageWarningMessage)
                } else if store.refreshFailed && store.mailboxPresentationReady {
                    ElectronicMailRefreshFailureToast(message: "Inbox could not refresh. Showing last saved state.")
                }
            }
        }
        .task {
            await Task.yield()
            if searchText != store.searchQuery {
                searchText = store.searchQuery
            }
            if store.session == nil {
                await store.load()
            }
        }
        .onChange(of: store.activeMailboxLabel) { _, _ in
            searchText = ""
        }
        .onReceive(NotificationCenter.default.publisher(for: .offlineContentSyncStorageWarning)) { notification in
            let warningUserID = notification.userInfo?["user_id"] as? String
            guard warningUserID == nil || warningUserID == store.session?.user.id else {
                return
            }
            let message = notification.userInfo?["message"] as? String
                ?? "Offline mail could not be saved. Online mail is still available."
            offlineStorageWarningMessage = message
            Task { @MainActor in
                try? await Task.sleep(for: .seconds(8))
                if offlineStorageWarningMessage == message {
                    offlineStorageWarningMessage = nil
                }
            }
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
        InboxMailboxList(
            snapshot: snapshot,
            metrics: metrics,
            colorScheme: colorScheme,
            onRetry: {
                Task {
                    if snapshot.isSearchActive {
                        await store.searchMailbox(snapshot.searchQuery)
                    } else {
                        await store.syncNow()
                    }
                }
            },
            onActivate: activate(row:),
            onSelect: select(row:),
            onToggleExpansion: { store.toggleExpansion(threadID: $0) },
            onOpenSelection: openActiveSelection,
            onMoveToTrash: moveSelectedThreadToTrash,
            onPermanentDelete: requestPermanentDelete
        )
        .equatable()
    }

    private func applySearchText() async {
        let query = searchText.trimmingCharacters(in: .whitespacesAndNewlines)
        if query.isEmpty {
            if store.isSearchActive {
                store.clearSearch()
            }
            return
        }
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

private struct InboxRenderSnapshot: Equatable {
    let storeIdentity: ObjectIdentifier
    let revision: UInt
    let chronologyDay: Date
    let chronologyCalendarIdentifier: String
    let chronologyTimeZoneIdentifier: String
    let chronologyLocaleIdentifier: String
    let sections: [InboxRenderSection]
    let flatRows: [InboxRowViewModel]
    let selectedRowID: String?
    let hasRows: Bool
    let mailboxPresentationReady: Bool
    let mailboxLoadingMessage: String?
    let mailboxLabel: MailboxLabel
    let mailboxTitle: String
    let isSearchActive: Bool
    let searchQuery: String
    let searchError: String?
    let emptyStateIsLoading: Bool
    let mailboxError: String?
    let mailboxRefreshFailed: Bool
    let footerProgressText: String?
    let footerShowsProgress: Bool
    let footerShowsRetry: Bool

    @MainActor
    init(store: InboxStore) {
        self.storeIdentity = ObjectIdentifier(store)
        self.revision = store.inboxPresentationRevision
        let now = Date()
        let calendar = Calendar.autoupdatingCurrent
        let locale = Locale.autoupdatingCurrent
        self.chronologyDay = calendar.startOfDay(for: now)
        self.chronologyCalendarIdentifier = String(describing: calendar.identifier)
        self.chronologyTimeZoneIdentifier = calendar.timeZone.identifier
        self.chronologyLocaleIdentifier = locale.identifier
        self.mailboxPresentationReady = store.mailboxPresentationReady
        if mailboxPresentationReady {
            self.sections = InboxChronologyPresenter.sections(
                from: store.sections,
                cacheOwner: store,
                revision: self.revision,
                now: now,
                calendar: calendar,
                locale: locale
            )
            self.flatRows = store.flatRows
        } else {
            self.sections = []
            self.flatRows = []
        }
        if let selectedThreadID = store.selectedThreadID {
            if let selectedMessageID = store.selectedMessageID {
                self.selectedRowID = "\(selectedThreadID)::message::\(selectedMessageID)"
            } else {
                self.selectedRowID = selectedThreadID
            }
        } else {
            self.selectedRowID = nil
        }
        self.hasRows = !self.flatRows.isEmpty
        self.mailboxLoadingMessage = store.mailboxLoadingProgressText
        self.mailboxLabel = store.activeMailboxLabel
        self.mailboxTitle = store.mailboxTitle
        self.isSearchActive = store.isSearchActive
        self.searchQuery = store.searchQuery
        self.searchError = store.searchError
        self.emptyStateIsLoading = store.searchInProgress
            || store.phase == .loading
            || (!mailboxPresentationReady && store.searchError == nil && !store.refreshFailed)
        if case .failed(let message) = store.phase {
            self.mailboxError = message
        } else {
            self.mailboxError = nil
        }
        self.mailboxRefreshFailed = store.refreshFailed
        self.footerProgressText = store.mailboxFooterProgressText
        self.footerShowsProgress = store.mailboxFooterShowsProgress
        self.footerShowsRetry = store.mailboxFooterShowsRetry
    }

    var emptyStateTitle: String {
        if emptyStateIsLoading {
            return isSearchActive ? "Searching" : "Loading \(mailboxTitle)"
        }
        if isSearchActive {
            return searchError == nil ? "No matching email" : "Search unavailable"
        }
        if mailboxError != nil || mailboxRefreshFailed {
            return "Mailbox unavailable"
        }
        return "No email in \(mailboxTitle)"
    }

    var emptyStateMessage: String {
        if emptyStateIsLoading {
            return mailboxLoadingMessage ?? (isSearchActive ? "Searching your mailbox..." : "Loading your mailbox...")
        }
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

    static func == (lhs: InboxRenderSnapshot, rhs: InboxRenderSnapshot) -> Bool {
        lhs.storeIdentity == rhs.storeIdentity
            && lhs.revision == rhs.revision
            && lhs.chronologyDay == rhs.chronologyDay
            && lhs.chronologyCalendarIdentifier == rhs.chronologyCalendarIdentifier
            && lhs.chronologyTimeZoneIdentifier == rhs.chronologyTimeZoneIdentifier
            && lhs.chronologyLocaleIdentifier == rhs.chronologyLocaleIdentifier
            && lhs.selectedRowID == rhs.selectedRowID
            && lhs.mailboxPresentationReady == rhs.mailboxPresentationReady
            && lhs.mailboxLoadingMessage == rhs.mailboxLoadingMessage
            && lhs.mailboxLabel == rhs.mailboxLabel
            && lhs.mailboxTitle == rhs.mailboxTitle
            && lhs.isSearchActive == rhs.isSearchActive
            && lhs.searchQuery == rhs.searchQuery
            && lhs.searchError == rhs.searchError
            && lhs.emptyStateIsLoading == rhs.emptyStateIsLoading
            && lhs.mailboxError == rhs.mailboxError
            && lhs.mailboxRefreshFailed == rhs.mailboxRefreshFailed
            && lhs.footerProgressText == rhs.footerProgressText
            && lhs.footerShowsProgress == rhs.footerShowsProgress
            && lhs.footerShowsRetry == rhs.footerShowsRetry
    }
}

private struct InboxMailboxList: View, Equatable {
    let snapshot: InboxRenderSnapshot
    let metrics: InboxLayoutMetrics
    let colorScheme: ColorScheme
    let onRetry: () -> Void
    let onActivate: (InboxRowViewModel) -> Void
    let onSelect: (InboxRowViewModel) -> Void
    let onToggleExpansion: (String) -> Void
    let onOpenSelection: () -> Void
    let onMoveToTrash: () -> Void
    let onPermanentDelete: () -> Void
    @FocusState private var isFocused: Bool

    static func == (lhs: InboxMailboxList, rhs: InboxMailboxList) -> Bool {
        lhs.snapshot == rhs.snapshot
            && lhs.metrics == rhs.metrics
            && lhs.colorScheme == rhs.colorScheme
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Color.clear
                .frame(height: ElectronicMailShellMetrics.contentTop)
                .accessibilityHidden(true)

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
                                onRetry: onRetry
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
                                    isSelected: snapshot.selectedRowID == row.id,
                                    metrics: metrics,
                                    colorScheme: colorScheme,
                                    actionHint: snapshot.mailboxLabel == .drafts ? "Open draft" : "Open email",
                                    onSelect: { onActivate(row) },
                                    onToggleExpansion: row.isExpandable ? { onToggleExpansion(row.threadID) } : nil
                                )
                                .equatable()
                                .id(row.id)
                            }
                        }

                        if snapshot.hasRows, let footerText = snapshot.footerProgressText {
                            InboxSyncFooter(
                                message: footerText,
                                showsProgress: snapshot.footerShowsProgress,
                                showsRetry: snapshot.footerShowsRetry,
                                colorScheme: colorScheme,
                                onRetry: onRetry
                            )
                            .frame(width: metrics.windowWidth)
                            .id("mailbox-sync-footer")
                        }

                    }
                    .frame(width: metrics.windowWidth, alignment: .topLeading)
                }
                .scrollIndicators(.automatic)
                .focusable()
                .focusEffectDisabled()
                .focused($isFocused)
                .defaultFocus($isFocused, true)
                .onKeyPress(.upArrow) {
                    moveSelection(by: -1, scrollProxy: scrollProxy)
                    return .handled
                }
                .onKeyPress(.downArrow) {
                    moveSelection(by: 1, scrollProxy: scrollProxy)
                    return .handled
                }
                .onKeyPress(.return) {
                    onOpenSelection()
                    return .handled
                }
                .onKeyPress(.delete, phases: .down) { keyPress in
                    guard keyPress.modifiers.contains(.command) else {
                        return .ignored
                    }
                    onPermanentDelete()
                    return .handled
                }
                .onDeleteCommand(perform: onMoveToTrash)
                .background {
                    InboxKeyboardNavigationCapture { delta in
                        moveSelection(by: delta, scrollProxy: scrollProxy)
                    }
                    .frame(width: 0, height: 0)
                }
                .transaction { transaction in
                    transaction.disablesAnimations = true
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            }
        }
    }

    private func moveSelection(by delta: Int, scrollProxy: ScrollViewProxy) {
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
        onSelect(nextRow)
        scrollProxy.scrollTo(nextRow.id, anchor: .center)
    }
}

enum InboxKeyboardNavigationPolicy {
    static func selectionDelta(
        keyCode: UInt16,
        modifiers: NSEvent.ModifierFlags,
        isTextEditing: Bool
    ) -> Int? {
        guard !isTextEditing else {
            return nil
        }

        let navigationBlockingModifiers: NSEvent.ModifierFlags = [.command, .control, .option, .shift]
        guard modifiers.intersection(navigationBlockingModifiers).isEmpty else {
            return nil
        }

        switch keyCode {
        case 126:
            return -1
        case 125:
            return 1
        default:
            return nil
        }
    }
}

private struct InboxKeyboardNavigationCapture: NSViewRepresentable {
    let onMove: (Int) -> Void

    func makeNSView(context: Context) -> NavigationView {
        let view = NavigationView()
        view.onMove = onMove
        return view
    }

    func updateNSView(_ nsView: NavigationView, context: Context) {
        nsView.onMove = onMove
        nsView.installMonitorIfNeeded()
    }

    static func dismantleNSView(_ nsView: NavigationView, coordinator: ()) {
        nsView.removeMonitor()
    }

    final class NavigationView: NSView {
        var onMove: ((Int) -> Void)?
        private var monitor: Any?

        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            if window == nil {
                removeMonitor()
            } else {
                installMonitorIfNeeded()
            }
        }

        deinit {
            removeMonitor()
        }

        func installMonitorIfNeeded() {
            guard monitor == nil, window != nil else {
                return
            }

            monitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
                self?.handle(event) ?? event
            }
        }

        func removeMonitor() {
            if let monitor {
                NSEvent.removeMonitor(monitor)
                self.monitor = nil
            }
        }

        private func handle(_ event: NSEvent) -> NSEvent? {
            guard
                let window,
                event.windowNumber == window.windowNumber,
                window.isKeyWindow
            else {
                return event
            }

            let responder = window.firstResponder
            let isTextEditing = responder is NSTextView || responder is NSTextField
            guard let delta = InboxKeyboardNavigationPolicy.selectionDelta(
                keyCode: event.keyCode,
                modifiers: event.modifierFlags,
                isTextEditing: isTextEditing
            ) else {
                return event
            }

            onMove?(delta)
            return nil
        }
    }
}

private struct InboxSyncFooter: View {
    let message: String
    let showsProgress: Bool
    let showsRetry: Bool
    let colorScheme: ColorScheme
    let onRetry: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            if showsProgress {
                ProgressView()
                    .controlSize(.small)
                    .accessibilityHidden(true)
            }
            Text(message)
                .font(ElectronicMailType.small())
                .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                .lineLimit(1)
            Spacer(minLength: 12)
            if showsRetry {
                Button("Retry", action: onRetry)
                    .padding(.horizontal, 14)
                    .frame(minHeight: ElectronicMailControlMetrics.actionHeight)
                    .buttonStyle(.bordered)
                    .buttonBorderShape(.capsule)
                    .font(ElectronicMailType.small(weight: .semibold))
            }
        }
        .padding(.horizontal, 28)
        .padding(.vertical, 18)
        .transaction { transaction in
            transaction.disablesAnimations = true
        }
        .accessibilityElement(children: .combine)
    }
}

struct InboxRenderSection: Identifiable, Equatable {
    let id: String
    let title: String
    let isFirstSection: Bool
    let rows: [InboxRowViewModel]
}

@MainActor
enum InboxChronologyPresenter {
    private static let cacheCapacity = 4
    private static var cache: [CacheEntry] = []
    private(set) static var cacheMissCount: UInt = 0
    static var cacheEntryCount: Int { cache.count }

    static func sections(
        from sourceSections: [InboxSectionViewModel],
        cacheOwner: AnyObject,
        revision: UInt,
        now: Date,
        calendar: Calendar,
        locale: Locale
    ) -> [InboxRenderSection] {
        cache.removeAll(where: { $0.owner == nil })
        let startOfToday = calendar.startOfDay(for: now)
        let key = CacheKey(
            storeIdentity: ObjectIdentifier(cacheOwner),
            revision: revision,
            startOfToday: startOfToday,
            calendarIdentifier: String(describing: calendar.identifier),
            timeZoneIdentifier: calendar.timeZone.identifier,
            localeIdentifier: locale.identifier
        )
        if let cachedIndex = cache.firstIndex(where: { $0.owner === cacheOwner && $0.key == key }) {
            let cached = cache.remove(at: cachedIndex)
            cache.append(cached)
            return cached.sections
        }

        let result = buildSections(
            from: sourceSections,
            startOfToday: startOfToday,
            calendar: calendar,
            locale: locale
        )
        cacheMissCount &+= 1
        cache.removeAll(where: { $0.owner === cacheOwner })
        cache.append(CacheEntry(owner: cacheOwner, key: key, sections: result))
        if cache.count > cacheCapacity {
            cache.removeFirst(cache.count - cacheCapacity)
        }
        return result
    }

    static func resetCacheForTesting() {
        cache.removeAll(keepingCapacity: false)
        cacheMissCount = 0
    }

    private static func buildSections(
        from sourceSections: [InboxSectionViewModel],
        startOfToday: Date,
        calendar: Calendar,
        locale: Locale
    ) -> [InboxRenderSection] {
        let pastSevenDaysStart = calendar.date(
            byAdding: .day,
            value: -7,
            to: startOfToday
        ) ?? startOfToday
        let context = PresentationContext(
            startOfToday: startOfToday,
            pastSevenDaysStart: pastSevenDaysStart,
            calendar: calendar,
            locale: locale
        )
        var presentedSections: [PresentedSection] = []

        for sourceSection in sourceSections where !sourceSection.rows.isEmpty {
            var parentGroup: ParentGroup?
            for row in sourceSection.rows {
                if row.isChild {
                    if parentGroup != nil {
                        parentGroup?.rows.append(row)
                    } else {
                        append(
                            rows: [row],
                            bucket: Bucket(
                                id: "fallback::\(sourceSection.id)",
                                title: sourceSection.title
                            ),
                            context: context,
                            to: &presentedSections
                        )
                    }
                    continue
                }

                appendParentGroup(
                    parentGroup,
                    context: context,
                    to: &presentedSections
                )
                parentGroup = ParentGroup(
                    sourceSectionID: sourceSection.id,
                    sourceTitle: sourceSection.title,
                    rows: [row]
                )
            }
            appendParentGroup(
                parentGroup,
                context: context,
                to: &presentedSections
            )
        }

        var runCountByBucketID: [String: Int] = [:]
        return presentedSections.enumerated().map { index, section in
            let runCount = runCountByBucketID[section.bucket.id, default: 0] + 1
            runCountByBucketID[section.bucket.id] = runCount
            let runSuffix = runCount == 1 ? "" : "::run-\(runCount)"
            return InboxRenderSection(
                id: "chronology::\(section.bucket.id)\(runSuffix)",
                title: section.bucket.title,
                isFirstSection: index == presentedSections.startIndex,
                rows: section.rows
            )
        }
    }

    private static func appendParentGroup(
        _ group: ParentGroup?,
        context: PresentationContext,
        to sections: inout [PresentedSection]
    ) {
        guard let group, let parent = group.rows.first else {
            return
        }

        let bucket = bucket(
            for: parent.receivedAt,
            fallback: group.sourceTitle,
            fallbackID: group.sourceSectionID,
            context: context
        )
        append(
            rows: group.rows,
            bucket: bucket,
            context: context,
            to: &sections
        )
    }

    private static func append(
        rows: [InboxRowViewModel],
        bucket: Bucket,
        context: PresentationContext,
        to sections: inout [PresentedSection]
    ) {
        guard !rows.isEmpty else {
            return
        }

        let presentedRows = rows.map(context.rowWithClientTimeLabel)
        if sections.last?.bucket.id == bucket.id {
            sections[sections.count - 1].rows.append(contentsOf: presentedRows)
        } else {
            sections.append(PresentedSection(bucket: bucket, rows: presentedRows))
        }
    }

    private static func bucket(
        for receivedAt: String,
        fallback: String,
        fallbackID: String,
        context: PresentationContext
    ) -> Bucket {
        guard let date = context.date(from: receivedAt) else {
            return Bucket(id: "fallback::\(fallbackID)", title: fallback)
        }
        if context.calendar.isDate(date, inSameDayAs: context.startOfToday) {
            return Bucket(id: "today", title: "Today")
        }
        if date >= context.pastSevenDaysStart, date < context.startOfToday {
            return Bucket(id: "past-seven-days", title: "Past 7 days")
        }
        if date < context.pastSevenDaysStart,
           context.calendar.isDate(date, equalTo: context.startOfToday, toGranularity: .month) {
            return Bucket(id: "earlier-this-month", title: "Earlier this month")
        }
        let components = context.calendar.dateComponents([.era, .year, .month], from: date)
        let era = components.era ?? 0
        let year = components.year ?? 0
        let month = components.month ?? 0
        let identifier = String(describing: context.calendar.identifier)
        return Bucket(
            id: "month::\(identifier)::\(era)-\(year)-\(month)",
            title: context.monthYearFormatter.string(from: date)
        )
    }

    private final class PresentationContext {
        let startOfToday: Date
        let pastSevenDaysStart: Date
        let calendar: Calendar
        let monthYearFormatter: DateFormatter
        private let timeFormatter: DateFormatter
        private let compactDateFormatter: DateFormatter
        private var parsedDates: [String: Date] = [:]
        private var malformedTimestamps: Set<String> = []

        init(
            startOfToday: Date,
            pastSevenDaysStart: Date,
            calendar: Calendar,
            locale: Locale
        ) {
            self.startOfToday = startOfToday
            self.pastSevenDaysStart = pastSevenDaysStart
            self.calendar = calendar
            self.monthYearFormatter = InboxMonthYearFormatter.make(
                calendar: calendar,
                locale: locale
            )

            let timeFormatter = DateFormatter()
            timeFormatter.locale = locale
            timeFormatter.calendar = calendar
            timeFormatter.timeZone = calendar.timeZone
            timeFormatter.dateStyle = .none
            timeFormatter.timeStyle = .short
            self.timeFormatter = timeFormatter

            let compactDateFormatter = DateFormatter()
            compactDateFormatter.locale = locale
            compactDateFormatter.calendar = calendar
            compactDateFormatter.timeZone = calendar.timeZone
            compactDateFormatter.setLocalizedDateFormatFromTemplate("MMM d")
            self.compactDateFormatter = compactDateFormatter
        }

        func date(from value: String) -> Date? {
            if let date = parsedDates[value] {
                return date
            }
            guard !malformedTimestamps.contains(value) else {
                return nil
            }
            guard let date = InboxReceivedDateParser.date(from: value) else {
                malformedTimestamps.insert(value)
                return nil
            }
            parsedDates[value] = date
            return date
        }

        func rowWithClientTimeLabel(_ row: InboxRowViewModel) -> InboxRowViewModel {
            guard let date = date(from: row.receivedAt) else {
                return row
            }
            let formatter = calendar.isDate(date, inSameDayAs: startOfToday)
                ? timeFormatter
                : compactDateFormatter
            let timeLabel = formatter.string(from: date)
            guard timeLabel != row.timeLabel else {
                return row
            }
            return InboxRowViewModel(
                id: row.id,
                sender: row.sender,
                title: row.title,
                summary: row.summary,
                receivedAt: row.receivedAt,
                section: row.section,
                isUnread: row.isUnread,
                isGrouped: row.isGrouped,
                threadID: row.threadID,
                focusedMessageID: row.focusedMessageID,
                messageCount: row.messageCount,
                timeLabel: timeLabel,
                hasAttachments: row.hasAttachments,
                presentationStatus: row.presentationStatus,
                isChild: row.isChild,
                isExpandable: row.isExpandable,
                isExpanded: row.isExpanded
            )
        }
    }

    private struct CacheKey: Equatable {
        let storeIdentity: ObjectIdentifier
        let revision: UInt
        let startOfToday: Date
        let calendarIdentifier: String
        let timeZoneIdentifier: String
        let localeIdentifier: String
    }

    private final class CacheEntry {
        weak var owner: AnyObject?
        let key: CacheKey
        let sections: [InboxRenderSection]

        init(owner: AnyObject, key: CacheKey, sections: [InboxRenderSection]) {
            self.owner = owner
            self.key = key
            self.sections = sections
        }
    }

    private struct Bucket {
        let id: String
        let title: String
    }

    private struct PresentedSection {
        let bucket: Bucket
        var rows: [InboxRowViewModel]
    }

    private struct ParentGroup {
        let sourceSectionID: String
        let sourceTitle: String
        var rows: [InboxRowViewModel]
    }
}

private enum InboxReceivedDateParser {
    private static let fractionalFormatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()

    private static let standardFormatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()

    static func date(from value: String) -> Date? {
        fractionalFormatter.date(from: value) ?? standardFormatter.date(from: value)
    }
}

private enum InboxMonthYearFormatter {
    static func make(calendar: Calendar, locale: Locale) -> DateFormatter {
        let formatter = DateFormatter()
        formatter.locale = locale
        formatter.calendar = calendar
        formatter.timeZone = calendar.timeZone
        formatter.setLocalizedDateFormatFromTemplate("MMMM yyyy")
        return formatter
    }
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
                    .padding(.horizontal, 16)
                    .frame(minHeight: ElectronicMailControlMetrics.actionHeight)
                    .buttonStyle(.bordered)
                    .buttonBorderShape(.capsule)
                    .font(ElectronicMailType.small(weight: .semibold))
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 48)
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
        .padding(.leading, metrics.senderLeading)
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
    let isSelected: Bool
    let metrics: InboxLayoutMetrics
    let colorScheme: ColorScheme
    let actionHint: String
    let onSelect: () -> Void
    let onToggleExpansion: (() -> Void)?

    static func == (lhs: InboxRowView, rhs: InboxRowView) -> Bool {
        lhs.row == rhs.row
            && lhs.isSelected == rhs.isSelected
            && lhs.metrics == rhs.metrics
            && lhs.colorScheme == rhs.colorScheme
            && lhs.actionHint == rhs.actionHint
    }

    var body: some View {
        ZStack(alignment: .leading) {
            Button(action: onSelect) {
                HStack(spacing: 0) {
                    Color.clear.frame(width: metrics.senderLeading)

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
                                .symbolRenderingMode(.multicolor)
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
                .background(isSelected ? ElectronicMailDesign.appleBlue : Color.clear)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel(accessibilityLabel)
            .accessibilityHint(actionHint)
            .accessibilityAddTraits(isSelected ? .isSelected : [])

            if row.isExpandable, let onToggleExpansion {
                Button(action: onToggleExpansion) {
                    Image(systemName: row.isExpanded ? "chevron.down" : "chevron.right")
                        .font(.system(size: 11, weight: .semibold))
                        .symbolRenderingMode(.multicolor)
                        .foregroundStyle(disclosureColor)
                        .frame(width: metrics.disclosureIconSize, height: metrics.disclosureIconSize)
                    .frame(width: metrics.disclosureHitWidth, height: ElectronicMailTypography.bodyLineHeight)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .offset(x: metrics.disclosureHitLeading)
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
            .foregroundStyle(color)
            .lineLimit(1)
            .truncationMode(.tail)
            .multilineTextAlignment(alignment == .trailing ? .trailing : .leading)
    }

    private var senderTextColor: Color {
        if isSelected {
            return ElectronicMailDesign.selectedText(for: colorScheme)
        }
        return row.isUnread
            ? ElectronicMailDesign.unreadText(for: colorScheme)
            : ElectronicMailDesign.readText(for: colorScheme)
    }

    private var subjectTextColor: Color {
        if isSelected {
            return ElectronicMailDesign.selectedText(for: colorScheme)
        }
        return row.isUnread
            ? ElectronicMailDesign.unreadText(for: colorScheme)
            : ElectronicMailDesign.readText(for: colorScheme)
    }

    private var metadataTextColor: Color {
        if isSelected {
            return ElectronicMailDesign.selectedText(for: colorScheme)
        }
        return row.isUnread
            ? ElectronicMailDesign.unreadText(for: colorScheme).opacity(0.88)
            : ElectronicMailDesign.secondaryText(for: colorScheme)
    }

    private var attachmentIconColor: Color {
        if isSelected {
            return ElectronicMailDesign.selectedText(for: colorScheme)
        }

        return ElectronicMailDesign.secondaryText(for: colorScheme).opacity(0.88)
    }

    private var disclosureColor: Color {
        isSelected
            ? ElectronicMailDesign.selectedText(for: colorScheme)
            : ElectronicMailDesign.appleBlue
    }
}

private enum ElectronicMailTypography {
    static let bodyLineHeight = ElectronicMailMailboxType.rowHeight
}

private struct InboxLayoutMetrics: Equatable {
    let windowWidth: CGFloat

    let disclosureIconSize: CGFloat = 22
    let disclosureHitWidth: CGFloat = 40
    let senderSubjectGap: CGFloat = 24
    let subjectAttachmentGap: CGFloat = 16
    let attachmentWidth: CGFloat = 18
    let attachmentIconSize: CGFloat = 14
    let attachmentTimeGap: CGFloat = 16
    let timeWidth: CGFloat = 120
    let childIndent: CGFloat = 18
    let sectionLabelHeight: CGFloat = 38

    init(windowSize: CGSize) {
        windowWidth = windowSize.width
    }

    private var sharedGrid: ElectronicMailLayoutMetrics {
        ElectronicMailLayoutMetrics(width: windowWidth)
    }

    var utilityCenter: CGFloat {
        sharedGrid.utilityCenter
    }

    var disclosureHitLeading: CGFloat {
        max(0, utilityCenter - disclosureHitWidth / 2)
    }

    var senderLeading: CGFloat {
        sharedGrid.textLeading
    }

    var subjectLeading: CGFloat {
        sharedGrid.subjectLeading
    }

    var senderWidth: CGFloat {
        max(0, subjectLeading - senderLeading - senderSubjectGap)
    }

    var trailingInset: CGFloat {
        sharedGrid.dateTrailing
    }

    var dividerTrailing: CGFloat {
        trailingInset
    }

    func sectionTopSpacing(isFirst: Bool) -> CGFloat {
        isFirst ? 12 : 24
    }
}

#if DEBUG
#Preview {
    InboxView(store: InboxStore(client: DemoAppClient()), searchText: .constant(""))
        .frame(width: 1440, height: 900)
}
#endif
