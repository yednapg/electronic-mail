import SwiftUI

public struct TodoHomeView: View {
    @Environment(\.colorScheme) private var colorScheme
    @ObservedObject private var store: InboxStore
    @State private var hiddenEntityIDs: Set<String> = []
    @State private var completingEntityIDs: Set<String> = []
    @State private var completionError: String?
    @State private var draftTitle = ""
    @State private var draftNotes = ""
    @State private var composerOpen = false
    @State private var creatingTask = false

    private let onOpenSource: (String) -> Void

    public init(store: InboxStore, onOpenSource: @escaping (String) -> Void = { _ in }) {
        self.store = store
        self.onOpenSource = onOpenSource
    }

    public var body: some View {
        ZStack {
            ElectronicMailDesign.background(for: colorScheme)
                .ignoresSafeArea()

            if let session = store.session {
                content(for: TodoHomeMapper.snapshot(
                    from: session,
                    now: Date(),
                    hiddenEntityIDs: hiddenEntityIDs,
                    refreshWarning: store.refreshFailed ? "To-do's could not refresh. Showing last saved state." : nil
                ))
            } else {
                loadingView
            }
        }
        .task {
            if store.session == nil {
                await store.load()
            }
        }
        .onAppear {
            store.startLiveRefreshLoop()
        }
        .onDisappear {
            store.stopLiveRefreshLoop()
        }
        .environment(\.font, .system(.body, design: .rounded))
    }

    private func content(for snapshot: TodoHomeSnapshot) -> some View {
        ScrollView(.vertical, showsIndicators: true) {
            VStack(alignment: .leading, spacing: 34) {
                header(snapshot: snapshot)
                summaryBlock(snapshot.summary)

                if !snapshot.agenda.isEmpty {
                    agendaPanel(snapshot.agenda)
                }

                TodoSectionView(
                    title: "Now",
                    items: snapshot.now,
                    colorScheme: colorScheme,
                    completingEntityIDs: completingEntityIDs,
                    onComplete: complete,
                    onOpenSource: onOpenSource
                )

                TodoSectionView(
                    title: "Later Today",
                    items: snapshot.today,
                    colorScheme: colorScheme,
                    completingEntityIDs: completingEntityIDs,
                    trailingButton: AnyView(addButton),
                    footer: AnyView(composer),
                    onComplete: complete,
                    onOpenSource: onOpenSource
                )

                if !snapshot.worthKnowing.isEmpty {
                    TodoSectionView(
                        title: "Worth Knowing",
                        items: snapshot.worthKnowing,
                        colorScheme: colorScheme,
                        completingEntityIDs: completingEntityIDs,
                        onComplete: complete,
                        onOpenSource: onOpenSource
                    )
                }
            }
            .frame(maxWidth: ElectronicMailShellMetrics.contentMaxWidth, alignment: .leading)
            .padding(.top, ElectronicMailShellMetrics.contentTop)
            .padding(.bottom, 70)
            .padding(.horizontal, ElectronicMailShellMetrics.navLeading)
            .frame(maxWidth: .infinity)
        }
    }

    private func header(snapshot: TodoHomeSnapshot) -> some View {
        HStack(alignment: .firstTextBaseline) {
            Text(snapshot.greeting)
                .font(ElectronicMailType.title(weight: .regular))
                .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))

            Spacer(minLength: 24)

