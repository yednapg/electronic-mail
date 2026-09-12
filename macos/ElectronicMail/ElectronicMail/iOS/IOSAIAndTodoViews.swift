import ElectronicMailShared
import SwiftUI

struct IOSAIInboxView: View {
    @ObservedObject var store: AIInboxStore
    @Bindable var router: IOSRouter

    @State private var searchText = ""
    @State private var searchTask: Task<Void, Never>?

    var body: some View {
        Group {
            if let profile = store.profile, profile.consented, profile.enabled {
                matterList
            } else if store.loading, store.response == nil {
                ProgressView("Organizing AI Inbox…")
            } else {
                consentView
            }
        }
        .navigationTitle("AI Inbox")
        .accessibilityIdentifier("ai-inbox.screen")
        .navigationBarTitleDisplayMode(.large)
        .searchable(text: $searchText, prompt: "Search matters")
        .onChange(of: searchText) { _, value in
            searchTask?.cancel()
            searchTask = Task {
                try? await Task.sleep(for: .milliseconds(350))
                guard !Task.isCancelled else { return }
                await store.refresh(query: value)
            }
        }
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    router.sheet = .aiOrganization
                } label: {
                    Image(systemName: "slider.horizontal.3")
                }
                .minimumTouchTarget()
                .accessibilityLabel("AI Inbox settings")
            }
        }
        .task { await store.refresh() }
        .onReceive(NotificationCenter.default.publisher(for: .electronicMailAIInboxChanged)) { _ in
            Task { await store.refresh(query: searchText) }
        }
    }

    private var matterList: some View {
        List {
            if store.stale, let message = store.errorMessage {
                Section {
                    Label(message, systemImage: "exclamationmark.triangle")
                        .font(.footnote)
                        .foregroundStyle(.orange)
                }
            }

            if let response = store.response {
                if !response.organizing.isEmpty {
                    let organizing = response.organizing.sorted(by: { $0.latestMessageAt > $1.latestMessageAt })
                    Section("Organizing") {
                        ForEach(0..<organizing.count, id: \.self) { index in
                            let row = organizing[index]
                            HStack(spacing: 12) {
                                ProgressView()
                                VStack(alignment: .leading, spacing: 3) {
                                    Text(row.title).font(.subheadline.weight(.semibold))
                                    Text(row.error ?? row.state.replacingOccurrences(of: "_", with: " ").capitalized)
                                        .font(.caption)
                                        .foregroundStyle(row.error == nil ? Color.secondary : Color.red)
                                }
                            }
                            .accessibilityElement(children: .combine)
                        }
                    }
                }

                Section("Matters") {
                    let matters = response.matters.sorted(by: { $0.latestMessageAt > $1.latestMessageAt })
                    ForEach(0..<matters.count, id: \.self) { index in
                        let matter = matters[index]
                        Button {
                            router.path.append(.aiMatter(matter.id))
                        } label: {
                            IOSMatterRow(matter: matter)
                        }
                        .buttonStyle(.plain)
                    }
                }
            }

            if store.response?.matters.isEmpty == true, store.response?.organizing.isEmpty == true {
                ContentUnavailableView.search(text: searchText)
                    .listRowBackground(Color.clear)
            }
        }
        .listStyle(.insetGrouped)
        .refreshable { await store.refresh(query: searchText) }
    }

    private var consentView: some View {
        ContentUnavailableView {
            Label("Organize mail into matters", systemImage: "sparkles.rectangle.stack")
        } description: {
            Text("AI Inbox groups related messages and highlights what needs you. Inbox remains available even if AI organization is off.")
        } actions: {
            Button("Enable focused matters") {
                Task { await store.enable(style: .focused) }
            }
            .buttonStyle(.borderedProminent)
            Button("Review privacy details") {
                router.sheet = .aiOrganization
            }
        }
    }
}

