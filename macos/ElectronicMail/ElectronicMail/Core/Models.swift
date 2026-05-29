import Foundation

public enum SourceType: String, Codable, Equatable {
    case gmail
    case calendar
    case manual
}

public enum TimingBand: String, Codable, Equatable {
    case now
    case today
    case later
    case hidden
}

public enum EntityCurrentState: String, Codable, Equatable {
    case open
    case waiting
    case done
}

public enum GmailThreadAction: String, Codable, Equatable {
    case archive
    case unarchive
    case markRead = "mark_read"
    case moveTrash = "move_trash"
    case deleteForever = "delete_forever"
}

public enum MailSendState: String, Codable, Equatable {
    case queued
    case sending
    case sent
    case failed
    case reauthRequired = "reauth_required"
}

public enum NeedType: String, Codable, Equatable {
    case decision
    case awareness
}

public struct GoogleAuthState: Codable, Equatable {
    let available: Bool
    let connected: Bool
    let connectURL: String?
    let canSendMail: Bool
    let missingScopes: [String]

    init(
        available: Bool,
        connected: Bool,
        connectURL: String?,
        canSendMail: Bool = false,
        missingScopes: [String] = []
    ) {
        self.available = available
        self.connected = connected
        self.connectURL = connectURL
        self.canSendMail = canSendMail
        self.missingScopes = missingScopes
    }

    enum CodingKeys: String, CodingKey {
        case available
        case connected
        case connectURL = "connect_url"
        case canSendMail = "can_send_mail"
        case missingScopes = "missing_scopes"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        available = try container.decode(Bool.self, forKey: .available)
        connected = try container.decode(Bool.self, forKey: .connected)
        connectURL = try container.decodeIfPresent(String.self, forKey: .connectURL)
        canSendMail = try container.decodeIfPresent(Bool.self, forKey: .canSendMail) ?? false
        missingScopes = try container.decodeIfPresent([String].self, forKey: .missingScopes) ?? []
    }
}

public struct DashboardProfile: Codable, Equatable {
    let email: String?
    let displayName: String?

    enum CodingKeys: String, CodingKey {
        case email
        case displayName = "display_name"
    }
}

public struct DashboardBriefingPart: Codable, Equatable, Hashable {
    let type: String
    let emoji: String
    let count: Int
    let text: String
}

public struct DashboardBriefingImportant: Codable, Equatable, Hashable {
    let emoji: String
    let count: Int
    let text: String
    let mailGroupID: String?
    let actionType: String?

    enum CodingKeys: String, CodingKey {
        case emoji
        case count
        case text
        case mailGroupID = "mail_group_id"
        case actionType = "action_type"
    }
}

public struct DashboardCalendarAvailability: Codable, Equatable, Hashable {
    let emoji: String
    let kind: String
    let time: String?
    let text: String
}

public struct DashboardBriefing: Codable, Equatable {
    let headline: String
    let brief: String
    let parts: [DashboardBriefingPart]?
    let important: DashboardBriefingImportant?
    let calendarAvailability: DashboardCalendarAvailability?

    public init(
        headline: String,
        brief: String,
        parts: [DashboardBriefingPart]? = nil,
        important: DashboardBriefingImportant? = nil,
        calendarAvailability: DashboardCalendarAvailability? = nil
    ) {
        self.headline = headline
        self.brief = brief
        self.parts = parts
        self.important = important
        self.calendarAvailability = calendarAvailability
    }

    enum CodingKeys: String, CodingKey {
        case headline
        case brief
        case parts
        case important
        case calendarAvailability = "calendar_availability"
    }
}

public struct DashboardResponse: Codable, Equatable {
    let auth: GoogleAuthState
    let profile: DashboardProfile?
    let briefing: DashboardBriefing?
    let feed: FeedResponse
}

public struct AuthUserResponse: Codable, Equatable {
    let id: String
    let email: String
    let displayName: String?
    let accessEnabled: Bool

