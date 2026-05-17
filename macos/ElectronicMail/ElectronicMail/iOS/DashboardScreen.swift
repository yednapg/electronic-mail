import ElectronicMailShared
import SwiftUI

struct DashboardScreen: View {
    @ObservedObject var store: DashboardStore
    let onSignOut: () -> Void
    @State private var selectedMode: DashboardMode = .todo

    var body: some View {
        NavigationStack {
            content
                .navigationTitle(selectedMode.title)
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .topBarLeading) {
                        Button(action: onSignOut) {
                            Image(systemName: "rectangle.portrait.and.arrow.right")
                        }
                        .accessibilityLabel("Sign out")
                    }

                    ToolbarItem(placement: .topBarTrailing) {
                        Button {
                            AppHaptics.lightImpact()
                            Task { await store.triggerSyncAndRefresh() }
                        } label: {
                            Image(systemName: store.syncing ? "arrow.triangle.2.circlepath.circle.fill" : "arrow.clockwise")
                        }
                        .accessibilityLabel("Refresh dashboard")
                    }
                }
                .sheet(item: selectedThreadBinding) { thread in
                    ThreadDetailView(thread: thread)
                }
        }
    }

    @ViewBuilder
    private var content: some View {
        switch store.phase {
        case .idle, .loading:
            ProgressView("Loading dashboard")
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .task {
                    await store.load()
                }
        case .failed(let message):
            ContentUnavailableView(
                "Dashboard unavailable",
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
                    store: store
                )
            } else {
                ContentUnavailableView("Dashboard is empty", systemImage: "tray")
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
            return "To-do"
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

    var body: some View {
        VStack(spacing: 0) {
            Picker("Mode", selection: $selectedMode) {
                ForEach(DashboardMode.allCases) { mode in
                    Label(mode.title, systemImage: mode.iconName)
                        .tag(mode)
                }
            }
            .pickerStyle(.segmented)
            .padding(.horizontal, 16)
            .padding(.top, 12)
            .padding(.bottom, 10)
            .background(Color(.systemBackground))
            .onChange(of: selectedMode) { _, _ in
                AppHaptics.selection()
            }

            switch selectedMode {
            case .todo:
                DashboardContentView(snapshot: snapshot, store: store)
            case .inbox:
                InboxContentView(snapshot: inboxSnapshot, store: store)
            }
        }
        .background(Color(.systemBackground))
    }
}

private struct DashboardContentView: View {
    let snapshot: DashboardSnapshot
    @ObservedObject var store: DashboardStore

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 24) {
                DashboardSummaryBlock(snapshot: snapshot)

                DashboardAgendaBlock(items: snapshot.agenda)

                ForEach(snapshot.sections) { section in
                    DashboardSectionBlock(section: section, store: store)
                }

                if !snapshot.hasVisibleWork {
                    ContentUnavailableView("Nothing needs attention", systemImage: "checkmark.circle")
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 30)
                }
            }
            .padding(.horizontal, 18)
            .padding(.vertical, 22)
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
        .background(Color(.systemGroupedBackground))
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
                        ForEach(snapshot.sections) { section in
                            InboxSectionBlock(section: section, store: store)
                        }
                    }
                    .padding(.top, 16)
                    .padding(.bottom, 32)
                }
                .refreshable {
                    AppHaptics.lightImpact()
                    await store.triggerSyncAndRefresh()
                }
                .overlay(alignment: .bottom) {
                    if snapshot.fullImportRunning {
                        StatusPill(text: "Importing older mail.")
                            .padding(.bottom, 14)
                    }
                }
            } else {
                ContentUnavailableView("Inbox is empty", systemImage: "tray")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(Color(.systemBackground))
            }
        }
        .background(Color(.systemBackground))
    }
}

private struct InboxSectionBlock: View {
    let section: MobileInboxSectionViewModel
    @ObservedObject var store: DashboardStore

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(section.title)
                .font(.system(size: 15, weight: .bold, design: .rounded))
                .foregroundStyle(.secondary)
                .padding(.horizontal, 20)
                .padding(.top, section.id == "today" ? 0 : 26)
                .padding(.bottom, 8)

            Rectangle()
                .fill(Color(.separator).opacity(0.7))
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
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Image(systemName: row.grouped ? "chevron.right.circle" : "circle.fill")
                    .font(.system(size: row.grouped ? 16 : 6, weight: .semibold))
                    .foregroundStyle(row.grouped ? Color.accentColor : Color.clear)
                    .frame(width: 20)

