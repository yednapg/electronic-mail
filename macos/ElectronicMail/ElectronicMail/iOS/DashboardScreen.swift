import ElectronicMailShared
import SwiftUI

struct DashboardScreen: View {
    @ObservedObject var store: DashboardStore
    let onSignOut: () -> Void
    @State private var selectedMode: DashboardMode = .inbox

    var body: some View {
        NavigationStack {
            content
                .toolbar(.hidden, for: .navigationBar)
                .sheet(item: selectedThreadBinding) { thread in
                    ThreadDetailView(thread: thread)
                }
        }
    }

    @ViewBuilder
    private var content: some View {
        switch store.phase {
        case .idle, .loading:
            ProgressView("Loading inbox")
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .task {
                    await store.load()
                }
        case .failed(let message):
            ContentUnavailableView(
                "Inbox unavailable",
                systemImage: "exclamationmark.triangle",
                description: Text(message)
            )
            .refreshable {
                AppHaptics.lightImpact()
                await store.refresh()
            }
        case .loaded:
            if let snapshot = store.snapshot {
                DashboardModeContainer(
                    selectedMode: $selectedMode,
                    snapshot: snapshot,
                    inboxSnapshot: store.inboxSnapshot,
                    store: store,
                    onSignOut: onSignOut
                )
            } else {
                ContentUnavailableView("Inbox is empty", systemImage: "tray")
            }
        }
    }

    private var selectedThreadBinding: Binding<ThreadDetailViewModel?> {
        Binding(
            get: { store.selectedThread },
            set: { value in
                if value == nil {
                    store.clearSelectedThread()
                }
            }
        )
    }
}

private enum DashboardMode: String, CaseIterable, Identifiable {
    case todo
    case inbox

    var id: String { rawValue }

    var title: String {
        switch self {
        case .todo:
            return "To-do's"
        case .inbox:
            return "Inbox"
        }
    }

    var iconName: String {
        switch self {
        case .todo:
            return "checklist"
        case .inbox:
            return "tray.full"
        }
    }
}

private struct DashboardModeContainer: View {
    @Binding var selectedMode: DashboardMode
    let snapshot: DashboardSnapshot
    let inboxSnapshot: MobileInboxSnapshot?
    @ObservedObject var store: DashboardStore
    let onSignOut: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            DashboardTopBar(
                selectedMode: $selectedMode,
                syncing: store.syncing,
                onRefresh: {
                    AppHaptics.lightImpact()
                    Task { await store.triggerSyncAndRefresh() }
                },
                onSignOut: onSignOut
            )

            switch selectedMode {
            case .todo:
                DashboardContentView(snapshot: snapshot, store: store)
            case .inbox:
                InboxContentView(snapshot: inboxSnapshot, store: store)
            }
        }
        .background(IOSDashboardPalette.background.ignoresSafeArea())
        .preferredColorScheme(.dark)
    }
}

private enum IOSDashboardPalette {
    static let background = Color.black
    static let surface = Color.white.opacity(0.075)
    static let line = Color.white.opacity(0.10)
    static let primary = Color.white.opacity(0.92)
    static let secondary = Color.white.opacity(0.58)
    static let tertiary = Color.white.opacity(0.36)
    static let green = Color(red: 0.24, green: 0.78, blue: 0.22)
    static let blue = Color(red: 0.05, green: 0.44, blue: 1.0)
}

private struct DashboardTopBar: View {
    @Binding var selectedMode: DashboardMode
    let syncing: Bool
    let onRefresh: () -> Void
    let onSignOut: () -> Void