    enum CodingKeys: String, CodingKey {
        case id
        case email
        case displayName = "display_name"
        case accessEnabled = "access_enabled"
    }
}

public struct MobileSessionExchangeRequest: Codable, Equatable {
    let loginCode: String

    enum CodingKeys: String, CodingKey {
        case loginCode = "login_code"
    }
}

public struct MobileSessionExchangeResponse: Codable, Equatable {
    public let sessionToken: String
    public let expiresAt: String
    public let user: AuthUserResponse

    enum CodingKeys: String, CodingKey {
        case sessionToken = "session_token"
        case expiresAt = "expires_at"
        case user
    }
}

public struct AppSessionUser: Codable, Equatable {
    let id: String
    let email: String
    let firstName: String?
    let displayName: String?

    enum CodingKeys: String, CodingKey {
        case id
        case email
        case firstName = "first_name"
        case displayName = "display_name"
    }
}

public struct AppSessionSyncState: Codable, Equatable {
    let lastSyncAt: String?
    let lastError: String?
    let enrichmentPendingCount: Int
    let readyGroupCount: Int
    let oldestImportedAt: String?
    let fullImportRunning: Bool
    let fullImportCompleted: Bool
    var fullImportCompletedAt: String? = nil
    var pendingActionCount: Int? = nil
    var lastActionSyncAt: String? = nil
    var lastActionError: String? = nil
    var lastAIError: String? = nil

    enum CodingKeys: String, CodingKey {
        case lastSyncAt = "last_sync_at"
        case lastError = "last_error"
        case enrichmentPendingCount = "enrichment_pending_count"
        case readyGroupCount = "ready_group_count"
        case oldestImportedAt = "oldest_imported_at"
        case fullImportRunning = "full_import_running"
        case fullImportCompleted = "full_import_completed"
        case fullImportCompletedAt = "full_import_completed_at"
        case pendingActionCount = "pending_action_count"
        case lastActionSyncAt = "last_action_sync_at"
        case lastActionError = "last_action_error"
        case lastAIError = "last_ai_error"
    }
}

public struct PostLoginReadinessResponse: Codable, Equatable {
    public let mode: String
    public let stage: String
    public let readyToEnter: Bool
    public let dashboardReady: Bool
    public let mailboxReady: Bool
    public let readyDashboardCount: Int
    public let readyMailGroupCount: Int
    public let fullImportRunning: Bool
    public let fullImportCompleted: Bool
    public let userDisplayName: String?
    public let errorMessage: String?

    enum CodingKeys: String, CodingKey {
        case mode
        case stage
        case readyToEnter = "ready_to_enter"
        case dashboardReady = "dashboard_ready"
        case mailboxReady = "mailbox_ready"
        case readyDashboardCount = "ready_dashboard_count"
        case readyMailGroupCount = "ready_mail_group_count"
        case fullImportRunning = "full_import_running"
        case fullImportCompleted = "full_import_completed"
        case userDisplayName = "user_display_name"
        case errorMessage = "error_message"
    }
}

public struct AppSessionResponse: Codable, Equatable {
    let user: AppSessionUser
    let readiness: PostLoginReadinessResponse
    let dashboard: DashboardResponse
    let mailbox: MailboxResponse
    let sync: AppSessionSyncState
}

public struct FeedResponse: Codable, Equatable {
    let now: [AttentionItem]
    let today: [AttentionItem]
    let worthKnowing: [AttentionItem]

    var isEmpty: Bool {
        now.isEmpty && today.isEmpty && worthKnowing.isEmpty
    }

    enum CodingKeys: String, CodingKey {
        case now
        case today
        case worthKnowing = "worth_knowing"
    }
}

public struct AttentionItemDetail: Codable, Equatable, Hashable {
    let body: [String]
    let actionLabel: String
    let sourceLabel: String