private struct IOSMatterRow: View {
    let matter: AIMatterRow

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Circle()
                .fill(statusColor.opacity(0.15))
                .frame(width: 40, height: 40)
                .overlay {
                    Image(systemName: statusSymbol).foregroundStyle(statusColor)
                }
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text(matter.title)
                        .font(.system(.body, design: .rounded, weight: matter.unread ? .bold : .semibold))
                        .lineLimit(2)
                    Spacer()
                    if matter.starred { Image(systemName: "star.fill").foregroundStyle(.yellow) }
                }
                Text(matter.summary)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
                HStack(spacing: 8) {
                    Text(matter.status.label)
                    Text("\(matter.messageCount) emails")
                    if let reviews = matter.reviewCount, reviews > 0 { Text("\(reviews) to review") }
                }
                .font(.caption2)
                .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 5)
        .accessibilityElement(children: .combine)
        .accessibilityHint("Opens AI matter")
    }

    private var statusColor: Color {
        switch matter.status {
        case .needsYou, .failed: .orange
        case .waitingOnOthers, .inProgress, .partiallyCompleted: .blue
        case .completed: .green
        case .cancelled, .informational: .secondary
        }
    }

    private var statusSymbol: String {
        switch matter.status {
        case .needsYou: "person.fill.questionmark"
        case .waitingOnOthers: "clock"
        case .inProgress, .partiallyCompleted: "arrow.triangle.2.circlepath"
        case .completed: "checkmark"
        case .failed: "exclamationmark"
        case .cancelled: "xmark"
        case .informational: "info"
        }
    }
}

struct IOSAIMatterView: View {
    @ObservedObject var store: AIInboxStore
    @Bindable var router: IOSRouter
    let gmailAccountID: String?
    let matterID: String

    @State private var showingTrashConfirmation = false
    @State private var preview: IOSQuickLookItem?
    @State private var attachmentError: String?