            Text(snapshot.timeLabel)
                .font(ElectronicMailType.title(weight: .regular))
                .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))

            Button {
                Task { await store.refresh() }
            } label: {
                Image(systemName: "arrow.clockwise")
                    .font(ElectronicMailType.icon(weight: .semibold))
                    .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                    .frame(width: 34, height: 34)
            }
            .buttonStyle(.plain)
            .help("Refresh")
        }
        .overlay(alignment: .bottomLeading) {
            if let warning = snapshot.refreshWarning {
                Text(warning)
                    .font(ElectronicMailType.small())
                    .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                    .padding(.top, 34)
            }
        }
    }

    private func summaryBlock(_ summary: TodoSummary) -> some View {
        summaryText(summary)
            .font(ElectronicMailType.summary(weight: .regular))
            .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
            .lineSpacing(5)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: 820, alignment: .leading)
    }

    private func summaryText(_ summary: TodoSummary) -> Text {
        let parts = summary.parts.filter { !$0.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
        guard !parts.isEmpty else {
            return Text(summary.fallbackBrief)
                .fontWeight(.light)
        }

        var text = Text("You have ")
            .fontWeight(.light)

        for index in parts.indices {
            text = text
                + Text(summarySeparator(index: index, count: parts.count))
                    .fontWeight(.light)
                + summaryPartText(parts[index])
        }

        text = text + Text(".").fontWeight(.light)

        if let important = summary.important {
            text = text
                + Text(" You also have \(important.emoji) ")
                    .fontWeight(.light)
                + Text(summaryCountPhrase(count: important.count, text: important.text))
                    .fontWeight(.bold)
                + Text(".")
                    .fontWeight(.light)
        }

        if let availability = summary.calendarAvailability {
            text = text
                + Text(" You are \(availability.emoji) ")
                    .fontWeight(.light)
                + Text(availability.text)
                    .fontWeight(.bold)
                + Text(".")
                    .fontWeight(.light)
        }

        return text
    }

    private func summaryPartText(_ part: DashboardBriefingPart) -> Text {
        if part.count == 0 {
            return Text("\(part.emoji) no \(part.text)")
                .fontWeight(.light)
        }

        return Text("\(part.emoji) ")
            .fontWeight(.light)
            + Text(summaryCountPhrase(count: part.count, text: part.text))
                .fontWeight(.bold)
    }

    private func summarySeparator(index: Int, count: Int) -> String {
        if index == 0 {
            return ""
        }
        return index == count - 1 ? " and " : ", "
    }

    private func summaryCountPhrase(count: Int, text: String) -> String {
        "\(count) \(singularized(text, count: count))"
    }

    private func singularized(_ text: String, count: Int) -> String {
        guard count == 1 else {
            return text
        }
        if text == "emails to reply" {
            return "email to reply"
        }
        return text.hasSuffix("s") ? String(text.dropLast()) : text
    }

    private func agendaPanel(_ agenda: [TodoAgendaItem]) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(agenda) { item in
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Text(item.time)
                        .font(ElectronicMailType.body())
                        .foregroundStyle(item.tone.color)
                        .frame(width: 74, alignment: .leading)

                    Text(item.title)
                        .font(ElectronicMailType.body())
                        .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                        .lineLimit(1)
                }
                .frame(height: ElectronicMailType.bodyLineHeight)
            }
        }
        .padding(.horizontal, 22)
        .padding(.vertical, 16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 7, style: .continuous)
                .fill(ElectronicMailDesign.panelFill(for: colorScheme))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 7, style: .continuous)
                .stroke(ElectronicMailDesign.panelBorder(for: colorScheme), lineWidth: 1)
        )
    }

    private var addButton: some View {
        Button {
            withAnimation(.easeInOut(duration: 0.16)) {
                composerOpen.toggle()
            }
        } label: {
            Image(systemName: "plus")
                .font(ElectronicMailType.icon(weight: .bold))
                .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                .frame(width: 30, height: 30)
        }
        .buttonStyle(.plain)
        .help("Add to-do")
    }

    private var composer: some View {
        Group {
            if composerOpen {
                VStack(alignment: .leading, spacing: 14) {
                    HStack(alignment: .center, spacing: 16) {
                        Image(systemName: "square")
                            .font(ElectronicMailType.icon())
                            .foregroundStyle(ElectronicMailDesign.tertiaryText(for: colorScheme))
                            .frame(width: 26)

                        TextField("New to-do", text: $draftTitle)
                            .textFieldStyle(.plain)
                            .font(ElectronicMailType.body(weight: .bold))
                            .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                            .onSubmit { createManualTask() }
                    }

                    TextField("Notes", text: $draftNotes, axis: .vertical)
                        .textFieldStyle(.plain)
                        .font(ElectronicMailType.detail())
                        .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                        .lineLimit(2...4)
                        .padding(.leading, 42)

                    if let completionError {
                        Text(completionError)
                            .font(ElectronicMailType.small())
                            .foregroundStyle(Color.red.opacity(0.82))
                            .padding(.leading, 42)
                    }

                    HStack(spacing: 10) {
                        Spacer()
                        Button("Cancel") {
                            resetComposer()
                        }
                        .buttonStyle(.plain)
                        .font(ElectronicMailType.small(weight: .medium))
                        .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))

                        Button {
                            createManualTask()
                        } label: {
                            if creatingTask {
                                ProgressView()
                                    .controlSize(.small)
                            } else {
                                Text("Add")
                            }
                        }
                        .buttonStyle(.plain)
                        .font(ElectronicMailType.small(weight: .bold))
                        .foregroundStyle(ElectronicMailDesign.appleBlue)
                        .disabled(creatingTask || draftTitle.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    }
                }
                .padding(.horizontal, 22)
                .padding(.vertical, 20)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(
                    RoundedRectangle(cornerRadius: 7, style: .continuous)
                        .fill(ElectronicMailDesign.panelFill(for: colorScheme))
                )
                .overlay(
                    RoundedRectangle(cornerRadius: 7, style: .continuous)
                        .stroke(ElectronicMailDesign.panelBorder(for: colorScheme), lineWidth: 1)
                )
                .transition(.opacity.combined(with: .move(edge: .top)))
            }
        }
    }

    private var loadingView: some View {
        VStack(spacing: 14) {
            ProgressView()
            Text("Loading to-do's")
                .font(ElectronicMailType.body())
                .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
        }
    }

    private func complete(_ item: AttentionItem) {
        guard !completingEntityIDs.contains(item.entityID) else {
            return
        }

        withAnimation(.easeInOut(duration: 0.18)) {
            hiddenEntityIDs.insert(item.entityID)
            completingEntityIDs.insert(item.entityID)
            completionError = nil
        }

        Task { @MainActor in
            do {
                _ = try await store.completeEntity(entityID: item.entityID)
                completingEntityIDs.remove(item.entityID)
            } catch {
                withAnimation(.easeInOut(duration: 0.18)) {
                    hiddenEntityIDs.remove(item.entityID)
                    completingEntityIDs.remove(item.entityID)
                    completionError = "Could not complete this item. It is still visible."
                }
            }
        }
    }

    private func createManualTask() {
        let title = draftTitle.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !title.isEmpty, !creatingTask else {
            return
        }
        let notes = draftNotes.trimmingCharacters(in: .whitespacesAndNewlines)
        creatingTask = true
        completionError = nil

        Task { @MainActor in
            do {
                _ = try await store.createManualTask(
                    title: title,
                    notes: notes.isEmpty ? nil : notes,
                    section: "today"
                )
                resetComposer()
            } catch {
                creatingTask = false
                completionError = "Could not add this to-do."
            }
        }
    }

    private func resetComposer() {
        creatingTask = false
        composerOpen = false
        draftTitle = ""
        draftNotes = ""
        completionError = nil
    }
}

