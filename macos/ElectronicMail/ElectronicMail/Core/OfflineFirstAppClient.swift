import Foundation

public final class OfflineFirstAppClient: AppClient {
    private let backend: AppClient
    private let localMailStore: LocalMailStore
    private let attachmentCache: EncryptedAttachmentCache
    private let attachmentPrefetchCoordinator: AttachmentPrefetchCoordinator
    private let offlineContentSyncCoordinator: OfflineContentSyncCoordinator
    private let remoteImageLoader: EmailRemoteImageLoader
    private var currentBackendUserID: String?
    private var cachedRemoteImageUserID: String?
    private var verifiedRemoteImageUserID: String?
    private var remoteImageConfigurationRevision: UInt64 = 0
    private let accountOperationLock = NSLock()
    private let accountSideEffectMutex = AccountSideEffectMutex()
    private var accountOperationGeneration: UInt64 = 0
    private var accountOperationsBlocked = false
    private var accountTransitionTask: Task<Void, Never>?

    public convenience init(backend: AppClient, localMailStore: LocalMailStore) {
        self.init(
            backend: backend,
            localMailStore: localMailStore,
            remoteImageLoader: .shared
        )
    }

    init(
        backend: AppClient,
        localMailStore: LocalMailStore,
        remoteImageLoader: EmailRemoteImageLoader
    ) {
        self.backend = backend
        self.localMailStore = localMailStore
        self.remoteImageLoader = remoteImageLoader
        let attachmentCache = EncryptedAttachmentCache()
        let attachmentPrefetchCoordinator = AttachmentPrefetchCoordinator(
            backend: backend,
            cache: attachmentCache
        )
        self.attachmentCache = attachmentCache
        self.attachmentPrefetchCoordinator = attachmentPrefetchCoordinator
        self.offlineContentSyncCoordinator = OfflineContentSyncCoordinator(
            backend: backend,
            localMailStore: localMailStore,
            attachmentPrefetchCoordinator: attachmentPrefetchCoordinator
        )
        let restoredUserID = localMailStore.readSession()?.user.id
        self.currentBackendUserID = restoredUserID
        self.cachedRemoteImageUserID = restoredUserID
        self.verifiedRemoteImageUserID = nil
        configureRemoteImageLoader()
    }

    public var baseURL: URL {
        get { backend.baseURL }
        set {
            backend.baseURL = newValue
            configureRemoteImageLoader()
        }
    }

    public var sessionToken: String? {
        get { backend.sessionToken }
        set {
            if backend.sessionToken != newValue {
                transitionAccountOperations(blocked: newValue?.isEmpty != false)
                let previousUserID = currentBackendUserID ?? cachedRemoteImageUserID
                currentBackendUserID = nil
                verifiedRemoteImageUserID = nil
                scheduleAccountTransitionReset(userID: previousUserID)
            }
            backend.sessionToken = newValue
            configureRemoteImageLoader()
        }
    }

    public var mode: AppRunMode {
        backend.mode
    }

    public var supportsRealtimeMailboxUpdates: Bool {
        backend.supportsRealtimeMailboxUpdates
    }

    public var supportsFolderCountPrefetch: Bool {
        backend.supportsFolderCountPrefetch
    }

    public func exchangeMobileSession(loginCode: String) async throws -> MobileSessionExchangeResponse {
        try await backend.exchangeMobileSession(loginCode: loginCode)
    }

    public func exchangeMobileSession(grant: MobileAuthenticationGrant) async throws -> MobileSessionExchangeResponse {
        try await backend.exchangeMobileSession(grant: grant)
    }

    public func logout() async throws {
        let userID = currentBackendUserID ?? localMailStore.readSession()?.user.id
        try await performRemoteAccountActionAndPurge(userID: userID) {
            try await backend.logout()
        }
    }

    public func disconnectGoogle(deleteData: Bool, revokeSessions: Bool) async throws {
        let userID = currentBackendUserID ?? localMailStore.readSession()?.user.id
        try await performRemoteAccountActionAndPurge(userID: userID) {
            try await backend.disconnectGoogle(deleteData: deleteData, revokeSessions: revokeSessions)
        }
    }

