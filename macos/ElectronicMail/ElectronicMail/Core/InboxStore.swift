import Foundation

public enum LoadPhase: Equatable {
    case idle
    case loading
    case loaded
    case failed(String)
}

public enum InboxRowVisualTone: Equatable {
    case selected
    case unread
    case read
}

public struct InboxRowViewModel: Identifiable, Equatable {
    public let id: String
    let sender: String
    let title: String
    let summary: String?
    let receivedAt: String
    let section: String
    let isUnread: Bool
    let isGrouped: Bool
    let isSelected: Bool
    let threadID: String
    let focusedMessageID: String?
    let messageCount: Int
    let timeLabel: String
    let presentationStatus: String?
    let isChild: Bool
    let isExpandable: Bool
    let isExpanded: Bool

    var visualTone: InboxRowVisualTone {
        if isSelected {
            return .selected
        }
        return isUnread ? .unread : .read
    }
}

public struct InboxSectionViewModel: Identifiable, Equatable {
    public let id: String
    let title: String
    let rows: [InboxRowViewModel]
}

public struct InboxMailboxFooterViewModel: Equatable {
    let text: String
    let progress: Double?
    let canLoadMore: Bool
}

@MainActor
public final class InboxStore: ObservableObject {
    @Published public private(set) var phase: LoadPhase = .idle
    @Published private(set) var session: AppSessionResponse?
    @Published private(set) var activeMailboxLabel: MailboxLabel = .inbox
    @Published private(set) var activeMailbox: MailboxResponse?
    @Published private(set) var refreshFailed = false
    @Published private(set) var manualSyncInProgress = false
    @Published private(set) var selectedThreadID: String?
    @Published private(set) var selectedMessageID: String?
    @Published private(set) var activeThreadID: String?
    @Published private(set) var activeMessageID: String?
    @Published private(set) var readerThreadID: String?
    @Published private(set) var readerFocusedMessageID: String?
    @Published private(set) var readerThread: ThreadReaderResponse?
    @Published private(set) var readerRow: InboxRowViewModel?
    @Published private(set) var readerError: String?
    @Published private(set) var threadErrors: [String: String] = [:]
    @Published private(set) var openedThreads: [String: ThreadReaderResponse] = [:]
    @Published private(set) var mailboxPageLoading = false
    @Published private(set) var expandedThreadIDs: Set<String> = []
    @Published var navigationPlaceholderVisible = false

    private let client: AppClient
    private let sessionCache: AppSessionCache
    private let threadCache: ThreadCache
    private let localMailStore: LocalMailStore
    private var inFlightSessionRefresh: Task<AppSessionResponse, Error>?
    private var inFlightMailboxRefresh: Task<MailboxResponse, Error>?
    private var inFlightThreads: [String: Task<ThreadReaderResponse, Error>] = [:]
    private var selectionPrefetchTask: Task<Void, Never>?
    private var syncLoopTask: Task<Void, Never>?
    private var eventStreamTask: Task<Void, Never>?
    private var eventRefreshTask: Task<Void, Never>?
    private var lastSyncTriggerAt: Date?
    private var lastMailboxRefreshAt: [MailboxLabel: Date] = [:]
    private var lastAutomaticMailboxCursor: String?
    private var bodyRefreshAttempts: [String: Int] = [:]
    private var readActionQueuedKeys: Set<String> = []
    private var lastMailboxEventID: String?
    private var pendingEventRefreshNeedsSession = false

    private let activeSyncInterval: TimeInterval = 60
    private let minimumSyncGap: TimeInterval = 18
    private let minimumMailboxRefreshGap: TimeInterval = 20
    private let eventRefreshDebounce: TimeInterval = 1
    private let threadFetchLimit = 50
    private let mailboxPageLimit = 100

    public init(
        client: AppClient = LiveBackendAppClient(baseURL: AppConfiguration.defaultBackendURL),
        sessionCache: AppSessionCache = AppSessionCache(),
        threadCache: ThreadCache = ThreadCache(),
        localMailStore: LocalMailStore = NoopLocalMailStore()
    ) {
        self.client = client
        self.sessionCache = sessionCache
        self.threadCache = threadCache
        self.localMailStore = localMailStore
    }

    public var runMode: AppRunMode {
        client.mode
    }

    public var backendURL: URL {
        client.baseURL
    }

    public var currentReadiness: PostLoginReadinessResponse? {
        session?.readiness
    }

    public var canSendMail: Bool {
        session?.dashboard.auth.canSendMail == true
    }

    public var missingMailScopes: [String] {
        session?.dashboard.auth.missingScopes ?? []
    }

