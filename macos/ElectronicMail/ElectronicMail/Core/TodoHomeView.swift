import SwiftUI

public struct TodoHomeView: View {
    @Environment(\.colorScheme) private var colorScheme
    @ObservedObject private var store: InboxStore
    @State private var hiddenEntityIDs: Set<String> = []
    @State private var completingEntityIDs: Set<String> = []
    @State private var completionError: String?
    @State private var draftTitle = ""
    @State private var draftNotes = ""
    @State private var composerSectionID: String?
    @State private var creatingTask = false
    @State private var expandedItemID: String?

    private let onCompose: () -> Void
    private let onOpenSource: (String) -> Void

    public init(
        store: InboxStore,
        onCompose: @escaping () -> Void = {},
        onOpenSource: @escaping (String) -> Void = { _ in }
    ) {
        self.store = store
        self.onCompose = onCompose
        self.onOpenSource = onOpenSource
    }

    public var body: some View {
        ZStack {
            ElectronicMailDesign.background(for: colorScheme)
                .ignoresSafeArea()

            if let session = store.session {
                content(for: TodoHomeMapper.snapshot(
                    from: session,
                    inboxRows: store.flatRows,
                    now: Date(),
                    hiddenEntityIDs: hiddenEntityIDs
                ))

                if store.refreshFailed {
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
        }
        .environment(\.font, .body)
    }

    private func content(for snapshot: TodoHomeSnapshot) -> some View {
        GeometryReader { proxy in
            let contentWidth = min(
                TodoTypography.maxContentWidth,
                max(
                    TodoTypography.minContentWidth,
                    proxy.size.width * 0.5 - ElectronicMailShellMetrics.navLeading * 2
                )
            )
            let metrics = TodoLayoutMetrics(contentWidth: contentWidth)

            ScrollViewReader { scrollProxy in
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

                            TodoSectionView(
                                section: snapshot.now,
                                metrics: metrics,
                                colorScheme: colorScheme,
                                completingEntityIDs: completingEntityIDs,
                                expandedItemID: $expandedItemID,
                                trailingButton: AnyView(addButton(for: snapshot.now, scrollProxy: scrollProxy)),
                                footer: AnyView(composer(for: snapshot.now)),
                                onComplete: complete,
                                onOpenSource: onOpenSource
                            )

                            TodoSectionView(
                                section: snapshot.laterToday,
                                metrics: metrics,
                                colorScheme: colorScheme,
                                completingEntityIDs: completingEntityIDs,
                                expandedItemID: $expandedItemID,
                                trailingButton: AnyView(addButton(for: snapshot.laterToday, scrollProxy: scrollProxy)),
                                footer: AnyView(composer(for: snapshot.laterToday)),
                                onComplete: complete,
                                onOpenSource: onOpenSource
                            )

                            TodoSectionView(
                                section: snapshot.worthKnowing,
                                metrics: metrics,
                                colorScheme: colorScheme,
                                completingEntityIDs: completingEntityIDs,
                                expandedItemID: $expandedItemID,
                                trailingButton: AnyView(addButton(for: snapshot.worthKnowing, scrollProxy: scrollProxy)),
                                footer: AnyView(composer(for: snapshot.worthKnowing)),
                                onComplete: complete,
                                onOpenSource: onOpenSource
                            )
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
    }

    private func header(snapshot: TodoHomeSnapshot) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                Text(snapshot.greeting)
                    .font(TodoTypography.normal(weight: .regular))
                    .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))

                Spacer(minLength: 24)

                Text(snapshot.headerContextLabel)
                    .font(TodoTypography.normal(weight: .regular))
                    .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))

            }

        }
    }

    private func summaryBlock(_ summary: TodoSummary) -> some View {
        InlineSummaryLayout(spacing: 0, lineSpacing: 6) {
            ForEach(Array(summaryTokens(summary).enumerated()), id: \.offset) { _, token in
                SummaryTokenView(token: token, colorScheme: colorScheme)
            }
        }
        .frame(maxWidth: 820, alignment: .leading)
    }

    private func summaryTokens(_ summary: TodoSummary) -> [SummaryToken] {
        let parts = summary.parts.filter { !$0.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
        guard !parts.isEmpty else {
            return [.text(summary.fallbackBrief, style: .muted)]
        }

        var tokens: [SummaryToken] = [.text("You have ", style: .muted)]

        for index in parts.indices {
            let separator = summarySeparator(index: index, count: parts.count)
            if !separator.isEmpty {
                tokens.append(.text(separator, style: .muted))
            }
            tokens.append(contentsOf: summaryPartTokens(parts[index]))
        }

        tokens.append(.text(".", style: .muted))

        if let important = summary.important {
            tokens.append(.text(" You also have ", style: .muted))
            tokens.append(.symbol(summarySymbol(type: "important", fallbackEmoji: important.emoji)))
            tokens.append(.text(" ", style: .muted))
            tokens.append(.text(summaryCountPhrase(count: important.count, text: important.text), style: .emphasis))
            tokens.append(.text(".", style: .muted))
        }

        if let availability = summary.calendarAvailability {
            tokens.append(.text(" You are ", style: .muted))
            tokens.append(.symbol(summarySymbol(type: "availability", fallbackEmoji: availability.emoji)))
            tokens.append(.text(" ", style: .muted))
            tokens.append(.text(availability.text, style: .emphasis))
            tokens.append(.text(".", style: .muted))
        }

        return tokens
    }

    private func summaryPartTokens(_ part: DashboardBriefingPart) -> [SummaryToken] {
        let symbol = summarySymbol(type: part.type, fallbackEmoji: part.emoji)
        if part.count == 0 {
            return [
                .symbol(symbol),
                .text(" no \(part.text)", style: .muted),
            ]
        }

        return [
            .symbol(symbol),
            .text(" ", style: .muted),
            .text(summaryCountPhrase(count: part.count, text: part.text), style: .emphasis),
        ]
    }

    private func summarySymbol(type: String?, fallbackEmoji: String) -> SummarySymbol {
        let normalized = type?.lowercased() ?? ""
        switch normalized {
        case "meetings", "calendar":
            return SummarySymbol(name: "calendar")
        case "tasks", "todo", "todos":
            return SummarySymbol(name: "checkmark.square")
        case "emails", "mail":
            return SummarySymbol(name: "envelope")
        case "important", "bill", "payment":
            return SummarySymbol(name: "creditcard")
        case "availability":
            return SummarySymbol(name: "sunset")
        default:
            if fallbackEmoji.contains("✅") {
                return SummarySymbol(name: "checkmark.square")
            }
            if fallbackEmoji.contains("📨") || fallbackEmoji.contains("✉") {
                return SummarySymbol(name: "envelope")
            }
            if fallbackEmoji.contains("💸") || fallbackEmoji.contains("💳") {
                return SummarySymbol(name: "creditcard")
            }
            if fallbackEmoji.contains("🌄") || fallbackEmoji.contains("☀") {
                return SummarySymbol(name: "sunset")
            }
            return SummarySymbol(name: "calendar")
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
                        .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                        .lineLimit(1)
                }
                .frame(height: TodoTypography.lineHeight)
            }

            ForEach(agenda) { item in
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Text(item.time)
                        .font(TodoTypography.normal())
                        .foregroundStyle(item.tone.color)
                        .frame(width: 74, alignment: .leading)

                    Text(item.title)
                        .font(TodoTypography.normal())
                        .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                        .lineLimit(1)
                }
                .frame(height: TodoTypography.lineHeight)
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

    private func addButton(for section: TodoSectionModel, scrollProxy: ScrollViewProxy) -> some View {
        ElectronicMailIconControl(
            symbol: "plus",
            accessibilityLabel: "Add to-do"
        ) {
            let willOpen = composerSectionID != section.id
            withAnimation(.easeInOut(duration: 0.16)) {
                composerSectionID = composerSectionID == section.id ? nil : section.id
            }
            if willOpen {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.05) {
                    withAnimation(.easeInOut(duration: 0.18)) {
                        scrollProxy.scrollTo(composerID(for: section.id), anchor: .center)
                    }
                }
            }
        }
    }

    private func composerID(for sectionID: String) -> String {
        "todo-composer-\(sectionID)"
    }

    private func composer(for section: TodoSectionModel) -> some View {
        Group {
            if composerSectionID == section.id {
                VStack(alignment: .leading, spacing: 14) {
                    HStack(alignment: .center, spacing: 16) {
                        Image(systemName: "circle")
                            .font(TodoTypography.normal())
                            .foregroundStyle(ElectronicMailDesign.tertiaryText(for: colorScheme))
                            .frame(width: 26)

                        TextField("New to-do", text: $draftTitle)
                            .textFieldStyle(.plain)
                            .font(TodoTypography.normal(weight: .semibold))
                            .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                            .onSubmit { createManualTask(in: section) }
                    }

                    TextField("Notes", text: $draftNotes, axis: .vertical)
                        .textFieldStyle(.plain)
                        .font(TodoTypography.small())
                        .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                        .lineLimit(2...4)
                        .padding(.leading, 42)

                    if let completionError {
                        Text(completionError)
                            .font(TodoTypography.small())
                            .foregroundStyle(Color.red.opacity(0.82))
                            .padding(.leading, 42)
                    }

                    HStack(spacing: 10) {
                        Spacer()
                        Button {
                            resetComposer()
                        } label: {
                            Text("Cancel")
                                .padding(.horizontal, 13)
                                .frame(minHeight: ElectronicMailControlMetrics.actionHeight)
                        }
                        .buttonStyle(.bordered)
                        .buttonBorderShape(.capsule)
                        .font(TodoTypography.small(weight: .regular))

                        Button {
                            createManualTask(in: section)
                        } label: {
                            Group {
                                if creatingTask {
                                    ProgressView()
                                        .controlSize(.small)
                                } else {
                                    Text("Add")
                                }
                            }
                            .padding(.horizontal, 16)
                            .frame(minHeight: ElectronicMailControlMetrics.actionHeight)
                        }
                        .buttonStyle(.borderedProminent)
                        .buttonBorderShape(.capsule)
                        .font(TodoTypography.small(weight: .regular))
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
                .id(composerID(for: section.id))
                .transition(.opacity.combined(with: .move(edge: .top)))
            }
        }
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

    private func createManualTask(in section: TodoSectionModel) {
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
                    section: section.backendSection
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
        composerSectionID = nil
        draftTitle = ""
        draftNotes = ""
        completionError = nil
    }
}

private struct SummarySymbol {
    let name: String
}

private enum TodoTypography {
    static let normalSize: CGFloat = 15
    static let lineHeight: CGFloat = 34
    static let tracking: CGFloat = 0
    static let sectionRowTopGap: CGFloat = 10
    static let contentTop: CGFloat = ElectronicMailShellMetrics.navTop + 8
    static let minContentWidth: CGFloat = 620
    static let maxContentWidth: CGFloat = 1000

    static func normal(weight: Font.Weight = .regular) -> Font {
        .system(size: normalSize, weight: weight)
    }

    static func sectionTitle() -> Font {
        ElectronicMailType.sectionTitle()
    }

    static func rowTitle() -> Font {
        ElectronicMailType.body(weight: .regular)
    }

    static func small(weight: Font.Weight = .regular) -> Font {
        ElectronicMailType.detail(weight: weight)
    }

    static func extraSmall(weight: Font.Weight = .regular) -> Font {
        ElectronicMailType.small(weight: weight)
    }

    static func checkbox() -> Font {
        .system(size: 15, weight: .regular)
    }
}

private enum SummaryToken {
    case text(String, style: SummaryTextStyle)
    case symbol(SummarySymbol)
}

private enum SummaryTextStyle {
    case muted
    case emphasis
}

private struct SummaryTokenView: View {
    let token: SummaryToken
    let colorScheme: ColorScheme

    var body: some View {
        switch token {
        case let .text(value, style):
            Text(value)
                .font(TodoTypography.small(weight: .regular))
                .foregroundStyle(textColor(for: style))
                .fixedSize(horizontal: true, vertical: false)
        case let .symbol(symbol):
            Image(systemName: symbol.name)
                .symbolRenderingMode(.multicolor)
                .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                .font(TodoTypography.small(weight: .regular))
                .fixedSize(horizontal: true, vertical: false)
        }
    }

    private func textColor(for style: SummaryTextStyle) -> Color {
        switch style {
        case .muted:
            return ElectronicMailDesign.secondaryText(for: colorScheme)
        case .emphasis:
            return ElectronicMailDesign.primaryText(for: colorScheme)
        }
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
    let metrics: TodoLayoutMetrics
    let colorScheme: ColorScheme
    let completingEntityIDs: Set<String>
    @Binding var expandedItemID: String?
    var trailingButton: AnyView?
    var footer: AnyView?
    let onComplete: (TodoRowViewModel) -> Void
    let onOpenSource: (String) -> Void

    init(
        section: TodoSectionModel,
        metrics: TodoLayoutMetrics,
        colorScheme: ColorScheme,
        completingEntityIDs: Set<String>,
        expandedItemID: Binding<String?>,
        trailingButton: AnyView? = nil,
        footer: AnyView? = nil,
        onComplete: @escaping (TodoRowViewModel) -> Void,
        onOpenSource: @escaping (String) -> Void
    ) {
        self.section = section
        self.metrics = metrics
        self.colorScheme = colorScheme
        self.completingEntityIDs = completingEntityIDs
        self._expandedItemID = expandedItemID
        self.trailingButton = trailingButton
        self.footer = footer
        self.onComplete = onComplete
        self.onOpenSource = onOpenSource
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            TodoSectionHeader(
                title: section.title,
                metrics: metrics,
                colorScheme: colorScheme,
                trailingButton: trailingButton
            )

            Spacer()
                .frame(height: TodoTypography.sectionRowTopGap)

            footer

            if section.rows.isEmpty {
                TodoEmptyRow(metrics: metrics, colorScheme: colorScheme)
            } else {
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
                            guard let threadID = row.gmailThreadID else { return }
                            onOpenSource(threadID)
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
    let metrics: TodoLayoutMetrics
    let colorScheme: ColorScheme
    let trailingButton: AnyView?

    var body: some View {
        ZStack(alignment: .topLeading) {
            Text(title)
                .font(TodoTypography.sectionTitle())
                .tracking(TodoTypography.tracking)
                .foregroundStyle(ElectronicMailDesign.sectionText(for: colorScheme))
                .lineLimit(1)
                .frame(height: TodoTypography.lineHeight)

            if let trailingButton {
                trailingButton
                    .offset(x: metrics.contentWidth - 30)
            }

            Rectangle()
                .fill(ElectronicMailDesign.divider(for: colorScheme))
                .frame(width: metrics.contentWidth, height: 1)
                .offset(y: TodoTypography.lineHeight)
        }
        .frame(width: metrics.contentWidth, height: TodoTypography.lineHeight + 3, alignment: .topLeading)
    }
}

private struct TodoEmptyRow: View {
    let metrics: TodoLayoutMetrics
    let colorScheme: ColorScheme

    var body: some View {
        Text("Nothing here.")
            .font(TodoTypography.normal())
            .tracking(TodoTypography.tracking)
            .foregroundStyle(ElectronicMailDesign.readText(for: colorScheme))
            .frame(width: metrics.subjectWidth, height: TodoTypography.lineHeight, alignment: .leading)
            .offset(x: metrics.subjectLeading)
            .frame(width: metrics.contentWidth, height: TodoTypography.lineHeight, alignment: .topLeading)
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
                Text(row.actionLabel)
                    .font(TodoTypography.small(weight: .semibold))
                    .foregroundStyle(ElectronicMailDesign.green)
                    .lineLimit(1)
                    .fixedSize(horizontal: true, vertical: false)

                if row.gmailThreadID != nil {
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
        row.gmailThreadID == nil ? "square.and.pencil" : "tray.full"
    }

    private var sourceAccessibilityLabel: String {
        "Source: \(sourceDisplayLabel)"
    }

    private var sourceDisplayLabel: String {
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
            Button(action: onComplete) {
                completionIcon
            }
            .buttonStyle(.plain)
            .help("Complete")
            .frame(width: 26, height: TodoTypography.lineHeight)
            .offset(x: checkboxX)

            Button(action: onToggleExpanded) {
                rowText(row.title, color: ElectronicMailDesign.primaryText(for: colorScheme))
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
        Text(value)
            .font(TodoTypography.rowTitle())
            .foregroundStyle(color)
            .lineLimit(1)
            .truncationMode(.tail)
            .multilineTextAlignment(alignment)
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
    let timeLabel: String
    let headerContextLabel: String
    let summary: TodoSummary
    let agenda: [TodoAgendaItem]
    let now: TodoSectionModel
    let laterToday: TodoSectionModel
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

    var backendSection: String {
        switch id {
        case "now":
            return "now"
        case "worth-knowing":
            return "later"
        default:
            return "today"
        }
    }
}

struct TodoRowViewModel: Equatable, Identifiable {
    let id: String
    let entityID: String
    let sender: String
    let title: String
    let timeLabel: String
    let detailText: String
    let actionLabel: String
    let sourceLabel: String
    let gmailThreadID: String?
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
        now: Date = Date(),
        hiddenEntityIDs: Set<String> = [],
        refreshWarning: String? = nil
    ) -> TodoHomeSnapshot {
        let feed = session.dashboard.feed
        let inboxRowsByThreadID = Dictionary(uniqueKeysWithValues: inboxRows.map { ($0.threadID, $0) })
        return TodoHomeSnapshot(
            greeting: greeting(from: session, now: now),
            timeLabel: DateFormatter.todoHeaderTime.string(from: now),
            headerContextLabel: "35°C",
            summary: summary(from: session.dashboard),
            agenda: agenda(from: feed),
            now: TodoSectionModel(
                id: "now",
                title: "Now",
                rows: todoRows(from: feed.now, inboxRowsByThreadID: inboxRowsByThreadID, hiddenEntityIDs: hiddenEntityIDs)
            ),
            laterToday: TodoSectionModel(
                id: "later-today",
                title: "Later Today",
                rows: todoRows(from: feed.today, inboxRowsByThreadID: inboxRowsByThreadID, hiddenEntityIDs: hiddenEntityIDs)
            ),
            worthKnowing: TodoSectionModel(
                id: "worth-knowing",
                title: "Worth Knowing",
                rows: todoRows(from: feed.worthKnowing, inboxRowsByThreadID: inboxRowsByThreadID, hiddenEntityIDs: hiddenEntityIDs)
            ),
            refreshWarning: refreshWarning,
            aiBuildStatus: aiBuildStatus(from: session),
            dashboardBuildStatus: dashboardBuildStatus(from: session)
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

    private static func aiBuildStatus(from session: AppSessionResponse) -> String? {
        nil
    }

    private static func dashboardBuildStatus(from session: AppSessionResponse) -> String? {
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
            title: inboxRow?.title ?? item.title,
            timeLabel: inboxRow?.timeLabel ?? dueTimeLabel(for: item.dueAt),
            detailText: detailText(for: item),
            actionLabel: actionLabel(for: item),
            sourceLabel: sourceLabel(for: item),
            gmailThreadID: item.gmailThreadID?.isEmpty == false ? item.gmailThreadID : nil
        )
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
    TodoHomeView(store: InboxStore(client: DemoAppClient()))
        .frame(width: 1100, height: 760)
}
#endif