private struct TodoSectionView: View {
    let title: String
    let items: [AttentionItem]
    let colorScheme: ColorScheme
    let completingEntityIDs: Set<String>
    var trailingButton: AnyView?
    var footer: AnyView?
    let onComplete: (AttentionItem) -> Void
    let onOpenSource: (String) -> Void

    init(
        title: String,
        items: [AttentionItem],
        colorScheme: ColorScheme,
        completingEntityIDs: Set<String>,
        trailingButton: AnyView? = nil,
        footer: AnyView? = nil,
        onComplete: @escaping (AttentionItem) -> Void,
        onOpenSource: @escaping (String) -> Void
    ) {
        self.title = title
        self.items = items
        self.colorScheme = colorScheme
        self.completingEntityIDs = completingEntityIDs
        self.trailingButton = trailingButton
        self.footer = footer
        self.onComplete = onComplete
        self.onOpenSource = onOpenSource
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .center) {
                Text(title)
                    .font(ElectronicMailType.title())
                    .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))

                Spacer()

                trailingButton
            }
            .padding(.bottom, 1)

            if items.isEmpty {
                Text("Nothing here.")
                    .font(ElectronicMailType.detail())
                    .foregroundStyle(ElectronicMailDesign.tertiaryText(for: colorScheme))
                    .frame(height: ElectronicMailType.bodyLineHeight)
            } else {
                VStack(alignment: .leading, spacing: 12) {
                    ForEach(items.indices, id: \.self) { index in
                        TodoItemRow(
                            item: items[index],
                            expanded: index == 0 && title == "Now",
                            colorScheme: colorScheme,
                            isCompleting: completingEntityIDs.contains(items[index].entityID),
                            onComplete: { onComplete(items[index]) },
                            onOpenSource: { onOpenSource(items[index].gmailThreadID ?? items[index].entityID) }
                        )
                    }
                }
            }

            footer
        }
    }
}