    var body: some View {
        Group {
            if let detail = store.detail, detail.id == matterID {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 14) {
                        VStack(alignment: .leading, spacing: 8) {
                            Label(detail.status.label, systemImage: "sparkles")
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(IOSMailDesign.accent)
                            Text(detail.title)
                                .font(.system(.title2, design: .rounded, weight: .bold))
                            Text(detail.summary)
                                .foregroundStyle(.secondary)
                            if detail.stableGoal != detail.summary, !detail.stableGoal.isEmpty {
                                Text(detail.stableGoal).font(.subheadline)
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(18)
                        .mailCard()

                        if let proposals = detail.reviewProposals, !proposals.isEmpty {
                            ForEach(proposals) { proposal in
                                VStack(alignment: .leading, spacing: 10) {
                                    Label("Review grouping", systemImage: "questionmark.bubble")
                                        .font(.headline)
                                    Text(proposal.subject).font(.subheadline.weight(.semibold))
                                    Text(proposal.explanation).font(.subheadline).foregroundStyle(.secondary)
                                    HStack {
                                        Button("Keep") { Task { await store.decideProposal("keep", matter: detail, proposal: proposal) } }
                                            .buttonStyle(.borderedProminent)
                                        Button("Remove") { Task { await store.decideProposal("remove", matter: detail, proposal: proposal) } }
                                            .buttonStyle(.bordered)
                                    }
                                }
                                .padding(16)
                                .mailCard()
                            }
                        }

                        if let reader = store.mobileMatterReader {
                            ForEach(reader.messages) { message in
                                VStack(alignment: .leading, spacing: 10) {
                                    HStack {
                                        Text(message.from).font(.headline)
                                        Spacer()
                                        Text(message.receivedAt).font(.caption).foregroundStyle(.secondary)
                                    }
                                    Text(message.subject).font(.subheadline.weight(.semibold))
                                    if let html = message.html, !html.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                                        IOSMailWebView(html: html)
                                    } else {
                                        Text(message.body).textSelection(.enabled)
                                    }
                                    ForEach(message.attachments) { attachment in
                                        Button {
                                            Task { await openAttachment(attachment) }
                                        } label: {
                                            Label(attachment.filename, systemImage: "doc")
                                                .frame(maxWidth: .infinity, alignment: .leading)
                                                .padding(12)
                                                .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 10))
                                        }
                                        .buttonStyle(.plain)
                                    }
                                }
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(16)
                                .mailCard()
                            }
                        }
                    }
                    .padding(16)
                }
                .toolbar {
                    ToolbarItemGroup(placement: .bottomBar) {
                        Button("Archive", systemImage: "archivebox") {
                            Task { _ = await store.perform(.archive, matter: detail) }
                        }
                        Spacer()
                        Button("Reply", systemImage: "arrowshape.turn.up.left") {
                            guard let message = store.mobileMatterReader?.messages.last,
                                  let threadID = message.threadID else { return }
                            router.composer = .reply(
                                mode: .reply,
                                threadID: threadID,
                                message: message,
                                gmailAccountID: gmailAccountID
                            )
                        }
                        Spacer()
                        Button("Trash", systemImage: "trash", role: .destructive) {
                            showingTrashConfirmation = true
                        }
                    }
                }
                .confirmationDialog("Move this matter to Trash?", isPresented: $showingTrashConfirmation) {
                    Button("Move to Trash", role: .destructive) {
                        Task { _ = await store.perform(.moveTrash, matter: detail, confirmMultiThreadTrash: true) }
                    }
                } message: {
                    Text("Every conversation grouped in this matter will be moved.")
                }
            } else if store.detailLoading {
                ProgressView("Loading matter…")
            } else {
                ContentUnavailableView("Matter unavailable", systemImage: "sparkles", description: Text(store.errorMessage ?? "Pull to refresh AI Inbox."))
            }
        }
        .navigationTitle("Matter")
        .navigationBarTitleDisplayMode(.inline)
        .task(id: matterID) { await store.select(matterID) }
        .onDisappear { store.closeDetail() }
        .sheet(item: $preview) { item in
            IOSQuickLookPreview(item: item).ignoresSafeArea()
        }
        .alert("Attachment unavailable", isPresented: Binding(
            get: { attachmentError != nil },
            set: { if !$0 { attachmentError = nil } }
        )) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(attachmentError ?? "The attachment could not be opened.")
        }
    }

    @MainActor
    private func openAttachment(_ attachment: MobileAttachmentPresentation) async {
        do {
            let payload = try await store.downloadMobileAttachment(
                messageID: attachment.messageID,
                attachmentID: attachment.id
            )
            let directory = FileManager.default.temporaryDirectory
                .appendingPathComponent("ElectronicMail-Matter-Preview-\(UUID().uuidString)", isDirectory: true)
            try FileManager.default.createDirectory(
                at: directory,
                withIntermediateDirectories: true,
                attributes: [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication]
            )
            let filename = payload.filename
                .replacingOccurrences(of: "/", with: "-")
                .replacingOccurrences(of: ":", with: "-")
            let fileURL = directory.appendingPathComponent(filename.isEmpty ? "Attachment" : filename)
            try payload.data.write(to: fileURL, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
            preview = IOSQuickLookItem(url: fileURL, title: payload.filename)
        } catch is CancellationError {
            return
        } catch {
            attachmentError = error.localizedDescription
            AppHaptics.error()
        }
    }
}

struct IOSTodoView: View {
    @ObservedObject var inboxStore: InboxStore
    @ObservedObject var aiStore: AIInboxStore
    @Bindable var router: IOSRouter