    public func setSessionToken(_ token: String?) {
        client.sessionToken = token
        if token == nil {
            session = nil
            activeMailboxLabel = .inbox
            activeMailbox = nil
            selectedThreadID = nil
            selectedMessageID = nil
            activeThreadID = nil
            activeMessageID = nil
            manualSyncInProgress = false
            mailboxPageLoading = false
            lastAutomaticMailboxCursor = nil
            readerThreadID = nil
            readerFocusedMessageID = nil
            readerThread = nil
            readerRow = nil
            readerError = nil
            openedThreads = [:]
            expandedThreadIDs = []
            threadErrors = [:]
            bodyRefreshAttempts = [:]
            readActionQueuedKeys = []
            lastMailboxRefreshAt = [:]
            inFlightMailboxRefresh?.cancel()
            inFlightMailboxRefresh = nil
            selectionPrefetchTask?.cancel()
            selectionPrefetchTask = nil
            stopMailboxEventStream()
            eventRefreshTask?.cancel()
            eventRefreshTask = nil
            pendingEventRefreshNeedsSession = false
            lastMailboxEventID = nil
            sessionCache.clear()
            localMailStore.clearAll()
            threadCache.clearMemory()
            phase = .idle
        }
    }

    public func exchangeMobileSession(loginCode: String) async throws -> MobileSessionExchangeResponse {
        let response = try await client.exchangeMobileSession(loginCode: loginCode)
        setSessionToken(response.sessionToken)
        return response
    }

    public var sections: [InboxSectionViewModel] {
        guard let mailbox = visibleMailbox else {
            return []
        }
        return mailbox.sections.compactMap { section in
            let rows = visibleRows(in: section).flatMap { row in
                let childRows = visibleChildren(for: row)
                let parent = InboxRowViewModel(
                    id: row.threadID,
                    sender: row.displaySender,
                    title: row.displayTitle,
                    summary: row.displaySummary,
                    receivedAt: row.latestReceivedAt,
                    section: section.title,
                    isUnread: row.isUnread,
                    isGrouped: row.isGrouped,
                    isSelected: selectedThreadID == row.threadID && selectedMessageID == nil,
                    threadID: row.threadID,
                    focusedMessageID: nil,
                    messageCount: row.messageCount,
                    timeLabel: Self.timeLabel(for: row.latestReceivedAt, sectionTitle: section.title),
                    presentationStatus: row.presentationStatus,
                    isChild: false,
                    isExpandable: childRows.count > 1,
                    isExpanded: expandedThreadIDs.contains(row.threadID)
                )
                guard expandedThreadIDs.contains(row.threadID) else {
                    return [parent]
                }
                let children = childRows.map { child in
                    InboxRowViewModel(
                        id: "\(row.threadID)::message::\(child.messageID)",
                        sender: child.displaySender,
                        title: child.displayTitle,
                        summary: child.snippet,
                        receivedAt: child.receivedAt,
                        section: section.title,
                        isUnread: child.isUnread,
                        isGrouped: false,
                        isSelected: selectedThreadID == row.threadID && selectedMessageID == child.messageID,
                        threadID: row.threadID,
                        focusedMessageID: child.messageID,
                        messageCount: 1,
                        timeLabel: Self.timeLabel(for: child.receivedAt, sectionTitle: section.title),
                        presentationStatus: row.presentationStatus,
                        isChild: true,
                        isExpandable: false,
                        isExpanded: false
                    )
                }
                return [parent] + children
            }
            guard !rows.isEmpty else {
                return nil
            }
            return InboxSectionViewModel(
                id: section.id,
                title: section.title,
                rows: rows
            )
        }
    }

    public var flatRows: [InboxRowViewModel] {
        sections.flatMap(\.rows)
    }

    public var dashboardFeedCount: Int {
        guard let feed = session?.dashboard.feed else {
            return 0
        }
        return feed.now.count + feed.today.count + feed.worthKnowing.count
    }

    public var mailboxVisibleRowCount: Int {
        visibleMailbox?.sections.reduce(0) { $0 + $1.rows.count } ?? 0
    }

    public var isReadyForMainInterface: Bool {
        guard let readiness = session?.readiness else {
            return false
        }
        return readiness.readyToEnter && mailboxVisibleRowCount > 0 && dashboardFeedCount > 0
    }

    public var canEnterWithBuildingDashboard: Bool {
        guard let readiness = session?.readiness else {
            return false
        }
        return readiness.mailboxReady && mailboxVisibleRowCount > 0
    }

    public var canLoadMoreMailbox: Bool {
        visibleMailbox?.nextCursor?.isEmpty == false
    }

    public var mailboxFooterText: String? {
        mailboxFooter?.text
    }