    enum CodingKeys: String, CodingKey {
        case body
        case actionLabel = "action_label"
        case sourceLabel = "source_label"
    }
}

public struct AttentionItem: Codable, Equatable, Identifiable, Hashable {
    public let id: String
    let entityID: String
    let userID: String
    let needType: NeedType
    let actionType: String
    let effortLevel: String
    let timingBand: TimingBand
    let actionConfidence: String
    let primaryAction: String
    let fallbackAction: String
    let title: String
    let whyThisIsHere: String
    let detail: AttentionItemDetail?
    let dueAt: String?
    let importanceLevel: String?
    let lifecycleState: String?
    let currentState: EntityCurrentState?
    let source: SourceType?
    let gmailThreadID: String?
    let gmailThreadAction: GmailThreadAction?
    let traceID: String
    let createdAt: String

    var canMutateGmailThread: Bool {
        gmailThreadID != nil && gmailThreadAction != nil
    }

    enum CodingKeys: String, CodingKey {
        case id
        case entityID = "entity_id"
        case userID = "user_id"
        case needType = "need_type"
        case actionType = "action_type"
        case effortLevel = "effort_level"
        case timingBand = "timing_band"
        case actionConfidence = "action_confidence"
        case primaryAction = "primary_action"
        case fallbackAction = "fallback_action"
        case title
        case whyThisIsHere = "why_this_is_here"
        case detail
        case dueAt = "due_at"
        case importanceLevel = "importance_level"
        case lifecycleState = "lifecycle_state"
        case currentState = "current_state"
        case source
        case gmailThreadID = "gmail_thread_id"
        case gmailThreadAction = "gmail_thread_action"
        case traceID = "trace_id"
        case createdAt = "created_at"
    }
}

public enum MailboxLabel: String, Codable, Equatable, Hashable {
    case inbox
    case sent
    case drafts
    case spam
    case trash
    case archive
    case all
}

public struct GmailThreadUpdate: Codable, Equatable, Identifiable, Hashable {
    public var id: String { sourceRecordID }

    let sourceRecordID: String
    let receivedAt: String
    let subject: String?
    let sender: String?
    let summary: String?

    enum CodingKeys: String, CodingKey {
        case sourceRecordID = "source_record_id"
        case receivedAt = "received_at"
        case subject
        case sender
        case summary
    }
}

public struct GmailThreadChildRow: Codable, Equatable, Identifiable, Hashable {
    public var id: String { messageID }

    let messageID: String
    let gmailThreadID: String?
    let sender: String?
    let subject: String?
    var aiTitle: String? = nil
    let snippet: String?
    let receivedAt: String
    let labelIDs: [String]
    let labels: [String]
    let unread: Bool

    var displaySender: String {
        cleanSender(sender ?? "Unknown")
    }

    var displayTitle: String {
        aiTitle ?? subject ?? snippet ?? "Untitled"
    }

    var isUnread: Bool {
        unread || labelIDs.contains("UNREAD") || labels.contains("UNREAD")
    }

    enum CodingKeys: String, CodingKey {
        case messageID = "message_id"
        case gmailThreadID = "gmail_thread_id"
        case sender
        case subject
        case aiTitle = "ai_title"
        case snippet
        case receivedAt = "received_at"
        case labelIDs = "label_ids"
        case labels
        case unread
    }

    private func cleanSender(_ value: String) -> String {
        value.split(separator: "<", maxSplits: 1).first.map { String($0).trimmingCharacters(in: .whitespacesAndNewlines) } ?? value
    }
}

public struct GmailThreadRow: Codable, Equatable, Identifiable, Hashable {
    public var id: String { threadID }

