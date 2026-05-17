import AppKit
import SwiftUI

enum ElectronicMailShellMetrics {
    static let navTop: CGFloat = 24
    static let navLeading: CGFloat = 56
    static let navIconFrame: CGFloat = 30
    static let navHitFrame: CGFloat = 44
    static let navTitleGap: CGFloat = 16
    static let navHeaderTitleGap: CGFloat = 2
    static let contentTop: CGFloat = navTop + 72
    static let contentMaxWidth: CGFloat = 860
    static let drawerWidth: CGFloat = 360
}

public struct SignedInShellView: View {
    @Environment(\.colorScheme) private var colorScheme
    @ObservedObject private var store: InboxStore
    @State private var selection: SignedInDestination = .todo
    @State private var commandPaletteOpen = false

    public init(store: InboxStore) {
        self.store = store
    }

    public var body: some View {
        ZStack(alignment: .topLeading) {
            content
                .frame(maxWidth: .infinity, maxHeight: .infinity)

            if !store.navigationPlaceholderVisible {
                ShellHeader(
                    title: selection == .todo ? nil : selection.title,
                    colorScheme: colorScheme,
                    navigationVisible: store.navigationPlaceholderVisible,
                    onMenu: toggleNavigation
                )
                .zIndex(5)
            }

            if store.navigationPlaceholderVisible {
                navigationDrawer
                    .transition(.move(edge: .leading).combined(with: .opacity))
                    .zIndex(2)

                navigationDrawerHeader
                    .zIndex(5)
            }

            if commandPaletteOpen {
                CommandPaletteView(
                    store: store,
                    colorScheme: colorScheme,
                    onRun: runCommand,
                    onClose: closeCommandPalette
                )
                .transition(.opacity.combined(with: .scale(scale: 0.985, anchor: .top)))
                .zIndex(10)
            }

            CommandPaletteKeyboardCapture(
                isOpen: $commandPaletteOpen
            )
            .frame(width: 1, height: 1)
            .opacity(0.01)
        }
        .background(ElectronicMailDesign.background(for: colorScheme))
        .animation(.easeInOut(duration: 0.22), value: store.navigationPlaceholderVisible)
        .onReceive(NotificationCenter.default.publisher(for: .electronicMailOpenCommandPalette)) { _ in
            openCommandPalette()
        }
    }

    @ViewBuilder
    private var content: some View {
        switch selection {
        case .todo:
            TodoHomeView(store: store) { entityID in
                store.select(threadID: entityID, prefetch: false)
                withAnimation(.easeInOut(duration: 0.16)) {
                    selection = .inbox
                }
            }
            .transition(.opacity)
        case .inbox:
            InboxView(store: store)
                .transition(.opacity)
        case .calendar:
            CalendarPlaceholderView(colorScheme: colorScheme)
                .transition(.opacity)
        }
    }

    private func toggleNavigation() {
        withAnimation(.easeInOut(duration: 0.18)) {
            store.toggleNavigationPlaceholder()
        }
    }

    private func openCommandPalette() {
        withAnimation(.easeInOut(duration: 0.12)) {
            commandPaletteOpen = true
        }
    }

    private func closeCommandPalette() {
        withAnimation(.easeInOut(duration: 0.12)) {
            commandPaletteOpen = false
        }
    }

    private func runCommand(_ command: CommandPaletteItem) {
        closeCommandPalette()

        switch command.action {
        case .navigate(let destination):
            select(destination)
        case .openThread(let threadID):
            store.select(threadID: threadID, prefetch: true)
            withAnimation(.easeInOut(duration: 0.16)) {
                selection = .inbox
                store.navigationPlaceholderVisible = false
            }
        }
    }

    private var navigationDrawer: some View {
        NavigationDrawer(
            selection: selection,
            colorScheme: colorScheme,
            onSelect: select
        )
        .frame(width: ElectronicMailShellMetrics.drawerWidth)
        .frame(maxHeight: .infinity)
        .background(ElectronicMailDesign.background(for: colorScheme))
    }

