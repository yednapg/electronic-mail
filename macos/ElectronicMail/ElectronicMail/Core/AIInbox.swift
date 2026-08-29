import Foundation
import SwiftUI

public enum AIGroupingStyle: String, Codable, CaseIterable, Identifiable {
    case focused
    case broader

    public var id: Self { self }
    var title: String { self == .focused ? "Focused matters" : "Broader projects" }
}

public struct AIOrganizationProfile: Codable, Equatable {
    let consented: Bool
    let enabled: Bool
    let available: Bool
    let groupingStyle: AIGroupingStyle
    let rolloutMode: String
    let activeGenerationID: String?
    let revision: Int

    enum CodingKeys: String, CodingKey {
        case consented, enabled, available, revision
        case groupingStyle = "grouping_style"
        case rolloutMode = "rollout_mode"
        case activeGenerationID = "active_generation_id"
    }
}

public struct AIOrganizationProfilePatch: Codable, Equatable {
    let consent: Bool?
    let enabled: Bool?
    let groupingStyle: AIGroupingStyle?
    let rebuildExisting: Bool

    public init(
        consent: Bool? = nil,
        enabled: Bool? = nil,
        groupingStyle: AIGroupingStyle? = nil,
        rebuildExisting: Bool = false
    ) {
        self.consent = consent
        self.enabled = enabled
        self.groupingStyle = groupingStyle
        self.rebuildExisting = rebuildExisting
    }

    enum CodingKeys: String, CodingKey {
        case consent, enabled
        case groupingStyle = "grouping_style"
        case rebuildExisting = "rebuild_existing"
    }
}

public enum AIMatterStatus: String, Codable {
    case needsYou = "needs_you"
    case waitingOnOthers = "waiting_on_others"
    case inProgress = "in_progress"
    case completed
    case partiallyCompleted = "partially_completed"
    case failed
    case cancelled
    case informational

    var label: String {
        switch self {
        case .needsYou: "Needs you"
        case .waitingOnOthers: "Waiting"
        case .inProgress: "In progress"
        case .completed: "Completed"
        case .partiallyCompleted: "Partially completed"
        case .failed: "Failed"
        case .cancelled: "Cancelled"
        case .informational: "Information"
        }
    }
}

public enum AIMatterConfidenceState: String, Codable {
    case automatic
    case provisional
    case confirmed
}

public struct AIMatterRow: Codable, Equatable, Identifiable {
    public let id: String
    let title: String
    let summary: String
    let status: AIMatterStatus
    let confidenceState: AIMatterConfidenceState
    let confidence: Double
    let latestMessageAt: String
    let messageCount: Int
    let unread: Bool
    let starred: Bool
    let participants: [String]
    var counterpartEntities: [String]? = nil
    let evidenceMessageIDs: [String]
    let revision: Int
    let openSubgoalCount: Int?
    let reviewCount: Int?
    let matchingMessageIDs: [String]

    enum CodingKeys: String, CodingKey {
        case id, title, summary, status, confidence, unread, starred, participants, revision
        case counterpartEntities = "counterpart_entities"
        case confidenceState = "confidence_state"
        case latestMessageAt = "latest_message_at"
        case messageCount = "message_count"
        case openSubgoalCount = "open_subgoal_count"
        case reviewCount = "review_count"
        case evidenceMessageIDs = "evidence_message_ids"
        case matchingMessageIDs = "matching_message_ids"
    }
}

public struct AIGroupingExplanation: Codable, Equatable {
    let messageID: String
    let eventRole: String
    let verdict: String
    let explanation: String
    let supportingFactors: [String]
    let conflictingFactors: [String]
    let evidenceMessageIDs: [String]

    enum CodingKeys: String, CodingKey {
        case verdict, explanation
        case messageID = "message_id"
        case eventRole = "event_role"
        case supportingFactors = "supporting_factors"
        case conflictingFactors = "conflicting_factors"
        case evidenceMessageIDs = "evidence_message_ids"
    }
}

public struct AIReviewProposal: Codable, Equatable, Identifiable {
    public let id: String
    let messageID: String
    let subject: String
    let sender: String?
    let occurredAt: String
    let recommendedAction: String
    let eventRole: String
    let verdict: String
    let explanation: String
    let supportingFactors: [String]
    let conflictingFactors: [String]
    let evidenceMessageIDs: [String]

    enum CodingKeys: String, CodingKey {
        case id, subject, sender, verdict, explanation
        case messageID = "message_id"
        case occurredAt = "occurred_at"
        case recommendedAction = "recommended_action"
        case eventRole = "event_role"
        case supportingFactors = "supporting_factors"
        case conflictingFactors = "conflicting_factors"
        case evidenceMessageIDs = "evidence_message_ids"
    }
}

public struct AIOrganizingRow: Codable, Equatable, Identifiable {
    public let id: String
    let gmailThreadID: String
    let title: String
    let sender: String?
    var counterpartEntities: [String]? = nil
    let snippet: String?
    let latestMessageAt: String
    let messageCount: Int
    let state: String
    let error: String?
    let matchingMessageIDs: [String]

    enum CodingKeys: String, CodingKey {
        case id, title, sender, snippet, state, error
        case counterpartEntities = "counterpart_entities"
        case gmailThreadID = "gmail_thread_id"
        case latestMessageAt = "latest_message_at"
        case messageCount = "message_count"
        case matchingMessageIDs = "matching_message_ids"
    }
}

public struct AIInboxResponse: Codable, Equatable {
    let profile: AIOrganizationProfile
    let generationID: String?
    let revision: String
    let stale: Bool
    let staleReason: String?
    let matters: [AIMatterRow]
    let organizing: [AIOrganizingRow]
    let generatedAt: String

    enum CodingKeys: String, CodingKey {
        case profile, revision, stale, matters, organizing
        case generationID = "generation_id"
        case staleReason = "stale_reason"
        case generatedAt = "generated_at"
    }
}

public struct AIMatterDetail: Codable, Equatable, Identifiable {
    public let id: String
    let title: String
    let stableGoal: String
    let summary: String
    let status: AIMatterStatus
    let confidenceState: AIMatterConfidenceState
    let confidence: Double
    let evidenceMessageIDs: [String]
    let revision: Int
    let latestReplyableMessageID: String?
    let totalMessages: Int
    let matterMessageIDs: [String]
    let reviewProposals: [AIReviewProposal]?
    let messages: [ThreadMessage]

    enum CodingKeys: String, CodingKey {
        case id, title, summary, status, confidence, revision, messages
        case stableGoal = "stable_goal"
        case confidenceState = "confidence_state"
        case evidenceMessageIDs = "evidence_message_ids"
        case latestReplyableMessageID = "latest_replyable_message_id"
        case totalMessages = "total_messages"
        case matterMessageIDs = "matter_message_ids"
        case reviewProposals = "review_proposals"
    }
}

public struct MatterDecisionRequest: Codable, Equatable {
    let clientDecisionID: String
    let decision: String
    let matterID: String
    let targetMatterID: String?
    let messageIDs: [String]
    let expectedRevision: Int

    enum CodingKeys: String, CodingKey {
        case decision
        case clientDecisionID = "client_decision_id"
        case matterID = "matter_id"
        case targetMatterID = "target_matter_id"
        case messageIDs = "message_ids"
        case expectedRevision = "expected_revision"
    }
}

public struct MatterDecisionResponse: Codable, Equatable {
    let clientDecisionID: String
    let decisionID: String
    let matterIDs: [String]
    let revision: String
    let state: String

    enum CodingKeys: String, CodingKey {
        case revision, state
        case clientDecisionID = "client_decision_id"
        case decisionID = "decision_id"
        case matterIDs = "matter_ids"
    }
}

public struct MatterEntityActionRequest: Codable, Equatable {
    let clientActionID: String
    let matterID: String
    let action: GmailThreadAction
    let expectedRevision: Int
    let confirmMultiThreadTrash: Bool

    enum CodingKeys: String, CodingKey {
        case action
        case clientActionID = "client_action_id"
        case matterID = "matter_id"
        case expectedRevision = "expected_revision"
        case confirmMultiThreadTrash = "confirm_multi_thread_trash"
    }
}

public struct MatterEntityActionResponse: Codable, Equatable {
    let clientActionID: String
    let matterID: String
    let action: GmailThreadAction
    let targetMessageIDs: [String]
    let affectedThreadCount: Int
    let state: String

    enum CodingKeys: String, CodingKey {
        case action, state
        case clientActionID = "client_action_id"
        case matterID = "matter_id"
        case targetMessageIDs = "target_message_ids"
        case affectedThreadCount = "affected_thread_count"
    }
}

private struct PendingMatterDecision: Codable, Identifiable {
    let id: String
    let request: MatterDecisionRequest
}

private final class MatterDecisionQueue {
    private let defaults: UserDefaults
    private let key = "ElectronicMail.PendingMatterDecisions.v1"

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    func read() -> [PendingMatterDecision] {
        guard let data = defaults.data(forKey: key) else { return [] }
        return (try? JSONDecoder.backend.decode([PendingMatterDecision].self, from: data)) ?? []
    }

    func append(_ request: MatterDecisionRequest) {
        var pending = read()
        guard !pending.contains(where: { $0.id == request.clientDecisionID }) else { return }
        pending.append(PendingMatterDecision(id: request.clientDecisionID, request: request))
        write(pending)
    }

