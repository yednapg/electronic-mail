import Combine
import Foundation

public extension Notification.Name {
    static let electronicMailAIInboxChanged = Notification.Name("ElectronicMailAIInboxChanged")
}

public enum AIGroupingStyle: String, Codable, CaseIterable, Identifiable, Sendable {
    case focused
    case broader

    public var id: Self { self }
    public var title: String { self == .focused ? "Focused matters" : "Broader projects" }
}

public enum AIReaderSummaryVisibility: String, CaseIterable, Identifiable, Sendable {
    case always
    case off

    public static let storageKey = "ElectronicMail.AIReaderSummaryVisibility"
    public var id: Self { self }
    public var title: String { self == .always ? "Always Show" : "Off" }
    public var explanation: String {
        self == .always
            ? "Shows the AI summary control in the email reader."
            : "Hides AI summaries from the email reader."
    }
}

public struct AIOrganizationProfile: Codable, Equatable, Sendable {
    public var gmailAccountID: String? = nil
    public let consented: Bool
    public let enabled: Bool
    public let available: Bool
    public let groupingStyle: AIGroupingStyle
    public let rolloutMode: String
    public let activeGenerationID: String?
    public let revision: Int

    enum CodingKeys: String, CodingKey {
        case consented, enabled, available, revision
        case groupingStyle = "grouping_style"
        case rolloutMode = "rollout_mode"
        case activeGenerationID = "active_generation_id"
        case gmailAccountID = "gmail_account_id"
    }
}

public struct AIOrganizationProfilePatch: Codable, Equatable, Sendable {
    public let consent: Bool?
    public let enabled: Bool?
    public let groupingStyle: AIGroupingStyle?
    public let rebuildExisting: Bool

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

public enum AIMatterStatus: String, Codable, Sendable {
    case needsYou = "needs_you"
    case waitingOnOthers = "waiting_on_others"
    case inProgress = "in_progress"
    case completed
    case partiallyCompleted = "partially_completed"
    case failed
    case cancelled
    case informational

