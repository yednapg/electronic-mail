import Foundation

public final class OfflineFirstAppClient: AppClient {
    private let backend: AppClient
    private let localMailStore: LocalMailStore

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
        set { backend.sessionToken = newValue }
    }

    public var mode: AppRunMode {
        backend.mode
    }

    public func exchangeMobileSession(loginCode: String) async throws -> MobileSessionExchangeResponse {
        try await backend.exchangeMobileSession(loginCode: loginCode)
    }

    public func appSession() async throws -> AppSessionResponse {
        await replayPendingThreadActions()
        let response = try await backend.appSession()
        localMailStore.writeSession(response)
        return response
    }

    public func mailbox(label: MailboxLabel, limit: Int, cursor: String?) async throws -> MailboxResponse {
        let userID = localMailStore.readSession()?.user.id
        do {
            let response = try await backend.mailbox(label: label, limit: limit, cursor: cursor)
            if let userID {
                localMailStore.writeMailbox(response, userID: userID, label: label)
            }
            return response
        } catch {
            if let userID, let cached = localMailStore.readMailbox(userID: userID, label: label) {
                return cached
            }
            throw error
        }
    }

    public func thread(threadID: String, limit: Int, offset: Int) async throws -> ThreadReaderResponse {
        let response = try await backend.thread(threadID: threadID, limit: limit, offset: offset)
        localMailStore.writeThread(response, userID: response.userID, threadID: threadID)
        return response
    }

    public func mailboxSyncState() async throws -> MailboxSyncStateResponse {
        try await backend.mailboxSyncState()
    }

    public func triggerMailboxSync() async throws -> MailboxSyncTriggerResponse {
        await replayPendingThreadActions()
        return try await backend.triggerMailboxSync()
    }

    public func syncMailboxNow() async throws -> MailboxSyncTriggerResponse {
        await replayPendingThreadActions()
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

    private func replayPendingThreadActions() async {
        for action in localMailStore.pendingThreadActions() {
            do {
                _ = try await backend.enqueueThreadAction(action.request)
                localMailStore.removePendingThreadAction(clientActionID: action.clientActionID)
            } catch {
                localMailStore.markPendingThreadActionFailed(clientActionID: action.clientActionID, error: error.localizedDescription)
                return
            }
        }
    }
}

private extension ISO8601DateFormatter {
    static let backendActionTimestamp: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()
}