    func remove(_ id: String) {
        write(read().filter { $0.id != id })
    }

    private func write(_ pending: [PendingMatterDecision]) {
        if pending.isEmpty {
            defaults.removeObject(forKey: key)
        } else if let data = try? JSONEncoder.backend.encode(pending) {
            defaults.set(data, forKey: key)
        }
    }
}

@MainActor
public final class AIInboxStore: ObservableObject {
    @Published private(set) var response: AIInboxResponse?
    @Published private(set) var detail: AIMatterDetail?
    @Published private(set) var loading = false
    @Published private(set) var detailLoading = false
    @Published private(set) var stale = false
    @Published private(set) var errorMessage: String?
    @Published private(set) var mutationInProgress = false
    @Published private(set) var pendingDecisionCount = 0
    @Published private(set) var groupingExplanations: [String: AIGroupingExplanation] = [:]
    @Published private(set) var explanationMessageIDsLoading: Set<String> = []

    private let client: AppClient
    private let decisionQueue: MatterDecisionQueue
    private var query: String?
    private var requestID = UUID()

    init(client: AppClient) {
        self.client = client
        self.decisionQueue = MatterDecisionQueue()
        pendingDecisionCount = self.decisionQueue.read().count
    }

    var selectedMatterID: String? { detail?.id }
    var profile: AIOrganizationProfile? { response?.profile }

    func refresh(query: String? = nil) async {
        self.query = query?.trimmingCharacters(in: .whitespacesAndNewlines)
        let id = UUID()
        requestID = id
        loading = response == nil
        do {
            let loaded = try await client.aiInbox(query: self.query)
            guard requestID == id else { return }
            response = loaded
            stale = loaded.stale
            errorMessage = loaded.staleReason
            loading = false
            await replayPendingDecisions()
        } catch is CancellationError {
            return
        } catch {
            guard requestID == id else { return }
            loading = false
            stale = response != nil
            errorMessage = response == nil
                ? error.localizedDescription
                : "AI Inbox could not refresh. Showing its last stable state; Inbox remains fully available."
        }
    }

