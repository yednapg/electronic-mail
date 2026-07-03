import Foundation

public struct MobileInboxSnapshot: Equatable {
    public let totalThreads: Int
    public let sections: [MobileInboxSectionViewModel]
    public let fullImportRunning: Bool
    public let smartReadiness: SmartReadinessResponse?

    public var isEmpty: Bool {
        totalThreads == 0 || sections.allSatisfy { $0.rows.isEmpty }
    }
}

public struct MobileInboxSectionViewModel: Equatable, Identifiable {
    public let id: String
    public let title: String
    public let rows: [MobileInboxRowViewModel]
}

public struct MobileInboxRowViewModel: Equatable, Identifiable {
    public let id: String
    public let entityID: String?
    public let threadID: String
    public let sender: String
    public let subject: String
    public let summary: String?
    public let timeLabel: String
    public let unread: Bool
    public let grouped: Bool
    public let dimmed: Bool
    public let badgeLabel: String?
    public let offlineReady: Bool
}

public enum MobileInboxViewModelBuilder {
    public static func snapshot(from session: AppSessionResponse) -> MobileInboxSnapshot {
        if let smartInbox = session.smartInbox, !smartInbox.isEmpty {
            return snapshot(
                from: smartInbox,
                readiness: session.smartReadiness,
                fallbackImportRunning: session.mailbox.fullImportRunning == true
            )
        }

        return snapshot(from: session.mailbox)
    }

    public static func snapshot(from mailbox: MailboxResponse) -> MobileInboxSnapshot {
        MobileInboxSnapshot(
            totalThreads: mailbox.totalThreads,
            sections: mailbox.sections.map { section in
                MobileInboxSectionViewModel(
                    id: section.id,
                    title: section.title,
                    rows: section.rows.map { row in
                        MobileInboxRowViewModel(
                            id: row.threadID,
                            entityID: row.entityID,
                            threadID: row.threadID,
                            sender: row.displaySender,
                            subject: row.displayTitle,
                            summary: nil,
                            timeLabel: timeLabel(for: row.latestReceivedAt, sectionTitle: section.title),
                            unread: row.isUnread,
                            grouped: row.isGrouped,
                            dimmed: row.currentState == .done || row.outcomeType == "done" || row.outcomeType == "complete",
                            badgeLabel: row.isGrouped ? "\(max(row.messageCount, 1)) grouped" : nil,
                            offlineReady: false
                        )
                    }
                )
            },
            fullImportRunning: mailbox.fullImportRunning == true,
            smartReadiness: nil
        )
    }

    private static func snapshot(
        from smartInbox: SmartInboxResponse,
        readiness: SmartReadinessResponse?,
        fallbackImportRunning: Bool
    ) -> MobileInboxSnapshot {
        MobileInboxSnapshot(
            totalThreads: smartInbox.totalRows,
            sections: smartInbox.sections.map { section in
                MobileInboxSectionViewModel(
                    id: section.id,
                    title: section.title,
                    rows: section.rows.map { row in
                        MobileInboxRowViewModel(
                            id: row.id,
                            entityID: row.id,
                            threadID: row.primaryThreadID,
                            sender: row.displaySender,
                            subject: row.displayTitle,
                            summary: nil,
                            timeLabel: timeLabel(for: row.latestMessageAt, sectionTitle: section.title),
                            unread: false,
                            grouped: row.isGrouped,
                            dimmed: row.actionType == "none",
                            badgeLabel: badgeLabel(for: row),
                            offlineReady: row.offlineStatus == "ready"
                        )
                    }
                )
            },
            fullImportRunning: fallbackImportRunning || readiness?.hotWindowComplete == false,
            smartReadiness: readiness
        )
    }

    private static func badgeLabel(for row: SmartInboxRow) -> String? {
        switch row.rowType {
        case "verified_group":
            return "\(max(row.sourceMessageIDs.count, 1)) grouped"
        case "related_bundle":
            return "Related"
        case "waiting":
            return "Waiting"
        case "update":
            return "Update"
        case "summarized_thread":
            return "Summary"
        default:
            return row.offlineStatus == "ready" ? "Offline" : nil
        }
    }

    private static func timeLabel(for value: String?, sectionTitle: String) -> String {
        guard let value else {
            return ""
        }
        guard let date = ISO8601DateFormatter.inboxBackend.date(from: value) else {
            return ""
        }

        if sectionTitle.localizedCaseInsensitiveContains("today") {
            return DateFormatter.inboxTime.string(from: date)
        }

        if sectionTitle.localizedCaseInsensitiveContains("yesterday") {
            return "Yesterday"
        }

        return DateFormatter.inboxDate.string(from: date)
    }
}

private extension DateFormatter {
    static let inboxTime: DateFormatter = {
        let formatter = DateFormatter()
        formatter.timeStyle = .short
        formatter.dateStyle = .none
        return formatter
    }()

    static let inboxDate: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "MMM d"
        return formatter
    }()
}

private extension ISO8601DateFormatter {
    static let inboxBackend: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withColonSeparatorInTimeZone]
        return formatter
    }()
}