    private var navigationDrawerHeader: some View {
        NavigationDrawerTopRow(
            isSelected: selection == .inbox,
            colorScheme: colorScheme,
            onToggle: toggleNavigation,
            onSelectInbox: { select(.inbox) }
        )
        .padding(.top, ElectronicMailShellMetrics.navTop)
        .padding(.leading, ElectronicMailShellMetrics.navLeading)
        .frame(width: ElectronicMailShellMetrics.drawerWidth, alignment: .leading)
    }

    private func select(_ destination: SignedInDestination) {
        withAnimation(.easeInOut(duration: 0.18)) {
            selection = destination
            store.navigationPlaceholderVisible = false
        }
    }
}

public extension Notification.Name {
    static let electronicMailOpenCommandPalette = Notification.Name("ElectronicMailOpenCommandPalette")
}

private enum CommandPaletteAction: Equatable {
    case navigate(SignedInDestination)
    case openThread(String)
}

private struct CommandPaletteItem: Identifiable, Equatable {
    let id: String
    let title: String
    let subtitle: String
    let keywords: [String]
    let priority: Int
    let kind: Kind
    let action: CommandPaletteAction

    enum Kind: Equatable {
        case navigation
        case work
        case email
    }

    var searchText: String {
        CommandPaletteSearch.normalize(([title, subtitle] + keywords).joined(separator: " "))
    }
}

private enum CommandPaletteBuilder {
    static let resultLimit = 8
    static let searchQueryMinLength = 2

    @MainActor
    static func results(
        for query: String,
        store: InboxStore
    ) -> [CommandPaletteItem] {
        let normalizedQuery = CommandPaletteSearch.normalize(query)
        let staticCommands = navigationCommands()
        let commands = normalizedQuery.count >= searchQueryMinLength
            ? staticCommands + workCommands(from: store.session) + emailCommands(from: store.sections)
            : staticCommands

        return CommandPaletteSearch
            .filter(commands: commands, query: query)
            .prefix(resultLimit)
            .map { $0 }
    }

    private static func navigationCommands() -> [CommandPaletteItem] {
        [
            CommandPaletteItem(
                id: "nav:todo",
                title: "To-do's",
                subtitle: "Go to current work",
                keywords: ["home", "today", "now", "work", "tasks"],
                priority: 20,
                kind: .navigation,
                action: .navigate(.todo)
            ),
            CommandPaletteItem(
                id: "nav:inbox",
                title: "Inbox",
                subtitle: "Open email list",
                keywords: ["gmail", "mail", "email", "threads"],
                priority: 24,
                kind: .navigation,
                action: .navigate(.inbox)
            ),
            CommandPaletteItem(
                id: "nav:calendar",
                title: "Calendar",
                subtitle: "Open calendar",
                keywords: ["events", "schedule", "meetings"],
                priority: 28,
                kind: .navigation,
                action: .navigate(.calendar)
            ),
        ]
    }

    private static func workCommands(from session: AppSessionResponse?) -> [CommandPaletteItem] {
        guard let session else {
            return []
        }

        let feed = session.dashboard.feed
        return [
            ("Now", feed.now, 0),
            ("Later Today", feed.today, 6),
            ("Worth Knowing", feed.worthKnowing, 12),
        ].flatMap { sectionTitle, items, priorityOffset in
            items.enumerated().compactMap { index, item in
                guard item.source != .calendar else {
                    return nil
                }

                let threadID = item.gmailThreadID ?? item.entityID
                let subtitle = threadID.isEmpty ? "\(sectionTitle) - Open To-do's" : "\(sectionTitle) - Open related thread"
                return CommandPaletteItem(
                    id: "work:\(item.id)",
                    title: compact(item.title.isEmpty ? item.whyThisIsHere : item.title, limit: 96),
                    subtitle: subtitle,
                    keywords: [
                        sectionTitle,
                        item.primaryAction,
                        item.whyThisIsHere,
                        item.gmailThreadID ?? "",
                        item.entityID,
                        item.dueAt ?? "",
                    ],
                    priority: priorityOffset + index,
                    kind: .work,
                    action: threadID.isEmpty ? .navigate(.todo) : .openThread(threadID)
                )
            }
        }
    }

