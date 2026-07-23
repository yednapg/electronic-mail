import Foundation

public struct DashboardSnapshot: Equatable {
    public let userDisplayName: String?
    public let dateLabel: String
    public let timeLabel: String
    public let summary: DashboardSummaryViewModel
    public let agenda: [DashboardAgendaItemViewModel]
    public let sections: [DashboardSectionViewModel]
    public let fullImportRunning: Bool
    public let refreshWarning: String?

    public var hasVisibleWork: Bool {
        !agenda.isEmpty || sections.contains { !$0.items.isEmpty }
    }
}

public struct DashboardSummaryViewModel: Equatable {
    public let headline: String
    public let brief: String
    public let parts: [DashboardSummaryPartViewModel]
    public let important: DashboardSummaryCalloutViewModel?
    public let calendarAvailability: DashboardSummaryCalloutViewModel?
}

public struct DashboardSummaryPartViewModel: Equatable, Identifiable {
    public let id: String
    public let emoji: String
    public let count: Int
    public let text: String
}

public struct DashboardSummaryCalloutViewModel: Equatable {
    public let emoji: String
    public let text: String
    public let count: Int?
}

public struct DashboardAgendaItemViewModel: Equatable, Identifiable {
    public enum Tone: String, Equatable {
        case blue
        case green
        case teal
        case lime
    }

    public let id: String
    public let time: String
    public let title: String
    public let allDay: Bool
    public let tone: Tone
}

public struct DashboardSectionViewModel: Equatable, Identifiable {
    public let id: String
    public let title: String
    public let items: [DashboardSectionItemViewModel]
    public let maxVisible: Int?
}

public struct DashboardSectionItemViewModel: Equatable, Identifiable {
    public let id: String
    public let entityID: String
    public let title: String
    public let primaryAction: String
    public let needType: String
    public let source: String?
    public let detail: DashboardItemDetailViewModel?
    public let action: DashboardItemActionViewModel?
}

public struct DashboardItemDetailViewModel: Equatable {
    public let body: [String]
    public let actionLabel: String
    public let confirmLabel: String
    public let dismissLabel: String
    public let sourceLabel: String
}

public struct DashboardItemActionViewModel: Equatable {
    public let label: String
    public let tone: DashboardActionTone
    public let operation: DashboardActionOperation?
    public let gmailThreadID: String?

    public var canRunOnBackend: Bool {
        operation != nil && gmailThreadID?.isEmpty == false
    }
}

public enum DashboardActionTone: Equatable {
    case blue
    case green
}

public enum DashboardActionOperation: Equatable {
    case archive
    case unarchive
    case markRead
}

public enum DashboardViewModelBuilder {
    public static func snapshot(
        from session: AppSessionResponse,
        now: Date = Date(),
        hiddenItemIDs: Set<String> = [],
        refreshWarning: String? = nil
    ) -> DashboardSnapshot {
        DashboardSnapshot(
            userDisplayName: session.user.displayName ?? session.user.firstName,
            dateLabel: DateFormatter.dashboardDate.string(from: now),
            timeLabel: DateFormatter.dashboardTime.string(from: now),
            summary: summary(from: session.dashboard),
            agenda: agenda(from: session.dashboard.feed),
            sections: sections(from: session.dashboard.feed, hiddenItemIDs: hiddenItemIDs),
            fullImportRunning: session.mailbox.fullImportRunning ?? session.sync.fullImportRunning,
            refreshWarning: refreshWarning
        )
    }

    public static func summary(from dashboard: DashboardResponse) -> DashboardSummaryViewModel {
        guard let briefing = dashboard.briefing else {
            return DashboardSummaryViewModel(
                headline: "Dashboard",
                brief: "Connect Google to generate a personalized briefing.",
                parts: [],
                important: nil,
                calendarAvailability: nil
            )
        }

        return DashboardSummaryViewModel(
            headline: briefing.headline,
            brief: briefing.brief,
            parts: briefing.parts?.map { part in
                DashboardSummaryPartViewModel(
                    id: part.type,
                    emoji: part.emoji,
                    count: part.count,
                    text: part.text
                )
            } ?? [],
            important: briefing.important.map { important in
                DashboardSummaryCalloutViewModel(
                    emoji: important.emoji,
                    text: important.text,
                    count: important.count
                )
            },
            calendarAvailability: briefing.calendarAvailability.map { availability in
                DashboardSummaryCalloutViewModel(
                    emoji: availability.emoji,
                    text: availability.text,
                    count: nil
                )
            }
        )
    }