    func enable(style: AIGroupingStyle) async {
        mutationInProgress = true
        defer { mutationInProgress = false }
        do {
            _ = try await client.updateAIOrganizationProfile(
                AIOrganizationProfilePatch(consent: true, enabled: true, groupingStyle: style)
            )
            await refresh(query: query)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func select(_ matterID: String) async {
        detailLoading = true
        groupingExplanations = [:]
        explanationMessageIDsLoading = []
        do {
            detail = try await client.aiMatter(matterID)
            detailLoading = false
        } catch {
            detailLoading = false
            errorMessage = error.localizedDescription
        }
    }

    func closeDetail() {
        detail = nil
        groupingExplanations = [:]
        explanationMessageIDsLoading = []
    }

    func decide(_ decision: String, matter: AIMatterRow) async {
        _ = await submitDecision(
            decision,
            matterID: matter.id,
            targetMatterID: nil,
            messageIDs: [],
            expectedRevision: matter.revision
        )
    }

    func decideProposal(
        _ decision: String,
        matter: AIMatterDetail,
        proposal: AIReviewProposal
    ) async {
        let accepted = await submitDecision(
            decision,
            matterID: matter.id,
            targetMatterID: nil,
            messageIDs: [proposal.messageID],
            expectedRevision: matter.revision
        )
        if accepted {
            await select(matter.id)
        }
    }

    func loadGroupingExplanation(matterID: String, messageID: String) async {
        guard groupingExplanations[messageID] == nil,
              !explanationMessageIDsLoading.contains(messageID) else { return }
        explanationMessageIDsLoading.insert(messageID)
        defer { explanationMessageIDsLoading.remove(messageID) }
        do {
            groupingExplanations[messageID] = try await client.aiGroupingExplanation(
                matterID: matterID,
                messageID: messageID
            )
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func organize(
        _ decision: String,
        matter: AIMatterDetail,
        targetMatterID: String? = nil,
        messageIDs: [String] = []
    ) async -> Bool {
        let accepted = await submitDecision(
            decision,
            matterID: matter.id,
            targetMatterID: targetMatterID,
            messageIDs: messageIDs,
            expectedRevision: matter.revision
        )
        if accepted { detail = nil }
        return accepted
    }

    func updateGroupingStyle(_ style: AIGroupingStyle, rebuildExisting: Bool) async -> Bool {
        mutationInProgress = true
        defer { mutationInProgress = false }
        do {
            _ = try await client.updateAIOrganizationProfile(
                AIOrganizationProfilePatch(
                    groupingStyle: style,
                    rebuildExisting: rebuildExisting
                )
            )
            await refresh(query: query)
            return true
        } catch {
            errorMessage = error.localizedDescription
            return false
        }
    }

    func disableAIInbox() async -> Bool {
        mutationInProgress = true
        defer { mutationInProgress = false }
        do {
            _ = try await client.updateAIOrganizationProfile(
                AIOrganizationProfilePatch(enabled: false)
            )
            detail = nil
            await refresh()
            return true
        } catch {
            errorMessage = error.localizedDescription
            return false
        }
    }

    func deleteAIData() async -> Bool {
        mutationInProgress = true
        defer { mutationInProgress = false }
        do {
            try await client.deleteAIOrganizationData()
            detail = nil
            response = nil
            await refresh()
            return true
        } catch {
            errorMessage = error.localizedDescription
            return false
        }
    }

    private func submitDecision(
        _ decision: String,
        matterID: String,
        targetMatterID: String?,
        messageIDs: [String],
        expectedRevision: Int
    ) async -> Bool {
        let request = MatterDecisionRequest(
            clientDecisionID: UUID().uuidString,
            decision: decision,
            matterID: matterID,
            targetMatterID: targetMatterID,
            messageIDs: messageIDs,
            expectedRevision: expectedRevision
        )
        mutationInProgress = true
        defer { mutationInProgress = false }
        do {
            _ = try await client.applyMatterDecision(request)
            await refresh(query: query)
            return true
        } catch APIError.httpStatus(let status) where status == 409 {
            errorMessage = "This matter changed on another device. It has been refreshed instead."
            await refresh(query: query)
            return false
        } catch {
            decisionQueue.append(request)
            pendingDecisionCount = decisionQueue.read().count
            errorMessage = "Your correction is saved on this Mac and will sync when the connection returns."
            return true
        }
    }

    func perform(
        _ action: GmailThreadAction,
        matter: AIMatterDetail,
        confirmMultiThreadTrash: Bool = false
    ) async -> Bool {
        mutationInProgress = true
        defer { mutationInProgress = false }
        do {
            _ = try await client.applyMatterAction(
                MatterEntityActionRequest(
                    clientActionID: UUID().uuidString,
                    matterID: matter.id,
                    action: action,
                    expectedRevision: matter.revision,
                    confirmMultiThreadTrash: confirmMultiThreadTrash
                )
            )
            detail = nil
            await refresh(query: query)
            return true
        } catch APIError.httpStatus(let status) where status == 409 {
            errorMessage = "This matter changed on another device. Refresh it before applying the action."
            await refresh(query: query)
        } catch {
            errorMessage = error.localizedDescription
        }
        return false
    }

    private func replayPendingDecisions() async {
        for pending in decisionQueue.read() {
            do {
                _ = try await client.applyMatterDecision(pending.request)
                decisionQueue.remove(pending.id)
            } catch APIError.httpStatus(let status) where status == 409 {
                decisionQueue.remove(pending.id)
            } catch {
                break
            }
        }
        pendingDecisionCount = decisionQueue.read().count
    }
}

@MainActor
public final class AIReaderChromeState: ObservableObject {
    @Published public private(set) var isSummaryCollapsed = false

    public init() {}

    func updateScrollDistance(_ scrollDistance: CGFloat) {
        let nextValue = AIReaderChromeScrollPolicy.isSummaryCollapsed(
            currentlyCollapsed: isSummaryCollapsed,
            scrollDistance: scrollDistance
        )
        guard nextValue != isSummaryCollapsed else { return }
        isSummaryCollapsed = nextValue
    }

    func reset() {
        guard isSummaryCollapsed else { return }
        isSummaryCollapsed = false
    }
}

public struct AIInboxView: View {
    @Environment(\.colorScheme) private var colorScheme
    @ObservedObject var store: AIInboxStore
    @Binding private var searchText: String
    @Binding private var showSettings: Bool
    @Binding private var showOrganize: Bool
    let currentUserDisplayName: String?
    let currentUserEmail: String?
    let onRespond: (String, MailComposerMode, String) -> Void
    let onGmailMutation: () async -> Void
    let onOpenAttachment: (ThreadAttachment, String) -> Void
    let isAttachmentDownloading: (ThreadAttachment, String) -> Bool
    let readerChromeState: AIReaderChromeState

    @State private var setupStyle: AIGroupingStyle = .focused
    @State private var selectedMatterID: String?
    @FocusState private var isListFocused: Bool

    public init(
        store: AIInboxStore,
        searchText: Binding<String>,
        showSettings: Binding<Bool>,
        showOrganize: Binding<Bool>,
        currentUserDisplayName: String?,
        currentUserEmail: String?,
        onRespond: @escaping (String, MailComposerMode, String) -> Void,
        onGmailMutation: @escaping () async -> Void,
        onOpenAttachment: @escaping (ThreadAttachment, String) -> Void,
        isAttachmentDownloading: @escaping (ThreadAttachment, String) -> Bool,
        readerChromeState: AIReaderChromeState
    ) {
        self.store = store
        self._searchText = searchText
        self._showSettings = showSettings
        self._showOrganize = showOrganize
        self.currentUserDisplayName = currentUserDisplayName
        self.currentUserEmail = currentUserEmail
        self.onRespond = onRespond
        self.onGmailMutation = onGmailMutation
        self.onOpenAttachment = onOpenAttachment
        self.isAttachmentDownloading = isAttachmentDownloading
        self.readerChromeState = readerChromeState
    }

    public var body: some View {
        Group {
            if let detail = store.detail {
                matterDetail(detail)
            } else if let profile = store.profile, !profile.consented || !profile.enabled {
                consentView(profile)
            } else if store.loading && store.response == nil {
                ProgressView("Loading AI Inbox…")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let response = store.response {
                inbox(response)
            } else {
                ContentUnavailableView(
                    "AI Inbox is unavailable",
                    systemImage: "sparkles.rectangle.stack",
                    description: Text(store.errorMessage ?? "Use Inbox while AI Inbox is unavailable.")
                )
            }
        }
        .task(id: searchText.trimmingCharacters(in: .whitespacesAndNewlines)) {
            await store.refresh(query: searchText)
        }
        .onReceive(NotificationCenter.default.publisher(for: .electronicMailAIInboxChanged)) { _ in
            Task { await store.refresh(query: searchText) }
        }
        .onChange(of: store.detail?.id) { _, detailID in
            if detailID == nil {
                readerChromeState.reset()
            }
        }
        .sheet(isPresented: $showSettings) {
            if let profile = store.profile {
                AIInboxSettingsSheet(store: store, profile: profile)
            }
        }
        .sheet(isPresented: $showOrganize) {
            if let matter = store.detail {
                OrganizeMatterSheet(
                    store: store,
                    matter: matter,
                    candidates: store.response?.matters.filter { $0.id != matter.id } ?? []
                )
            }
        }
    }

    private func consentView(_ profile: AIOrganizationProfile) -> some View {
        VStack(alignment: .leading, spacing: 20) {
            Image(systemName: "sparkles.rectangle.stack.fill")
                .font(.system(size: 38))
                .foregroundStyle(ElectronicMailDesign.appleBlue)
            Text("Organize mail by what it is actually about")
                .font(.title2.weight(.semibold))
            Text("AI Inbox sends cleaned email text and bounded text from supported documents to OpenAI. Sensitive authentication codes, links, payment numbers, and account numbers are removed or tokenized first. OpenAI's standard API retention applies, and API data is not used to train models by default. Your normal Inbox is unchanged and always remains available.")
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Picker("Grouping style", selection: $setupStyle) {
                ForEach(AIGroupingStyle.allCases) { style in
                    Text(style.title).tag(style)
                }
            }
            .pickerStyle(.segmented)
            Text(setupStyle == .focused
                 ? "Recommended. Keeps each concrete request, case, purchase, or task separate."
                 : "Combines related work into wider projects when the evidence supports it.")
                .font(.callout)
                .foregroundStyle(.secondary)
            Button("Enable AI Inbox") {
                Task { await store.enable(style: setupStyle) }
            }
            .buttonStyle(.borderedProminent)
            .disabled(store.mutationInProgress || !profile.available)
            if !profile.available {
                Text("AI Inbox is currently disabled on this server. Inbox continues to work normally.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: 620, alignment: .leading)
        .padding(44)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }

    private func inbox(_ response: AIInboxResponse) -> some View {
        GeometryReader { proxy in
            let metrics = AIInboxListMetrics(windowSize: proxy.size)
            let sections = AIInboxChronologyPresenter.sections(
                response: response,
                now: Date(),
                calendar: .autoupdatingCurrent,
                locale: .autoupdatingCurrent
            )
            let matters = sections.flatMap(\.rows).compactMap { item -> AIMatterRow? in
                guard case .matter(let matter) = item else { return nil }
                return matter
            }

            VStack(alignment: .leading, spacing: 0) {
                if store.stale || store.errorMessage != nil {
                    HStack(spacing: 8) {
                        Image(systemName: "exclamationmark.triangle.fill")
                        Text(store.errorMessage ?? "Showing the last stable AI Inbox. Use Inbox if needed.")
                            .lineLimit(1)
                        Spacer(minLength: 12)
                    }
                    .font(ElectronicMailType.small())
                    .padding(.horizontal, metrics.senderLeading)
                    .frame(width: metrics.windowWidth, height: 34)
                    .foregroundStyle(Color.orange)
                    .background(Color.orange.opacity(0.10))
                }

                Color.clear
                    .frame(height: ElectronicMailShellMetrics.contentTop)
                    .accessibilityHidden(true)

                ScrollViewReader { scrollProxy in
                    ScrollView(.vertical) {
                        LazyVStack(alignment: .leading, spacing: 0) {
                            if sections.isEmpty {
                                VStack(spacing: 12) {
                                    Text(searchText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                                         ? "Nothing to organize"
                                         : "No matching matters")
                                        .font(ElectronicMailType.sectionTitle())
                                        .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                                    Text(searchText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                                         ? "New inbox mail will appear here while it is organized."
                                         : "Try a different sender, headline, or phrase.")
                                        .font(ElectronicMailType.body())
                                        .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                                }
                                .frame(width: metrics.windowWidth)
                                .padding(.top, 48)
                            }

                            ForEach(sections) { section in
                                AIInboxSectionHeader(
                                    title: section.title,
                                    isFirstSection: section.isFirstSection,
                                    metrics: metrics,
                                    colorScheme: colorScheme
                                )

                                ForEach(section.rows) { item in
                                    switch item {
                                    case .matter(let matter):
                                        AIInboxMatterListRow(
                                            matter: matter,
                                            isSelected: selectedMatterID == matter.id,
                                            metrics: metrics,
                                            colorScheme: colorScheme,
                                            disabled: store.mutationInProgress,
                                            onOpen: { open(matter) }
                                        )
                                        .id(matter.id)
                                    case .organizing(let row):
                                        AIInboxOrganizingListRow(
                                            row: row,
                                            metrics: metrics,
                                            colorScheme: colorScheme
                                        )
                                        .id(item.id)
                                    }
                                }
                            }
                        }
                        .frame(width: metrics.windowWidth, alignment: .topLeading)
                    }
                    .scrollIndicators(.automatic)
                    .refreshable { await store.refresh(query: searchText) }
                    .focusable()
                    .focusEffectDisabled()
                    .focused($isListFocused)
                    .defaultFocus($isListFocused, true)
                    .onKeyPress(.upArrow) {
                        moveSelection(in: matters, by: -1, scrollProxy: scrollProxy)
                        return .handled
                    }
                    .onKeyPress(.downArrow) {
                        moveSelection(in: matters, by: 1, scrollProxy: scrollProxy)
                        return .handled
                    }
                    .onKeyPress(.return) {
                        openSelectedMatter(in: matters)
                        return .handled
                    }
                    .background {
                        InboxKeyboardNavigationCapture(
                            onMove: { delta in
                                moveSelection(in: matters, by: delta, scrollProxy: scrollProxy)
                            },
                            onOpenSelection: {
                                openSelectedMatter(in: matters)
                            }
                        )
                        .frame(width: 0, height: 0)
                    }
                    .onAppear {
                        restoreSelectedMatterPosition(in: matters, scrollProxy: scrollProxy)
                    }
                    .transaction { transaction in
                        transaction.disablesAnimations = true
                    }
                }
            }
        }
    }

    private func open(_ matter: AIMatterRow) {
        guard !store.mutationInProgress else { return }
        selectedMatterID = matter.id
        Task { await store.select(matter.id) }
    }

    private func moveSelection(
        in matters: [AIMatterRow],
        by delta: Int,
        scrollProxy: ScrollViewProxy
    ) {
        guard !matters.isEmpty else { return }
        let currentIndex = selectedMatterID.flatMap { selectedID in
            matters.firstIndex(where: { $0.id == selectedID })
        }
        let fallbackIndex = delta > 0 ? -1 : matters.count
        let nextIndex = min(max((currentIndex ?? fallbackIndex) + delta, 0), matters.count - 1)
        let nextMatter = matters[nextIndex]
        selectedMatterID = nextMatter.id
        scrollProxy.scrollTo(nextMatter.id, anchor: .center)
    }

    private func openSelectedMatter(in matters: [AIMatterRow]) {
        guard let selectedMatterID,
              let matter = matters.first(where: { $0.id == selectedMatterID }) else {
            return
        }
        open(matter)
    }

    private func restoreSelectedMatterPosition(
        in matters: [AIMatterRow],
        scrollProxy: ScrollViewProxy
    ) {
        guard let selectedMatterID,
              matters.contains(where: { $0.id == selectedMatterID }) else {
            return
        }
        DispatchQueue.main.async {
            scrollProxy.scrollTo(selectedMatterID, anchor: .center)
        }
    }

    private func matterDetail(_ matter: AIMatterDetail) -> some View {
        AIMatterConversationView(
            matter: matter,
            colorScheme: colorScheme,
            currentUserDisplayName: currentUserDisplayName,
            currentUserEmail: currentUserEmail,
            onRespond: onRespond,
            onThreadAction: { action in
                Task { _ = await perform(action, matter: matter) }
            },
            decisionsDisabled: store.mutationInProgress,
            groupingExplanations: store.groupingExplanations,
            explanationMessageIDsLoading: store.explanationMessageIDsLoading,
            onLoadGroupingExplanation: { messageID in
                Task {
                    await store.loadGroupingExplanation(
                        matterID: matter.id,
                        messageID: messageID
                    )
                }
            },
            onReviewDecision: { decision, proposal in
                Task { await store.decideProposal(decision, matter: matter, proposal: proposal) }
            },
            onOpenAttachment: onOpenAttachment,
            isAttachmentDownloading: isAttachmentDownloading,
            readerChromeState: readerChromeState
        )
        .onExitCommand {
            store.closeDetail()
        }
    }

    private func perform(
        _ action: GmailThreadAction,
        matter: AIMatterDetail,
        confirmTrash: Bool = false
    ) async -> Bool {
        let applied = await store.perform(action, matter: matter, confirmMultiThreadTrash: confirmTrash)
        if applied { await onGmailMutation() }
        return applied
    }

}

private struct AIMatterConversationView: View {
    let matter: AIMatterDetail
    let colorScheme: ColorScheme
    let currentUserDisplayName: String?
    let currentUserEmail: String?
    let onRespond: (String, MailComposerMode, String) -> Void
    let onThreadAction: (GmailThreadAction) -> Void
    let decisionsDisabled: Bool
    let groupingExplanations: [String: AIGroupingExplanation]
    let explanationMessageIDsLoading: Set<String>
    let onLoadGroupingExplanation: (String) -> Void
    let onReviewDecision: (String, AIReviewProposal) -> Void
    let onOpenAttachment: (ThreadAttachment, String) -> Void
    let isAttachmentDownloading: (ThreadAttachment, String) -> Bool
    let readerChromeState: AIReaderChromeState

    @State private var expandedMessageKeys: Set<EmailThreadPresentationItem.ID> = []
    @State private var activeMessageKey: EmailThreadPresentationItem.ID?
    @State private var initializedMatterID: String?
    @State private var pinnedSummaryCardHeight: CGFloat = 0
    @State private var summaryScrollOrigin: CGFloat?
    @State private var establishingInitialScrollPosition = false
    @State private var initialScrollOriginCaptureReady = false
    @State private var initialScrollMeasurementRequest = 0
    @State private var initialTopPeekRequest: Int?

    var body: some View {
        GeometryReader { proxy in
            let contentWidth = min(
                ElectronicMailControlMetrics.readerMaxWidth,
                max(1, proxy.size.width - 72)
            )
            let presentation = EmailThreadPresentation.snapshot(from: matter.messages)
            let activeMessage = resolvedActiveMessage(in: presentation)
            let renderIdentities = presentation.items.map {
                AIMatterMessageRenderIdentity(id: $0.id, renderRevision: $0.message.renderRevision)
            }
            let pinnedSummaryHeight = pinnedSummaryCardHeight

            ZStack(alignment: .bottom) {
                VStack(alignment: .leading, spacing: 0) {
                    AIMatterPinnedSummary(
                        summary: matter.summary,
                        colorScheme: colorScheme,
                        gradientSeed: matter.id,
                        contentWidth: contentWidth,
                        chromeState: readerChromeState
                    )
                    .zIndex(1)

                ScrollViewReader { scrollProxy in
                    ElectronicMailReaderFadingScrollView(
                        colorScheme: colorScheme,
                        showsIndicators: true,
                        scrollIdentity: matter.id,
                        offsetReadRequest: initialScrollMeasurementRequest,
                        topPeekRequest: initialTopPeekRequest,
                        fadeTopInset: ElectronicMailControlMetrics.readerScrollFadeTopInset,
                        onScrollOffsetChange: handleScrollOffset
                    ) {
                        VStack(alignment: .leading, spacing: 0) {

                            if !(matter.reviewProposals ?? []).isEmpty {
                                AIMatterReviewSection(
                                    proposals: matter.reviewProposals ?? [],
                                    colorScheme: colorScheme,
                                    disabled: decisionsDisabled,
                                    onKeepSeparate: { onReviewDecision("separate", $0) },
                                    onAdd: { onReviewDecision("confirm", $0) },
                                    onOpenEmail: { proposal in
                                        openMessage(
                                            proposal.messageID,
                                            in: presentation,
                                            scrollProxy: scrollProxy
                                        )
                                    }
                                )
                                .padding(.bottom, 22)
                            }

                            ForEach(Array(presentation.items.enumerated()), id: \.element.id) { index, item in
                                if index > 0 {
                                    Rectangle()
                                        .fill(ElectronicMailDesign.readerHairline(for: colorScheme))
                                        .frame(height: 1)
                                }

                                EmailMessageCard(
                                    threadID: item.message.threadID ?? matter.id,
                                    message: item.message,
                                    expanded: presentation.items.count == 1 || expandedMessageKeys.contains(item.id),
                                    allowsCollapse: presentation.items.count > 1,
                                    currentUserDisplayName: currentUserDisplayName,
                                    currentUserEmail: currentUserEmail,
                                    colorScheme: colorScheme,
                                    mailboxLabel: .inbox,
                                    onRespond: { mode, messageID in
                                        respond(to: item.message, mode: mode, messageID: messageID)
                                    },
                                    onThreadAction: { action, _ in onThreadAction(action) },
                                    onOpenAttachment: onOpenAttachment,
                                    isAttachmentDownloading: isAttachmentDownloading,
                                    groupingDetails: groupingDetails(for: item.message.id),
                                    onOpenDetails: groupingEventMessageIDs.contains(item.message.id)
                                        ? { loadGroupingExplanation(for: item.message.id) }
                                        : nil,
                                    onToggle: { toggle(item.id, in: presentation) }
                                )
                                .id(item.id)
                            }

                            if activeMessage.flatMap(replyThreadID(for:)) != nil {
                                Color.clear
                                    .frame(
                                        height: ElectronicMailControlMetrics.readerFixedActionsReservedHeight
                                    )
                                    .accessibilityHidden(true)
                            }

                            if presentation.items.count > 1 {
                                Color.clear
                                    .frame(
                                        height: max(
                                            0,
                                            proxy.size.height - pinnedSummaryHeight - 96
                                        )
                                    )
                                    .accessibilityHidden(true)
                            }
                        }
                        .frame(width: contentWidth, alignment: .leading)
                        .frame(maxWidth: .infinity)
                    }
                    .onAppear {
                        synchronizeExpansion(in: presentation, scrollProxy: scrollProxy)
                    }
                    .onChange(of: renderIdentities) { _, _ in
                        synchronizeExpansion(in: presentation, scrollProxy: scrollProxy)
                    }
                    .onChange(of: matter.id) { _, _ in
                        resetConversationPosition()
                        synchronizeExpansion(in: presentation, scrollProxy: scrollProxy)
                    }
                    }
                }

                if let activeMessage, let threadID = replyThreadID(for: activeMessage) {
                    ReaderActionBubbles(
                        onReply: { onRespond(threadID, .reply, activeMessage.id) },
                        onReplyAll: { onRespond(threadID, .replyAll, activeMessage.id) },
                        onForward: { onRespond(threadID, .forward, activeMessage.id) },
                        onCompletion: matter.status == .completed
                            ? { onThreadAction(.archive) }
                            : nil,
                        completionHelp: matter.status == .completed
                            ? "This matter appears complete"
                            : nil
                    )
                    .frame(width: contentWidth)
                    .padding(.bottom, ElectronicMailControlMetrics.readerFloatingActionsBottomInset)
                    .zIndex(2)
                }
            }
            .onPreferenceChange(AIMatterPinnedSummaryHeightPreferenceKey.self) { nextHeight in
                guard abs(nextHeight - pinnedSummaryCardHeight) > 0.25 else { return }
                pinnedSummaryCardHeight = nextHeight
            }
            .onDisappear {
                readerChromeState.reset()
            }
        }
    }

    private func openMessage(
        _ messageID: String,
        in presentation: EmailThreadPresentationSnapshot,
        scrollProxy: ScrollViewProxy
    ) {
        guard let item = presentation.items.first(where: { $0.message.id == messageID }) else { return }
        expandedMessageKeys.insert(item.id)
        activeMessageKey = item.id
        withAnimation(.easeOut(duration: 0.2)) {
            scrollProxy.scrollTo(item.id, anchor: .center)
        }
    }

    private func resolvedActiveMessage(
        in presentation: EmailThreadPresentationSnapshot
    ) -> ThreadMessage? {
        if let activeMessageKey,
           let active = presentation.items.first(where: { $0.id == activeMessageKey })?.message,
           replyThreadID(for: active) != nil {
            return active
        }
        if let latestReplyableMessageID = matter.latestReplyableMessageID,
           let latestReplyable = presentation.items.first(where: {
               $0.message.id == latestReplyableMessageID && replyThreadID(for: $0.message) != nil
           })?.message {
            return latestReplyable
        }
        return presentation.items.reversed().first(where: {
            replyThreadID(for: $0.message) != nil
        })?.message
    }

    private func respond(to message: ThreadMessage, mode: MailComposerMode, messageID: String) {
        guard let threadID = replyThreadID(for: message) else { return }
        onRespond(threadID, mode, messageID)
    }

    private func replyThreadID(for message: ThreadMessage) -> String? {
        let threadID = message.threadID?.trimmingCharacters(in: .whitespacesAndNewlines)
        return threadID?.isEmpty == false ? threadID : nil
    }

    private func toggle(
        _ messageKey: EmailThreadPresentationItem.ID,
        in presentation: EmailThreadPresentationSnapshot
    ) {
        if expandedMessageKeys.contains(messageKey) {
            expandedMessageKeys.remove(messageKey)
            if activeMessageKey == messageKey {
                activeMessageKey = presentation.items.reversed().first(where: {
                    expandedMessageKeys.contains($0.id)
                })?.id
            }
        } else {
            expandedMessageKeys.insert(messageKey)
            activeMessageKey = messageKey
        }
    }

    private func synchronizeExpansion(
        in presentation: EmailThreadPresentationSnapshot,
        scrollProxy: ScrollViewProxy
    ) {
        let validKeys = Set(presentation.items.map(\.id))
        expandedMessageKeys.formIntersection(validKeys)

        if initializedMatterID != matter.id, let latestMessageKey = presentation.latestMessageKey {
            expandedMessageKeys = [latestMessageKey]
            activeMessageKey = latestMessageKey
            initializedMatterID = matter.id
            summaryScrollOrigin = nil
            readerChromeState.reset()
            establishingInitialScrollPosition = true
            initialScrollOriginCaptureReady = false
            DispatchQueue.main.async {
                scrollProxy.scrollTo(latestMessageKey, anchor: .top)
                DispatchQueue.main.async {
                    if presentation.items.count > 1 {
                        initialTopPeekRequest = (initialTopPeekRequest ?? 0) + 1
                    }
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.20) {
                        guard establishingInitialScrollPosition else { return }
                        initialScrollOriginCaptureReady = true
                        initialScrollMeasurementRequest &+= 1
                    }
                }
            }
        } else if let activeMessageKey, !validKeys.contains(activeMessageKey) {
            self.activeMessageKey = presentation.items.reversed().first(where: {
                expandedMessageKeys.contains($0.id)
            })?.id ?? presentation.latestMessageKey
        }
    }

    private func handleScrollOffset(_ offset: CGFloat) {
        if establishingInitialScrollPosition {
            guard initialScrollOriginCaptureReady else { return }
            summaryScrollOrigin = offset
            establishingInitialScrollPosition = false
            initialScrollOriginCaptureReady = false
            return
        }

        guard let summaryScrollOrigin else {
            self.summaryScrollOrigin = offset
            return
        }
        readerChromeState.updateScrollDistance(abs(offset - summaryScrollOrigin))
    }

    private func resetConversationPosition() {
        expandedMessageKeys.removeAll()
        activeMessageKey = nil
        initializedMatterID = nil
        summaryScrollOrigin = nil
        readerChromeState.reset()
        establishingInitialScrollPosition = false
        initialScrollOriginCaptureReady = false
        initialScrollMeasurementRequest = 0
        initialTopPeekRequest = nil
    }

    private var groupingEventMessageIDs: Set<String> {
        Set(matter.matterMessageIDs)
    }

    private func groupingDetails(for messageID: String) -> EmailMessageGroupingDetails? {
        guard groupingEventMessageIDs.contains(messageID) else { return nil }
        let explanation = groupingExplanations[messageID]
        return EmailMessageGroupingDetails(
            explanation: explanation?.explanation,
            supportingFactors: explanation?.supportingFactors ?? [],
            conflictingFactors: explanation?.conflictingFactors ?? [],
            isLoading: explanationMessageIDsLoading.contains(messageID)
        )
    }

    private func loadGroupingExplanation(for messageID: String) {
        guard groupingExplanations[messageID] == nil,
              !explanationMessageIDsLoading.contains(messageID)
        else { return }
        onLoadGroupingExplanation(messageID)
    }
}

private struct AIMatterMessageRenderIdentity: Equatable {
    let id: EmailThreadPresentationItem.ID
    let renderRevision: UInt64
}

/// Keeps high-frequency reader chrome changes inside the small pinned summary
/// instead of invalidating the complete matter conversation and its HTML body.
private struct AIMatterPinnedSummary: View {
    let summary: String
    let colorScheme: ColorScheme
    let gradientSeed: String
    let contentWidth: CGFloat
    @ObservedObject var chromeState: AIReaderChromeState
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        Group {
            if chromeState.isSummaryCollapsed {
                AIMatterCompactSummaryCard(
                    summary: summary,
                    colorScheme: colorScheme,
                    gradientSeed: gradientSeed
                )
                .transition(.opacity.combined(with: .scale(scale: 0.985, anchor: .top)))
            } else {
                AIMatterSummaryCard(
                    summary: summary,
                    colorScheme: colorScheme,
                    gradientSeed: gradientSeed
                )
                .transition(.opacity.combined(with: .scale(scale: 0.985, anchor: .top)))
            }
        }
        .frame(width: contentWidth, alignment: .leading)
        .padding(.top, chromeState.isSummaryCollapsed ? 4 : ElectronicMailControlMetrics.readerContentTop)
        .padding(.bottom, ElectronicMailControlMetrics.readerPinnedSummaryBottomSpacing)
        .frame(maxWidth: .infinity)
        .background {
            GeometryReader { summaryProxy in
                Color.clear.preference(
                    key: AIMatterPinnedSummaryHeightPreferenceKey.self,
                    value: summaryProxy.size.height
                )
            }
        }
        .animation(
            reduceMotion ? nil : .easeOut(duration: 0.18),
            value: chromeState.isSummaryCollapsed
        )
    }
}

private struct AIMatterPinnedSummaryHeightPreferenceKey: PreferenceKey {
    static let defaultValue: CGFloat = 0

    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = nextValue()
    }
}

enum AIReaderChromeScrollPolicy {
    static let collapseThreshold: CGFloat = 32
    static let expandThreshold: CGFloat = 8

    static func isSummaryCollapsed(
        currentlyCollapsed: Bool,
        scrollDistance: CGFloat
    ) -> Bool {
        let distance = max(0, scrollDistance)
        if currentlyCollapsed {
            return distance > expandThreshold
        }
        return distance >= collapseThreshold
    }
}

private struct AIMatterCompactSummaryCard: View {
    let summary: String
    let colorScheme: ColorScheme
    let gradientSeed: String

    private var textGradient: LinearGradient {
        AISummaryGradientVariant.stableVariant(for: gradientSeed).gradient
    }

    var body: some View {
        HStack(spacing: 12) {
            Label("AI summary", systemImage: "sparkles")
                .font(ElectronicMailReaderType.metadata(weight: .semibold))
                .foregroundStyle(textGradient)
                .fixedSize()

            Text(summary)
                .font(ElectronicMailReaderType.metadata())
                .foregroundStyle(textGradient)
                .lineLimit(1)
                .truncationMode(.tail)

            Spacer(minLength: 0)
        }
        .padding(.horizontal, 16)
        .frame(maxWidth: .infinity, minHeight: 52, maxHeight: 52, alignment: .leading)
        .aiSummaryGlassSurface()
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("AI summary: \(summary)")
    }
}

private struct AIMatterSummaryCard: View {
    let summary: String
    let colorScheme: ColorScheme
    let gradientSeed: String

    private var textGradient: LinearGradient {
        AISummaryGradientVariant.stableVariant(for: gradientSeed).gradient
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            Label("AI summary", systemImage: "sparkles")
                .font(ElectronicMailReaderType.metadata(weight: .semibold))
                .foregroundStyle(textGradient)

            Text(summary)
                .font(ElectronicMailReaderType.body())
                .foregroundStyle(textGradient)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .aiSummaryGlassSurface()
        .accessibilityElement(children: .combine)
        .accessibilityLabel("AI summary: \(summary)")
    }
}

private extension View {
    @ViewBuilder
    func aiSummaryGlassSurface() -> some View {
        if #available(macOS 26.0, *) {
            background {
                Color.clear
                    .glassEffect(
                        .clear,
                        in: .rect(cornerRadius: 12)
                    )
                    .opacity(0.24)
            }
        } else {
            background {
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .fill(.ultraThinMaterial)
                    .opacity(0.24)
            }
        }
    }
}

private enum AISummaryGradientVariant: Int, CaseIterable {
    case coolAurora
    case warmSunset
    case oceanViolet
    case softIridescent

    var gradient: LinearGradient {
        LinearGradient(
            colors: colors,
            startPoint: .topLeading,
            endPoint: .bottomTrailing
        )
    }

    private var colors: [Color] {
        switch self {
        case .coolAurora:
            return [
                Color(red: 79.0 / 255.0, green: 209.0 / 255.0, blue: 197.0 / 255.0),
                Color(red: 91.0 / 255.0, green: 140.0 / 255.0, blue: 255.0 / 255.0),
                Color(red: 139.0 / 255.0, green: 92.0 / 255.0, blue: 246.0 / 255.0),
                Color(red: 217.0 / 255.0, green: 106.0 / 255.0, blue: 203.0 / 255.0),
            ]
        case .warmSunset:
            return [
                Color(red: 255.0 / 255.0, green: 138.0 / 255.0, blue: 101.0 / 255.0),
                Color(red: 246.0 / 255.0, green: 196.0 / 255.0, blue: 83.0 / 255.0),
                Color(red: 233.0 / 255.0, green: 106.0 / 255.0, blue: 146.0 / 255.0),
                Color(red: 155.0 / 255.0, green: 107.0 / 255.0, blue: 223.0 / 255.0),
            ]
        case .oceanViolet:
            return [
                Color(red: 93.0 / 255.0, green: 214.0 / 255.0, blue: 192.0 / 255.0),
                Color(red: 75.0 / 255.0, green: 157.0 / 255.0, blue: 255.0 / 255.0),
                Color(red: 99.0 / 255.0, green: 102.0 / 255.0, blue: 241.0 / 255.0),
                Color(red: 167.0 / 255.0, green: 139.0 / 255.0, blue: 250.0 / 255.0),
            ]
        case .softIridescent:
            return [
                Color(red: 169.0 / 255.0, green: 225.0 / 255.0, blue: 211.0 / 255.0),
                Color(red: 166.0 / 255.0, green: 211.0 / 255.0, blue: 239.0 / 255.0),
                Color(red: 198.0 / 255.0, green: 180.0 / 255.0, blue: 231.0 / 255.0),
                Color(red: 235.0 / 255.0, green: 183.0 / 255.0, blue: 205.0 / 255.0),
                Color(red: 241.0 / 255.0, green: 195.0 / 255.0, blue: 169.0 / 255.0),
            ]
        }
    }

    static func stableVariant(for seed: String) -> Self {
        // Swift's native hash is randomized between process launches. FNV-1a
        // keeps each matter's palette stable while distributing matters across
        // all four visual treatments.
        var hash: UInt64 = 14_695_981_039_346_656_037
        for byte in seed.utf8 {
            hash ^= UInt64(byte)
            hash &*= 1_099_511_628_211
        }

        return allCases[Int(hash % UInt64(allCases.count))]
    }
}

private struct AIMatterReviewSection: View {
    let proposals: [AIReviewProposal]
    let colorScheme: ColorScheme
    let disabled: Bool
    let onKeepSeparate: (AIReviewProposal) -> Void
    let onAdd: (AIReviewProposal) -> Void
    let onOpenEmail: (AIReviewProposal) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label(
                "\(proposals.count) email\(proposals.count == 1 ? "" : "s") to review",
                systemImage: "exclamationmark.bubble.fill"
            )
            .font(ElectronicMailReaderType.metadata(weight: .semibold))
            .foregroundStyle(Color.orange)

            ForEach(proposals) { proposal in
                VStack(alignment: .leading, spacing: 9) {
                    Text(proposal.subject)
                        .font(ElectronicMailReaderType.metadata(weight: .semibold))
                        .lineLimit(2)
                    Text(proposal.explanation)
                        .font(ElectronicMailReaderType.body())
                        .fixedSize(horizontal: false, vertical: true)

                    if !proposal.supportingFactors.isEmpty {
                        factorList("Evidence for", values: proposal.supportingFactors, color: .green)
                    }
                    if !proposal.conflictingFactors.isEmpty {
                        factorList("Evidence against", values: proposal.conflictingFactors, color: .orange)
                    }

                    HStack(spacing: 12) {
                        Button("Keep separate") { onKeepSeparate(proposal) }
                        Button("Add to this matter") { onAdd(proposal) }
                            .buttonStyle(.borderedProminent)
                        Button("Open email") { onOpenEmail(proposal) }
                    }
                    .controlSize(.small)
                    .disabled(disabled)
                }
                .padding(13)
                .background(Color.orange.opacity(colorScheme == .dark ? 0.10 : 0.07), in: RoundedRectangle(cornerRadius: 9))
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .fill(ElectronicMailDesign.readerControlFill(for: colorScheme))
        )
        .overlay {
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .stroke(Color.orange.opacity(0.45), lineWidth: 1)
        }
    }

    private func factorList(_ title: String, values: [String], color: Color) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title)
                .font(.caption.weight(.semibold))
                .foregroundStyle(color)
            ForEach(values, id: \.self) { value in
                Text("• \(value)")
                    .font(.caption)
                    .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
            }
        }
    }
}