    private static func emailCommands(from sections: [InboxSectionViewModel]) -> [CommandPaletteItem] {
        var seen = Set<String>()
        var commands: [CommandPaletteItem] = []

        for (sectionIndex, section) in sections.enumerated() {
            for (rowIndex, row) in section.rows.enumerated() {
                guard seen.insert(row.threadID).inserted else {
                    continue
                }

                commands.append(
                    CommandPaletteItem(
                        id: "email:\(row.threadID)",
                        title: "\(compact(row.sender, limit: 56)): \(compact(row.title, limit: 92))",
                        subtitle: "\(section.title) - Open email",
                        keywords: [
                            "gmail",
                            "mail",
                            "inbox",
                            "email",
                            section.title,
                            row.sender,
                            row.title,
                            row.threadID,
                        ],
                        priority: 18 + sectionIndex * 20 + rowIndex,
                        kind: .email,
                        action: .openThread(row.threadID)
                    )
                )
            }
        }

        return commands
    }

    private static func compact(_ value: String, limit: Int) -> String {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.count > limit else {
            return trimmed
        }
        let end = trimmed.index(trimmed.startIndex, offsetBy: max(0, limit - 1))
        return String(trimmed[..<end]) + "..."
    }
}

private enum CommandPaletteSearch {
    static func filter(commands: [CommandPaletteItem], query: String) -> [CommandPaletteItem] {
        let normalizedQuery = normalize(query)
        if normalizedQuery.isEmpty {
            return commands
                .filter { $0.kind == .navigation }
                .sorted(by: compare)
        }

        let tokens = normalizedQuery.split(separator: " ").map(String.init)
        return commands
            .compactMap { command -> (CommandPaletteItem, Int)? in
                let score = score(command: command, normalizedQuery: normalizedQuery, tokens: tokens)
                return score > 0 ? (command, score) : nil
            }
            .sorted { left, right in
                if left.1 != right.1 {
                    return left.1 > right.1
                }
                return compare(left.0, right.0)
            }
            .map(\.0)
    }