    let threadID: String
    let entityID: String?
    let title: String?
    let href: String?
    let latestSourceRecordID: String
    let latestReceivedAt: String
    let latestMessageAt: String?
    let latestSubject: String?
    let latestSender: String?
    let sender: String?
    let participants: [String]
    let messageCount: Int
    let summary: String?
    var aiGroupID: String? = nil
    var aiTitle: String? = nil
    var aiSummary: String? = nil
    let snippet: String?
    let labelIDs: [String]
    let labels: [String]
    let unread: Bool
    let actionNeeded: Bool
    let actionType: String?
    let actionTypeKey: String?
    let priority: Int?
    let dashboardVisible: Bool?
    let currentState: EntityCurrentState?
    let lifecycleState: String?
    let outcomeType: String?
    let lifecycleUpdates: [GmailThreadUpdate]
    var children: [GmailThreadChildRow]? = nil
    let enrichmentStatus: String?
    var presentationStatus: String? = nil
    var pendingAction: GmailThreadAction? = nil

    var displaySender: String {
        cleanSender(sender ?? latestSender ?? participants.first ?? "Unknown")
    }

    var displayTitle: String {
        if presentationStatus == "ai_pending", aiTitle == nil {
            return "Building title... \(title ?? latestSubject ?? summary ?? snippet ?? "Email")"
        }
        return aiTitle ?? title ?? latestSubject ?? summary ?? snippet ?? "Untitled"
    }

    var displaySummary: String? {
        aiSummary ?? summary ?? snippet
    }

    var isGrouped: Bool {
        messageCount > 1 || lifecycleUpdates.count > 1 || childRows.count > 1
    }

    var childRows: [GmailThreadChildRow] {
        children ?? []
    }

    var isUnread: Bool {
        unread || labelIDs.contains("UNREAD") || labels.contains("UNREAD")
    }

    enum CodingKeys: String, CodingKey {
        case threadID = "thread_id"
        case entityID = "entity_id"
        case title
        case href
        case latestSourceRecordID = "latest_source_record_id"
        case latestReceivedAt = "latest_received_at"
        case latestMessageAt = "latest_message_at"
        case latestSubject = "latest_subject"
        case latestSender = "latest_sender"
        case sender
        case participants
        case messageCount = "message_count"
        case summary
        case aiGroupID = "ai_group_id"
        case aiTitle = "ai_title"
        case aiSummary = "ai_summary"
        case snippet
        case labelIDs = "label_ids"
        case labels
        case unread
        case actionNeeded = "action_needed"
        case actionType = "action_type"
        case actionTypeKey = "action_type_key"
        case priority
        case dashboardVisible = "dashboard_visible"
        case currentState = "current_state"
        case lifecycleState = "lifecycle_state"
        case outcomeType = "outcome_type"
        case lifecycleUpdates = "lifecycle_updates"
        case children
        case enrichmentStatus = "enrichment_status"
        case presentationStatus = "presentation_status"
        case pendingAction = "pending_action"
    }

    private static func cleanSender(_ value: String) -> String {
        value.split(separator: "<", maxSplits: 1).first.map { String($0).trimmingCharacters(in: .whitespacesAndNewlines) } ?? value
    }

    private func cleanSender(_ value: String) -> String {
        Self.cleanSender(value)
    }
}

public struct GmailThreadSection: Codable, Equatable, Identifiable {
    public let id: String
    let title: String
    let rows: [GmailThreadRow]
}

public struct MailboxResponse: Codable, Equatable {
    let label: MailboxLabel
    let totalThreads: Int
    let nextCursor: String?
    let loadedThreads: Int?
    let windowDays: Int?
    let sections: [GmailThreadSection]
    let readyCount: Int?
    let pendingCount: Int?
    let oldestImportedAt: String?
    let fullImportRunning: Bool?
    let fullImportCompleted: Bool?

    var isEmpty: Bool {
        totalThreads == 0 || sections.allSatisfy { $0.rows.isEmpty }
    }