                VStack(alignment: .leading, spacing: 4) {
                    HStack(alignment: .firstTextBaseline, spacing: 10) {
                        Text(row.sender)
                            .font(.system(size: 16, weight: row.unread ? .semibold : .regular, design: .rounded))
                            .foregroundStyle(row.dimmed ? .secondary : .primary)
                            .lineLimit(1)

                        Spacer(minLength: 8)

                        Text(row.timeLabel)
                            .font(.system(size: 14, weight: row.unread ? .semibold : .regular, design: .rounded))
                            .foregroundStyle(row.dimmed ? .tertiary : .secondary)
                            .lineLimit(1)
                    }

                    Text(row.subject)
                        .font(.system(size: 16, weight: row.unread ? .semibold : .regular, design: .rounded))
                        .foregroundStyle(row.dimmed ? .tertiary : .primary)
                        .lineLimit(1)
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 9)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .background(Color(.systemBackground))
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(Color(.separator).opacity(0.45))
                .frame(height: 0.5)
                .padding(.leading, 54)
        }
    }
}

private struct DashboardSummaryBlock: View {
    let snapshot: DashboardSnapshot

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Text(snapshot.dateLabel)
                Spacer()
                Text(snapshot.timeLabel)
            }
            .font(.footnote)
            .foregroundStyle(.secondary)

            VStack(alignment: .leading, spacing: 8) {
                Text(snapshot.summary.headline)
                    .font(.system(size: 34, weight: .bold, design: .rounded))
                    .fixedSize(horizontal: false, vertical: true)

                Text(snapshot.summary.brief)
                    .font(.body)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

private struct DashboardAgendaBlock: View {
    let items: [DashboardAgendaItemViewModel]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Calendar")
                .font(.headline)

            if items.isEmpty {
                Text("No calendar items right now.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            } else {
                VStack(alignment: .leading, spacing: 10) {
                    ForEach(items) { item in
                        HStack(alignment: .firstTextBaseline, spacing: 12) {
                            Text(item.time)
                                .font(.subheadline.weight(.semibold))
                                .foregroundStyle(color(for: item.tone))
                                .frame(width: 74, alignment: .leading)

                            Text(item.title)
                                .font(.subheadline)
                                .foregroundStyle(.primary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
            }
        }
    }

    private func color(for tone: DashboardAgendaItemViewModel.Tone) -> Color {
        switch tone {
        case .blue:
            return .blue
        case .green:
            return .green
        case .teal:
            return .teal
        case .lime:
            return .mint
        }
    }
}

private struct DashboardSectionBlock: View {
    let section: DashboardSectionViewModel
    @ObservedObject var store: DashboardStore

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Text(section.title)
                    .font(.title3.weight(.bold))

                Text("\(section.items.count)")
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(.secondary)
            }

            if section.items.isEmpty {
                Text("Nothing here yet.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            } else {
                VStack(spacing: 10) {
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
}

private struct DashboardItemRow: View {
    let item: DashboardSectionItemViewModel
    let actionState: DashboardActionState
    let onOpen: () -> Void
    let onDismiss: () -> Void
    let onAction: (DashboardItemActionViewModel) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 12) {
                Button(action: onDismiss) {
                    Image(systemName: "circle")
                        .font(.title3)
                }
                .buttonStyle(.plain)
                .foregroundStyle(.secondary)
                .accessibilityLabel("Mark done")

                Button(action: onOpen) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(item.title)
                            .font(.body.weight(.medium))
                            .foregroundStyle(.primary)
                            .fixedSize(horizontal: false, vertical: true)

                        if let detail = item.detail {
                            Text(detail.actionLabel)
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                                .lineLimit(2)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .buttonStyle(.plain)
            }

            if let action = item.action, action.canRunOnBackend {
                Button {
                    onAction(action)
                } label: {
                    HStack(spacing: 8) {
                        if actionState == .loading {
                            ProgressView()
                        }
                        Text(actionLabel(for: action, state: actionState))
                            .font(.footnote.weight(.semibold))
                    }
                    .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .tint(action.tone == .blue ? .blue : .green)
                .disabled(actionState == .loading || actionState == .done)
            }
        }
        .padding(14)
        .background(Color(.secondarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
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
