import Foundation
import SwiftUI

public struct TodoHomeView: View {
    @Environment(\.colorScheme) private var colorScheme
    @ObservedObject private var store: InboxStore
    @ObservedObject private var aiInboxStore: AIInboxStore
    @State private var hiddenEntityIDs: Set<String> = []
    @State private var completingEntityIDs: Set<String> = []
    @State private var completionError: String?
    @State private var expandedItemID: String?
    @State private var collapsedSectionIDs: Set<String> = []
    @Binding private var searchText: String

    private let onOpenSource: (String) -> Void
    private let onOpenAIMatter: (String) -> Void

    public init(
        store: InboxStore,
        aiInboxStore: AIInboxStore,
        searchText: Binding<String> = .constant(""),
        onOpenSource: @escaping (String) -> Void = { _ in },
        onOpenAIMatter: @escaping (String) -> Void = { _ in }
    ) {
        self.store = store
        self.aiInboxStore = aiInboxStore
        self._searchText = searchText
        self.onOpenSource = onOpenSource
        self.onOpenAIMatter = onOpenAIMatter
    }

    public var body: some View {
        ZStack {
            ElectronicMailDesign.background(for: colorScheme)
                .ignoresSafeArea()

            if let session = store.session {
                TimelineView(.periodic(from: .now, by: 60)) { context in
                    content(for: TodoHomeMapper.snapshot(
                        from: session,
                        inboxRows: store.flatRows,
                        aiTodos: aiInboxStore.todoItems,
                        todoOrganizingCount: aiInboxStore.todoOrganizingCount,
                        now: context.date,
                        hiddenEntityIDs: hiddenEntityIDs
                    ))
                }

                if store.refreshFailed && aiInboxStore.todoRefreshFailed {
                    ElectronicMailRefreshFailureToast(message: "To-do's could not refresh. Showing last saved state.")
                }
            } else {
                loadingView
            }
        }
        .task {
            if store.session == nil {
                await store.load()
            }
            await aiInboxStore.refreshTodos()

            while aiInboxStore.todoOrganizingCount > 0 && !Task.isCancelled {
                do {
                    try await Task.sleep(nanoseconds: 4_000_000_000)
                } catch {
                    return
                }
                await aiInboxStore.refreshTodos()
            }
        }
        .environment(\.font, .body)
    }

    private func content(for snapshot: TodoHomeSnapshot) -> some View {
        GeometryReader { proxy in
            let contentWidth = TodoPageLayout.contentWidth(for: proxy.size.width)
            let metrics = TodoLayoutMetrics(contentWidth: contentWidth)

            ScrollView(.vertical, showsIndicators: true) {
                VStack(alignment: .leading, spacing: 48) {
                    header(snapshot: snapshot)

                    if let aiBuildStatus = snapshot.aiBuildStatus {
                        aiBuildStatusPanel(aiBuildStatus)
                    }

                    if let dashboardBuildStatus = snapshot.dashboardBuildStatus {
                        aiBuildStatusPanel(dashboardBuildStatus)
                    } else {
                        agendaPanel(snapshot.agenda)

                        VStack(alignment: .leading, spacing: 0) {
                            todoSection(
                                filtered(snapshot.now),
                                isFirstSection: true,
                                metrics: metrics
                            )

                            todoSection(filtered(snapshot.laterToday), metrics: metrics)

                            todoSection(filtered(snapshot.upcoming), metrics: metrics)

                            todoSection(filtered(snapshot.worthKnowing), metrics: metrics)
                        }
                    }
                }
                .frame(width: contentWidth, alignment: .leading)
                .padding(.top, TodoTypography.contentTop)
                .padding(.bottom, 70)
                .padding(.horizontal, ElectronicMailShellMetrics.navLeading)
                .frame(maxWidth: .infinity)
            }
        }
    }

    private func todoSection(
        _ section: TodoSectionModel,
        isFirstSection: Bool = false,
        metrics: TodoLayoutMetrics
    ) -> some View {
        TodoSectionView(
            section: section,
            isFirstSection: isFirstSection,
            metrics: metrics,
            colorScheme: colorScheme,
            completingEntityIDs: completingEntityIDs,
            expandedItemID: $expandedItemID,
            collapsed: collapsedSectionIDs.contains(section.id),
            onToggleCollapsed: { toggleCollapsed(section) },
            onComplete: complete,
            onOpenSource: onOpenSource,
            onOpenAIMatter: onOpenAIMatter
        )
    }

    private func filtered(_ section: TodoSectionModel) -> TodoSectionModel {
        let query = searchText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else { return section }

        let rows = section.rows.filter { row in
            [row.title, row.sender, row.detailText, row.actionLabel, row.sourceLabel]
                .contains { $0.localizedCaseInsensitiveContains(query) }
        }
        return TodoSectionModel(id: section.id, title: section.title, rows: rows)
    }

    private func toggleCollapsed(_ section: TodoSectionModel) {
        withAnimation(.easeInOut(duration: 0.18)) {
            if collapsedSectionIDs.contains(section.id) {
                collapsedSectionIDs.remove(section.id)
            } else {
                collapsedSectionIDs.insert(section.id)
                if let expandedItemID,
                   section.rows.contains(where: { $0.id == expandedItemID }) {
                    self.expandedItemID = nil
                }
            }
        }
    }

    private func header(snapshot: TodoHomeSnapshot) -> some View {
        summaryBlock(snapshot)
    }

