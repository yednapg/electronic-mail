import AppKit
import CoreServices
import CryptoKit
import Foundation

public struct AttachmentDownloadID: Hashable, Sendable {
    public let messageID: String
    public let attachmentID: String

    public init(messageID: String, attachmentID: String) {
        self.messageID = messageID
        self.attachmentID = attachmentID
    }
}

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

/// A foreground attachment open is not itself stored in an actor task map.
/// This lock-backed fence lets account invalidation stop it at every suspension
/// and file-operation boundary without relying only on cooperative Task
/// cancellation.
final class AttachmentOpenAuthorization: @unchecked Sendable {
    private let lock = NSLock()
    private var valid = true

    func invalidate() {
        lock.lock()
        valid = false
        lock.unlock()
    }

    func check() throws {
        try Task.checkCancellation()
        lock.lock()
        let isValid = valid
        lock.unlock()
        guard isValid else {
            throw CancellationError()
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

    func saveAndOpen(
        data: Data,
        at fileURL: URL,
        authorization: AttachmentOpenAuthorization? = nil
    ) throws -> AttachmentFileHandlingResult {
        try Task.checkCancellation()
        try authorization?.check()
        let accessed = startAccessingSecurityScope(fileURL)
        defer {
            if accessed {
                stopAccessingSecurityScope(fileURL)
            }
        }

        try Task.checkCancellation()
        try authorization?.check()
        do {
            try writeData(data, fileURL)
        } catch {
            throw AttachmentFileHandlingError.couldNotSave(error)
        }

        try Task.checkCancellation()
        try authorization?.check()
        do {
            try quarantineFile(fileURL)
        } catch {
            throw AttachmentFileHandlingError.couldNotQuarantine(error)
        }

        try Task.checkCancellation()
        try authorization?.check()
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
        suggestedFilename: String,
        authorization: AttachmentOpenAuthorization? = nil
    ) async throws -> AttachmentFileHandlingResult {
        try Task.checkCancellation()
        try authorization?.check()
        guard let fileURL = chooseDestination(suggestedFilename) else {
            return .cancelled
        }
        try Task.checkCancellation()
        try authorization?.check()

        let operations = operations
        let data = attachment.data
        let fileTask = Task.detached(priority: .userInitiated) {
            try operations.saveAndOpen(
                data: data,
                at: fileURL,
                authorization: authorization
            )
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

public enum MailboxSetupDeadlineDecision: Equatable {
    case wait
    case enter
    case retry
}

public struct MailboxSetupDeadlinePolicy {
    public let startedAt: Date
    public let minimumDisplaySeconds: TimeInterval
    public let maximumWaitSeconds: TimeInterval

    public init(
        startedAt: Date,
        minimumDisplaySeconds: TimeInterval,
        maximumWaitSeconds: TimeInterval
    ) {
        self.startedAt = startedAt
        self.minimumDisplaySeconds = max(0, minimumDisplaySeconds)
        self.maximumWaitSeconds = max(0, maximumWaitSeconds)
    }

    public func decision(
        now: Date,
        initialWindowReady: Bool,
        committedBatchReady: Bool
    ) -> MailboxSetupDeadlineDecision {
        let elapsed = max(0, now.timeIntervalSince(startedAt))
        if elapsed >= minimumDisplaySeconds, initialWindowReady {
            return .enter
        }
        if elapsed >= maximumWaitSeconds {
            return committedBatchReady ? .enter : .retry
        }
        return .wait
    }
}

public struct MailboxSetupProgressSnapshot: Equatable {
    public let phase: String
    public let initialMetadataCount: Int
    public let initialTargetCount: Int
    public let initialBodyReadyCount: Int
    public let initialBodyTargetCount: Int
    public let historyMetadataCount: Int
    public let historyBodyReadyCount: Int
    public let estimatedTotalCount: Int
    public let initialWindowComplete: Bool
    public let historyMetadataComplete: Bool
    public let historyBodyComplete: Bool
    public let hasVerifiedBatch: Bool
    public let confirmedEmpty: Bool

    public var initialTargetReady: Bool {
        guard hasVerifiedBatch || confirmedEmpty else {
            return false
        }
        if confirmedEmpty {
            return true
        }
        return initialTargetCount > 0
            && initialMetadataCount >= initialTargetCount
            && initialBodyTargetCount > 0
            && initialBodyReadyCount >= min(25, min(initialTargetCount, initialBodyTargetCount))
    }

    public var progressFraction: Double {
        if initialTargetReady {
            return 1
        }
        let metadataFraction = Self.fraction(initialMetadataCount, initialTargetCount)
        let bodyFraction = Self.fraction(initialBodyReadyCount, initialBodyTargetCount)
        if initialTargetCount <= 0 {
            return bodyFraction
        }
        if initialBodyTargetCount <= 0 {
            return metadataFraction
        }
        return min(1, (metadataFraction + bodyFraction) / 2)
    }

    private static func fraction(_ value: Int, _ target: Int) -> Double {
        guard target > 0 else {
            return 0
        }
        return min(1, max(0, Double(value) / Double(target)))
    }
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
    var hydratedThreads: [MailboxHydratedThreadState] = []
}

private struct MailboxPageContext: Equatable {
    let generation: UInt
    let label: MailboxLabel
    let searchQuery: String?
    let userID: String

    var paginationKey: MailboxPaginationKey {
        MailboxPaginationKey(label: label, searchQuery: searchQuery)
    }
}

private struct MailboxPaginationKey: Hashable {
    let label: MailboxLabel
    let searchQuery: String?
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

private enum MailboxPaginationError: LocalizedError {
    case repeatedCursor
    case pageLimitExceeded
    case mismatchedLabel
    case mismatchedRevision
    case incompleteSnapshot

    var errorDescription: String? {
        switch self {
        case .repeatedCursor:
            return "The mailbox server repeated a page. Try refreshing again."
        case .pageLimitExceeded:
            return "The mailbox is too large to load safely in one pass. Try refreshing again."
        case .mismatchedLabel, .mismatchedRevision:
            return "The mailbox changed while it was loading. Try refreshing again."
        case .incompleteSnapshot:
            return "The mailbox server returned an incomplete result. Try refreshing again."
        }
    }
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

private struct ConversationExpansionContext: Equatable {
    let requestID: UUID
    let accountGeneration: UInt
    let userID: String
    let mailboxLabel: MailboxLabel
    let searchQuery: String?
    let mailboxRowIdentity: ConversationMailboxRowIdentity
}

private struct ConversationMailboxRowIdentity: Equatable {
    let contentRevision: String?
    let messageCount: Int
    let latestSourceRecordID: String

    init(row: GmailThreadRow) {
        let normalizedRevision = row.contentRevision?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        contentRevision = normalizedRevision?.isEmpty == false ? normalizedRevision : nil
        messageCount = row.messageCount
        latestSourceRecordID = row.latestSourceRecordID
    }
}

private struct ConversationContentIdentity: Equatable {
    let contentRevision: String?
    let totalMessages: Int
    let messageIDs: [String]

    init(thread: ThreadReaderResponse) {
        contentRevision = thread.contentRevision
        totalMessages = thread.totalMessages
        var seen: Set<String> = []
        messageIDs = thread.messages.compactMap { message in
            seen.insert(message.id).inserted ? message.id : nil
        }
    }
}

private struct AuthoritativeConversationContent: Equatable {
    let mailboxRowIdentity: ConversationMailboxRowIdentity
    let threadIdentity: ConversationContentIdentity
}

private enum AuthoritativeConversationExpansionResult {
    case expanded
    case becameSingleMessage
    case rejected
}

private enum ThreadPrefetchOutcome {
    case cached(ThreadReaderResponse)
    case started(request: ThreadPrefetchRequestRecord, thread: ThreadReaderResponse)
    case joined(request: ThreadPrefetchRequestRecord, thread: ThreadReaderResponse)
    case failed

    var completedNetworkRequest: Bool {
        switch self {
        case .started, .joined:
            return true
        case .cached, .failed:
            return false
        }
    }

    var thread: ThreadReaderResponse? {
        switch self {
        case .cached(let thread), .started(_, let thread), .joined(_, let thread):
            return thread
        case .failed:
            return nil
        }
    }
}

private final class ThreadPrefetchRequestRecord {
    var isAccepted = true
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

/// A synchronous epoch fence shared by InboxStore and its background writer.
///
/// The lock is intentionally held across the underlying store mutation. This
/// lets a synchronous token clear retire the old epoch and wait for any write
/// that already passed validation before deleting the database rows and
/// account key. Writes that have not started yet validate after the retirement
/// and are discarded.
private final class LocalMailStoreWriteEpochFence: @unchecked Sendable {
    private let lock = NSLock()
    private var invalidatedThroughGeneration: UInt?

    func performWrite(
        operationGeneration: UInt,
        _ operation: () -> Void
    ) {
        lock.lock()
        defer { lock.unlock() }
        if let invalidatedThroughGeneration,
           operationGeneration <= invalidatedThroughGeneration {
            return
        }
        operation()
    }

    func invalidate(through operationGeneration: UInt) {
        lock.lock()
        if let invalidatedThroughGeneration {
            self.invalidatedThroughGeneration = max(
                invalidatedThroughGeneration,
                operationGeneration
            )
        } else {
            invalidatedThroughGeneration = operationGeneration
        }
        lock.unlock()
    }
}

private actor LocalMailStoreWorker {
    private let store: LocalMailStore
    private let writeEpochFence: LocalMailStoreWriteEpochFence

    init(store: LocalMailStore, writeEpochFence: LocalMailStoreWriteEpochFence) {
        self.store = store
        self.writeEpochFence = writeEpochFence
    }

    func readSession() -> AppSessionResponse? {
        store.readSession()
    }

    func writeSession(_ session: AppSessionResponse, operationGeneration: UInt) {
        writeEpochFence.performWrite(operationGeneration: operationGeneration) {
            store.writeSession(session)
        }
    }

    func readMailbox(userID: String, label: MailboxLabel) -> MailboxResponse? {
        store.readMailbox(userID: userID, label: label)
    }

    func writeMailbox(
        _ mailbox: MailboxResponse,
        userID: String,
        label: MailboxLabel,
        operationGeneration: UInt
    ) {
        writeEpochFence.performWrite(operationGeneration: operationGeneration) {
            store.writeMailbox(mailbox, userID: userID, label: label)
        }
    }

    func writeMailbox(
        _ mailbox: MailboxResponse,
        userID: String,
        label: MailboxLabel,
        removingThreadIDs: [String],
        reenteringThreadIDs: [String],
        reentryLabels: [MailboxLabel],
        operationGeneration: UInt
    ) {
        writeEpochFence.performWrite(operationGeneration: operationGeneration) {
            store.writeMailbox(
                mailbox,
                userID: userID,
                label: label,
                removingThreadIDs: removingThreadIDs,
                reenteringThreadIDs: reenteringThreadIDs,
                reentryLabels: reentryLabels
            )
        }
    }

    func readThread(userID: String, threadID: String) -> ThreadReaderResponse? {
        store.readThread(userID: userID, threadID: threadID)
    }

    func writeThread(
        _ thread: ThreadReaderResponse,
        userID: String,
        threadID: String,
        operationGeneration: UInt
    ) {
        writeEpochFence.performWrite(operationGeneration: operationGeneration) {
            store.writeThread(thread, userID: userID, threadID: threadID)
        }
    }

    /// Actor isolation makes this a drain barrier for writes already submitted
    /// to the worker. The shared fence also rejects stale writes submitted by a
    /// caller that resumes only after this method returns.
    func invalidateWritesAndDrain(through operationGeneration: UInt) {
        writeEpochFence.invalidate(through: operationGeneration)
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
        didSet {
            invalidateInboxSections()
            updateVisibleMailboxPageLoading()
        }
    }
    @Published private(set) var activeMailbox: MailboxResponse? {
        didSet {
            if !isSearchActive {
                reconcileConversationExpansionState(with: activeMailbox)
            }
            invalidateInboxSections()
        }
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
        didSet {
            invalidateInboxSections()
            updateVisibleMailboxPageLoading()
        }
    }
    @Published private(set) var searchResults: MailboxResponse? {
        didSet {
            if isSearchActive {
                reconcileConversationExpansionState(with: searchResults)
            }
            invalidateInboxSections()
        }
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
    @Published public private(set) var downloadingAttachmentIDs: Set<AttachmentDownloadID> = []

    private let client: AppClient
    private let sessionCache: AppSessionCache
    private let threadCache: ThreadCache
    private let localMailStore: LocalMailStore
    private let localMailStoreWriteEpochFence: LocalMailStoreWriteEpochFence
    private let localMailStoreWorker: LocalMailStoreWorker
    private let automaticallyPrefetchThreads: Bool
    private let bodyRefreshDelaysNanoseconds: [UInt64]
    private var inFlightSessionRefresh: Task<AppSessionResponse, Error>?
    private var inFlightMailboxRefresh: Task<MailboxResponse, Error>?
    private var inFlightMailboxRefreshContext: MailboxRefreshContext?
    private var inFlightMailboxPages: [MailboxPaginationKey: Task<MailboxResponse, Error>] = [:]
    private var inFlightThreads: [String: Task<ThreadReaderResponse, Error>] = [:]
    private var inFlightThreadRequestRecords: [String: ThreadPrefetchRequestRecord] = [:]
    private var inFlightThreadLabelMutationGenerations: [String: UInt] = [:]
    private var selectionPrefetchTask: Task<Void, Never>?
    private var priorityPrefetchTask: Task<Void, Never>?
    private var syncLoopTask: Task<Void, Never>?
    private var eventStreamTask: Task<Void, Never>?
    private var eventRefreshTask: Task<Void, Never>?
    private var postSendRefreshTask: Task<Void, Never>?
    private var readinessRefreshTask: Task<Void, Never>?
    private var readinessRefreshOwnerID: UUID?
    private var lastSyncTriggerAt: Date?
    private var lastMailboxRefreshAt: [MailboxLabel: Date] = [:]
    private var mailboxPageGenerations: [MailboxPaginationKey: UInt] = [:]
    private var mailboxPageRequestIDs: [MailboxPaginationKey: UUID] = [:]
    private var searchRequestID: UUID?
    private var pendingSearchCompletionRefresh = false
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
    private var pendingConversationExpansions: [String: ConversationExpansionContext] = [:]
    private var authoritativeConversationContent: [String: AuthoritativeConversationContent] = [:]
    private var folderCountPrefetchEnabled = false
    private var folderCountsRefreshRevision: UInt = 0
    private var folderCountsRefreshOwnerID: UUID?
    private var lastCompletedFolderCountsRefreshAt: Date?
    private var inFlightFolderCountRequest: Task<MailboxResponse, Error>?
    private var inFlightFolderCountRequestID: UUID?
    private var accountOperationGeneration: UInt = 0
    private var localMailStoreWritesBlocked = false
    private var attachmentOpenAuthorizations: [UUID: AttachmentOpenAuthorization] = [:]
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
    private let mailboxPagePublicationRowBatchSize = 500
    private let maximumAutomaticMailboxPages = 1_000
    private let maximumMailboxReconciliationRestarts = 3
    private let mailboxReconciliationRestartDelayNanoseconds: UInt64 = 25_000_000
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
        let localMailStoreWriteEpochFence = LocalMailStoreWriteEpochFence()
        self.localMailStoreWriteEpochFence = localMailStoreWriteEpochFence
        self.localMailStoreWorker = LocalMailStoreWorker(
            store: localMailStore,
            writeEpochFence: localMailStoreWriteEpochFence
        )
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
            let retiringWriteGeneration = accountOperationGeneration
            localMailStoreWritesBlocked = token?.isEmpty != false
            invalidateAccountScopedOperations()
            // This synchronous retirement shares the same lock held across
            // worker mutations. A direct token clear therefore cannot delete
            // mail and its encryption key while an already-authorized writer
            // is still committing, and queued old-generation writes are
            // rejected when they eventually execute.
            localMailStoreWriteEpochFence.invalidate(through: retiringWriteGeneration)
        }
        if token == nil {
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
            pendingConversationExpansions = [:]
            authoritativeConversationContent = [:]
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
            pendingSearchCompletionRefresh = false
            searchHydrationRefreshTask?.cancel()
            searchHydrationRefreshTask = nil
            lastSSEConnectedAt = nil
            lastSSEDisconnectedAt = nil
            lastSSEEventID = nil
            lastSSEEventType = nil
            lastSSEEventAt = nil
            lastForcedMailboxRefreshAt = nil
            lastForcedMailboxRefreshError = nil
            downloadingAttachmentIDs = []
            attachmentErrorMessage = nil
            timestampLabelCache.removeAll(keepingCapacity: false)
            sessionCache.clear()
            if tokenChanged {
                if preservingPendingThreadActions {
                    // Authentication expiry clears only identity/session state.
                    // Encrypted mail, its account key, and queued actions remain
                    // available for the same account after reauthentication.
                    localMailStore.clearSession()
                } else {
                    localMailStore.clearAll()
                }
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
        await prepareLocalMailStoreForAccountPurge()
        try await client.logout()
    }

    public func disconnectGoogleAndDeleteData() async throws {
        await prepareLocalMailStoreForAccountPurge()
        try await client.disconnectGoogle(deleteData: true, revokeSessions: true)
    }

    public func deleteAccountPermanently() async throws {
        await prepareLocalMailStoreForAccountPurge()
        try await client.deleteAccount()
    }

    public func deleteSyncedGoogleData() async throws {
        await prepareLocalMailStoreForAccountPurge()
        do {
            try await client.deleteGoogleData()
        } catch {
            // OfflineFirstAppClient resumes its authenticated account
            // generation after this scoped data purge, including on failure.
            // Match that contract so a retry can rebuild the local cache.
            localMailStoreWritesBlocked = false
            throw error
        }
        localMailStore.clearAll()
        localMailStoreWritesBlocked = false
        pendingLocalActionCount = 0
        threadCache.clearMemory()
        sessionCache.clear()
        activeMailbox = nil
        searchResults = nil
        openedThreads = [:]
        pendingConversationExpansions = [:]
        authoritativeConversationContent = [:]
        expandedThreadIDs = []
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
        guard mailboxPresentationReady else {
            return []
        }
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
                let expansionRequested = expandedThreadIDs.contains(row.threadID)
                let childRows = expansionRequested
                    ? (verifiedConversationChildren(for: row) ?? [])
                    : []
                let isExpanded = expansionRequested
                    && childRows.count >= 2
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
                    isExpandable: row.messageCount > 1,
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

    var pendingConversationExpansionThreadIDs: Set<String> {
        Set(pendingConversationExpansions.keys)
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

    public var setupProgress: MailboxSetupProgressSnapshot {
        let progress = preferredMailboxSyncProgress
        let mailbox = activeMailboxLabel == .inbox ? activeMailbox : session?.mailbox
        let rows = mailbox?.sections.flatMap(\.rows) ?? []
        let estimatedTotal = max(
            0,
            max(progress.estimatedTotalCount ?? 0, mailbox?.totalThreads ?? 0)
        )
        let defaultInitialTarget = min(100, estimatedTotal)
        let reportedInitialTarget = progress.initialTargetCount ?? 0
        let initialTarget = reportedInitialTarget > 0 ? reportedInitialTarget : defaultInitialTarget
        let defaultBodyTarget = min(25, initialTarget)
        let reportedBodyTarget = progress.initialBodyTargetCount ?? 0
        let bodyTarget = reportedBodyTarget > 0
            ? min(25, min(initialTarget, reportedBodyTarget))
            : defaultBodyTarget
        let metadataCount = max(
            0,
            max(progress.initialMetadataCount ?? 0, min(rows.count, initialTarget))
        )
        let rowBodyReadyCount = rows.prefix(max(0, bodyTarget)).filter { $0.bodyReady == true }.count
        let bodyReadyCount = max(0, max(progress.initialBodyReadyCount ?? 0, rowBodyReadyCount))
        let confirmedEmpty = mailbox.map {
            Self.isConfirmedEmptyMailbox($0, progress: progress)
        } == true
        let completeLegacySnapshot = mailbox.map(Self.isCompleteMailboxSnapshot) == true
        let verifiedBatch = mailbox.map {
            Self.isVerifiedMailboxSnapshot($0, progress: progress)
        } == true
        let reportedPhase = progress.phase?.trimmingCharacters(in: .whitespacesAndNewlines)
        let phase = reportedPhase?.isEmpty == false ? reportedPhase! : (currentReadiness?.stage ?? "starting")

        return MailboxSetupProgressSnapshot(
            phase: phase,
            initialMetadataCount: metadataCount,
            initialTargetCount: initialTarget,
            initialBodyReadyCount: bodyReadyCount,
            initialBodyTargetCount: bodyTarget,
            historyMetadataCount: max(0, progress.historyMetadataCount ?? 0),
            historyBodyReadyCount: max(0, progress.historyBodyReadyCount ?? 0),
            estimatedTotalCount: estimatedTotal,
            initialWindowComplete: progress.initialWindowComplete ?? completeLegacySnapshot,
            historyMetadataComplete: progress.historyMetadataComplete ?? completeLegacySnapshot,
            historyBodyComplete: progress.historyBodyComplete ?? completeLegacySnapshot,
            hasVerifiedBatch: verifiedBatch,
            confirmedEmpty: confirmedEmpty
        )
    }

    public var isReadyForMainInterface: Bool {
        let legacyReady = !preferredMailboxSyncProgress.hasReportedProgress
            && session?.readiness.readyToEnter == true
        return mailboxPresentationReady && (setupProgress.initialTargetReady || legacyReady)
    }

    public var canEnterWithBuildingDashboard: Bool {
        setupProgress.hasVerifiedBatch || setupProgress.confirmedEmpty
    }

    public var mailboxPresentationReady: Bool {
        guard let mailbox = visibleMailbox else {
            return false
        }
        let progress = mailbox.label == .inbox
            ? preferredMailboxSyncProgress
            : mailbox.mailboxSyncProgress
        return Self.isVerifiedMailboxSnapshot(mailbox, progress: progress)
    }

    public var mailboxLoadingProgressText: String? {
        guard !mailboxPresentationReady else {
            return nil
        }
        guard let mailbox = visibleMailbox else {
            return isSearchActive ? "Searching your mailbox..." : "Loading your mailbox..."
        }
        let visibleRows = mailbox.sections.reduce(0) { $0 + $1.rows.count }
        let loadedThreads = min(mailbox.totalThreads, visibleRows)
        guard mailbox.totalThreads > 0 else {
            return isSearchActive ? "Searching your mailbox..." : "Syncing your mailbox..."
        }
        if mailbox.fullImportRunning == true || mailbox.fullImportCompleted == false,
           loadedThreads >= mailbox.totalThreads,
           mailbox.nextCursor?.isEmpty != false {
            return "Finishing mailbox sync..."
        }
        return "Loading \(loadedThreads) of \(mailbox.totalThreads) emails..."
    }

    public var mailboxFooterProgressText: String? {
        guard mailboxPresentationReady, let mailbox = visibleMailbox else {
            return nil
        }
        if refreshFailed {
            return "Sync paused. Your saved email is still available."
        }
        if isSearchActive {
            guard mailboxPageLoading || mailbox.nextCursor?.isEmpty == false else {
                return nil
            }
            let loaded = mailbox.sections.reduce(0) { $0 + $1.rows.count }
            return "Loading more results · \(loaded) of \(max(loaded, mailbox.totalThreads))"
        }

        let progress = preferredMailboxSyncProgress
        if progress.hasReportedProgress {
            let metadataCount = max(
                mailbox.sections.reduce(0) { $0 + $1.rows.count },
                max(progress.initialMetadataCount ?? 0, progress.historyMetadataCount ?? 0)
            )
            let estimatedTotal = max(0, progress.estimatedTotalCount ?? 0)
            if progress.historyMetadataComplete != true {
                return estimatedTotal > 0
                    ? "Loading older mail… \(metadataCount.formatted(.number)) of \(max(metadataCount, estimatedTotal).formatted(.number))"
                    : "\(metadataCount.formatted(.number)) conversations ready"
            }
            if progress.historyBodyComplete != true {
                let readyBodies = max(
                    progress.initialBodyReadyCount ?? 0,
                    progress.historyBodyReadyCount ?? 0
                )
                return estimatedTotal > 0
                    ? "Saving email for offline use · \(readyBodies) of \(estimatedTotal)"
                    : "Saving email for offline use · \(readyBodies) conversations ready"
            }
        }
        if mailboxPageLoading || mailbox.nextCursor?.isEmpty == false {
            let loaded = mailbox.sections.reduce(0) { $0 + $1.rows.count }
            return "Loading older mail… \(loaded.formatted(.number)) of \(max(loaded, mailbox.totalThreads).formatted(.number))"
        }
        return nil
    }

    public var mailboxFooterShowsProgress: Bool {
        mailboxFooterProgressText != nil && !refreshFailed
    }

    public var mailboxFooterShowsRetry: Bool {
        mailboxPresentationReady && refreshFailed
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

    public func load() async {
        guard client.mode != .localBackend || client.sessionToken?.isEmpty == false else {
            phase = .failed("Sign in with Google to load your mailbox.")
            return
        }
        _ = await restoreLocalCache()
        if session == nil {
            phase = .loading
        }
        await refresh(allowEmptyDashboard: false, forceMailbox: true)
        if session != nil {
            startLiveRefreshLoop()
            await refreshFolderCounts()
        }
    }

    @discardableResult
    public func restoreLocalCache() async -> Bool {
        let operationGeneration = accountOperationGeneration
        let memoryCachedSession = session ?? sessionCache.read()
        let diskCachedSession = memoryCachedSession == nil ? await localMailStoreWorker.readSession() : nil
        guard operationGeneration == accountOperationGeneration else {
            return false
        }
        guard let cached = memoryCachedSession ?? diskCachedSession else {
            return false
        }
        if session != cached {
            session = cached
        }
        let separatelyCachedMailbox = await localMailStoreWorker.readMailbox(
            userID: cached.user.id,
            label: activeMailboxLabel
        )
        guard operationGeneration == accountOperationGeneration,
              session?.user.id == cached.user.id else {
            return false
        }
        let candidate = Self.preferredRestoredMailbox(
            session: cached,
            separatelyCachedMailbox: separatelyCachedMailbox,
            label: activeMailboxLabel
        )
        if activeMailbox != candidate {
            activeMailbox = candidate
        }
        let verified = candidate.map {
            Self.isVerifiedMailboxSnapshot($0, progress: cached.readiness.mailboxSyncProgress)
        } == true
        phase = verified ? .loaded : .loading
        await refreshPendingLocalActionCount()
        guard operationGeneration == accountOperationGeneration,
              session?.user.id == cached.user.id else {
            return false
        }
        if verified {
            seedActiveSelectionIfNeeded()
            refreshReaderRow()
        }
        return verified
    }

    private static func preferredRestoredMailbox(
        session: AppSessionResponse,
        separatelyCachedMailbox: MailboxResponse?,
        label: MailboxLabel
    ) -> MailboxResponse? {
        guard label == .inbox else {
            return separatelyCachedMailbox
        }
        guard let separatelyCachedMailbox else {
            return session.mailbox
        }
        let sessionMailbox = session.mailbox
        guard sessionMailbox.label == label else {
            return separatelyCachedMailbox
        }

        let progress = MailboxSyncProgress.newestMerged([
            session.readiness.mailboxSyncProgress,
            session.sync.mailboxSyncProgress,
            sessionMailbox.mailboxSyncProgress,
        ])
        let sessionGeneration = normalizedMailboxGeneration(sessionMailbox.syncGeneration)
        let separateGeneration = normalizedMailboxGeneration(separatelyCachedMailbox.syncGeneration)
        let reportedGeneration = normalizedMailboxGeneration(progress.syncGeneration)

        if sessionGeneration != separateGeneration {
            if reportedGeneration == sessionGeneration, sessionGeneration != nil {
                return sessionMailbox
            }
            if reportedGeneration == separateGeneration, separateGeneration != nil {
                return separatelyCachedMailbox
            }
            switch (
                mailboxProgressDate(sessionMailbox),
                mailboxProgressDate(separatelyCachedMailbox)
            ) {
            case let (sessionDate?, separateDate?) where sessionDate != separateDate:
                return sessionDate > separateDate ? sessionMailbox : separatelyCachedMailbox
            default:
                // Generation identifiers are opaque. The embedded mailbox is
                // part of the newest committed app-session record, so it is
                // the safe tie-breaker for an interrupted generation switch.
                return sessionMailbox
            }
        }

        if isConfirmedEmptyMailbox(sessionMailbox, progress: progress) {
            return sessionMailbox
        }
        let sessionIsAuthoritative = isCompleteMailboxSnapshot(sessionMailbox)
        let separateIsAuthoritative = isCompleteMailboxSnapshot(separatelyCachedMailbox)
        if sessionIsAuthoritative != separateIsAuthoritative {
            return sessionIsAuthoritative ? sessionMailbox : separatelyCachedMailbox
        }

        let sessionRows = sessionMailbox.sections.reduce(0) { $0 + $1.rows.count }
        let separateRows = separatelyCachedMailbox.sections.reduce(0) { $0 + $1.rows.count }
        if sessionRows != separateRows {
            return sessionRows > separateRows ? sessionMailbox : separatelyCachedMailbox
        }
        switch (
            mailboxProgressDate(sessionMailbox),
            mailboxProgressDate(separatelyCachedMailbox)
        ) {
        case let (sessionDate?, separateDate?) where sessionDate != separateDate:
            return sessionDate > separateDate ? sessionMailbox : separatelyCachedMailbox
        default:
            return sessionMailbox
        }
    }

    private static func normalizedMailboxGeneration(_ generation: String?) -> String? {
        guard let generation = generation?.trimmingCharacters(in: .whitespacesAndNewlines),
              !generation.isEmpty else {
            return nil
        }
        return generation
    }

    private static func mailboxProgressDate(_ mailbox: MailboxResponse) -> Date? {
        [mailbox.lastProgressAt, mailbox.generatedAt]
            .compactMap(mailboxTimestampDate)
            .max()
    }

    private static func mailboxTimestampDate(_ value: String?) -> Date? {
        guard let value, !value.isEmpty else {
            return nil
        }
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return fractional.date(from: value) ?? ISO8601DateFormatter().date(from: value)
    }

    public func refresh() async {
        await refresh(allowEmptyDashboard: false)
    }

    public func refreshForReadiness() async {
        await refreshSetupSyncState()
    }

    public func beginReadinessRefresh() {
        guard readinessRefreshTask == nil else {
            return
        }
        let ownerID = UUID()
        readinessRefreshOwnerID = ownerID
        readinessRefreshTask = Task { [weak self] in
            guard let self else { return }
            await self.refreshForReadiness()
            guard self.readinessRefreshOwnerID == ownerID else {
                return
            }
            self.readinessRefreshTask = nil
            self.readinessRefreshOwnerID = nil
        }
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
        guard let userID = session?.user.id else {
            return
        }
        searchHydrationRefreshTask?.cancel()
        searchHydrationRefreshTask = nil
        pendingConversationExpansions = [:]
        authoritativeConversationContent = [:]
        expandedThreadIDs = []
        pendingSearchCompletionRefresh = false
        cancelActiveMailboxRefresh()
        resetSearchMailboxPagination()
        let requestID = UUID()
        let paginationKey = MailboxPaginationKey(label: activeMailboxLabel, searchQuery: normalized)
        let pageContext = MailboxPageContext(
            generation: mailboxPageGeneration(for: paginationKey),
            label: activeMailboxLabel,
            searchQuery: normalized,
            userID: userID
        )
        searchRequestID = requestID
        searchQuery = normalized
        searchResults = nil
        searchInProgress = true
        searchError = nil
        defer { finishSearchRequest(id: requestID) }
        do {
            let response = try await client.searchMailbox(
                query: normalized,
                label: activeMailboxLabel,
                limit: mailboxPageLimit,
                cursor: nil,
                hydrateInBackground: true
            )
            guard searchRequestID == requestID, searchQuery == normalized else { return }
            let completed = try await completeMailboxSnapshot(startingWith: response, context: pageContext)
            guard searchRequestID == requestID,
                  searchQuery == normalized,
                  matchesMailboxPageContext(pageContext) else { return }
            searchResults = completed
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
        pendingConversationExpansions = [:]
        authoritativeConversationContent = [:]
        expandedThreadIDs = []
        pendingSearchCompletionRefresh = false
        resetSearchMailboxPagination()
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
            await persistLocalSession(
                merged,
                operationGeneration: operationGeneration
            )
            guard operationGeneration == accountOperationGeneration,
                  session?.user.id == merged.user.id else {
                return
            }
            await refreshPendingLocalActionCount()
            guard operationGeneration == accountOperationGeneration,
                  session?.user.id == merged.user.id else {
                return
            }
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
            guard operationGeneration == accountOperationGeneration else {
                return
            }
            if session == nil, let cached = memoryCachedSession ?? diskCachedSession {
                if session != cached {
                    session = cached
                }
                let cachedMailbox = await localMailStoreWorker.readMailbox(userID: cached.user.id, label: activeMailboxLabel)
                guard operationGeneration == accountOperationGeneration,
                      session?.user.id == cached.user.id else {
                    return
                }
                if activeMailbox != cachedMailbox {
                    activeMailbox = cachedMailbox
                }
                if activeMailbox == nil, activeMailboxLabel == .inbox {
                    if activeMailbox != cached.mailbox {
                        activeMailbox = cached.mailbox
                    }
                }
                let nextPhase: LoadPhase = activeMailbox.map(Self.isCompleteMailboxSnapshot) == true
                    ? .loaded
                    : .failed(error.localizedDescription)
                if phase != nextPhase {
                    phase = nextPhase
                }
            } else if activeMailbox == nil,
                      activeMailboxLabel == .inbox,
                      let cachedInbox = session?.mailbox {
                if activeMailbox != cachedInbox {
                    activeMailbox = cachedInbox
                }
                let nextPhase: LoadPhase = Self.isCompleteMailboxSnapshot(cachedInbox)
                    ? .loaded
                    : .failed(error.localizedDescription)
                if phase != nextPhase {
                    phase = nextPhase
                }
                if Self.isCompleteMailboxSnapshot(cachedInbox) {
                    seedActiveSelectionIfNeeded()
                }
            } else if session == nil {
                let nextPhase = LoadPhase.failed(error.localizedDescription)
                if phase != nextPhase {
                    phase = nextPhase
                }
            } else if activeMailbox.map(Self.isCompleteMailboxSnapshot) != true {
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
        // The visible search owns the shared page-generation slot. Background
        // mailbox refreshes wait until search is cleared instead of cancelling
        // an in-progress search cursor chain.
        guard !isSearchActive else {
            return
        }
        let label = activeMailboxLabel
        let paginationKey = MailboxPaginationKey(label: label, searchQuery: nil)
        if inFlightMailboxPages[paginationKey] != nil {
            updateVisibleMailboxPageLoading()
            return
        }
        let now = Date()
        if !force,
           let lastRefresh = lastMailboxRefreshAt[label],
           activeMailbox != nil,
           now.timeIntervalSince(lastRefresh) < minimumMailboxRefreshGap {
            return
        }

        if inFlightMailboxRefresh != nil,
           let existingContext = inFlightMailboxRefreshContext,
           existingContext.accountGeneration == accountOperationGeneration,
           existingContext.userID == userID,
           existingContext.label == label,
           !force || existingContext.force {
            // The caller that created this refresh owns the complete cursor
            // chain. A second consumer of only its first-page task could race
            // the owner and clear presentation state while later pages load.
            return
        }
        cancelActiveMailboxRefresh()
        resetMailboxPagination(for: paginationKey)
        let context = MailboxRefreshContext(
            requestID: UUID(),
            accountGeneration: accountOperationGeneration,
            userID: userID,
            label: label,
            allowCachedFallback: allowCachedFallback,
            force: force,
            startedAt: now
        )
        let task = Task { [client, mailboxPageLimit] in
            try Task.checkCancellation()
            return try await client.mailbox(label: label, limit: mailboxPageLimit, cursor: nil)
        }
        inFlightMailboxRefreshContext = context
        inFlightMailboxRefresh = task

        do {
            let firstPage = try await task.value
            guard ownsActiveMailboxRefresh(context),
                  context.accountGeneration == accountOperationGeneration,
                  session?.user.id == context.userID,
                  activeMailboxLabel == context.label else {
                return
            }
            let hadVerifiedSnapshot = activeMailbox.map {
                Self.isVerifiedMailboxSnapshot($0, progress: preferredMailboxSyncProgress)
            } == true
            let canRetainVerifiedSnapshot = hadVerifiedSnapshot
            let discardLoadedPages = discardLoadedMailboxPagesOnNextRefresh
            let requiresFreshAuthoritativeChain = discardLoadedPages
                || activeMailbox.map {
                    Self.mailboxRevisionChangedWithinGeneration($0, firstPage)
                } == true
            let initialSnapshot = requiresFreshAuthoritativeChain
                ? firstPage
                : activeMailbox?.preservingLoadedPages(
                    afterRefreshingFirstPage: firstPage,
                    discardStalePages: false
                ) ?? firstPage
            discardLoadedMailboxPagesOnNextRefresh = false

            // A changed revision makes the new cursor chain authoritative for
            // removals as well as additions. Keep the last verified snapshot
            // visible until that replacement chain reaches its end; publishing
            // its verified first page would otherwise collapse a large mailbox
            // to one transport page and disturb selection/scroll position.
            // Fresh accounts have no verified fallback, so they continue to
            // publish progressive batches as soon as those batches verify.
            let defersAuthoritativeReplacement = requiresFreshAuthoritativeChain
                && canRetainVerifiedSnapshot
                && initialSnapshot.nextCursor?.isEmpty == false

            // Once rows have been verified, an unverified refresh is only
            // progress state. It must not empty the list or its disk fallback.
            let initialSnapshotIsVerified = Self.isVerifiedMailboxSnapshot(
                initialSnapshot,
                progress: preferredMailboxSyncProgress
            )
            if !defersAuthoritativeReplacement,
               (!canRetainVerifiedSnapshot || initialSnapshotIsVerified),
               activeMailbox != initialSnapshot {
                activeMailbox = initialSnapshot
            }

            let pageContext = MailboxPageContext(
                generation: mailboxPageGeneration(for: paginationKey),
                label: context.label,
                searchQuery: nil,
                userID: context.userID
            )
            if initialSnapshotIsVerified,
               !defersAuthoritativeReplacement {
                await publishVerifiedMailboxSnapshot(initialSnapshot, context: context)
            }
            let verifiedSnapshotPublisher: (@MainActor (MailboxResponse) async -> Void)?
            if defersAuthoritativeReplacement {
                verifiedSnapshotPublisher = nil
            } else {
                verifiedSnapshotPublisher = { [weak self] snapshot in
                    guard let self else {
                        return
                    }
                    await self.publishVerifiedMailboxSnapshot(snapshot, context: context)
                }
            }
            let completedSnapshot = try await completeMailboxSnapshot(
                startingWith: initialSnapshot,
                context: pageContext,
                onVerifiedSnapshot: verifiedSnapshotPublisher
            )
            guard ownsActiveMailboxRefresh(context),
                  context.accountGeneration == accountOperationGeneration,
                  session?.user.id == context.userID,
                  activeMailboxLabel == context.label else {
                return
            }

            let snapshotIsComplete = Self.isCompleteMailboxSnapshot(completedSnapshot)
            let snapshotIsVerified = Self.isVerifiedMailboxSnapshot(
                completedSnapshot,
                progress: preferredMailboxSyncProgress
            )
            let cursorChainComplete = completedSnapshot.nextCursor?.isEmpty != false
            let authoritativeReplacementReady = !defersAuthoritativeReplacement
                || cursorChainComplete
            let hasPresentableFallback = canRetainVerifiedSnapshot
            if (snapshotIsVerified && authoritativeReplacementReady) || !hasPresentableFallback {
                if activeMailbox != completedSnapshot {
                    activeMailbox = completedSnapshot
                }
            }
            if defersAuthoritativeReplacement,
               snapshotIsVerified,
               cursorChainComplete {
                await publishVerifiedMailboxSnapshot(completedSnapshot, context: context)
            }
            if authoritativeReplacementReady || !hasPresentableFallback {
                updateFolderCount(from: completedSnapshot)
            }
            if snapshotIsComplete,
               let revision = completedSnapshot.mailboxRevision,
               !revision.isEmpty {
                lastSeenMailboxRevision = revision
            }
            lastMailboxRefreshAt[context.label] = context.startedAt
            if context.force {
                lastForcedMailboxRefreshAt = Date()
                lastForcedMailboxRefreshError = nil
            }
            if refreshFailed {
                refreshFailed = false
            }
            let nextPhase: LoadPhase = snapshotIsVerified || hasPresentableFallback ? .loaded : .loading
            if phase != nextPhase {
                phase = nextPhase
            }
            if mailboxPresentationReady {
                seedActiveSelectionIfNeeded()
                refreshReaderRow()
                if automaticallyPrefetchThreads {
                    prefetchPriorityThreads()
                }
            }
            clearActiveMailboxRefresh(context)
            if snapshotIsVerified, authoritativeReplacementReady {
                await persistLocalMailbox(
                    completedSnapshot,
                    userID: context.userID,
                    label: context.label,
                    operationGeneration: context.accountGeneration
                )
            }
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
            if let cached,
               Self.isVerifiedMailboxSnapshot(cached, progress: preferredMailboxSyncProgress)
                || activeMailbox.map({ Self.isVerifiedMailboxSnapshot($0, progress: preferredMailboxSyncProgress) }) != true {
                if activeMailbox != cached {
                    activeMailbox = cached
                }
                let cachedIsVerified = Self.isVerifiedMailboxSnapshot(cached, progress: preferredMailboxSyncProgress)
                let nextPhase: LoadPhase = cachedIsVerified ? .loaded : .failed(error.localizedDescription)
                if phase != nextPhase {
                    phase = nextPhase
                }
                if cachedIsVerified {
                    seedActiveSelectionIfNeeded()
                }
            } else if context.allowCachedFallback,
                      context.label == .inbox,
                      let sessionInbox = session?.mailbox,
                      Self.isVerifiedMailboxSnapshot(sessionInbox, progress: preferredMailboxSyncProgress)
                        || activeMailbox.map({ Self.isVerifiedMailboxSnapshot($0, progress: preferredMailboxSyncProgress) }) != true {
                if activeMailbox != sessionInbox {
                    activeMailbox = sessionInbox
                }
                let sessionInboxIsVerified = Self.isVerifiedMailboxSnapshot(
                    sessionInbox,
                    progress: preferredMailboxSyncProgress
                )
                let nextPhase: LoadPhase = sessionInboxIsVerified ? .loaded : .failed(error.localizedDescription)
                if phase != nextPhase {
                    phase = nextPhase
                }
                if sessionInboxIsVerified {
                    seedActiveSelectionIfNeeded()
                }
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

    private func publishVerifiedMailboxSnapshot(
        _ snapshot: MailboxResponse,
        context: MailboxRefreshContext
    ) async {
        let progress = mailboxSyncProgress(for: snapshot)
        guard context.accountGeneration == accountOperationGeneration,
              session?.user.id == context.userID,
              snapshot.label == context.label,
              Self.isVerifiedMailboxSnapshot(snapshot, progress: progress) else {
            return
        }

        await persistLocalMailbox(
            snapshot,
            userID: context.userID,
            label: context.label,
            operationGeneration: context.accountGeneration
        )
        guard context.accountGeneration == accountOperationGeneration,
              session?.user.id == context.userID,
              activeMailboxLabel == context.label,
              !isSearchActive else {
            return
        }
        if activeMailbox != snapshot {
            activeMailbox = snapshot
        }
        updateFolderCount(from: snapshot)
        if phase != .loaded {
            phase = .loaded
        }
        seedActiveSelectionIfNeeded()
        refreshReaderRow()
    }

    public func loadMoreMailbox(automatic _: Bool = false) async {
        let operationGeneration = accountOperationGeneration
        guard !mailboxPageLoading,
              let mailbox = visibleMailbox,
              let cursor = mailbox.nextCursor,
              !cursor.isEmpty,
              let userID = session?.user.id else {
            return
        }

        let paginationKey = MailboxPaginationKey(
            label: activeMailboxLabel,
            searchQuery: isSearchActive ? searchQuery : nil
        )
        let context = MailboxPageContext(
            generation: mailboxPageGeneration(for: paginationKey),
            label: activeMailboxLabel,
            searchQuery: isSearchActive ? searchQuery : nil,
            userID: userID
        )
        do {
            let completed = try await completeMailboxSnapshot(startingWith: mailbox, context: context)
            guard matchesMailboxPageContext(context),
                  visibleMailbox?.nextCursor == cursor else {
                return
            }
            if context.searchQuery != nil {
                if searchResults != completed {
                    searchResults = completed
                }
            } else {
                if activeMailbox != completed {
                    activeMailbox = completed
                }
                updateFolderCount(from: completed)
                if Self.isCompleteMailboxSnapshot(completed) {
                    await persistLocalMailbox(
                        completed,
                        userID: userID,
                        label: context.label,
                        operationGeneration: operationGeneration
                    )
                    guard operationGeneration == accountOperationGeneration,
                          session?.user.id == userID else {
                        return
                    }
                }
            }
            if refreshFailed {
                refreshFailed = false
            }
            seedActiveSelectionIfNeeded()
            refreshReaderRow()
        } catch is CancellationError {
            // A new account, mailbox, search, or revision owns presentation.
        } catch {
            if matchesMailboxPageContext(context) {
                if context.searchQuery != nil {
                    searchError = error.localizedDescription
                } else {
                    refreshFailed = true
                }
            }
        }
    }

    private func completeMailboxSnapshot(
        startingWith initialSnapshot: MailboxResponse,
        context: MailboxPageContext,
        onVerifiedSnapshot: (@MainActor (MailboxResponse) async -> Void)? = nil
    ) async throws -> MailboxResponse {
        guard let firstCursor = initialSnapshot.nextCursor, !firstCursor.isEmpty else {
            try Self.validateCompletedMailboxSnapshot(initialSnapshot)
            return initialSnapshot
        }
        let paginationKey = context.paginationKey
        guard inFlightMailboxPages[paginationKey] == nil else {
            throw CancellationError()
        }

        let requestID = UUID()
        mailboxPageRequestIDs[paginationKey] = requestID
        let task = Task {
            [
                client,
                mailboxPageLimit,
                maximumAutomaticMailboxPages,
                maximumMailboxReconciliationRestarts,
                mailboxReconciliationRestartDelayNanoseconds,
                mailboxPagePublicationRowBatchSize,
            ] in
            let accumulator = MailboxPageAccumulator(initialSnapshot)
            var seenCursors: Set<String> = []
            var requestedPageCount = 0
            var reconciliationRestartCount = 0
            var rowsSincePublication = 0
            var finalPublishedSnapshot: MailboxResponse?

            do {
                while let cursor = await accumulator.nextCursor(), !cursor.isEmpty {
                    try Task.checkCancellation()
                    guard seenCursors.insert(cursor).inserted else {
                        throw MailboxPaginationError.repeatedCursor
                    }
                    requestedPageCount += 1
                    guard requestedPageCount <= maximumAutomaticMailboxPages else {
                        throw MailboxPaginationError.pageLimitExceeded
                    }

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
                        page = try await client.mailbox(
                            label: context.label,
                            limit: mailboxPageLimit,
                            cursor: cursor
                        )
                    }
                    try Task.checkCancellation()
                    guard page.label == context.label else {
                        throw MailboxPaginationError.mismatchedLabel
                    }
                    let currentIdentity = await accumulator.identitySnapshot()
                    guard Self.mailboxPaginationIdentityMatches(currentIdentity, page) else {
                        reconciliationRestartCount += 1
                        guard reconciliationRestartCount <= maximumMailboxReconciliationRestarts else {
                            // The backend is actively reconciling. Return the last
                            // internally consistent prefix as loading progress; it
                            // is never presentation-ready or persisted, and a
                            // later event/poll will start another bounded pass.
                            return await accumulator.snapshot()
                        }
                        let delay = mailboxReconciliationRestartDelayNanoseconds
                            * UInt64(reconciliationRestartCount)
                        try await Task.sleep(nanoseconds: delay)
                        try Task.checkCancellation()
                        let restarted: MailboxResponse
                        if let query = context.searchQuery {
                            restarted = try await client.searchMailbox(
                                query: query,
                                label: context.label,
                                limit: mailboxPageLimit,
                                cursor: nil,
                                hydrateInBackground: true
                            )
                        } else {
                            restarted = try await client.mailbox(
                                label: context.label,
                                limit: mailboxPageLimit,
                                cursor: nil
                            )
                        }
                        try Task.checkCancellation()
                        guard restarted.label == context.label else {
                            throw MailboxPaginationError.mismatchedLabel
                        }
                        await accumulator.reset(to: restarted)
                        seenCursors.removeAll(keepingCapacity: true)
                        rowsSincePublication = 0
                        finalPublishedSnapshot = nil
                        continue
                    }
                    rowsSincePublication += await accumulator.append(page)
                    let reachedEnd = page.nextCursor?.isEmpty != false
                    if let onVerifiedSnapshot,
                       rowsSincePublication >= mailboxPagePublicationRowBatchSize || reachedEnd {
                        let snapshot = await accumulator.snapshot()
                        await onVerifiedSnapshot(snapshot)
                        rowsSincePublication = 0
                        if reachedEnd {
                            finalPublishedSnapshot = snapshot
                        }
                    }
                }
            } catch {
                // A successfully decoded page is committed progress even if a
                // later continuation fails. Publish the bounded in-memory
                // prefix once before surfacing the retry state; cancellation
                // deliberately does not expose data to a superseded context.
                if !(error is CancellationError),
                   rowsSincePublication > 0,
                   let onVerifiedSnapshot {
                    await onVerifiedSnapshot(await accumulator.snapshot())
                }
                throw error
            }

            let accumulated: MailboxResponse
            if let finalPublishedSnapshot {
                accumulated = finalPublishedSnapshot
            } else {
                accumulated = await accumulator.snapshot()
            }
            try Self.validateCompletedMailboxSnapshot(accumulated)
            return accumulated
        }
        inFlightMailboxPages[paginationKey] = task
        updateVisibleMailboxPageLoading()
        defer {
            if mailboxPageRequestIDs[paginationKey] == requestID {
                inFlightMailboxPages[paginationKey] = nil
                mailboxPageRequestIDs[paginationKey] = nil
                updateVisibleMailboxPageLoading()
            }
        }

        let completed = try await task.value
        guard mailboxPageRequestIDs[paginationKey] == requestID,
              paginationContextIsCurrent(context) else {
            throw CancellationError()
        }
        return completed
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
            authoritativeConversationContent[threadID] = nil
            if selectedThreadID == threadID, selectedMessageID != nil {
                selectedMessageID = nil
                activeMessageID = nil
            }
            return
        }
        if pendingConversationExpansions.removeValue(forKey: threadID) != nil {
            return
        }
        guard let row = visibleMailboxRow(threadID: threadID),
              row.messageCount > 1,
              let userID = session?.user.id else {
            return
        }
        if let children = verifiedConversationChildren(for: row), children.count >= 2 {
            expandedThreadIDs.insert(threadID)
            return
        }

        let context = ConversationExpansionContext(
            requestID: UUID(),
            accountGeneration: accountOperationGeneration,
            userID: userID,
            mailboxLabel: activeMailboxLabel,
            searchQuery: isSearchActive ? searchQuery : nil,
            mailboxRowIdentity: ConversationMailboxRowIdentity(row: row)
        )
        pendingConversationExpansions[threadID] = context
        Task { [weak self] in
            await self?.prepareConversationExpansion(threadID: threadID, context: context)
        }
    }

    private func prepareConversationExpansion(
        threadID: String,
        context: ConversationExpansionContext
    ) async {
        guard conversationExpansionContextIsCurrent(context, threadID: threadID) else {
            return
        }
        if completeConversationExpansion(threadID: threadID, context: context) {
            return
        }

        let initialOutcome = await performThreadPrefetch(
            threadID: threadID,
            force: false,
            silent: true,
            replacingInFlightRequest: nil
        )
        guard conversationExpansionContextIsCurrent(context, threadID: threadID) else {
            return
        }
        if completeConversationExpansion(threadID: threadID, context: context) {
            return
        }
        switch initialOutcome {
        case .started(_, let thread):
            if await resolveAuthoritativeConversationExpansion(
                threadID: threadID,
                context: context,
                thread: thread
            ) {
                return
            }
            discardConversationExpansion(threadID: threadID, context: context)
            return
        case .joined(let request, _):
            if await retryConversationExpansionWithFreshNetworkRequest(
                threadID: threadID,
                context: context,
                replacingInFlightRequest: request
            ) {
                return
            }
            discardConversationExpansion(threadID: threadID, context: context)
            return
        case .failed:
            discardConversationExpansion(threadID: threadID, context: context)
            return
        case .cached:
            break
        }

        // The cache snapshot was structurally incomplete or revision-stale.
        // Bypass local storage and ask the coalesced network path exactly once.
        let forcedOutcome = await performThreadPrefetch(
            threadID: threadID,
            force: true,
            silent: true,
            replacingInFlightRequest: nil
        )
        guard conversationExpansionContextIsCurrent(context, threadID: threadID) else {
            return
        }
        if completeConversationExpansion(threadID: threadID, context: context) {
            return
        }
        switch forcedOutcome {
        case .started(_, let thread):
            if await resolveAuthoritativeConversationExpansion(
                threadID: threadID,
                context: context,
                thread: thread
            ) {
                return
            }
        case .joined(let request, _):
            if await retryConversationExpansionWithFreshNetworkRequest(
                threadID: threadID,
                context: context,
                replacingInFlightRequest: request
            ) {
                return
            }
        case .cached, .failed:
            break
        }
        discardConversationExpansion(threadID: threadID, context: context)
    }

    private func retryConversationExpansionWithFreshNetworkRequest(
        threadID: String,
        context: ConversationExpansionContext,
        replacingInFlightRequest: ThreadPrefetchRequestRecord
    ) async -> Bool {
        let outcome = await performThreadPrefetch(
            threadID: threadID,
            force: true,
            silent: true,
            replacingInFlightRequest: replacingInFlightRequest
        )
        guard outcome.completedNetworkRequest,
              let thread = outcome.thread,
              conversationExpansionContextIsCurrent(context, threadID: threadID) else {
            return false
        }
        if completeConversationExpansion(threadID: threadID, context: context) {
            return true
        }
        return await resolveAuthoritativeConversationExpansion(
            threadID: threadID,
            context: context,
            thread: thread
        )
    }

    private func resolveAuthoritativeConversationExpansion(
        threadID: String,
        context: ConversationExpansionContext,
        thread: ThreadReaderResponse
    ) async -> Bool {
        switch completeConversationExpansionFromAuthoritativeNetwork(
            threadID: threadID,
            context: context,
            thread: thread
        ) {
        case .expanded:
            return true
        case .becameSingleMessage:
            discardConversationExpansion(threadID: threadID, context: context)
            expandedThreadIDs.remove(threadID)
            authoritativeConversationContent[threadID] = nil
            await reconcileMailboxAfterConversationBecameSingleMessage()
            return true
        case .rejected:
            return false
        }
    }

    @discardableResult
    private func completeConversationExpansion(
        threadID: String,
        context: ConversationExpansionContext
    ) -> Bool {
        guard conversationExpansionContextIsCurrent(context, threadID: threadID),
              let row = visibleMailboxRow(threadID: threadID),
              row.messageCount > 1 else {
            return false
        }
        guard let children = verifiedConversationChildren(for: row),
              children.count >= 2 else {
            return false
        }
        pendingConversationExpansions[threadID] = nil
        expandedThreadIDs.insert(threadID)
        return true
    }

    private func completeConversationExpansionFromAuthoritativeNetwork(
        threadID: String,
        context: ConversationExpansionContext,
        thread: ThreadReaderResponse
    ) -> AuthoritativeConversationExpansionResult {
        guard conversationExpansionContextIsCurrent(context, threadID: threadID),
              !thread.hasMore else {
            return .rejected
        }
        let messages = Self.orderedUniqueMessages(thread.messages)
        if thread.totalMessages <= 1, messages.count <= 1 {
            return .becameSingleMessage
        }
        guard messages.count >= 2,
              messages.count >= thread.totalMessages else {
            return .rejected
        }
        authoritativeConversationContent[threadID] = AuthoritativeConversationContent(
            mailboxRowIdentity: context.mailboxRowIdentity,
            threadIdentity: ConversationContentIdentity(thread: thread)
        )
        pendingConversationExpansions[threadID] = nil
        expandedThreadIDs.insert(threadID)
        return .expanded
    }

    private func reconcileMailboxAfterConversationBecameSingleMessage() async {
        if isSearchActive {
            let query = searchQuery
            await searchMailbox(query)
            return
        }
        discardLoadedMailboxPagesOnNextRefresh = true
        lastMailboxRefreshAt[activeMailboxLabel] = nil
        await refreshActiveMailbox(allowCachedFallback: true, force: true)
    }

    private func discardConversationExpansion(
        threadID: String,
        context: ConversationExpansionContext
    ) {
        guard pendingConversationExpansions[threadID] == context else {
            return
        }
        pendingConversationExpansions[threadID] = nil
    }

    private func conversationExpansionContextIsCurrent(
        _ context: ConversationExpansionContext,
        threadID: String
    ) -> Bool {
        pendingConversationExpansions[threadID] == context
            && accountOperationGeneration == context.accountGeneration
            && session?.user.id == context.userID
            && activeMailboxLabel == context.mailboxLabel
            && (isSearchActive ? searchQuery : nil) == context.searchQuery
            && visibleMailboxRow(threadID: threadID).map { ConversationMailboxRowIdentity(row: $0) }
                == context.mailboxRowIdentity
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
        pendingConversationExpansions = [:]
        authoritativeConversationContent = [:]
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
        let operationGeneration = accountOperationGeneration
        let authorizationID = UUID()
        let authorization = AttachmentOpenAuthorization()
        attachmentOpenAuthorizations[authorizationID] = authorization
        let downloadID = AttachmentDownloadID(
            messageID: messageID,
            attachmentID: attachment.attachmentID
        )
        guard downloadingAttachmentIDs.insert(downloadID).inserted else {
            return
        }
        defer {
            downloadingAttachmentIDs.remove(downloadID)
            attachmentOpenAuthorizations[authorizationID] = nil
        }
        attachmentErrorMessage = nil

        let downloaded: DownloadedAttachment
        do {
            downloaded = try await client.downloadAttachment(messageID: messageID, attachment: attachment)
        } catch {
            guard operationGeneration == accountOperationGeneration,
                  (try? authorization.check()) != nil else {
                return
            }
            attachmentErrorMessage = "The attachment could not be downloaded. \(error.localizedDescription)"
            return
        }

        guard operationGeneration == accountOperationGeneration,
              (try? authorization.check()) != nil else {
            return
        }

        do {
            _ = try await fileHandler.saveAndOpen(
                downloaded,
                suggestedFilename: sanitizedAttachmentFilename(downloaded.filename),
                authorization: authorization
            )
        } catch is CancellationError {
            return
        } catch {
            guard operationGeneration == accountOperationGeneration,
                  (try? authorization.check()) != nil else {
                return
            }
            attachmentErrorMessage = error.localizedDescription
        }
    }

    public func dismissAttachmentError() {
        attachmentErrorMessage = nil
    }

    public func isAttachmentDownloading(_ attachment: ThreadAttachment, messageID: String) -> Bool {
        downloadingAttachmentIDs.contains(
            AttachmentDownloadID(messageID: messageID, attachmentID: attachment.attachmentID)
        )
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
            let removesFromCurrentMailbox = shouldRemoveFromCurrentMailbox(action: action)
            let reentryLabels = mailboxReentryLabels(for: action)
            applyLocalThreadAction(threadID: threadID, action: action, targetMessageID: targetMessageID)
            if let userID = session?.user.id, let activeMailbox {
                // Keep the optimistic projection on disk before asking the
                // offline-first client to refresh. If the network is down, that
                // refresh falls back to this cache; writing first prevents the
                // stale pre-action row from immediately reappearing.
                if removesFromCurrentMailbox || !reentryLabels.isEmpty {
                    await persistLocalMailbox(
                        activeMailbox,
                        userID: userID,
                        label: activeMailboxLabel,
                        removingThreadIDs: removesFromCurrentMailbox ? [threadID] : [],
                        reenteringThreadIDs: reentryLabels.isEmpty ? [] : [threadID],
                        reentryLabels: reentryLabels,
                        operationGeneration: operationGeneration
                    )
                } else {
                    await persistLocalMailbox(
                        activeMailbox,
                        userID: userID,
                        label: activeMailboxLabel,
                        operationGeneration: operationGeneration
                    )
                }
                guard operationGeneration == accountOperationGeneration,
                      session?.user.id == userID else {
                    return true
                }
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
            guard operationGeneration == accountOperationGeneration else {
                return true
            }
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
        let operationGeneration = accountOperationGeneration
        guard !readerLabelMutationOwners.keys.contains(where: { $0.threadID == threadID }),
              let thread = openedThreads[threadID],
              let userID = session?.user.id,
              thread.userID == userID else {
            return
        }
        await persistLocalThread(
            thread,
            userID: userID,
            threadID: threadID,
            operationGeneration: operationGeneration
        )
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

    @discardableResult
    private func installCachedThread(
        _ fetchedCachedThread: ThreadReaderResponse,
        userID: String,
        threadID: String,
        labelMutationGenerationAtReadStart: UInt,
        writeToMemoryCache: Bool
    ) -> ThreadReaderResponse {
        let cached = preservingReaderLabelMutations(
            in: fetchedCachedThread,
            threadID: threadID,
            since: labelMutationGenerationAtReadStart
        )
        if writeToMemoryCache {
            threadCache.write(cached, userID: userID, threadID: threadID)
        }
        openedThreads[threadID] = cached
        updateReader(threadID: threadID, thread: cached, error: nil)
        scheduleBodyRefreshIfNeeded(threadID: threadID, thread: cached)
        return cached
    }

    @discardableResult
    public func prefetchThread(
        threadID: String,
        force: Bool = false,
        silent: Bool = true
    ) async -> Bool {
        let outcome = await performThreadPrefetch(
            threadID: threadID,
            force: force,
            silent: silent,
            replacingInFlightRequest: nil
        )
        return outcome.completedNetworkRequest
    }

    private func performThreadPrefetch(
        threadID: String,
        force: Bool,
        silent: Bool,
        replacingInFlightRequest: ThreadPrefetchRequestRecord?
    ) async -> ThreadPrefetchOutcome {
        guard let userID = session?.user.id else {
            if !silent {
                let message = "Sign in with Google to load this email."
                threadErrors[threadID] = message
                if readerThreadID == threadID {
                    readerError = message
                }
            }
            return .failed
        }
        let operationGeneration = accountOperationGeneration
        let labelMutationGenerationAtReadStart = readerLabelMutationGenerations[threadID] ?? 0
        let openedThreadAtReadStart = force ? nil : openedThreads[threadID]
        let memoryCachedThread = force
            ? nil
            : threadCache.read(userID: userID, threadID: threadID).flatMap { $0.userID == userID ? $0 : nil }
        let diskCachedThread = memoryCachedThread == nil && !force
            ? await localMailStoreWorker.readThread(userID: userID, threadID: threadID)
            : nil
        guard operationGeneration == accountOperationGeneration,
              session?.user.id == userID else {
            return .failed
        }
        let currentOpenedThread = force
            ? nil
            : openedThreads[threadID].flatMap { $0.userID == userID ? $0 : nil }
        let concurrentlyInstalledThread = currentOpenedThread != openedThreadAtReadStart
            ? currentOpenedThread
            : nil
        let cachedThread = (concurrentlyInstalledThread ?? memoryCachedThread ?? diskCachedThread)
            .flatMap { $0.userID == userID ? $0 : nil }
        if let fetchedCachedThread = cachedThread {
            installCachedThread(
                fetchedCachedThread,
                userID: userID,
                threadID: threadID,
                labelMutationGenerationAtReadStart: labelMutationGenerationAtReadStart,
                writeToMemoryCache: concurrentlyInstalledThread == nil && memoryCachedThread == nil
            )
            if silent {
                return .cached(fetchedCachedThread)
            }
        }
        if let replacingInFlightRequest {
            replacingInFlightRequest.isAccepted = false
            if inFlightThreadRequestRecords[threadID] === replacingInFlightRequest {
                inFlightThreads[threadID]?.cancel()
                inFlightThreads[threadID] = nil
                inFlightThreadRequestRecords[threadID] = nil
                inFlightThreadLabelMutationGenerations[threadID] = nil
            }
        }
        if let inFlight = inFlightThreads[threadID],
           let requestRecord = inFlightThreadRequestRecords[threadID] {
            let inFlightLabelMutationGeneration =
                inFlightThreadLabelMutationGenerations[threadID] ?? labelMutationGenerationAtReadStart
            do {
                let fetchedThread = try await inFlight.value
                guard operationGeneration == accountOperationGeneration,
                      session?.user.id == userID,
                      fetchedThread.userID == userID,
                      requestRecord.isAccepted else {
                    return .failed
                }
                let thread = preservingReaderLabelMutations(
                    in: fetchedThread,
                    threadID: threadID,
                    since: inFlightLabelMutationGeneration
                )
                openedThreads[threadID] = thread
                updateReader(threadID: threadID, thread: thread, error: nil)
                return .joined(request: requestRecord, thread: thread)
            } catch {
                guard operationGeneration == accountOperationGeneration else {
                    return .failed
                }
                if !silent {
                    recordThreadError(error.localizedDescription, threadID: threadID)
                }
                if let cached = openedThreads[threadID] {
                    scheduleBodyRefreshIfNeeded(threadID: threadID, thread: cached)
                }
                return .failed
            }
        }

        let inFlightLabelMutationGeneration = readerLabelMutationGenerations[threadID] ?? 0
        let requestRecord = ThreadPrefetchRequestRecord()
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
            var contentRevision = firstPage.contentRevision
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
                contentRevision = page.contentRevision ?? contentRevision
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
                messages: messages,
                contentRevision: contentRevision
            )
        }
        inFlightThreads[threadID] = task
        inFlightThreadRequestRecords[threadID] = requestRecord
        inFlightThreadLabelMutationGenerations[threadID] = inFlightLabelMutationGeneration
        do {
            let fetchedThread = try await task.value
            if inFlightThreadRequestRecords[threadID] === requestRecord {
                inFlightThreads[threadID] = nil
                inFlightThreadRequestRecords[threadID] = nil
                inFlightThreadLabelMutationGenerations[threadID] = nil
            }
            guard operationGeneration == accountOperationGeneration,
                  session?.user.id == userID,
                  requestRecord.isAccepted else {
                return .failed
            }
            let thread = preservingReaderLabelMutations(
                in: fetchedThread,
                threadID: threadID,
                since: inFlightLabelMutationGeneration
            )
            openedThreads[threadID] = thread
            threadCache.write(thread, userID: userID, threadID: threadID)
            threadErrors[threadID] = nil
            updateReader(threadID: threadID, thread: thread, error: nil)
            await persistLocalThread(
                thread,
                userID: userID,
                threadID: threadID,
                operationGeneration: operationGeneration
            )
            guard operationGeneration == accountOperationGeneration,
                  session?.user.id == userID,
                  requestRecord.isAccepted else {
                return .failed
            }
            if startPendingHydrationRefreshAfterCurrentRequest(
                threadID: threadID,
                threadNeedsRefresh: thread.needsReaderBodyRefresh
            ) {
                return .started(request: requestRecord, thread: thread)
            }
            scheduleBodyRefreshIfNeeded(threadID: threadID, thread: thread)
            return .started(request: requestRecord, thread: thread)
        } catch {
            guard operationGeneration == accountOperationGeneration else {
                return .failed
            }
            if inFlightThreadRequestRecords[threadID] === requestRecord {
                inFlightThreads[threadID] = nil
                inFlightThreadRequestRecords[threadID] = nil
                inFlightThreadLabelMutationGenerations[threadID] = nil
            }
            if cachedThread == nil, !silent {
                recordThreadError(error.localizedDescription, threadID: threadID)
            }
            if startPendingHydrationRefreshAfterCurrentRequest(
                threadID: threadID,
                threadNeedsRefresh: openedThreads[threadID]?.needsReaderBodyRefresh ?? true
            ) {
                return .failed
            }
            if let cached = openedThreads[threadID] {
                scheduleBodyRefreshIfNeeded(threadID: threadID, thread: cached)
            }
            return .failed
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
            if await refreshVisibleSearchIfAwaitingCompletion() {
                return
            }
            guard let revision = state.mailboxRevision, !revision.isEmpty else {
                return
            }
            guard lastSeenMailboxRevision != revision else {
                return
            }
            await refreshActiveMailbox(allowCachedFallback: true, force: true)
        } catch {
            if transitionToReauthenticationIfNeeded(for: error) {
                return
            }
            lastForcedMailboxRefreshError = error.localizedDescription
        }
    }

    private func refreshSetupSyncState() async {
        guard client.mode == .localBackend, hasSessionToken else {
            return
        }
        do {
            let state = try await client.mailboxSyncState()
            guard hasSessionToken else {
                return
            }
            syncState = state
            if !state.connected {
                transitionToReauthentication()
            }
        } catch is CancellationError {
            return
        } catch {
            // Setup can still advance from app-session and verified mailbox
            // snapshots. A progress-only request must not discard those rows.
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
            if let userID = session?.user.id, !envelope.hydratedThreads.isEmpty {
                Task { [client] in
                    await client.observeHydratedThreads(envelope.hydratedThreads, userID: userID)
                }
            }
            if let readerThreadID,
               envelope.threadIDs.contains(readerThreadID) {
                cancelBodyRefresh(for: readerThreadID)
                pendingHydrationRefreshThreadIDs.insert(readerThreadID)
                startPendingHydrationRefreshIfPossible(threadID: readerThreadID)
            }
            // Row body_ready/content_revision observations also changed, even
            // when none of the hydrated threads is currently open.
            scheduleEventRefresh(needsSession: false)
        case "mailbox-sync-progress":
            // Progress lives on app-session/readiness as well as mailbox
            // responses, so refresh both through the coalesced event path.
            scheduleEventRefresh(needsSession: true)
        case "mailbox-changed":
            let envelope = decodedEventEnvelope(event.data)
            guard eventAffectsActiveMailbox(envelope) else {
                return
            }
            if visibleSearchAwaitsCompletion
                || shouldRefreshForRevision(envelope.mailboxRevision) {
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
            if visibleSearchAwaitsCompletion
                || shouldRefreshForRevision(state.mailboxRevision) {
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
              eventSearchKey == Self.searchKey(label: label, query: query),
              let userID = session?.user.id else {
            return
        }

        searchHydrationRefreshTask?.cancel()
        cancelActiveMailboxRefresh()
        let paginationKey = MailboxPaginationKey(label: label, searchQuery: query)
        resetMailboxPagination(for: paginationKey)
        let requestID = UUID()
        let pageContext = MailboxPageContext(
            generation: mailboxPageGeneration(for: paginationKey),
            label: label,
            searchQuery: query,
            userID: userID
        )
        searchRequestID = requestID
        searchInProgress = true
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
                let completed = try await self.completeMailboxSnapshot(
                    startingWith: response,
                    context: pageContext
                )
                guard self.searchRequestID == requestID,
                      self.activeMailboxLabel == label,
                      self.searchQuery == query,
                      self.matchesMailboxPageContext(pageContext) else {
                    return
                }
                self.searchResults = completed
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
            self.finishSearchRequest(id: requestID)
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
        }
        if await refreshVisibleSearchIfAwaitingCompletion() {
            return
        }
        guard !needsSession else {
            return
        }
        await refreshActiveMailbox(allowCachedFallback: true, force: true)
    }

    private var visibleSearchAwaitsCompletion: Bool {
        guard isSearchActive else {
            return false
        }
        if searchInProgress {
            return true
        }
        guard let searchResults, searchError == nil else {
            return false
        }
        return !Self.isCompleteMailboxSnapshot(searchResults)
    }

    @discardableResult
    private func refreshVisibleSearchIfAwaitingCompletion() async -> Bool {
        guard isSearchActive else {
            return false
        }
        if searchInProgress {
            pendingSearchCompletionRefresh = true
            return true
        }
        guard let searchResults,
              searchError == nil,
              !Self.isCompleteMailboxSnapshot(searchResults) else {
            pendingSearchCompletionRefresh = false
            return false
        }
        pendingSearchCompletionRefresh = false
        let query = searchQuery
        await searchMailbox(query)
        return true
    }

    private func finishSearchRequest(id requestID: UUID) {
        guard searchRequestID == requestID else {
            return
        }
        searchRequestID = nil
        searchInProgress = false
        guard pendingSearchCompletionRefresh else {
            return
        }
        pendingSearchCompletionRefresh = false
        guard isSearchActive,
              let searchResults,
              searchError == nil,
              !Self.isCompleteMailboxSnapshot(searchResults) else {
            return
        }
        scheduleEventRefresh(needsSession: false)
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
        let hydratedThreads: [MailboxHydratedThreadState]
        if let threadsJSON = payload["threads"],
           JSONSerialization.isValidJSONObject(threadsJSON),
           let threadsData = try? JSONSerialization.data(withJSONObject: threadsJSON),
           let decoded = try? JSONDecoder.backend.decode([MailboxHydratedThreadState].self, from: threadsData) {
            hydratedThreads = decoded
        } else {
            hydratedThreads = []
        }
        return MailboxEventEnvelope(
            mailboxRevision: revision,
            mailboxLabels: labels ?? (mailboxLabel.map { [$0] } ?? []),
            searchKey: searchKey,
            threadIDs: threadIDs ?? hydratedThreads.map(\.threadID),
            hydratedThreads: hydratedThreads
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
            fullImportCompleted: mailbox.fullImportCompleted,
            syncGeneration: mailbox.syncGeneration,
            phase: mailbox.phase,
            initialTargetCount: mailbox.initialTargetCount,
            initialMetadataCount: mailbox.initialMetadataCount,
            initialBodyTargetCount: mailbox.initialBodyTargetCount,
            initialBodyReadyCount: mailbox.initialBodyReadyCount,
            historyMetadataCount: mailbox.historyMetadataCount,
            historyBodyReadyCount: mailbox.historyBodyReadyCount,
            estimatedTotalCount: mailbox.estimatedTotalCount,
            initialWindowComplete: mailbox.initialWindowComplete,
            historyMetadataComplete: mailbox.historyMetadataComplete,
            historyBodyComplete: mailbox.historyBodyComplete,
            lastProgressAt: mailbox.lastProgressAt
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
        if readerThreadID == threadID {
            closeReader()
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
            fullImportCompleted: mailbox.fullImportCompleted,
            syncGeneration: mailbox.syncGeneration,
            phase: mailbox.phase,
            initialTargetCount: mailbox.initialTargetCount,
            initialMetadataCount: mailbox.initialMetadataCount,
            initialBodyTargetCount: mailbox.initialBodyTargetCount,
            initialBodyReadyCount: mailbox.initialBodyReadyCount,
            historyMetadataCount: mailbox.historyMetadataCount,
            historyBodyReadyCount: mailbox.historyBodyReadyCount,
            estimatedTotalCount: mailbox.estimatedTotalCount,
            initialWindowComplete: mailbox.initialWindowComplete,
            historyMetadataComplete: mailbox.historyMetadataComplete,
            historyBodyComplete: mailbox.historyBodyComplete,
            lastProgressAt: mailbox.lastProgressAt
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

    private func mailboxReentryLabels(for action: GmailThreadAction) -> [MailboxLabel] {
        switch action {
        case .archive:
            return [.archive]
        case .unarchive:
            return [.inbox]
        case .restoreTrash, .notSpam:
            return [.inbox, .all]
        case .moveTrash:
            return [.trash]
        case .markSpam:
            return [.spam]
        case .star:
            return [.starred]
        case .unstar, .markRead, .markUnread, .deleteForever:
            return []
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

    /// Retires the currently visible account before an OfflineFirstAppClient
    /// destructive operation is allowed to remove SQLite rows or the account
    /// encryption key. The actor call is a drain barrier for writes already
    /// submitted to the worker; the epoch fence rejects old callers that have
    /// not reached the worker yet.
    private func prepareLocalMailStoreForAccountPurge() async {
        let retiringWriteGeneration = accountOperationGeneration
        localMailStoreWritesBlocked = true
        invalidateAccountScopedOperations()
        await localMailStoreWorker.invalidateWritesAndDrain(
            through: retiringWriteGeneration
        )
    }

    private func persistLocalSession(
        _ session: AppSessionResponse,
        operationGeneration: UInt
    ) async {
        guard !localMailStoreWritesBlocked else {
            return
        }
        await localMailStoreWorker.writeSession(
            session,
            operationGeneration: operationGeneration
        )
    }

    private func persistLocalMailbox(
        _ mailbox: MailboxResponse,
        userID: String,
        label: MailboxLabel,
        operationGeneration: UInt
    ) async {
        guard !localMailStoreWritesBlocked else {
            return
        }
        await localMailStoreWorker.writeMailbox(
            mailbox,
            userID: userID,
            label: label,
            operationGeneration: operationGeneration
        )
    }

    private func persistLocalMailbox(
        _ mailbox: MailboxResponse,
        userID: String,
        label: MailboxLabel,
        removingThreadIDs: [String],
        reenteringThreadIDs: [String],
        reentryLabels: [MailboxLabel],
        operationGeneration: UInt
    ) async {
        guard !localMailStoreWritesBlocked else {
            return
        }
        await localMailStoreWorker.writeMailbox(
            mailbox,
            userID: userID,
            label: label,
            removingThreadIDs: removingThreadIDs,
            reenteringThreadIDs: reenteringThreadIDs,
            reentryLabels: reentryLabels,
            operationGeneration: operationGeneration
        )
    }

    private func persistLocalThread(
        _ thread: ThreadReaderResponse,
        userID: String,
        threadID: String,
        operationGeneration: UInt
    ) async {
        guard !localMailStoreWritesBlocked else {
            return
        }
        await localMailStoreWorker.writeThread(
            thread,
            userID: userID,
            threadID: threadID,
            operationGeneration: operationGeneration
        )
    }

    private func invalidateAccountScopedOperations() {
        accountOperationGeneration &+= 1
        pendingConversationExpansions = [:]
        authoritativeConversationContent = [:]
        for authorization in attachmentOpenAuthorizations.values {
            authorization.invalidate()
        }
        attachmentOpenAuthorizations = [:]
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
        for record in inFlightThreadRequestRecords.values {
            record.isAccepted = false
        }
        inFlightThreads = [:]
        inFlightThreadRequestRecords = [:]
        inFlightThreadLabelMutationGenerations = [:]
        selectionPrefetchTask?.cancel()
        selectionPrefetchTask = nil
        priorityPrefetchTask?.cancel()
        priorityPrefetchTask = nil
        eventRefreshTask?.cancel()
        eventRefreshTask = nil
        postSendRefreshTask?.cancel()
        postSendRefreshTask = nil
        readinessRefreshOwnerID = nil
        readinessRefreshTask?.cancel()
        readinessRefreshTask = nil
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
        pendingSearchCompletionRefresh = false
        cancelFolderCountRefresh()
        lastCompletedFolderCountsRefreshAt = nil
        resetAllMailboxPagination()
    }

    private func mailboxPageGeneration(for key: MailboxPaginationKey) -> UInt {
        mailboxPageGenerations[key, default: 0]
    }

    private func resetMailboxPagination(for key: MailboxPaginationKey) {
        mailboxPageGenerations[key] = mailboxPageGeneration(for: key) &+ 1
        inFlightMailboxPages[key]?.cancel()
        inFlightMailboxPages[key] = nil
        mailboxPageRequestIDs[key] = nil
        updateVisibleMailboxPageLoading()
    }

    private func resetSearchMailboxPagination() {
        let keys = Set(inFlightMailboxPages.keys)
            .union(mailboxPageGenerations.keys)
            .filter { $0.searchQuery != nil }
        for key in keys {
            resetMailboxPagination(for: key)
        }
    }

    private func resetAllMailboxPagination() {
        let keys = Set(inFlightMailboxPages.keys).union(mailboxPageGenerations.keys)
        for key in keys {
            resetMailboxPagination(for: key)
        }
        mailboxPageLoading = false
    }

    private func updateVisibleMailboxPageLoading() {
        let key = MailboxPaginationKey(
            label: activeMailboxLabel,
            searchQuery: searchQuery.isEmpty ? nil : searchQuery
        )
        mailboxPageLoading = inFlightMailboxPages[key] != nil
    }

    private func paginationContextIsCurrent(_ context: MailboxPageContext) -> Bool {
        context.generation == mailboxPageGeneration(for: context.paginationKey)
            && context.userID == session?.user.id
    }

    private func matchesMailboxPageContext(_ context: MailboxPageContext) -> Bool {
        let searchMatches = context.searchQuery.map { isSearchActive && searchQuery == $0 } ?? !isSearchActive
        return paginationContextIsCurrent(context)
            && context.label == activeMailboxLabel
            && searchMatches
    }

    private static func isCompleteMailboxSnapshot(_ mailbox: MailboxResponse) -> Bool {
        guard mailbox.fullImportRunning != true,
              mailbox.fullImportCompleted != false,
              mailbox.nextCursor?.isEmpty != false else {
            return false
        }
        let visibleRows = mailbox.sections.reduce(0) { $0 + $1.rows.count }
        let loadedThreads = max(mailbox.loadedThreads ?? visibleRows, visibleRows)
        return loadedThreads >= mailbox.totalThreads && visibleRows >= mailbox.totalThreads
    }

    private static func isConfirmedEmptyMailbox(
        _ mailbox: MailboxResponse,
        progress: MailboxSyncProgress? = nil
    ) -> Bool {
        guard mailbox.totalThreads == 0,
              mailbox.sections.allSatisfy({ $0.rows.isEmpty }),
              mailbox.nextCursor?.isEmpty != false else {
            return false
        }
        guard mailbox.loadedThreads == nil || mailbox.loadedThreads == 0 else {
            return false
        }
        let completeLegacySnapshot = mailbox.fullImportRunning != true
            && mailbox.fullImportCompleted != false
        let explicitlyConfirmedInitialWindow = progress?.initialWindowComplete == true
            && max(0, progress?.initialTargetCount ?? 0) == 0
            && max(0, progress?.initialMetadataCount ?? 0) == 0
            && max(0, progress?.estimatedTotalCount ?? 0) == 0
        return completeLegacySnapshot || explicitlyConfirmedInitialWindow
    }

    private static func isVerifiedMailboxSnapshot(
        _ mailbox: MailboxResponse,
        progress: MailboxSyncProgress
    ) -> Bool {
        let rows = mailbox.sections.flatMap(\.rows)
        let rowIDs = rows.map(\.threadID)
        guard Set(rowIDs).count == rowIDs.count else {
            return false
        }
        if isConfirmedEmptyMailbox(mailbox, progress: progress) {
            return true
        }
        if isCompleteMailboxSnapshot(mailbox) {
            return true
        }

        let estimatedTotal = max(0, max(progress.estimatedTotalCount ?? 0, mailbox.totalThreads))
        let initialTarget = max(0, progress.initialTargetCount ?? min(100, estimatedTotal))
        let committedBatchTarget = min(25, initialTarget)
        guard committedBatchTarget > 0 else {
            return false
        }
        return max(0, progress.initialMetadataCount ?? 0) >= committedBatchTarget
    }

    private static func validateCompletedMailboxSnapshot(_ mailbox: MailboxResponse) throws {
        guard mailbox.fullImportRunning != true,
              mailbox.fullImportCompleted != false else {
            return
        }
        guard isCompleteMailboxSnapshot(mailbox) else {
            throw MailboxPaginationError.incompleteSnapshot
        }
    }

    private static func mailboxRevisionsMatch(_ current: MailboxResponse, _ page: MailboxResponse) -> Bool {
        let currentRevision = current.mailboxRevision?.isEmpty == false ? current.mailboxRevision : nil
        let pageRevision = page.mailboxRevision?.isEmpty == false ? page.mailboxRevision : nil
        switch (currentRevision, pageRevision) {
        case (nil, nil):
            return true
        case let (.some(currentRevision), .some(pageRevision)):
            return currentRevision == pageRevision
        default:
            return false
        }
    }

    private static func mailboxPaginationIdentityMatches(
        _ current: MailboxResponse,
        _ page: MailboxResponse
    ) -> Bool {
        let currentGeneration = current.syncGeneration?.isEmpty == false ? current.syncGeneration : nil
        let pageGeneration = page.syncGeneration?.isEmpty == false ? page.syncGeneration : nil
        switch (currentGeneration, pageGeneration) {
        case let (.some(currentGeneration), .some(pageGeneration)):
            // A sync generation owns a stable-ID/keyset cursor chain. Counts,
            // revisions, and progress may all advance while that import runs.
            return currentGeneration == pageGeneration
        case (nil, nil):
            // Legacy servers do not expose a generation fence, so retain the
            // stricter revision contract for their cursor chains.
            return current.totalThreads == page.totalThreads
                && mailboxRevisionsMatch(current, page)
        default:
            return false
        }
    }

    private static func mailboxRevisionChangedWithinGeneration(
        _ current: MailboxResponse,
        _ incoming: MailboxResponse
    ) -> Bool {
        guard current.label == incoming.label,
              normalizedMailboxGeneration(current.syncGeneration)
                == normalizedMailboxGeneration(incoming.syncGeneration),
              let currentRevision = current.mailboxRevision?.trimmingCharacters(in: .whitespacesAndNewlines),
              !currentRevision.isEmpty,
              let incomingRevision = incoming.mailboxRevision?.trimmingCharacters(in: .whitespacesAndNewlines),
              !incomingRevision.isEmpty else {
            return false
        }
        return currentRevision != incomingRevision
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

    private var preferredMailboxSyncProgress: MailboxSyncProgress {
        mailboxSyncProgress(for: activeMailbox)
    }

    private func mailboxSyncProgress(for mailbox: MailboxResponse?) -> MailboxSyncProgress {
        MailboxSyncProgress.newestMerged([
            session?.readiness.mailboxSyncProgress,
            session?.sync.mailboxSyncProgress,
            syncState?.mailboxSyncProgress,
            mailbox?.mailboxSyncProgress,
        ].compactMap { $0 })
    }

    private func visibleRows(in section: GmailThreadSection) -> [GmailThreadRow] {
        if activeMailboxLabel == .all || activeMailboxLabel == .archive {
            return section.rows
        }
        return section.rows.filter { $0.isVisible(in: activeMailboxLabel) }
    }

    private func visibleMailboxRow(threadID: String) -> GmailThreadRow? {
        guard let mailbox = visibleMailbox else {
            return nil
        }
        for section in mailbox.sections {
            if let row = visibleRows(in: section).first(where: { $0.threadID == threadID }) {
                return row
            }
        }
        return nil
    }

    private func reconcileConversationExpansionState(with mailbox: MailboxResponse?) {
        guard !pendingConversationExpansions.isEmpty
                || !authoritativeConversationContent.isEmpty
                || !expandedThreadIDs.isEmpty else {
            return
        }
        let trackedThreadIDs = Set(pendingConversationExpansions.keys)
            .union(authoritativeConversationContent.keys)
            .union(expandedThreadIDs)
        var rowsByThreadID: [String: GmailThreadRow] = [:]
        rowsByThreadID.reserveCapacity(trackedThreadIDs.count)
        var remainingThreadIDs = trackedThreadIDs
        if let mailbox {
            for section in mailbox.sections {
                for row in section.rows where remainingThreadIDs.contains(row.threadID) {
                    guard activeMailboxLabel == .all
                            || activeMailboxLabel == .archive
                            || row.isVisible(in: activeMailboxLabel) else {
                        continue
                    }
                    rowsByThreadID[row.threadID] = row
                    remainingThreadIDs.remove(row.threadID)
                }
                if remainingThreadIDs.isEmpty {
                    break
                }
            }
        }

        pendingConversationExpansions = pendingConversationExpansions.filter { threadID, context in
            guard let row = rowsByThreadID[threadID] else {
                return false
            }
            return ConversationMailboxRowIdentity(row: row) == context.mailboxRowIdentity
        }
        authoritativeConversationContent = authoritativeConversationContent.filter {
            threadID,
            authoritative in
            guard let row = rowsByThreadID[threadID] else {
                return false
            }
            return ConversationMailboxRowIdentity(row: row) == authoritative.mailboxRowIdentity
        }

        let reconciledExpandedThreadIDs = expandedThreadIDs.filter { threadID in
            guard let row = rowsByThreadID[threadID],
                  let children = verifiedConversationChildren(for: row) else {
                return false
            }
            return children.count >= 2
        }
        let removedExpandedThreadIDs = expandedThreadIDs.subtracting(reconciledExpandedThreadIDs)
        if !removedExpandedThreadIDs.isEmpty {
            if let selectedThreadID,
               removedExpandedThreadIDs.contains(selectedThreadID),
               selectedMessageID != nil {
                selectedMessageID = nil
                activeMessageID = nil
            }
            expandedThreadIDs = reconciledExpandedThreadIDs
        }
    }

    /// A mailbox page may carry only the latest lightweight child while its
    /// aggregate `message_count` describes the full Gmail conversation. Never
    /// mix that sparse projection with reader data: expand from one verified,
    /// complete source or keep the parent collapsed until it is available.
    private func verifiedConversationChildren(for row: GmailThreadRow) -> [GmailThreadChildRow]? {
        if let thread = openedThreads[row.threadID] {
            if let authoritative = authoritativeConversationContent[row.threadID],
               authoritative == AuthoritativeConversationContent(
                    mailboxRowIdentity: ConversationMailboxRowIdentity(row: row),
                    threadIdentity: ConversationContentIdentity(thread: thread)
                ),
               let children = completeAuthoritativeConversationChildren(from: thread) {
                return children
            }
            if conversationThreadCacheMatchesMailboxRow(thread, row: row),
               let children = completeConversationChildren(
                   from: thread,
                   expectedCount: row.messageCount
               ) {
                return children
            }
        }

        let children = Self.orderedUniqueChildren(row.childRows)
        guard children.count >= max(2, row.messageCount) else {
            return nil
        }
        return children
    }

    private func conversationThreadCacheMatchesMailboxRow(
        _ thread: ThreadReaderResponse,
        row: GmailThreadRow
    ) -> Bool {
        if let rowRevision = ConversationMailboxRowIdentity(row: row).contentRevision {
            let threadRevision = thread.contentRevision?
                .trimmingCharacters(in: .whitespacesAndNewlines)
            return threadRevision == rowRevision
        }
        return ConversationContentIdentity(thread: thread).messageIDs.contains(row.latestSourceRecordID)
    }

    private func completeConversationChildren(
        from thread: ThreadReaderResponse,
        expectedCount: Int
    ) -> [GmailThreadChildRow]? {
        guard !thread.hasMore else {
            return nil
        }
        let messages = Self.orderedUniqueMessages(thread.messages)
        let declaredCount = max(thread.totalMessages, messages.count)
        guard messages.count >= max(2, max(expectedCount, declaredCount)) else {
            return nil
        }
        return messages.map { message in
            conversationChildRow(message: message, thread: thread)
        }
    }

    private func completeAuthoritativeConversationChildren(
        from thread: ThreadReaderResponse
    ) -> [GmailThreadChildRow]? {
        guard !thread.hasMore else {
            return nil
        }
        let messages = Self.orderedUniqueMessages(thread.messages)
        guard messages.count >= 2,
              messages.count >= thread.totalMessages else {
            return nil
        }
        return messages.map { message in
            conversationChildRow(message: message, thread: thread)
        }
    }

    private func conversationChildRow(
        message: ThreadMessage,
        thread: ThreadReaderResponse
    ) -> GmailThreadChildRow {
        let sender = activeMailboxLabel == .sent
            ? (message.to ?? message.fromAddress)
            : message.fromAddress
        return GmailThreadChildRow(
            messageID: message.id,
            gmailThreadID: message.threadID ?? thread.gmailThreadID,
            sender: sender,
            subject: message.subject,
            snippet: message.snippet,
            receivedAt: message.receivedAt,
            labelIDs: message.labelIDs,
            labels: message.labelIDs,
            unread: message.labelIDs.contains { $0.caseInsensitiveCompare("UNREAD") == .orderedSame }
        )
    }

    private static func orderedUniqueMessages(_ messages: [ThreadMessage]) -> [ThreadMessage] {
        var seen: Set<String> = []
        let unique = messages.filter { seen.insert($0.id).inserted }
        return unique.sorted { lhs, rhs in
            chronologicalOrder(
                lhsReceivedAt: lhs.receivedAt,
                lhsID: lhs.id,
                rhsReceivedAt: rhs.receivedAt,
                rhsID: rhs.id
            )
        }
    }

    private static func orderedUniqueChildren(_ children: [GmailThreadChildRow]) -> [GmailThreadChildRow] {
        var seen: Set<String> = []
        let unique = children.filter { seen.insert($0.messageID).inserted }
        return unique.sorted { lhs, rhs in
            chronologicalOrder(
                lhsReceivedAt: lhs.receivedAt,
                lhsID: lhs.messageID,
                rhsReceivedAt: rhs.receivedAt,
                rhsID: rhs.messageID
            )
        }
    }

    private static func chronologicalOrder(
        lhsReceivedAt: String,
        lhsID: String,
        rhsReceivedAt: String,
        rhsID: String
    ) -> Bool {
        let lhsDate = ISO8601DateFormatter.shared.date(from: lhsReceivedAt)
        let rhsDate = ISO8601DateFormatter.shared.date(from: rhsReceivedAt)
        if let lhsDate, let rhsDate, lhsDate != rhsDate {
            return lhsDate < rhsDate
        }
        if lhsReceivedAt != rhsReceivedAt {
            return lhsReceivedAt < rhsReceivedAt
        }
        return lhsID < rhsID
    }

    private func invalidateInboxSections() {
        inboxSectionsRevision &+= 1
        inboxSectionsCache = nil
    }

    private func refreshPendingLocalActionCount() async {
        let operationGeneration = accountOperationGeneration
        guard let userID = session?.user.id else {
            if pendingLocalActionCount != 0 {
                pendingLocalActionCount = 0
            }
            return
        }
        let nextCount = await localMailStoreWorker.pendingThreadActionCount(userID: userID)
        guard operationGeneration == accountOperationGeneration,
              session?.user.id == userID else {
            return
        }
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
            },
            contentRevision: contentRevision
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