private enum AIInboxListItem: Identifiable, Equatable {
    case matter(AIMatterRow)
    case organizing(AIOrganizingRow)

    var id: String {
        switch self {
        case .matter(let matter): "matter::\(matter.id)"
        case .organizing(let row): "organizing::\(row.id)"
        }
    }

    var latestMessageAt: String {
        switch self {
        case .matter(let matter): matter.latestMessageAt
        case .organizing(let row): row.latestMessageAt
        }
    }
}

private struct AIInboxListSection: Identifiable, Equatable {
    let id: String
    let title: String
    let isFirstSection: Bool
    let rows: [AIInboxListItem]
}

private enum AIInboxChronologyPresenter {
    static func sections(
        response: AIInboxResponse,
        now: Date,
        calendar: Calendar,
        locale: Locale
    ) -> [AIInboxListSection] {
        let startOfToday = calendar.startOfDay(for: now)
        let pastSevenDaysStart = calendar.date(byAdding: .day, value: -7, to: startOfToday) ?? startOfToday
        let monthFormatter = DateFormatter()
        monthFormatter.locale = locale
        monthFormatter.calendar = calendar
        monthFormatter.timeZone = calendar.timeZone
        monthFormatter.setLocalizedDateFormatFromTemplate("MMMM yyyy")

        let sourceRows = response.matters.map(AIInboxListItem.matter)
            + response.organizing.map(AIInboxListItem.organizing)
        let rows = sourceRows.enumerated().sorted { lhs, rhs in
            let lhsDate = AIInboxDateFormatting.date(from: lhs.element.latestMessageAt) ?? .distantPast
            let rhsDate = AIInboxDateFormatting.date(from: rhs.element.latestMessageAt) ?? .distantPast
            return lhsDate == rhsDate ? lhs.offset < rhs.offset : lhsDate > rhsDate
        }.map(\.element)

        var buckets: [(id: String, title: String, rows: [AIInboxListItem])] = []
        for row in rows {
            let bucket = bucket(
                for: row.latestMessageAt,
                startOfToday: startOfToday,
                pastSevenDaysStart: pastSevenDaysStart,
                calendar: calendar,
                monthFormatter: monthFormatter
            )
            if buckets.last?.id == bucket.id {
                buckets[buckets.count - 1].rows.append(row)
            } else {
                buckets.append((bucket.id, bucket.title, [row]))
            }
        }

        return buckets.enumerated().map { index, bucket in
            AIInboxListSection(
                id: bucket.id,
                title: bucket.title,
                isFirstSection: index == 0,
                rows: bucket.rows
            )
        }
    }