    init(
        label: MailboxLabel,
        totalThreads: Int,
        nextCursor: String? = nil,
        loadedThreads: Int? = nil,
        windowDays: Int? = nil,
        sections: [GmailThreadSection] = [],
        readyCount: Int? = nil,
        pendingCount: Int? = nil,
        oldestImportedAt: String? = nil,
        fullImportRunning: Bool? = nil,
        fullImportCompleted: Bool? = nil
    ) {
        self.label = label
        self.totalThreads = totalThreads
        self.nextCursor = nextCursor
        self.loadedThreads = loadedThreads
        self.windowDays = windowDays
        self.sections = sections
        self.readyCount = readyCount
        self.pendingCount = pendingCount
        self.oldestImportedAt = oldestImportedAt
        self.fullImportRunning = fullImportRunning
        self.fullImportCompleted = fullImportCompleted
    }

    enum CodingKeys: String, CodingKey {
        case label
        case totalThreads = "total_threads"
        case nextCursor = "next_cursor"
        case loadedThreads = "loaded_threads"
        case windowDays = "window_days"
        case sections
        case readyCount = "ready_count"
        case pendingCount = "pending_count"
        case oldestImportedAt = "oldest_imported_at"
        case fullImportRunning = "full_import_running"
        case fullImportCompleted = "full_import_completed"
    }
}

public struct MailboxSyncStateResponse: Codable, Equatable {
    let connected: Bool
    let lastHistoryID: String?
    let lastFullSyncAt: String?
    let watchExpirationAt: String?
    let lastSyncStartedAt: String?
    let lastSyncCompletedAt: String?
    let lastSyncError: String?
    var watchStatus: String? = nil
    var lastDeltaSyncAt: String? = nil
    var lastPollAt: String? = nil
    var pollerOnline: Bool? = nil
    var mailboxRevision: String? = nil
    let totalThreads: Int
    var fullImportRunning: Bool? = nil
    var fullImportCompleted: Bool? = nil
    var fullImportCompletedAt: String? = nil
    var pendingActionCount: Int? = nil
    var lastActionSyncAt: String? = nil
    var lastActionError: String? = nil
    var lastAIError: String? = nil

    enum CodingKeys: String, CodingKey {
        case connected
        case lastHistoryID = "last_history_id"
        case lastFullSyncAt = "last_full_sync_at"
        case watchExpirationAt = "watch_expiration_at"
        case lastSyncStartedAt = "last_sync_started_at"
        case lastSyncCompletedAt = "last_sync_completed_at"
        case lastSyncError = "last_sync_error"
        case watchStatus = "watch_status"
        case lastDeltaSyncAt = "last_delta_sync_at"
        case lastPollAt = "last_poll_at"
        case pollerOnline = "poller_online"
        case mailboxRevision = "mailbox_revision"
        case totalThreads = "total_threads"
        case fullImportRunning = "full_import_running"
        case fullImportCompleted = "full_import_completed"
        case fullImportCompletedAt = "full_import_completed_at"
        case pendingActionCount = "pending_action_count"
        case lastActionSyncAt = "last_action_sync_at"
        case lastActionError = "last_action_error"
        case lastAIError = "last_ai_error"
    }
}

public struct MailboxSyncTriggerResponse: Codable, Equatable {
    let status: String
    let state: MailboxSyncStateResponse
    let jobID: String?
    let queuedAt: String?

    enum CodingKeys: String, CodingKey {
        case status
        case state
        case jobID = "job_id"
        case queuedAt = "queued_at"
    }
}

public struct GmailThreadMutationResponse: Codable, Equatable {
    let threadID: String
    let action: GmailThreadAction

    enum CodingKeys: String, CodingKey {
        case threadID = "thread_id"
        case action
    }
}

public struct QueuedThreadActionRequest: Codable, Equatable {
    let clientActionID: String
    let mailboxThreadID: String
    let targetMessageID: String?
    let action: GmailThreadAction
    let createdAt: String