    var body: some View {
        HStack(spacing: 14) {
            Menu {
                ForEach(DashboardMode.allCases) { mode in
                    Button {
                        guard selectedMode != mode else {
                            return
                        }
                        AppHaptics.selection()
                        selectedMode = mode
                    } label: {
                        Label(mode.title, systemImage: selectedMode == mode ? "checkmark" : mode.iconName)
                    }
                }

                Divider()

                Button(action: onRefresh) {
                    Label("Refresh", systemImage: syncing ? "arrow.triangle.2.circlepath.circle.fill" : "arrow.clockwise")
                }

                Button(role: .destructive, action: onSignOut) {
                    Label("Sign out", systemImage: "rectangle.portrait.and.arrow.right")
                }
            } label: {
                Image(systemName: "line.3.horizontal")
                    .font(.system(size: 24, weight: .semibold))
                    .foregroundStyle(IOSDashboardPalette.primary)
                    .frame(width: 34, height: 34)
                    .contentShape(Rectangle())
            }
            .accessibilityLabel("Open navigation menu")

            Text(selectedMode.title)
                .font(.system(size: 29, weight: .bold, design: .rounded))
                .foregroundStyle(IOSDashboardPalette.primary)
                .lineLimit(1)
                .minimumScaleFactor(0.82)

            Spacer(minLength: 12)

            Button {
                AppHaptics.lightImpact()
            } label: {
                Image(systemName: "magnifyingglass")
                    .font(.system(size: 20, weight: .semibold))
                    .foregroundStyle(IOSDashboardPalette.primary)
                    .frame(width: 40, height: 40)
                    .background(IOSDashboardPalette.surface, in: Circle())
                    .overlay(
                        Circle()
                            .stroke(IOSDashboardPalette.line, lineWidth: 1)
                    )
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Search \(selectedMode.title)")
        }
        .padding(.horizontal, 20)
        .padding(.top, 14)
        .padding(.bottom, 12)
    }
}

private struct DashboardContentView: View {
    let snapshot: DashboardSnapshot
    @ObservedObject var store: DashboardStore

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 30) {
                TodayStatusCard(snapshot: snapshot)

                ForEach(snapshot.sections) { section in
                    DashboardSectionBlock(section: section, store: store)
                }

                if !snapshot.hasVisibleWork {
                    ContentUnavailableView("Nothing needs attention", systemImage: "checkmark.circle")
                        .foregroundStyle(IOSDashboardPalette.secondary)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 30)
                }
            }
            .padding(.horizontal, 20)
            .padding(.top, 14)
            .padding(.bottom, 46)
        }
        .refreshable {
            AppHaptics.lightImpact()
            await store.triggerSyncAndRefresh()
        }
        .overlay(alignment: .bottom) {
            VStack(spacing: 8) {
                if snapshot.fullImportRunning {
                    StatusPill(text: "Processing older mail in background.")
                }

                if let warning = snapshot.refreshWarning {
                    StatusPill(text: warning)
                }
            }
            .padding(.bottom, 14)
        }
        .background(IOSDashboardPalette.background)
    }
}

private struct TodayStatusCard: View {
    let snapshot: DashboardSnapshot

    private var availabilityText: String {
        if let text = snapshot.summary.calendarAvailability?.text.trimmingCharacters(in: .whitespacesAndNewlines), !text.isEmpty {
            return text
        }

        if snapshot.agenda.isEmpty {
            return "You're free today"
        }

        return "\(snapshot.agenda.count) calendar item\(snapshot.agenda.count == 1 ? "" : "s") today"
    }

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: "calendar")
                .font(.system(size: 17, weight: .semibold))
                .foregroundStyle(IOSDashboardPalette.green)
                .frame(width: 22)

            Text("Today")
                .font(.system(size: 16, weight: .bold, design: .rounded))
                .foregroundStyle(IOSDashboardPalette.green)

            Text(availabilityText)
                .font(.system(size: 16, weight: .semibold, design: .rounded))
                .foregroundStyle(IOSDashboardPalette.primary)
                .lineLimit(1)
                .minimumScaleFactor(0.82)

            Spacer(minLength: 0)

            Image(systemName: "chevron.right")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(IOSDashboardPalette.secondary)
        }
        .padding(.horizontal, 16)
        .frame(height: 64)
        .frame(maxWidth: .infinity)
        .background(IOSDashboardPalette.surface, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .stroke(IOSDashboardPalette.line, lineWidth: 1)
        )
    }
}

private struct InboxContentView: View {
    let snapshot: MobileInboxSnapshot?
    @ObservedObject var store: DashboardStore