    public static func agenda(from feed: FeedResponse) -> [DashboardAgendaItemViewModel] {
        let visibleItems = feed.now + feed.today + feed.worthKnowing
        return visibleItems
            .compactMap(toAgendaItem)
            .sorted { left, right in
                if left.sortValue != right.sortValue {
                    return left.sortValue < right.sortValue
                }
                if left.allDay != right.allDay {
                    return left.allDay && !right.allDay
                }
                return left.time.localizedCaseInsensitiveCompare(right.time) == .orderedAscending
            }
            .enumerated()
            .map { index, item in
                DashboardAgendaItemViewModel(
                    id: item.id,
                    time: item.time,
                    title: item.title,
                    allDay: item.allDay,
                    tone: tone(for: item.timingBand, index: index)
                )
            }
    }

    public static func sections(from feed: FeedResponse, hiddenItemIDs: Set<String> = []) -> [DashboardSectionViewModel] {
        [
            DashboardSectionViewModel(
                id: "now",
                title: "Now",
                items: sectionItems(from: feed.now, hiddenItemIDs: hiddenItemIDs),
                maxVisible: 6
            ),
            DashboardSectionViewModel(
                id: "today",
                title: "Today",
                items: sectionItems(from: feed.today, hiddenItemIDs: hiddenItemIDs),
                maxVisible: 5
            ),
            DashboardSectionViewModel(
                id: "worth-knowing",
                title: "Worth Knowing",
                items: sectionItems(from: feed.worthKnowing, hiddenItemIDs: hiddenItemIDs),
                maxVisible: 3
            ),
        ]
    }

    private static func sectionItems(from items: [AttentionItem], hiddenItemIDs: Set<String>) -> [DashboardSectionItemViewModel] {
        sortSectionFeedItems(items)
            .filter { $0.source != .calendar }
            .filter { !hiddenItemIDs.contains($0.id) }
            .map(toSectionItem)
    }

    private static func toSectionItem(_ item: AttentionItem) -> DashboardSectionItemViewModel {
        DashboardSectionItemViewModel(
            id: item.id,
            entityID: item.entityID,
            title: item.needType == .awareness && item.source == .calendar && !item.whyThisIsHere.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? item.whyThisIsHere : item.title,
            primaryAction: item.primaryAction,
            needType: item.needType.rawValue,
            source: item.source?.rawValue,
            detail: detail(from: item),
            action: action(from: item)
        )
    }

    private static func detail(from item: AttentionItem) -> DashboardItemDetailViewModel? {
        guard item.source == .gmail else {
            return nil
        }

        if let detail = item.detail {
            return DashboardItemDetailViewModel(
                body: detail.body.isEmpty ? [detailDescription(from: item)] : detail.body,
                actionLabel: detail.actionLabel,
                confirmLabel: primaryActionDoneLabel(item.primaryAction),
                dismissLabel: "Not needed",
                sourceLabel: normalizedSourceLabel(detail.sourceLabel)
            )
        }

        return DashboardItemDetailViewModel(
            body: [detailDescription(from: item)],
            actionLabel: nextMoveLabel(for: item),
            confirmLabel: primaryActionDoneLabel(item.primaryAction),
            dismissLabel: "Not needed",
            sourceLabel: sourceLabel(for: item)
        )
    }

    private static func action(from item: AttentionItem) -> DashboardItemActionViewModel? {
        if let gmailAction = item.gmailThreadAction, let threadID = item.gmailThreadID, !threadID.isEmpty {
            switch gmailAction {
            case .archive:
                return DashboardItemActionViewModel(label: "Archive", tone: .green, operation: .archive, gmailThreadID: threadID)
            case .unarchive:
                return DashboardItemActionViewModel(label: "Unarchive", tone: .blue, operation: .unarchive, gmailThreadID: threadID)
            case .markRead:
                return DashboardItemActionViewModel(label: "Mark Read", tone: .green, operation: .markRead, gmailThreadID: threadID)
            case .markUnread, .moveTrash, .restoreTrash, .markSpam, .notSpam, .star, .unstar, .deleteForever:
                return nil
            }
        }

        if item.primaryAction == "confirm" && item.title.range(of: #"^within\b"#, options: [.regularExpression, .caseInsensitive]) != nil {
            return DashboardItemActionViewModel(label: "RSVP", tone: .blue, operation: nil, gmailThreadID: nil)
        }

        if item.primaryAction == "register" {
            return DashboardItemActionViewModel(label: "Register", tone: .blue, operation: nil, gmailThreadID: nil)
        }

        let lowerTitle = item.title.lowercased()
        if lowerTitle.contains("read notice") || lowerTitle.contains("ofs in ipo") {
            return DashboardItemActionViewModel(label: "Read Notice", tone: .green, operation: nil, gmailThreadID: nil)
        }

        return nil
    }