    private static func bucket(
        for value: String,
        startOfToday: Date,
        pastSevenDaysStart: Date,
        calendar: Calendar,
        monthFormatter: DateFormatter
    ) -> (id: String, title: String) {
        guard let date = AIInboxDateFormatting.date(from: value) else {
            return ("unknown-date", "Earlier")
        }
        if calendar.isDate(date, inSameDayAs: startOfToday) {
            return ("today", "Today")
        }
        if date >= pastSevenDaysStart, date < startOfToday {
            return ("past-seven-days", "Past 7 days")
        }
        if date < pastSevenDaysStart,
           calendar.isDate(date, equalTo: startOfToday, toGranularity: .month) {
            return ("earlier-this-month", "Earlier this month")
        }
        let components = calendar.dateComponents([.era, .year, .month], from: date)
        return (
            "month::\(components.era ?? 0)-\(components.year ?? 0)-\(components.month ?? 0)",
            monthFormatter.string(from: date)
        )
    }
}

private enum AIInboxDateFormatting {
    private static let fractionalFormatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()

    private static let standardFormatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()

    static func date(from value: String) -> Date? {
        fractionalFormatter.date(from: value) ?? standardFormatter.date(from: value)
    }

    static func mailboxLabel(for value: String, calendar: Calendar = .autoupdatingCurrent) -> String {
        guard let date = date(from: value) else { return value }
        let formatter = DateFormatter()
        formatter.locale = .autoupdatingCurrent
        formatter.calendar = calendar
        formatter.timeZone = calendar.timeZone
        if calendar.isDateInToday(date) {
            formatter.dateStyle = .none
            formatter.timeStyle = .short
        } else {
            formatter.setLocalizedDateFormatFromTemplate("MMM d")
        }
        return formatter.string(from: date)
    }
}