    public func deleteGoogleData() async throws {
        let userID = currentBackendUserID ?? localMailStore.readSession()?.user.id
        try await performRemoteAccountActionAndPurge(userID: userID, resumeAfterPurge: true) {
            try await backend.deleteGoogleData()
        }
    }

    public func deleteAccount() async throws {
        let userID = currentBackendUserID ?? localMailStore.readSession()?.user.id
        try await performRemoteAccountActionAndPurge(userID: userID) {
            try await backend.deleteAccount()
        }
    }

    public func appSession() async throws -> AppSessionResponse {
        let operationGeneration = try await beginAccountOperationAfterTransitions()
        let expectedSessionToken = backend.sessionToken
        let response = try await refreshLocalSession(
            expectedSessionToken: expectedSessionToken,
            operationGeneration: operationGeneration
        )
        try await withAccountSideEffectFence(operationGeneration) {
            await self.offlineContentSyncCoordinator.observe(
                mailbox: response.mailbox,
                userID: response.user.id,
                isInitialWindow: true,
                accountEpoch: operationGeneration
            )
        }
        await replayPendingThreadActions(
            for: response.user.id,
            expectedSessionToken: expectedSessionToken,
            operationGeneration: operationGeneration
        )
        try validateSessionToken(expectedSessionToken)
        try validateAccountOperation(operationGeneration)
        return response
    }

    public func mailbox(label: MailboxLabel, limit: Int, cursor: String?) async throws -> MailboxResponse {
        let operationGeneration = try await beginAccountOperationAfterTransitions()
        let expectedSessionToken = backend.sessionToken
        let userID = try await resolvedBackendUserID(
            expectedSessionToken: expectedSessionToken,
            operationGeneration: operationGeneration
        )
        do {
            let response = try await backend.mailbox(label: label, limit: limit, cursor: cursor)
            try validateSessionToken(expectedSessionToken)
            try validateAccountOperation(operationGeneration)
            if let userID {
                try await withAccountSideEffectFence(operationGeneration) {
                    await self.offlineContentSyncCoordinator.observe(
                        mailbox: response,
                        userID: userID,
                        accountEpoch: operationGeneration
                    )
                }
            }
            try validateAccountOperation(operationGeneration)
            return response
        } catch {
            try validateSessionToken(expectedSessionToken)
            try validateAccountOperation(operationGeneration)
            // A mailbox cache is an accumulated snapshot, not a transport page.
            // Returning it for a cursor request would make the caller append a
            // whole snapshot as though it were that page. Only the initial
            // request may fall back to the last atomically persisted snapshot.
            if cursor == nil,
               let userID,
               let cached = localMailStore.readMailbox(userID: userID, label: label) {
                try validateAccountOperation(operationGeneration)
                try await withAccountSideEffectFence(operationGeneration) {
                    await self.offlineContentSyncCoordinator.observe(
                        mailbox: cached,
                        userID: userID,
                        accountEpoch: operationGeneration
                    )
                }
                return cached
            }
            throw error
        }
    }

    public func mailboxFolderCount(label: MailboxLabel) async throws -> MailboxResponse {
        let operationGeneration = try await beginAccountOperationAfterTransitions()
        let expectedSessionToken = backend.sessionToken
        let userID = try await resolvedBackendUserID(
            expectedSessionToken: expectedSessionToken,
            operationGeneration: operationGeneration
        )
        do {
            let response = try await backend.mailboxFolderCount(label: label)
            try validateSessionToken(expectedSessionToken)
            try validateAccountOperation(operationGeneration)
            return response
        } catch {
            try validateSessionToken(expectedSessionToken)
            try validateAccountOperation(operationGeneration)
            if let userID, let cached = localMailStore.readMailbox(userID: userID, label: label) {
                try validateAccountOperation(operationGeneration)
                return cached
            }
            throw error
        }
    }

    public func searchMailbox(
        query: String,
        label: MailboxLabel?,
        limit: Int,
        cursor: String?,
        hydrateInBackground: Bool = true
    ) async throws -> MailboxResponse {
        try await accountScopedResponse {
            try await self.backend.searchMailbox(
                query: query,
                label: label,
                limit: limit,
                cursor: cursor,
                hydrateInBackground: hydrateInBackground
            )
        }
    }