    var body: some View {
        Group {
            if let snapshot, !snapshot.isEmpty {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 0, pinnedViews: []) {
                        ForEach(Array(snapshot.sections.enumerated()), id: \.element.id) { index, section in
                            InboxSectionBlock(section: section, store: store, isFirstSection: index == 0)
                        }
                    }
                    .padding(.top, 6)
                    .padding(.bottom, 104)
                }
                .refreshable {
                    AppHaptics.lightImpact()
                    await store.triggerSyncAndRefresh()
                }
                .overlay(alignment: .bottom) {
                    VStack(spacing: 12) {
                        if snapshot.fullImportRunning {
                            StatusPill(text: "Importing older mail.")
                        }

                        ComposeButton()
                    }
                    .padding(.trailing, 18)
                    .padding(.bottom, 18)
                    .frame(maxWidth: .infinity, alignment: .trailing)
                }
            } else {
                ContentUnavailableView("Inbox is empty", systemImage: "tray")
                    .foregroundStyle(IOSDashboardPalette.secondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(IOSDashboardPalette.background)
            }
        }
        .background(IOSDashboardPalette.background)
    }
}

private struct ComposeButton: View {
    var body: some View {
        Button {
            AppHaptics.lightImpact()
        } label: {
            Image(systemName: "plus")
                .font(.system(size: 24, weight: .semibold))
                .foregroundStyle(.white)
                .frame(width: 58, height: 58)
                .background(IOSDashboardPalette.blue, in: Circle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Compose")
    }
}

private struct InboxSectionBlock: View {
    let section: MobileInboxSectionViewModel
    @ObservedObject var store: DashboardStore
    let isFirstSection: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(section.title)
                .font(.system(size: 18, weight: .bold, design: .rounded))
                .foregroundStyle(IOSDashboardPalette.tertiary)
                .padding(.horizontal, 20)
                .padding(.top, isFirstSection ? 0 : 22)
                .padding(.bottom, 9)

            Rectangle()
                .fill(IOSDashboardPalette.line)
                .frame(height: 0.5)
                .padding(.leading, 20)

            ForEach(section.rows) { row in
                InboxRowView(
                    row: row,
                    onOpen: {
                        AppHaptics.selection()
                        Task { await store.openThread(for: row) }
                    }
                )
            }
        }
    }
}

private struct InboxRowView: View {
    let row: MobileInboxRowViewModel
    let onOpen: () -> Void

    var body: some View {
        Button(action: onOpen) {
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 5) {
                    Text(row.sender)
                        .font(.system(size: 16, weight: row.unread ? .semibold : .medium, design: .rounded))
                        .foregroundStyle(row.dimmed ? IOSDashboardPalette.tertiary : IOSDashboardPalette.primary)
                        .lineLimit(1)

                    Text(row.subject)
                        .font(.system(size: 15, weight: row.unread ? .medium : .regular, design: .rounded))
                        .foregroundStyle(row.dimmed ? IOSDashboardPalette.tertiary : IOSDashboardPalette.secondary)
                        .lineLimit(2)

                    Text(previewText)
                        .font(.system(size: 13, weight: .regular, design: .rounded))
                        .foregroundStyle(IOSDashboardPalette.tertiary)
                        .lineLimit(2)
                }
                .layoutPriority(1)

                VStack(alignment: .trailing, spacing: 8) {
                    Text(row.timeLabel)
                        .font(.system(size: 13, weight: row.unread ? .medium : .regular, design: .rounded))
                        .foregroundStyle(row.dimmed ? IOSDashboardPalette.tertiary : IOSDashboardPalette.secondary)
                        .lineLimit(1)

                    if row.grouped {
                        ThreadIndicatorIcon()
                    }
                }
                .frame(minWidth: 58, alignment: .trailing)
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 12)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .background(IOSDashboardPalette.background)
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(IOSDashboardPalette.line)
                .frame(height: 0.5)
                .padding(.leading, 20)
        }
    }

    private var previewText: String {
        let subject = row.subject.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !subject.isEmpty else {
            return "Open this conversation to review the latest email and next step."
        }
        return "Latest message about \(subject). Open to review the details and next step."
    }
}

private struct ThreadIndicatorIcon: View {
    var body: some View {
        Image(systemName: "chevron.right.circle")
            .font(.system(size: 17, weight: .semibold))
            .foregroundStyle(IOSDashboardPalette.blue)
            .accessibilityLabel("Grouped thread")
    }
}