    public var mailboxTitle: String {
        switch activeMailboxLabel {
        case .inbox:
            return "Inbox"
        case .sent:
            return "Sent"
        case .drafts:
            return "Drafts"
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

    public var mailboxFooter: InboxMailboxFooterViewModel? {
        guard let mailbox = visibleMailbox else {
            return nil
        }

        let visibleRows = mailbox.sections.reduce(0) { $0 + $1.rows.count }
        let loadedThreads = max(mailbox.loadedThreads ?? visibleRows, visibleRows)
        let totalThreads = max(mailbox.totalThreads, loadedThreads)
        let progress = totalThreads > 0 ? min(1, Double(loadedThreads) / Double(totalThreads)) : nil

        if mailbox.nextCursor?.isEmpty == false {
            return InboxMailboxFooterViewModel(
                text: "Showing \(loadedThreads) of \(totalThreads). Load more...",
                progress: progress,
                canLoadMore: true
            )
        }

        if mailbox.fullImportRunning == true || (totalThreads == 0 && mailbox.fullImportCompleted != true) {
            return InboxMailboxFooterViewModel(
                text: "Syncing more emails...",
                progress: progress,
                canLoadMore: false
            )
        }

        if mailbox.fullImportCompleted == true {
            return nil
        }

        return InboxMailboxFooterViewModel(
            text: "Limit reached",
            progress: progress,
            canLoadMore: false
        )
    }

    public func load() async {
        guard client.mode != .localBackend || client.sessionToken?.isEmpty == false else {
            phase = .failed("Sign in with Google to load your mailbox.")
            return
        }
        if let cached = sessionCache.read() ?? localMailStore.readSession() {
            session = cached
            activeMailbox = localMailStore.readMailbox(userID: cached.user.id, label: activeMailboxLabel) ?? cached.mailbox
            phase = .loaded
        } else {
            phase = .loading
        }
        await refresh()
    }

    public func refresh() async {
        await refresh(allowEmptyDashboard: false)
    }

    public func refreshForReadiness() async {
        await refresh(allowEmptyDashboard: true)
    }

    public func createManualTask(title: String, notes: String? = nil, section: String = "today") async throws -> TaskResponse {
        let task = try await client.createTask(
            TaskCreateRequest(
                title: title,
                notes: notes,
                section: section,
                dueAt: nil
            )
        )
        await refresh(allowEmptyDashboard: true)
        return task
    }

    public func sendCompose(to: [String], cc: [String] = [], bcc: [String] = [], subject: String, bodyText: String) async throws -> MailSendResponse {
        guard canSendMail else {
            return missingSendScopeResponse()
        }

        let response = try await client.sendCompose(
            MailComposeRequest(
                clientSendID: UUID().uuidString,
                to: to,
                cc: cc,
                bcc: bcc,
                subject: subject,
                bodyText: bodyText,
                bodyHTML: nil,
                createdAt: ISO8601DateFormatter.backendActionTimestamp.string(from: Date())
            )
        )
        await refreshAfterSend(response)
        return response
    }

    public func sendReply(threadID: String, bodyText: String, cc: [String] = [], bcc: [String] = []) async throws -> MailSendResponse {
        guard canSendMail else {
            return missingSendScopeResponse(mailboxThreadID: threadID)
        }

        let response = try await client.sendReply(
            threadID: threadID,
            request: MailReplyRequest(
                clientSendID: UUID().uuidString,
                cc: cc,
                bcc: bcc,
                bodyText: bodyText,
                bodyHTML: nil,
                createdAt: ISO8601DateFormatter.backendActionTimestamp.string(from: Date())
            )
        )
        await refreshAfterSend(response)
        return response
    }

    private func missingSendScopeResponse(mailboxThreadID: String? = nil) -> MailSendResponse {
        MailSendResponse(
            clientSendID: UUID().uuidString,
            serverSendID: nil,
            mailboxThreadID: mailboxThreadID,
            gmailThreadID: nil,
            gmailMessageID: nil,
            state: .reauthRequired,
            queuedAt: nil,
            sentAt: nil,
            error: "Google needs permission to send mail.",
            reauthURL: nil
        )
    }

    private func refreshAfterSend(_ response: MailSendResponse) async {
        if response.state == .sent || response.state == .queued || response.state == .sending {
            await refresh(allowEmptyDashboard: true)
            if let threadID = response.mailboxThreadID ?? readerThreadID {
                await prefetchThread(threadID: threadID, force: true, silent: true)
            }
        }
    }

    public func completeEntity(entityID: String, note: String? = nil) async throws -> EntityOutcomeResponse {
        let outcome = try await client.completeEntity(entityID, request: EntityOutcomeRequest(note: note))
        await refresh(allowEmptyDashboard: true)
        return outcome
    }

    private func refresh(allowEmptyDashboard: Bool) async {
        do {
            let task = inFlightSessionRefresh ?? Task { [client] in
                try await client.appSession()
            }
            inFlightSessionRefresh = task
            let next = try await task.value
            inFlightSessionRefresh = nil
            let merged = sessionCache.merge(current: session ?? sessionCache.read(), next: next, allowEmptyDashboard: allowEmptyDashboard)
            session = merged
            if activeMailboxLabel == .inbox {
                activeMailbox = activeMailbox?.preservingLoadedPages(afterRefreshingFirstPage: merged.mailbox) ?? merged.mailbox
            }
            sessionCache.write(merged)
            localMailStore.writeSession(merged)
            refreshFailed = false
            phase = .loaded
            await refreshActiveMailbox(allowCachedFallback: true)
            seedActiveSelectionIfNeeded()
            refreshReaderRow()
            prefetchPriorityThreads()
        } catch is CancellationError {
            inFlightSessionRefresh = nil
        } catch {
            inFlightSessionRefresh = nil
            refreshFailed = true
            if session == nil, let cached = sessionCache.read() ?? localMailStore.readSession() {
                session = cached
                activeMailbox = localMailStore.readMailbox(userID: cached.user.id, label: activeMailboxLabel) ?? cached.mailbox
                phase = .loaded
            } else if session == nil {
                phase = .failed(error.localizedDescription)
            }
        }
    }

    private func refreshActiveMailbox(allowCachedFallback: Bool) async {
        guard let userID = session?.user.id else {
            return
        }
        let label = activeMailboxLabel
        let now = Date()
        if let lastRefresh = lastMailboxRefreshAt[label],
           activeMailbox != nil,
           now.timeIntervalSince(lastRefresh) < minimumMailboxRefreshGap {
            return
        }

        do {
            let task = inFlightMailboxRefresh ?? Task { [client, mailboxPageLimit] in
                try await client.mailbox(label: label, limit: mailboxPageLimit, cursor: nil)
            }
            inFlightMailboxRefresh = task
            let mailbox = try await task.value
            inFlightMailboxRefresh = nil
            guard label == activeMailboxLabel else {
                return
            }
            lastMailboxRefreshAt[label] = now

            let merged = activeMailbox?.preservingLoadedPages(afterRefreshingFirstPage: mailbox) ?? mailbox
            activeMailbox = merged
            localMailStore.writeMailbox(merged, userID: userID, label: label)
            refreshFailed = false
            phase = .loaded
            seedActiveSelectionIfNeeded()
            refreshReaderRow()
            prefetchPriorityThreads()
        } catch {
            inFlightMailboxRefresh = nil
            refreshFailed = true
            if allowCachedFallback, let cached = localMailStore.readMailbox(userID: userID, label: activeMailboxLabel) {
                activeMailbox = cached
                phase = .loaded
                seedActiveSelectionIfNeeded()
            } else if activeMailbox == nil {
                phase = .failed(error.localizedDescription)
            }
        }
    }

    public func loadMoreMailbox(automatic: Bool = false) async {
        guard !mailboxPageLoading, let cursor = visibleMailbox?.nextCursor, !cursor.isEmpty, let userID = session?.user.id else {
            return
        }
        if automatic, lastAutomaticMailboxCursor == cursor {
            return
        }
        if automatic {
            lastAutomaticMailboxCursor = cursor
        }
        mailboxPageLoading = true
        defer {
            mailboxPageLoading = false
        }
        do {
            let page = try await client.mailbox(label: activeMailboxLabel, limit: mailboxPageLimit, cursor: cursor)
            let current = visibleMailbox
            let merged = current?.appendingPage(page) ?? page
            activeMailbox = merged
            localMailStore.writeMailbox(merged, userID: userID, label: activeMailboxLabel)
            refreshFailed = false
            seedActiveSelectionIfNeeded()
            refreshReaderRow()
        } catch {
            refreshFailed = true
        }
    }

    public func select(threadID: String, prefetch: Bool = true) {
        select(threadID: threadID, focusedMessageID: nil, prefetch: prefetch)
    }

    public func select(threadID: String, focusedMessageID: String?, prefetch: Bool = true) {
        selectedThreadID = threadID
        selectedMessageID = focusedMessageID
        activeThreadID = threadID
        activeMessageID = focusedMessageID
        threadErrors[threadID] = nil
        if prefetch {
            selectionPrefetchTask?.cancel()
            selectionPrefetchTask = nil
            Task { await prefetchThread(threadID: threadID, force: false, silent: true) }
        } else {
            scheduleSelectionPrefetch(threadID: threadID)
        }
    }

    public func openActiveSelection() {
        guard let activeThreadID else {
            seedActiveSelectionIfNeeded()
            if let activeThreadID {
                _ = openReader(threadID: activeThreadID, focusedMessageID: activeMessageID)
            }
            return
        }
        _ = openReader(threadID: activeThreadID, focusedMessageID: activeMessageID)
    }

    @discardableResult
    public func openReader(threadID: String, focusedMessageID: String? = nil) -> Task<Void, Never> {
        selectionPrefetchTask?.cancel()
        selectionPrefetchTask = nil
        selectedThreadID = threadID
        selectedMessageID = focusedMessageID
        activeThreadID = threadID
        activeMessageID = focusedMessageID
        readerThreadID = threadID
        readerFocusedMessageID = focusedMessageID
        readerThread = openedThreads[threadID]
        readerRow = rowViewModel(threadID: threadID)
        readerError = nil
        threadErrors[threadID] = nil
        markReadAfterOpening(threadID: threadID, focusedMessageID: focusedMessageID)

        return Task { [weak self] in
            await self?.prefetchThread(threadID: threadID, force: false, silent: false)
        }
    }

    public func closeReader() {
        readerThreadID = nil
        readerFocusedMessageID = nil
    }

    public func toggleExpansion(threadID: String) {
        if expandedThreadIDs.contains(threadID) {
            expandedThreadIDs.remove(threadID)
            if selectedThreadID == threadID, selectedMessageID != nil {
                selectedMessageID = nil
                activeMessageID = nil
            }
        } else {
            expandedThreadIDs.insert(threadID)
        }
    }

    public func setMailboxLabel(_ label: MailboxLabel) async {
        guard activeMailboxLabel != label else {
            return
        }
        activeMailboxLabel = label
        selectedThreadID = nil
        selectedMessageID = nil
        activeThreadID = nil
        activeMessageID = nil
        readerThreadID = nil
        readerFocusedMessageID = nil
        readerThread = nil
        readerRow = nil
        readerError = nil
        expandedThreadIDs = []
        inFlightMailboxRefresh?.cancel()
        inFlightMailboxRefresh = nil
        lastAutomaticMailboxCursor = nil
        lastMailboxRefreshAt[label] = nil
        if let userID = session?.user.id, let cached = localMailStore.readMailbox(userID: userID, label: label) {
            activeMailbox = cached
            phase = .loaded
            seedActiveSelectionIfNeeded()
        } else if label == .inbox, let session {
            activeMailbox = session.mailbox
            phase = .loaded
            seedActiveSelectionIfNeeded()
        } else {
            activeMailbox = nil
        }
        if label == .inbox {
            await refresh()
            await refreshActiveMailbox(allowCachedFallback: true)
            return
        }
        await refreshActiveMailbox(allowCachedFallback: true)
    }

    public func syncNow() async {
        guard !manualSyncInProgress else {
            return
        }
        manualSyncInProgress = true
        defer {
            manualSyncInProgress = false
        }

        do {
            if client.mode == .localBackend {
                _ = try await client.syncMailboxNow()
            }
            await refresh(allowEmptyDashboard: true)
        } catch {
            refreshFailed = true
            await refreshActiveMailbox(allowCachedFallback: true)
        }
    }

    public func performSelectedThreadAction(_ action: GmailThreadAction) async {
        guard let activeThreadID else {
            seedActiveSelectionIfNeeded()
            return
        }
        _ = await performThreadAction(action, threadID: activeThreadID, targetMessageID: activeMessageID)
    }

    @discardableResult
    private func performThreadAction(_ action: GmailThreadAction, threadID: String, targetMessageID: String? = nil) async -> Bool {
        do {
            let request = QueuedThreadActionRequest(
                clientActionID: UUID().uuidString,
                mailboxThreadID: threadID,
                targetMessageID: targetMessageID,
                action: action,
                createdAt: ISO8601DateFormatter.backendActionTimestamp.string(from: Date())
            )
            _ = try await client.enqueueThreadAction(request)
            applyLocalThreadAction(threadID: threadID, action: action)
            lastMailboxRefreshAt[activeMailboxLabel] = nil
            await refreshActiveMailbox(allowCachedFallback: true)
            return true
        } catch {
            refreshFailed = true
            return false
        }
    }

    public func toggleNavigationPlaceholder() {
        navigationPlaceholderVisible.toggle()
    }

    public func prefetchThread(threadID: String, force: Bool = false, silent: Bool = true) async {
        guard let userID = session?.user.id else {
            if !silent {
                let message = "Sign in with Google to load this email."
                threadErrors[threadID] = message
                if readerThreadID == threadID {
                    readerError = message
                }
            }
            return
        }
        let cachedThread = force ? nil : (localMailStore.readThread(userID: userID, threadID: threadID) ?? threadCache.read(userID: userID, threadID: threadID))
        if let cached = cachedThread {
            openedThreads[threadID] = cached
            updateReader(threadID: threadID, thread: cached, error: nil)
            scheduleBodyRefreshIfNeeded(threadID: threadID, thread: cached)
            if threadCache.read(userID: userID, threadID: threadID) != nil {
                localMailStore.writeThread(cached, userID: userID, threadID: threadID)
            }
            if silent {
                return
            }
        }
        if let inFlight = inFlightThreads[threadID] {
            do {
                let thread = try await inFlight.value
                openedThreads[threadID] = thread
                updateReader(threadID: threadID, thread: thread, error: nil)
            } catch {
                if !silent {
                    recordThreadError(error.localizedDescription, threadID: threadID)
                }
            }
            return
        }

        let task = Task { [client, threadFetchLimit] in
            try await client.thread(threadID: threadID, limit: threadFetchLimit, offset: 0)
        }
        inFlightThreads[threadID] = task
        do {
            let thread = try await task.value
            inFlightThreads[threadID] = nil
            openedThreads[threadID] = thread
            threadCache.write(thread, userID: userID, threadID: threadID)
            localMailStore.writeThread(thread, userID: userID, threadID: threadID)
            threadErrors[threadID] = nil
            updateReader(threadID: threadID, thread: thread, error: nil)
            scheduleBodyRefreshIfNeeded(threadID: threadID, thread: thread)
        } catch {
            inFlightThreads[threadID] = nil
            if cachedThread == nil, !silent {
                recordThreadError(error.localizedDescription, threadID: threadID)
            }
        }
    }

    public func triggerMailboxSyncIfNeeded() async {
        guard client.mode == .localBackend else {
            return
        }
        if let lastSyncTriggerAt, Date().timeIntervalSince(lastSyncTriggerAt) < minimumSyncGap {
            return
        }
        lastSyncTriggerAt = Date()
        await refreshActiveMailbox(allowCachedFallback: true)
    }

    public func startLiveRefreshLoop() {
        guard client.mode == .localBackend else {
            return
        }
        startMailboxEventStream()
        guard syncLoopTask == nil else {
            return
        }
        syncLoopTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.triggerMailboxSyncIfNeeded()
                let interval = self?.activeSyncInterval ?? 8
                try? await Task.sleep(nanoseconds: UInt64(interval * 1_000_000_000))
            }
        }
    }