private struct AIInboxListMetrics: Equatable {
    let windowWidth: CGFloat
    let disclosureIconSize: CGFloat = 22
    let disclosureHitWidth: CGFloat = 40
    let senderSubjectGap: CGFloat = 24
    let subjectAttachmentGap: CGFloat = 16
    let attachmentWidth: CGFloat = 34
    let attachmentIconSize: CGFloat = 16
    let statusControlSize: CGFloat = 28
    let attachmentTimeGap: CGFloat = 16
    let timeWidth: CGFloat = 120
    let sectionLabelHeight: CGFloat = 38
    let reviewActionsWidth: CGFloat = 132

    init(windowSize: CGSize) {
        windowWidth = windowSize.width
    }

    private var sharedGrid: ElectronicMailLayoutMetrics {
        ElectronicMailLayoutMetrics(width: windowWidth)
    }

    var utilityCenter: CGFloat { sharedGrid.utilityCenter }
    var disclosureHitLeading: CGFloat { max(0, utilityCenter - disclosureHitWidth / 2) }
    var senderLeading: CGFloat { sharedGrid.textLeading }
    var subjectLeading: CGFloat { sharedGrid.subjectLeading }
    var senderWidth: CGFloat { max(0, subjectLeading - senderLeading - senderSubjectGap) }
    var trailingInset: CGFloat { sharedGrid.dateTrailing }
    var dividerTrailing: CGFloat { trailingInset }
    var reviewActionsTrailing: CGFloat {
        subjectAttachmentGap + attachmentWidth + attachmentTimeGap + timeWidth + trailingInset
    }