    public func thread(threadID: String, limit: Int, offset: Int) async throws -> ThreadReaderResponse {
        let operationGeneration = try await beginAccountOperationAfterTransitions()
        let expectedSessionToken = backend.sessionToken
        let expectedUserID = currentBackendUserID ?? localMailStore.readSession()?.user.id
        if let expectedUserID {
            try await withAccountSideEffectFence(operationGeneration) {
                await self.offlineContentSyncCoordinator.prioritize(
                    threadID: threadID,
                    contentRevision: self.localMailStore.readThread(
                        userID: expectedUserID,
                        threadID: threadID
                    )?.contentRevision,
                    userID: expectedUserID,
                    accountEpoch: operationGeneration
                )
            }
            try validateAccountOperation(operationGeneration)
        }
        let response: ThreadReaderResponse
        do {
            response = try await backend.thread(threadID: threadID, limit: limit, offset: offset)
        } catch {
            try validateSessionToken(expectedSessionToken)
            try validateAccountOperation(operationGeneration)
            throw error
        }
        try validateSessionToken(expectedSessionToken)
        try validateAccountOperation(operationGeneration)
        guard let expectedUserID else {
            // An unverified response may be shown online, but it must never
            // create an account key or durable body under provider-supplied
            // identity alone.
            return response
        }
        if response.userID != expectedUserID {
            throw APIError.emptyResponse
        }
        try performIfCurrent(operationGeneration) {
            if response.offset == 0, !response.hasMore {
                localMailStore.writeThread(response, userID: response.userID, threadID: threadID)
            }
        }
        try await withAccountSideEffectFence(operationGeneration) {
            await self.attachmentPrefetchCoordinator.enqueue(
                thread: response,
                userID: response.userID,
                selected: true,
                accountEpoch: operationGeneration
            )
        }
        try validateAccountOperation(operationGeneration)
        return response
    }

    public func batchThreads(threadIDs: [String]) async throws -> MailboxThreadBatchResponse {
        let operationGeneration = try await beginAccountOperationAfterTransitions()
        let expectedSessionToken = backend.sessionToken
        let expectedUserID = currentBackendUserID ?? localMailStore.readSession()?.user.id
        let response: MailboxThreadBatchResponse
        do {
            response = try await backend.batchThreads(threadIDs: threadIDs)
        } catch {
            try validateSessionToken(expectedSessionToken)
            try validateAccountOperation(operationGeneration)
            throw error
        }
        try validateSessionToken(expectedSessionToken)
        try validateAccountOperation(operationGeneration)
        if let expectedUserID {
            try performIfCurrent(operationGeneration) {
                for thread in response.threads where thread.userID == expectedUserID {
                    localMailStore.writeThread(
                        thread,
                        userID: expectedUserID,
                        threadID: thread.gmailThreadID ?? thread.entityID
                    )
                }
                for threadID in response.missingThreadIDs {
                    localMailStore.removeThread(userID: expectedUserID, threadID: threadID)
                }
            }
        }
        return response
    }

    public func observeHydratedThreads(
        _ threads: [MailboxHydratedThreadState],
        userID: String
    ) async {
        guard let operationGeneration = try? await beginAccountOperationAfterTransitions(),
              currentBackendUserID == userID else { return }
        try? await withAccountSideEffectFence(operationGeneration) {
            await self.offlineContentSyncCoordinator.observe(
                hydratedThreads: threads,
                userID: userID,
                accountEpoch: operationGeneration
            )
        }
    }

    public func mailboxSyncState() async throws -> MailboxSyncStateResponse {
        let operationGeneration = try await beginAccountOperationAfterTransitions()
        let expectedSessionToken = backend.sessionToken
        let userID = try await resolvedBackendUserID(
            expectedSessionToken: expectedSessionToken,
            operationGeneration: operationGeneration
        )
        let response: MailboxSyncStateResponse
        do {
            response = try await backend.mailboxSyncState()
        } catch {
            try validateSessionToken(expectedSessionToken)
            try validateAccountOperation(operationGeneration)
            throw error
        }
        try validateSessionToken(expectedSessionToken)
        try validateAccountOperation(operationGeneration)
        if let userID {
            try await withAccountSideEffectFence(operationGeneration) {
                await self.offlineContentSyncCoordinator.observe(
                    progress: response,
                    userID: userID,
                    accountEpoch: operationGeneration
                )
            }
        }
        try validateAccountOperation(operationGeneration)
        return response
    }