    private static func toAgendaItem(_ item: AttentionItem) -> SortableAgendaItem? {
        guard item.source == .calendar, let dueAt = item.dueAt, !dueAt.isEmpty else {
            return nil
        }

        let allDay = !isTimedTimestamp(dueAt)
        guard let sortValue = agendaSortValue(for: dueAt, allDay: allDay) else {
            return nil
        }

        return SortableAgendaItem(
            id: item.id,
            time: allDay ? "All day" : formattedScheduleTime(for: dueAt),
            title: actionSentence(for: item),
            allDay: allDay,
            timingBand: item.timingBand,
            sortValue: sortValue
        )
    }

    private static func sortSectionFeedItems(_ items: [AttentionItem]) -> [AttentionItem] {
        items.sorted { left, right in
            let leftDue = feedSortValue(left)
            let rightDue = feedSortValue(right)

            if let leftDue, let rightDue, leftDue != rightDue {
                return leftDue < rightDue
            }

            if leftDue != nil && rightDue == nil {
                return true
            }

            if leftDue == nil && rightDue != nil {
                return false
            }

            if left.source == .calendar && right.source != .calendar {
                return true
            }

            if left.source != .calendar && right.source == .calendar {
                return false
            }

            return false
        }
    }

    private static func feedSortValue(_ item: AttentionItem) -> TimeInterval? {
        guard let dueAt = item.dueAt, !dueAt.isEmpty else {
            return nil
        }
        return agendaSortValue(for: dueAt, allDay: !isTimedTimestamp(dueAt))
    }

    private static func agendaSortValue(for value: String, allDay: Bool) -> TimeInterval? {
        if allDay, let date = DateFormatter.dashboardDateOnly.date(from: value) {
            return Calendar(identifier: .gregorian).startOfDay(for: date).timeIntervalSince1970
        }

        if let date = ISO8601DateFormatter.dashboard.date(from: value) {
            return date.timeIntervalSince1970
        }

        return nil
    }

    private static func formattedScheduleTime(for value: String) -> String {
        guard let date = ISO8601DateFormatter.dashboard.date(from: value) else {
            return value
        }
        return DateFormatter.dashboardSchedule.string(from: date)
    }

    private static func isTimedTimestamp(_ value: String) -> Bool {
        value.contains("T")
    }

    private static func tone(for timingBand: TimingBand, index: Int) -> DashboardAgendaItemViewModel.Tone {
        switch timingBand {
        case .now:
            return .blue
        case .today:
            return .teal
        case .later, .hidden:
            return index.isMultiple(of: 2) ? .green : .lime
        }
    }

    private static func actionSentence(for item: AttentionItem) -> String {
        let title = item.title.trimmingCharacters(in: .whitespacesAndNewlines)
        if startsWithActionVerb(title) || shouldKeepNaturalTitle(item, title: title) {
            return title
        }

        switch item.primaryAction {
        case "reply":
            if title.lowercased() == "reply needed" {
                return "Reply to this thread"
            }
            return startsWithVerb(title, verbs: ["reply", "respond"]) ? title : "Reply about \(title)"
        case "confirm":
            if title.lowercased() == "rsvp needed" {
                return "Confirm this invite"
            }
            return startsWithVerb(title, verbs: ["confirm", "rsvp"]) ? title : "Confirm \(title)"
        case "pay":
            return startsWithVerb(title, verbs: ["pay"]) ? title : "Pay \(stripDuePrefix(title))"
        case "track":
            return startsWithVerb(title, verbs: ["track"]) ? title : "Track \(stripDuePrefix(title))"
        case "review":
            return "Review \(title)"
        case "join":
            return "Join \(title)"
        case "send":
            return "Send \(title)"
        case "approve":
            return "Approve \(title)"
        case "register":
            return "Register for \(title)"
        case "open":
            if title.hasPrefix("Due ") {
                return "Review item due \(title.dropFirst(4))"
            }
            return startsWithVerb(title, verbs: ["review", "open"]) ? title : "Review \(title)"
        default:
            return title
        }
    }