    enum CodingKeys: String, CodingKey {
        case clientActionID = "client_action_id"
        case mailboxThreadID = "mailbox_thread_id"
        case targetMessageID = "target_message_id"
        case action
        case createdAt = "created_at"
    }
}

public enum QueuedThreadActionState: String, Codable, Equatable {
    case queued
    case applying
    case applied
    case failed
}

public struct QueuedThreadActionResponse: Codable, Equatable {
    let clientActionID: String
    let serverActionID: String
    let mailboxThreadID: String
    let targetMessageID: String?
    let action: GmailThreadAction
    let state: QueuedThreadActionState
    let queuedAt: String
    let appliedAt: String?
    let error: String?

    enum CodingKeys: String, CodingKey {
        case clientActionID = "client_action_id"
        case serverActionID = "server_action_id"
        case mailboxThreadID = "mailbox_thread_id"
        case targetMessageID = "target_message_id"
        case action
        case state
        case queuedAt = "queued_at"
        case appliedAt = "applied_at"
        case error
    }
}

public struct MailComposeRequest: Codable, Equatable {
    let clientSendID: String
    let to: [String]
    let cc: [String]
    let bcc: [String]
    let subject: String
    let bodyText: String
    let bodyHTML: String?
    let createdAt: String

    enum CodingKeys: String, CodingKey {
        case clientSendID = "client_send_id"
        case to
        case cc
        case bcc
        case subject
        case bodyText = "body_text"
        case bodyHTML = "body_html"
        case createdAt = "created_at"
    }
}

public struct MailReplyRequest: Codable, Equatable {
    let clientSendID: String
    let cc: [String]
    let bcc: [String]
    let bodyText: String
    let bodyHTML: String?
    let createdAt: String

    enum CodingKeys: String, CodingKey {
        case clientSendID = "client_send_id"
        case cc
        case bcc
        case bodyText = "body_text"
        case bodyHTML = "body_html"
        case createdAt = "created_at"
    }
}

public struct MailSendResponse: Codable, Equatable {
    let clientSendID: String
    let serverSendID: String?
    let mailboxThreadID: String?
    let gmailThreadID: String?
    let gmailMessageID: String?
    let state: MailSendState
    let queuedAt: String?
    let sentAt: String?
    let error: String?
    let reauthURL: String?

    enum CodingKeys: String, CodingKey {
        case clientSendID = "client_send_id"
        case serverSendID = "server_send_id"
        case mailboxThreadID = "mailbox_thread_id"
        case gmailThreadID = "gmail_thread_id"
        case gmailMessageID = "gmail_message_id"
        case state
        case queuedAt = "queued_at"
        case sentAt = "sent_at"
        case error
        case reauthURL = "reauth_url"
    }
}

public struct TaskCreateRequest: Codable, Equatable {
    let title: String
    let notes: String?
    let section: String?
    let dueAt: String?

    enum CodingKeys: String, CodingKey {
        case title
        case notes
        case section
        case dueAt = "due_at"
    }
}

public struct TaskUpdateRequest: Codable, Equatable {
    let title: String?
    let notes: String?
    let section: String?
    let dueAt: String?
    let status: String?

    enum CodingKeys: String, CodingKey {
        case title
        case notes
        case section
        case dueAt = "due_at"
        case status
    }
}

public struct TaskResponse: Codable, Equatable {
    let id: String
    let userID: String
    let entityID: String
    let title: String
    let notes: String?
    let section: String
    let dueAt: String?
    let status: String
    let createdAt: String
    let updatedAt: String

    enum CodingKeys: String, CodingKey {
        case id
        case userID = "user_id"
        case entityID = "entity_id"
        case title
        case notes
        case section
        case dueAt = "due_at"
        case status
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }
}

public struct EntityOutcomeRequest: Codable, Equatable {
    let note: String?
}

public struct EntitySnoozeRequest: Codable, Equatable {
    let snoozeUntil: String
    let note: String?

    enum CodingKeys: String, CodingKey {
        case snoozeUntil = "snooze_until"
        case note
    }
}

