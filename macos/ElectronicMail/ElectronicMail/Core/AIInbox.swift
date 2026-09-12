import Foundation
import SwiftUI

#if !ELECTRONIC_MAIL_SHARED_AI_DOMAIN
public enum AIGroupingStyle: String, Codable, CaseIterable, Identifiable {
    case focused
    case broader

    public var id: Self { self }
    var title: String { self == .focused ? "Focused matters" : "Broader projects" }
}

public enum AIReaderSummaryVisibility: String, CaseIterable, Identifiable {
    case always
    case off

    public static let storageKey = "ElectronicMail.AIReaderSummaryVisibility"

    public var id: Self { self }

    var title: String {
        switch self {
        case .always: return "Always Show"
        case .off: return "Off"
        }
    }

    var explanation: String {
        switch self {
        case .always:
            return "Shows the AI summary control in the email reader."
        case .off:
            return "Hides AI summaries from the email reader."
        }
    }
}

public struct AIOrganizationProfile: Codable, Equatable {
    var gmailAccountID: String? = nil
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
        case gmailAccountID = "gmail_account_id"
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
    var gmailAccountID: String? = nil
    var sourceAccountEmail: String? = nil
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
        case gmailAccountID = "gmail_account_id"
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
    var gmailAccountID: String? = nil
    var sourceAccountEmail: String? = nil
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
        case gmailAccountID = "gmail_account_id"
    }
}

public struct AIInboxResponse: Codable, Equatable {
    var gmailAccountID: String? = nil
    var profile: AIOrganizationProfile
    let generationID: String?
    let revision: String
    let stale: Bool
    let staleReason: String?
    var matters: [AIMatterRow]
    var organizing: [AIOrganizingRow]
    let generatedAt: String

    enum CodingKeys: String, CodingKey {
        case profile, revision, stale, matters, organizing
        case generationID = "generation_id"
        case staleReason = "stale_reason"
        case generatedAt = "generated_at"
        case gmailAccountID = "gmail_account_id"
    }
}

public enum AITodoDisplayKind: String, Codable {
    case todo
    case worthKnowing = "worth_knowing"
    case hidden
}

public enum AITodoStatus: String, Codable {
    case open
    case completed
    case snoozed
    case dismissed
}

public struct AITodoItem: Codable, Equatable, Identifiable {
    var gmailAccountID: String? = nil
    var sourceAccountEmail: String? = nil
    public let id: String
    let matterID: String
    let sourceSubgoalID: String?
    let displayKind: AITodoDisplayKind
    let title: String?
    let detail: String?
    let actionType: String
    let requirement: String
    let dueAt: String?
    let urgency: String
    let confidence: Double
    let evidenceMessageIDs: [String]
    let evidenceText: String?
    let status: AITodoStatus
    let sourceLabel: String
    let latestMessageAt: String?
    let revision: Int

    enum CodingKeys: String, CodingKey {
        case id, title, detail, requirement, urgency, confidence, status, revision
        case gmailAccountID = "gmail_account_id"
        case matterID = "matter_id"
        case sourceSubgoalID = "source_subgoal_id"
        case displayKind = "display_kind"
        case actionType = "action_type"
        case dueAt = "due_at"
        case evidenceMessageIDs = "evidence_message_ids"
        case evidenceText = "evidence_text"
        case sourceLabel = "source_label"
        case latestMessageAt = "latest_message_at"
    }
}

public struct AITodoResponse: Codable, Equatable {
    var gmailAccountID: String? = nil
    var items: [AITodoItem]
    let organizingCount: Int
    let generatedAt: String

    enum CodingKeys: String, CodingKey {
        case items
        case gmailAccountID = "gmail_account_id"
        case organizingCount = "organizing_count"
        case generatedAt = "generated_at"
    }
}

public struct AITodoUpdateRequest: Codable, Equatable {
    let status: AITodoStatus
    let snoozedUntil: String?

    init(status: AITodoStatus, snoozedUntil: String? = nil) {
        self.status = status
        self.snoozedUntil = snoozedUntil
    }

    enum CodingKeys: String, CodingKey {
        case status
        case snoozedUntil = "snoozed_until"
    }
}