    public func triggerMailboxSync() async throws -> MailboxSyncTriggerResponse {
        let operationGeneration = try await beginAccountOperationAfterTransitions()
        let expectedSessionToken = backend.sessionToken
        let userID = try await resolvedBackendUserID(
            expectedSessionToken: expectedSessionToken,
            operationGeneration: operationGeneration
        )
        if let userID {
            await replayPendingThreadActions(
                for: userID,
                expectedSessionToken: expectedSessionToken,
                operationGeneration: operationGeneration
            )
        }
        try validateAccountOperation(operationGeneration)
        let response: MailboxSyncTriggerResponse
        do {
            response = try await backend.triggerMailboxSync()
        } catch {
            try validateSessionToken(expectedSessionToken)
            try validateAccountOperation(operationGeneration)
            throw error
        }
        try validateSessionToken(expectedSessionToken)
        try validateAccountOperation(operationGeneration)
        if let userID {
            try await withAccountSideEffectFence(operationGeneration) {
                await self.offlineContentSyncCoordinator.observe(
                    progress: response.state,
                    userID: userID,
                    accountEpoch: operationGeneration
                )
            }
        }
        try validateAccountOperation(operationGeneration)
        return response
    }

    public func syncMailboxNow() async throws -> MailboxSyncTriggerResponse {
        let operationGeneration = try await beginAccountOperationAfterTransitions()
        let expectedSessionToken = backend.sessionToken
        let userID = try await resolvedBackendUserID(
            expectedSessionToken: expectedSessionToken,
            operationGeneration: operationGeneration
        )
        if let userID {
            await replayPendingThreadActions(
                for: userID,
                expectedSessionToken: expectedSessionToken,
                operationGeneration: operationGeneration
            )
        }
        try validateAccountOperation(operationGeneration)
        let response: MailboxSyncTriggerResponse
        do {
            response = try await backend.syncMailboxNow()
        } catch {
            try validateSessionToken(expectedSessionToken)
            try validateAccountOperation(operationGeneration)
            throw error
        }
        try validateSessionToken(expectedSessionToken)
        try validateAccountOperation(operationGeneration)
        if let userID {
            try await withAccountSideEffectFence(operationGeneration) {
                await self.offlineContentSyncCoordinator.observe(
                    progress: response.state,
                    userID: userID,
                    accountEpoch: operationGeneration
                )
            }
        }
        try validateAccountOperation(operationGeneration)
        return response
    }

