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
    func writeMailbox(
        _ mailbox: MailboxResponse,
        userID: String,
        label: MailboxLabel,
        removingThreadIDs: [String],
        reenteringThreadIDs: [String],
        reentryLabels: [MailboxLabel]
    )
    func readThread(userID: String, threadID: String) -> ThreadReaderResponse?
    func writeThread(_ thread: ThreadReaderResponse, userID: String, threadID: String)
    func removeThread(userID: String, threadID: String)
    func purgeAccount(userID: String)
    func writePendingThreadAction(_ action: LocalPendingThreadAction)
    func pendingThreadActions() -> [LocalPendingThreadAction]
    func removePendingThreadAction(clientActionID: String)
    func markPendingThreadActionFailed(clientActionID: String, error: String)
    func clearSession()
    func clearAll()
}

public extension LocalMailStore {
    func writeMailbox(
        _ mailbox: MailboxResponse,
        userID: String,
        label: MailboxLabel,
        removingThreadIDs _: [String],
        reenteringThreadIDs _: [String],
        reentryLabels _: [MailboxLabel]
    ) {
        writeMailbox(mailbox, userID: userID, label: label)
    }

    func removeThread(userID _: String, threadID _: String) {}
    func purgeAccount(userID _: String) {}
    func clearSession() {}
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

    public func removeThread(userID: String, threadID: String) {}

    public func purgeAccount(userID: String) {}

    public func writePendingThreadAction(_ action: LocalPendingThreadAction) {}

    public func pendingThreadActions() -> [LocalPendingThreadAction] {
        []
    }

    public func removePendingThreadAction(clientActionID: String) {}

    public func markPendingThreadActionFailed(clientActionID: String, error: String) {}

    public func clearSession() {}

    public func clearAll() {}
}

public final class MemoryLocalMailStore: LocalMailStore {
    private struct MailboxTombstone {
        let progressDate: Date?
        let mailboxRevision: String?
    }

    private var currentUserID: String?
    private var sessions: [String: AppSessionResponse] = [:]
    private var mailboxes: [String: [MailboxLabel: MailboxResponse]] = [:]
    private var mailboxTombstones: [String: [MailboxLabel: [String: [String: MailboxTombstone]]]] = [:]
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
        let generation = Self.mailboxGenerationScope(mailbox)
        var userTombstones = mailboxTombstones[userID] ?? [:]
        var labelTombstones = userTombstones[label] ?? [:]
        labelTombstones = labelTombstones.filter { $0.key == generation }
        var generationTombstones = labelTombstones[generation] ?? [:]
        Self.clearTombstonesForNewerAuthoritativeInclusion(
            &generationTombstones,
            mailbox: mailbox
        )
        labelTombstones[generation] = generationTombstones
        userTombstones[label] = labelTombstones
        mailboxTombstones[userID] = userTombstones