public struct AIMatterDetail: Codable, Equatable, Identifiable {
    var gmailAccountID: String? = nil
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
        case gmailAccountID = "gmail_account_id"
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
    var gmailAccountID: String? = nil
    let clientDecisionID: String
    let decisionID: String
    let matterIDs: [String]
    let revision: String
    let state: String

    enum CodingKeys: String, CodingKey {
        case revision, state
        case gmailAccountID = "gmail_account_id"
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
    var gmailAccountID: String? = nil
    let clientActionID: String
    let matterID: String
    let action: GmailThreadAction
    let targetMessageIDs: [String]
    let affectedThreadCount: Int
    let state: String

    enum CodingKeys: String, CodingKey {
        case action, state
        case gmailAccountID = "gmail_account_id"
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
    @Published private(set) var todoItems: [AITodoItem] = []
    @Published private(set) var todoOrganizingCount = 0
    @Published private(set) var todoLoading = false
    @Published private(set) var todoRefreshFailed = false

    private let client: AppClient
    private let decisionQueue: MatterDecisionQueue
    private var query: String?
    private var requestID = UUID()
    private var detailRequestID = UUID()
    private var detailCache: [String: AIMatterDetail] = [:]

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
            if response?.revision != loaded.revision {
                detailCache.removeAll()
            }
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

    func refreshTodos(limit: Int = 100) async {
        todoLoading = todoItems.isEmpty
        do {
            let loaded = try await client.aiTodos(limit: limit)
            todoItems = loaded.items
            todoOrganizingCount = loaded.organizingCount
            todoRefreshFailed = false
            todoLoading = false
        } catch is CancellationError {
            return
        } catch {
            todoRefreshFailed = true
            todoLoading = false
        }
    }

    func completeTodo(_ item: AITodoItem) async -> Bool {
        mutationInProgress = true
        defer { mutationInProgress = false }
        do {
            _ = try await client.updateAITodo(
                item.id,
                gmailAccountID: item.gmailAccountID,
                request: AITodoUpdateRequest(status: .completed)
            )
            todoItems.removeAll { $0.id == item.id }
            detail = nil
            return true
        } catch {
            errorMessage = error.localizedDescription
        }
        return false
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
        let id = UUID()
        detailRequestID = id
        detailLoading = true
        groupingExplanations = [:]
        explanationMessageIDsLoading = []
        if let cached = detailCache[matterID] {
            detail = cached
            detailLoading = false
            return
        }
        do {
            let loaded = try await client.aiMatter(matterID)
            guard detailRequestID == id else { return }
            detailCache[matterID] = loaded
            detail = loaded
            detailLoading = false
        } catch {
            guard detailRequestID == id else { return }
            detailLoading = false
            errorMessage = error.localizedDescription
        }
    }

    /// Loads a matter for an inline list expansion without changing the active
    /// reader selection. The same cached detail is reused if the user opens
    /// the matter afterwards.
    func detailForExpansion(_ matterID: String) async throws -> AIMatterDetail {
        if let cached = detailCache[matterID] {
            return cached
        }
        let loaded = try await client.aiMatter(matterID)
        detailCache[matterID] = loaded
        return loaded
    }

    func reportExpansionError(_ error: Error) {
        errorMessage = error.localizedDescription
    }