    public var label: String {
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

public enum AIMatterConfidenceState: String, Codable, Sendable {
    case automatic
    case provisional
    case confirmed
}

public struct AIMatterRow: Codable, Equatable, Identifiable, Sendable {
    public var gmailAccountID: String? = nil
    public var sourceAccountEmail: String? = nil
    public let id: String
    public let title: String
    public let summary: String
    public let status: AIMatterStatus
    public let confidenceState: AIMatterConfidenceState
    public let confidence: Double
    public let latestMessageAt: String
    public let messageCount: Int
    public let unread: Bool
    public let starred: Bool
    public let participants: [String]
    public var counterpartEntities: [String]? = nil
    public let evidenceMessageIDs: [String]
    public let revision: Int
    public let openSubgoalCount: Int?
    public let reviewCount: Int?
    public let matchingMessageIDs: [String]

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

public struct AIGroupingExplanation: Codable, Equatable, Sendable {
    public let messageID: String
    public let eventRole: String
    public let verdict: String
    public let explanation: String
    public let supportingFactors: [String]
    public let conflictingFactors: [String]
    public let evidenceMessageIDs: [String]

    enum CodingKeys: String, CodingKey {
        case verdict, explanation
        case messageID = "message_id"
        case eventRole = "event_role"
        case supportingFactors = "supporting_factors"
        case conflictingFactors = "conflicting_factors"
        case evidenceMessageIDs = "evidence_message_ids"
    }
}

public struct AIReviewProposal: Codable, Equatable, Identifiable, Sendable {
    public let id: String
    public let messageID: String
    public let subject: String
    public let sender: String?
    public let occurredAt: String
    public let recommendedAction: String
    public let eventRole: String
    public let verdict: String
    public let explanation: String
    public let supportingFactors: [String]
    public let conflictingFactors: [String]
    public let evidenceMessageIDs: [String]

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

public struct AIOrganizingRow: Codable, Equatable, Identifiable, Sendable {
    public var gmailAccountID: String? = nil
    public var sourceAccountEmail: String? = nil
    public let id: String
    public let gmailThreadID: String
    public let title: String
    public let sender: String?
    public var counterpartEntities: [String]? = nil
    public let snippet: String?
    public let latestMessageAt: String
    public let messageCount: Int
    public let state: String
    public let error: String?
    public let matchingMessageIDs: [String]

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

public struct AIInboxResponse: Codable, Equatable, Sendable {
    public var gmailAccountID: String? = nil
    public var profile: AIOrganizationProfile
    public let generationID: String?
    public let revision: String
    public let stale: Bool
    public let staleReason: String?
    public var matters: [AIMatterRow]
    public var organizing: [AIOrganizingRow]
    public let generatedAt: String

    enum CodingKeys: String, CodingKey {
        case profile, revision, stale, matters, organizing
        case generationID = "generation_id"
        case staleReason = "stale_reason"
        case generatedAt = "generated_at"
        case gmailAccountID = "gmail_account_id"
    }
}

public enum AITodoDisplayKind: String, Codable, Sendable {
    case todo
    case worthKnowing = "worth_knowing"
    case hidden
}

public enum AITodoStatus: String, Codable, Sendable {
    case open
    case completed
    case snoozed
    case dismissed
}

public struct AITodoItem: Codable, Equatable, Identifiable, Sendable {
    public var gmailAccountID: String? = nil
    public var sourceAccountEmail: String? = nil
    public let id: String
    public let matterID: String
    public let sourceSubgoalID: String?
    public let displayKind: AITodoDisplayKind
    public let title: String?
    public let detail: String?
    public let actionType: String
    public let requirement: String
    public let dueAt: String?
    public let urgency: String
    public let confidence: Double
    public let evidenceMessageIDs: [String]
    public let evidenceText: String?
    public let status: AITodoStatus
    public let sourceLabel: String
    public let latestMessageAt: String?
    public let revision: Int

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

public struct AITodoResponse: Codable, Equatable, Sendable {
    public var gmailAccountID: String? = nil
    public var items: [AITodoItem]
    public let organizingCount: Int
    public let generatedAt: String

    enum CodingKeys: String, CodingKey {
        case items
        case gmailAccountID = "gmail_account_id"
        case organizingCount = "organizing_count"
        case generatedAt = "generated_at"
    }
}

public struct AITodoUpdateRequest: Codable, Equatable, Sendable {
    public let status: AITodoStatus
    public let snoozedUntil: String?

    public init(status: AITodoStatus, snoozedUntil: String? = nil) {
        self.status = status
        self.snoozedUntil = snoozedUntil
    }

    enum CodingKeys: String, CodingKey {
        case status
        case snoozedUntil = "snoozed_until"
    }
}

public struct AIMatterDetail: Codable, Equatable, Identifiable {
    public var gmailAccountID: String? = nil
    public let id: String
    public let title: String
    public let stableGoal: String
    public let summary: String
    public let status: AIMatterStatus
    public let confidenceState: AIMatterConfidenceState
    public let confidence: Double
    public let evidenceMessageIDs: [String]
    public let revision: Int
    public let latestReplyableMessageID: String?
    public let totalMessages: Int
    public let matterMessageIDs: [String]
    public let reviewProposals: [AIReviewProposal]?
    public let messages: [ThreadMessage]

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

public struct MatterDecisionRequest: Codable, Equatable, Sendable {
    public let clientDecisionID: String
    public let decision: String
    public let matterID: String
    public let targetMatterID: String?
    public let messageIDs: [String]
    public let expectedRevision: Int

    public init(clientDecisionID: String, decision: String, matterID: String, targetMatterID: String?, messageIDs: [String], expectedRevision: Int) {
        self.clientDecisionID = clientDecisionID
        self.decision = decision
        self.matterID = matterID
        self.targetMatterID = targetMatterID
        self.messageIDs = messageIDs
        self.expectedRevision = expectedRevision
    }

    enum CodingKeys: String, CodingKey {
        case decision
        case clientDecisionID = "client_decision_id"
        case matterID = "matter_id"
        case targetMatterID = "target_matter_id"
        case messageIDs = "message_ids"
        case expectedRevision = "expected_revision"
    }
}

public struct MatterDecisionResponse: Codable, Equatable, Sendable {
    public var gmailAccountID: String? = nil
    public let clientDecisionID: String
    public let decisionID: String
    public let matterIDs: [String]
    public let revision: String
    public let state: String

    enum CodingKeys: String, CodingKey {
        case revision, state
        case gmailAccountID = "gmail_account_id"
        case clientDecisionID = "client_decision_id"
        case decisionID = "decision_id"
        case matterIDs = "matter_ids"
    }
}

public struct MatterEntityActionRequest: Codable, Equatable {
    public let clientActionID: String
    public let matterID: String
    public let action: GmailThreadAction
    public let expectedRevision: Int
    public let confirmMultiThreadTrash: Bool

    public init(clientActionID: String, matterID: String, action: GmailThreadAction, expectedRevision: Int, confirmMultiThreadTrash: Bool) {
        self.clientActionID = clientActionID
        self.matterID = matterID
        self.action = action
        self.expectedRevision = expectedRevision
        self.confirmMultiThreadTrash = confirmMultiThreadTrash
    }

    enum CodingKeys: String, CodingKey {
        case action
        case clientActionID = "client_action_id"
        case matterID = "matter_id"
        case expectedRevision = "expected_revision"
        case confirmMultiThreadTrash = "confirm_multi_thread_trash"
    }
}

public struct MatterEntityActionResponse: Codable, Equatable {
    public var gmailAccountID: String? = nil
    public let clientActionID: String
    public let matterID: String
    public let action: GmailThreadAction
    public let targetMessageIDs: [String]
    public let affectedThreadCount: Int
    public let state: String

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
    let accountScopeID: String?
    let request: MatterDecisionRequest
}

private final class MatterDecisionQueue {
    private let defaults: UserDefaults
    private let key = "ElectronicMail.PendingMatterDecisions.v1"

    init(defaults: UserDefaults = .standard) { self.defaults = defaults }

    func read() -> [PendingMatterDecision] {
        guard let data = defaults.data(forKey: key) else { return [] }
        return (try? JSONDecoder.backend.decode([PendingMatterDecision].self, from: data)) ?? []
    }

    func append(_ request: MatterDecisionRequest, accountScopeID: String?) {
        var pending = read()
        guard !pending.contains(where: { $0.id == request.clientDecisionID }) else { return }
        pending.append(PendingMatterDecision(
            id: request.clientDecisionID,
            accountScopeID: accountScopeID,
            request: request
        ))
        write(pending)
    }

    func remove(_ id: String) { write(read().filter { $0.id != id }) }

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
    @Published public private(set) var response: AIInboxResponse?
    @Published public private(set) var detail: AIMatterDetail?
    @Published public private(set) var loading = false
    @Published public private(set) var detailLoading = false
    @Published public private(set) var stale = false
    @Published public private(set) var errorMessage: String?
    @Published public private(set) var mutationInProgress = false
    @Published public private(set) var pendingDecisionCount = 0
    @Published public private(set) var groupingExplanations: [String: AIGroupingExplanation] = [:]
    @Published public private(set) var explanationMessageIDsLoading: Set<String> = []
    @Published public private(set) var todoItems: [AITodoItem] = []
    @Published public private(set) var todoOrganizingCount = 0
    @Published public private(set) var todoLoading = false
    @Published public private(set) var todoRefreshFailed = false

    private let client: AppClient
    private let decisionQueue: MatterDecisionQueue
    private var query: String?
    private var requestID = UUID()
    private var detailRequestID = UUID()
    private var todoRequestID = UUID()
    private var detailCache: [String: AIMatterDetail] = [:]
    private var accountScopeID: String?

    public init(client: AppClient) {
        self.client = client
        decisionQueue = MatterDecisionQueue()
        pendingDecisionCount = decisionQueue.read().filter { $0.accountScopeID == nil }.count
    }

    public var selectedMatterID: String? { detail?.id }
    public var profile: AIOrganizationProfile? { response?.profile }

    public func refresh(query: String? = nil) async {
        self.query = query?.trimmingCharacters(in: .whitespacesAndNewlines)
        let id = UUID()
        requestID = id
        loading = response == nil
        do {
            let loaded = try await client.aiInbox(query: self.query)
            guard requestID == id else { return }
            if response?.revision != loaded.revision { detailCache.removeAll() }
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

    public func refreshTodos(limit: Int = 100) async {
        let id = UUID()
        todoRequestID = id
        todoLoading = todoItems.isEmpty
        do {
            let loaded = try await client.aiTodos(limit: limit)
            guard todoRequestID == id else { return }
            todoItems = loaded.items
            todoOrganizingCount = loaded.organizingCount
            todoRefreshFailed = false
            todoLoading = false
        } catch is CancellationError {
            return
        } catch {
            guard todoRequestID == id else { return }
            todoRefreshFailed = true
            todoLoading = false
        }
    }

    public func resetForAccountChange(accountID: String? = nil) {
        requestID = UUID()
        detailRequestID = UUID()
        todoRequestID = UUID()
        query = nil
        response = nil
        detail = nil
        loading = false
        detailLoading = false
        stale = false
        errorMessage = nil
        mutationInProgress = false
        groupingExplanations = [:]
        explanationMessageIDsLoading = []
        todoItems = []
        todoOrganizingCount = 0
        todoLoading = false
        todoRefreshFailed = false
        detailCache.removeAll()
        accountScopeID = accountID
        pendingDecisionCount = decisionQueue.read().filter { $0.accountScopeID == accountID }.count
    }

    public func completeTodo(_ item: AITodoItem) async -> Bool {
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
            return false
        }
    }

    public func enable(style: AIGroupingStyle) async {
        mutationInProgress = true
        defer { mutationInProgress = false }
        do {
            _ = try await client.updateAIOrganizationProfile(
                AIOrganizationProfilePatch(consent: true, enabled: true, groupingStyle: style)
            )
            await refresh(query: query)
        } catch { errorMessage = error.localizedDescription }
    }

    public func select(_ matterID: String) async {
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
    public func detailForExpansion(_ matterID: String) async throws -> AIMatterDetail {
        if let cached = detailCache[matterID] {
            return cached
        }
        let loaded = try await client.aiMatter(matterID)
        detailCache[matterID] = loaded
        return loaded
    }

    public func reportExpansionError(_ error: Error) {
        errorMessage = error.localizedDescription
    }

    public func closeDetail() {
        detailRequestID = UUID()
        detailLoading = false
        detail = nil
        groupingExplanations = [:]
        explanationMessageIDsLoading = []
    }

    public func decide(_ decision: String, matter: AIMatterRow) async {
        _ = await submitDecision(decision, matterID: matter.id, targetMatterID: nil, messageIDs: [], expectedRevision: matter.revision)
    }

    public func decideProposal(_ decision: String, matter: AIMatterDetail, proposal: AIReviewProposal) async {
        if await submitDecision(decision, matterID: matter.id, targetMatterID: nil, messageIDs: [proposal.messageID], expectedRevision: matter.revision) {
            await select(matter.id)
        }
    }

    public func loadGroupingExplanation(matterID: String, messageID: String) async {
        guard groupingExplanations[messageID] == nil,
              !explanationMessageIDsLoading.contains(messageID) else { return }
        explanationMessageIDsLoading.insert(messageID)
        defer { explanationMessageIDsLoading.remove(messageID) }
        do {
            groupingExplanations[messageID] = try await client.aiGroupingExplanation(matterID: matterID, messageID: messageID)
        } catch { errorMessage = error.localizedDescription }
    }

    public func organize(_ decision: String, matter: AIMatterDetail, targetMatterID: String? = nil, messageIDs: [String] = []) async -> Bool {
        let accepted = await submitDecision(decision, matterID: matter.id, targetMatterID: targetMatterID, messageIDs: messageIDs, expectedRevision: matter.revision)
        if accepted { detail = nil }
        return accepted
    }

    public func updateGroupingStyle(_ style: AIGroupingStyle, rebuildExisting: Bool) async -> Bool {
        mutationInProgress = true
        defer { mutationInProgress = false }
        do {
            _ = try await client.updateAIOrganizationProfile(AIOrganizationProfilePatch(groupingStyle: style, rebuildExisting: rebuildExisting))
            await refresh(query: query)
            return true
        } catch {
            errorMessage = error.localizedDescription
            return false
        }
    }

    public func disableAIInbox() async -> Bool {
        mutationInProgress = true
        defer { mutationInProgress = false }
        do {
            _ = try await client.updateAIOrganizationProfile(AIOrganizationProfilePatch(enabled: false))
            detail = nil
            await refresh()
            return true
        } catch {
            errorMessage = error.localizedDescription
            return false
        }
    }

    public func deleteAIData() async -> Bool {
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

    public func perform(_ action: GmailThreadAction, matter: AIMatterDetail, confirmMultiThreadTrash: Bool = false) async -> Bool {
        mutationInProgress = true
        defer { mutationInProgress = false }
        do {
            _ = try await client.applyMatterAction(MatterEntityActionRequest(
                clientActionID: UUID().uuidString,
                matterID: matter.id,
                action: action,
                expectedRevision: matter.revision,
                confirmMultiThreadTrash: confirmMultiThreadTrash
            ))
            detail = nil
            await refresh(query: query)
            return true
        } catch APIError.httpStatus(let status) where status == 409 {
            errorMessage = "This matter changed on another device. Refresh it before applying the action."
            await refresh(query: query)
        } catch { errorMessage = error.localizedDescription }
        return false
    }

    public func downloadMobileAttachment(messageID: String, attachmentID: String) async throws -> DownloadedAttachment {
        guard let message = detail?.messages.first(where: { $0.id == messageID }),
              let attachment = message.attachments.first(where: { $0.id == attachmentID }) else {
            throw APIError.httpStatus(404)
        }
        return try await client.downloadAttachment(messageID: messageID, attachment: attachment)
    }

    private func submitDecision(_ decision: String, matterID: String, targetMatterID: String?, messageIDs: [String], expectedRevision: Int) async -> Bool {
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
            decisionQueue.append(request, accountScopeID: accountScopeID)
            pendingDecisionCount = decisionQueue.read().filter { $0.accountScopeID == accountScopeID }.count
            errorMessage = "Your correction is saved on this device and will sync when the connection returns."
            return true
        }
    }

    private func replayPendingDecisions() async {
        for pending in decisionQueue.read().filter({ $0.accountScopeID == accountScopeID }) {
            do {
                _ = try await client.applyMatterDecision(pending.request)
                decisionQueue.remove(pending.id)
            } catch APIError.httpStatus(let status) where status == 409 {
                decisionQueue.remove(pending.id)
            } catch { break }
        }
        pendingDecisionCount = decisionQueue.read().filter { $0.accountScopeID == accountScopeID }.count
    }
}