    @Environment(\.colorScheme) private var colorScheme
    @State private var expandedIDs: Set<String> = []
    @State private var todoError: String?

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 18, pinnedViews: [.sectionHeaders]) {
                header
                aiTodoSection
                if let snapshot = inboxStore.mobileTodoSnapshot {
                    agendaSection(snapshot)
                    ForEach(snapshot.sections) { section in
                        todoSection(section)
                    }
                } else if aiStore.todoItems.isEmpty {
                    ContentUnavailableView("No to-do's yet", systemImage: "checklist", description: Text("Pull to refresh after mail finishes syncing."))
                }
            }
            .padding(16)
        }
        .background(IOSMailDesign.canvas(colorScheme))
        .navigationTitle("To-do's")
        .accessibilityIdentifier("todo.screen")
        .navigationBarTitleDisplayMode(.large)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    router.sheet = .newTask
                } label: {
                    Image(systemName: "plus")
                }
                .minimumTouchTarget()
                .accessibilityLabel("New to-do")
            }
        }
        .refreshable {
            async let mailbox: Void = inboxStore.refresh()
            async let todos: Void = aiStore.refreshTodos()
            _ = await (mailbox, todos)
        }
        .task { await aiStore.refreshTodos() }
        .alert("To-do could not update", isPresented: Binding(
            get: { todoError != nil },
            set: { if !$0 { todoError = nil } }
        )) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(todoError ?? "Try again.")
        }
    }

    private var header: some View {
        TimelineView(.periodic(from: .now, by: 60)) { context in
            VStack(alignment: .leading, spacing: 4) {
                Text(context.date.formatted(.dateTime.weekday(.wide).month(.wide).day()))
                    .font(.system(.title3, design: .rounded, weight: .bold))
                Text(context.date.formatted(date: .omitted, time: .shortened))
                    .font(.system(.largeTitle, design: .rounded, weight: .bold))
                if aiStore.todoOrganizingCount > 0 {
                    Label("Organizing \(aiStore.todoOrganizingCount) more", systemImage: "sparkles")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(18)
            .mailCard()
        }
    }

    @ViewBuilder
    private var aiTodoSection: some View {
        let actionable = aiStore.todoItems.filter { $0.displayKind == .todo }
        let worthKnowing = aiStore.todoItems.filter { $0.displayKind == .worthKnowing }
        if !actionable.isEmpty {
            IOSSectionHeader(title: "AI To-do's", count: actionable.count)
            ForEach(actionable) { item in aiTodoRow(item) }
        }
        if !worthKnowing.isEmpty {
            IOSSectionHeader(title: "Worth Knowing", count: worthKnowing.count)
            ForEach(worthKnowing) { item in aiTodoRow(item) }
        }
    }

    private func aiTodoRow(_ item: AITodoItem) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top) {
                Button {
                    Task {
                        if await aiStore.completeTodo(item) {
                            AppHaptics.success()
                        } else {
                            todoError = aiStore.errorMessage ?? "The AI to-do is still open."
                            AppHaptics.error()
                        }
                    }
                } label: {
                    Image(systemName: "circle")
                        .font(.title3)
                }
                .minimumTouchTarget()
                .accessibilityLabel("Complete \(item.title ?? item.requirement)")

                VStack(alignment: .leading, spacing: 4) {
                    Text(item.title ?? item.requirement)
                        .font(.system(.body, design: .rounded, weight: .semibold))
                    Text(item.detail ?? item.evidenceText ?? item.sourceLabel)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .lineLimit(expandedIDs.contains(item.id) ? nil : 2)
                    HStack {
                        Text(item.sourceLabel)
                        Text(item.urgency.capitalized)
                        if let due = item.dueAt { Text(due) }
                    }
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                }
                Spacer()
                Button {
                    toggle(item.id)
                } label: {
                    Image(systemName: expandedIDs.contains(item.id) ? "chevron.up" : "chevron.down")
                }
                .minimumTouchTarget()
            }
            if expandedIDs.contains(item.id) {
                Button("Open source matter") { router.path.append(.aiMatter(item.matterID)) }
                    .buttonStyle(.bordered)
            }
        }
        .padding(14)
        .mailCard()
    }

    @ViewBuilder
    private func agendaSection(_ snapshot: MobileTodoSnapshot) -> some View {
        if !snapshot.agenda.isEmpty {
            IOSSectionHeader(title: "Agenda", count: snapshot.agenda.count)
            ForEach(snapshot.agenda) { item in
                HStack(spacing: 14) {
                    Text(item.time)
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                        .frame(width: 64, alignment: .leading)
                    Circle().fill(IOSMailDesign.accent).frame(width: 8, height: 8)
                    Text(item.title).font(.subheadline.weight(.medium))
                    Spacer()
                }
                .padding(14)
                .mailCard()
                .accessibilityElement(children: .combine)
            }
        }
    }

    private func todoSection(_ section: MobileTodoSection) -> some View {
        Group {
            if !section.items.isEmpty {
                IOSSectionHeader(title: section.title, count: section.items.count)
                ForEach(section.items) { item in
                    VStack(alignment: .leading, spacing: 8) {
                        HStack(alignment: .top) {
                            Button {
                                Task {
                                    do {
                                        _ = try await inboxStore.completeEntity(entityID: item.entityID)
                                        await inboxStore.refresh()
                                        AppHaptics.success()
                                    } catch {
                                        todoError = error.localizedDescription
                                        AppHaptics.error()
                                    }
                                }
                            } label: { Image(systemName: "circle") }
                            .minimumTouchTarget()
                            .accessibilityLabel("Complete \(item.title)")

                            VStack(alignment: .leading, spacing: 4) {
                                Text(item.title).font(.body.weight(.semibold))
                                Text(item.primaryAction).font(.subheadline).foregroundStyle(.secondary)
                                if expandedIDs.contains(item.id), let detail = item.detail {
                                    ForEach(detail.body, id: \.self) { Text($0).font(.subheadline) }
                                    if let threadID = item.gmailThreadID {
                                        Button(detail.sourceLabel) { router.path.append(.todoSource(threadID)) }
                                            .buttonStyle(.bordered)
                                    }
                                }
                            }
                            Spacer()
                            Button { toggle(item.id) } label: {
                                Image(systemName: expandedIDs.contains(item.id) ? "chevron.up" : "chevron.down")
                            }
                            .minimumTouchTarget()
                        }
                    }
                    .padding(14)
                    .mailCard()
                }
            }
        }
    }

    private func toggle(_ id: String) {
        if expandedIDs.contains(id) { expandedIDs.remove(id) }
        else { expandedIDs.insert(id) }
        AppHaptics.selection()
    }
}

