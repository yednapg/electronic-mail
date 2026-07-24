import AppKit
import CoreServices
import CryptoKit
import Foundation

private struct PendingMailActionError: LocalizedError {
    let count: Int

    var errorDescription: String? {
        let noun = count == 1 ? "email change is" : "email changes are"
        return "\(count) \(noun) still waiting to sync. Check your connection and try signing out again."
    }
}

enum AttachmentFileHandlingResult: Equatable {
    case cancelled
    case opened(URL)
}

enum AttachmentFileHandlingError: LocalizedError {
    case couldNotSave(Error)
    case couldNotQuarantine(Error)
    case couldNotOpen

    var errorDescription: String? {
        switch self {
        case .couldNotSave(let error):
            return "The attachment could not be saved to the selected location. \(error.localizedDescription)"
        case .couldNotQuarantine(let error):
            return "The attachment was saved, but macOS could not mark it as a downloaded email attachment, so Electronic Mail did not open it. \(error.localizedDescription)"
        case .couldNotOpen:
            return "The attachment was saved, but macOS could not open it. Open it from Finder or choose a default app and try again."
        }
    }
}

enum AttachmentFileQuarantine {
    static let typeKey = kLSQuarantineTypeKey as String
    static let emailAttachmentType = kLSQuarantineTypeEmailAttachment as String

    static func apply(to url: URL) throws {
        var resourceValues = URLResourceValues()
        resourceValues.quarantineProperties = [
            typeKey: emailAttachmentType,
            kLSQuarantineAgentNameKey as String: "Electronic Mail",
            kLSQuarantineAgentBundleIdentifierKey as String: "app.electronicmail.mac"
        ]
        var mutableURL = url
        try mutableURL.setResourceValues(resourceValues)

        let appliedProperties = try mutableURL
            .resourceValues(forKeys: [.quarantinePropertiesKey])
            .quarantineProperties
        guard appliedProperties?[typeKey] as? String == emailAttachmentType else {
            throw VerificationError.metadataWasNotApplied
        }
    }

    private enum VerificationError: LocalizedError {
        case metadataWasNotApplied

        var errorDescription: String? {
            "The file's quarantine metadata could not be verified."
        }
    }
}

struct AttachmentFileOperations: Sendable {
    typealias SecurityScopeStarter = @Sendable (_ url: URL) -> Bool
    typealias SecurityScopeStopper = @Sendable (_ url: URL) -> Void
    typealias DataWriter = @Sendable (_ data: Data, _ url: URL) throws -> Void
    typealias FileQuarantiner = @Sendable (_ url: URL) throws -> Void
    typealias FileOpener = @Sendable (_ url: URL) -> Bool

    let startAccessingSecurityScope: SecurityScopeStarter
    let stopAccessingSecurityScope: SecurityScopeStopper
    let writeData: DataWriter
    let quarantineFile: FileQuarantiner
    let openFile: FileOpener

    func saveAndOpen(data: Data, at fileURL: URL) throws -> AttachmentFileHandlingResult {
        try Task.checkCancellation()
        let accessed = startAccessingSecurityScope(fileURL)
        defer {
            if accessed {
                stopAccessingSecurityScope(fileURL)
            }
        }

        try Task.checkCancellation()
        do {
            try writeData(data, fileURL)
        } catch {
            throw AttachmentFileHandlingError.couldNotSave(error)
        }

        try Task.checkCancellation()
        do {
            try quarantineFile(fileURL)
        } catch {
            throw AttachmentFileHandlingError.couldNotQuarantine(error)
        }

        try Task.checkCancellation()
        guard openFile(fileURL) else {
            throw AttachmentFileHandlingError.couldNotOpen
        }
        return .opened(fileURL)
    }
}

@MainActor
struct AttachmentFileHandler {
    typealias DestinationChooser = (_ suggestedFilename: String) -> URL?
    typealias SecurityScopeStarter = AttachmentFileOperations.SecurityScopeStarter
    typealias SecurityScopeStopper = AttachmentFileOperations.SecurityScopeStopper
    typealias DataWriter = AttachmentFileOperations.DataWriter
    typealias FileQuarantiner = AttachmentFileOperations.FileQuarantiner
    typealias FileOpener = AttachmentFileOperations.FileOpener

    private let chooseDestination: DestinationChooser
    private let operations: AttachmentFileOperations

    init(
        chooseDestination: @escaping DestinationChooser,
        startAccessingSecurityScope: @escaping SecurityScopeStarter,
        stopAccessingSecurityScope: @escaping SecurityScopeStopper,
        writeData: @escaping DataWriter,
        quarantineFile: @escaping FileQuarantiner,
        openFile: @escaping FileOpener
    ) {
        self.chooseDestination = chooseDestination
        operations = AttachmentFileOperations(
            startAccessingSecurityScope: startAccessingSecurityScope,
            stopAccessingSecurityScope: stopAccessingSecurityScope,
            writeData: writeData,
            quarantineFile: quarantineFile,
            openFile: openFile
        )
    }

    static var live: AttachmentFileHandler {
        AttachmentFileHandler(
            chooseDestination: { suggestedFilename in
                let panel = NSSavePanel()
                panel.nameFieldStringValue = suggestedFilename
                panel.canCreateDirectories = true
                panel.prompt = "Save and Open"
                guard panel.runModal() == .OK else {
                    return nil
                }
                return panel.url
            },
            startAccessingSecurityScope: { $0.startAccessingSecurityScopedResource() },
            stopAccessingSecurityScope: { $0.stopAccessingSecurityScopedResource() },
            writeData: { data, url in
                try data.write(to: url, options: .atomic)
            },
            quarantineFile: { url in
                try AttachmentFileQuarantine.apply(to: url)
            },
            openFile: { NSWorkspace.shared.open($0) }
        )
    }