    static func normalize(_ value: String) -> String {
        value
            .folding(options: [.diacriticInsensitive, .caseInsensitive], locale: .current)
            .lowercased()
            .replacingOccurrences(of: #"[^a-z0-9]+"#, with: " ", options: .regularExpression)
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private static func score(
        command: CommandPaletteItem,
        normalizedQuery: String,
        tokens: [String]
    ) -> Int {
        guard tokens.allSatisfy({ command.searchText.contains($0) }) else {
            return 0
        }

        let title = normalize(command.title)
        let subtitle = normalize(command.subtitle)
        var score = 10

        if title == normalizedQuery {
            score += 90
        } else if title.hasPrefix(normalizedQuery) {
            score += 70
        } else if title.contains(normalizedQuery) {
            score += 45
        } else if subtitle.contains(normalizedQuery) {
            score += 16
        }

        score += max(0, 32 - command.priority)
        return score
    }

    private static func compare(_ left: CommandPaletteItem, _ right: CommandPaletteItem) -> Bool {
        if left.priority != right.priority {
            return left.priority < right.priority
        }
        return left.title.localizedCaseInsensitiveCompare(right.title) == .orderedAscending
    }
}

private struct CommandPaletteView: View {
    @ObservedObject var store: InboxStore
    let colorScheme: ColorScheme
    let onRun: (CommandPaletteItem) -> Void
    let onClose: () -> Void

    @State private var query = ""
    @State private var selectedIndex = 0
    @FocusState private var searchFocused: Bool

    private var results: [CommandPaletteItem] {
        CommandPaletteBuilder.results(for: query, store: store)
    }

    var body: some View {
        ZStack {
            Button(action: onClose) {
                Rectangle()
                    .fill(backdropFill)
                    .ignoresSafeArea()
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Close command palette")

            VStack(alignment: .leading, spacing: 12) {
                ZStack(alignment: .leading) {
                    if query.isEmpty {
                        Text("Command + K")
                            .font(ElectronicMailType.title())
                            .tracking(0.52)
                            .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                    }

                    TextField("", text: $query)
                        .textFieldStyle(.plain)
                        .font(ElectronicMailType.title())
                        .tracking(0.52)
                        .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                        .focused($searchFocused)
                }
                .frame(height: ElectronicMailType.bodyLineHeight)

                Rectangle()
                    .fill(ElectronicMailDesign.divider(for: colorScheme).opacity(query.isEmpty ? 0 : 1))
                    .frame(height: 1)

                if !results.isEmpty {
                    VStack(spacing: 2) {
                        ForEach(Array(results.enumerated()), id: \.element.id) { index, command in
                            CommandPaletteResultRow(
                                command: command,
                                selected: index == selectedIndex,
                                colorScheme: colorScheme
                            )
                            .contentShape(Rectangle())
                            .onTapGesture {
                                selectedIndex = index
                                onRun(command)
                            }
                        }
                    }
                    .padding(.top, query.isEmpty ? 2 : 4)
                }
            }
            .frame(width: 760)
            .shadow(color: Color.black.opacity(colorScheme == .dark ? 0.5 : 0.16), radius: 26, x: 0, y: 12)
            .background(
                CommandPaletteNavigationCapture(
                    selectedIndex: $selectedIndex,
                    resultsCount: results.count,
                    onRun: { runSelectedResult() },
                    onClose: onClose
                )
            )
        }
        .onAppear {
            DispatchQueue.main.async {
                searchFocused = true
            }
        }
        .onChange(of: query) { _, _ in
            selectedIndex = 0
        }
        .onChange(of: results.count) { _, count in
            selectedIndex = min(selectedIndex, max(0, count - 1))
        }
    }

    private var backdropFill: Color {
        ElectronicMailDesign.background(for: colorScheme).opacity(colorScheme == .dark ? 0.82 : 0.62)
    }

    private func runSelectedResult() {
        guard results.indices.contains(selectedIndex) else {
            return
        }
        onRun(results[selectedIndex])
    }
}

private struct CommandPaletteResultRow: View {
    let command: CommandPaletteItem
    let selected: Bool
    let colorScheme: ColorScheme

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: symbolName)
                .font(ElectronicMailType.detail(weight: .semibold))
                .foregroundStyle(selected ? ElectronicMailDesign.selectedText(for: colorScheme) : ElectronicMailDesign.secondaryText(for: colorScheme))
                .frame(width: 26)

            VStack(alignment: .leading, spacing: 3) {
                Text(command.title)
                    .font(ElectronicMailType.detail(weight: .semibold))
                    .foregroundStyle(selected ? ElectronicMailDesign.selectedText(for: colorScheme) : ElectronicMailDesign.primaryText(for: colorScheme))
                    .lineLimit(1)

                Text(command.subtitle)
                    .font(ElectronicMailType.small())
                    .foregroundStyle(selected ? ElectronicMailDesign.selectedText(for: colorScheme).opacity(0.78) : ElectronicMailDesign.secondaryText(for: colorScheme))
                    .lineLimit(1)
            }

            Spacer(minLength: 16)
        }
        .frame(height: 46)
        .padding(.horizontal, 12)
        .background(
            RoundedRectangle(cornerRadius: 6, style: .continuous)
                .fill(selected ? ElectronicMailDesign.appleBlue : Color.clear)
        )
    }

    private var symbolName: String {
        switch command.kind {
        case .navigation:
            return "arrow.turn.down.right"
        case .work:
            return "checklist"
        case .email:
            return "envelope"
        }
    }
}

private struct CommandPaletteKeyboardCapture: NSViewRepresentable {
    @Binding var isOpen: Bool

    func makeNSView(context: Context) -> KeyCaptureView {
        let view = KeyCaptureView()
        view.onToggle = {
            withAnimation(.easeInOut(duration: 0.12)) {
                isOpen = true
            }
        }
        view.installMonitorIfNeeded()
        return view
    }

    func updateNSView(_ nsView: KeyCaptureView, context: Context) {
        nsView.onToggle = {
            withAnimation(.easeInOut(duration: 0.12)) {
                isOpen = true
            }
        }
        nsView.installMonitorIfNeeded()
    }

    final class KeyCaptureView: NSView {
        var onToggle: (() -> Void)?
        private var monitor: Any?

        deinit {
            if let monitor {
                NSEvent.removeMonitor(monitor)
            }
        }