    public func stopLiveRefreshLoop() {
        syncLoopTask?.cancel()
        syncLoopTask = nil
        stopMailboxEventStream()
        eventRefreshTask?.cancel()
        eventRefreshTask = nil
    }

    private func startMailboxEventStream() {
        guard eventStreamTask == nil,
              let token = client.sessionToken,
              !token.isEmpty else {
            return
        }
        eventStreamTask = Task { [weak self] in
            var reconnectDelay: TimeInterval = 1
            while !Task.isCancelled {
                do {
                    try await self?.runMailboxEventStream(sessionToken: token)
                    reconnectDelay = 1
                } catch is CancellationError {
                    return
                } catch {
                    try? await Task.sleep(nanoseconds: UInt64(reconnectDelay * 1_000_000_000))
                    reconnectDelay = min(reconnectDelay * 2, 30)
                }
            }
        }
    }

    private func stopMailboxEventStream() {
        eventStreamTask?.cancel()
        eventStreamTask = nil
    }

    private func runMailboxEventStream(sessionToken: String) async throws {
        guard let url = URL(string: "/v1/events/mailbox", relativeTo: client.baseURL)?.absoluteURL else {
            throw APIError.invalidURL
        }
        var request = URLRequest(url: url)
        request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        request.setValue("Bearer \(sessionToken)", forHTTPHeaderField: "Authorization")
        if let lastMailboxEventID {
            request.setValue(lastMailboxEventID, forHTTPHeaderField: "Last-Event-ID")
        }
        let (bytes, response) = try await URLSession.shared.bytes(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.emptyResponse
        }
        guard (200..<300).contains(httpResponse.statusCode) else {
            throw APIError.httpStatus(httpResponse.statusCode)
        }
        var parser = ServerSentEventParser()
        for try await line in bytes.lines {
            if Task.isCancelled {
                return
            }
            if let event = parser.feed(line: line) {
                handleMailboxServerEvent(event)
            }
        }
    }