    func sectionTopSpacing(isFirst: Bool) -> CGFloat {
        isFirst ? 12 : 24
    }
}

private struct AIInboxSectionHeader: View {
    let title: String
    let isFirstSection: Bool
    let metrics: AIInboxListMetrics
    let colorScheme: ColorScheme

    var body: some View {
        let topSpacing = metrics.sectionTopSpacing(isFirst: isFirstSection)
        VStack(alignment: .leading, spacing: 0) {
            Text(title)
                .font(ElectronicMailMailboxType.section())
                .foregroundStyle(
                    ElectronicMailDesign.sectionText(for: colorScheme)
                        .opacity(ElectronicMailMailboxType.sectionOpacity)
                )
                .lineLimit(1)
                .frame(height: metrics.sectionLabelHeight, alignment: .leading)
                .accessibilityAddTraits(.isHeader)
            Divider()
        }
        .padding(.leading, metrics.senderLeading)
        .padding(.trailing, metrics.dividerTrailing)
        .padding(.top, topSpacing)
        .frame(
            width: metrics.windowWidth,
            height: topSpacing + metrics.sectionLabelHeight + 1,
            alignment: .topLeading
        )
    }
}

private struct AIInboxStatusIcon: View {
    let symbol: String
    let color: Color
    let metrics: AIInboxListMetrics
    let colorScheme: ColorScheme

    var body: some View {
        Image(systemName: symbol)
            .font(.system(size: metrics.attachmentIconSize, weight: .semibold))
            .symbolRenderingMode(.monochrome)
            .foregroundStyle(color)
            .frame(
                width: metrics.statusControlSize,
                height: metrics.statusControlSize
            )
            .background {
                Circle()
                    .fill(ElectronicMailDesign.readerActionFill(for: colorScheme))
            }
            .overlay {
                Circle()
                    .stroke(
                        ElectronicMailDesign.panelBorder(for: colorScheme),
                        lineWidth: 0.75
                    )
            }
    }
}

private struct AIInboxMatterListRow: View {
    let matter: AIMatterRow
    let isSelected: Bool
    let metrics: AIInboxListMetrics
    let colorScheme: ColorScheme
    let disabled: Bool
    let onOpen: () -> Void