    func saveAndOpen(
        _ attachment: DownloadedAttachment,
        suggestedFilename: String
    ) async throws -> AttachmentFileHandlingResult {
        try Task.checkCancellation()
        guard let fileURL = chooseDestination(suggestedFilename) else {
            return .cancelled
        }

        let operations = operations
        let data = attachment.data
        let fileTask = Task.detached(priority: .userInitiated) {
            try operations.saveAndOpen(data: data, at: fileURL)
        }
        return try await withTaskCancellationHandler {
            try await fileTask.value
        } onCancel: {
            fileTask.cancel()
        }
    }
}

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
    let threadID: String
    let focusedMessageID: String?
    let messageCount: Int
    let timeLabel: String
    let hasAttachments: Bool
    let presentationStatus: String?
    let isChild: Bool
    let isExpandable: Bool
    let isExpanded: Bool

    func visualTone(isSelected: Bool = false) -> InboxRowVisualTone {
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

public struct MailboxFolderCount: Equatable {
    let total: Int
    let unread: Int
}

enum MailboxBackgroundRefreshPolicy {
    static let maximumRealtimeEventAge: TimeInterval = 25

    static func shouldPollSyncState(
        supportsRealtimeUpdates: Bool,
        realtimeConnected: Bool,
        lastRealtimeEventAt: Date?,
        now: Date = Date(),
        maximumEventAge: TimeInterval = MailboxBackgroundRefreshPolicy.maximumRealtimeEventAge
    ) -> Bool {
        guard supportsRealtimeUpdates,
              realtimeConnected,
              let lastRealtimeEventAt else {
            return true
        }
        return now.timeIntervalSince(lastRealtimeEventAt) > maximumEventAge
    }
}

@usableFromInline
enum ReaderBodyRefreshPolicy {
    // The final fallback runs after the backend's first 30-second retry backoff,
    // even when the initial Gmail request itself took time to fail. Healthy SSE
    // hydration normally cancels this schedule much earlier.
    @usableFromInline
    static let defaultDelaysNanoseconds: [UInt64] = [
        1_000_000_000,
        2_000_000_000,
        4_000_000_000,
        8_000_000_000,
        16_000_000_000,
        45_000_000_000,
    ]
}

struct ThreadTaskOwnerRegistry {
    private var ownerIDs: [String: UUID] = [:]

    mutating func claim(threadID: String, ownerID: UUID = UUID()) -> UUID {
        ownerIDs[threadID] = ownerID
        return ownerID
    }

    func ownerID(for threadID: String) -> UUID? {
        ownerIDs[threadID]
    }

    @discardableResult
    mutating func release(threadID: String, ownerID: UUID) -> Bool {
        guard ownerIDs[threadID] == ownerID else {
            return false
        }
        ownerIDs[threadID] = nil
        return true
    }

    mutating func cancel(threadID: String) {
        ownerIDs[threadID] = nil
    }

    mutating func cancelAll() {
        ownerIDs = [:]
    }
}

private struct MailboxEventEnvelope {
    var mailboxRevision: String?
    var mailboxLabels: [String] = []
    var searchKey: String?
    var threadIDs: [String] = []
}

private struct MailboxPageContext: Equatable {
    let generation: UInt
    let label: MailboxLabel
    let searchQuery: String?
    let userID: String
}

private struct MailboxRefreshContext: Equatable {
    let requestID: UUID
    let accountGeneration: UInt
    let userID: String
    let label: MailboxLabel
    let allowCachedFallback: Bool
    let force: Bool
    let startedAt: Date
}

private struct InboxTimestampLabelCacheKey: Hashable {
    let value: String
    let usesTimeStyle: Bool
}

private struct InboxSectionsCache {
    let revision: UInt
    let sections: [InboxSectionViewModel]
    let flatRows: [InboxRowViewModel]
    let rowsByID: [String: InboxRowViewModel]
    let firstRowByThreadID: [String: InboxRowViewModel]
}

private struct ReaderLabelMutationKey: Hashable {
    let threadID: String
    let messageID: String
    let labelID: String
}

private struct ReaderLabelMutation {
    let id: UUID
    let threadID: String
    let labelID: String
    let isPresent: Bool
    let targetMessageID: String?
    let beganWithoutLoadedThread: Bool
    let previousPresenceByMessageID: [String: Bool]
}

private struct ReaderLabelIntentKey: Hashable {
    let threadID: String
    let labelID: String
    let targetMessageID: String?
}

private struct ReaderLabelIntentOverride {
    let ownerID: UUID
    let isPresent: Bool
    var settled: Bool
}

private actor LocalMailStoreWorker {
    private let store: LocalMailStore

    init(store: LocalMailStore) {
        self.store = store
    }

    func readSession() -> AppSessionResponse? {
        store.readSession()
    }

    func writeSession(_ session: AppSessionResponse) {
        store.writeSession(session)
    }

    func readMailbox(userID: String, label: MailboxLabel) -> MailboxResponse? {
        store.readMailbox(userID: userID, label: label)
    }

    func writeMailbox(_ mailbox: MailboxResponse, userID: String, label: MailboxLabel) {
        store.writeMailbox(mailbox, userID: userID, label: label)
    }

    func readThread(userID: String, threadID: String) -> ThreadReaderResponse? {
        store.readThread(userID: userID, threadID: threadID)
    }

    func writeThread(_ thread: ThreadReaderResponse, userID: String, threadID: String) {
        store.writeThread(thread, userID: userID, threadID: threadID)
    }

    func pendingThreadActionCount(userID: String) -> Int {
        store.pendingThreadActions().lazy.filter { $0.userID == userID }.count
    }
}

@MainActor
public final class InboxStore: ObservableObject {
    @Published public private(set) var phase: LoadPhase = .idle
    @Published private(set) var session: AppSessionResponse?
    @Published private(set) var activeMailboxLabel: MailboxLabel = .inbox {
        didSet { invalidateInboxSections() }
    }
    @Published private(set) var activeMailbox: MailboxResponse? {
        didSet { invalidateInboxSections() }
    }
    @Published private(set) var refreshFailed = false
    @Published private(set) var manualSyncInProgress = false
    @Published private(set) var selectedThreadID: String?
    @Published private(set) var selectedMessageID: String?
    private(set) var activeThreadID: String?
    private(set) var activeMessageID: String?
    @Published private(set) var readerThreadID: String?
    @Published private(set) var readerFocusedMessageID: String?
    @Published private(set) var readerThread: ThreadReaderResponse?
    @Published private(set) var readerRow: InboxRowViewModel?
    @Published private(set) var readerError: String?
    private(set) var threadErrors: [String: String] = [:]
    private(set) var openedThreads: [String: ThreadReaderResponse] = [:]
    @Published private(set) var mailboxPageLoading = false
    @Published private(set) var expandedThreadIDs: Set<String> = [] {
        didSet { invalidateInboxSections() }
    }
    private(set) var realtimeConnected = false
    private(set) var syncState: MailboxSyncStateResponse?
    @Published private(set) var mailboxCounts: [MailboxLabel: MailboxFolderCount] = [:]
    @Published public private(set) var pendingLocalActionCount = 0
    @Published private(set) var searchQuery = "" {
        didSet { invalidateInboxSections() }
    }
    @Published private(set) var searchResults: MailboxResponse? {
        didSet { invalidateInboxSections() }
    }
    @Published private(set) var searchInProgress = false
    @Published private(set) var searchError: String?
    private(set) var lastSSEConnectedAt: Date?
    private(set) var lastSSEDisconnectedAt: Date?
    private(set) var lastSSEEventID: String?
    private(set) var lastSSEEventType: String?
    private(set) var lastSSEEventAt: Date?
    private(set) var lastForcedMailboxRefreshAt: Date?
    private(set) var lastForcedMailboxRefreshError: String?
    @Published private(set) var attachmentErrorMessage: String?

    private let client: AppClient
    private let sessionCache: AppSessionCache
    private let threadCache: ThreadCache
    private let localMailStore: LocalMailStore
    private let localMailStoreWorker: LocalMailStoreWorker
    private let automaticallyPrefetchThreads: Bool
    private let bodyRefreshDelaysNanoseconds: [UInt64]
    private var inFlightSessionRefresh: Task<AppSessionResponse, Error>?
    private var inFlightMailboxRefresh: Task<MailboxResponse, Error>?
    private var inFlightMailboxRefreshContext: MailboxRefreshContext?
    private var inFlightMailboxPage: Task<MailboxResponse, Error>?
    private var inFlightThreads: [String: Task<ThreadReaderResponse, Error>] = [:]
    private var inFlightThreadLabelMutationGenerations: [String: UInt] = [:]
    private var selectionPrefetchTask: Task<Void, Never>?
    private var priorityPrefetchTask: Task<Void, Never>?
    private var syncLoopTask: Task<Void, Never>?
    private var eventStreamTask: Task<Void, Never>?
    private var eventRefreshTask: Task<Void, Never>?
    private var postSendRefreshTask: Task<Void, Never>?
    private var lastSyncTriggerAt: Date?
    private var lastMailboxRefreshAt: [MailboxLabel: Date] = [:]
    private var mailboxPageGeneration: UInt = 0
    private var mailboxPageRequestID: UUID?
    private var requestedMailboxPageCursors: Set<String> = []
    private var searchRequestID: UUID?
    private var searchHydrationRefreshTask: Task<Void, Never>?
    private var bodyRefreshAttempts: [String: Int] = [:]
    private var bodyRefreshTasks: [String: Task<Void, Never>] = [:]
    private var bodyRefreshTaskOwners = ThreadTaskOwnerRegistry()
    private var pendingHydrationRefreshThreadIDs: Set<String> = []
    private var hydrationRefreshTaskOwners = ThreadTaskOwnerRegistry()
    private var readActionQueuedKeys: Set<String> = []
    private var lastMailboxEventID: String?
    private var lastSeenMailboxRevision: String?
    private var pendingEventRefreshNeedsSession = false
    private var discardLoadedMailboxPagesOnNextRefresh = false
    private var timestampLabelCache: [InboxTimestampLabelCacheKey: String] = [:]
    private var inboxSectionsRevision: UInt = 0
    private var inboxSectionsCache: InboxSectionsCache?
    private var folderCountPrefetchEnabled = false
    private var folderCountsRefreshRevision: UInt = 0
    private var folderCountsRefreshOwnerID: UUID?
    private var lastCompletedFolderCountsRefreshAt: Date?
    private var inFlightFolderCountRequest: Task<MailboxResponse, Error>?
    private var inFlightFolderCountRequestID: UUID?
    private var accountOperationGeneration: UInt = 0
    private var readerLabelMutationOwners: [ReaderLabelMutationKey: UUID] = [:]
    private var readerLabelMutationGenerations: [String: UInt] = [:]
    private var readerLabelMutationLabelGenerations: [String: [String: UInt]] = [:]
    private var readerLabelIntentOverrides: [ReaderLabelIntentKey: ReaderLabelIntentOverride] = [:]

    private let activeSyncInterval: TimeInterval = 10
    private let minimumSyncGap: TimeInterval = 8
    private let minimumMailboxRefreshGap: TimeInterval = 20
    private let folderCountsFreshnessInterval: TimeInterval = 60
    private let eventRefreshDebounce: TimeInterval = 1
    private let threadFetchLimit = 50
    private let mailboxPageLimit = 100
    private let timestampLabelCacheLimit = 2_048
    private static let folderCountLabels: [MailboxLabel] = [
        .inbox,
        .starred,
        .drafts,
        .sent,
        .spam,
        .trash,
        .archive,
        .all,
    ]

    public init(
        client: AppClient = LiveBackendAppClient(baseURL: AppConfiguration.defaultBackendURL),
        sessionCache: AppSessionCache = AppSessionCache(),
        threadCache: ThreadCache = ThreadCache(),
        localMailStore: LocalMailStore = NoopLocalMailStore(),
        automaticallyPrefetchThreads: Bool = false,
        bodyRefreshDelaysNanoseconds: [UInt64] = ReaderBodyRefreshPolicy.defaultDelaysNanoseconds
    ) {
        self.client = client
        self.sessionCache = sessionCache
        self.threadCache = threadCache
        self.localMailStore = localMailStore
        self.localMailStoreWorker = LocalMailStoreWorker(store: localMailStore)
        self.automaticallyPrefetchThreads = automaticallyPrefetchThreads
        self.bodyRefreshDelaysNanoseconds = bodyRefreshDelaysNanoseconds
    }

    public var runMode: AppRunMode {
        client.mode
    }

    public var backendURL: URL {
        client.baseURL
    }

    public var hasSessionToken: Bool {
        client.sessionToken?.isEmpty == false
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
        updateSessionToken(token, preservingPendingThreadActions: false)
    }

    private func expireSessionForReauthentication() {
        updateSessionToken(nil, preservingPendingThreadActions: true)
    }

    private func updateSessionToken(_ token: String?, preservingPendingThreadActions: Bool) {
        let previousToken = client.sessionToken
        client.sessionToken = token
        let tokenChanged = previousToken != token
        if tokenChanged {
            invalidateAccountScopedOperations()
        }
        if token == nil {
            let pendingThreadActions = preservingPendingThreadActions && tokenChanged
                ? localMailStore.pendingThreadActions()
                : []
            session = nil
            activeMailboxLabel = .inbox
            activeMailbox = nil
            selectedThreadID = nil
            selectedMessageID = nil
            activeThreadID = nil
            activeMessageID = nil
            manualSyncInProgress = false
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
            cancelActiveMailboxRefresh()
            selectionPrefetchTask?.cancel()
            selectionPrefetchTask = nil
            priorityPrefetchTask?.cancel()
            priorityPrefetchTask = nil
            stopLiveRefreshLoop()
            eventRefreshTask?.cancel()
            eventRefreshTask = nil
            pendingEventRefreshNeedsSession = false
            lastMailboxEventID = nil
            lastSeenMailboxRevision = nil
            discardLoadedMailboxPagesOnNextRefresh = false
            realtimeConnected = false
            syncState = nil
            mailboxCounts = [:]
            lastCompletedFolderCountsRefreshAt = nil
            folderCountPrefetchEnabled = false
            cancelFolderCountRefresh()
            pendingLocalActionCount = 0
            searchQuery = ""
            searchResults = nil
            searchInProgress = false
            searchError = nil
            searchHydrationRefreshTask?.cancel()
            searchHydrationRefreshTask = nil
            lastSSEConnectedAt = nil
            lastSSEDisconnectedAt = nil
            lastSSEEventID = nil
            lastSSEEventType = nil
            lastSSEEventAt = nil
            lastForcedMailboxRefreshAt = nil
            lastForcedMailboxRefreshError = nil
            timestampLabelCache.removeAll(keepingCapacity: false)
            sessionCache.clear()
            if tokenChanged {
                localMailStore.clearAll()
                pendingThreadActions.forEach(localMailStore.writePendingThreadAction)
            }
            threadCache.clearMemory()
            phase = .idle
        } else if previousToken != token {
            stopMailboxEventStream()
            startLiveRefreshLoop()
        }
    }

    public func exchangeMobileSession(loginCode: String) async throws -> MobileSessionExchangeResponse {
        let response = try await client.exchangeMobileSession(loginCode: loginCode)
        setSessionToken(response.sessionToken)
        return response
    }

    public func exchangeMobileSession(grant: MobileAuthenticationGrant) async throws -> MobileSessionExchangeResponse {
        let response = try await client.exchangeMobileSession(grant: grant)
        setSessionToken(response.sessionToken)
        return response
    }

    public func logoutRemoteSession() async throws {
        await refreshPendingLocalActionCount()
        let pendingBeforeReplay = pendingLocalActionCount
        if pendingBeforeReplay > 0 {
            _ = try await client.syncMailboxNow()
            await refreshPendingLocalActionCount()
            let pendingAfterReplay = pendingLocalActionCount
            guard pendingAfterReplay == 0 else {
                throw PendingMailActionError(count: pendingAfterReplay)
            }
        }
        try await client.logout()
    }

    public func disconnectGoogleAndDeleteData() async throws {
        try await client.disconnectGoogle(deleteData: true, revokeSessions: true)
    }

    public func deleteAccountPermanently() async throws {
        try await client.deleteAccount()
    }

    public func deleteSyncedGoogleData() async throws {
        try await client.deleteGoogleData()
        invalidateAccountScopedOperations()
        localMailStore.clearAll()
        pendingLocalActionCount = 0
        threadCache.clearMemory()
        sessionCache.clear()
        activeMailbox = nil
        searchResults = nil
        openedThreads = [:]
        timestampLabelCache.removeAll(keepingCapacity: false)
        mailboxCounts = [:]
        lastCompletedFolderCountsRefreshAt = nil
        selectedThreadID = nil
        readerThreadID = nil
        readerThread = nil
        phase = .loading
        await refresh(allowEmptyDashboard: true, forceMailbox: true)
        await refreshFolderCounts()
    }

    public var sections: [InboxSectionViewModel] {
        if let inboxSectionsCache, inboxSectionsCache.revision == inboxSectionsRevision {
            return inboxSectionsCache.sections
        }
        guard let mailbox = visibleMailbox else {
            inboxSectionsCache = InboxSectionsCache(
                revision: inboxSectionsRevision,
                sections: [],
                flatRows: [],
                rowsByID: [:],
                firstRowByThreadID: [:]
            )
            return []
        }
        let sections: [InboxSectionViewModel] = mailbox.sections.compactMap { section -> InboxSectionViewModel? in
            let visibleParentRows = visibleRows(in: section)
            var rows: [InboxRowViewModel] = []
            rows.reserveCapacity(visibleParentRows.count)
            for row in visibleParentRows {
                let childRows = visibleChildren(for: row)
                let isExpanded = expandedThreadIDs.contains(row.threadID)
                let parent = InboxRowViewModel(
                    id: row.threadID,
                    sender: row.displaySender,
                    title: row.displayTitle,
                    summary: row.displaySummary,
                    receivedAt: row.latestReceivedAt,
                    section: section.title,
                    isUnread: row.isUnread,
                    isGrouped: row.isGrouped,
                    threadID: row.threadID,
                    focusedMessageID: nil,
                    messageCount: row.messageCount,
                    timeLabel: timeLabel(for: row.latestReceivedAt, sectionTitle: section.title),
                    hasAttachments: row.hasAttachments == true || (row.attachmentCount ?? 0) > 0,
                    presentationStatus: row.presentationStatus,
                    isChild: false,
                    isExpandable: childRows.count > 1,
                    isExpanded: isExpanded
                )
                rows.append(parent)
                guard isExpanded else {
                    continue
                }
                rows.reserveCapacity(rows.count + childRows.count)
                for child in childRows {
                    rows.append(
                        InboxRowViewModel(
                            id: "\(row.threadID)::message::\(child.messageID)",
                            sender: child.displaySender,
                            title: child.displayTitle,
                            summary: child.snippet,
                            receivedAt: child.receivedAt,
                            section: section.title,
                            isUnread: child.isUnread,
                            isGrouped: false,
                            threadID: row.threadID,
                            focusedMessageID: child.messageID,
                            messageCount: 1,
                            timeLabel: timeLabel(for: child.receivedAt, sectionTitle: section.title),
                            hasAttachments: false,
                            presentationStatus: row.presentationStatus,
                            isChild: true,
                            isExpandable: false,
                            isExpanded: false
                        )
                    )
                }
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
        let flatRows = sections.flatMap(\.rows)
        var rowsByID: [String: InboxRowViewModel] = [:]
        var firstRowByThreadID: [String: InboxRowViewModel] = [:]
        rowsByID.reserveCapacity(flatRows.count)
        firstRowByThreadID.reserveCapacity(flatRows.count)
        for row in flatRows {
            rowsByID[row.id] = row
            if firstRowByThreadID[row.threadID] == nil {
                firstRowByThreadID[row.threadID] = row
            }
        }
        inboxSectionsCache = InboxSectionsCache(
            revision: inboxSectionsRevision,
            sections: sections,
            flatRows: flatRows,
            rowsByID: rowsByID,
            firstRowByThreadID: firstRowByThreadID
        )
        return sections
    }

    public var flatRows: [InboxRowViewModel] {
        _ = sections
        return inboxSectionsCache?.flatRows ?? []
    }

    var inboxPresentationRevision: UInt {
        inboxSectionsRevision
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

    public var activeMailboxCount: MailboxFolderCount? {
        mailboxCounts[activeMailboxLabel]
    }

    public var isSearchActive: Bool {
        !searchQuery.isEmpty
    }

    public var syncStatusText: String {
        if manualSyncInProgress {
            return "Syncing..."
        }
        if refreshFailed || syncState?.lastSyncError?.isEmpty == false || syncState?.lastActionError?.isEmpty == false {
            return "Sync issue"
        }
        if realtimeConnected {
            return "Up to date"
        }
        return syncState == nil ? "Checking mail..." : "Up to date"
    }

    public var isReadyForMainInterface: Bool {
        guard let readiness = session?.readiness else {
            return false
        }
        return readiness.readyToEnter
    }

    public var canEnterWithBuildingDashboard: Bool {
        guard let readiness = session?.readiness else {
            return false
        }
        return readiness.mailboxReady
    }

    public var canLoadMoreMailbox: Bool {
        guard let cursor = visibleMailbox?.nextCursor, !cursor.isEmpty else {
            return false
        }
        return !requestedMailboxPageCursors.contains(cursor)
    }

    public var mailboxPageCursor: String? {
        guard let cursor = visibleMailbox?.nextCursor, !cursor.isEmpty else {
            return nil
        }
        return cursor
    }

    public var mailboxFooterText: String? {
        mailboxFooter?.text
    }

    public var mailboxTitle: String {
        if isSearchActive {
            return "Search"
        }
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
        case .starred:
            return "Starred"
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

        if let cursor = mailbox.nextCursor, !cursor.isEmpty {
            let cursorAlreadyRequested = requestedMailboxPageCursors.contains(cursor)
            return InboxMailboxFooterViewModel(
                text: cursorAlreadyRequested && !mailboxPageLoading
                    ? "Limit reached"
                    : "Showing \(loadedThreads) of \(totalThreads). Load more...",
                progress: progress,
                canLoadMore: !cursorAlreadyRequested
            )
        }

        if totalThreads == 0 {
            return nil
        }

        if mailbox.fullImportRunning == true {
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
        let memoryCachedSession = sessionCache.read()
        let diskCachedSession = memoryCachedSession == nil ? await localMailStoreWorker.readSession() : nil
        if let cached = memoryCachedSession ?? diskCachedSession {
            session = cached
            activeMailbox = await localMailStoreWorker.readMailbox(userID: cached.user.id, label: activeMailboxLabel)
            phase = activeMailbox == nil ? .loading : .loaded
            await refreshPendingLocalActionCount()
        } else {
            phase = .loading
        }
        await refresh(allowEmptyDashboard: false, forceMailbox: true)
        if session != nil {
            startLiveRefreshLoop()
            await refreshFolderCounts()
        }
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

    public func sendCompose(
        clientSendID: String = UUID().uuidString,
        to: [String],
        cc: [String] = [],
        bcc: [String] = [],
        subject: String,
        bodyText: String,
        attachments: [MailAttachmentUpload] = []
    ) async throws -> MailSendResponse {
        guard canSendMail else {
            return missingSendScopeResponse(clientSendID: clientSendID)
        }

        let response = try await client.sendCompose(
            MailComposeRequest(
                clientSendID: clientSendID,
                to: to,
                cc: cc,
                bcc: bcc,
                subject: subject,
                bodyText: bodyText,
                bodyHTML: nil,
                attachments: attachments,
                createdAt: ISO8601DateFormatter.backendActionTimestamp.string(from: Date())
            )
        )
        scheduleRefreshAfterConfirmedSend(response)
        return response
    }

    public func sendReply(
        clientSendID: String = UUID().uuidString,
        threadID: String,
        sourceMessageID: String? = nil,
        mode: MailReplyMode = .reply,
        to: [String] = [],
        cc: [String] = [],
        bcc: [String] = [],
        subject: String? = nil,
        bodyText: String,
        attachments: [MailAttachmentUpload] = [],
        includeOriginalAttachments: Bool = false
    ) async throws -> MailSendResponse {
        guard canSendMail else {
            return missingSendScopeResponse(clientSendID: clientSendID, mailboxThreadID: threadID)
        }

        let response = try await client.sendReply(
            threadID: threadID,
            request: MailReplyRequest(
                clientSendID: clientSendID,
                sourceMessageID: sourceMessageID,
                mode: mode,
                to: to,
                cc: cc,
                bcc: bcc,
                subject: subject,
                bodyText: bodyText,
                bodyHTML: nil,
                attachments: attachments,
                includeOriginalAttachments: includeOriginalAttachments,
                createdAt: ISO8601DateFormatter.backendActionTimestamp.string(from: Date())
            )
        )
        scheduleRefreshAfterConfirmedSend(response)
        return response
    }

    public func sendStatus(serverSendID: String) async throws -> MailSendResponse {
        do {
            let response = try await client.sendStatus(serverSendID: serverSendID)
            scheduleRefreshAfterConfirmedSend(response)
            return response
        } catch {
            _ = transitionToReauthenticationIfNeeded(for: error)
            throw error
        }
    }

    public func retrySend(serverSendID: String) async throws -> MailSendResponse {
        do {
            let response = try await client.retrySend(serverSendID: serverSendID)
            scheduleRefreshAfterConfirmedSend(response)
            return response
        } catch {
            _ = transitionToReauthenticationIfNeeded(for: error)
            throw error
        }
    }

    private func missingSendScopeResponse(
        clientSendID: String,
        mailboxThreadID: String? = nil
    ) -> MailSendResponse {
        MailSendResponse(
            clientSendID: clientSendID,
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

    private func scheduleRefreshAfterConfirmedSend(
        _ response: MailSendResponse,
        forceMailbox: Bool = false,
        includeFolderCounts: Bool = false
    ) {
        guard response.state == .sent else {
            return
        }
        postSendRefreshTask?.cancel()
        postSendRefreshTask = Task { [weak self] in
            await self?.refreshAfterSend(
                response,
                forceMailbox: forceMailbox,
                includeFolderCounts: includeFolderCounts
            )
        }
    }

    private func refreshAfterSend(
        _ response: MailSendResponse,
        forceMailbox: Bool,
        includeFolderCounts: Bool
    ) async {
        discardLoadedMailboxPagesOnNextRefresh = true
        await refresh(allowEmptyDashboard: true, forceMailbox: forceMailbox)
        guard !Task.isCancelled else { return }
        if let threadID = response.mailboxThreadID ?? readerThreadID {
            await prefetchThread(threadID: threadID, force: true, silent: true)
        }
        guard !Task.isCancelled else { return }
        if includeFolderCounts {
            await refreshFolderCounts()
        }
    }

    public func saveDraft(_ request: MailDraftSaveRequest) async throws -> MailDraftResponse {
        let response: MailDraftResponse
        if let gmailDraftID = request.gmailDraftID, !gmailDraftID.isEmpty {
            response = try await client.updateDraft(gmailDraftID: gmailDraftID, request: request)
        } else {
            response = try await client.createDraft(request)
        }
        if response.state == .saved {
            lastMailboxRefreshAt[.drafts] = nil
        }
        return response
    }

    public func draft(mailboxThreadID: String) async throws -> MailDraftResponse {
        try await client.draft(mailboxThreadID: mailboxThreadID)
    }

    public func deleteDraft(gmailDraftID: String) async throws {
        try await client.deleteDraft(gmailDraftID: gmailDraftID)
        lastMailboxRefreshAt[.drafts] = nil
        if activeMailboxLabel == .drafts {
            await refreshActiveMailbox(allowCachedFallback: true, force: true)
        }
        await refreshFolderCounts()
    }

    public func sendDraft(gmailDraftID: String, clientDraftID: String, clientSendID: String) async throws -> MailSendResponse {
        let response = try await client.sendDraft(
            gmailDraftID: gmailDraftID,
            request: MailDraftSendRequest(clientSendID: clientSendID, clientDraftID: clientDraftID)
        )
        scheduleRefreshAfterConfirmedSend(
            response,
            forceMailbox: true,
            includeFolderCounts: true
        )
        return response
    }

    public func searchMailbox(_ query: String) async {
        let normalized = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !normalized.isEmpty else {
            clearSearch()
            return
        }
        searchHydrationRefreshTask?.cancel()
        searchHydrationRefreshTask = nil
        resetMailboxPagination()
        let requestID = UUID()
        searchRequestID = requestID
        searchQuery = normalized
        searchResults = nil
        searchInProgress = true
        searchError = nil
        defer {
            if searchRequestID == requestID {
                searchRequestID = nil
                searchInProgress = false
            }
        }
        do {
            let response = try await client.searchMailbox(
                query: normalized,
                label: activeMailboxLabel,
                limit: mailboxPageLimit,
                cursor: nil,
                hydrateInBackground: true
            )
            guard searchRequestID == requestID, searchQuery == normalized else { return }
            searchResults = response
            selectedThreadID = nil
            selectedMessageID = nil
            activeThreadID = nil
            activeMessageID = nil
            seedActiveSelectionIfNeeded()
        } catch {
            guard searchRequestID == requestID, searchQuery == normalized else { return }
            searchError = error.localizedDescription
            searchResults = nil
        }
    }

    public func clearSearch() {
        searchHydrationRefreshTask?.cancel()
        searchHydrationRefreshTask = nil
        resetMailboxPagination()
        searchRequestID = nil
        searchQuery = ""
        searchResults = nil
        searchError = nil
        searchInProgress = false
        selectedThreadID = nil
        selectedMessageID = nil
        activeThreadID = nil
        activeMessageID = nil
        seedActiveSelectionIfNeeded()
    }

    public func completeEntity(entityID: String, note: String? = nil) async throws -> EntityOutcomeResponse {
        let outcome = try await client.completeEntity(entityID, request: EntityOutcomeRequest(note: note))
        await refresh(allowEmptyDashboard: true)
        return outcome
    }

    private func refresh(allowEmptyDashboard: Bool, forceMailbox: Bool = false) async {
        let operationGeneration = accountOperationGeneration
        do {
            let task = inFlightSessionRefresh ?? Task { [client] in
                try await client.appSession()
            }
            inFlightSessionRefresh = task
            let next = try await task.value
            guard operationGeneration == accountOperationGeneration else {
                return
            }
            inFlightSessionRefresh = nil
            let merged = sessionCache.merge(current: session ?? sessionCache.read(), next: next, allowEmptyDashboard: allowEmptyDashboard)
            let sessionChanged = session != merged
            if sessionChanged {
                session = merged
            }
            sessionCache.write(merged)
            await localMailStoreWorker.writeSession(merged)
            await refreshPendingLocalActionCount()
            if refreshFailed {
                refreshFailed = false
            }
            if phase != .loaded {
                phase = .loaded
            }
            await refreshActiveMailbox(allowCachedFallback: true, force: forceMailbox || activeMailbox == nil)
            seedActiveSelectionIfNeeded()
            refreshReaderRow()
            if automaticallyPrefetchThreads {
                prefetchPriorityThreads()
            }
        } catch is CancellationError {
            guard operationGeneration == accountOperationGeneration else {
                return
            }
            inFlightSessionRefresh = nil
        } catch {
            guard operationGeneration == accountOperationGeneration else {
                return
            }
            inFlightSessionRefresh = nil
            if transitionToReauthenticationIfNeeded(for: error) {
                return
            }
            if !refreshFailed {
                refreshFailed = true
            }
            let memoryCachedSession = sessionCache.read()
            let diskCachedSession = memoryCachedSession == nil ? await localMailStoreWorker.readSession() : nil
            if session == nil, let cached = memoryCachedSession ?? diskCachedSession {
                if session != cached {
                    session = cached
                }
                let cachedMailbox = await localMailStoreWorker.readMailbox(userID: cached.user.id, label: activeMailboxLabel)
                if activeMailbox != cachedMailbox {
                    activeMailbox = cachedMailbox
                }
                if activeMailbox == nil, activeMailboxLabel == .inbox {
                    if activeMailbox != cached.mailbox {
                        activeMailbox = cached.mailbox
                    }
                }
                let nextPhase: LoadPhase = activeMailbox == nil ? .failed(error.localizedDescription) : .loaded
                if phase != nextPhase {
                    phase = nextPhase
                }
            } else if activeMailbox == nil,
                      activeMailboxLabel == .inbox,
                      let cachedInbox = session?.mailbox {
                if activeMailbox != cachedInbox {
                    activeMailbox = cachedInbox
                }
                if phase != .loaded {
                    phase = .loaded
                }
                seedActiveSelectionIfNeeded()
            } else if session == nil {
                let nextPhase = LoadPhase.failed(error.localizedDescription)
                if phase != nextPhase {
                    phase = nextPhase
                }
            }
        }
    }

    private static func isAuthenticationFailure(_ error: Error) -> Bool {
        guard case APIError.httpStatus(let status) = error else {
            return false
        }
        return status == 401 || status == 403
    }

    @discardableResult
    private func transitionToReauthenticationIfNeeded(for error: Error) -> Bool {
        guard Self.isAuthenticationFailure(error) else {
            return false
        }
        transitionToReauthentication()
        return true
    }

    private func transitionToReauthentication() {
        refreshFailed = false
        expireSessionForReauthentication()
        phase = .failed("Sign in with Google to load your mailbox.")
    }

    private func refreshActiveMailbox(allowCachedFallback: Bool, force: Bool = false) async {
        guard let userID = session?.user.id else {
            return
        }
        let label = activeMailboxLabel
        let now = Date()
        if !force,
           let lastRefresh = lastMailboxRefreshAt[label],
           activeMailbox != nil,
           now.timeIntervalSince(lastRefresh) < minimumMailboxRefreshGap {
            return
        }

        let context: MailboxRefreshContext
        let task: Task<MailboxResponse, Error>
        if let existingTask = inFlightMailboxRefresh,
           let existingContext = inFlightMailboxRefreshContext,
           existingContext.accountGeneration == accountOperationGeneration,
           existingContext.userID == userID,
           existingContext.label == label,
           !force || existingContext.force {
            context = existingContext
            task = existingTask
        } else {
            cancelActiveMailboxRefresh()
            context = MailboxRefreshContext(
                requestID: UUID(),
                accountGeneration: accountOperationGeneration,
                userID: userID,
                label: label,
                allowCachedFallback: allowCachedFallback,
                force: force,
                startedAt: now
            )
            task = Task { [client, mailboxPageLimit] in
                try Task.checkCancellation()
                return try await client.mailbox(label: label, limit: mailboxPageLimit, cursor: nil)
            }
            inFlightMailboxRefreshContext = context
            inFlightMailboxRefresh = task
        }

        do {
            let mailbox = try await task.value
            guard ownsActiveMailboxRefresh(context),
                  context.accountGeneration == accountOperationGeneration,
                  session?.user.id == context.userID,
                  activeMailboxLabel == context.label else {
                return
            }
            clearActiveMailboxRefresh(context)
            lastMailboxRefreshAt[context.label] = context.startedAt

            let shouldResetPagination = context.force
                || discardLoadedMailboxPagesOnNextRefresh
                || Self.mailboxRevisionChanged(from: activeMailbox, to: mailbox)
            let merged = activeMailbox?.preservingLoadedPages(
                afterRefreshingFirstPage: mailbox,
                discardStalePages: context.force || discardLoadedMailboxPagesOnNextRefresh
            ) ?? mailbox
            if shouldResetPagination {
                resetMailboxPagination()
            }
            discardLoadedMailboxPagesOnNextRefresh = false
            let mailboxChanged = activeMailbox != merged
            if mailboxChanged {
                activeMailbox = merged
            }
            updateFolderCount(from: merged)
            if let revision = merged.mailboxRevision, !revision.isEmpty {
                lastSeenMailboxRevision = revision
            }
            if context.force {
                lastForcedMailboxRefreshAt = Date()
                lastForcedMailboxRefreshError = nil
            }
            if refreshFailed {
                refreshFailed = false
            }
            if phase != .loaded {
                phase = .loaded
            }
            seedActiveSelectionIfNeeded()
            refreshReaderRow()
            if automaticallyPrefetchThreads {
                prefetchPriorityThreads()
            }
            await localMailStoreWorker.writeMailbox(
                merged,
                userID: context.userID,
                label: context.label
            )
        } catch is CancellationError {
            clearActiveMailboxRefresh(context)
        } catch {
            guard ownsActiveMailboxRefresh(context),
                  context.accountGeneration == accountOperationGeneration,
                  session?.user.id == context.userID,
                  activeMailboxLabel == context.label else {
                return
            }
            inFlightMailboxRefresh = nil
            let cached = context.allowCachedFallback
                ? await localMailStoreWorker.readMailbox(userID: context.userID, label: context.label)
                : nil
            guard ownsActiveMailboxRefresh(context),
                  context.accountGeneration == accountOperationGeneration,
                  session?.user.id == context.userID,
                  activeMailboxLabel == context.label else {
                return
            }
            clearActiveMailboxRefresh(context)
            if context.force {
                lastForcedMailboxRefreshError = error.localizedDescription
            }
            if !refreshFailed {
                refreshFailed = true
            }
            if let cached {
                if activeMailbox != cached {
                    activeMailbox = cached
                }
                if phase != .loaded {
                    phase = .loaded
                }
                seedActiveSelectionIfNeeded()
            } else if context.allowCachedFallback,
                      context.label == .inbox,
                      let sessionInbox = session?.mailbox {
                if activeMailbox != sessionInbox {
                    activeMailbox = sessionInbox
                }
                if phase != .loaded {
                    phase = .loaded
                }
                seedActiveSelectionIfNeeded()
            } else if activeMailbox == nil {
                let nextPhase = LoadPhase.failed(error.localizedDescription)
                if phase != nextPhase {
                    phase = nextPhase
                }
            }
        }
    }

    private func ownsActiveMailboxRefresh(_ context: MailboxRefreshContext) -> Bool {
        inFlightMailboxRefreshContext == context
    }

    private func clearActiveMailboxRefresh(_ context: MailboxRefreshContext) {
        guard ownsActiveMailboxRefresh(context) else {
            return
        }
        inFlightMailboxRefresh = nil
        inFlightMailboxRefreshContext = nil
    }

    private func cancelActiveMailboxRefresh() {
        inFlightMailboxRefresh?.cancel()
        inFlightMailboxRefresh = nil
        inFlightMailboxRefreshContext = nil
    }

    public func loadMoreMailbox(automatic _: Bool = false) async {
        guard !mailboxPageLoading,
              let cursor = visibleMailbox?.nextCursor,
              !cursor.isEmpty,
              let userID = session?.user.id,
              requestedMailboxPageCursors.insert(cursor).inserted else {
            return
        }

        let context = MailboxPageContext(
            generation: mailboxPageGeneration,
            label: activeMailboxLabel,
            searchQuery: isSearchActive ? searchQuery : nil,
            userID: userID
        )
        let requestID = UUID()
        mailboxPageRequestID = requestID
        mailboxPageLoading = true
        let task = Task { [client, mailboxPageLimit] in
            try Task.checkCancellation()
            let page: MailboxResponse
            if let query = context.searchQuery {
                page = try await client.searchMailbox(
                    query: query,
                    label: context.label,
                    limit: mailboxPageLimit,
                    cursor: cursor,
                    hydrateInBackground: true
                )
            } else {
                page = try await client.mailbox(label: context.label, limit: mailboxPageLimit, cursor: cursor)
            }
            try Task.checkCancellation()
            return page
        }
        inFlightMailboxPage = task
        defer {
            if mailboxPageRequestID == requestID {
                inFlightMailboxPage = nil
                mailboxPageRequestID = nil
                mailboxPageLoading = false
            }
        }
        do {
            let page = try await task.value
            guard mailboxPageRequestID == requestID,
                  matchesMailboxPageContext(context),
                  visibleMailbox?.nextCursor == cursor,
                  page.label == context.label else {
                if matchesMailboxPageContext(context) {
                    requestedMailboxPageCursors.remove(cursor)
                }
                return
            }
            let current = visibleMailbox
            let merged = current?.appendingPage(page) ?? page
            if context.searchQuery != nil {
                if searchResults != merged {
                    searchResults = merged
                }
            } else {
                if activeMailbox != merged {
                    activeMailbox = merged
                }
                updateFolderCount(from: merged)
                await localMailStoreWorker.writeMailbox(merged, userID: userID, label: context.label)
            }
            if refreshFailed {
                refreshFailed = false
            }
            seedActiveSelectionIfNeeded()
            refreshReaderRow()
        } catch is CancellationError {
            if matchesMailboxPageContext(context) {
                requestedMailboxPageCursors.remove(cursor)
            }
        } catch {
            if matchesMailboxPageContext(context) {
                requestedMailboxPageCursors.remove(cursor)
                refreshFailed = true
            }
        }
    }

    public func select(threadID: String, prefetch: Bool = true) {
        select(threadID: threadID, focusedMessageID: nil, prefetch: prefetch)
    }

    public func select(threadID: String, focusedMessageID: String?, prefetch: Bool = true) {
        if selectedThreadID != threadID {
            selectedThreadID = threadID
        }
        if selectedMessageID != focusedMessageID {
            selectedMessageID = focusedMessageID
        }
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

    public func clearSelection() {
        guard selectedThreadID != nil || selectedMessageID != nil || activeThreadID != nil || activeMessageID != nil else {
            return
        }
        selectionPrefetchTask?.cancel()
        selectionPrefetchTask = nil
        selectedThreadID = nil
        selectedMessageID = nil
        activeThreadID = nil
        activeMessageID = nil
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
        if let previousThreadID = readerThreadID, previousThreadID != threadID {
            cancelBodyRefresh(for: previousThreadID)
            pendingHydrationRefreshThreadIDs.remove(previousThreadID)
        }
        selectionPrefetchTask?.cancel()
        selectionPrefetchTask = nil
        if selectedThreadID != threadID {
            selectedThreadID = threadID
        }
        if selectedMessageID != focusedMessageID {
            selectedMessageID = focusedMessageID
        }
        activeThreadID = threadID
        activeMessageID = focusedMessageID
        if readerThreadID != threadID {
            readerThreadID = threadID
        }
        if readerFocusedMessageID != focusedMessageID {
            readerFocusedMessageID = focusedMessageID
        }
        let nextThread = openedThreads[threadID]
        if readerThread != nextThread {
            readerThread = nextThread
        }
        let nextRow = rowViewModel(threadID: threadID)
        if readerRow != nextRow {
            readerRow = nextRow
        }
        if readerError != nil {
            readerError = nil
        }
        threadErrors[threadID] = nil
        markReadAfterOpening(threadID: threadID, focusedMessageID: focusedMessageID)

        return Task { [weak self] in
            await self?.prefetchThread(threadID: threadID, force: false, silent: false)
        }
    }

    public func closeReader() {
        if let readerThreadID {
            cancelBodyRefresh(for: readerThreadID)
            pendingHydrationRefreshThreadIDs.remove(readerThreadID)
        }
        if readerThreadID != nil {
            readerThreadID = nil
        }
        if readerFocusedMessageID != nil {
            readerFocusedMessageID = nil
        }
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
        let operationGeneration = accountOperationGeneration
        let userID = session?.user.id
        cancelFolderCountRefresh()
        if let readerThreadID {
            cancelBodyRefresh(for: readerThreadID)
            pendingHydrationRefreshThreadIDs.remove(readerThreadID)
        }
        activeMailboxLabel = label
        clearSearch()
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
        cancelActiveMailboxRefresh()
        lastMailboxRefreshAt[label] = nil
        if let userID,
           let cached = await localMailStoreWorker.readMailbox(userID: userID, label: label) {
            guard operationGeneration == accountOperationGeneration,
                  session?.user.id == userID,
                  activeMailboxLabel == label else {
                return
            }
            activeMailbox = cached
            phase = .loaded
            seedActiveSelectionIfNeeded()
        } else {
            guard operationGeneration == accountOperationGeneration,
                  session?.user.id == userID,
                  activeMailboxLabel == label else {
                return
            }
            activeMailbox = nil
        }
        await refreshActiveMailbox(allowCachedFallback: true, force: true)
        guard operationGeneration == accountOperationGeneration,
              session?.user.id == userID,
              activeMailboxLabel == label else {
            return
        }
        await refreshFolderCountsIfNeeded()
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
                let response = try await client.syncMailboxNow()
                syncState = response.state
                guard response.state.connected, response.status.lowercased() != "not_connected" else {
                    transitionToReauthentication()
                    return
                }
                await refreshPendingLocalActionCount()
            }
            await refresh(allowEmptyDashboard: true, forceMailbox: true)
            await refreshFolderCounts()
        } catch {
            if transitionToReauthenticationIfNeeded(for: error) {
                return
            }
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

    public func performReaderThreadAction(
        _ action: GmailThreadAction,
        threadID: String,
        messageID: String? = nil
    ) async {
        await performTargetedThreadAction(
            action,
            threadID: threadID,
            messageID: messageID ?? readerFocusedMessageID
        )
    }

    public func performTargetedThreadAction(
        _ action: GmailThreadAction,
        threadID: String,
        messageID: String?
    ) async {
        let targetMessageID: String?
        switch action {
        case .star, .unstar, .markRead, .markUnread, .moveTrash, .restoreTrash, .markSpam, .notSpam, .deleteForever:
            targetMessageID = messageID
        case .archive, .unarchive:
            targetMessageID = nil
        }
        _ = await performThreadAction(action, threadID: threadID, targetMessageID: targetMessageID)
    }

    public func openAttachment(_ attachment: ThreadAttachment, messageID: String) async {
        await openAttachment(attachment, messageID: messageID, fileHandler: .live)
    }

    func openAttachment(
        _ attachment: ThreadAttachment,
        messageID: String,
        fileHandler: AttachmentFileHandler
    ) async {
        attachmentErrorMessage = nil

        let downloaded: DownloadedAttachment
        do {
            downloaded = try await client.downloadAttachment(messageID: messageID, attachment: attachment)
        } catch {
            attachmentErrorMessage = "The attachment could not be downloaded. \(error.localizedDescription)"
            return
        }

        guard !Task.isCancelled else {
            return
        }

        do {
            _ = try await fileHandler.saveAndOpen(
                downloaded,
                suggestedFilename: sanitizedAttachmentFilename(downloaded.filename)
            )
        } catch is CancellationError {
            return
        } catch {
            attachmentErrorMessage = error.localizedDescription
        }
    }

    public func dismissAttachmentError() {
        attachmentErrorMessage = nil
    }

    @discardableResult
    private func performThreadAction(_ action: GmailThreadAction, threadID: String, targetMessageID: String? = nil) async -> Bool {
        let operationGeneration = accountOperationGeneration
        let readerLabelMutation = beginReaderLabelMutation(
            action: action,
            threadID: threadID,
            targetMessageID: targetMessageID
        )
        do {
            let request = QueuedThreadActionRequest(
                clientActionID: UUID().uuidString,
                mailboxThreadID: threadID,
                targetMessageID: targetMessageID,
                action: action,
                createdAt: ISO8601DateFormatter.backendActionTimestamp.string(from: Date())
            )
            _ = try await client.enqueueThreadAction(request)
            guard operationGeneration == accountOperationGeneration else {
                return true
            }
            finishReaderLabelMutation(readerLabelMutation, succeeded: true)
            if readerLabelMutation == nil {
                applyConfirmedReaderLabelChange(
                    action: action,
                    threadID: threadID,
                    targetMessageID: targetMessageID
                )
            }
            await persistReaderThreadIfSettled(threadID: threadID)
            await refreshPendingLocalActionCount()
            guard operationGeneration == accountOperationGeneration else {
                return true
            }
            applyLocalThreadAction(threadID: threadID, action: action, targetMessageID: targetMessageID)
            if let userID = session?.user.id, let activeMailbox {
                // Keep the optimistic projection on disk before asking the
                // offline-first client to refresh. If the network is down, that
                // refresh falls back to this cache; writing first prevents the
                // stale pre-action row from immediately reappearing.
                await localMailStoreWorker.writeMailbox(
                    activeMailbox,
                    userID: userID,
                    label: activeMailboxLabel
                )
            }
            discardLoadedMailboxPagesOnNextRefresh = true
            lastMailboxRefreshAt[activeMailboxLabel] = nil
            if isSearchActive {
                await searchMailbox(searchQuery)
            } else {
                await refreshActiveMailbox(allowCachedFallback: true)
            }
            return true
        } catch {
            guard operationGeneration == accountOperationGeneration else {
                return true
            }
            finishReaderLabelMutation(readerLabelMutation, succeeded: false)
            await persistReaderThreadIfSettled(threadID: threadID)
            refreshFailed = true
            return false
        }
    }

    private func beginReaderLabelMutation(
        action: GmailThreadAction,
        threadID: String,
        targetMessageID: String?
    ) -> ReaderLabelMutation? {
        guard let labelChange = readerLabelChange(for: action) else {
            return nil
        }
        recordReaderLabelMutationGeneration(threadID: threadID, labelID: labelChange.labelID)
        supersedeReaderLabelIntentOverrides(
            threadID: threadID,
            labelID: labelChange.labelID,
            targetMessageID: targetMessageID
        )

        let currentThread = readerThreadID == threadID
            ? (readerThread ?? openedThreads[threadID])
            : openedThreads[threadID]
        let targetMessages = currentThread?.messages.filter { message in
            targetMessageID == nil || message.id == targetMessageID
        } ?? []

        let mutationID = UUID()
        guard let currentThread, !targetMessages.isEmpty else {
            let intentKey = ReaderLabelIntentKey(
                threadID: threadID,
                labelID: labelChange.labelID,
                targetMessageID: targetMessageID
            )
            readerLabelIntentOverrides[intentKey] = ReaderLabelIntentOverride(
                ownerID: mutationID,
                isPresent: labelChange.isPresent,
                settled: false
            )
            return ReaderLabelMutation(
                id: mutationID,
                threadID: threadID,
                labelID: labelChange.labelID,
                isPresent: labelChange.isPresent,
                targetMessageID: targetMessageID,
                beganWithoutLoadedThread: true,
                previousPresenceByMessageID: [:]
            )
        }

        var previousPresence: [String: Bool] = [:]
        for message in targetMessages {
            previousPresence[message.id] = message.hasLabel(labelChange.labelID)
        }
        for messageID in previousPresence.keys {
            readerLabelMutationOwners[
                ReaderLabelMutationKey(threadID: threadID, messageID: messageID, labelID: labelChange.labelID)
            ] = mutationID
        }

        let nextThread = currentThread.settingMessageLabel(
            labelChange.labelID,
            presenceByMessageID: Dictionary(
                uniqueKeysWithValues: previousPresence.keys.map { ($0, labelChange.isPresent) }
            )
        )
        updateCachedReaderThread(nextThread, threadID: threadID)
        return ReaderLabelMutation(
            id: mutationID,
            threadID: threadID,
            labelID: labelChange.labelID,
            isPresent: labelChange.isPresent,
            targetMessageID: targetMessageID,
            beganWithoutLoadedThread: false,
            previousPresenceByMessageID: previousPresence
        )
    }

    private func readerLabelChange(for action: GmailThreadAction) -> (labelID: String, isPresent: Bool)? {
        switch action {
        case .markRead:
            return ("UNREAD", false)
        case .markUnread:
            return ("UNREAD", true)
        case .star:
            return ("STARRED", true)
        case .unstar:
            return ("STARRED", false)
        case .archive, .unarchive, .moveTrash, .restoreTrash, .markSpam, .notSpam, .deleteForever:
            return nil
        }
    }

    private func supersedeReaderLabelIntentOverrides(
        threadID: String,
        labelID: String,
        targetMessageID: String?
    ) {
        let overlappingKeys = readerLabelIntentOverrides.keys.filter { key in
            key.threadID == threadID
                && key.labelID == labelID
                && (key.targetMessageID == nil
                    || targetMessageID == nil
                    || key.targetMessageID == targetMessageID)
        }
        for key in overlappingKeys {
            readerLabelIntentOverrides[key] = nil
        }
    }

    private func applyConfirmedReaderLabelChange(
        action: GmailThreadAction,
        threadID: String,
        targetMessageID: String?
    ) {
        guard let labelChange = readerLabelChange(for: action) else {
            return
        }
        let currentThread = readerThreadID == threadID
            ? (readerThread ?? openedThreads[threadID])
            : openedThreads[threadID]
        guard let currentThread else {
            return
        }
        var presenceByMessageID: [String: Bool] = [:]
        for message in currentThread.messages where targetMessageID == nil || message.id == targetMessageID {
            let key = ReaderLabelMutationKey(threadID: threadID, messageID: message.id, labelID: labelChange.labelID)
            guard readerLabelMutationOwners[key] == nil else {
                continue
            }
            presenceByMessageID[message.id] = labelChange.isPresent
        }
        guard !presenceByMessageID.isEmpty else {
            return
        }
        updateCachedReaderThread(
            currentThread.settingMessageLabel(labelChange.labelID, presenceByMessageID: presenceByMessageID),
            threadID: threadID
        )
    }

    private func finishReaderLabelMutation(_ mutation: ReaderLabelMutation?, succeeded: Bool) {
        guard let mutation else {
            return
        }

        if mutation.beganWithoutLoadedThread {
            let intentKey = ReaderLabelIntentKey(
                threadID: mutation.threadID,
                labelID: mutation.labelID,
                targetMessageID: mutation.targetMessageID
            )
            guard var intent = readerLabelIntentOverrides[intentKey],
                  intent.ownerID == mutation.id else {
                return
            }

            if succeeded {
                intent.settled = true
                readerLabelIntentOverrides[intentKey] = intent
                let currentThread = readerThreadID == mutation.threadID
                    ? (readerThread ?? openedThreads[mutation.threadID])
                    : openedThreads[mutation.threadID]
                if let currentThread {
                    let confirmedPresence: [String: Bool] = Dictionary(
                        uniqueKeysWithValues: currentThread.messages.compactMap { message -> (String, Bool)? in
                            guard mutation.targetMessageID == nil || message.id == mutation.targetMessageID else {
                                return nil
                            }
                            return (message.id, mutation.isPresent)
                        }
                    )
                    if !confirmedPresence.isEmpty {
                        updateCachedReaderThread(
                            currentThread.settingMessageLabel(
                                mutation.labelID,
                                presenceByMessageID: confirmedPresence
                            ),
                            threadID: mutation.threadID
                        )
                    }
                }
            } else {
                readerLabelIntentOverrides[intentKey] = nil
            }
            recordReaderLabelMutationGeneration(
                threadID: mutation.threadID,
                labelID: mutation.labelID
            )
            return
        }

        var settledPresence: [String: Bool] = [:]
        var didSettleOwnedMutation = false
        let currentThread = readerThreadID == mutation.threadID
            ? (readerThread ?? openedThreads[mutation.threadID])
            : openedThreads[mutation.threadID]
        for (messageID, previousPresence) in mutation.previousPresenceByMessageID {
            let key = ReaderLabelMutationKey(
                threadID: mutation.threadID,
                messageID: messageID,
                labelID: mutation.labelID
            )
            guard readerLabelMutationOwners[key] == mutation.id else {
                continue
            }
            didSettleOwnedMutation = true
            readerLabelMutationOwners[key] = nil
            if succeeded {
                settledPresence[messageID] = mutation.isPresent
                continue
            }
            guard currentThread?.messages.first(where: { $0.id == messageID })?.hasLabel(mutation.labelID) == mutation.isPresent else {
                continue
            }
            settledPresence[messageID] = previousPresence
        }

        // A reader request can start after the optimistic update but while the
        // action request is suspended. Advancing the generation again on both
        // confirmation and rollback makes that in-flight reader response merge
        // the settled label state instead of restoring its stale snapshot.
        if didSettleOwnedMutation {
            recordReaderLabelMutationGeneration(
                threadID: mutation.threadID,
                labelID: mutation.labelID
            )
        }

        guard !settledPresence.isEmpty, let currentThread else {
            return
        }
        updateCachedReaderThread(
            currentThread.settingMessageLabel(mutation.labelID, presenceByMessageID: settledPresence),
            threadID: mutation.threadID
        )
    }

    private func recordReaderLabelMutationGeneration(threadID: String, labelID: String) {
        let generation = (readerLabelMutationGenerations[threadID] ?? 0) &+ 1
        readerLabelMutationGenerations[threadID] = generation
        readerLabelMutationLabelGenerations[threadID, default: [:]][labelID] = generation
    }

    private func updateCachedReaderThread(_ thread: ThreadReaderResponse, threadID: String) {
        openedThreads[threadID] = thread
        if readerThreadID == threadID {
            readerThread = thread
        }
        if let userID = session?.user.id, thread.userID == userID {
            threadCache.write(thread, userID: userID, threadID: threadID)
        }
    }

    private func persistReaderThreadIfSettled(threadID: String) async {
        guard !readerLabelMutationOwners.keys.contains(where: { $0.threadID == threadID }),
              let thread = openedThreads[threadID],
              let userID = session?.user.id,
              thread.userID == userID else {
            return
        }
        await localMailStoreWorker.writeThread(thread, userID: userID, threadID: threadID)
    }

    private func preservingReaderLabelMutations(
        in fetchedThread: ThreadReaderResponse,
        threadID: String,
        since mutationGenerationAtStart: UInt
    ) -> ThreadReaderResponse {
        var reconciledThread = fetchedThread
        if (readerLabelMutationGenerations[threadID] ?? 0) != mutationGenerationAtStart,
           let currentThread = readerThreadID == threadID
            ? (readerThread ?? openedThreads[threadID])
            : openedThreads[threadID] {
            let changedLabelIDs = readerLabelMutationLabelGenerations[threadID, default: [:]]
                .filter { $0.value > mutationGenerationAtStart }
                .map(\.key)
            for labelID in changedLabelIDs {
                let presenceByMessageID = Dictionary(
                    uniqueKeysWithValues: currentThread.messages.map { ($0.id, $0.hasLabel(labelID)) }
                )
                reconciledThread = reconciledThread.settingMessageLabel(
                    labelID,
                    presenceByMessageID: presenceByMessageID
                )
            }
        }

        let intentOverrides = readerLabelIntentOverrides.filter { $0.key.threadID == threadID }
        for (intentKey, intent) in intentOverrides {
            guard intent.settled else {
                continue
            }
            let targetMessages = reconciledThread.messages.filter { message in
                intentKey.targetMessageID == nil || message.id == intentKey.targetMessageID
            }
            guard !targetMessages.isEmpty else {
                continue
            }
            if targetMessages.allSatisfy({ $0.hasLabel(intentKey.labelID) == intent.isPresent }) {
                if intent.settled,
                   readerLabelIntentOverrides[intentKey]?.ownerID == intent.ownerID {
                    readerLabelIntentOverrides[intentKey] = nil
                }
                continue
            }
            reconciledThread = reconciledThread.settingMessageLabel(
                intentKey.labelID,
                presenceByMessageID: Dictionary(
                    uniqueKeysWithValues: targetMessages.map { ($0.id, intent.isPresent) }
                )
            )
        }
        return reconciledThread
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
        let operationGeneration = accountOperationGeneration
        let labelMutationGenerationAtReadStart = readerLabelMutationGenerations[threadID] ?? 0
        let memoryCachedThread = force
            ? nil
            : threadCache.read(userID: userID, threadID: threadID).flatMap { $0.userID == userID ? $0 : nil }
        let diskCachedThread = memoryCachedThread == nil && !force
            ? await localMailStoreWorker.readThread(userID: userID, threadID: threadID)
            : nil
        guard operationGeneration == accountOperationGeneration,
              session?.user.id == userID else {
            return
        }
        let cachedThread = (memoryCachedThread ?? diskCachedThread).flatMap { $0.userID == userID ? $0 : nil }
        if let fetchedCachedThread = cachedThread {
            let cached = preservingReaderLabelMutations(
                in: fetchedCachedThread,
                threadID: threadID,
                since: labelMutationGenerationAtReadStart
            )
            if memoryCachedThread == nil {
                threadCache.write(cached, userID: userID, threadID: threadID)
            }
            openedThreads[threadID] = cached
            updateReader(threadID: threadID, thread: cached, error: nil)
            scheduleBodyRefreshIfNeeded(threadID: threadID, thread: cached)
            if silent {
                return
            }
        }
        if let inFlight = inFlightThreads[threadID] {
            let inFlightLabelMutationGeneration =
                inFlightThreadLabelMutationGenerations[threadID] ?? labelMutationGenerationAtReadStart
            do {
                let fetchedThread = try await inFlight.value
                guard operationGeneration == accountOperationGeneration,
                      session?.user.id == userID,
                      fetchedThread.userID == userID else {
                    return
                }
                let thread = preservingReaderLabelMutations(
                    in: fetchedThread,
                    threadID: threadID,
                    since: inFlightLabelMutationGeneration
                )
                openedThreads[threadID] = thread
                updateReader(threadID: threadID, thread: thread, error: nil)
            } catch {
                guard operationGeneration == accountOperationGeneration else {
                    return
                }
                if !silent {
                    recordThreadError(error.localizedDescription, threadID: threadID)
                }
                if let cached = openedThreads[threadID] {
                    scheduleBodyRefreshIfNeeded(threadID: threadID, thread: cached)
                }
            }
            return
        }

        let inFlightLabelMutationGeneration = readerLabelMutationGenerations[threadID] ?? 0
        let task = Task { [client, threadFetchLimit, userID] in
            let firstPage = try await client.thread(threadID: threadID, limit: threadFetchLimit, offset: 0)
            guard firstPage.userID == userID else {
                throw APIError.emptyResponse
            }
            var messages = firstPage.messages
            var seenMessageIDs = Set(messages.map(\.id))
            messages.reserveCapacity(
                min(max(firstPage.totalMessages, messages.count), threadFetchLimit * 201)
            )
            var source = firstPage.source
            var gmailThreadID = firstPage.gmailThreadID
            var subject = firstPage.subject
            var title = firstPage.title
            var totalMessages = max(firstPage.totalMessages, messages.count)
            var limit = firstPage.limit
            var hasMore = firstPage.hasMore
            var nextOffset = firstPage.offset + firstPage.messages.count
            var pageCount = 0
            while hasMore, pageCount < 200 {
                try Task.checkCancellation()
                let requestedOffset = nextOffset
                guard requestedOffset > firstPage.offset || pageCount > 0 else {
                    break
                }
                let page = try await client.thread(
                    threadID: threadID,
                    limit: threadFetchLimit,
                    offset: requestedOffset
                )
                guard page.userID == userID else {
                    throw APIError.emptyResponse
                }
                for message in page.messages where seenMessageIDs.insert(message.id).inserted {
                    messages.append(message)
                }
                source = source ?? page.source
                gmailThreadID = gmailThreadID ?? page.gmailThreadID
                subject = subject ?? page.subject
                title = title ?? page.title
                totalMessages = max(totalMessages, page.totalMessages, messages.count)
                limit = max(limit, page.limit)
                hasMore = page.hasMore
                pageCount += 1

                let followingOffset = requestedOffset + page.messages.count
                guard !hasMore || followingOffset > requestedOffset else {
                    break
                }
                nextOffset = followingOffset
            }
            guard pageCount > 0 else {
                return firstPage
            }
            return ThreadReaderResponse(
                entityID: firstPage.entityID,
                userID: firstPage.userID,
                source: source,
                gmailThreadID: gmailThreadID,
                subject: subject,
                title: title,
                summary: nil,
                totalMessages: totalMessages,
                limit: limit,
                offset: 0,
                hasMore: hasMore,
                messages: messages
            )
        }
        inFlightThreads[threadID] = task
        inFlightThreadLabelMutationGenerations[threadID] = inFlightLabelMutationGeneration
        do {
            let fetchedThread = try await task.value
            guard operationGeneration == accountOperationGeneration,
                  session?.user.id == userID else {
                return
            }
            inFlightThreads[threadID] = nil
            inFlightThreadLabelMutationGenerations[threadID] = nil
            let thread = preservingReaderLabelMutations(
                in: fetchedThread,
                threadID: threadID,
                since: inFlightLabelMutationGeneration
            )
            openedThreads[threadID] = thread
            threadCache.write(thread, userID: userID, threadID: threadID)
            threadErrors[threadID] = nil
            updateReader(threadID: threadID, thread: thread, error: nil)
            await localMailStoreWorker.writeThread(thread, userID: userID, threadID: threadID)
            if startPendingHydrationRefreshAfterCurrentRequest(
                threadID: threadID,
                threadNeedsRefresh: thread.needsReaderBodyRefresh
            ) {
                return
            }
            scheduleBodyRefreshIfNeeded(threadID: threadID, thread: thread)
        } catch {
            guard operationGeneration == accountOperationGeneration else {
                return
            }
            inFlightThreads[threadID] = nil
            inFlightThreadLabelMutationGenerations[threadID] = nil
            if cachedThread == nil, !silent {
                recordThreadError(error.localizedDescription, threadID: threadID)
            }
            if startPendingHydrationRefreshAfterCurrentRequest(
                threadID: threadID,
                threadNeedsRefresh: openedThreads[threadID]?.needsReaderBodyRefresh ?? true
            ) {
                return
            }
            if let cached = openedThreads[threadID] {
                scheduleBodyRefreshIfNeeded(threadID: threadID, thread: cached)
            }
        }
    }

    public func triggerMailboxSyncIfNeeded() async {
        guard client.mode == .localBackend else {
            return
        }
        let now = Date()
        let shouldPollSyncState = MailboxBackgroundRefreshPolicy.shouldPollSyncState(
            supportsRealtimeUpdates: client.supportsRealtimeMailboxUpdates,
            realtimeConnected: realtimeConnected,
            lastRealtimeEventAt: lastSSEEventAt,
            now: now
        )
        guard shouldPollSyncState else {
            return
        }
        if client.supportsRealtimeMailboxUpdates, realtimeConnected {
            realtimeConnected = false
            lastSSEDisconnectedAt = now
        }
        if let lastSyncTriggerAt, now.timeIntervalSince(lastSyncTriggerAt) < minimumSyncGap {
            return
        }
        lastSyncTriggerAt = now
        do {
            let state = try await client.mailboxSyncState()
            syncState = state
            guard state.connected else {
                transitionToReauthentication()
                return
            }
            guard let revision = state.mailboxRevision, !revision.isEmpty else {
                return
            }
            guard lastSeenMailboxRevision != revision else {
                return
            }
            lastSeenMailboxRevision = revision
            await refreshActiveMailbox(allowCachedFallback: true, force: true)
        } catch {
            if transitionToReauthenticationIfNeeded(for: error) {
                return
            }
            lastForcedMailboxRefreshError = error.localizedDescription
        }
    }

    public func startLiveRefreshLoop() {
        guard client.supportsRealtimeMailboxUpdates else {
            return
        }
        startMailboxEventStream()
        guard syncLoopTask == nil else {
            return
        }
        syncLoopTask = Task { [weak self] in
            while !Task.isCancelled {
                let interval = self?.activeSyncInterval ?? 8
                try? await Task.sleep(nanoseconds: UInt64(interval * 1_000_000_000))
                guard !Task.isCancelled else {
                    return
                }
                await self?.triggerMailboxSyncIfNeeded()
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
                    guard !Task.isCancelled else { return }
                    try await Task.sleep(nanoseconds: UInt64(reconnectDelay * 1_000_000_000))
                    reconnectDelay = min(reconnectDelay * 2, 30)
                } catch is CancellationError {
                    return
                } catch {
                    if self?.transitionToReauthenticationIfNeeded(for: error) == true {
                        return
                    }
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
        realtimeConnected = true
        lastSSEConnectedAt = Date()
        var parser = ServerSentEventParser()
        do {
            for try await line in bytes.lines {
                if Task.isCancelled {
                    realtimeConnected = false
                    lastSSEDisconnectedAt = Date()
                    return
                }
                if let event = parser.feed(line: line) {
                    handleMailboxServerEvent(event)
                }
            }
            realtimeConnected = false
            lastSSEDisconnectedAt = Date()
        } catch {
            realtimeConnected = false
            lastSSEDisconnectedAt = Date()
            throw error
        }
    }

    func handleMailboxServerEvent(_ event: MailboxServerEvent) {
        let eventDate = Date()
        if !realtimeConnected {
            lastSSEConnectedAt = eventDate
        }
        realtimeConnected = true
        if let id = event.id, !id.isEmpty {
            lastMailboxEventID = id
            lastSSEEventID = id
        }
        lastSSEEventType = event.event
        lastSSEEventAt = eventDate
        switch event.event {
        case "mailbox-search-hydrated":
            let envelope = decodedEventEnvelope(event.data)
            guard let searchKey = envelope.searchKey else {
                return
            }
            scheduleHydratedSearchRefresh(matchingSearchKey: searchKey)
        case "thread-content-hydrated":
            let envelope = decodedEventEnvelope(event.data)
            guard let readerThreadID,
                  envelope.threadIDs.contains(readerThreadID) else {
                return
            }
            cancelBodyRefresh(for: readerThreadID)
            pendingHydrationRefreshThreadIDs.insert(readerThreadID)
            startPendingHydrationRefreshIfPossible(threadID: readerThreadID)
        case "mailbox-changed":
            let envelope = decodedEventEnvelope(event.data)
            guard eventAffectsActiveMailbox(envelope) else {
                return
            }
            if shouldRefreshForRevision(envelope.mailboxRevision) {
                scheduleEventRefresh(needsSession: false)
            }
        case "dashboard-changed":
            let envelope = decodedEventEnvelope(event.data)
            _ = shouldRefreshForRevision(envelope.mailboxRevision)
            scheduleEventRefresh(needsSession: true)
        case "sync-state":
            guard let state = decodedSyncState(event.data) else {
                return
            }
            syncState = state
            guard state.connected else {
                transitionToReauthentication()
                return
            }
            if shouldRefreshForRevision(state.mailboxRevision) {
                scheduleEventRefresh(needsSession: false)
            }
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

    private func scheduleHydratedSearchRefresh(matchingSearchKey eventSearchKey: String) {
        let query = searchQuery.trimmingCharacters(in: .whitespacesAndNewlines)
        let label = activeMailboxLabel
        guard !query.isEmpty,
              eventSearchKey == Self.searchKey(label: label, query: query) else {
            return
        }

        searchHydrationRefreshTask?.cancel()
        resetMailboxPagination()
        let requestID = UUID()
        searchRequestID = requestID
        searchHydrationRefreshTask = Task { [weak self] in
            guard let self else {
                return
            }
            do {
                let response = try await self.client.searchMailbox(
                    query: query,
                    label: label,
                    limit: self.mailboxPageLimit,
                    cursor: nil,
                    hydrateInBackground: false
                )
                try Task.checkCancellation()
                guard self.searchRequestID == requestID,
                      self.activeMailboxLabel == label,
                      self.searchQuery == query,
                      Self.searchKey(label: self.activeMailboxLabel, query: self.searchQuery) == eventSearchKey else {
                    return
                }
                self.searchResults = response
                self.searchError = nil
                self.seedActiveSelectionIfNeeded()
            } catch is CancellationError {
                // A changed or cleared search owns the visible results now.
            } catch {
                guard self.searchRequestID == requestID,
                      self.activeMailboxLabel == label,
                      self.searchQuery == query else {
                    return
                }
                if self.searchResults == nil {
                    self.searchError = error.localizedDescription
                }
            }

            guard self.searchRequestID == requestID else {
                return
            }
            self.searchRequestID = nil
            self.searchInProgress = false
            self.searchHydrationRefreshTask = nil
        }
    }

    private func performEventRefresh() async {
        let needsSession = pendingEventRefreshNeedsSession
        pendingEventRefreshNeedsSession = false
        eventRefreshTask = nil
        lastMailboxRefreshAt[activeMailboxLabel] = nil
        if needsSession {
            await refresh(allowEmptyDashboard: true, forceMailbox: true)
        } else {
            await refreshActiveMailbox(allowCachedFallback: true, force: true)
        }
    }

    private func sanitizedAttachmentFilename(_ filename: String) -> String {
        let invalidCharacters = CharacterSet(charactersIn: "/\\:\0\r\n\t")
        let cleaned = filename
            .components(separatedBy: invalidCharacters)
            .joined(separator: "_")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return cleaned.isEmpty ? "attachment" : cleaned
    }

    private func shouldRefreshForRevision(_ revision: String?) -> Bool {
        guard let revision, !revision.isEmpty else {
            return true
        }
        if lastSeenMailboxRevision == revision {
            return false
        }
        lastSeenMailboxRevision = revision
        return true
    }

    private func eventAffectsActiveMailbox(_ envelope: MailboxEventEnvelope) -> Bool {
        let labels = envelope.mailboxLabels
        if labels.isEmpty {
            return true
        }
        let active = activeMailboxLabel.rawValue
        return labels.contains("all") || labels.contains(active)
    }

    private func decodedEventEnvelope(_ data: String) -> MailboxEventEnvelope {
        guard let raw = data.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: raw) as? [String: Any] else {
            return MailboxEventEnvelope()
        }
        let mailboxLabel = json["mailbox_label"] as? String
        let payload = json["payload"] as? [String: Any] ?? [:]
        let revision = payload["mailbox_revision"] as? String
        let labels = payload["mailbox_labels"] as? [String]
        let searchKey = payload["search_key"] as? String
        let threadIDs = payload["thread_ids"] as? [String]
        return MailboxEventEnvelope(
            mailboxRevision: revision,
            mailboxLabels: labels ?? (mailboxLabel.map { [$0] } ?? []),
            searchKey: searchKey,
            threadIDs: threadIDs ?? []
        )
    }

    private static func searchKey(label: MailboxLabel, query: String) -> String {
        let normalizedQuery = query.trimmingCharacters(in: .whitespacesAndNewlines)
        let digest = SHA256.hash(data: Data("\(label.rawValue)\0\(normalizedQuery)".utf8))
        return digest.map { String(format: "%02x", $0) }.joined()
    }

    private func decodedSyncState(_ data: String) -> MailboxSyncStateResponse? {
        guard let raw = data.data(using: .utf8) else {
            return nil
        }
        return try? JSONDecoder.backend.decode(MailboxSyncStateResponse.self, from: raw)
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
        _ = sections
        return inboxSectionsCache?.rowsByID[Self.rowID(threadID: threadID, focusedMessageID: focusedMessageID)]?.isUnread == true
    }

    private func readActionKey(threadID: String, focusedMessageID: String?) -> String {
        guard let focusedMessageID else {
            return threadID
        }
        return "\(threadID)::message::\(focusedMessageID)"
    }

    private func seedActiveSelectionIfNeeded() {
        let rows = flatRows
        if let activeThreadID,
           inboxSectionsCache?.rowsByID[Self.rowID(threadID: activeThreadID, focusedMessageID: activeMessageID)] != nil {
            if selectedThreadID == nil {
                selectedThreadID = activeThreadID
                selectedMessageID = activeMessageID
            }
            return
        }
        let firstRow = rows.first
        activeThreadID = firstRow?.threadID
        activeMessageID = firstRow?.focusedMessageID
        if selectedThreadID == nil
            || inboxSectionsCache?.rowsByID[
                Self.rowID(threadID: selectedThreadID, focusedMessageID: selectedMessageID)
            ] == nil {
            selectedThreadID = firstRow?.threadID
            selectedMessageID = firstRow?.focusedMessageID
        }
    }

    private func rowViewModel(threadID: String) -> InboxRowViewModel? {
        _ = sections
        return inboxSectionsCache?.firstRowByThreadID[threadID]
    }

    private static func rowID(threadID: String?, focusedMessageID: String?) -> String {
        guard let threadID else {
            return ""
        }
        guard let focusedMessageID else {
            return threadID
        }
        return "\(threadID)::message::\(focusedMessageID)"
    }

    private func refreshReaderRow() {
        guard let readerThreadID else {
            return
        }
        let nextRow = rowViewModel(threadID: readerThreadID)
        if readerRow != nextRow {
            readerRow = nextRow
        }
    }

    private func updateReader(threadID: String, thread: ThreadReaderResponse, error: String?) {
        guard readerThreadID == threadID else {
            return
        }
        if readerThread != thread {
            readerThread = thread
        }
        let nextRow = rowViewModel(threadID: threadID)
        if readerRow != nextRow {
            readerRow = nextRow
        }
        if readerError != error {
            readerError = error
        }
    }

    private func recordThreadError(_ message: String, threadID: String) {
        threadErrors[threadID] = message
        guard readerThreadID == threadID else {
            return
        }
        if readerError != message {
            readerError = message
        }
        let nextRow = rowViewModel(threadID: threadID)
        if readerRow != nextRow {
            readerRow = nextRow
        }
    }

    private func startPendingHydrationRefreshAfterCurrentRequest(
        threadID: String,
        threadNeedsRefresh: Bool
    ) -> Bool {
        guard pendingHydrationRefreshThreadIDs.contains(threadID) else {
            return false
        }
        guard readerThreadID == threadID else {
            pendingHydrationRefreshThreadIDs.remove(threadID)
            return false
        }
        guard threadNeedsRefresh else {
            pendingHydrationRefreshThreadIDs.remove(threadID)
            return false
        }
        return startPendingHydrationRefreshIfPossible(threadID: threadID)
    }

    @discardableResult
    private func startPendingHydrationRefreshIfPossible(threadID: String) -> Bool {
        guard pendingHydrationRefreshThreadIDs.contains(threadID),
              readerThreadID == threadID,
              inFlightThreads[threadID] == nil,
              hydrationRefreshTaskOwners.ownerID(for: threadID) == nil else {
            return false
        }
        let ownerID = hydrationRefreshTaskOwners.claim(threadID: threadID)
        Task { [weak self] in
            await self?.performPendingHydrationRefresh(threadID: threadID, ownerID: ownerID)
        }
        return true
    }

    private func performPendingHydrationRefresh(threadID: String, ownerID: UUID) async {
        guard hydrationRefreshTaskOwners.ownerID(for: threadID) == ownerID else {
            return
        }
        guard readerThreadID == threadID else {
            pendingHydrationRefreshThreadIDs.remove(threadID)
            _ = hydrationRefreshTaskOwners.release(threadID: threadID, ownerID: ownerID)
            return
        }
        guard inFlightThreads[threadID] == nil else {
            _ = hydrationRefreshTaskOwners.release(threadID: threadID, ownerID: ownerID)
            return
        }

        pendingHydrationRefreshThreadIDs.remove(threadID)
        await prefetchThread(threadID: threadID, force: true, silent: true)

        guard hydrationRefreshTaskOwners.release(threadID: threadID, ownerID: ownerID) else {
            return
        }
        guard pendingHydrationRefreshThreadIDs.contains(threadID) else {
            return
        }
        guard readerThreadID == threadID else {
            pendingHydrationRefreshThreadIDs.remove(threadID)
            return
        }
        if openedThreads[threadID]?.needsReaderBodyRefresh == true {
            startPendingHydrationRefreshIfPossible(threadID: threadID)
        } else {
            pendingHydrationRefreshThreadIDs.remove(threadID)
        }
    }

    private func scheduleBodyRefreshIfNeeded(threadID: String, thread: ThreadReaderResponse) {
        guard thread.needsReaderBodyRefresh, readerThreadID == threadID else {
            cancelBodyRefresh(for: threadID)
            return
        }
        guard bodyRefreshTasks[threadID] == nil else {
            return
        }
        let attempts = bodyRefreshAttempts[threadID, default: 0]
        guard attempts < bodyRefreshDelaysNanoseconds.count else {
            return
        }
        bodyRefreshAttempts[threadID] = attempts + 1
        let delay = bodyRefreshDelaysNanoseconds[attempts]
        let ownerID = bodyRefreshTaskOwners.claim(threadID: threadID)
        bodyRefreshTasks[threadID] = Task { [weak self] in
            try? await Task.sleep(nanoseconds: delay)
            guard let self else {
                return
            }
            guard !Task.isCancelled, self.readerThreadID == threadID else {
                self.finishBodyRefreshTask(threadID: threadID, ownerID: ownerID)
                return
            }
            self.finishBodyRefreshTask(threadID: threadID, ownerID: ownerID)
            await self.prefetchThread(threadID: threadID, force: true, silent: true)
        }
    }

    private func finishBodyRefreshTask(threadID: String, ownerID: UUID) {
        guard bodyRefreshTaskOwners.release(threadID: threadID, ownerID: ownerID) else {
            return
        }
        bodyRefreshTasks[threadID] = nil
    }

    private func cancelBodyRefresh(for threadID: String) {
        bodyRefreshTaskOwners.cancel(threadID: threadID)
        bodyRefreshTasks.removeValue(forKey: threadID)?.cancel()
        bodyRefreshAttempts[threadID] = nil
    }

    private func prefetchPriorityThreads() {
        let rows = flatRows
        let grouped = rows.filter(\.isGrouped)
        let candidates = Array((rows.prefix(4) + grouped.prefix(2)).map(\.threadID).uniqued().prefix(4))
        priorityPrefetchTask?.cancel()
        priorityPrefetchTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 750_000_000)
            guard !Task.isCancelled else {
                return
            }
            for threadID in candidates {
                guard !Task.isCancelled else {
                    return
                }
                await self?.prefetchThread(threadID: threadID, force: false, silent: true)
                try? await Task.sleep(nanoseconds: 100_000_000)
            }
        }
    }

    private func applyLocalThreadAction(threadID: String, action: GmailThreadAction, targetMessageID: String? = nil) {
        if action == .markRead || action == .markUnread {
            applyLocalReadState(threadID: threadID, targetMessageID: targetMessageID, unread: action == .markUnread)
            return
        }
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
            unreadThreads: mailbox.unreadThreads.map { max(0, $0 - (mailboxRow(threadID: threadID)?.isUnread == true ? 1 : 0)) },
            nextCursor: mailbox.nextCursor,
            loadedThreads: mailbox.loadedThreads.map { max(0, $0 - 1) },
            windowDays: mailbox.windowDays,
            sections: nextSections,
            readyCount: mailbox.readyCount,
            pendingCount: mailbox.pendingCount,
            mailboxRevision: mailbox.mailboxRevision,
            generatedAt: mailbox.generatedAt,
            oldestImportedAt: mailbox.oldestImportedAt,
            fullImportRunning: mailbox.fullImportRunning,
            fullImportCompleted: mailbox.fullImportCompleted
        )
        if let activeMailbox {
            updateFolderCount(from: activeMailbox)
        }
        if selectedThreadID == threadID {
            selectedThreadID = nil
            selectedMessageID = nil
            activeThreadID = nil
            activeMessageID = nil
            seedActiveSelectionIfNeeded()
        }
    }

    private func applyLocalReadState(threadID: String, targetMessageID: String?, unread: Bool) {
        guard let mailbox = activeMailbox else {
            return
        }
        let wasUnread = mailboxRow(threadID: threadID)?.isUnread == true
        var changed = false
        let nextSections = mailbox.sections.map { section in
            GmailThreadSection(
                id: section.id,
                title: section.title,
                rows: section.rows.map { row in
                    guard row.threadID == threadID else {
                        return row
                    }
                    changed = true
                    return unread ? row.markedUnread(targetMessageID: targetMessageID) : row.markedRead(targetMessageID: targetMessageID)
                }
            )
        }
        guard changed else {
            return
        }
        activeMailbox = MailboxResponse(
            label: mailbox.label,
            totalThreads: mailbox.totalThreads,
            unreadThreads: mailbox.unreadThreads.map { count in
                if unread, !wasUnread { return count + 1 }
                if !unread, wasUnread { return max(0, count - 1) }
                return count
            },
            nextCursor: mailbox.nextCursor,
            loadedThreads: mailbox.loadedThreads,
            windowDays: mailbox.windowDays,
            sections: nextSections,
            readyCount: mailbox.readyCount,
            pendingCount: mailbox.pendingCount,
            mailboxRevision: mailbox.mailboxRevision,
            generatedAt: mailbox.generatedAt,
            oldestImportedAt: mailbox.oldestImportedAt,
            fullImportRunning: mailbox.fullImportRunning,
            fullImportCompleted: mailbox.fullImportCompleted
        )
        if let activeMailbox {
            updateFolderCount(from: activeMailbox)
        }
        refreshReaderRow()
    }

    private func shouldRemoveFromCurrentMailbox(action: GmailThreadAction) -> Bool {
        switch action {
        case .archive:
            return activeMailboxLabel == .inbox
        case .unarchive:
            return activeMailboxLabel == .archive
        case .moveTrash:
            return activeMailboxLabel != .trash
        case .restoreTrash:
            return activeMailboxLabel == .trash
        case .markSpam:
            return activeMailboxLabel != .spam
        case .notSpam:
            return activeMailboxLabel == .spam
        case .star:
            return false
        case .unstar:
            return activeMailboxLabel == .starred
        case .deleteForever:
            return true
        case .markRead, .markUnread:
            return false
        }
    }

    public func setFolderCountPrefetchEnabled(_ enabled: Bool) async {
        guard folderCountPrefetchEnabled != enabled else {
            if enabled {
                await refreshFolderCountsIfNeeded()
            }
            return
        }
        folderCountPrefetchEnabled = enabled
        guard enabled else {
            cancelFolderCountRefresh()
            return
        }
        await refreshFolderCountsIfNeeded()
    }

    public func refreshFolderCounts() async {
        await performFolderCountRefresh(labels: Self.folderCountLabels)
    }

    private func refreshFolderCountsIfNeeded(now: Date = Date()) async {
        guard folderCountPrefetchEnabled,
              client.supportsFolderCountPrefetch,
              client.mode != .localBackend || client.sessionToken?.isEmpty == false else {
            return
        }
        guard folderCountsRefreshOwnerID == nil else {
            return
        }

        let missingLabels = Self.folderCountLabels.filter { mailboxCounts[$0] == nil }
        if !missingLabels.isEmpty {
            await performFolderCountRefresh(labels: missingLabels)
            return
        }

        if let lastCompletedFolderCountsRefreshAt,
           now.timeIntervalSince(lastCompletedFolderCountsRefreshAt) < folderCountsFreshnessInterval {
            return
        }
        await performFolderCountRefresh(labels: Self.folderCountLabels)
    }

    private func performFolderCountRefresh(labels: [MailboxLabel]) async {
        guard folderCountPrefetchEnabled,
              client.supportsFolderCountPrefetch,
              client.mode != .localBackend || client.sessionToken?.isEmpty == false,
              !labels.isEmpty else {
            return
        }
        cancelFolderCountRefresh()
        let refreshRevision = folderCountsRefreshRevision
        let refreshOwnerID = UUID()
        folderCountsRefreshOwnerID = refreshOwnerID
        defer {
            clearFolderCountRefreshOwner(id: refreshOwnerID)
        }
        let countsAtStart = mailboxCounts
        var refreshedCounts: [MailboxLabel: MailboxFolderCount] = [:]
        var completedEveryRequest = true
        for label in labels {
            guard !Task.isCancelled,
                  refreshRevision == folderCountsRefreshRevision,
                  folderCountsRefreshOwnerID == refreshOwnerID else {
                return
            }
            if label == activeMailboxLabel,
               let activeMailbox,
               activeMailbox.label == label {
                refreshedCounts[label] = Self.folderCount(from: activeMailbox)
                continue
            }

            let requestID = UUID()
            let request = Task { [client] in
                try await client.mailboxFolderCount(label: label)
            }
            inFlightFolderCountRequestID = requestID
            inFlightFolderCountRequest = request
            do {
                let mailbox = try await request.value
                clearInFlightFolderCountRequest(id: requestID)
                guard !Task.isCancelled,
                      refreshRevision == folderCountsRefreshRevision,
                      folderCountsRefreshOwnerID == refreshOwnerID else {
                    return
                }
                refreshedCounts[label] = Self.folderCount(from: mailbox)
            } catch is CancellationError {
                clearInFlightFolderCountRequest(id: requestID)
                return
            } catch {
                clearInFlightFolderCountRequest(id: requestID)
                guard refreshRevision == folderCountsRefreshRevision,
                      folderCountsRefreshOwnerID == refreshOwnerID else {
                    return
                }
                completedEveryRequest = false
                continue
            }
        }
        guard !Task.isCancelled,
              refreshRevision == folderCountsRefreshRevision,
              folderCountsRefreshOwnerID == refreshOwnerID else {
            return
        }
        var mergedCounts = mailboxCounts
        for (label, count) in refreshedCounts where mailboxCounts[label] == countsAtStart[label] {
            mergedCounts[label] = count
        }
        if mergedCounts != mailboxCounts {
            mailboxCounts = mergedCounts
        }
        if completedEveryRequest,
           Self.folderCountLabels.allSatisfy({ mailboxCounts[$0] != nil }) {
            lastCompletedFolderCountsRefreshAt = Date()
        }
    }

    private func cancelFolderCountRefresh() {
        folderCountsRefreshRevision &+= 1
        folderCountsRefreshOwnerID = nil
        inFlightFolderCountRequest?.cancel()
        inFlightFolderCountRequest = nil
        inFlightFolderCountRequestID = nil
    }

    private func clearInFlightFolderCountRequest(id: UUID) {
        guard inFlightFolderCountRequestID == id else {
            return
        }
        inFlightFolderCountRequest = nil
        inFlightFolderCountRequestID = nil
    }

    private func clearFolderCountRefreshOwner(id: UUID) {
        guard folderCountsRefreshOwnerID == id else {
            return
        }
        folderCountsRefreshOwnerID = nil
    }

    private func updateFolderCount(from mailbox: MailboxResponse) {
        let nextCount = Self.folderCount(from: mailbox)
        guard mailboxCounts[mailbox.label] != nextCount else {
            return
        }
        mailboxCounts[mailbox.label] = nextCount
    }

    private static func folderCount(from mailbox: MailboxResponse) -> MailboxFolderCount {
        let loadedUnread = mailbox.sections
            .flatMap(\.rows)
            .filter(\.isUnread)
            .count
        return MailboxFolderCount(
            total: mailbox.totalThreads,
            unread: mailbox.unreadThreads ?? loadedUnread
        )
    }

    private func invalidateAccountScopedOperations() {
        accountOperationGeneration &+= 1
        readerLabelMutationOwners = [:]
        readerLabelMutationGenerations = [:]
        readerLabelMutationLabelGenerations = [:]
        readerLabelIntentOverrides = [:]
        inFlightSessionRefresh?.cancel()
        inFlightSessionRefresh = nil
        cancelActiveMailboxRefresh()
        for task in inFlightThreads.values {
            task.cancel()
        }
        inFlightThreads = [:]
        inFlightThreadLabelMutationGenerations = [:]
        selectionPrefetchTask?.cancel()
        selectionPrefetchTask = nil
        priorityPrefetchTask?.cancel()
        priorityPrefetchTask = nil
        eventRefreshTask?.cancel()
        eventRefreshTask = nil
        postSendRefreshTask?.cancel()
        postSendRefreshTask = nil
        for task in bodyRefreshTasks.values {
            task.cancel()
        }
        bodyRefreshTasks = [:]
        bodyRefreshTaskOwners.cancelAll()
        bodyRefreshAttempts = [:]
        pendingHydrationRefreshThreadIDs = []
        hydrationRefreshTaskOwners.cancelAll()
        searchHydrationRefreshTask?.cancel()
        searchHydrationRefreshTask = nil
        searchRequestID = nil
        cancelFolderCountRefresh()
        lastCompletedFolderCountsRefreshAt = nil
        resetMailboxPagination()
    }

    private func resetMailboxPagination() {
        mailboxPageGeneration &+= 1
        inFlightMailboxPage?.cancel()
        inFlightMailboxPage = nil
        mailboxPageRequestID = nil
        requestedMailboxPageCursors = []
        mailboxPageLoading = false
    }

    private func matchesMailboxPageContext(_ context: MailboxPageContext) -> Bool {
        context.generation == mailboxPageGeneration
            && context.label == activeMailboxLabel
            && context.searchQuery == (isSearchActive ? searchQuery : nil)
            && context.userID == session?.user.id
    }

    private static func mailboxRevisionChanged(from current: MailboxResponse?, to next: MailboxResponse) -> Bool {
        guard let currentRevision = current?.mailboxRevision,
              !currentRevision.isEmpty,
              let nextRevision = next.mailboxRevision,
              !nextRevision.isEmpty else {
            return false
        }
        return currentRevision != nextRevision
    }

    private func mailboxRow(threadID: String) -> GmailThreadRow? {
        activeMailbox?.sections.lazy.flatMap(\.rows).first { $0.threadID == threadID }
    }

    private var visibleMailbox: MailboxResponse? {
        if isSearchActive {
            return searchResults
        }
        if let activeMailbox, activeMailbox.label == activeMailboxLabel {
            return activeMailbox
        }
        return nil
    }

    private func visibleRows(in section: GmailThreadSection) -> [GmailThreadRow] {
        if activeMailboxLabel == .all || activeMailboxLabel == .archive {
            return section.rows
        }
        return section.rows.filter { $0.isVisible(in: activeMailboxLabel) }
    }

    private func visibleChildren(for row: GmailThreadRow) -> [GmailThreadChildRow] {
        if activeMailboxLabel == .all || activeMailboxLabel == .archive {
            return row.childRows
        }
        return row.childRows.filter { $0.isVisible(in: activeMailboxLabel) }
    }

    private func invalidateInboxSections() {
        inboxSectionsRevision &+= 1
        inboxSectionsCache = nil
    }

    private func refreshPendingLocalActionCount() async {
        guard let userID = session?.user.id else {
            if pendingLocalActionCount != 0 {
                pendingLocalActionCount = 0
            }
            return
        }
        let nextCount = await localMailStoreWorker.pendingThreadActionCount(userID: userID)
        if pendingLocalActionCount != nextCount {
            pendingLocalActionCount = nextCount
        }
    }

    private func timeLabel(for value: String, sectionTitle: String) -> String {
        let usesTimeStyle = sectionTitle == "Today"
        let cacheKey = InboxTimestampLabelCacheKey(value: value, usesTimeStyle: usesTimeStyle)
        if let cached = timestampLabelCache[cacheKey] {
            return cached
        }

        let label: String
        if let date = ISO8601DateFormatter.shared.date(from: value) {
            label = usesTimeStyle
                ? DateFormatter.inboxTime.string(from: date)
                : DateFormatter.inboxDate.string(from: date)
        } else {
            label = value
        }

        if timestampLabelCache.count >= timestampLabelCacheLimit {
            timestampLabelCache.removeAll(keepingCapacity: true)
        }
        timestampLabelCache[cacheKey] = label
        return label
    }
}

private extension GmailThreadRow {
    func isVisible(in mailboxLabel: MailboxLabel) -> Bool {
        switch mailboxLabel {
        case .all:
            return true
        case .inbox:
            return containsMailboxLabel("INBOX")
        case .sent:
            return containsMailboxLabel("SENT")
        case .drafts:
            return containsMailboxLabel("DRAFT")
        case .spam:
            return containsMailboxLabel("SPAM")
        case .trash:
            return containsMailboxLabel("TRASH")
        case .archive:
            return true
        case .starred:
            return containsMailboxLabel("STARRED")
        }
    }

    private func containsMailboxLabel(_ target: String) -> Bool {
        labelIDs.contains { $0.caseInsensitiveCompare(target) == .orderedSame }
            || labels.contains { $0.caseInsensitiveCompare(target) == .orderedSame }
    }
}

private extension GmailThreadChildRow {
    func isVisible(in mailboxLabel: MailboxLabel) -> Bool {
        switch mailboxLabel {
        case .all:
            return true
        case .inbox:
            return containsMailboxLabel("INBOX")
        case .sent:
            return containsMailboxLabel("SENT")
        case .drafts:
            return containsMailboxLabel("DRAFT")
        case .spam:
            return containsMailboxLabel("SPAM")
        case .trash:
            return containsMailboxLabel("TRASH")
        case .archive:
            return true
        case .starred:
            return containsMailboxLabel("STARRED")
        }
    }

    private func containsMailboxLabel(_ target: String) -> Bool {
        labelIDs.contains { $0.caseInsensitiveCompare(target) == .orderedSame }
            || labels.contains { $0.caseInsensitiveCompare(target) == .orderedSame }
    }
}

private extension Array where Element: Hashable {
    func uniqued() -> [Element] {
        var seen = Set<Element>()
        return filter { seen.insert($0).inserted }
    }
}

private extension ThreadReaderResponse {
    func settingMessageLabel(_ labelID: String, presenceByMessageID: [String: Bool]) -> ThreadReaderResponse {
        ThreadReaderResponse(
            entityID: entityID,
            userID: userID,
            source: source,
            gmailThreadID: gmailThreadID,
            subject: subject,
            title: title,
            summary: summary,
            totalMessages: totalMessages,
            limit: limit,
            offset: offset,
            hasMore: hasMore,
            messages: messages.map { message in
                guard let isPresent = presenceByMessageID[message.id] else {
                    return message
                }
                return message.settingLabel(labelID, isPresent: isPresent)
            }
        )
    }

    var needsHTMLRenderDocumentRefresh: Bool {
        messages.contains { $0.needsHTMLRenderDocumentRefresh }
    }

    var needsReaderBodyRefresh: Bool {
        needsHTMLRenderDocumentRefresh || messages.contains { $0.needsReaderBodyRefresh }
    }
}

private extension ThreadMessage {
    func hasLabel(_ labelID: String) -> Bool {
        labelIDs.contains { $0.caseInsensitiveCompare(labelID) == .orderedSame }
    }

    func settingLabel(_ labelID: String, isPresent: Bool) -> ThreadMessage {
        guard hasLabel(labelID) != isPresent else {
            return self
        }
        var nextLabelIDs = labelIDs.filter { $0.caseInsensitiveCompare(labelID) != .orderedSame }
        if isPresent {
            nextLabelIDs.append(labelID)
        }
        return ThreadMessage(copying: self, labelIDs: nextLabelIDs)
    }

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
        !bodyComplete
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
