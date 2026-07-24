import Foundation

public struct LocalPendingThreadAction: Codable, Equatable {
    let clientActionID: String
    let userID: String
    let mailboxThreadID: String
    let targetMessageID: String?
    let action: GmailThreadAction
    let createdAt: String
    let error: String?

    var request: QueuedThreadActionRequest {
        QueuedThreadActionRequest(
            clientActionID: clientActionID,
            mailboxThreadID: mailboxThreadID,
            targetMessageID: targetMessageID,
            action: action,
            createdAt: createdAt
        )
    }
}

public protocol LocalMailStore: AnyObject {
    func readSession() -> AppSessionResponse?
    func writeSession(_ session: AppSessionResponse)
    func readMailbox(userID: String, label: MailboxLabel) -> MailboxResponse?
    func writeMailbox(_ mailbox: MailboxResponse, userID: String, label: MailboxLabel)
    func readThread(userID: String, threadID: String) -> ThreadReaderResponse?
    func writeThread(_ thread: ThreadReaderResponse, userID: String, threadID: String)
    func writePendingThreadAction(_ action: LocalPendingThreadAction)
    func pendingThreadActions() -> [LocalPendingThreadAction]
    func removePendingThreadAction(clientActionID: String)
    func markPendingThreadActionFailed(clientActionID: String, error: String)
    func clearAll()
}

public final class NoopLocalMailStore: LocalMailStore {
    public init() {}

    public func readSession() -> AppSessionResponse? {
        nil
    }

    public func writeSession(_ session: AppSessionResponse) {}

    public func readMailbox(userID: String, label: MailboxLabel) -> MailboxResponse? {
        nil
    }

    public func writeMailbox(_ mailbox: MailboxResponse, userID: String, label: MailboxLabel) {}

    public func readThread(userID: String, threadID: String) -> ThreadReaderResponse? {
        nil
    }

    public func writeThread(_ thread: ThreadReaderResponse, userID: String, threadID: String) {}

    public func writePendingThreadAction(_ action: LocalPendingThreadAction) {}

    public func pendingThreadActions() -> [LocalPendingThreadAction] {
        []
    }

    public func removePendingThreadAction(clientActionID: String) {}

    public func markPendingThreadActionFailed(clientActionID: String, error: String) {}

    public func clearAll() {}
}

public final class MemoryLocalMailStore: LocalMailStore {
    private var currentUserID: String?
    private var sessions: [String: AppSessionResponse] = [:]
    private var mailboxes: [String: [MailboxLabel: MailboxResponse]] = [:]
    private var threads: [String: [String: ThreadReaderResponse]] = [:]
    private var actions: [String: LocalPendingThreadAction] = [:]

    public init() {}

    public func readSession() -> AppSessionResponse? {
        guard let currentUserID else {
            return nil
        }
        return sessions[currentUserID]
    }

    public func writeSession(_ session: AppSessionResponse) {
        currentUserID = session.user.id
        sessions[session.user.id] = session
    }

    public func readMailbox(userID: String, label: MailboxLabel) -> MailboxResponse? {
        guard let mailbox = mailboxes[userID]?[label], mailbox.label == label else {
            return nil
        }
        return mailbox
    }

    public func writeMailbox(_ mailbox: MailboxResponse, userID: String, label: MailboxLabel) {
        guard mailbox.label == label else {
            return
        }
        var userMailboxes = mailboxes[userID] ?? [:]
        userMailboxes[label] = mailbox
        mailboxes[userID] = userMailboxes
    }

    public func readThread(userID: String, threadID: String) -> ThreadReaderResponse? {
        threads[userID]?[threadID]
    }

    public func writeThread(_ thread: ThreadReaderResponse, userID: String, threadID: String) {
        var userThreads = threads[userID] ?? [:]
        userThreads[threadID] = thread
        threads[userID] = userThreads
    }

    public func writePendingThreadAction(_ action: LocalPendingThreadAction) {
        actions[action.clientActionID] = action
    }

    public func pendingThreadActions() -> [LocalPendingThreadAction] {
        actions.values.sorted { $0.createdAt < $1.createdAt }
    }

    public func removePendingThreadAction(clientActionID: String) {
        actions[clientActionID] = nil
    }

    public func markPendingThreadActionFailed(clientActionID: String, error: String) {
        guard let action = actions[clientActionID] else {
            return
        }
        actions[clientActionID] = LocalPendingThreadAction(
            clientActionID: action.clientActionID,
            userID: action.userID,
            mailboxThreadID: action.mailboxThreadID,
            targetMessageID: action.targetMessageID,
            action: action.action,
            createdAt: action.createdAt,
            error: error
        )
    }

    public func clearAll() {
        currentUserID = nil
        sessions = [:]
        mailboxes = [:]
        threads = [:]
        actions = [:]
    }
}