    var body: some View {
        ZStack(alignment: .leading) {
            Button(action: onOpen) {
                HStack(spacing: 0) {
                    Color.clear.frame(width: metrics.senderLeading)

                    rowText(
                        participantLabel,
                        font: ElectronicMailMailboxType.sender(unread: matter.unread),
                        color: primaryTextColor
                    )
                    .frame(width: metrics.senderWidth, alignment: .leading)
                    .clipped()

                    Color.clear.frame(width: metrics.senderSubjectGap)

                    rowText(
                        matter.title,
                        font: ElectronicMailMailboxType.subject(unread: matter.unread),
                        color: primaryTextColor
                    )
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .clipped()

                    Color.clear.frame(width: metrics.subjectAttachmentGap)

                    stateIndicator
                        .frame(width: metrics.attachmentWidth, alignment: .center)

                    Color.clear.frame(width: metrics.attachmentTimeGap)

                    rowText(
                        AIInboxDateFormatting.mailboxLabel(for: matter.latestMessageAt),
                        font: ElectronicMailMailboxType.metadata(unread: matter.unread),
                        color: metadataTextColor
                    )
                    .frame(width: metrics.timeWidth, alignment: .trailing)
                    .clipped()

                    Color.clear.frame(width: metrics.trailingInset)
                }
                .frame(width: metrics.windowWidth, height: ElectronicMailMailboxType.rowHeight)
                .background(isSelected ? ElectronicMailDesign.appleBlue : Color.clear)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .disabled(disabled)
            .accessibilityLabel(accessibilityLabel)
            .accessibilityHint("Opens the matter summary and chronological messages")
            .accessibilityAddTraits(isSelected ? .isSelected : [])

            if matter.messageCount > 1 {
                Button(action: onOpen) {
                    Image(systemName: "chevron.right")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(isSelected ? selectedTextColor : ElectronicMailDesign.appleBlue)
                        .frame(width: metrics.disclosureIconSize, height: metrics.disclosureIconSize)
                        .frame(width: metrics.disclosureHitWidth, height: ElectronicMailMailboxType.rowHeight)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .disabled(disabled)
                .offset(x: metrics.disclosureHitLeading)
                .accessibilityLabel("Open \(matter.messageCount) messages")
            }

        }
        .frame(
            width: metrics.windowWidth,
            height: ElectronicMailMailboxType.rowHeight,
            alignment: .topLeading
        )
    }

    private var participantLabel: String {
        AIInboxParticipantFormatting.listLabel(
            counterpartEntities: matter.counterpartEntities,
            fallbackParticipants: matter.participants,
            emptyLabel: matter.status.label
        )
    }

    private var stateIndicator: some View {
        ZStack(alignment: .topTrailing) {
            AIInboxStatusIcon(
                symbol: stateSymbol,
                color: stateColor,
                metrics: metrics,
                colorScheme: colorScheme
            )
            if (matter.reviewCount ?? 0) > 0 {
                Text("\(matter.reviewCount ?? 0)")
                    .font(.system(size: 9, weight: .bold, design: .rounded))
                    .foregroundStyle(.white)
                    .frame(minWidth: 14, minHeight: 14)
                    .background(Color.orange, in: Circle())
                    .offset(x: 8, y: -8)
            }
        }
            .help(stateHelp)
            .accessibilityLabel(stateHelp)
    }

    private var stateSymbol: String {
        return switch matter.status {
        case .needsYou: "exclamationmark"
        case .waitingOnOthers: "clock.fill"
        case .inProgress: "arrow.trianglehead.2.clockwise.rotate.90"
        case .completed: "checkmark"
        case .partiallyCompleted: "checkmark.circle.badge.questionmark"
        case .failed: "exclamationmark.triangle.fill"
        case .cancelled: "xmark"
        case .informational: "info"
        }
    }

    private var stateColor: Color {
        return switch matter.status {
        case .needsYou: .orange
        case .waitingOnOthers, .inProgress: ElectronicMailDesign.appleBlue
        case .completed: .green
        case .partiallyCompleted: .yellow
        case .failed: .red
        case .cancelled: ElectronicMailDesign.secondaryText(for: colorScheme)
        case .informational: ElectronicMailDesign.appleBlue
        }
    }

    private var stateHelp: String {
        (matter.reviewCount ?? 0) > 0
            ? "\(matter.reviewCount ?? 0) email\((matter.reviewCount ?? 0) == 1 ? "" : "s") to review"
            : matter.status.label
    }

    private var accessibilityLabel: String {
        [
            matter.unread ? "Unread" : "Read",
            participantLabel,
            matter.title,
            AIInboxDateFormatting.mailboxLabel(for: matter.latestMessageAt),
            "\(matter.messageCount) messages",
            stateHelp,
        ].joined(separator: ", ")
    }

    private func rowText(_ value: String, font: Font, color: Color) -> some View {
        Text(value)
            .font(font)
            .foregroundStyle(color)
            .lineLimit(1)
            .truncationMode(.tail)
    }

    private var selectedTextColor: Color {
        ElectronicMailDesign.selectedText(for: colorScheme)
    }

    private var primaryTextColor: Color {
        if isSelected { return selectedTextColor }
        return matter.unread
            ? ElectronicMailDesign.unreadText(for: colorScheme)
            : ElectronicMailDesign.readText(for: colorScheme)
    }

    private var metadataTextColor: Color {
        if isSelected { return selectedTextColor }
        return matter.unread
            ? ElectronicMailDesign.unreadText(for: colorScheme).opacity(0.88)
            : ElectronicMailDesign.secondaryText(for: colorScheme)
    }
}

private struct AIInboxOrganizingListRow: View {
    let row: AIOrganizingRow
    let metrics: AIInboxListMetrics
    let colorScheme: ColorScheme

    var body: some View {
        HStack(spacing: 0) {
            Color.clear.frame(width: metrics.senderLeading)

            Text(senderLabel)
                .font(ElectronicMailMailboxType.sender(unread: false))
                .foregroundStyle(ElectronicMailDesign.readText(for: colorScheme))
                .lineLimit(1)
                .truncationMode(.tail)
                .frame(width: metrics.senderWidth, alignment: .leading)
                .clipped()

            Color.clear.frame(width: metrics.senderSubjectGap)

            Text(row.state == "failed" ? "\(row.title) — Couldn’t organize yet" : row.title)
                .font(ElectronicMailMailboxType.subject(unread: false))
                .foregroundStyle(
                    row.state == "failed"
                        ? Color.orange
                        : ElectronicMailDesign.readText(for: colorScheme)
                )
                .lineLimit(1)
                .truncationMode(.tail)
                .frame(maxWidth: .infinity, alignment: .leading)
                .clipped()

            Color.clear.frame(width: metrics.subjectAttachmentGap)

            Group {
                if row.state == "failed" {
                    AIInboxStatusIcon(
                        symbol: "exclamationmark.triangle.fill",
                        color: .red,
                        metrics: metrics,
                        colorScheme: colorScheme
                    )
                } else {
                    AIInboxStatusIcon(
                        symbol: "sparkles",
                        color: .yellow,
                        metrics: metrics,
                        colorScheme: colorScheme
                    )
                }
            }
            .frame(width: metrics.attachmentWidth, alignment: .center)

            Color.clear.frame(width: metrics.attachmentTimeGap)

            Text(AIInboxDateFormatting.mailboxLabel(for: row.latestMessageAt))
                .font(ElectronicMailMailboxType.metadata(unread: false))
                .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                .lineLimit(1)
                .frame(width: metrics.timeWidth, alignment: .trailing)
                .clipped()

            Color.clear.frame(width: metrics.trailingInset)
        }
        .frame(width: metrics.windowWidth, height: ElectronicMailMailboxType.rowHeight)
        .contentShape(Rectangle())
        .help(row.state == "failed" ? "Still available in Inbox" : "Organizing…")
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(senderLabel), \(row.title), \(row.state == "failed" ? "Could not organize yet" : "Organizing")")
    }

    private var senderLabel: String {
        if let counterpartLabel = AIInboxParticipantFormatting.counterpartLabel(
            row.counterpartEntities
        ) {
            return counterpartLabel
        }
        guard let sender = row.sender else { return "Organizing…" }
        let label = AIInboxParticipantFormatting.compactLabel(sender)
        return label.isEmpty ? "Organizing…" : label
    }
}

enum AIInboxParticipantFormatting {
    static func compactLabel(_ value: String) -> String {
        EmailAddressDisplayFormatter.displayName(from: value)
    }

    static func listLabel(
        counterpartEntities: [String]?,
        fallbackParticipants: [String],
        emptyLabel: String
    ) -> String {
        if let label = counterpartLabel(counterpartEntities) {
            return label
        }

        let labels = uniqueLabels(fallbackParticipants.map(compactLabel))
        return naturalLanguageLabel(labels) ?? emptyLabel
    }

    static func counterpartLabel(_ values: [String]?) -> String? {
        let labels = uniqueLabels(values ?? [])
        return naturalLanguageLabel(labels)
    }

    private static func naturalLanguageLabel(_ labels: [String]) -> String? {
        guard let last = labels.last else { return nil }
        if labels.count == 1 { return last }
        if labels.count == 2 { return "\(labels[0]) and \(last)" }
        return "\(labels.dropLast().joined(separator: ", ")), and \(last)"
    }

    private static func uniqueLabels(_ values: [String]) -> [String] {
        var seen = Set<String>()
        return values.compactMap { value in
            let label = value.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !label.isEmpty else { return nil }
            let key = label.folding(options: [.caseInsensitive, .diacriticInsensitive], locale: .current)
            guard seen.insert(key).inserted else { return nil }
            return label
        }
    }
}

private struct AIInboxSettingsSheet: View {
    @Environment(\.dismiss) private var dismiss
    @ObservedObject var store: AIInboxStore
    let profile: AIOrganizationProfile

    @State private var style: AIGroupingStyle
    @State private var confirmDelete = false

    init(store: AIInboxStore, profile: AIOrganizationProfile) {
        self.store = store
        self.profile = profile
        _style = State(initialValue: profile.groupingStyle)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack {
                Text("AI Inbox Settings").font(.title2.weight(.semibold))
                Spacer()
                Button("Done") { dismiss() }.keyboardShortcut(.cancelAction)
            }

            Picker("Grouping style", selection: $style) {
                ForEach(AIGroupingStyle.allCases) { option in
                    Text(option.title).tag(option)
                }
            }
            .pickerStyle(.segmented)

            Text("Future mail changes the grouping preference without moving existing messages. Rebuild creates a new generation, while confirmed memberships and explicit separations remain protected.")
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            HStack {
                Button("Apply to Future Mail") {
                    Task {
                        if await store.updateGroupingStyle(style, rebuildExisting: false) {
                            dismiss()
                        }
                    }
                }
                .disabled(style == profile.groupingStyle || store.mutationInProgress)

                Button("Rebuild Automatic Organization") {
                    Task {
                        if await store.updateGroupingStyle(style, rebuildExisting: true) {
                            dismiss()
                        }
                    }
                }
                .disabled(style == profile.groupingStyle || store.mutationInProgress)
            }

            Divider()

            Text("Turning AI Inbox off stops new OpenAI processing. Your normal Inbox and Gmail data remain available.")
                .font(.callout)
                .foregroundStyle(.secondary)
            Button("Disable AI Inbox") {
                Task {
                    if await store.disableAIInbox() { dismiss() }
                }
            }
            .disabled(store.mutationInProgress)

            Button("Delete AI Data", role: .destructive) { confirmDelete = true }
                .disabled(store.mutationInProgress)
            Text("This permanently removes summaries, embeddings, matters, attachment text, and corrections. It does not delete any Gmail message.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding(24)
        .frame(width: 560)
        .alert("Delete all AI Inbox data?", isPresented: $confirmDelete) {
            Button("Cancel", role: .cancel) {}
            Button("Delete AI Data", role: .destructive) {
                Task {
                    if await store.deleteAIData() { dismiss() }
                }
            }
        } message: {
            Text("Gmail mail is preserved, but AI organization and every correction will be removed.")
        }
    }
}

private struct OrganizeMatterSheet: View {
    @Environment(\.dismiss) private var dismiss
    @ObservedObject var store: AIInboxStore
    let matter: AIMatterDetail
    let candidates: [AIMatterRow]

    @State private var targetMatterID: String?
    @State private var selectedMessageIDs: Set<String> = []

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                VStack(alignment: .leading, spacing: 3) {
                    Text("Organize Matter").font(.title2.weight(.semibold))
                    Text(matter.title).foregroundStyle(.secondary).lineLimit(1)
                }
                Spacer()
                Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction)
            }

            Text("Select messages to separate or move. User-confirmed corrections are locked against future automatic moves.")
                .font(.callout)
                .foregroundStyle(.secondary)

            ScrollView {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(matter.messages) { message in
                        Toggle(
                            isOn: Binding(
                                get: { selectedMessageIDs.contains(message.id) },
                                set: { selected in
                                    if selected {
                                        selectedMessageIDs.insert(message.id)
                                    } else {
                                        selectedMessageIDs.remove(message.id)
                                    }
                                }
                            )
                        ) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(message.subject ?? "No subject").lineLimit(1)
                                Text(message.fromAddress ?? "Unknown sender")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(1)
                            }
                        }
                        .toggleStyle(.checkbox)
                        .padding(.vertical, 4)
                    }
                }
            }
            .frame(minHeight: 180, maxHeight: 300)

            Button("Separate Selected Messages") {
                Task {
                    if await store.organize(
                        "separate",
                        matter: matter,
                        messageIDs: Array(selectedMessageIDs)
                    ) {
                        dismiss()
                    }
                }
            }
            .disabled(selectedMessageIDs.isEmpty || store.mutationInProgress)

            Divider()

            if candidates.isEmpty {
                Text("There are no other visible matters to merge with or move messages into.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            } else {
                Picker("Target matter", selection: $targetMatterID) {
                    Text("Choose a matter").tag(String?.none)
                    ForEach(candidates) { candidate in
                        Text(candidate.title).tag(String?.some(candidate.id))
                    }
                }

                HStack {
                    Button("Move Selected") {
                        guard let targetMatterID else { return }
                        Task {
                            if await store.organize(
                                "move",
                                matter: matter,
                                targetMatterID: targetMatterID,
                                messageIDs: Array(selectedMessageIDs)
                            ) {
                                dismiss()
                            }
                        }
                    }
                    .disabled(
                        targetMatterID == nil
                            || selectedMessageIDs.isEmpty
                            || store.mutationInProgress
                    )

                    Button("Merge Entire Matter") {
                        guard let targetMatterID else { return }
                        Task {
                            if await store.organize(
                                "merge",
                                matter: matter,
                                targetMatterID: targetMatterID
                            ) {
                                dismiss()
                            }
                        }
                    }
                    .disabled(targetMatterID == nil || store.mutationInProgress)
                }
            }
        }
        .padding(24)
        .frame(width: 620, height: 590)
    }
}

public extension Notification.Name {
    static let electronicMailAIInboxChanged = Notification.Name("ElectronicMailAIInboxChanged")
}
