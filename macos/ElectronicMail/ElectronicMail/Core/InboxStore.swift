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
    let receivedAt: String
    let section: String
    let isUnread: Bool
    let isGrouped: Bool
    let isSelected: Bool
    let threadID: String
    let messageCount: Int
    let timeLabel: String

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

@MainActor
public final class InboxStore: ObservableObject {
    @Published public private(set) var phase: LoadPhase = .idle
    @Published private(set) var session: AppSessionResponse?
    @Published private(set) var refreshFailed = false
    @Published private(set) var selectedThreadID: String?
    @Published private(set) var activeThreadID: String?
    @Published private(set) var readerThreadID: String?
    @Published private(set) var readerThread: ThreadReaderResponse?
    @Published private(set) var readerRow: InboxRowViewModel?
    @Published private(set) var readerError: String?
    @Published private(set) var threadErrors: [String: String] = [:]
    @Published private(set) var openedThreads: [String: ThreadReaderResponse] = [:]
    @Published var navigationPlaceholderVisible = false

    private let client: AppClient
    private let sessionCache: AppSessionCache
    private let threadCache: ThreadCache
    private var inFlightSessionRefresh: Task<AppSessionResponse, Error>?
    private var inFlightThreads: [String: Task<ThreadReaderResponse, Error>] = [:]
    private var selectionPrefetchTask: Task<Void, Never>?
    private var syncLoopTask: Task<Void, Never>?
    private var lastSyncTriggerAt: Date?

    private let activeSyncInterval: TimeInterval = 8
    private let minimumSyncGap: TimeInterval = 7
    private let threadFetchLimit = 50

    public init(
        client: AppClient = LiveBackendAppClient(baseURL: AppConfiguration.defaultBackendURL),
        sessionCache: AppSessionCache = AppSessionCache(),
        threadCache: ThreadCache = ThreadCache()
    ) {
        self.client = client
        self.sessionCache = sessionCache
        self.threadCache = threadCache
    }

    public var runMode: AppRunMode {
        client.mode
    }

    public var backendURL: URL {
        client.baseURL
    }

    public func setSessionToken(_ token: String?) {
        client.sessionToken = token
        if token == nil {
            session = nil
            selectedThreadID = nil
            activeThreadID = nil
            readerThreadID = nil
            readerThread = nil
            readerRow = nil
            readerError = nil
            openedThreads = [:]
            threadErrors = [:]
            selectionPrefetchTask?.cancel()
            selectionPrefetchTask = nil
            sessionCache.clear()
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
        guard let mailbox = session?.mailbox else {
            return []
        }
        return mailbox.sections.map { section in
            InboxSectionViewModel(
                id: section.id,
                title: section.title,
                rows: section.rows.map { row in
                    InboxRowViewModel(
                        id: row.threadID,
                        sender: row.displaySender,
                        title: row.displayTitle,
                        receivedAt: row.latestReceivedAt,
                        section: section.title,
                        isUnread: row.isUnread,
                        isGrouped: row.isGrouped,
                        isSelected: selectedThreadID == row.threadID,
                        threadID: row.threadID,
                        messageCount: row.messageCount,
                        timeLabel: Self.timeLabel(for: row.latestReceivedAt, sectionTitle: section.title)
                    )
                }
            )
        }
    }

    public var flatRows: [InboxRowViewModel] {
        sections.flatMap(\.rows)
    }

    public func load() async {
        guard client.mode != .localBackend || client.sessionToken?.isEmpty == false else {
            phase = .failed("Sign in with Google to load your mailbox.")
            return
        }
        if let cached = sessionCache.read() {
            session = cached
            phase = .loaded
        } else {
            phase = .loading
        }
        await refresh()
    }

    public func refresh() async {
        await refresh(allowEmptyDashboard: false)
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
            sessionCache.write(merged)
            refreshFailed = false
            phase = .loaded
            seedActiveSelectionIfNeeded()
            refreshReaderRow()
            prefetchPriorityThreads()
        } catch is CancellationError {
            inFlightSessionRefresh = nil
        } catch {
            inFlightSessionRefresh = nil
            refreshFailed = true
            if session == nil, let cached = sessionCache.read() {
                session = cached
                phase = .loaded
            } else if session == nil {
                phase = .failed(error.localizedDescription)
            }
        }
    }

    public func select(threadID: String, prefetch: Bool = true) {
        selectedThreadID = threadID
        activeThreadID = threadID
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
                _ = openReader(threadID: activeThreadID)
            }
            return
        }
        _ = openReader(threadID: activeThreadID)
    }

    @discardableResult
    public func openReader(threadID: String) -> Task<Void, Never> {
        selectionPrefetchTask?.cancel()
        selectionPrefetchTask = nil
        selectedThreadID = threadID
        activeThreadID = threadID
        readerThreadID = threadID
        readerThread = openedThreads[threadID]
        readerRow = rowViewModel(threadID: threadID)
        readerError = nil
        threadErrors[threadID] = nil

        return Task { [weak self] in
            await self?.prefetchThread(threadID: threadID, force: false, silent: false)
        }
    }

    public func closeReader() {
        readerThreadID = nil
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
        let cachedThread = force ? nil : threadCache.read(userID: userID, threadID: threadID)
        if let cached = cachedThread {
            openedThreads[threadID] = cached
            updateReader(threadID: threadID, thread: cached, error: nil)
            if !cached.needsHTMLRenderDocumentRefresh {
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
            threadErrors[threadID] = nil
            updateReader(threadID: threadID, thread: thread, error: nil)
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
        do {
            _ = try await client.syncMailboxNow()
            await refresh()
        } catch {
            do {
                _ = try await client.triggerMailboxSync()
                try? await Task.sleep(nanoseconds: 2_000_000_000)
                await refresh()
                try? await Task.sleep(nanoseconds: 4_000_000_000)
                await refresh()
            } catch {}
        }
    }

    public func startLiveRefreshLoop() {
        guard client.mode == .localBackend, syncLoopTask == nil else {
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

    private func seedActiveSelectionIfNeeded() {
        if let activeThreadID, flatRows.contains(where: { $0.threadID == activeThreadID }) {
            if selectedThreadID == nil {
                selectedThreadID = activeThreadID
            }
            return
        }
        let firstThreadID = flatRows.first?.threadID
        activeThreadID = firstThreadID
        if selectedThreadID == nil || !flatRows.contains(where: { $0.threadID == selectedThreadID }) {
            selectedThreadID = firstThreadID
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

    private func prefetchPriorityThreads() {
        let rows = flatRows
        let grouped = rows.filter(\.isGrouped)
        let candidates = Array((rows.prefix(10) + grouped).map(\.threadID).uniqued().prefix(14))
        candidates.forEach { threadID in
            Task { await prefetchThread(threadID: threadID, force: false, silent: true) }
        }
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
}

private extension ISO8601DateFormatter {
    static let shared: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withColonSeparatorInTimeZone]
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
