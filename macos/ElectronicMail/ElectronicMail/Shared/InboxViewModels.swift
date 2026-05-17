import Foundation

public struct MobileInboxSnapshot: Equatable {
    public let totalThreads: Int
    public let sections: [MobileInboxSectionViewModel]
    public let fullImportRunning: Bool

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
    public let sender: String
    public let subject: String
    public let timeLabel: String
    public let unread: Bool
    public let grouped: Bool
    public let dimmed: Bool
}

public enum MobileInboxViewModelBuilder {
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
                            sender: row.displaySender,
                            subject: row.displayTitle,
                            timeLabel: timeLabel(for: row.latestReceivedAt, sectionTitle: section.title),
                            unread: row.isUnread,
                            grouped: row.isGrouped,
                            dimmed: row.currentState == .done || row.outcomeType == "done" || row.outcomeType == "complete"
                        )
                    }
                )
            },
            fullImportRunning: mailbox.fullImportRunning == true
        )
    }

    private static func timeLabel(for value: String, sectionTitle: String) -> String {
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