    func closeDetail() {
        detailRequestID = UUID()
        detailLoading = false
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
            let removesMatterFromInbox = action == .moveTrash || action == .deleteForever
            if removesMatterFromInbox {
                response?.matters.removeAll { $0.id == matter.id }
                detailCache[matter.id] = nil
            }
            detail = nil
            // Gmail mutations are queued. Refreshing the AI Inbox immediately can
            // race the worker and briefly restore the group that was just removed.
            // The worker emits an AI Inbox event after the complete group succeeds.
            if !removesMatterFromInbox {
                await refresh(query: query)
            }
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
#endif

@MainActor
public final class AIReaderChromeState: ObservableObject {
    @Published public private(set) var isSummaryCollapsed = true

    public init() {}

    func updateScrollDistance(_ scrollDistance: CGFloat) {
        let nextValue = AIReaderChromeScrollPolicy.isSummaryCollapsed(
            currentlyCollapsed: isSummaryCollapsed,
            scrollDistance: scrollDistance
        )
        guard nextValue != isSummaryCollapsed else { return }
        isSummaryCollapsed = nextValue
    }

    func toggleSummary() {
        isSummaryCollapsed.toggle()
    }

    func reset(collapsed: Bool = true) {
        guard isSummaryCollapsed != collapsed else { return }
        isSummaryCollapsed = collapsed
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

    @State private var selectedMatterID: String?
    @State private var openingMatterID: String?
    @State private var navigationCursor = InboxKeyboardNavigationCursor()
    @FocusState private var isKeyboardNavigationFocused: Bool
    @State private var expandedMatterIDs: Set<String> = []
    @State private var expandedMatterDetails: [String: AIMatterDetail] = [:]
    @State private var expandingMatterIDs: Set<String> = []

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
            } else if store.detailLoading {
                ProgressView("Opening email…")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .onExitCommand {
                        openingMatterID = nil
                        store.closeDetail()
                    }
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
                .frame(maxWidth: .infinity, maxHeight: .infinity)
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
        VStack(alignment: .leading, spacing: 16) {
            Image(systemName: "sparkles.rectangle.stack.fill")
                .font(.system(size: ElectronicMailType.mailboxHeaderSize, weight: .semibold))
                .foregroundStyle(ElectronicMailDesign.appleBlue)
            Text("Set up AI Inbox")
                .font(ElectronicMailType.sectionTitle(weight: .semibold))
            Text("Organize cleaned email into focused matters with OpenAI. Your normal Inbox stays unchanged.")
                .font(ElectronicMailType.body())
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            DisclosureGroup("Privacy details") {
                Text("Authentication codes, links, payment numbers, and account numbers are removed or tokenized first. Bounded text from supported documents may be included. OpenAI's API retention applies, and API data is not used to train models by default.")
                    .font(ElectronicMailType.small())
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 6)
            }
            .font(ElectronicMailType.small(weight: .medium))

            HStack(spacing: 12) {
                Button("Enable AI Inbox") {
                    Task { await store.enable(style: profile.groupingStyle) }
                }
                .buttonStyle(.borderedProminent)
                .disabled(store.mutationInProgress || !profile.available)

                Text("Focused matters")
                    .font(ElectronicMailType.small())
                    .foregroundStyle(.tertiary)
            }
            if !profile.available {
                Text("AI Inbox is currently disabled on this server. Inbox continues to work normally.")
                    .font(ElectronicMailType.small())
                    .foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: 460, alignment: .leading)
        .padding(36)
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
                ScrollViewReader { scrollProxy in
                    ElectronicMailMailboxScrollView(
                        colorScheme: colorScheme,
                        showsIndicators: true
                    ) {
                        LazyVStack(alignment: .leading, spacing: 0) {
                            Color.clear
                                .frame(height: 0)
                                .id("ai-inbox-list-top")

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
                                            detail: expandedMatterDetails[matter.id],
                                            isExpanded: expandedMatterIDs.contains(matter.id),
                                            isExpanding: expandingMatterIDs.contains(matter.id),
                                            metrics: metrics,
                                            colorScheme: colorScheme,
                                            disabled: store.mutationInProgress,
                                            onOpen: { open(matter) },
                                            onToggleExpansion: { toggleExpansion(for: matter) }
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
                    .focused($isKeyboardNavigationFocused)
                    .onMoveCommand { direction in
                        switch direction {
                        case .up:
                            moveSelection(in: matters, by: -1, scrollProxy: scrollProxy)
                        case .down:
                            moveSelection(in: matters, by: 1, scrollProxy: scrollProxy)
                        default:
                            break
                        }
                    }
                    .onKeyPress(.return, phases: .down) { _ in
                        openSelectedMatter(in: matters)
                        return .handled
                    }
                    .onAppear {
                        selectFirstMatter(in: matters, scrollProxy: scrollProxy)
                        DispatchQueue.main.async {
                            isKeyboardNavigationFocused = true
                        }
                    }
                    .onChange(of: matters.map(\.id)) { _, _ in
                        normalizeSelection(in: matters)
                    }
                    .transaction { transaction in
                        transaction.disablesAnimations = true
                    }
                }
            }
        }
        .overlay {
            if store.stale || store.errorMessage != nil {
                ElectronicMailRefreshFailureToast(
                    message: store.errorMessage
                        ?? "AI Inbox could not refresh. Showing its last stable state."
                )
            }
        }
    }

    private func open(_ matter: AIMatterRow) {
        guard !store.mutationInProgress, openingMatterID == nil else { return }
        selectedMatterID = matter.id
        navigationCursor.selectedRowID = matter.id
        openingMatterID = matter.id
        Task {
            await store.select(matter.id)
            if openingMatterID == matter.id {
                openingMatterID = nil
            }
        }
    }

    private func toggleExpansion(for matter: AIMatterRow) {
        guard matter.messageCount > 1, !store.mutationInProgress else { return }
        if expandedMatterIDs.contains(matter.id) {
            expandedMatterIDs.remove(matter.id)
            return
        }
        if expandedMatterDetails[matter.id] != nil {
            expandedMatterIDs.insert(matter.id)
            return
        }

        expandingMatterIDs.insert(matter.id)
        Task {
            defer { expandingMatterIDs.remove(matter.id) }
            do {
                expandedMatterDetails[matter.id] = try await store.detailForExpansion(matter.id)
                expandedMatterIDs.insert(matter.id)
            } catch {
                store.reportExpansionError(error)
            }
        }
    }

    private func moveSelection(
        in matters: [AIMatterRow],
        by delta: Int,
        scrollProxy: ScrollViewProxy
    ) {
        guard !matters.isEmpty else { return }
        let currentSelectionID = navigationCursor.selectedRowID ?? selectedMatterID
        let currentIndex = currentSelectionID.flatMap { selectedID in
            matters.firstIndex(where: { $0.id == selectedID })
        }
        let fallbackIndex = delta > 0 ? -1 : matters.count
        let nextIndex = min(max((currentIndex ?? fallbackIndex) + delta, 0), matters.count - 1)
        let nextMatter = matters[nextIndex]
        navigationCursor.selectedRowID = nextMatter.id
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

    private func normalizeSelection(in matters: [AIMatterRow]) {
        guard let firstMatter = matters.first else {
            navigationCursor.selectedRowID = nil
            selectedMatterID = nil
            return
        }
        if selectedMatterID == nil || !matters.contains(where: { $0.id == selectedMatterID }) {
            navigationCursor.selectedRowID = firstMatter.id
            selectedMatterID = firstMatter.id
        } else {
            navigationCursor.selectedRowID = selectedMatterID
        }
    }

    private func selectFirstMatter(
        in matters: [AIMatterRow],
        scrollProxy: ScrollViewProxy
    ) {
        guard let firstMatter = matters.first else {
            navigationCursor.selectedRowID = nil
            selectedMatterID = nil
            return
        }
        navigationCursor.selectedRowID = firstMatter.id
        selectedMatterID = firstMatter.id
        DispatchQueue.main.async {
            scrollProxy.scrollTo("ai-inbox-list-top", anchor: .top)
        }
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

    @AppStorage(AIReaderSummaryVisibility.storageKey)
    private var summaryVisibilityRawValue = AIReaderSummaryVisibility.always.rawValue

    @State private var expandedMessageKeys: Set<EmailThreadPresentationItem.ID> = []
    @State private var activeMessageKey: EmailThreadPresentationItem.ID?
    @State private var initializedMatterID: String?
    @State private var pinnedSummaryCardHeight: CGFloat = 0
    @State private var summaryScrollOrigin: CGFloat?
    @State private var establishingInitialScrollPosition = false
    @State private var initialScrollOriginCaptureReady = false
    @State private var initialScrollMeasurementRequest = 0
    @State private var initialTopPeekRequest: Int?
    @State private var conversationContentHeight: CGFloat = 0

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
            let summaryVisibility = AIReaderSummaryVisibility(rawValue: summaryVisibilityRawValue)
                ?? .always
            let showsSummary = AIReaderSummaryPresentationPolicy.shouldPresent(
                mode: summaryVisibility,
                summary: matter.summary
            )
            let pinnedSummaryHeight = showsSummary ? pinnedSummaryCardHeight : 0
            let readerActionsVisible = activeMessage.flatMap(replyThreadID(for:)) != nil
            let scrollViewportHeight = max(0, proxy.size.height - pinnedSummaryHeight)
            let needsReaderActionClearance = ElectronicMailReaderActionClearance.isRequired(
                contentHeight: conversationContentHeight,
                viewportHeight: scrollViewportHeight,
                actionsVisible: readerActionsVisible
            )

            ZStack(alignment: .bottom) {
                VStack(alignment: .leading, spacing: 0) {
                    if showsSummary {
                        AIMatterPinnedSummary(
                            summary: matter.summary,
                            colorScheme: colorScheme,
                            gradientSeed: matter.id,
                            contentWidth: contentWidth,
                            chromeState: readerChromeState,
                            onToggle: toggleSummary
                        )
                        .zIndex(1)
                    }

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
                            }
                            .background {
                                GeometryReader { contentProxy in
                                    Color.clear.preference(
                                        key: ElectronicMailReaderContentHeightPreferenceKey.self,
                                        value: contentProxy.size.height
                                    )
                                }
                            }

                            if needsReaderActionClearance {
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
            .onPreferenceChange(ElectronicMailReaderContentHeightPreferenceKey.self) { nextHeight in
                guard abs(nextHeight - conversationContentHeight) > 0.25 else { return }
                conversationContentHeight = nextHeight
            }
            .onChange(of: summaryVisibilityRawValue) { _, _ in
                resetSummaryPresentation()
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
            resetSummaryPresentation()
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

    private func resetSummaryPresentation() {
        readerChromeState.reset(collapsed: true)
        summaryScrollOrigin = nil
    }

    private func toggleSummary() {
        let wasCollapsed = readerChromeState.isSummaryCollapsed
        readerChromeState.toggleSummary()
        if wasCollapsed {
            summaryScrollOrigin = nil
        }
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
    let onToggle: () -> Void

    var body: some View {
        AIMatterSummaryDisclosure(
            summary: summary,
            colorScheme: colorScheme,
            gradientSeed: gradientSeed,
            isExpanded: !chromeState.isSummaryCollapsed,
            onToggle: onToggle
        )
        .frame(width: contentWidth, alignment: .leading)
        .padding(.top, 4)
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
    }
}

private struct AIMatterPinnedSummaryHeightPreferenceKey: PreferenceKey {
    static let defaultValue: CGFloat = 0

    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = nextValue()
    }
}

enum AIReaderChromeScrollPolicy {
    static let collapseDistance: CGFloat = 96

    static func isSummaryCollapsed(
        currentlyCollapsed: Bool,
        scrollDistance: CGFloat
    ) -> Bool {
        currentlyCollapsed || scrollDistance >= collapseDistance
    }
}

enum AIReaderSummaryPresentationPolicy {
    static func shouldPresent(
        mode: AIReaderSummaryVisibility,
        summary: String
    ) -> Bool {
        guard !summary.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return false
        }

        switch mode {
        case .always:
            return true
        case .off:
            return false
        }
    }
}

private struct AIMatterSummaryDisclosure: View {
    let summary: String
    let colorScheme: ColorScheme
    let gradientSeed: String
    let isExpanded: Bool
    let onToggle: () -> Void

    private var textGradient: LinearGradient {
        AISummaryGradientVariant.stableVariant(for: gradientSeed).gradient
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            Button(action: onToggle) {
                HStack(spacing: 8) {
                    Image(systemName: "chevron.right")
                        .font(.system(size: 10, weight: .semibold))
                        .rotationEffect(.degrees(isExpanded ? 90 : 0))

                    Label("AI summary", systemImage: "sparkles")
                        .font(ElectronicMailReaderType.metadata(weight: .semibold))

                    Spacer(minLength: 0)
                }
                .foregroundStyle(textGradient)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel(isExpanded ? "Collapse AI summary" : "Expand AI summary")

            if isExpanded {
                Text(summary)
                    .font(ElectronicMailReaderType.body())
                    .foregroundStyle(textGradient)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .electronicMailSummaryCardSurface()
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
    let childIndent: CGFloat = 22
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
        isFirst
            ? ElectronicMailControlMetrics.mailboxFirstSectionTopSpacing
            : ElectronicMailControlMetrics.mailboxSectionTopSpacing
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
    let detail: AIMatterDetail?
    let isExpanded: Bool
    let isExpanding: Bool
    let metrics: AIInboxListMetrics
    let colorScheme: ColorScheme
    let disabled: Bool
    let onOpen: () -> Void
    let onToggleExpansion: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            parentRow
            if isExpanded, let detail {
                VStack(spacing: 0) {
                    ForEach(detail.messages) { message in
                        AIInboxMatterMessageListRow(
                            message: message,
                            metrics: metrics,
                            colorScheme: colorScheme,
                            onOpen: onOpen
                        )
                    }
                }
                .background(expandedMessagesTint)
            }
        }
        .frame(width: metrics.windowWidth, alignment: .topLeading)
    }

    private var expandedMessagesTint: Color {
        ElectronicMailDesign.appleBlue.opacity(colorScheme == .dark ? 0.20 : 0.09)
    }

    private var parentRow: some View {
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
                Button(action: onToggleExpansion) {
                    Image(systemName: isExpanded ? "chevron.down" : "chevron.right")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(isSelected ? selectedTextColor : ElectronicMailDesign.appleBlue)
                        .frame(width: metrics.disclosureIconSize, height: metrics.disclosureIconSize)
                        .frame(width: metrics.disclosureHitWidth, height: ElectronicMailMailboxType.rowHeight)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .disabled(disabled || isExpanding)
                .offset(x: metrics.disclosureHitLeading)
                .accessibilityLabel(isExpanded ? "Collapse \(matter.messageCount) messages" : "Expand \(matter.messageCount) messages")
                .accessibilityHint("Shows or hides the emails in this matter")
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

private struct AIInboxMatterMessageListRow: View {
    let message: ThreadMessage
    let metrics: AIInboxListMetrics
    let colorScheme: ColorScheme
    let onOpen: () -> Void

    var body: some View {
        Button(action: onOpen) {
            HStack(spacing: 0) {
                Color.clear.frame(width: metrics.senderLeading + metrics.childIndent)

                rowText(sender)
                    .frame(width: max(0, metrics.senderWidth - metrics.childIndent), alignment: .leading)
                    .clipped()

                Color.clear.frame(width: metrics.senderSubjectGap)

                rowText(subject)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .clipped()

                Color.clear.frame(width: metrics.subjectAttachmentGap)
                Color.clear.frame(width: metrics.attachmentWidth)
                Color.clear.frame(width: metrics.attachmentTimeGap)

                rowText(AIInboxDateFormatting.mailboxLabel(for: message.receivedAt))
                    .frame(width: metrics.timeWidth, alignment: .trailing)
                    .clipped()

                Color.clear.frame(width: metrics.trailingInset)
            }
            .frame(width: metrics.windowWidth, height: ElectronicMailMailboxType.rowHeight)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Email from \(sender), \(subject)")
        .accessibilityHint("Opens the matter summary and chronological messages")
    }

    private var sender: String {
        EmailAddressDisplayFormatter.displayName(from: message.fromAddress ?? "")
    }

    private var subject: String {
        let generatedTitle = message.aiTitle?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        let value = generatedTitle.isEmpty
            ? (message.subject?.trimmingCharacters(in: .whitespacesAndNewlines) ?? "")
            : generatedTitle
        return value.isEmpty ? "(No subject)" : value
    }

    private func rowText(_ value: String) -> some View {
        Text(value)
            .font(ElectronicMailMailboxType.metadata(unread: false))
            .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
            .lineLimit(1)
            .truncationMode(.tail)
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

#if !ELECTRONIC_MAIL_SHARED_AI_DOMAIN
public extension Notification.Name {
    static let electronicMailAIInboxChanged = Notification.Name("ElectronicMailAIInboxChanged")
}
#endif