public struct EntityOutcomeResponse: Codable, Equatable {
    let id: String
    let userID: String
    let entityID: String
    let outcomeType: String
    let snoozeUntil: String?
    let note: String?
    let createdAt: String

    enum CodingKeys: String, CodingKey {
        case id
        case userID = "user_id"
        case entityID = "entity_id"
        case outcomeType = "outcome_type"
        case snoozeUntil = "snooze_until"
        case note
        case createdAt = "created_at"
    }
}

public struct GmailDraftRequest: Codable, Equatable {
    let to: String
    let cc: String?
    let bcc: String?
    let subject: String
    let body: String
    let entityID: String?
    let threadID: String?

    enum CodingKeys: String, CodingKey {
        case to
        case cc
        case bcc
        case subject
        case body
        case entityID = "entity_id"
        case threadID = "thread_id"
    }
}

public struct GmailDraftResponse: Codable, Equatable {
    let id: String
    let userID: String
    let entityID: String?
    let gmailDraftID: String
    let gmailMessageID: String?
    let threadID: String?
    let to: String
    let cc: String?
    let bcc: String?
    let subject: String
    let body: String
    let status: String
    let createdAt: String
    let updatedAt: String

    enum CodingKeys: String, CodingKey {
        case id
        case userID = "user_id"
        case entityID = "entity_id"
        case gmailDraftID = "gmail_draft_id"
        case gmailMessageID = "gmail_message_id"
        case threadID = "thread_id"
        case to
        case cc
        case bcc
        case subject
        case body
        case status
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }
}

public struct ThreadReaderResponse: Codable, Equatable {
    let entityID: String
    let userID: String
    let source: SourceType?
    let gmailThreadID: String?
    let subject: String?
    let title: String?
    let summary: String?
    let totalMessages: Int
    let limit: Int
    let offset: Int
    let hasMore: Bool
    let messages: [ThreadMessage]

    enum CodingKeys: String, CodingKey {
        case entityID = "entity_id"
        case userID = "user_id"
        case source
        case gmailThreadID = "gmail_thread_id"
        case subject
        case title
        case summary
        case totalMessages = "total_messages"
        case limit
        case offset
        case hasMore = "has_more"
        case messages
    }

    init(
        entityID: String,
        userID: String,
        source: SourceType?,
        gmailThreadID: String?,
        subject: String?,
        title: String? = nil,
        summary: String? = nil,
        totalMessages: Int = 0,
        limit: Int = 50,
        offset: Int = 0,
        hasMore: Bool = false,
        messages: [ThreadMessage]
    ) {
        self.entityID = entityID
        self.userID = userID
        self.source = source
        self.gmailThreadID = gmailThreadID
        self.subject = subject
        self.title = title
        self.summary = summary
        self.totalMessages = totalMessages
        self.limit = limit
        self.offset = offset
        self.hasMore = hasMore
        self.messages = messages
    }
}

public struct ThreadMessage: Codable, Equatable, Identifiable {
    public let id: String
    let source: SourceType
    let threadID: String?
    let fromAddress: String?
    let to: String?
    let cc: String?
    let bcc: String?
    let subject: String?
    let body: String
    let htmlBody: String?
    let htmlRenderDocument: String?
    let reader: ThreadMessageReader?
    let snippet: String?
    let labelIDs: [String]
    let receivedAt: String

    enum CodingKeys: String, CodingKey {
        case id
        case source
        case threadID = "thread_id"
        case fromAddress = "from_address"
        case to
        case cc
        case bcc
        case subject
        case body
        case htmlBody = "html_body"
        case htmlRenderDocument = "html_render_document"
        case reader
        case snippet
        case labelIDs = "label_ids"
        case receivedAt = "received_at"
    }