private struct DashboardSectionBlock: View {
    let section: DashboardSectionViewModel
    @ObservedObject var store: DashboardStore

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .center, spacing: 9) {
                Text(displayTitle)
                    .font(.system(size: 20, weight: .bold, design: .rounded))
                    .foregroundStyle(IOSDashboardPalette.tertiary)

                Text("\(section.items.count)")
                    .font(.system(size: 13, weight: .bold, design: .rounded))
                    .foregroundStyle(IOSDashboardPalette.tertiary)
                    .padding(.horizontal, 7)
                    .padding(.vertical, 3)
                    .background(IOSDashboardPalette.surface, in: Capsule())

                Spacer()

                Button {
                    AppHaptics.lightImpact()
                } label: {
                    Image(systemName: "plus")
                        .font(.system(size: 18, weight: .bold))
                        .foregroundStyle(IOSDashboardPalette.primary)
                        .frame(width: 32, height: 32)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Add item to \(displayTitle)")
            }
            .padding(.bottom, 9)

            Rectangle()
                .fill(IOSDashboardPalette.line)
                .frame(height: 0.5)

            if section.items.isEmpty {
                Text("Nothing here yet.")
                    .font(.system(size: 15, weight: .regular, design: .rounded))
                    .foregroundStyle(IOSDashboardPalette.tertiary)
                    .padding(.top, 14)
            } else {
                VStack(spacing: 0) {
                    ForEach(section.items) { item in
                        DashboardItemRow(
                            item: item,
                            actionState: store.actionStates[item.id] ?? .idle,
                            onOpen: {
                                AppHaptics.selection()
                                Task { await store.openThread(for: item) }
                            },
                            onDismiss: {
                                AppHaptics.selection()
                                store.dismissItem(item.id)
                            },
                            onAction: { action in
                                AppHaptics.lightImpact()
                                Task {
                                    await store.performAction(action, for: item.id)
                                    if case .done = store.actionStates[item.id] {
                                        AppHaptics.success()
                                    } else if case .failed = store.actionStates[item.id] {
                                        AppHaptics.error()
                                    }
                                }
                            }
                        )
                    }
                }
            }
        }
    }

    private var displayTitle: String {
        section.id == "today" ? "Later Today" : section.title
    }
}

private struct DashboardItemRow: View {
    let item: DashboardSectionItemViewModel
    let actionState: DashboardActionState
    let onOpen: () -> Void
    let onDismiss: () -> Void
    let onAction: (DashboardItemActionViewModel) -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 13) {
            Button(action: onDismiss) {
                Image(systemName: "circle")
                    .font(.system(size: 21, weight: .regular))
                    .foregroundStyle(IOSDashboardPalette.secondary)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Mark done")

            Button(action: onOpen) {
                VStack(alignment: .leading, spacing: 5) {
                    Text(item.title)
                        .font(.system(size: 16, weight: .semibold, design: .rounded))
                        .foregroundStyle(IOSDashboardPalette.primary)
                        .lineLimit(2)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .buttonStyle(.plain)
        }
        .padding(.vertical, 12)
        .contentShape(Rectangle())
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(IOSDashboardPalette.line)
                .frame(height: 0.5)
                .padding(.leading, 34)
        }
        .contextMenu {
            Button("Open", action: onOpen)
            Button("Mark done", action: onDismiss)

            if let action = item.action, action.canRunOnBackend {
                Button(actionLabel(for: action, state: actionState)) {
                    onAction(action)
                }
            }
        }
    }

    private func actionLabel(for action: DashboardItemActionViewModel, state: DashboardActionState) -> String {
        switch state {
        case .idle:
            return action.label
        case .loading:
            return "Working"
        case .done:
            return "Done"
        case .failed:
            return "Try again"
        }
    }
}

private struct ThreadDetailView: View {
    let thread: ThreadDetailViewModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                if let summary = thread.summary, !summary.isEmpty {
                    Section("Summary") {
                        Text(summary)
                    }
                }

                Section(thread.sourceLabel) {
                    ForEach(thread.messages) { message in
                        VStack(alignment: .leading, spacing: 8) {
                            Text(message.sender)
                                .font(.subheadline.weight(.semibold))

                            Text(message.subject)
                                .font(.subheadline)
                                .foregroundStyle(.secondary)

                            Text(message.body)
                                .font(.body)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        .padding(.vertical, 6)
                    }
                }
            }
            .navigationTitle(thread.title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") {
                        dismiss()
                    }
                }
            }
        }
    }
}

private struct StatusPill: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.footnote.weight(.medium))
            .foregroundStyle(.secondary)
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(.regularMaterial, in: Capsule())
    }
}