    private func handleMailboxServerEvent(_ event: MailboxServerEvent) {
        if let id = event.id, !id.isEmpty {
            lastMailboxEventID = id
        }
        switch event.event {
        case "mailbox-changed":
            scheduleEventRefresh(needsSession: false)
        case "dashboard-changed":
            scheduleEventRefresh(needsSession: true)
        default:
            break
        }
    }

    private func scheduleEventRefresh(needsSession: Bool) {
        pendingEventRefreshNeedsSession = pendingEventRefreshNeedsSession || needsSession
        eventRefreshTask?.cancel()
        eventRefreshTask = Task { [weak self] in
            guard let self else {
                return
            }
            try? await Task.sleep(nanoseconds: UInt64(self.eventRefreshDebounce * 1_000_000_000))
            guard !Task.isCancelled else {
                return
            }
            await self.performEventRefresh()
        }
    }

    private func performEventRefresh() async {
        let needsSession = pendingEventRefreshNeedsSession
        pendingEventRefreshNeedsSession = false
        eventRefreshTask = nil
        lastMailboxRefreshAt[activeMailboxLabel] = nil
        if needsSession {
            await refresh(allowEmptyDashboard: true)
        } else {
            await refreshActiveMailbox(allowCachedFallback: true)
        }
    }

    private func scheduleSelectionPrefetch(threadID: String) {
        selectionPrefetchTask?.cancel()
        selectionPrefetchTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 220_000_000)
            guard !Task.isCancelled else {
                return
            }
            await self?.prefetchThread(threadID: threadID, force: false, silent: true)
        }
    }

    private func markReadAfterOpening(threadID: String, focusedMessageID: String?) {
        guard rowNeedsMarkRead(threadID: threadID, focusedMessageID: focusedMessageID) else {
            return
        }
        let key = readActionKey(threadID: threadID, focusedMessageID: focusedMessageID)
        guard !readActionQueuedKeys.contains(key) else {
            return
        }
        readActionQueuedKeys.insert(key)
        Task { [weak self] in
            guard let self else {
                return
            }
            let succeeded = await self.performThreadAction(.markRead, threadID: threadID, targetMessageID: focusedMessageID)
            if !succeeded {
                self.readActionQueuedKeys.remove(key)
            }
        }
    }

    private func rowNeedsMarkRead(threadID: String, focusedMessageID: String?) -> Bool {
        flatRows.contains { row in
            row.threadID == threadID && row.focusedMessageID == focusedMessageID && row.isUnread
        }
    }

    private func readActionKey(threadID: String, focusedMessageID: String?) -> String {
        guard let focusedMessageID else {
            return threadID
        }
        return "\(threadID)::message::\(focusedMessageID)"
    }

    private func seedActiveSelectionIfNeeded() {
        if let activeThreadID,
           flatRows.contains(where: { $0.threadID == activeThreadID && $0.focusedMessageID == activeMessageID }) {
            if selectedThreadID == nil {
                selectedThreadID = activeThreadID
                selectedMessageID = activeMessageID
            }
            return
        }
        let firstRow = flatRows.first
        activeThreadID = firstRow?.threadID
        activeMessageID = firstRow?.focusedMessageID
        if selectedThreadID == nil
            || !flatRows.contains(where: { $0.threadID == selectedThreadID && $0.focusedMessageID == selectedMessageID }) {
            selectedThreadID = firstRow?.threadID
            selectedMessageID = firstRow?.focusedMessageID
        }
    }

    private func rowViewModel(threadID: String) -> InboxRowViewModel? {
        flatRows.first { $0.threadID == threadID }
    }

    private func refreshReaderRow() {
        guard let readerThreadID else {
            return
        }
        readerRow = rowViewModel(threadID: readerThreadID)
    }

    private func updateReader(threadID: String, thread: ThreadReaderResponse, error: String?) {
        guard readerThreadID == threadID else {
            return
        }
        readerThread = thread
        readerRow = rowViewModel(threadID: threadID)
        readerError = error
    }

    private func recordThreadError(_ message: String, threadID: String) {
        threadErrors[threadID] = message
        guard readerThreadID == threadID else {
            return
        }
        readerError = message
        readerRow = rowViewModel(threadID: threadID)
    }

    private func scheduleBodyRefreshIfNeeded(threadID: String, thread: ThreadReaderResponse) {
        guard thread.needsReaderBodyRefresh, readerThreadID == threadID else {
            bodyRefreshAttempts[threadID] = nil
            return
        }
        let attempts = bodyRefreshAttempts[threadID, default: 0]
        guard attempts < 3 else {
            return
        }
        bodyRefreshAttempts[threadID] = attempts + 1
        Task { [weak self] in
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            await self?.prefetchThread(threadID: threadID, force: true, silent: true)
        }
    }

    private func prefetchPriorityThreads() {
        let rows = flatRows
        let grouped = rows.filter(\.isGrouped)
        let candidates = Array((rows.prefix(10) + grouped).map(\.threadID).uniqued().prefix(14))
        candidates.forEach { threadID in
            Task { await prefetchThread(threadID: threadID, force: false, silent: true) }
        }
    }

    private func applyLocalThreadAction(threadID: String, action: GmailThreadAction) {
        guard shouldRemoveFromCurrentMailbox(action: action), let mailbox = activeMailbox else {
            return
        }

        let nextSections = mailbox.sections.compactMap { section -> GmailThreadSection? in
            let rows = section.rows.filter { $0.threadID != threadID }
            guard !rows.isEmpty else {
                return nil
            }
            return GmailThreadSection(id: section.id, title: section.title, rows: rows)
        }
        activeMailbox = MailboxResponse(
            label: mailbox.label,
            totalThreads: max(0, mailbox.totalThreads - 1),
            nextCursor: mailbox.nextCursor,
            loadedThreads: mailbox.loadedThreads.map { max(0, $0 - 1) },
            windowDays: mailbox.windowDays,
            sections: nextSections,
            readyCount: mailbox.readyCount,
            pendingCount: mailbox.pendingCount,
            oldestImportedAt: mailbox.oldestImportedAt,
            fullImportRunning: mailbox.fullImportRunning,
            fullImportCompleted: mailbox.fullImportCompleted
        )
        if selectedThreadID == threadID {
            selectedThreadID = nil
            selectedMessageID = nil
            activeThreadID = nil
            activeMessageID = nil
            seedActiveSelectionIfNeeded()
        }
    }

    private func shouldRemoveFromCurrentMailbox(action: GmailThreadAction) -> Bool {
        switch action {
        case .archive:
            return activeMailboxLabel == .inbox
        case .unarchive:
            return activeMailboxLabel == .archive
        case .moveTrash:
            return activeMailboxLabel != .trash
        case .deleteForever:
            return true
        case .markRead:
            return false
        }
    }

    private var visibleMailbox: MailboxResponse? {
        if let activeMailbox, activeMailbox.label == activeMailboxLabel {
            return activeMailbox
        }
        return activeMailboxLabel == .inbox ? session?.mailbox : nil
    }

    private func visibleRows(in section: GmailThreadSection) -> [GmailThreadRow] {
        section.rows.filter { $0.isVisible(in: activeMailboxLabel) }
    }

    private func visibleChildren(for row: GmailThreadRow) -> [GmailThreadChildRow] {
        row.childRows.filter { $0.isVisible(in: activeMailboxLabel) }
    }

    private static func timeLabel(for value: String, sectionTitle: String) -> String {
        guard let date = ISO8601DateFormatter.shared.date(from: value) else {
            return value
        }
        if sectionTitle == "Today" {
            return DateFormatter.inboxTime.string(from: date)
        }
        return DateFormatter.inboxDate.string(from: date)
    }
}