        var userMailboxes = mailboxes[userID] ?? [:]
        userMailboxes[label] = mailbox.removingCachedThreadIDs(Set(generationTombstones.keys))
        mailboxes[userID] = userMailboxes
    }

    public func writeMailbox(
        _ mailbox: MailboxResponse,
        userID: String,
        label: MailboxLabel,
        removingThreadIDs: [String],
        reenteringThreadIDs: [String],
        reentryLabels: [MailboxLabel]
    ) {
        guard mailbox.label == label else {
            return
        }
        let generation = Self.mailboxGenerationScope(mailbox)
        var userTombstones = mailboxTombstones[userID] ?? [:]
        var labelTombstones = userTombstones[label] ?? [:]
        labelTombstones = labelTombstones.filter { $0.key == generation }
        var generationTombstones = labelTombstones[generation] ?? [:]
        let removalProgressDate = Self.mailboxProgressDate(mailbox)
        let removalRevision = Self.normalizedMailboxRevision(mailbox.mailboxRevision)
        for threadID in removingThreadIDs {
            generationTombstones[threadID] = MailboxTombstone(
                progressDate: removalProgressDate,
                mailboxRevision: removalRevision
            )
        }
        Self.clearTombstonesForNewerAuthoritativeInclusion(
            &generationTombstones,
            mailbox: mailbox
        )
        labelTombstones[generation] = generationTombstones
        userTombstones[label] = labelTombstones
        for reentryLabel in reentryLabels {
            var destinationTombstones = userTombstones[reentryLabel] ?? [:]
            for threadID in reenteringThreadIDs {
                destinationTombstones[generation]?[threadID] = nil
            }
            userTombstones[reentryLabel] = destinationTombstones
        }
        mailboxTombstones[userID] = userTombstones

        var userMailboxes = mailboxes[userID] ?? [:]
        let activeTombstones = userTombstones[label]?[generation] ?? [:]
        userMailboxes[label] = mailbox.removingCachedThreadIDs(Set(activeTombstones.keys))
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

    public func removeThread(userID: String, threadID: String) {
        threads[userID]?[threadID] = nil
        if threads[userID]?.isEmpty == true {
            threads[userID] = nil
        }
    }

    public func purgeAccount(userID: String) {
        sessions[userID] = nil
        mailboxes[userID] = nil
        mailboxTombstones[userID] = nil
        threads[userID] = nil
        actions = actions.filter { $0.value.userID != userID }
        if currentUserID == userID {
            currentUserID = nil
        }
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

    public func clearSession() {
        currentUserID = nil
        sessions = [:]
    }

    public func clearAll() {
        currentUserID = nil
        sessions = [:]
        mailboxes = [:]
        mailboxTombstones = [:]
        threads = [:]
        actions = [:]
    }

    private static func mailboxGenerationScope(_ mailbox: MailboxResponse) -> String {
        if let generation = mailbox.syncGeneration?.trimmingCharacters(in: .whitespacesAndNewlines),
           !generation.isEmpty {
            return "generation:\(generation)"
        }
        if let revision = mailbox.mailboxRevision?.trimmingCharacters(in: .whitespacesAndNewlines),
           !revision.isEmpty {
            return "legacy-revision:\(revision)"
        }
        return "legacy-ungenerated"
    }

    private static func clearTombstonesForNewerAuthoritativeInclusion(
        _ tombstones: inout [String: MailboxTombstone],
        mailbox: MailboxResponse
    ) {
        guard isAuthoritativeMailboxSnapshot(mailbox),
              let incomingProgressDate = mailboxProgressDate(mailbox),
              let incomingRevision = normalizedMailboxRevision(mailbox.mailboxRevision) else {
            return
        }
        let includedThreadIDs = Set(mailbox.sections.flatMap(\.rows).map(\.threadID))
        tombstones = tombstones.filter { threadID, tombstone in
            guard includedThreadIDs.contains(threadID),
                  let removalProgressDate = tombstone.progressDate,
                  let removalRevision = tombstone.mailboxRevision,
                  incomingRevision != removalRevision else {
                return true
            }
            return incomingProgressDate <= removalProgressDate
        }
    }

    private static func isAuthoritativeMailboxSnapshot(_ mailbox: MailboxResponse) -> Bool {
        if mailbox.syncGeneration?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty == false,
           mailbox.historyMetadataComplete != true {
            return false
        }
        guard mailbox.fullImportRunning != true,
              mailbox.fullImportCompleted != false,
              mailbox.nextCursor?.isEmpty != false else {
            return false
        }
        let visibleRows = mailbox.sections.reduce(0) { $0 + $1.rows.count }
        let loadedRows = max(mailbox.loadedThreads ?? visibleRows, visibleRows)
        return visibleRows >= mailbox.totalThreads && loadedRows >= mailbox.totalThreads
    }

    private static func mailboxProgressDate(_ mailbox: MailboxResponse) -> Date? {
        [mailbox.lastProgressAt, mailbox.generatedAt]
            .compactMap(timestampDate)
            .max()
    }

    private static func normalizedMailboxRevision(_ revision: String?) -> String? {
        guard let revision = revision?.trimmingCharacters(in: .whitespacesAndNewlines),
              !revision.isEmpty else {
            return nil
        }
        return revision
    }

    private static func timestampDate(_ value: String?) -> Date? {
        guard let value, !value.isEmpty else {
            return nil
        }
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return fractional.date(from: value) ?? ISO8601DateFormatter().date(from: value)
    }
}

extension MailboxResponse {
    func removingCachedThreadIDs(_ threadIDs: Set<String>) -> MailboxResponse {
        guard !threadIDs.isEmpty else {
            return self
        }
        let originalRows = sections.flatMap(\.rows)
        let removedRows = originalRows.filter { threadIDs.contains($0.threadID) }
        guard !removedRows.isEmpty else {
            return self
        }
        let nextSections = sections.compactMap { section -> GmailThreadSection? in
            let rows = section.rows.filter { !threadIDs.contains($0.threadID) }
            guard !rows.isEmpty else {
                return nil
            }
            return GmailThreadSection(id: section.id, title: section.title, rows: rows)
        }
        let visibleRows = nextSections.reduce(0) { $0 + $1.rows.count }
        let removedUnread = removedRows.filter(\.isUnread).count
        return MailboxResponse(
            label: label,
            totalThreads: max(visibleRows, totalThreads - removedRows.count),
            unreadThreads: unreadThreads.map { max(0, $0 - removedUnread) },
            nextCursor: nextCursor,
            loadedThreads: loadedThreads.map { max(visibleRows, $0 - removedRows.count) },
            windowDays: windowDays,
            sections: nextSections,
            readyCount: readyCount,
            pendingCount: pendingCount,
            mailboxRevision: mailboxRevision,
            generatedAt: generatedAt,
            oldestImportedAt: oldestImportedAt,
            fullImportRunning: fullImportRunning,
            fullImportCompleted: fullImportCompleted,
            syncGeneration: syncGeneration,
            phase: phase,
            initialTargetCount: initialTargetCount,
            initialMetadataCount: initialMetadataCount,
            initialBodyTargetCount: initialBodyTargetCount,
            initialBodyReadyCount: initialBodyReadyCount,
            historyMetadataCount: historyMetadataCount,
            historyBodyReadyCount: historyBodyReadyCount,
            estimatedTotalCount: estimatedTotalCount,
            initialWindowComplete: initialWindowComplete,
            historyMetadataComplete: historyMetadataComplete,
            historyBodyComplete: historyBodyComplete,
            lastProgressAt: lastProgressAt
        )
    }
}