    private func summaryBlock(_ snapshot: TodoHomeSnapshot) -> some View {
        InlineSummaryLayout(spacing: 0, lineSpacing: 6) {
            ForEach(Array(summaryTokens(snapshot).enumerated()), id: \.offset) { _, token in
                SummaryTokenView(token: token, colorScheme: colorScheme)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func summaryTokens(_ snapshot: TodoHomeSnapshot) -> [SummaryToken] {
        let summary = snapshot.summary
        let parts = summary.parts.filter { !$0.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
        var tokens: [SummaryToken] = []

        guard !parts.isEmpty else {
            tokens.append(.text(summary.fallbackBrief, style: .supporting))
            return tokens
        }

        tokens.append(.text("You have ", style: .supporting))

        for index in parts.indices {
            let separator = summarySeparator(index: index, count: parts.count)
            if !separator.isEmpty {
                tokens.append(.text(separator, style: .supporting))
            }
            tokens.append(contentsOf: summaryPartTokens(parts[index]))
        }

        tokens.append(.text(".", style: .supporting))

        if let important = summary.important {
            tokens.append(.text(" You also have ", style: .supporting))
            tokens.append(.symbol(summarySymbol(type: "important", fallbackEmoji: important.emoji)))
            tokens.append(.text(" ", style: .supporting))
            tokens.append(.text(summaryCountPhrase(count: important.count, text: important.text), style: .statement))
            tokens.append(.text(".", style: .supporting))
        }

        if let availability = summary.calendarAvailability {
            tokens.append(.text(" You are ", style: .supporting))
            tokens.append(.symbol(summarySymbol(type: "availability", fallbackEmoji: availability.emoji)))
            tokens.append(.text(" ", style: .supporting))
            tokens.append(.text(availability.text, style: .statement))
            tokens.append(.text(".", style: .supporting))
        }

        return tokens
    }

    private func summaryPartTokens(_ part: DashboardBriefingPart) -> [SummaryToken] {
        let symbol = summarySymbol(type: part.type, fallbackEmoji: part.emoji)
        if part.count == 0 {
            return [
                .symbol(symbol),
                .text(" no \(part.text)", style: .supporting),
            ]
        }

        return [
            .symbol(symbol),
            .text(" ", style: .supporting),
            .text(summaryCountPhrase(count: part.count, text: part.text), style: .metric),
        ]
    }

    private func summarySymbol(type: String?, fallbackEmoji: String) -> SummarySymbol {
        let normalized = type?.lowercased() ?? ""
        switch normalized {
        case "meetings", "calendar":
            return SummarySymbol(name: "calendar", tone: .blue)
        case "tasks", "todo", "todos":
            return SummarySymbol(name: "checkmark.square", tone: .green)
        case "emails", "mail":
            return SummarySymbol(name: "envelope", tone: .blue)
        case "important", "bill", "payment":
            return SummarySymbol(name: "creditcard", tone: .green)
        case "availability":
            return SummarySymbol(name: "sunset", tone: .orange)
        default:
            if fallbackEmoji.contains("✅") {
                return SummarySymbol(name: "checkmark.square", tone: .green)
            }
            if fallbackEmoji.contains("📨") || fallbackEmoji.contains("✉") {
                return SummarySymbol(name: "envelope", tone: .blue)
            }
            if fallbackEmoji.contains("💸") || fallbackEmoji.contains("💳") {
                return SummarySymbol(name: "creditcard", tone: .green)
            }
            if fallbackEmoji.contains("🌄") || fallbackEmoji.contains("☀") {
                return SummarySymbol(name: "sunset", tone: .orange)
            }
            return SummarySymbol(name: "calendar", tone: .blue)
        }
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
            if agenda.isEmpty {
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Text("Today")
                        .font(TodoTypography.normal())
                        .foregroundStyle(ElectronicMailDesign.green)
                        .frame(width: 74, alignment: .leading)

                    Text("You're free today!")
                        .font(TodoTypography.normal())
                        .foregroundStyle(ElectronicMailDesign.readText(for: colorScheme))
                        .lineLimit(1)
                }
                .frame(height: TodoTypography.lineHeight)
            }

            ForEach(agenda) { item in
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Text(item.time)
                        .font(TodoTypography.normal())
                        .foregroundStyle(item.tone.color)
                        .frame(width: 82, alignment: .leading)

                    Text(item.title)
                        .font(TodoTypography.normal())
                        .foregroundStyle(ElectronicMailDesign.readText(for: colorScheme))
                        .lineLimit(1)
                }
                .frame(height: TodoTypography.lineHeight)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .electronicMailSummaryCardSurface()
    }

    private func aiBuildStatusPanel(_ text: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            ProgressView()
                .controlSize(.small)
                .frame(width: 22)

            Text(text)
                .font(TodoTypography.normal())
                .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                .lineLimit(2)
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

    private var loadingView: some View {
        VStack(spacing: 14) {
            ProgressView()
            Text("Loading to-do's")
                .font(TodoTypography.normal())
                .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
        }
    }

    private func complete(_ row: TodoRowViewModel) {
        guard !completingEntityIDs.contains(row.entityID) else {
            return
        }

        withAnimation(.easeInOut(duration: 0.18)) {
            hiddenEntityIDs.insert(row.entityID)
            completingEntityIDs.insert(row.entityID)
            completionError = nil
            if expandedItemID == row.id {
                expandedItemID = nil
            }
        }

        Task { @MainActor in
            if let todoID = row.aiTodoID,
               let todo = aiInboxStore.todoItems.first(where: { $0.id == todoID }) {
                let completed = await aiInboxStore.completeTodo(todo)
                withAnimation(.easeInOut(duration: 0.18)) {
                    completingEntityIDs.remove(row.entityID)
                    if !completed {
                        hiddenEntityIDs.remove(row.entityID)
                        completionError = "Could not complete this item. It is still visible."
                    }
                }
                return
            }
            do {
                _ = try await store.completeEntity(entityID: row.entityID)
                completingEntityIDs.remove(row.entityID)
            } catch {
                withAnimation(.easeInOut(duration: 0.18)) {
                    hiddenEntityIDs.remove(row.entityID)
                    completingEntityIDs.remove(row.entityID)
                    completionError = "Could not complete this item. It is still visible."
                }
            }
        }
    }

}

private struct SummarySymbol {
    enum Tone {
        case blue
        case green
        case orange

        var color: Color {
            switch self {
            case .blue:
                return Color(nsColor: .systemBlue)
            case .green:
                return Color(nsColor: .systemGreen)
            case .orange:
                return Color(nsColor: .systemOrange)
            }
        }
    }

    let name: String
    let tone: Tone
}

private enum TodoTypography {
    // Bind directly to the shared SF Pro mailbox scale. To-do hierarchy must
    // use the same roles as Inbox rather than parallel rounded approximations.
    static let headerSize = ElectronicMailType.mailboxHeaderSize
    static let briefSize = ElectronicMailType.mailboxHeaderSize
    static let normalSize = ElectronicMailType.bodySize
    static let detailSize = ElectronicMailType.detailSize
    static let metadataSize = ElectronicMailType.smallSize
    static let sectionSize = ElectronicMailType.sectionTitleSize
    static let lineHeight = ElectronicMailMailboxType.rowHeight
    static let tracking: CGFloat = 0
    static let sectionLabelHeight: CGFloat = 38
    static let contentTop: CGFloat = ElectronicMailShellMetrics.navTop + 24
    static let minContentWidth: CGFloat = 620
    static let maxContentWidth: CGFloat = 1000

    static func header(weight: Font.Weight = .regular) -> Font {
        .system(size: headerSize, weight: weight)
    }

    static func brief(weight: Font.Weight = .regular) -> Font {
        .system(size: briefSize, weight: weight)
    }

    static func normal(weight: Font.Weight = .regular) -> Font {
        .system(size: normalSize, weight: weight)
    }

    static func sectionTitle() -> Font {
        ElectronicMailMailboxType.section()
    }

    static func rowTitle(weight: Font.Weight = .regular) -> Font {
        .system(size: ElectronicMailMailboxType.subjectSize, weight: weight)
    }

    static func small(weight: Font.Weight = .regular) -> Font {
        .system(size: detailSize, weight: weight)
    }

    static func extraSmall(weight: Font.Weight = .regular) -> Font {
        .system(size: metadataSize, weight: weight)
    }

    static func checkbox() -> Font {
        .system(size: 18, weight: .regular)
    }
}

enum TodoPageLayout {
    static func contentWidth(for containerWidth: CGFloat) -> CGFloat {
        min(
            TodoTypography.maxContentWidth,
            max(
                TodoTypography.minContentWidth,
                containerWidth * 0.5 - ElectronicMailShellMetrics.navLeading * 2
            )
        )
    }
}

struct TodoDateTimeRail: View {
    @Environment(\.colorScheme) private var colorScheme
    let name: String
    let width: CGFloat

    var body: some View {
        TimelineView(.periodic(from: .now, by: 60)) { context in
            HStack(alignment: .firstTextBaseline) {
                greeting(for: context.date)
                Spacer(minLength: 24)
                Text(DateFormatter.todoHeaderTime.string(from: context.date))
            }
            .font(TodoTypography.header())
            .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
            .frame(width: width)
        }
        .allowsHitTesting(false)
        .accessibilityElement(children: .combine)
    }

    private func greeting(for date: Date) -> Text {
        let hour = Calendar.current.component(.hour, from: date)
        let period = hour < 12 ? "morning" : hour < 17 ? "afternoon" : "evening"
        return Text("Good \(period), ") + Text(name).fontWeight(.semibold)
    }
}

private enum SummaryToken {
    case text(String, style: SummaryTextStyle)
    case symbol(SummarySymbol)
}

private enum SummaryTextStyle {
    case supporting
    case name
    case metric
    case statement

    var weight: Font.Weight {
        switch self {
        case .supporting: .regular
        case .name: .semibold
        case .metric: .semibold
        case .statement: .semibold
        }
    }
}

private struct SummaryTokenView: View {
    let token: SummaryToken
    let colorScheme: ColorScheme

    var body: some View {
        switch token {
        case let .text(value, style):
            Text(value)
                .font(TodoTypography.brief(weight: style.weight))
                .foregroundStyle(textColor(for: style))
                .fixedSize(horizontal: true, vertical: false)
        case let .symbol(symbol):
            Image(systemName: symbol.name)
                .symbolRenderingMode(.hierarchical)
                .foregroundStyle(symbol.tone.color)
                .font(TodoTypography.brief(weight: .medium))
                .fixedSize(horizontal: true, vertical: false)
        }
    }

    private func textColor(for _: SummaryTextStyle) -> Color {
        ElectronicMailDesign.primaryText(for: colorScheme)
    }
}

private struct InlineSummaryLayout: Layout {
    let spacing: CGFloat
    let lineSpacing: CGFloat

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let maxWidth = proposal.width ?? subviews.reduce(0) { partial, subview in
            partial + subview.sizeThatFits(.unspecified).width + spacing
        }
        return measuredSize(maxWidth: maxWidth, subviews: subviews)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let sizes = subviews.map { $0.sizeThatFits(.unspecified) }
        var x = bounds.minX
        var y = bounds.minY
        var lineHeight: CGFloat = 0

        for index in subviews.indices {
            let size = sizes[index]
            if x > bounds.minX, x + size.width > bounds.maxX {
                x = bounds.minX
                y += lineHeight + lineSpacing
                lineHeight = 0
            }

            subviews[index].place(
                at: CGPoint(x: x, y: y),
                proposal: ProposedViewSize(width: size.width, height: size.height)
            )
            x += size.width + spacing
            lineHeight = max(lineHeight, size.height)
        }
    }

    private func measuredSize(maxWidth: CGFloat, subviews: Subviews) -> CGSize {
        var width: CGFloat = 0
        var x: CGFloat = 0
        var totalHeight: CGFloat = 0
        var lineHeight: CGFloat = 0

        for subview in subviews {
            let size = subview.sizeThatFits(.unspecified)
            if x > 0, x + size.width > maxWidth {
                width = max(width, x - spacing)
                totalHeight += lineHeight + lineSpacing
                x = 0
                lineHeight = 0
            }

            x += size.width + spacing
            lineHeight = max(lineHeight, size.height)
        }

        width = max(width, max(0, x - spacing))
        totalHeight += lineHeight
        return CGSize(width: min(width, maxWidth), height: totalHeight)
    }
}

private struct TodoSectionView: View {
    let section: TodoSectionModel
    let isFirstSection: Bool
    let metrics: TodoLayoutMetrics
    let colorScheme: ColorScheme
    let completingEntityIDs: Set<String>
    @Binding var expandedItemID: String?
    let collapsed: Bool
    let onToggleCollapsed: () -> Void
    let onComplete: (TodoRowViewModel) -> Void
    let onOpenSource: (String) -> Void
    let onOpenAIMatter: (String) -> Void

    init(
        section: TodoSectionModel,
        isFirstSection: Bool = false,
        metrics: TodoLayoutMetrics,
        colorScheme: ColorScheme,
        completingEntityIDs: Set<String>,
        expandedItemID: Binding<String?>,
        collapsed: Bool,
        onToggleCollapsed: @escaping () -> Void,
        onComplete: @escaping (TodoRowViewModel) -> Void,
        onOpenSource: @escaping (String) -> Void,
        onOpenAIMatter: @escaping (String) -> Void
    ) {
        self.section = section
        self.isFirstSection = isFirstSection
        self.metrics = metrics
        self.colorScheme = colorScheme
        self.completingEntityIDs = completingEntityIDs
        self._expandedItemID = expandedItemID
        self.collapsed = collapsed
        self.onToggleCollapsed = onToggleCollapsed
        self.onComplete = onComplete
        self.onOpenSource = onOpenSource
        self.onOpenAIMatter = onOpenAIMatter
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            TodoSectionHeader(
                title: section.title,
                isFirstSection: isFirstSection,
                metrics: metrics,
                colorScheme: colorScheme,
                collapsed: collapsed,
                onToggleCollapsed: onToggleCollapsed
            )

            if !collapsed {
                ForEach(section.rows) { row in
                    TodoItemRow(
                        row: row,
                        metrics: metrics,
                        expanded: expandedItemID == row.id,
                        colorScheme: colorScheme,
                        isCompleting: completingEntityIDs.contains(row.entityID),
                        onToggleExpanded: { toggleExpanded(row.id) },
                        onComplete: { onComplete(row) },
                        onOpenSource: {
                            if let matterID = row.aiMatterID {
                                onOpenAIMatter(matterID)
                            } else if let threadID = row.gmailThreadID {
                                onOpenSource(threadID)
                            }
                        }
                    )
                }
            }

        }
    }

    private func toggleExpanded(_ id: String) {
        let isClosingCurrent = expandedItemID == id

        withAnimation(isClosingCurrent ? TodoExpansionMotion.close : TodoExpansionMotion.open) {
            expandedItemID = isClosingCurrent ? nil : id
        }
    }
}

private enum TodoExpansionMotion {
    static let openDuration: TimeInterval = 0.30
    static let closeDuration: TimeInterval = 0.22
    static let open = Animation.timingCurve(0.18, 0.0, 0.12, 1.0, duration: openDuration)
    static let close = Animation.timingCurve(0.32, 0.0, 0.22, 1.0, duration: closeDuration)
}

private struct TodoSectionHeader: View {
    let title: String
    let isFirstSection: Bool
    let metrics: TodoLayoutMetrics
    let colorScheme: ColorScheme
    let collapsed: Bool
    let onToggleCollapsed: () -> Void

    var body: some View {
        let topSpacing = isFirstSection
            ? ElectronicMailControlMetrics.mailboxFirstSectionTopSpacing
            : ElectronicMailControlMetrics.mailboxSectionTopSpacing

        VStack(alignment: .leading, spacing: 0) {
            Button(action: onToggleCollapsed) {
                HStack(spacing: 7) {
                    Text(title)
                        .font(TodoTypography.sectionTitle())
                        .lineLimit(1)

                    Image(systemName: collapsed ? "chevron.right" : "chevron.down")
                        .font(.system(size: 9, weight: .semibold))
                        .accessibilityHidden(true)
                }
                .foregroundStyle(
                    ElectronicMailDesign.sectionText(for: colorScheme)
                        .opacity(ElectronicMailMailboxType.sectionOpacity)
                )
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .help(collapsed ? "Expand \(title)" : "Collapse \(title)")
            .accessibilityLabel(title)
            .accessibilityValue(collapsed ? "Collapsed" : "Expanded")
            .accessibilityAddTraits(.isHeader)
            .frame(
                width: metrics.contentWidth,
                height: TodoTypography.sectionLabelHeight,
                alignment: .leading
            )

            Divider()
        }
        .padding(.top, topSpacing)
        .frame(
            width: metrics.contentWidth,
            height: topSpacing + TodoTypography.sectionLabelHeight + 1,
            alignment: .topLeading
        )
    }
}

private struct TodoItemRow: View {
    let row: TodoRowViewModel
    let metrics: TodoLayoutMetrics
    let expanded: Bool
    let colorScheme: ColorScheme
    let isCompleting: Bool
    let onToggleExpanded: () -> Void
    let onComplete: () -> Void
    let onOpenSource: () -> Void

    @State private var revealedContentHeight: CGFloat = 0

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            rowHeader(
                checkboxX: metrics.checkboxX,
                subjectLeading: metrics.subjectLeading,
                subjectWidth: metrics.subjectWidth,
                containerWidth: metrics.contentWidth
            )

            revealedContent
        }
        .frame(width: metrics.contentWidth, alignment: .leading)
        .background(alignment: .topLeading) {
            rowSurface
                .frame(width: metrics.expandedCardWidth)
                .contentShape(RoundedRectangle(cornerRadius: 7, style: .continuous))
                .onTapGesture(perform: onToggleExpanded)
                .offset(x: metrics.expandedCardLeading)
        }
        .overlay(alignment: .topLeading) {
            rowBorder
                .frame(width: metrics.expandedCardWidth)
                .offset(x: metrics.expandedCardLeading)
        }
    }

    private var revealedContent: some View {
        expandedContentContainer
            .background {
                GeometryReader { proxy in
                    Color.clear
                        .preference(key: TodoRevealedContentHeightKey.self, value: proxy.size.height)
                }
            }
            .onPreferenceChange(TodoRevealedContentHeightKey.self) { height in
                guard height > 0, abs(revealedContentHeight - height) > 0.5 else {
                    return
                }

                if expanded {
                    withAnimation(TodoExpansionMotion.open) {
                        revealedContentHeight = height
                    }
                } else {
                    var transaction = Transaction()
                    transaction.disablesAnimations = true
                    withTransaction(transaction) {
                        revealedContentHeight = height
                    }
                }
            }
            .frame(height: expanded ? revealedContentHeight : 0, alignment: .top)
            .clipped()
            .allowsHitTesting(expanded)
            .accessibilityHidden(!expanded)
    }

    private var expandedContentContainer: some View {
        expandedContent
            .padding(.top, metrics.expandedVerticalGap)
            .padding(.bottom, metrics.expandedBottomPadding)
    }

    private var expandedContent: some View {
        VStack(alignment: .leading, spacing: metrics.expandedContentGap) {
            Text(row.detailText)
                .font(TodoTypography.small())
                .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                .lineSpacing(4)
                .fixedSize(horizontal: false, vertical: true)
                .frame(width: metrics.detailWidth, alignment: .leading)
                .offset(x: metrics.subjectLeading)

            actionRow
        }
        .frame(width: metrics.contentWidth, alignment: .leading)
    }

    private var actionRow: some View {
        ZStack(alignment: .leading) {
            HStack(spacing: 14) {
                if row.gmailThreadID != nil || row.aiMatterID != nil {
                    Button(action: onOpenSource) {
                        HStack(spacing: 6) {
                            Image(systemName: "play.fill")
                                .font(.system(size: 9, weight: .semibold))

                            Text(row.actionLabel)
                                .font(TodoTypography.small(weight: .semibold))
                        }
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(ElectronicMailDesign.green)
                    .lineLimit(1)
                    .fixedSize(horizontal: true, vertical: false)
                    .accessibilityLabel("Start task: \(row.title)")
                } else {
                    Text(row.actionLabel)
                        .font(TodoTypography.small(weight: .semibold))
                        .foregroundStyle(ElectronicMailDesign.green)
                        .lineLimit(1)
                        .fixedSize(horizontal: true, vertical: false)
                }

                if row.gmailThreadID != nil || row.aiMatterID != nil {
                    Rectangle()
                        .fill(ElectronicMailDesign.secondaryText(for: colorScheme).opacity(0.65))
                        .frame(width: 1, height: 18)

                    Button("Open source") {
                        onOpenSource()
                    }
                    .buttonStyle(.plain)
                    .font(TodoTypography.small(weight: .semibold))
                    .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                    .lineLimit(1)
                    .fixedSize(horizontal: true, vertical: false)
                }
            }
            .frame(width: metrics.footerActionWidth, alignment: .leading)
            .clipped()
            .offset(x: metrics.subjectLeading)

            Image(systemName: sourceIconName)
                .font(TodoTypography.small(weight: .semibold))
                .foregroundStyle(ElectronicMailDesign.readText(for: colorScheme))
                .frame(width: metrics.footerSourceFrameWidth, alignment: .trailing)
                .accessibilityLabel(sourceAccessibilityLabel)
                .zIndex(1)
        }
        .frame(width: metrics.contentWidth, height: TodoTypography.lineHeight, alignment: .leading)
    }

    private var sourceIconName: String {
        if row.aiMatterID != nil {
            return "sparkles"
        }
        return row.gmailThreadID == nil ? "square.and.pencil" : "tray.full"
    }

    private var sourceAccessibilityLabel: String {
        "Source: \(sourceDisplayLabel)"
    }

    private var sourceDisplayLabel: String {
        if row.aiMatterID != nil {
            return "AI Inbox"
        }
        if row.gmailThreadID != nil {
            return "Gmail"
        }

        let trimmed = row.sourceLabel
            .replacingOccurrences(of: "\n", with: " ")
            .replacingOccurrences(of: "\r", with: " ")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmed.isEmpty {
            return trimmed
        }
        return "Manual"
    }

    private func rowHeader(
        checkboxX: CGFloat,
        subjectLeading: CGFloat,
        subjectWidth: CGFloat,
        containerWidth: CGFloat
    ) -> some View {
        ZStack(alignment: .topLeading) {
            if row.allowsCompletion {
                Button(action: onComplete) {
                    completionIcon
                }
                .buttonStyle(.plain)
                .help("Complete")
                .frame(width: 26, height: TodoTypography.lineHeight)
                .offset(x: checkboxX)
            } else {
                Image(systemName: "info.circle")
                    .font(TodoTypography.small(weight: .medium))
                    .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                    .frame(width: 26, height: TodoTypography.lineHeight)
                    .offset(x: checkboxX)
                    .accessibilityLabel("Worth knowing")
            }

            Button(action: onToggleExpanded) {
                rowText(row.title, color: ElectronicMailDesign.readText(for: colorScheme))
                    .frame(width: subjectWidth, height: TodoTypography.lineHeight, alignment: .leading)
            }
            .buttonStyle(.plain)
            .contentShape(Rectangle())
            .offset(x: subjectLeading)
        }
        .frame(width: containerWidth, height: TodoTypography.lineHeight, alignment: .topLeading)
    }

    private var rowSurface: some View {
        RoundedRectangle(cornerRadius: 7, style: .continuous)
            .fill(ElectronicMailDesign.panelFill(for: colorScheme).opacity(expanded ? 1 : 0))
    }

    private var rowBorder: some View {
        RoundedRectangle(cornerRadius: 7, style: .continuous)
            .stroke(ElectronicMailDesign.panelBorder(for: colorScheme).opacity(expanded ? 1 : 0), lineWidth: 1)
    }

    @ViewBuilder
    private var completionIcon: some View {
        if isCompleting {
            Image(systemName: "checkmark.circle.fill")
                .font(TodoTypography.checkbox())
                .foregroundStyle(ElectronicMailDesign.green)
        } else {
            Image(systemName: "circle")
                .font(TodoTypography.checkbox())
                .foregroundStyle(ElectronicMailDesign.tertiaryText(for: colorScheme))
        }
    }

    private func rowText(_ value: String, color: Color, alignment: TextAlignment = .leading) -> some View {
        styledTitle(value, baseColor: color)
            .lineLimit(1)
            .truncationMode(.tail)
            .multilineTextAlignment(alignment)
    }

    private func styledTitle(_ value: String, baseColor: Color) -> Text {
        guard let actionRange = actionRange(in: value) else {
            return Text(value)
                .font(TodoTypography.rowTitle())
                .foregroundColor(baseColor)
        }

        return Text(String(value[..<actionRange.lowerBound]))
            .font(TodoTypography.rowTitle())
            .foregroundColor(baseColor)
            + Text(String(value[actionRange]))
                .font(TodoTypography.rowTitle())
                .foregroundColor(actionAccent)
            + Text(String(value[actionRange.upperBound...]))
                .font(TodoTypography.rowTitle())
                .foregroundColor(baseColor)
    }

    private func actionRange(in value: String) -> Range<String.Index>? {
        let candidates: [String]
        switch row.primaryAction.lowercased() {
        case "reply":
            candidates = ["Reply", "Respond"]
        case "confirm":
            candidates = ["RSVP", "Confirm"]
        case "pay":
            candidates = ["Pay"]
        case "track":
            candidates = ["Track"]
        case "review":
            candidates = ["Review"]
        case "join":
            candidates = ["Join"]
        case "send":
            candidates = ["Send"]
        case "approve":
            candidates = ["Approve"]
        case "register":
            candidates = ["Register"]
        case "open":
            candidates = ["Read Notice", "Review", "Open", "Read"]
        default:
            candidates = []
        }

        for candidate in candidates {
            let pattern = "\\b\(NSRegularExpression.escapedPattern(for: candidate))\\b"
            if let range = value.range(of: pattern, options: [.regularExpression, .caseInsensitive]) {
                return range
            }
        }
        return nil
    }

    private var actionAccent: Color {
        switch row.primaryAction.lowercased() {
        case "open", "pay", "track", "review":
            return ElectronicMailDesign.successAccent(for: colorScheme)
        default:
            return Color(nsColor: .systemBlue)
        }
    }

}

private struct TodoRevealedContentHeightKey: PreferenceKey {
    static var defaultValue: CGFloat = 0

    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = max(value, nextValue())
    }
}

struct TodoHomeSnapshot: Equatable {
    let greeting: String
    let summary: TodoSummary
    let agenda: [TodoAgendaItem]
    let now: TodoSectionModel
    let laterToday: TodoSectionModel
    let upcoming: TodoSectionModel
    let worthKnowing: TodoSectionModel
    let refreshWarning: String?
    let aiBuildStatus: String?
    let dashboardBuildStatus: String?
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

struct TodoSectionModel: Equatable, Identifiable {
    let id: String
    let title: String
    let rows: [TodoRowViewModel]
}

struct TodoRowViewModel: Equatable, Identifiable {
    let id: String
    let entityID: String
    let sender: String
    let title: String
    let timeLabel: String
    let detailText: String
    let primaryAction: String
    let actionLabel: String
    let sourceLabel: String
    let gmailThreadID: String?
    let aiMatterID: String?
    let aiTodoID: String?
    let allowsCompletion: Bool
}

struct TodoLayoutMetrics: Equatable {
    let contentWidth: CGFloat

