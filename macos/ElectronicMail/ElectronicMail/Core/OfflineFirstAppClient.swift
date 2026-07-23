import Foundation

public final class OfflineFirstAppClient: AppClient {
    private let backend: AppClient
    private let localMailStore: LocalMailStore
    private var currentBackendUserID: String?

    public init(backend: AppClient, localMailStore: LocalMailStore) {
        self.backend = backend
        self.localMailStore = localMailStore
    }

    public var baseURL: URL {
        get { backend.baseURL }
        set { backend.baseURL = newValue }
    }

    public var sessionToken: String? {
        get { backend.sessionToken }
        set {
            if backend.sessionToken != newValue {
                currentBackendUserID = nil
            }
            backend.sessionToken = newValue
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
        try await backend.logout()
    }

    public func disconnectGoogle(deleteData: Bool, revokeSessions: Bool) async throws {
        try await backend.disconnectGoogle(deleteData: deleteData, revokeSessions: revokeSessions)
    }

    public func deleteGoogleData() async throws {
        try await backend.deleteGoogleData()
    }

    public func deleteAccount() async throws {
        try await backend.deleteAccount()
    }

    public func appSession() async throws -> AppSessionResponse {
        let expectedSessionToken = backend.sessionToken
        let response = try await refreshLocalSession(expectedSessionToken: expectedSessionToken)
        await replayPendingThreadActions(for: response.user.id, expectedSessionToken: expectedSessionToken)
        try validateSessionToken(expectedSessionToken)
        return response
    }

    public func mailbox(label: MailboxLabel, limit: Int, cursor: String?) async throws -> MailboxResponse {
        let expectedSessionToken = backend.sessionToken
        let userID = await resolvedBackendUserID()
        do {
            let response = try await backend.mailbox(label: label, limit: limit, cursor: cursor)
            try validateSessionToken(expectedSessionToken)
            if let userID {
                localMailStore.writeMailbox(response, userID: userID, label: label)
            }
            return response
        } catch {
            try validateSessionToken(expectedSessionToken)
            if let userID, let cached = localMailStore.readMailbox(userID: userID, label: label) {
                return cached
            }
            throw error
        }
    }

    public func mailboxFolderCount(label: MailboxLabel) async throws -> MailboxResponse {
        let expectedSessionToken = backend.sessionToken
        let userID = await resolvedBackendUserID()
        do {
            let response = try await backend.mailboxFolderCount(label: label)
            try validateSessionToken(expectedSessionToken)
            return response
        } catch {
            try validateSessionToken(expectedSessionToken)
            if let userID, let cached = localMailStore.readMailbox(userID: userID, label: label) {
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
        try await backend.searchMailbox(
            query: query,
            label: label,
            limit: limit,
            cursor: cursor,
            hydrateInBackground: hydrateInBackground
        )
    }

    public func thread(threadID: String, limit: Int, offset: Int) async throws -> ThreadReaderResponse {
        let expectedSessionToken = backend.sessionToken
        let expectedUserID = currentBackendUserID ?? localMailStore.readSession()?.user.id
        let response = try await backend.thread(threadID: threadID, limit: limit, offset: offset)
        try validateSessionToken(expectedSessionToken)
        if let expectedUserID, response.userID != expectedUserID {
            throw APIError.emptyResponse
        }
        localMailStore.writeThread(response, userID: response.userID, threadID: threadID)
        return response
    }

    public func mailboxSyncState() async throws -> MailboxSyncStateResponse {
        try await backend.mailboxSyncState()
    }

    public func triggerMailboxSync() async throws -> MailboxSyncTriggerResponse {
        await replayPendingThreadActionsForCurrentUser()
        return try await backend.triggerMailboxSync()
    }

    public func syncMailboxNow() async throws -> MailboxSyncTriggerResponse {
        await replayPendingThreadActionsForCurrentUser()
        return try await backend.syncMailboxNow()
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
        do {
            let response = try await backend.enqueueThreadAction(request)
            localMailStore.removePendingThreadAction(clientActionID: request.clientActionID)
            return response
        } catch {
            guard Self.isRetryableOfflineActionError(error) else {
                throw error
            }
            if let userID = localMailStore.readSession()?.user.id {
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
        try await backend.sendCompose(request)
    }

    public func sendReply(threadID: String, request: MailReplyRequest) async throws -> MailSendResponse {
        try await backend.sendReply(threadID: threadID, request: request)
    }

    public func outbox(limit: Int) async throws -> MailOutboxResponse {
        try await backend.outbox(limit: limit)
    }

    public func sendStatus(serverSendID: String) async throws -> MailSendResponse {
        try await backend.sendStatus(serverSendID: serverSendID)
    }

    public func retrySend(serverSendID: String) async throws -> MailSendResponse {
        try await backend.retrySend(serverSendID: serverSendID)
    }

    public func createDraft(_ request: MailDraftSaveRequest) async throws -> MailDraftResponse {
        try await backend.createDraft(request)
    }

    public func draft(mailboxThreadID: String) async throws -> MailDraftResponse {
        try await backend.draft(mailboxThreadID: mailboxThreadID)
    }

    public func updateDraft(gmailDraftID: String, request: MailDraftSaveRequest) async throws -> MailDraftResponse {
        try await backend.updateDraft(gmailDraftID: gmailDraftID, request: request)
    }

    public func deleteDraft(gmailDraftID: String) async throws {
        try await backend.deleteDraft(gmailDraftID: gmailDraftID)
    }

    public func sendDraft(gmailDraftID: String, request: MailDraftSendRequest) async throws -> MailSendResponse {
        try await backend.sendDraft(gmailDraftID: gmailDraftID, request: request)
    }

    public func downloadAttachment(messageID: String, attachment: ThreadAttachment) async throws -> DownloadedAttachment {
        try await backend.downloadAttachment(messageID: messageID, attachment: attachment)
    }

    public func createTask(_ request: TaskCreateRequest) async throws -> TaskResponse {
        try await backend.createTask(request)
    }

    public func updateTask(_ taskID: String, request: TaskUpdateRequest) async throws -> TaskResponse {
        try await backend.updateTask(taskID, request: request)
    }

    public func completeEntity(_ entityID: String, request: EntityOutcomeRequest) async throws -> EntityOutcomeResponse {
        try await backend.completeEntity(entityID, request: request)
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

    private func refreshLocalSession(expectedSessionToken: String?) async throws -> AppSessionResponse {
        let response = try await backend.appSession()
        try validateSessionToken(expectedSessionToken)
        currentBackendUserID = response.user.id
        localMailStore.writeSession(response)
        return response
    }

    private func resolvedBackendUserID() async -> String? {
        if let currentBackendUserID {
            return currentBackendUserID
        }
        let expectedSessionToken = backend.sessionToken
        return try? await refreshLocalSession(expectedSessionToken: expectedSessionToken).user.id
    }

    private func replayPendingThreadActionsForCurrentUser() async {
        let expectedSessionToken = backend.sessionToken
        guard let userID = await resolvedBackendUserID() else {
            return
        }
        guard backend.sessionToken == expectedSessionToken else {
            return
        }
        await replayPendingThreadActions(for: userID, expectedSessionToken: expectedSessionToken)
    }

    private func replayPendingThreadActions(for userID: String, expectedSessionToken: String?) async {
        for action in localMailStore.pendingThreadActions() {
            guard backend.sessionToken == expectedSessionToken else {
                return
            }
            guard action.userID == userID else {
                continue
            }
            do {
                _ = try await backend.enqueueThreadAction(action.request)
                guard backend.sessionToken == expectedSessionToken else {
                    return
                }
                localMailStore.removePendingThreadAction(clientActionID: action.clientActionID)
            } catch {
                guard backend.sessionToken == expectedSessionToken else {
                    return
                }
                if Self.shouldDiscardReplayedOfflineAction(error) {
                    localMailStore.removePendingThreadAction(clientActionID: action.clientActionID)
                    continue
                }
                localMailStore.markPendingThreadActionFailed(clientActionID: action.clientActionID, error: error.localizedDescription)
                return
            }
        }
    }

    private func validateSessionToken(_ expectedSessionToken: String?) throws {
        guard backend.sessionToken == expectedSessionToken else {
            throw CancellationError()
        }
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