        func installMonitorIfNeeded() {
            guard monitor == nil else {
                return
            }

            monitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
                guard
                    event.modifierFlags.intersection(.deviceIndependentFlagsMask).contains(.command),
                    !event.modifierFlags.intersection(.deviceIndependentFlagsMask).contains(.shift),
                    !event.modifierFlags.intersection(.deviceIndependentFlagsMask).contains(.option),
                    event.charactersIgnoringModifiers?.lowercased() == "k"
                else {
                    return event
                }

                self?.onToggle?()
                return nil
            }
        }
    }
}

private struct CommandPaletteNavigationCapture: NSViewRepresentable {
    @Binding var selectedIndex: Int
    let resultsCount: Int
    let onRun: () -> Void
    let onClose: () -> Void

    func makeNSView(context: Context) -> NavigationView {
        let view = NavigationView()
        view.configure(
            selectedIndex: $selectedIndex,
            resultsCount: resultsCount,
            onRun: onRun,
            onClose: onClose
        )
        view.installMonitorIfNeeded()
        return view
    }

    func updateNSView(_ nsView: NavigationView, context: Context) {
        nsView.configure(
            selectedIndex: $selectedIndex,
            resultsCount: resultsCount,
            onRun: onRun,
            onClose: onClose
        )
        nsView.installMonitorIfNeeded()
    }

    final class NavigationView: NSView {
        private var selectedIndex: Binding<Int>?
        private var resultsCount = 0
        private var onRun: (() -> Void)?
        private var onClose: (() -> Void)?
        private var monitor: Any?

        deinit {
            if let monitor {
                NSEvent.removeMonitor(monitor)
            }
        }

        func configure(
            selectedIndex: Binding<Int>,
            resultsCount: Int,
            onRun: @escaping () -> Void,
            onClose: @escaping () -> Void
        ) {
            self.selectedIndex = selectedIndex
            self.resultsCount = resultsCount
            self.onRun = onRun
            self.onClose = onClose
        }

        func installMonitorIfNeeded() {
            guard monitor == nil else {
                return
            }

            monitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
                self?.handle(event) ?? event
            }
        }

        private func handle(_ event: NSEvent) -> NSEvent? {
            switch event.keyCode {
            case 53:
                onClose?()
                return nil
            case 125:
                moveSelection(by: 1)
                return nil
            case 126:
                moveSelection(by: -1)
                return nil
            case 36, 76:
                onRun?()
                return nil
            default:
                return event
            }
        }

        private func moveSelection(by delta: Int) {
            guard resultsCount > 0, let selectedIndex else {
                return
            }

            selectedIndex.wrappedValue = (selectedIndex.wrappedValue + delta + resultsCount) % resultsCount
        }
    }
}

private enum SignedInDestination {
    case todo
    case inbox
    case calendar

    var title: String {
        switch self {
        case .todo:
            return "To-do's"
        case .inbox:
            return "Inbox"
        case .calendar:
            return "Calendar"
        }
    }
}

private struct ShellHeader: View {
    let title: String?
    let colorScheme: ColorScheme
    let navigationVisible: Bool
    let onMenu: () -> Void

    var body: some View {
        HStack(spacing: ElectronicMailShellMetrics.navHeaderTitleGap) {
            ShellMenuButton(
                colorScheme: colorScheme,
                accessibilityLabel: navigationVisible ? "Hide navigation" : "Show navigation",
                action: onMenu
            )

            if let title {
                Text(title)
                    .font(ElectronicMailType.title())
                    .tracking(0.52)
                    .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                    .frame(height: ElectronicMailType.bodyLineHeight, alignment: .center)
            }
        }
        .padding(.top, ElectronicMailShellMetrics.navTop)
        .padding(.leading, ElectronicMailShellMetrics.navLeading)
    }
}

private struct ShellMenuButton: View {
    let colorScheme: ColorScheme
    let accessibilityLabel: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            ElectronicMailHamburgerIcon()
                .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                .frame(width: ElectronicMailShellMetrics.navIconFrame, height: ElectronicMailShellMetrics.navIconFrame)
                .frame(
                    width: ElectronicMailShellMetrics.navHitFrame,
                    height: ElectronicMailShellMetrics.navHitFrame,
                    alignment: .leading
                )
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .frame(width: ElectronicMailShellMetrics.navHitFrame, height: ElectronicMailShellMetrics.navHitFrame)
        .contentShape(Rectangle())
        .help(accessibilityLabel)
        .accessibilityLabel(accessibilityLabel)
    }
}