private extension GmailThreadRow {
    func isVisible(in mailboxLabel: MailboxLabel) -> Bool {
        let rowLabels = Set((labelIDs + labels).map { $0.uppercased() })
        switch mailboxLabel {
        case .all:
            return true
        case .inbox:
            return rowLabels.contains("INBOX")
        case .sent:
            return rowLabels.contains("SENT")
        case .drafts:
            return rowLabels.contains("DRAFT")
        case .spam:
            return rowLabels.contains("SPAM")
        case .trash:
            return rowLabels.contains("TRASH")
        case .archive:
            return rowLabels.isDisjoint(with: ["INBOX", "SENT", "DRAFT", "SPAM", "TRASH"])
        }
    }
}

private extension GmailThreadChildRow {
    func isVisible(in mailboxLabel: MailboxLabel) -> Bool {
        let rowLabels = Set((labelIDs + labels).map { $0.uppercased() })
        switch mailboxLabel {
        case .all:
            return true
        case .inbox:
            return rowLabels.contains("INBOX")
        case .sent:
            return rowLabels.contains("SENT")
        case .drafts:
            return rowLabels.contains("DRAFT")
        case .spam:
            return rowLabels.contains("SPAM")
        case .trash:
            return rowLabels.contains("TRASH")
        case .archive:
            return rowLabels.isDisjoint(with: ["INBOX", "SENT", "DRAFT", "SPAM", "TRASH"])
        }
    }
}