    private static func shouldKeepNaturalTitle(_ item: AttentionItem, title: String) -> Bool {
        if item.needType == .awareness {
            return true
        }
        if title.range(of: #"^within\b"#, options: [.regularExpression, .caseInsensitive]) != nil {
            return true
        }
        if title.range(of: #"\bis offering\b"#, options: [.regularExpression, .caseInsensitive]) != nil {
            return true
        }
        if title.range(of: #"[.!?]$"#, options: .regularExpression) != nil {
            return true
        }
        return title.range(
            of: #"^(you\b|your\b|you're\b|we\b|this\b|[A-Z][A-Za-z0-9&.'/-]+(?: [A-Z][A-Za-z0-9&.'/-]+){0,4} (?:updated|declined|approved|confirmed|registered|delivered|shipped|sent|accepted|resolved|says|changed|scheduled|is offering)\b)"#,
            options: [.regularExpression, .caseInsensitive]
        ) != nil
    }

    private static func startsWithActionVerb(_ title: String) -> Bool {
        startsWithVerb(title, verbs: [
            "reply",
            "respond",
            "confirm",
            "rsvp",
            "pay",
            "track",
            "review",
            "open",
            "join",
            "send",
            "approve",
            "register",
        ])
    }

    private static func startsWithVerb(_ title: String, verbs: [String]) -> Bool {
        let normalized = title.lowercased()
        return verbs.contains { normalized.hasPrefix($0) }
    }

    private static func stripDuePrefix(_ title: String) -> String {
        title.hasPrefix("Due ") ? "item due \(title.dropFirst(4))" : title
    }

    private static func detailDescription(from item: AttentionItem) -> String {
        let explanation = item.whyThisIsHere.trimmingCharacters(in: .whitespacesAndNewlines)
        if !explanation.isEmpty {
            return explanation
        }

        let title = item.title.trimmingCharacters(in: .whitespacesAndNewlines)
        if !title.isEmpty {
            return "\(title) is still part of your mailbox work."
        }

        return "This email needs attention."
    }

    private static func nextMoveLabel(for item: AttentionItem) -> String {
        if item.currentState == .waiting && (item.primaryAction == "none" || item.primaryAction == "open") {
            return "Wait for the reply"
        }

        switch item.primaryAction {
        case "reply":
            return "Reply in the thread"
        case "confirm":
            return "Confirm or decline"
        case "pay":
            return "Make the payment"
        case "track":
            return "Check latest status"
        case "review":
            return "Review and decide"
        case "send":
            return "Send the missing item"
        case "approve":
            return "Approve or deny"
        case "register":
            return "Register if useful"
        case "open":
            return "Open and read"
        default:
            return "Read the latest email"
        }
    }

    private static func primaryActionDoneLabel(_ action: String) -> String {
        switch action {
        case "reply":
            return "Replied"
        case "confirm":
            return "Confirmed"
        case "pay":
            return "Paid"
        case "track":
            return "Tracked"
        case "review":
            return "Reviewed"
        case "send":
            return "Sent"
        case "approve":
            return "Approved"
        case "register":
            return "Registered"
        case "open":
            return "Read"
        default:
            return "Done"
        }
    }

    private static func sourceLabel(for item: AttentionItem) -> String {
        item.source == .calendar ? "Calendar" : "Gmail"
    }

    private static func normalizedSourceLabel(_ label: String) -> String {
        label.lowercased() == "gmail" ? "Gmail" : label
    }
}

private struct SortableAgendaItem {
    let id: String
    let time: String
    let title: String
    let allDay: Bool
    let timingBand: TimingBand
    let sortValue: TimeInterval
}

private extension ISO8601DateFormatter {
    static let dashboard: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withColonSeparatorInTimeZone]
        return formatter
    }()
}

private extension DateFormatter {
    static let dashboardDate: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "EEEE, MMM d"
        return formatter
    }()

    static let dashboardTime: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "h:mm a"
        return formatter
    }()

    static let dashboardSchedule: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "h:mm a"
        return formatter
    }()

    static let dashboardDateOnly: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter
    }()
}
