import SwiftUI

enum DigestPalette {
    static let text = Color(red: 0.02, green: 0.02, blue: 0.02)
    static let muted = Color(red: 0.337, green: 0.337, blue: 0.337)
    static let subtle = Color(red: 0.455, green: 0.455, blue: 0.455)
    static let rule = Color(red: 0.867, green: 0.867, blue: 0.867)
    static let agendaBackground = Color(red: 0.957, green: 0.957, blue: 0.957)
    static let agendaBlue = Color(red: 0.125, green: 0.659, blue: 1.0)
    static let agendaGreen = Color(red: 0.098, green: 0.78, blue: 0.18)
    static let agendaTeal = Color(red: 0.259, green: 0.839, blue: 0.788)
    static let agendaLime = Color(red: 0.345, green: 0.796, blue: 0.259)
    static let ctaBlue = Color(red: 0.125, green: 0.659, blue: 1.0)
    static let ctaGreen = Color(red: 0.098, green: 0.78, blue: 0.18)

    static func rounded(size: CGFloat, weight: Font.Weight = .regular) -> Font {
        .system(size: size, weight: weight, design: .rounded)
    }
}

struct SignedOutView: View {
    @ObservedObject var store: DashboardStore

    var body: some View {
        DigestShell {
            DashboardMetaView()

            VStack(alignment: .leading, spacing: 0) {
                SummaryText(
                    headline: "Connect your Google account.",
                    brief: store.dashboard?.auth.available == false
                        ? "Google OAuth is not configured in the backend yet."
                        : "The dashboard needs live Gmail and Calendar access before it can build your brief."
                )

                Button {
                    Task { await store.connectGoogle() }
                } label: {
                    Text("Continue with Google")
                        .font(DigestPalette.rounded(size: 15, weight: .semibold))
                        .foregroundStyle(DigestPalette.text)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 10)
                        .overlay(
                            RoundedRectangle(cornerRadius: 8, style: .continuous)
                                .stroke(DigestPalette.text, lineWidth: 1)
                        )
                }
                .buttonStyle(.plain)
            }
        }
        .refreshable {
            await store.refresh()
        }
    }
}

struct DashboardView: View {
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass
    let dashboard: DashboardResponse
    @ObservedObject var store: DashboardStore

    var body: some View {
        DigestShell {
            DashboardMetaView()

            if let briefing = dashboard.briefing {
                SummaryText(headline: briefing.headline, brief: briefing.brief)
            } else {
                SummaryText(
                    headline: "Your dashboard is ready.",
                    brief: "Connect Google to generate a personalized briefing."
                )
            }

            AgendaView(items: DashboardPresentation.agendaItems(for: dashboard.feed))

            VStack(alignment: .leading, spacing: horizontalSizeClass == .compact ? 40 : 48) {
                DashboardSectionView(
                    title: "Now",
                    items: DashboardPresentation.sortedSectionItems(dashboard.feed.now),
                    maxVisible: 4,
                    store: store
                )
                DashboardSectionView(
                    title: "Today",
                    items: DashboardPresentation.sortedSectionItems(dashboard.feed.today),
                    maxVisible: 5,
                    store: store
                )
                DashboardSectionView(
                    title: "Worth Knowing",
                    items: DashboardPresentation.sortedSectionItems(dashboard.feed.worthKnowing),
                    maxVisible: 3,
                    store: store
                )
            }

            if dashboard.feed.isEmpty {
                Text("Nothing needs attention.")
                    .font(DigestPalette.rounded(size: 16))
                    .foregroundStyle(DigestPalette.muted)
            }
        }
        .refreshable {
            await store.refresh()
        }
    }
}

private struct DigestShell<Content: View>: View {
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass
    @ViewBuilder let content: () -> Content

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                content()
            }
            .frame(maxWidth: horizontalSizeClass == .compact ? 576 : 720, alignment: .leading)
            .padding(.horizontal, horizontalSizeClass == .compact ? 18 : 22)
            .padding(.top, horizontalSizeClass == .compact ? 28 : 54)
            .padding(.bottom, horizontalSizeClass == .compact ? 56 : 88)
            .frame(maxWidth: .infinity, alignment: .center)
        }
        .background(Color.white)
    }
}