    init(
        id: String,
        source: SourceType,
        threadID: String?,
        fromAddress: String?,
        to: String?,
        cc: String?,
        bcc: String?,
        subject: String?,
        body: String,
        htmlBody: String?,
        htmlRenderDocument: String?,
        reader: ThreadMessageReader? = nil,
        snippet: String?,
        labelIDs: [String],
        receivedAt: String
    ) {
        self.id = id
        self.source = source
        self.threadID = threadID
        self.fromAddress = fromAddress
        self.to = to
        self.cc = cc
        self.bcc = bcc
        self.subject = subject
        self.body = body
        self.htmlBody = htmlBody
        self.htmlRenderDocument = htmlRenderDocument
        self.reader = reader
        self.snippet = snippet
        self.labelIDs = labelIDs
        self.receivedAt = receivedAt
    }
}

public struct ThreadMessageReader: Codable, Equatable {
    let primaryText: String
    let markers: [ThreadMessageReaderMarker]
    let signatureText: String?
    let quotedText: String?
    let footerText: String?
    let originalHTMLAvailable: Bool

    enum CodingKeys: String, CodingKey {
        case primaryText = "primary_text"
        case markers
        case signatureText = "signature_text"
        case quotedText = "quoted_text"
        case footerText = "footer_text"
        case originalHTMLAvailable = "original_html_available"
    }
}

public struct ThreadMessageReaderMarker: Codable, Equatable, Hashable, Identifiable {
    let kind: String
    let label: String
    let text: String

    public var id: String {
        "\(kind)::\(label)::\(text)"
    }
}

public struct TraceReplayResponse: Codable, Equatable {
    let entityID: String
    let sourceRecordIDs: [String]
    let items: [TraceRecord]

    enum CodingKeys: String, CodingKey {
        case entityID = "entity_id"
        case sourceRecordIDs = "source_record_ids"
        case items
    }
}

public struct TraceRecord: Codable, Equatable, Identifiable {
    public let id: String
    let traceID: String
    let entityID: String?
    let sourceRecordID: String?
    let userID: String
    let stage: String
    let input: [String: JSONValue]
    let output: [String: JSONValue]
    let createdAt: String

    enum CodingKeys: String, CodingKey {
        case id
        case traceID = "trace_id"
        case entityID = "entity_id"
        case sourceRecordID = "source_record_id"
        case userID = "user_id"
        case stage
        case input
        case output
        case createdAt = "created_at"
    }
}

public enum JSONValue: Codable, Equatable {
    case string(String)
    case number(Double)
    case bool(Bool)
    case object([String: JSONValue])
    case array([JSONValue])
    case null

    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()

        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode(Bool.self) {
            self = .bool(value)
        } else if let value = try? container.decode(Double.self) {
            self = .number(value)
        } else if let value = try? container.decode(String.self) {
            self = .string(value)
        } else if let value = try? container.decode([String: JSONValue].self) {
            self = .object(value)
        } else if let value = try? container.decode([JSONValue].self) {
            self = .array(value)
        } else {
            throw DecodingError.dataCorruptedError(in: container, debugDescription: "Unsupported JSON value")
        }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()

        switch self {
        case .string(let value):
            try container.encode(value)
        case .number(let value):
            try container.encode(value)
        case .bool(let value):
            try container.encode(value)
        case .object(let value):
            try container.encode(value)
        case .array(let value):
            try container.encode(value)
        case .null:
            try container.encodeNil()
        }
    }

    var displayString: String {
        switch self {
        case .string(let value):
            return value
        case .number(let value):
            return String(value)
        case .bool(let value):
            return value ? "true" : "false"
        case .object(let value):
            return "\(value.count) fields"
        case .array(let value):
            return "\(value.count) items"
        case .null:
            return "null"
        }
    }
}

public extension JSONDecoder {
    static let backend: JSONDecoder = {
        JSONDecoder()
    }()
}

public extension JSONEncoder {
    static let backend: JSONEncoder = {
        JSONEncoder()
    }()
}