private extension Array where Element: Hashable {
    func uniqued() -> [Element] {
        var seen = Set<Element>()
        return filter { seen.insert($0).inserted }
    }
}

private extension ThreadReaderResponse {
    var needsHTMLRenderDocumentRefresh: Bool {
        messages.contains { $0.needsHTMLRenderDocumentRefresh }
    }

    var needsReaderBodyRefresh: Bool {
        needsHTMLRenderDocumentRefresh || messages.contains { $0.needsReaderBodyRefresh }
    }
}

private extension ThreadMessage {
    var needsHTMLRenderDocumentRefresh: Bool {
        hasNonEmptyHTMLBody && !hasNonEmptyHTMLRenderDocument
    }

    private var hasNonEmptyHTMLBody: Bool {
        htmlBody?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty == false
    }

    private var hasNonEmptyHTMLRenderDocument: Bool {
        htmlRenderDocument?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty == false
    }

    var needsReaderBodyRefresh: Bool {
        let bodyMissing = body.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        let readerMissing = reader?.originalHTMLAvailable == false && htmlBody == nil && htmlRenderDocument == nil
        return bodyMissing || readerMissing
    }
}

private extension ISO8601DateFormatter {
    static let shared: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withColonSeparatorInTimeZone]
        return formatter
    }()

    static let backendActionTimestamp: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()
}

private extension DateFormatter {
    static let inboxTime: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "hh:mm a"
        return formatter
    }()

    static let inboxDate: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "MMM d"
        return formatter
    }()
}