private struct DashboardMetaView: View {
    @State private var now = Date()
    private let timer = Timer.publish(every: 1, on: .main, in: .common).autoconnect()

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(DashboardPresentation.dateLabel(for: now))
                .font(DigestPalette.rounded(size: 20))
                .foregroundStyle(DigestPalette.text)

            Spacer(minLength: 24)

            Text(DashboardPresentation.clockLabel(for: now))
                .font(DigestPalette.rounded(size: 20))
                .foregroundStyle(DigestPalette.text)
                .monospacedDigit()
        }
        .onReceive(timer) { value in
            now = value
        }
    }
}

private struct SummaryText: View {
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass
    let headline: String
    let brief: String

    var body: some View {
        (Text(headline + " ")
            .fontWeight(.bold)
            + Text(brief)
            .fontWeight(.light))
            .font(DigestPalette.rounded(size: horizontalSizeClass == .compact ? 19 : 23, weight: .light))
            .foregroundStyle(DigestPalette.text)
            .lineSpacing(horizontalSizeClass == .compact ? 3 : 2)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.top, horizontalSizeClass == .compact ? 26 : 30)
            .padding(.bottom, horizontalSizeClass == .compact ? 34 : 48)
    }
}

private struct AgendaView: View {
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass
    let items: [AgendaItem]

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            if items.isEmpty {
                Text("No calendar items.")
                    .font(DigestPalette.rounded(size: 16))
                    .foregroundStyle(DigestPalette.muted)
            } else {
                ForEach(items) { item in
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text(item.time)
                            .font(DigestPalette.rounded(size: horizontalSizeClass == .compact ? 15 : 16, weight: .semibold))
                            .foregroundStyle(item.tone.color)
                            .frame(width: horizontalSizeClass == .compact ? 62 : 76, alignment: .leading)

                        Text(item.title)
                            .font(DigestPalette.rounded(size: horizontalSizeClass == .compact ? 15 : 16))
                            .foregroundStyle(DigestPalette.muted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
        .padding(.horizontal, horizontalSizeClass == .compact ? 14 : 18)
        .padding(.top, horizontalSizeClass == .compact ? 14 : 18)
        .padding(.bottom, horizontalSizeClass == .compact ? 12 : 16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(DigestPalette.agendaBackground)
        .padding(.bottom, horizontalSizeClass == .compact ? 38 : 50)
    }
}

struct DashboardSectionView: View {
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass
    let title: String
    let items: [AttentionItem]
    let maxVisible: Int
    @ObservedObject var store: DashboardStore
    @State private var expanded = false

    private var visibleItems: [AttentionItem] {
        expanded ? items : Array(items.prefix(maxVisible))
    }

    var body: some View {
        if !items.isEmpty {
            VStack(alignment: .leading, spacing: 0) {
                Text(title)
                    .font(DigestPalette.rounded(size: horizontalSizeClass == .compact ? 16 : 18, weight: .bold))
                    .foregroundStyle(DigestPalette.text)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.bottom, 11)
                    .overlay(alignment: .bottom) {
                        Rectangle()
                            .fill(DigestPalette.rule)
                            .frame(height: 1)
                    }

                VStack(alignment: .leading, spacing: 10) {
                    ForEach(visibleItems) { item in
                        FeedItemRow(item: item, actionState: store.actionStates[item.id] ?? .idle) {
                            Task { await store.runGmailAction(for: item) }
                        }
                    }
                }
                .padding(.top, 14)

                if items.count > maxVisible {
                    Button(expanded ? "Show less" : "Show \(items.count - maxVisible) more") {
                        withAnimation(.snappy) {
                            expanded.toggle()
                        }
                    }
                    .buttonStyle(.plain)
                    .font(DigestPalette.rounded(size: 15))
                    .foregroundStyle(DigestPalette.muted)
                    .padding(.top, 12)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

struct FeedItemRow: View {
    let item: AttentionItem
    let actionState: ItemActionState
    let runAction: () -> Void
    @State private var checked = false

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Button {
                checked.toggle()
            } label: {
                ZStack {
                    RoundedRectangle(cornerRadius: 2, style: .continuous)
                        .fill(checked ? DigestPalette.text : Color.white)
                        .overlay(
                            RoundedRectangle(cornerRadius: 2, style: .continuous)
                                .stroke(checked ? DigestPalette.text : Color(red: 0.843, green: 0.843, blue: 0.843), lineWidth: 1)
                        )

                    if checked {
                        Image(systemName: "checkmark")
                            .font(DigestPalette.rounded(size: 11, weight: .bold))
                            .foregroundStyle(Color.white)
                    }
                }
                .frame(width: 18, height: 18)
                .padding(.top, 3)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("\(checked ? "Untick" : "Tick") \(DashboardPresentation.sectionTitle(for: item))")

            FlowingActionText(
                item: item,
                checked: checked,
                actionState: actionState,
                runAction: runAction
            )
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct FlowingActionText: View {
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass
    let item: AttentionItem
    let checked: Bool
    let actionState: ItemActionState
    let runAction: () -> Void

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 0) {
            NavigationLink(value: item.entityID) {
                Text(DashboardPresentation.sectionTitle(for: item))
                    .strikethrough(checked, color: DigestPalette.muted)
            }
            .buttonStyle(.plain)

            if let cta = DashboardPresentation.cta(for: item) {
                Text(" → ")
                Button {
                    runAction()
                } label: {
                    Text(actionLabel(defaultLabel: cta.label))
                        .fontWeight(.bold)
                        .underline()
                        .foregroundStyle(cta.tone.ctaColor)
                }
                .buttonStyle(.plain)
                .disabled(actionState == .loading || actionState == .done)
            }
        }
        .font(DigestPalette.rounded(size: horizontalSizeClass == .compact ? 16 : 17))
        .foregroundStyle(DigestPalette.text)
        .lineSpacing(horizontalSizeClass == .compact ? 3 : 2)
        .fixedSize(horizontal: false, vertical: true)
    }

    private func actionLabel(defaultLabel: String) -> String {
        switch actionState {
        case .loading:
            return "Working..."
        case .done:
            return "Done"
        case .idle, .failed:
            return defaultLabel
        }
    }
}

private struct AgendaItem: Identifiable, Equatable {
    let id: String
    let time: String
    let title: String
    let allDay: Bool
    let sortValue: TimeInterval
    let tone: DigestTone
}

private struct DigestCTA: Equatable {
    let label: String
    let tone: DigestTone
}

private enum DigestTone: Equatable {
    case blue
    case green
    case teal
    case lime

    var color: Color {
        switch self {
        case .blue:
            return DigestPalette.agendaBlue
        case .green:
            return DigestPalette.agendaGreen
        case .teal:
            return DigestPalette.agendaTeal
        case .lime:
            return DigestPalette.agendaLime
        }
    }

    var ctaColor: Color {
        switch self {
        case .blue:
            return DigestPalette.ctaBlue
        case .green, .teal, .lime:
            return DigestPalette.ctaGreen
        }
    }
}

private enum DashboardPresentation {
    static func dateLabel(for date: Date) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US")
        formatter.dateFormat = "EEEE, MMMM d"
        return formatter.string(from: date)
    }

    static func clockLabel(for date: Date) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US")
        formatter.dateFormat = "h:mm a"
        return formatter.string(from: date)
    }

    static func agendaItems(for feed: FeedResponse) -> [AgendaItem] {
        let visibleItems = feed.now + feed.today + feed.worthKnowing

        return visibleItems
            .compactMap { item -> AgendaItem? in
                guard item.source == .calendar, let dueAt = item.dueAt else {
                    return nil
                }

                let isTimed = isTimedTimestamp(dueAt)

                if !isTimed && hasExplicitTime(dueAt) {
                    return nil
                }

                return AgendaItem(
                    id: item.id,
                    time: isTimed ? scheduleTimeLabel(dueAt) : "All day",
                    title: actionSentence(for: item),
                    allDay: !isTimed,
                    sortValue: agendaSortValue(dueAt, isTimed: isTimed),
                    tone: tone(for: item.timingBand, index: 0)
                )
            }
            .sorted { left, right in
                if left.sortValue != right.sortValue {
                    return left.sortValue < right.sortValue
                }

                if left.allDay != right.allDay {
                    return left.allDay
                }

                let timeOrder = left.time.localizedCompare(right.time)
                if timeOrder != .orderedSame {
                    return timeOrder == .orderedAscending
                }

                return left.title.localizedCompare(right.title) == .orderedAscending
            }
            .enumerated()
            .map { index, item in
                AgendaItem(
                    id: item.id,
                    time: item.time,
                    title: item.title,
                    allDay: item.allDay,
                    sortValue: item.sortValue,
                    tone: tone(for: visibleItems.first { $0.id == item.id }?.timingBand ?? .later, index: index)
                )
            }
    }

    static func sortedSectionItems(_ items: [AttentionItem]) -> [AttentionItem] {
        items.sorted { left, right in
            let leftDueAt = feedSortValue(left)
            let rightDueAt = feedSortValue(right)

            if let leftDueAt, let rightDueAt, leftDueAt != rightDueAt {
                return leftDueAt < rightDueAt
            }

            if leftDueAt != nil && rightDueAt == nil {
                return true
            }

            if leftDueAt == nil && rightDueAt != nil {
                return false
            }

            if left.source == .calendar && right.source != .calendar {
                return true
            }

            if left.source != .calendar && right.source == .calendar {
                return false
            }

            return left.title.localizedCompare(right.title) == .orderedAscending
        }
    }

    static func sectionTitle(for item: AttentionItem) -> String {
        if item.needType == .awareness {
            if item.source == .calendar && !item.whyThisIsHere.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                return item.whyThisIsHere
            }

            return item.title
        }

        return actionSentence(for: item)
    }

    static func cta(for item: AttentionItem) -> DigestCTA? {
        guard item.source == .gmail, item.gmailThreadID != nil else {
            return nil
        }

        switch item.gmailThreadAction {
        case .archive:
            return DigestCTA(label: "Archive", tone: .green)
        case .unarchive:
            return DigestCTA(label: "Unarchive", tone: .blue)
        case .markRead:
            return DigestCTA(label: "Mark read", tone: .blue)
        case .none:
            return nil
        }
    }

    private static func actionSentence(for item: AttentionItem) -> String {
        let cleanTitle = item.title.trimmingCharacters(in: .whitespacesAndNewlines)

        if startsWithActionVerb(cleanTitle) || shouldKeepNaturalTitle(item, cleanTitle) {
            return cleanTitle
        }

        switch item.primaryAction {
        case "reply":
            if cleanTitle.lowercased() == "reply needed" {
                return "Reply to this thread"
            }

            return startsWithVerb(cleanTitle, ["reply", "respond"]) ? cleanTitle : "Reply about \(cleanTitle)"
        case "confirm":
            if cleanTitle.lowercased() == "rsvp needed" {
                return "Confirm this invite"
            }

            return startsWithVerb(cleanTitle, ["confirm", "rsvp"]) ? cleanTitle : "Confirm \(cleanTitle)"
        case "pay":
            return startsWithVerb(cleanTitle, ["pay"]) ? cleanTitle : "Pay \(stripDuePrefix(cleanTitle))"
        case "track":
            return startsWithVerb(cleanTitle, ["track"]) ? cleanTitle : "Track \(stripDuePrefix(cleanTitle))"
        case "review":
            return "Review \(cleanTitle)"
        case "join":
            return "Join \(cleanTitle)"
        case "send":
            return "Send \(cleanTitle)"
        case "approve":
            return "Approve \(cleanTitle)"
        case "register":
            return "Register for \(cleanTitle)"
        case "open":
            if cleanTitle.hasPrefix("Due ") {
                return "Review item due \(cleanTitle.dropFirst(4))"
            }

            return startsWithVerb(cleanTitle, ["review", "open"]) ? cleanTitle : "Review \(cleanTitle)"
        default:
            return cleanTitle
        }
    }

    private static func shouldKeepNaturalTitle(_ item: AttentionItem, _ title: String) -> Bool {
        if item.needType == .awareness {
            return true
        }

        if title.range(of: #"[.!?]$"#, options: .regularExpression) != nil {
            return true
        }

        let pattern = #"^(you\b|your\b|you're\b|we\b|this\b|[A-Z][A-Za-z0-9&.'/-]+(?: [A-Z][A-Za-z0-9&.'/-]+){0,4} (?:updated|declined|approved|confirmed|registered|delivered|shipped|sent|accepted|resolved|says|changed|scheduled)\b)"#
        return title.range(of: pattern, options: [.regularExpression, .caseInsensitive]) != nil
    }

    private static func startsWithActionVerb(_ title: String) -> Bool {
        startsWithVerb(title, [
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
            "register"
        ])
    }

    private static func startsWithVerb(_ title: String, _ verbs: [String]) -> Bool {
        let normalizedTitle = title.lowercased()
        return verbs.contains { normalizedTitle.hasPrefix($0) }
    }

    private static func stripDuePrefix(_ title: String) -> String {
        title.hasPrefix("Due ") ? "item due \(title.dropFirst(4))" : title
    }

    private static func tone(for timingBand: TimingBand, index: Int) -> DigestTone {
        if timingBand == .now {
            return .blue
        }

        if timingBand == .today {
            return .teal
        }

        return index.isMultiple(of: 2) ? .green : .lime
    }

    private static func scheduleTimeLabel(_ value: String) -> String {
        guard let date = parsedDate(value) else {
            return ""
        }

        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_GB")
        formatter.dateFormat = "HH:mm"
        return formatter.string(from: date)
    }

    private static func feedSortValue(_ item: AttentionItem) -> TimeInterval? {
        guard let dueAt = item.dueAt, !dueAt.isEmpty else {
            return nil
        }

        let isTimed = isTimedTimestamp(dueAt)

        if !isTimed && hasExplicitTime(dueAt) {
            return nil
        }

        return agendaSortValue(dueAt, isTimed: isTimed)
    }

    private static func agendaSortValue(_ value: String, isTimed: Bool) -> TimeInterval {
        let parseSource = isTimed ? value : "\(value)T00:00:00"
        return parsedDate(parseSource)?.timeIntervalSince1970 ?? TimeInterval.greatestFiniteMagnitude
    }

    private static func isTimedTimestamp(_ value: String) -> Bool {
        guard value.range(of: #"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}"#, options: .regularExpression) != nil else {
            return false
        }

        return parsedDate(value) != nil
    }

    private static func hasExplicitTime(_ value: String) -> Bool {
        value.range(of: #"T\d{2}:\d{2}| \d{2}:\d{2}"#, options: .regularExpression) != nil
    }

    private static func parsedDate(_ value: String) -> Date? {
        let normalizedValue = normalizeDateString(value)
        let isoFormatter = ISO8601DateFormatter()
        isoFormatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]

        if let date = isoFormatter.date(from: normalizedValue) {
            return date
        }

        isoFormatter.formatOptions = [.withInternetDateTime]
        if let date = isoFormatter.date(from: normalizedValue) {
            return date
        }

        for format in ["yyyy-MM-dd'T'HH:mm:ss", "yyyy-MM-dd'T'HH:mm", "yyyy-MM-dd"] {
            let formatter = DateFormatter()
            formatter.locale = Locale(identifier: "en_US_POSIX")
            formatter.dateFormat = format

            if let date = formatter.date(from: normalizedValue) {
                return date
            }
        }

        return nil
    }

    private static func normalizeDateString(_ value: String) -> String {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)

        if let range = trimmed.range(of: #"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}(:\d{2})?"#, options: .regularExpression) {
            return String(trimmed[range]).replacingOccurrences(of: " ", with: "T")
        }

        if let range = trimmed.range(of: #"^\d{4}-\d{2}-\d{2}T[^ ]+"#, options: .regularExpression) {
            return String(trimmed[range])
        }

        if let range = trimmed.range(of: #"^\d{4}-\d{2}-\d{2}$"#, options: .regularExpression) {
            return String(trimmed[range])
        }

        return trimmed
    }
}