private struct TodoItemRow: View {
    let item: AttentionItem
    let expanded: Bool
    let colorScheme: ColorScheme
    let isCompleting: Bool
    let onComplete: () -> Void
    let onOpenSource: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .firstTextBaseline, spacing: 16) {
                Button(action: onComplete) {
                    Group {
                        if isCompleting {
                            ProgressView()
                                .controlSize(.small)
                        } else {
                            Image(systemName: "square")
                                .font(ElectronicMailType.icon())
                        }
                    }
                    .frame(width: 26, height: 26)
                    .foregroundStyle(ElectronicMailDesign.tertiaryText(for: colorScheme))
                }
                .buttonStyle(.plain)
                .help("Complete")

                Text(item.title)
                    .font(ElectronicMailType.body(weight: expanded ? .bold : .regular))
                    .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                    .fixedSize(horizontal: false, vertical: true)

                Spacer(minLength: 16)

                if item.source == .gmail, item.gmailThreadID?.isEmpty == false {
                    Button(action: onOpenSource) {
                        Image(systemName: "envelope")
                            .font(ElectronicMailType.icon())
                            .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                            .frame(width: 30, height: 30)
                    }
                    .buttonStyle(.plain)
                    .help("Open source email")
                }
            }

            if expanded, let detailText = detailText {
                Text(detailText)
                    .font(ElectronicMailType.detail())
                    .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                    .lineSpacing(4)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.leading, 42)

                HStack(spacing: 16) {
                    Text(item.detail?.actionLabel ?? "Done")
                        .font(ElectronicMailType.detail(weight: .bold))
                        .foregroundStyle(ElectronicMailDesign.green)

                    Text("|")
                        .font(ElectronicMailType.detail(weight: .semibold))
                        .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))

                    Button("No") {
                        onComplete()
                    }
                    .buttonStyle(.plain)
                    .font(ElectronicMailType.detail(weight: .bold))
                    .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))

                    Spacer()

                    if let source = item.detail?.sourceLabel {
                        HStack(spacing: 6) {
                            Image(systemName: item.source == .manual ? "square.and.pencil" : "tray.full")
                                .font(ElectronicMailType.small())
                            Text("Source: \(source)")
                                .font(ElectronicMailType.small())
                        }
                        .foregroundStyle(ElectronicMailDesign.tertiaryText(for: colorScheme))
                    }
                }
                .padding(.leading, 42)
            }
        }
        .padding(.horizontal, expanded ? 22 : 0)
        .padding(.vertical, expanded ? 20 : 0)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background {
            if expanded {
                RoundedRectangle(cornerRadius: 7, style: .continuous)
                    .fill(ElectronicMailDesign.panelFill(for: colorScheme))
            }
        }
        .overlay {
            if expanded {
                RoundedRectangle(cornerRadius: 7, style: .continuous)
                    .stroke(ElectronicMailDesign.panelBorder(for: colorScheme), lineWidth: 1)
            }
        }
    }

    private var detailText: String? {
        if let detail = item.detail, !detail.body.isEmpty {
            return detail.body.joined(separator: " ")
        }
        let fallback = item.whyThisIsHere.trimmingCharacters(in: .whitespacesAndNewlines)
        return fallback.isEmpty ? nil : fallback
    }
}

struct TodoHomeSnapshot: Equatable {
    let greeting: String
    let timeLabel: String
    let summary: TodoSummary
    let agenda: [TodoAgendaItem]
    let now: [AttentionItem]
    let today: [AttentionItem]
    let worthKnowing: [AttentionItem]
    let refreshWarning: String?
}

struct TodoSummary: Equatable {
    let fallbackBrief: String
    let parts: [DashboardBriefingPart]
    let important: DashboardBriefingImportant?
    let calendarAvailability: DashboardCalendarAvailability?
}

struct TodoAgendaItem: Equatable, Identifiable {
    enum Tone: Equatable {
        case blue
        case green
        case teal

        var color: Color {
            switch self {
            case .blue:
                return ElectronicMailDesign.appleBlue
            case .green:
                return ElectronicMailDesign.green
            case .teal:
                return Color.teal
            }
        }
    }

    let id: String
    let time: String
    let title: String
    let tone: Tone
    let sortValue: TimeInterval
}