    var contentLeading: CGFloat {
        min(max(contentWidth * 0.075, 56), 96)
    }

    var checkboxX: CGFloat {
        8
    }

    var subjectLeading: CGFloat {
        44
    }

    var subjectWidth: CGFloat {
        max(0, contentWidth - subjectLeading - dividerTrailing)
    }

    var detailWidth: CGFloat {
        max(0, expandedCardWidth - subjectLeading - expandedHorizontalInset)
    }

    var footerSourceFrameWidth: CGFloat {
        max(0, expandedCardWidth - subjectLeading)
    }

    var footerActionWidth: CGFloat {
        max(0, expandedCardWidth - subjectLeading - 230)
    }

    var expandedCardLeading: CGFloat {
        0
    }

    var expandedCardWidth: CGFloat {
        contentWidth
    }

    var expandedCheckboxWidth: CGFloat {
        26
    }

    var expandedContentGap: CGFloat {
        12
    }

    var expandedHorizontalInset: CGFloat {
        subjectLeading
    }

    var expandedVerticalGap: CGFloat {
        12
    }

    var expandedBottomPadding: CGFloat {
        14
    }

    var dividerTrailing: CGFloat {
        min(max(contentWidth * 0.055, 48), 86)
    }
}

enum TodoHomeMapper {
    static func snapshot(
        from session: AppSessionResponse,
        inboxRows: [InboxRowViewModel] = [],
        aiTodos: [AITodoItem] = [],
        todoOrganizingCount: Int = 0,
        now: Date = Date(),
        hiddenEntityIDs: Set<String> = [],
        refreshWarning: String? = nil
    ) -> TodoHomeSnapshot {
        let feed = session.dashboard.feed
        let inboxRowsByThreadID = Dictionary(uniqueKeysWithValues: inboxRows.map { ($0.threadID, $0) })
        let visibleTodos = aiTodos.filter { $0.status == .open }
        let actionTodos = visibleTodos.filter { $0.displayKind == .todo }
        let nowTodos = actionTodos.filter { $0.urgency == "now" }
        let laterTodos = actionTodos.filter { $0.urgency == "today" }
        let upcomingTodos = actionTodos.filter { !["now", "today"].contains($0.urgency) }
        let worthKnowingTodos = visibleTodos.filter { $0.displayKind == .worthKnowing }
        return TodoHomeSnapshot(
            greeting: greeting(from: session, now: now),
            summary: summary(from: session.dashboard, feed: feed, aiTodos: actionTodos),
            agenda: agenda(from: feed),
            now: TodoSectionModel(
                id: "now",
                title: "Now",
                rows: todoRows(
                    from: feed.now.filter { $0.source != .gmail },
                    inboxRowsByThreadID: inboxRowsByThreadID,
                    hiddenEntityIDs: hiddenEntityIDs
                ) + todoRows(from: nowTodos, hiddenEntityIDs: hiddenEntityIDs)
            ),
            laterToday: TodoSectionModel(
                id: "later-today",
                title: "Later Today",
                rows: todoRows(
                    from: feed.today.filter { $0.source != .gmail },
                    inboxRowsByThreadID: inboxRowsByThreadID,
                    hiddenEntityIDs: hiddenEntityIDs
                ) + todoRows(from: laterTodos, hiddenEntityIDs: hiddenEntityIDs)
            ),
            upcoming: TodoSectionModel(
                id: "upcoming",
                title: "Upcoming",
                rows: todoRows(from: upcomingTodos, hiddenEntityIDs: hiddenEntityIDs)
            ),
            worthKnowing: TodoSectionModel(
                id: "worth-knowing",
                title: "Worth Knowing",
                rows: todoRows(
                    from: feed.worthKnowing.filter { $0.source != .gmail },
                    inboxRowsByThreadID: inboxRowsByThreadID,
                    hiddenEntityIDs: hiddenEntityIDs
                ) + todoRows(from: worthKnowingTodos, hiddenEntityIDs: hiddenEntityIDs)
            ),
            refreshWarning: refreshWarning,
            aiBuildStatus: todoOrganizingCount > 0 && visibleTodos.isEmpty ? "Finding clear next actions" : nil,
            dashboardBuildStatus: dashboardBuildStatus(from: session, actionableMatterCount: actionTodos.count)
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

    private static func summary(
        from dashboard: DashboardResponse,
        feed: FeedResponse,
        aiTodos: [AITodoItem]
    ) -> TodoSummary {
        guard let briefing = dashboard.briefing else {
            return TodoSummary(
                fallbackBrief: "Connect Google to generate a personalized briefing.",
                parts: [],
                important: nil,
                calendarAvailability: nil
            )
        }

        let manualTaskCount = (feed.now + feed.today + feed.worthKnowing).filter { $0.source == .manual }.count
        let replyCount = aiTodos.filter { $0.actionType == "reply" }.count
        let actionableTaskCount = manualTaskCount + aiTodos.count - replyCount
        let originalParts = briefing.parts ?? []
        var parts = originalParts.filter { part in
            let type = part.type.lowercased()
            return !["tasks", "todo", "todos", "emails", "mail"].contains(type)
        }
        parts.append(DashboardBriefingPart(type: "tasks", emoji: "", count: actionableTaskCount, text: "tasks"))
        parts.append(DashboardBriefingPart(type: "emails", emoji: "", count: replyCount, text: "emails to reply"))

        return TodoSummary(
            fallbackBrief: briefing.brief,
            parts: parts,
            important: briefing.important,
            calendarAvailability: briefing.calendarAvailability
        )
    }

    private static func aiBuildStatus(from session: AppSessionResponse) -> String? {
        nil
    }

    private static func dashboardBuildStatus(from session: AppSessionResponse, actionableMatterCount: Int) -> String? {
        guard actionableMatterCount == 0 else {
            return nil
        }
        let feed = session.dashboard.feed
        let feedCount = feed.now.count + feed.today.count + feed.worthKnowing.count
        guard feedCount == 0 else {
            return nil
        }
        let readiness = session.readiness
        let importOrDashboardWorkActive = !readiness.readyToEnter
            || !readiness.dashboardReady
            || readiness.fullImportRunning
            || session.mailbox.fullImportRunning == true
            || session.sync.enrichmentPendingCount > 0
        guard importOrDashboardWorkActive else {
            return nil
        }
        switch readiness.stage {
        case "starting_full_import", "importing_recent_gmail":
            return "Syncing Gmail"
        case "grouping_threads", "writing_titles":
            return "Preparing your inbox"
        default:
            return "Syncing Gmail"
        }
    }

    private static func todoRows(
        from items: [AttentionItem],
        inboxRowsByThreadID: [String: InboxRowViewModel],
        hiddenEntityIDs: Set<String>
    ) -> [TodoRowViewModel] {
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
            .map { item in
                row(from: item, inboxRow: item.gmailThreadID.flatMap { inboxRowsByThreadID[$0] })
            }
    }

    private static func row(from item: AttentionItem, inboxRow: InboxRowViewModel?) -> TodoRowViewModel {
        TodoRowViewModel(
            id: item.id,
            entityID: item.entityID,
            sender: sender(for: item, inboxRow: inboxRow),
            title: item.title,
            timeLabel: inboxRow?.timeLabel ?? dueTimeLabel(for: item.dueAt),
            detailText: detailText(for: item),
            primaryAction: item.primaryAction,
            actionLabel: actionLabel(for: item),
            sourceLabel: sourceLabel(for: item),
            gmailThreadID: item.gmailThreadID?.isEmpty == false ? item.gmailThreadID : nil,
            aiMatterID: nil,
            aiTodoID: nil,
            allowsCompletion: true
        )
    }

    private static func todoRows(
        from items: [AITodoItem],
        hiddenEntityIDs: Set<String>
    ) -> [TodoRowViewModel] {
        items.compactMap { item in
            let entityID = "ai-todo:\(item.id)"
            guard !hiddenEntityIDs.contains(entityID) else {
                return nil
            }
            return TodoRowViewModel(
                id: entityID,
                entityID: entityID,
                sender: item.sourceLabel,
                title: item.title ?? "Open this update",
                timeLabel: item.dueAt.flatMap(todoMatterTimeLabel) ?? "",
                detailText: item.detail ?? item.evidenceText ?? "Open the source for details.",
                primaryAction: item.actionType,
                actionLabel: item.displayKind == .todo ? "Start task" : "Open source",
                sourceLabel: item.sourceAccountEmail ?? "AI Inbox",
                gmailThreadID: nil,
                aiMatterID: item.matterID,
                aiTodoID: item.id,
                allowsCompletion: item.displayKind == .todo
            )
        }
    }

    private static func todoMatterTimeLabel(_ value: String) -> String {
        guard let date = ISO8601DateFormatter.todo.date(from: value) else {
            return ""
        }
        return DateFormatter.todoTime.string(from: date)
    }

    private static func sender(for item: AttentionItem, inboxRow: InboxRowViewModel?) -> String {
        if let sender = inboxRow?.sender, !sender.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return sender
        }
        if let sourceLabel = item.detail?.sourceLabel.trimmingCharacters(in: .whitespacesAndNewlines), !sourceLabel.isEmpty {
            return sourceLabel
        }
        if item.source == .manual {
            return "Manual"
        }
        return item.source?.rawValue.capitalized ?? "Work"
    }

    private static func detailText(for item: AttentionItem) -> String {
        if let detail = item.detail, !detail.body.isEmpty {
            let body = detail.body.joined(separator: " ").trimmingCharacters(in: .whitespacesAndNewlines)
            if !body.isEmpty {
                return body
            }
        }
        let fallback = item.whyThisIsHere.trimmingCharacters(in: .whitespacesAndNewlines)
        return fallback.isEmpty ? item.title : fallback
    }

    private static func actionLabel(for item: AttentionItem) -> String {
        if let label = item.detail?.actionLabel.trimmingCharacters(in: .whitespacesAndNewlines), !label.isEmpty {
            return label
        }
        return item.primaryAction.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "Open" : item.primaryAction
    }

    private static func sourceLabel(for item: AttentionItem) -> String {
        if let label = item.detail?.sourceLabel.trimmingCharacters(in: .whitespacesAndNewlines), !label.isEmpty {
            return label
        }
        return item.source?.rawValue.capitalized ?? "Source"
    }

    private static func dueTimeLabel(for value: String?) -> String {
        guard let value, !value.isEmpty else {
            return ""
        }
        if let date = ISO8601DateFormatter.todo.date(from: value) {
            return DateFormatter.todoTime.string(from: date)
        }
        return ""
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

#if DEBUG
#Preview {
    let client = DemoAppClient()
    TodoHomeView(
        store: InboxStore(client: client),
        aiInboxStore: AIInboxStore(client: client)
    )
        .frame(width: 1100, height: 760)
}
#endif