    public func archiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        try await enqueueOrStoreThreadAction(threadID: threadID, action: .archive)
    }

    public func unarchiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        try await enqueueOrStoreThreadAction(threadID: threadID, action: .unarchive)
    }

    public func markThreadRead(_ threadID: String) async throws -> GmailThreadMutationResponse {
        try await enqueueOrStoreThreadAction(threadID: threadID, action: .markRead)
    }

    public func enqueueThreadAction(_ request: QueuedThreadActionRequest) async throws -> QueuedThreadActionResponse {
        let operationGeneration = try await beginAccountOperationAfterTransitions()
        let expectedSessionToken = backend.sessionToken
        do {
            let response = try await backend.enqueueThreadAction(request)
            try validateSessionToken(expectedSessionToken)
            try performIfCurrent(operationGeneration) {
                localMailStore.removePendingThreadAction(clientActionID: request.clientActionID)
            }
            return response
        } catch {
            try validateSessionToken(expectedSessionToken)
            try validateAccountOperation(operationGeneration)
            guard Self.isRetryableOfflineActionError(error) else {
                throw error
            }
            if let userID = localMailStore.readSession()?.user.id {
                try performIfCurrent(operationGeneration) {
                    localMailStore.writePendingThreadAction(
                        LocalPendingThreadAction(
                            clientActionID: request.clientActionID,
                            userID: userID,
                            mailboxThreadID: request.mailboxThreadID,
                            targetMessageID: request.targetMessageID,
                            action: request.action,
                            createdAt: request.createdAt,
                            error: error.localizedDescription
                        )
                    )
                }
                return QueuedThreadActionResponse(
                    clientActionID: request.clientActionID,
                    serverActionID: request.clientActionID,
                    mailboxThreadID: request.mailboxThreadID,
                    targetMessageID: request.targetMessageID,
                    action: request.action,
                    state: .queued,
                    queuedAt: request.createdAt,
                    appliedAt: nil,
                    error: error.localizedDescription
                )
            }
            throw error
        }
    }

    public func sendCompose(_ request: MailComposeRequest) async throws -> MailSendResponse {
        try await accountScopedResponse { try await self.backend.sendCompose(request) }
    }

    public func sendReply(threadID: String, request: MailReplyRequest) async throws -> MailSendResponse {
        try await accountScopedResponse {
            try await self.backend.sendReply(threadID: threadID, request: request)
        }
    }

    public func outbox(limit: Int) async throws -> MailOutboxResponse {
        try await accountScopedResponse { try await self.backend.outbox(limit: limit) }
    }

    public func sendStatus(serverSendID: String) async throws -> MailSendResponse {
        try await accountScopedResponse {
            try await self.backend.sendStatus(serverSendID: serverSendID)
        }
    }

    public func retrySend(serverSendID: String) async throws -> MailSendResponse {
        try await accountScopedResponse {
            try await self.backend.retrySend(serverSendID: serverSendID)
        }
    }

    public func createDraft(_ request: MailDraftSaveRequest) async throws -> MailDraftResponse {
        try await accountScopedResponse { try await self.backend.createDraft(request) }
    }

    public func draft(mailboxThreadID: String) async throws -> MailDraftResponse {
        try await accountScopedResponse {
            try await self.backend.draft(mailboxThreadID: mailboxThreadID)
        }
    }

    public func updateDraft(gmailDraftID: String, request: MailDraftSaveRequest) async throws -> MailDraftResponse {
        try await accountScopedResponse {
            try await self.backend.updateDraft(gmailDraftID: gmailDraftID, request: request)
        }
    }

    public func deleteDraft(gmailDraftID: String) async throws {
        try await accountScopedResponse {
            try await self.backend.deleteDraft(gmailDraftID: gmailDraftID)
        }
    }

    public func sendDraft(gmailDraftID: String, request: MailDraftSendRequest) async throws -> MailSendResponse {
        try await accountScopedResponse {
            try await self.backend.sendDraft(gmailDraftID: gmailDraftID, request: request)
        }
    }

    public func downloadAttachment(messageID: String, attachment: ThreadAttachment) async throws -> DownloadedAttachment {
        let operationGeneration = try await beginAccountOperationAfterTransitions()
        let expectedSessionToken = backend.sessionToken
        let userID = currentBackendUserID ?? localMailStore.readSession()?.user.id
        let downloaded: DownloadedAttachment
        do {
            if let userID {
                downloaded = try await attachmentPrefetchCoordinator.download(
                    messageID: messageID,
                    attachment: attachment,
                    userID: userID,
                    accountEpoch: operationGeneration
                )
            } else {
                downloaded = try await backend.downloadAttachment(
                    messageID: messageID,
                    attachment: attachment
                )
            }
        } catch {
            try validateSessionToken(expectedSessionToken)
            try validateAccountOperation(operationGeneration)
            throw error
        }
        try validateSessionToken(expectedSessionToken)
        try validateAccountOperation(operationGeneration)
        return downloaded
    }

    public func createTask(_ request: TaskCreateRequest) async throws -> TaskResponse {
        try await accountScopedResponse { try await self.backend.createTask(request) }
    }

    public func updateTask(_ taskID: String, request: TaskUpdateRequest) async throws -> TaskResponse {
        try await accountScopedResponse {
            try await self.backend.updateTask(taskID, request: request)
        }
    }

    public func completeEntity(_ entityID: String, request: EntityOutcomeRequest) async throws -> EntityOutcomeResponse {
        try await accountScopedResponse {
            try await self.backend.completeEntity(entityID, request: request)
        }
    }

    private func enqueueOrStoreThreadAction(threadID: String, action: GmailThreadAction) async throws -> GmailThreadMutationResponse {
        let request = QueuedThreadActionRequest(
            clientActionID: UUID().uuidString,
            mailboxThreadID: threadID,
            targetMessageID: nil,
            action: action,
            createdAt: ISO8601DateFormatter.backendActionTimestamp.string(from: Date())
        )
        _ = try await enqueueThreadAction(request)
        return GmailThreadMutationResponse(threadID: threadID, action: action)
    }

    private func refreshLocalSession(
        expectedSessionToken: String?,
        operationGeneration: UInt64
    ) async throws -> AppSessionResponse {
        let response: AppSessionResponse
        do {
            response = try await backend.appSession()
        } catch {
            try validateSessionToken(expectedSessionToken)
            try validateAccountOperation(operationGeneration)
            throw error
        }
        try validateSessionToken(expectedSessionToken)
        try validateAccountOperation(operationGeneration)
        try await withAccountSideEffectFence(operationGeneration) {
            let configurationRevision = try self.performIfCurrent(operationGeneration) {
                self.currentBackendUserID = response.user.id
                self.cachedRemoteImageUserID = response.user.id
                self.verifiedRemoteImageUserID = response.user.id
                self.remoteImageConfigurationRevision &+= 1
                return self.remoteImageConfigurationRevision
            }
            await self.attachmentPrefetchCoordinator.activate(
                userID: response.user.id,
                accountEpoch: operationGeneration
            )
            // Reader WebViews can be opened as soon as this method returns.
            // Await configuration and serialize it with purge so an old
            // response cannot reactivate image networking after account data
            // has been removed.
            await self.remoteImageLoader.configure(
                baseURL: self.backend.baseURL,
                sessionToken: self.backend.sessionToken,
                cacheUserID: response.user.id,
                networkUserID: response.user.id,
                configurationRevision: configurationRevision
            )
            try self.performIfCurrent(operationGeneration) {
                self.localMailStore.writeSession(response)
            }
        }
        return response
    }

    private func resolvedBackendUserID(
        expectedSessionToken: String?,
        operationGeneration: UInt64
    ) async throws -> String? {
        if let currentBackendUserID = currentAccountUserID() {
            return currentBackendUserID
        }
        return try await refreshLocalSession(
            expectedSessionToken: expectedSessionToken,
            operationGeneration: operationGeneration
        ).user.id
    }

    private func replayPendingThreadActions(
        for userID: String,
        expectedSessionToken: String?,
        operationGeneration: UInt64
    ) async {
        for action in localMailStore.pendingThreadActions() {
            guard backend.sessionToken == expectedSessionToken,
                  (try? validateAccountOperation(operationGeneration)) != nil else {
                return
            }
            guard action.userID == userID else {
                continue
            }
            do {
                _ = try await backend.enqueueThreadAction(action.request)
                guard backend.sessionToken == expectedSessionToken,
                      (try? performIfCurrent(operationGeneration, operation: {
                          localMailStore.removePendingThreadAction(clientActionID: action.clientActionID)
                      })) != nil else {
                    return
                }
            } catch {
                guard backend.sessionToken == expectedSessionToken,
                      (try? validateAccountOperation(operationGeneration)) != nil else {
                    return
                }
                if Self.shouldDiscardReplayedOfflineAction(error) {
                    try? performIfCurrent(operationGeneration) {
                        localMailStore.removePendingThreadAction(clientActionID: action.clientActionID)
                    }
                    continue
                }
                try? performIfCurrent(operationGeneration) {
                    localMailStore.markPendingThreadActionFailed(
                        clientActionID: action.clientActionID,
                        error: error.localizedDescription
                    )
                }
                return
            }
        }
    }

    private func accountScopedResponse<Response>(
        _ operation: () async throws -> Response
    ) async throws -> Response {
        let operationGeneration = try await beginAccountOperationAfterTransitions()
        let expectedSessionToken = backend.sessionToken
        do {
            let response = try await operation()
            try validateSessionToken(expectedSessionToken)
            try validateAccountOperation(operationGeneration)
            return response
        } catch {
            try validateSessionToken(expectedSessionToken)
            try validateAccountOperation(operationGeneration)
            throw error
        }
    }

    private func validateSessionToken(_ expectedSessionToken: String?) throws {
        guard backend.sessionToken == expectedSessionToken else {
            throw CancellationError()
        }
    }

    private func performRemoteAccountActionAndPurge(
        userID: String?,
        resumeAfterPurge: Bool = false,
        operation: () async throws -> Void
    ) async throws {
        transitionAccountOperations(blocked: true)
        do {
            try await operation()
        } catch {
            await purgeLocalAccount(userID)
            if resumeAfterPurge {
                transitionAccountOperations(blocked: false)
            }
            throw error
        }
        await purgeLocalAccount(userID)
        if resumeAfterPurge {
            transitionAccountOperations(blocked: false)
        }
    }

    private func purgeLocalAccount(_ userID: String?) async {
        transitionAccountOperations(blocked: true)
        let invalidatingAccountEpoch = currentAccountOperationGeneration()
        currentBackendUserID = nil
        cachedRemoteImageUserID = nil
        verifiedRemoteImageUserID = nil
        let remoteImageConfigurationRevision = nextRemoteImageConfigurationRevision()
        await withExclusiveAccountSideEffects {
            await self.remoteImageLoader.configure(
                baseURL: self.backend.baseURL,
                sessionToken: nil,
                cacheUserID: nil,
                networkUserID: nil,
                configurationRevision: remoteImageConfigurationRevision
            )
            if let userID {
                await self.offlineContentSyncCoordinator.reset(
                    userID: userID,
                    invalidatingAccountEpoch: invalidatingAccountEpoch
                )
                await self.attachmentPrefetchCoordinator.reset(
                    userID: userID,
                    invalidatingAccountEpoch: invalidatingAccountEpoch
                )
                await self.remoteImageLoader.deactivateNetwork(userID: userID)
                await self.remoteImageLoader.purge(userID: userID)
                await self.attachmentCache.purge(userID: userID)
                // The account key is removed by the local store only after
                // every media writer is cancelled and awaited, so no late
                // response can recreate encrypted bytes or a replacement key.
                self.localMailStore.purgeAccount(userID: userID)
            } else {
                await self.offlineContentSyncCoordinator.reset(
                    invalidatingAccountEpoch: invalidatingAccountEpoch
                )
                await self.attachmentPrefetchCoordinator.reset(
                    invalidatingAccountEpoch: invalidatingAccountEpoch
                )
                await self.remoteImageLoader.purge(userID: nil)
                await self.attachmentCache.purgeAll()
                self.localMailStore.clearAll()
            }
        }
    }

    private func configureRemoteImageLoader() {
        let baseURL = backend.baseURL
        let token = backend.sessionToken
        let cacheUserID = cachedRemoteImageUserID
        let networkUserID = verifiedRemoteImageUserID
        let configurationRevision = nextRemoteImageConfigurationRevision()
        Task { [remoteImageLoader] in
            await remoteImageLoader.configure(
                baseURL: baseURL,
                sessionToken: token,
                cacheUserID: cacheUserID,
                networkUserID: networkUserID,
                configurationRevision: configurationRevision
            )
        }
    }

    private func nextRemoteImageConfigurationRevision() -> UInt64 {
        accountOperationLock.lock()
        remoteImageConfigurationRevision &+= 1
        let revision = remoteImageConfigurationRevision
        accountOperationLock.unlock()
        return revision
    }

    private func beginAccountOperation() throws -> UInt64 {
        accountOperationLock.lock()
        defer { accountOperationLock.unlock() }
        guard !accountOperationsBlocked else {
            throw CancellationError()
        }
        return accountOperationGeneration
    }

    private func beginAccountOperationAfterTransitions() async throws -> UInt64 {
        let operationGeneration = try beginAccountOperation()
        await currentAccountTransitionTask()?.value
        try validateAccountOperation(operationGeneration)
        return operationGeneration
    }

    private func currentAccountTransitionTask() -> Task<Void, Never>? {
        accountOperationLock.lock()
        defer { accountOperationLock.unlock() }
        return accountTransitionTask
    }

    private func validateAccountOperation(_ generation: UInt64) throws {
        accountOperationLock.lock()
        let isCurrent = !accountOperationsBlocked && accountOperationGeneration == generation
        accountOperationLock.unlock()
        guard isCurrent else {
            throw CancellationError()
        }
    }

    private func performIfCurrent<Result>(
        _ generation: UInt64,
        operation: () -> Result
    ) throws -> Result {
        accountOperationLock.lock()
        defer { accountOperationLock.unlock() }
        guard !accountOperationsBlocked, accountOperationGeneration == generation else {
            throw CancellationError()
        }
        return operation()
    }

    private func transitionAccountOperations(blocked: Bool) {
        accountOperationLock.lock()
        accountOperationGeneration &+= 1
        accountOperationsBlocked = blocked
        accountOperationLock.unlock()
    }

    private func currentAccountUserID() -> String? {
        accountOperationLock.lock()
        let userID = currentBackendUserID
        accountOperationLock.unlock()
        return userID
    }

    private func currentAccountOperationGeneration() -> UInt64 {
        accountOperationLock.lock()
        let generation = accountOperationGeneration
        accountOperationLock.unlock()
        return generation
    }

    private func scheduleAccountTransitionReset(userID: String?) {
        let invalidatingAccountEpoch = currentAccountOperationGeneration()
        accountOperationLock.lock()
        let previousTransition = accountTransitionTask
        let task = Task { [weak self] in
            await previousTransition?.value
            guard let self else { return }
            await self.withExclusiveAccountSideEffects {
                await self.offlineContentSyncCoordinator.reset(
                    userID: userID,
                    invalidatingAccountEpoch: invalidatingAccountEpoch
                )
                await self.attachmentPrefetchCoordinator.reset(
                    userID: userID,
                    invalidatingAccountEpoch: invalidatingAccountEpoch
                )
                await self.remoteImageLoader.deactivateNetwork(userID: userID)
            }
        }
        accountTransitionTask = task
        accountOperationLock.unlock()
    }

    private func withAccountSideEffectFence<Result>(
        _ generation: UInt64,
        operation: () async throws -> Result
    ) async throws -> Result {
        await accountSideEffectMutex.acquire()
        do {
            try validateAccountOperation(generation)
            let result = try await operation()
            try validateAccountOperation(generation)
            await accountSideEffectMutex.release()
            return result
        } catch {
            await accountSideEffectMutex.release()
            throw error
        }
    }

    private func withExclusiveAccountSideEffects(
        operation: () async -> Void
    ) async {
        await accountSideEffectMutex.acquire()
        await operation()
        await accountSideEffectMutex.release()
    }

    private static func isRetryableOfflineActionError(_ error: Error) -> Bool {
        if case APIError.httpStatus(let status) = error {
            return status == 408 || status == 429 || status >= 500
        }
        guard let urlError = error as? URLError else {
            return false
        }
        switch urlError.code {
        case .timedOut,
             .cannotFindHost,
             .cannotConnectToHost,
             .networkConnectionLost,
             .dnsLookupFailed,
             .notConnectedToInternet,
             .internationalRoamingOff,
             .callIsActive,
             .dataNotAllowed,
             .secureConnectionFailed:
            return true
        default:
            return false
        }
    }

    private static func shouldDiscardReplayedOfflineAction(_ error: Error) -> Bool {
        guard case APIError.httpStatus(let status) = error else {
            // A durable user action should survive failures whose permanence is
            // unknown. This keeps it available for a later app or network fix.
            return false
        }
        return [400, 404, 409, 410, 413, 422].contains(status)
    }
}

private extension ISO8601DateFormatter {
    static let backendActionTimestamp: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()
}

/// A non-reentrant async mutex for account-scoped actor side effects. Account
/// generation validation alone is insufficient when a stale actor message can
/// be delivered after a purge message. Serializing both paths guarantees the
/// stale work either completes before purge (and is removed by it) or validates
/// after purge and is rejected.
private actor AccountSideEffectMutex {
    private var isHeld = false
    private var waiters: [CheckedContinuation<Void, Never>] = []

    func acquire() async {
        guard isHeld else {
            isHeld = true
            return
        }
        await withCheckedContinuation { continuation in
            waiters.append(continuation)
        }
    }

    func release() {
        if waiters.isEmpty {
            isHeld = false
        } else {
            waiters.removeFirst().resume()
        }
    }
}