private struct IOSSectionHeader: View {
    let title: String
    let count: Int
    var body: some View {
        HStack {
            Text(title).font(.system(.title3, design: .rounded, weight: .bold))
            Spacer()
            Text("\(count)").font(.caption.monospacedDigit()).foregroundStyle(.secondary)
        }
        .padding(.top, 4)
        .accessibilityAddTraits(.isHeader)
    }
}

struct IOSNewTaskView: View {
    @ObservedObject var store: InboxStore
    @Environment(\.dismiss) private var dismiss
    @State private var title = ""
    @State private var notes = ""
    @State private var section = "today"
    @State private var saving = false
    @State private var error: String?

    var body: some View {
        NavigationStack {
            Form {
                Section("To-do") {
                    TextField("What needs doing?", text: $title, axis: .vertical)
                    TextField("Notes", text: $notes, axis: .vertical)
                }
                Section("When") {
                    Picker("Section", selection: $section) {
                        Text("Now").tag("now")
                        Text("Today").tag("today")
                        Text("Later").tag("later")
                    }
                }
                if let error { Text(error).foregroundStyle(.red) }
            }
            .navigationTitle("New To-do")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Add") { Task { await save() } }
                        .disabled(saving || title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
        }
    }

    @MainActor
    private func save() async {
        saving = true
        do {
            _ = try await store.createManualTask(title: title, notes: notes.isEmpty ? nil : notes, section: section)
            await store.refresh()
            AppHaptics.success()
            dismiss()
        } catch {
            self.error = error.localizedDescription
            saving = false
            AppHaptics.error()
        }
    }
}

struct IOSAIOrganizationView: View {
    @ObservedObject var store: AIInboxStore
    @Environment(\.dismiss) private var dismiss
    @State private var style = AIGroupingStyle.focused
    @State private var rebuildExisting = false

    var body: some View {
        NavigationStack {
            Form {
                Section("Privacy") {
                    Label("AI organization uses mail content only to group your messages and surface actions in this account.", systemImage: "lock.shield")
                    Text("Inbox and every Gmail action continue to work when AI Inbox is disabled.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Section("Grouping") {
                    Picker("Style", selection: $style) {
                        ForEach(AIGroupingStyle.allCases) { option in Text(option.title).tag(option) }
                    }
                    Toggle("Rebuild existing matters", isOn: $rebuildExisting)
                    Button(store.profile?.enabled == true ? "Save changes" : "Enable AI Inbox") {
                        Task {
                            if store.profile?.enabled == true {
                                _ = await store.updateGroupingStyle(style, rebuildExisting: rebuildExisting)
                            } else {
                                await store.enable(style: style)
                            }
                            dismiss()
                        }
                    }
                    .disabled(store.mutationInProgress)
                }
            }
            .navigationTitle("AI Organization")
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Done") { dismiss() } } }
            .onAppear { style = store.profile?.groupingStyle ?? .focused }
        }
    }
}