private struct CalendarPlaceholderView: View {
    let colorScheme: ColorScheme

    var body: some View {
        ZStack {
            ElectronicMailDesign.background(for: colorScheme)
                .ignoresSafeArea()

            VStack(alignment: .leading, spacing: 18) {
                Text("Calendar view will live here.")
                    .font(ElectronicMailType.body())
                    .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
            }
            .frame(maxWidth: 760, maxHeight: .infinity, alignment: .topLeading)
            .padding(.top, ElectronicMailShellMetrics.contentTop)
            .padding(.horizontal, ElectronicMailShellMetrics.navLeading)
        }
    }
}

private struct NavigationDrawer: View {
    let selection: SignedInDestination
    let colorScheme: ColorScheme
    let onSelect: (SignedInDestination) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            NavigationDrawerLabel(title: "Drafts", colorScheme: colorScheme)
            NavigationDrawerLabel(title: "Sent", colorScheme: colorScheme)
            NavigationDrawerLabel(title: "Trash", colorScheme: colorScheme)

            NavigationDrawerItem(
                title: "Calendar",
                isSelected: selection == .calendar,
                colorScheme: colorScheme
            ) {
                onSelect(.calendar)
            }

            NavigationDrawerItem(
                title: "To-do's",
                isSelected: selection == .todo,
                colorScheme: colorScheme
            ) {
                onSelect(.todo)
            }

            Spacer(minLength: 0)
        }
        .padding(.top, ElectronicMailShellMetrics.navTop + ElectronicMailType.bodyLineHeight + 18)
        .padding(.leading, ElectronicMailShellMetrics.navLeading)
        .padding(.trailing, 28)
        .padding(.bottom, 28)
    }
}

private struct NavigationDrawerTopRow: View {
    let isSelected: Bool
    let colorScheme: ColorScheme
    let onToggle: () -> Void
    let onSelectInbox: () -> Void

    var body: some View {
        HStack(spacing: ElectronicMailShellMetrics.navHeaderTitleGap) {
            ShellMenuButton(
                colorScheme: colorScheme,
                accessibilityLabel: "Hide navigation",
                action: onToggle
            )

            Button(action: onSelectInbox) {
                Text("Inbox")
                    .font(ElectronicMailType.title())
                    .tracking(0.52)
                    .foregroundStyle(isSelected ? ElectronicMailDesign.appleBlue : ElectronicMailDesign.primaryText(for: colorScheme))
                    .frame(height: ElectronicMailType.bodyLineHeight, alignment: .center)
                    .opacity(isSelected ? 1 : 0.94)
            }
            .buttonStyle(.plain)
            .help("Inbox")
            .accessibilityLabel("Inbox")
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct NavigationDrawerItem: View {
    let title: String
    var symbolName: String?
    let isSelected: Bool
    let colorScheme: ColorScheme
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: ElectronicMailShellMetrics.navTitleGap) {
                if let symbolName {
                    Image(systemName: symbolName)
                        .font(ElectronicMailType.icon(weight: .semibold))
                        .frame(width: ElectronicMailShellMetrics.navIconFrame, alignment: .center)
                } else {
                    Color.clear
                        .frame(width: ElectronicMailShellMetrics.navIconFrame, height: 1)
                }

                Text(title)
                    .font(ElectronicMailType.title())
                    .tracking(0.52)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .foregroundStyle(isSelected ? ElectronicMailDesign.appleBlue : ElectronicMailDesign.primaryText(for: colorScheme))
            .opacity(isSelected ? 1 : 0.94)
        }
        .buttonStyle(.plain)
        .help(title)
        .accessibilityLabel(title)
    }
}

private struct NavigationDrawerLabel: View {
    let title: String
    let colorScheme: ColorScheme

    var body: some View {
        Text(title)
            .font(ElectronicMailType.title())
            .tracking(0.52)
            .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.leading, ElectronicMailShellMetrics.navIconFrame + ElectronicMailShellMetrics.navTitleGap)
    }
}