enum TodoHomeMapper {
    static func snapshot(
        from session: AppSessionResponse,
        now: Date = Date(),
        hiddenEntityIDs: Set<String> = [],
        refreshWarning: String? = nil
    ) -> TodoHomeSnapshot {
        let feed = session.dashboard.feed
        return TodoHomeSnapshot(
            greeting: greeting(from: session, now: now),
            timeLabel: DateFormatter.todoHeaderTime.string(from: now),
            summary: summary(from: session.dashboard),
            agenda: agenda(from: feed),
            now: todoItems(from: feed.now, hiddenEntityIDs: hiddenEntityIDs),
            today: todoItems(from: feed.today, hiddenEntityIDs: hiddenEntityIDs),
            worthKnowing: todoItems(from: feed.worthKnowing, hiddenEntityIDs: hiddenEntityIDs),
            refreshWarning: refreshWarning
        )
    }

    private static func greeting(from session: AppSessionResponse, now: Date) -> String {
        if let headline = session.dashboard.briefing?.headline,
           !headline.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return polishedGreeting(headline)
        }

        let hour = Calendar.current.component(.hour, from: now)
        let period = hour < 12 ? "morning" : hour < 17 ? "afternoon" : "evening"
        let name = session.user.firstName ?? session.user.displayName ?? "there"
        return "Good \(period), \(name)!"
    }

    private static func polishedGreeting(_ value: String) -> String {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        let withoutTerminal = trimmed.trimmingCharacters(in: CharacterSet(charactersIn: ".! "))
        guard !withoutTerminal.isEmpty else {
            return trimmed
        }
        return withoutTerminal.contains(",") ? "\(withoutTerminal)!" : "\(withoutTerminal)."
    }

    private static func summary(from dashboard: DashboardResponse) -> TodoSummary {
        guard let briefing = dashboard.briefing else {
            return TodoSummary(
                fallbackBrief: "Connect Google to generate a personalized briefing.",
                parts: [],
                important: nil,
                calendarAvailability: nil
            )
        }

        return TodoSummary(
            fallbackBrief: briefing.brief,
            parts: briefing.parts ?? [],
            important: briefing.important,
            calendarAvailability: briefing.calendarAvailability
        )
    }

    private static func todoItems(from items: [AttentionItem], hiddenEntityIDs: Set<String>) -> [AttentionItem] {
        items
            .filter { $0.source != .calendar }
            .filter { !hiddenEntityIDs.contains($0.entityID) }
            .sorted { left, right in
                let leftDue = sortValue(for: left.dueAt)
                let rightDue = sortValue(for: right.dueAt)
                if let leftDue, let rightDue, leftDue != rightDue {
                    return leftDue < rightDue
                }
                if leftDue != nil && rightDue == nil {
                    return true
                }
                if leftDue == nil && rightDue != nil {
                    return false
                }
                return left.createdAt < right.createdAt
            }
    }

    private static func agenda(from feed: FeedResponse) -> [TodoAgendaItem] {
        (feed.now + feed.today + feed.worthKnowing)
            .filter { $0.source == .calendar }
            .compactMap { item in
                guard let dueAt = item.dueAt, let sortValue = sortValue(for: dueAt) else {
                    return nil
                }
                return TodoAgendaItem(
                    id: item.id,
                    time: formattedTime(for: dueAt),
                    title: item.title,
                    tone: tone(for: item.timingBand),
                    sortValue: sortValue
                )
            }
            .sorted { $0.sortValue < $1.sortValue }
    }

    private static func sortValue(for value: String?) -> TimeInterval? {
        guard let value, !value.isEmpty else {
            return nil
        }
        if let date = ISO8601DateFormatter.todo.date(from: value) {
            return date.timeIntervalSince1970
        }
        if let date = DateFormatter.todoDateOnly.date(from: value) {
            return date.timeIntervalSince1970
        }
        return nil
    }

    private static func formattedTime(for value: String) -> String {
        if let date = ISO8601DateFormatter.todo.date(from: value) {
            return DateFormatter.todoTime.string(from: date)
        }
        return "All day"
    }

    private static func tone(for timingBand: TimingBand) -> TodoAgendaItem.Tone {
        switch timingBand {
        case .now:
            return .blue
        case .today:
            return .green
        case .later, .hidden:
            return .teal
        }
    }
}

private extension ISO8601DateFormatter {
    static let todo: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withColonSeparatorInTimeZone]
        return formatter
    }()
}

private extension DateFormatter {
    static let todoHeaderTime: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "h:mm a"
        return formatter
    }()

    static let todoTime: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "HH:mm"
        return formatter
    }()

    static let todoDateOnly: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter
    }()
}

#Preview {
    TodoHomeView(store: InboxStore(client: DemoAppClient()))
        .frame(width: 1100, height: 760)
}
