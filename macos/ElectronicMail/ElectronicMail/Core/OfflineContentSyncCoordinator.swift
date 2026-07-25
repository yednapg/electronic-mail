import Foundation

public extension Notification.Name {
    static let offlineContentSyncStorageWarning = Notification.Name("ElectronicMail.offlineContentSyncStorageWarning")
}

enum OfflineContentPriorityPolicy {
    static func priority(
        bodyReady: Bool,
        initialWindowPosition: Int?,
        selected: Bool = false
    ) -> Int? {
        if selected { return 100 }
        guard bodyReady else { return nil }
        return initialWindowPosition.map { 0..<25 ~= $0 } == true ? 90 : 10
    }
}

/// Keeps complete conversation snapshots on disk without delaying mailbox rows.
///
/// Mailbox pages are observations, not work ownership: duplicate pages, SSE
/// invalidations, and a relaunch can all enqueue the same thread safely. Work is
/// coalesced by thread ID and at most two 20-thread requests run concurrently.
public actor OfflineContentSyncCoordinator {
    private struct MetadataObservation: Equatable {
        let syncGeneration: String?
        let metadataCount: Int
        let historyMetadataComplete: Bool?

        init?(mailbox: MailboxResponse) {
            let hasProgressContract = mailbox.syncGeneration != nil
                || mailbox.phase != nil
                || mailbox.initialMetadataCount != nil
                || mailbox.historyMetadataCount != nil
                || mailbox.estimatedTotalCount != nil
                || mailbox.historyMetadataComplete != nil
            guard hasProgressContract else { return nil }
            syncGeneration = mailbox.syncGeneration
            metadataCount = max(mailbox.initialMetadataCount ?? 0, mailbox.historyMetadataCount ?? 0)
            historyMetadataComplete = mailbox.historyMetadataComplete
        }

        init?(state: MailboxSyncStateResponse) {
            let hasProgressContract = state.syncGeneration != nil
                || state.phase != nil
                || state.initialMetadataCount != nil
                || state.historyMetadataCount != nil
                || state.estimatedTotalCount != nil
                || state.historyMetadataComplete != nil
            guard hasProgressContract else { return nil }
            syncGeneration = state.syncGeneration
            metadataCount = max(state.initialMetadataCount ?? 0, state.historyMetadataCount ?? 0)
            historyMetadataComplete = state.historyMetadataComplete
        }
    }

    private struct MetadataCrawlKey: Hashable {
        let userID: String
        let label: MailboxLabel
    }

    private struct MetadataCrawlOwner: Equatable {
        let id: UUID
        let syncGeneration: String?
    }

    private struct MetadataCrawlState {
        var syncGeneration: String?
        var nextCursor: String?
        var lastPageCursor: String?
        var seenCursors: Set<String> = []
        var isComplete = false
        var completedMetadataCount = 0
    }

    private struct WorkItem: Equatable {
        let threadID: String
        let contentRevision: String?
        let priority: Int
        var retryCount: Int
        var pendingResponseCount: Int
    }

    private struct BatchResult {
        let requested: [WorkItem]
        let result: Result<MailboxThreadBatchResponse, Error>
    }

    private struct PaginatedResult {
        let requested: WorkItem
        let result: Result<ThreadReaderResponse, Error>
    }

    private enum PaginatedSnapshotError: Error {
        case incomplete
        case identityChanged
        case revisionChanged
        case repeatedPage
    }

    private let backend: AppClient
    private let localMailStore: LocalMailStore
    private let attachmentPrefetchCoordinator: AttachmentPrefetchCoordinator?
    private var pendingByUser: [String: [String: WorkItem]] = [:]
    private var pausedUsers: Set<String> = []
    private var drainTask: Task<Void, Never>?
    private var drainOwnerID: UUID?
    private var currentUserID: String?
    private var currentAccountEpoch: UInt64?
    private var invalidatedThroughAccountEpoch: UInt64 = 0
    private var workGenerations: [String: UInt] = [:]
    private var latestMetadataObservations: [String: MetadataObservation] = [:]
    private var metadataCrawlStates: [MetadataCrawlKey: MetadataCrawlState] = [:]
    private var metadataCrawlOwners: [MetadataCrawlKey: MetadataCrawlOwner] = [:]
    private var metadataCrawlTasks: [MetadataCrawlKey: Task<Void, Never>] = [:]
    private let globalMetadataLabels: [MailboxLabel] = [.all, .spam, .trash]
    private let pendingResponsesBeforePaginatedFallback: Int
    private let retryDelayOverride: Duration?

    private static let paginatedThreadPageSize = 100
    private static let maximumPaginatedThreadPages = 10_000

    init(
        backend: AppClient,
        localMailStore: LocalMailStore,
        attachmentPrefetchCoordinator: AttachmentPrefetchCoordinator? = nil,
        pendingResponsesBeforePaginatedFallback: Int = 2,
        retryDelayOverride: Duration? = nil
    ) {
        self.backend = backend
        self.localMailStore = localMailStore
        self.attachmentPrefetchCoordinator = attachmentPrefetchCoordinator
        self.pendingResponsesBeforePaginatedFallback = max(1, pendingResponsesBeforePaginatedFallback)
        self.retryDelayOverride = retryDelayOverride
    }

    deinit {
        drainTask?.cancel()
        metadataCrawlTasks.values.forEach { $0.cancel() }
    }

    public func observe(
        mailbox: MailboxResponse,
        userID: String,
        isInitialWindow: Bool = false,
        accountEpoch: UInt64? = nil
    ) {
        guard acceptObservation(userID: userID, accountEpoch: accountEpoch) else { return }
        guard !pausedUsers.contains(userID) else { return }

        enqueueRows(
            mailbox.sections.flatMap(\.rows),
            userID: userID
        )
        if mailbox.label != .all, let observation = MetadataObservation(mailbox: mailbox) {
            resumeGlobalMetadataCrawls(userID: userID, observation: observation)
        }
    }

    private func enqueueRows(
        _ rows: [GmailThreadRow],
        userID: String
    ) {
        var userPending = pendingByUser[userID] ?? [:]
        for row in rows {
            // The backend batch endpoint is a reader-priority request. Cold
            // history must stay on the server's slow queue until progress marks
            // its body ready; only an explicit reader selection bypasses this.
            guard row.bodyReady == true else { continue }
            // This rank is assigned by the server's canonical global initial
            // window. Folder position (including Inbox order) is never used as
            // a proxy for newest-25 attachment eligibility.
            guard let priority = OfflineContentPriorityPolicy.priority(
                bodyReady: true,
                initialWindowPosition: row.initialWindowPosition
            ) else {
                continue
            }
            let cached = localMailStore.readThread(userID: userID, threadID: row.threadID)
            let cachedIsComplete = cached?.messages.allSatisfy(\.bodyComplete) == true && cached?.hasMore != true
            let sameRevision = row.contentRevision == nil || cached?.contentRevision == row.contentRevision
            if cachedIsComplete && sameRevision {
                prefetchInitialAttachmentsIfNeeded(cached, priority: priority, userID: userID)
                continue
            }
            let next = WorkItem(
                threadID: row.threadID,
                contentRevision: row.contentRevision,
                priority: priority,
                retryCount: userPending[row.threadID]?.retryCount ?? 0,
                pendingResponseCount: userPending[row.threadID]?.pendingResponseCount ?? 0
            )
            if let existing = userPending[row.threadID], existing.priority > next.priority {
                continue
            }
            userPending[row.threadID] = next
        }
        pendingByUser[userID] = userPending
        scheduleDrain(userID: userID)
    }

    public func prioritize(
        threadID: String,
        contentRevision: String?,
        userID: String,
        accountEpoch: UInt64? = nil
    ) {
        guard acceptObservation(userID: userID, accountEpoch: accountEpoch) else { return }
        var userPending = pendingByUser[userID] ?? [:]
        userPending[threadID] = WorkItem(
            threadID: threadID,
            contentRevision: contentRevision,
            priority: OfflineContentPriorityPolicy.priority(
                bodyReady: false,
                initialWindowPosition: nil,
                selected: true
            ) ?? 100,
            retryCount: userPending[threadID]?.retryCount ?? 0,
            pendingResponseCount: userPending[threadID]?.pendingResponseCount ?? 0
        )
        pendingByUser[userID] = userPending
        scheduleDrain(userID: userID)
    }

    public func observe(
        progress state: MailboxSyncStateResponse,
        userID: String,
        accountEpoch: UInt64? = nil
    ) {
        guard acceptObservation(userID: userID, accountEpoch: accountEpoch) else { return }
        guard !pausedUsers.contains(userID), let observation = MetadataObservation(state: state) else {
            return
        }
        resumeGlobalMetadataCrawls(userID: userID, observation: observation)
    }

    public func observe(
        hydratedThreads: [MailboxHydratedThreadState],
        userID: String,
        accountEpoch: UInt64? = nil
    ) {
        guard acceptObservation(userID: userID, accountEpoch: accountEpoch) else { return }
        guard currentUserID == userID, !pausedUsers.contains(userID) else { return }
        var userPending = pendingByUser[userID] ?? [:]
        for state in hydratedThreads where state.bodyReady {
            guard let priority = OfflineContentPriorityPolicy.priority(
                bodyReady: state.bodyReady,
                initialWindowPosition: state.initialWindowPosition
            ) else {
                continue
            }
            let cached = localMailStore.readThread(userID: userID, threadID: state.threadID)
            let cachedIsComplete = cached?.messages.allSatisfy(\.bodyComplete) == true && cached?.hasMore != true
            let sameRevision = state.contentRevision == nil || cached?.contentRevision == state.contentRevision
            if cachedIsComplete && sameRevision {
                prefetchInitialAttachmentsIfNeeded(cached, priority: priority, userID: userID)
                continue
            }
            let next = WorkItem(
                threadID: state.threadID,
                contentRevision: state.contentRevision,
                priority: priority,
                retryCount: userPending[state.threadID]?.retryCount ?? 0,
                pendingResponseCount: userPending[state.threadID]?.pendingResponseCount ?? 0
            )
            if let existing = userPending[state.threadID], existing.priority > next.priority {
                continue
            }
            userPending[state.threadID] = next
        }
        pendingByUser[userID] = userPending
        scheduleDrain(userID: userID)
    }

    private func prefetchInitialAttachmentsIfNeeded(
        _ thread: ThreadReaderResponse?,
        priority: Int,
        userID: String
    ) {
        guard priority >= 90, let thread, let attachmentPrefetchCoordinator else { return }
        Task {
            await attachmentPrefetchCoordinator.enqueue(
                thread: thread,
                userID: userID,
                selected: false
            )
        }
    }

    public func reset(
        userID: String? = nil,
        invalidatingAccountEpoch: UInt64? = nil
    ) {
        if let invalidatingAccountEpoch {
            guard invalidatingAccountEpoch >= invalidatedThroughAccountEpoch else { return }
            invalidatedThroughAccountEpoch = invalidatingAccountEpoch
            if let currentAccountEpoch, currentAccountEpoch > invalidatingAccountEpoch {
                // A late cleanup from an older credential transition must not
                // cancel work already activated by a newer verified account.
                return
            }
            currentAccountEpoch = nil
        }
        if let userID {
            workGenerations[userID, default: 0] &+= 1
            pendingByUser[userID] = nil
            pausedUsers.remove(userID)
            latestMetadataObservations[userID] = nil
            cancelMetadataCrawls(userID: userID, discardState: true)
            if currentUserID == userID {
                drainTask?.cancel()
                drainTask = nil
                drainOwnerID = nil
                currentUserID = nil
            }
        } else {
            for userID in Set(workGenerations.keys).union(pendingByUser.keys) {
                workGenerations[userID, default: 0] &+= 1
            }
            pendingByUser = [:]
            pausedUsers = []
            latestMetadataObservations = [:]
            metadataCrawlTasks.values.forEach { $0.cancel() }
            metadataCrawlTasks = [:]
            metadataCrawlOwners = [:]
            metadataCrawlStates = [:]
            drainTask?.cancel()
            drainTask = nil
            drainOwnerID = nil
            currentUserID = nil
        }
    }

    private func acceptObservation(userID: String, accountEpoch: UInt64?) -> Bool {
        if let accountEpoch {
            guard accountEpoch >= invalidatedThroughAccountEpoch else { return false }
            if let currentAccountEpoch, accountEpoch < currentAccountEpoch {
                return false
            }
            if currentAccountEpoch != accountEpoch {
                currentAccountEpoch = accountEpoch
            }
        }
        if currentUserID != userID {
            if let previousUserID = currentUserID {
                cancelMetadataCrawls(userID: previousUserID)
            }
            currentUserID = userID
            drainTask?.cancel()
            drainTask = nil
            drainOwnerID = nil
        }
        return true
    }

    private func scheduleDrain(userID: String) {
        guard drainTask == nil, !pausedUsers.contains(userID) else { return }
        let ownerID = UUID()
        let generation = workGenerations[userID, default: 0]
        drainOwnerID = ownerID
        drainTask = Task { [weak self] in
            await self?.drain(userID: userID, generation: generation, ownerID: ownerID)
        }
    }

    private func resumeGlobalMetadataCrawls(
        userID: String,
        observation: MetadataObservation
    ) {
        latestMetadataObservations[userID] = observation
        guard currentUserID == userID, !pausedUsers.contains(userID) else { return }

        for label in globalMetadataLabels {
            resumeMetadataCrawl(
                key: MetadataCrawlKey(userID: userID, label: label),
                observation: observation
            )
        }
    }

    private func resumeMetadataCrawl(
        key: MetadataCrawlKey,
        observation: MetadataObservation
    ) {
        var state = metadataCrawlStates[key] ?? MetadataCrawlState(
            syncGeneration: observation.syncGeneration,
            nextCursor: nil,
            lastPageCursor: nil
        )
        let generationChanged = observation.syncGeneration.map { state.syncGeneration != $0 } == true
        if generationChanged {
            cancelMetadataCrawl(key: key, discardState: true)
            state = MetadataCrawlState(
                syncGeneration: observation.syncGeneration,
                nextCursor: nil,
                lastPageCursor: nil
            )
        } else if state.syncGeneration == nil {
            state.syncGeneration = observation.syncGeneration
        }
        guard metadataCrawlOwners[key] == nil else {
            metadataCrawlStates[key] = state
            return
        }
        if state.isComplete {
            guard observation.metadataCount > state.completedMetadataCount else {
                metadataCrawlStates[key] = state
                return
            }
            // Refetch only the prior tail page. Stable keyset cursors then walk
            // newly appended metadata without rescanning the whole generation.
            state.nextCursor = state.lastPageCursor
            state.seenCursors = []
            state.isComplete = false
        }
        metadataCrawlStates[key] = state

        let owner = MetadataCrawlOwner(id: UUID(), syncGeneration: observation.syncGeneration)
        metadataCrawlOwners[key] = owner
        metadataCrawlTasks[key] = Task { [weak self] in
            await self?.crawlMetadata(
                key: key,
                observation: observation,
                owner: owner
            )
        }
    }

    private func crawlMetadata(
        key: MetadataCrawlKey,
        observation: MetadataObservation,
        owner: MetadataCrawlOwner
    ) async {
        do {
            for _ in 0..<10_000 {
                try Task.checkCancellation()
                guard metadataCrawlOwners[key] == owner,
                      currentUserID == key.userID,
                      !pausedUsers.contains(key.userID),
                      let state = metadataCrawlStates[key] else {
                    throw CancellationError()
                }
                let requestedCursor = state.nextCursor
                let page = try await backend.mailbox(
                    label: key.label,
                    limit: 100,
                    cursor: requestedCursor
                )
                try Task.checkCancellation()
                guard metadataCrawlOwners[key] == owner,
                      currentUserID == key.userID,
                      page.label == key.label,
                      var updatedState = metadataCrawlStates[key] else {
                    throw CancellationError()
                }
                if let expectedGeneration = observation.syncGeneration,
                   let pageGeneration = page.syncGeneration,
                   expectedGeneration != pageGeneration {
                    throw CancellationError()
                }

                enqueueRows(page.sections.flatMap(\.rows), userID: key.userID)
                updatedState.lastPageCursor = requestedCursor
                if let nextCursor = page.nextCursor, !nextCursor.isEmpty {
                    guard nextCursor != requestedCursor,
                          updatedState.seenCursors.insert(nextCursor).inserted else {
                        metadataCrawlStates[key] = updatedState
                        break
                    }
                    updatedState.nextCursor = nextCursor
                    metadataCrawlStates[key] = updatedState
                    continue
                }
                updatedState.nextCursor = nil
                updatedState.isComplete = true
                updatedState.completedMetadataCount = observation.metadataCount
                metadataCrawlStates[key] = updatedState
                break
            }
        } catch {
            // The durable cursor remains at the failed page. A later progress
            // observation resumes it without disturbing any active mailbox UI.
        }

        finishMetadataCrawl(
            key: key,
            observation: observation,
            owner: owner
        )
    }

    private func finishMetadataCrawl(
        key: MetadataCrawlKey,
        observation: MetadataObservation,
        owner: MetadataCrawlOwner
    ) {
        guard metadataCrawlOwners[key] == owner else { return }
        metadataCrawlTasks[key] = nil
        metadataCrawlOwners[key] = nil
        guard let latest = latestMetadataObservations[key.userID], latest != observation else { return }
        resumeMetadataCrawl(key: key, observation: latest)
    }

    private func cancelMetadataCrawl(key: MetadataCrawlKey, discardState: Bool) {
        metadataCrawlTasks[key]?.cancel()
        metadataCrawlTasks[key] = nil
        metadataCrawlOwners[key] = nil
        if discardState {
            metadataCrawlStates[key] = nil
        }
    }

    private func cancelMetadataCrawls(userID: String, discardState: Bool = false) {
        let keys = Set(metadataCrawlStates.keys)
            .union(metadataCrawlTasks.keys)
            .filter { $0.userID == userID }
        for key in keys {
            cancelMetadataCrawl(key: key, discardState: discardState)
        }
    }

    private func drain(userID: String, generation: UInt, ownerID: UUID) async {
        defer {
            if drainOwnerID == ownerID {
                drainTask = nil
                drainOwnerID = nil
            }
        }
        while !Task.isCancelled {
            guard ownsWork(userID: userID, generation: generation, ownerID: ownerID) else { return }
            let batch = takeNextWork(userID: userID, maximum: 40)
            guard !batch.isEmpty else { return }
            let chunks = stride(from: 0, to: batch.count, by: 20).map {
                Array(batch[$0 ..< min($0 + 20, batch.count)])
            }

            let results = await withTaskGroup(of: BatchResult.self, returning: [BatchResult].self) { group in
                for chunk in chunks.prefix(2) {
                    group.addTask { [backend] in
                        do {
                            let response = try await backend.batchThreads(threadIDs: chunk.map(\.threadID))
                            return BatchResult(requested: chunk, result: .success(response))
                        } catch {
                            return BatchResult(requested: chunk, result: .failure(error))
                        }
                    }
                }
                var output: [BatchResult] = []
                for await result in group {
                    output.append(result)
                }
                return output
            }

            guard ownsWork(userID: userID, generation: generation, ownerID: ownerID) else { return }

            var retryItems: [WorkItem] = []
            var paginatedItems: [WorkItem] = []
            var storageFailed = false
            for batchResult in results {
                switch batchResult.result {
                case .success(let response):
                    let requestedByID = Dictionary(uniqueKeysWithValues: batchResult.requested.map { ($0.threadID, $0) })
                    for thread in response.threads {
                        guard ownsWork(userID: userID, generation: generation, ownerID: ownerID) else { return }
                        let threadID = thread.gmailThreadID ?? thread.entityID
                        guard thread.userID == userID, requestedByID[threadID] != nil else {
                            // Never persist a cross-account or unsolicited
                            // response under the work owner's encryption key.
                            continue
                        }
                        localMailStore.writeThread(thread, userID: userID, threadID: threadID)
                        guard localMailStore.readThread(userID: userID, threadID: threadID) == thread else {
                            storageFailed = true
                            break
                        }
                        if requestedByID[threadID]?.priority ?? 0 >= 90 {
                            await attachmentPrefetchCoordinator?.enqueue(
                                thread: thread,
                                userID: userID,
                                selected: false
                            )
                            guard ownsWork(userID: userID, generation: generation, ownerID: ownerID) else { return }
                        }
                    }
                    for threadID in response.missingThreadIDs {
                        guard ownsWork(userID: userID, generation: generation, ownerID: ownerID) else { return }
                        localMailStore.removeThread(userID: userID, threadID: threadID)
                    }
                    for threadID in response.pendingThreadIDs {
                        if var item = requestedByID[threadID], item.retryCount < 8 {
                            item.retryCount += 1
                            item.pendingResponseCount += 1
                            if item.pendingResponseCount >= pendingResponsesBeforePaginatedFallback {
                                paginatedItems.append(item)
                            } else {
                                retryItems.append(item)
                            }
                        }
                    }
                case .failure(let error):
                    if error is CancellationError { return }
                    for var item in batchResult.requested where item.retryCount < 8 {
                        item.retryCount += 1
                        retryItems.append(item)
                    }
                }
                if storageFailed { break }
            }

            if !storageFailed, !paginatedItems.isEmpty {
                let paginatedResults = await fetchPaginatedSnapshots(
                    paginatedItems,
                    userID: userID
                )
                guard ownsWork(userID: userID, generation: generation, ownerID: ownerID) else { return }
                for paginatedResult in paginatedResults {
                    switch paginatedResult.result {
                    case .success(let thread):
                        guard ownsWork(userID: userID, generation: generation, ownerID: ownerID) else { return }
                        localMailStore.writeThread(
                            thread,
                            userID: userID,
                            threadID: paginatedResult.requested.threadID
                        )
                        guard localMailStore.readThread(
                            userID: userID,
                            threadID: paginatedResult.requested.threadID
                        ) == thread else {
                            storageFailed = true
                            break
                        }
                        if paginatedResult.requested.priority >= 90 {
                            await attachmentPrefetchCoordinator?.enqueue(
                                thread: thread,
                                userID: userID,
                                selected: false
                            )
                            guard ownsWork(userID: userID, generation: generation, ownerID: ownerID) else { return }
                        }
                    case .failure(let error):
                        if error is CancellationError { return }
                        if (error as? APIError) == .httpStatus(404) {
                            guard ownsWork(userID: userID, generation: generation, ownerID: ownerID) else { return }
                            localMailStore.removeThread(
                                userID: userID,
                                threadID: paginatedResult.requested.threadID
                            )
                            continue
                        }
                        var item = paginatedResult.requested
                        if item.retryCount < 8 {
                            item.retryCount += 1
                            retryItems.append(item)
                        }
                    }
                }
            }

            if storageFailed {
                pausedUsers.insert(userID)
                cancelMetadataCrawls(userID: userID)
                await MainActor.run {
                    NotificationCenter.default.post(
                        name: .offlineContentSyncStorageWarning,
                        object: nil,
                        userInfo: [
                            "user_id": userID,
                            "message": "Offline mail could not be saved. Online mail is still available.",
                        ]
                    )
                }
                return
            }

            guard ownsWork(userID: userID, generation: generation, ownerID: ownerID) else { return }
            guard !retryItems.isEmpty else { continue }
            requeue(retryItems, userID: userID)
            let highestRetry = retryItems.map(\.retryCount).max() ?? 1
            let delaySeconds = min(60, 2 << min(5, max(0, highestRetry - 1)))
            do {
                try await Task.sleep(for: retryDelayOverride ?? .seconds(delaySeconds))
            } catch {
                return
            }
        }
    }

    private func fetchPaginatedSnapshots(
        _ items: [WorkItem],
        userID: String
    ) async -> [PaginatedResult] {
        var output: [PaginatedResult] = []
        for start in stride(from: 0, to: items.count, by: 2) {
            if Task.isCancelled { break }
            let chunk = Array(items[start ..< min(start + 2, items.count)])
            let chunkResults = await withTaskGroup(
                of: PaginatedResult.self,
                returning: [PaginatedResult].self
            ) { group in
                for item in chunk {
                    group.addTask { [backend] in
                        do {
                            let thread = try await Self.fetchCompletePaginatedThread(
                                backend: backend,
                                item: item,
                                expectedUserID: userID
                            )
                            return PaginatedResult(requested: item, result: .success(thread))
                        } catch {
                            return PaginatedResult(requested: item, result: .failure(error))
                        }
                    }
                }
                var results: [PaginatedResult] = []
                for await result in group {
                    results.append(result)
                }
                return results
            }
            output.append(contentsOf: chunkResults)
        }
        return output
    }

    private static func fetchCompletePaginatedThread(
        backend: AppClient,
        item: WorkItem,
        expectedUserID: String
    ) async throws -> ThreadReaderResponse {
        var snapshot: ThreadReaderResponse?
        var expectedEntityID: String?
        var expectedGmailThreadID: String?
        var expectedSource: SourceType?
        var expectedRevision: String?
        var expectedTotalMessages: Int?
        var offset = 0
        var seenMessageIDs = Set<String>()

        for _ in 0..<maximumPaginatedThreadPages {
            try Task.checkCancellation()
            let page = try await backend.thread(
                threadID: item.threadID,
                limit: paginatedThreadPageSize,
                offset: offset
            )
            try Task.checkCancellation()

            let logicalThreadID = page.gmailThreadID ?? page.entityID
            guard logicalThreadID == item.threadID,
                  page.userID == expectedUserID,
                  page.offset == offset,
                  page.messages.count <= paginatedThreadPageSize else {
                throw PaginatedSnapshotError.identityChanged
            }

            if snapshot == nil {
                expectedEntityID = page.entityID
                expectedGmailThreadID = page.gmailThreadID
                expectedSource = page.source
                expectedRevision = page.contentRevision
                expectedTotalMessages = page.totalMessages
                snapshot = page
                seenMessageIDs.formUnion(page.messages.map(\.id))
            } else {
                guard page.entityID == expectedEntityID,
                      page.gmailThreadID == expectedGmailThreadID,
                      page.source == expectedSource,
                      page.totalMessages == expectedTotalMessages else {
                    throw PaginatedSnapshotError.identityChanged
                }
                guard page.contentRevision == expectedRevision else {
                    throw PaginatedSnapshotError.revisionChanged
                }
                let newlyObserved = page.messages.reduce(into: 0) { count, message in
                    if seenMessageIDs.insert(message.id).inserted {
                        count += 1
                    }
                }
                if page.hasMore, newlyObserved == 0 {
                    throw PaginatedSnapshotError.repeatedPage
                }
                snapshot = snapshot?.appendingPage(page)
            }

            guard let current = snapshot else {
                throw PaginatedSnapshotError.incomplete
            }
            if !page.hasMore {
                guard current.messages.count == page.totalMessages,
                      current.messages.allSatisfy(\.bodyComplete) else {
                    throw PaginatedSnapshotError.incomplete
                }
                return current
            }
            guard !page.messages.isEmpty else {
                throw PaginatedSnapshotError.repeatedPage
            }
            offset += page.messages.count
        }
        throw PaginatedSnapshotError.incomplete
    }

    private func ownsWork(userID: String, generation: UInt, ownerID: UUID) -> Bool {
        currentUserID == userID
            && workGenerations[userID, default: 0] == generation
            && drainOwnerID == ownerID
            && !pausedUsers.contains(userID)
    }

    private func takeNextWork(userID: String, maximum: Int) -> [WorkItem] {
        var pending = pendingByUser[userID] ?? [:]
        let selected = pending.values.sorted {
            if $0.priority == $1.priority {
                return $0.threadID < $1.threadID
            }
            return $0.priority > $1.priority
        }.prefix(maximum)
        for item in selected {
            pending[item.threadID] = nil
        }
        pendingByUser[userID] = pending
        return Array(selected)
    }

    private func requeue(_ items: [WorkItem], userID: String) {
        var pending = pendingByUser[userID] ?? [:]
        for item in items {
            if let existing = pending[item.threadID], existing.priority > item.priority {
                continue
            }
            pending[item.threadID] = item
        }
        pendingByUser[userID] = pending
    }
}
