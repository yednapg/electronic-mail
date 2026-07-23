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
    case markUnread = "mark_unread"
    case moveTrash = "move_trash"
    case restoreTrash = "restore_trash"
    case markSpam = "mark_spam"
    case notSpam = "not_spam"
    case star
    case unstar
    case deleteForever = "delete_forever"
}

public enum MailSendState: String, Codable, Equatable {
    case queued
    case sending
    case sent
    case failed
    case reauthRequired = "reauth_required"
}

public enum MailComposerMode: String, Codable, Equatable {
    case compose
    case reply
    case replyAll = "reply_all"
    case forward
    case draft
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
    let handoffID: String
    let codeVerifier: String

    init(loginCode: String, handoffID: String, codeVerifier: String) {
        self.loginCode = loginCode
        self.handoffID = handoffID
        self.codeVerifier = codeVerifier
    }

    enum CodingKeys: String, CodingKey {
        case loginCode = "login_code"
        case handoffID = "handoff_id"
        case codeVerifier = "code_verifier"
    }
}

public struct MobileAuthenticationGrant: Equatable {
    public let loginCode: String
    public let handoffID: String
    public let codeVerifier: String

    public init(loginCode: String, handoffID: String, codeVerifier: String) {
        self.loginCode = loginCode
        self.handoffID = handoffID
        self.codeVerifier = codeVerifier
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

public enum MailboxLabel: String, Codable, Equatable, Hashable, CaseIterable {
    case inbox
    case sent
    case drafts
    case spam
    case trash
    case archive
    case all
    case starred
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
        subject ?? snippet ?? "Untitled"
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
        EmailAddressDisplayFormatter.displayName(from: value)
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
    var hasAttachments: Bool? = nil
    var attachmentCount: Int? = nil
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
        title ?? latestSubject ?? snippet ?? "Untitled"
    }

    var displaySummary: String? {
        snippet
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
        case hasAttachments = "has_attachments"
        case attachmentCount = "attachment_count"
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
        EmailAddressDisplayFormatter.displayName(from: value)
    }

    private func cleanSender(_ value: String) -> String {
        Self.cleanSender(value)
    }
}

private enum EmailAddressDisplayFormatter {
    static func displayName(from rawValue: String) -> String {
        let value = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else {
            return rawValue
        }

        if let angleStart = value.firstIndex(of: "<"),
           let angleEnd = value[angleStart...].firstIndex(of: ">") {
            let name = cleanDisplayName(String(value[..<angleStart]))
            if !name.isEmpty {
                return name
            }

            let email = String(value[value.index(after: angleStart)..<angleEnd])
            return displayEmailAddress(email)
        }

        let unquotedValue = cleanDisplayName(value)
        if isEmailAddress(unquotedValue) {
            return displayEmailAddress(unquotedValue)
        }

        return unquotedValue.isEmpty ? value : unquotedValue
    }

    private static func cleanDisplayName(_ value: String) -> String {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        let unquoted: String
        if trimmed.count >= 2,
           trimmed.first == "\"",
           trimmed.last == "\"" {
            unquoted = String(trimmed.dropFirst().dropLast())
        } else {
            unquoted = trimmed
        }

        return unquoted
            .replacingOccurrences(of: "\\\"", with: "\"")
            .split(whereSeparator: { $0.isWhitespace })
            .joined(separator: " ")
    }

    private static func displayEmailAddress(_ value: String) -> String {
        let email = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let atIndex = email.firstIndex(of: "@") else {
            return cleanDisplayName(email)
        }

        let localPart = String(email[..<atIndex])
        let domain = String(email[email.index(after: atIndex)...]).lowercased()
        if shouldPreserveRawEmail(localPart: localPart, domain: domain) {
            return email
        }

        let localDisplay = displayLocalPart(localPart)
        guard !localDisplay.isEmpty, !domain.isEmpty else {
            return email
        }

        return "\(localDisplay) - \(domain)"
    }

    private static func shouldPreserveRawEmail(localPart: String, domain: String) -> Bool {
        let freeMailDomains = ["gmail.com", "googlemail.com", "icloud.com", "me.com", "outlook.com", "hotmail.com", "yahoo.com"]
        guard freeMailDomains.contains(domain) else {
            return false
        }

        let normalized = localPart.lowercased()
        let servicePrefixes = ["no-reply", "noreply", "notification", "notifications", "support", "alerts", "team", "update", "updates"]
        if servicePrefixes.contains(where: { normalized == $0 || normalized.hasPrefix("\($0)+") || normalized.hasPrefix("\($0).") }) {
            return false
        }

        return true
    }

    private static func displayLocalPart(_ value: String) -> String {
        value
            .split { character in
                character == "." || character == "_" || character == "-" || character == "+"
            }
            .map { segment in
                let text = String(segment).lowercased()
                guard let first = text.first else {
                    return text
                }
                return String(first).uppercased() + String(text.dropFirst())
            }
            .joined(separator: " ")
    }

    private static func isEmailAddress(_ value: String) -> Bool {
        let parts = value.split(separator: "@", maxSplits: 1)
        guard parts.count == 2 else {
            return false
        }

        return !parts[0].isEmpty && parts[1].contains(".")
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
    var unreadThreads: Int? = nil
    let nextCursor: String?
    let loadedThreads: Int?
    let windowDays: Int?
    let sections: [GmailThreadSection]
    let readyCount: Int?
    let pendingCount: Int?
    let mailboxRevision: String?
    let generatedAt: String?
    let oldestImportedAt: String?
    let fullImportRunning: Bool?
    let fullImportCompleted: Bool?

    var isEmpty: Bool {
        totalThreads == 0 || sections.allSatisfy { $0.rows.isEmpty }
    }

    init(
        label: MailboxLabel,
        totalThreads: Int,
        unreadThreads: Int? = nil,
        nextCursor: String? = nil,
        loadedThreads: Int? = nil,
        windowDays: Int? = nil,
        sections: [GmailThreadSection] = [],
        readyCount: Int? = nil,
        pendingCount: Int? = nil,
        mailboxRevision: String? = nil,
        generatedAt: String? = nil,
        oldestImportedAt: String? = nil,
        fullImportRunning: Bool? = nil,
        fullImportCompleted: Bool? = nil
    ) {
        self.label = label
        self.totalThreads = totalThreads
        self.unreadThreads = unreadThreads
        self.nextCursor = nextCursor
        self.loadedThreads = loadedThreads
        self.windowDays = windowDays
        self.sections = sections
        self.readyCount = readyCount
        self.pendingCount = pendingCount
        self.mailboxRevision = mailboxRevision
        self.generatedAt = generatedAt
        self.oldestImportedAt = oldestImportedAt
        self.fullImportRunning = fullImportRunning
        self.fullImportCompleted = fullImportCompleted
    }

    enum CodingKeys: String, CodingKey {
        case label
        case totalThreads = "total_threads"
        case unreadThreads = "unread_threads"
        case nextCursor = "next_cursor"
        case loadedThreads = "loaded_threads"
        case windowDays = "window_days"
        case sections
        case readyCount = "ready_count"
        case pendingCount = "pending_count"
        case mailboxRevision = "mailbox_revision"
        case generatedAt = "generated_at"
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
    let attachments: [MailAttachmentUpload]
    let createdAt: String

    init(
        clientSendID: String,
        to: [String],
        cc: [String],
        bcc: [String],
        subject: String,
        bodyText: String,
        bodyHTML: String?,
        attachments: [MailAttachmentUpload] = [],
        createdAt: String
    ) {
        self.clientSendID = clientSendID
        self.to = to
        self.cc = cc
        self.bcc = bcc
        self.subject = subject
        self.bodyText = bodyText
        self.bodyHTML = bodyHTML
        self.attachments = attachments
        self.createdAt = createdAt
    }

    enum CodingKeys: String, CodingKey {
        case clientSendID = "client_send_id"
        case to
        case cc
        case bcc
        case subject
        case bodyText = "body_text"
        case bodyHTML = "body_html"
        case attachments
        case createdAt = "created_at"
    }
}

public struct MailAttachmentUpload: Codable, Equatable {
    let filename: String
    let mimeType: String
    let dataBase64: String

    enum CodingKeys: String, CodingKey {
        case filename
        case mimeType = "mime_type"
        case dataBase64 = "data_base64"
    }
}

public enum MailReplyMode: String, Codable, Equatable {
    case reply
    case replyAll = "reply_all"
    case forward
}

public struct MailReplyRequest: Codable, Equatable {
    let clientSendID: String
    let sourceMessageID: String?
    let mode: MailReplyMode
    let to: [String]
    let cc: [String]
    let bcc: [String]
    let subject: String?
    let bodyText: String
    let bodyHTML: String?
    let attachments: [MailAttachmentUpload]
    let includeQuotedOriginal: Bool
    let includeOriginalAttachments: Bool
    let createdAt: String

    init(
        clientSendID: String,
        sourceMessageID: String? = nil,
        mode: MailReplyMode = .reply,
        to: [String] = [],
        cc: [String],
        bcc: [String],
        subject: String? = nil,
        bodyText: String,
        bodyHTML: String?,
        attachments: [MailAttachmentUpload] = [],
        includeQuotedOriginal: Bool = true,
        includeOriginalAttachments: Bool = false,
        createdAt: String
    ) {
        self.clientSendID = clientSendID
        self.sourceMessageID = sourceMessageID
        self.mode = mode
        self.to = to
        self.cc = cc
        self.bcc = bcc
        self.subject = subject
        self.bodyText = bodyText
        self.bodyHTML = bodyHTML
        self.attachments = attachments
        self.includeQuotedOriginal = includeQuotedOriginal
        self.includeOriginalAttachments = includeOriginalAttachments
        self.createdAt = createdAt
    }

    enum CodingKeys: String, CodingKey {
        case clientSendID = "client_send_id"
        case sourceMessageID = "source_message_id"
        case mode
        case to
        case cc
        case bcc
        case subject
        case bodyText = "body_text"
        case bodyHTML = "body_html"
        case attachments
        case includeQuotedOriginal = "include_quoted_original"
        case includeOriginalAttachments = "include_original_attachments"
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

public typealias MailOutboxResponse = [MailSendResponse]

enum DurableSendConfirmationDecision: Equatable {
    case confirmedSent
    case poll(serverSendID: String)
    case preserveForRetry(message: String)
}

enum DurableSendConfirmationPolicy {
    // A short, bounded sequence keeps confirmation responsive without turning a
    // delayed worker into an unbounded task owned by the composer view.
    static let pollDelayNanoseconds: [UInt64] = [
        250_000_000,
        500_000_000,
        1_000_000_000,
        2_000_000_000,
        3_000_000_000,
    ]

    static func decision(for response: MailSendResponse) -> DurableSendConfirmationDecision {
        switch response.state {
        case .sent:
            return .confirmedSent
        case .queued, .sending:
            guard let serverSendID = normalized(response.serverSendID) else {
                return .preserveForRetry(
                    message: "Delivery has not been confirmed. Your message is preserved; retry safely."
                )
            }
            return .poll(serverSendID: serverSendID)
        case .failed:
            return .preserveForRetry(message: response.error ?? "Send failed. You can retry safely.")
        case .reauthRequired:
            return .preserveForRetry(
                message: response.error ?? "Google needs permission to send mail."
            )
        }
    }

    static func matches(
        _ response: MailSendResponse,
        expectedClientSendID: String,
        expectedServerSendID: String? = nil
    ) -> Bool {
        guard response.clientSendID == expectedClientSendID else {
            return false
        }
        guard let expectedServerSendID else {
            return true
        }
        return normalized(response.serverSendID) == expectedServerSendID
    }

    private static func normalized(_ value: String?) -> String? {
        guard let value else { return nil }
        let normalized = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return normalized.isEmpty ? nil : normalized
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

public struct MailDraftSaveRequest: Codable, Equatable {
    let clientDraftID: String
    let gmailDraftID: String?
    let gmailThreadID: String?
    let to: [String]
    let cc: [String]
    let bcc: [String]
    let subject: String
    let bodyText: String
    let bodyHTML: String?
    let attachments: [MailAttachmentUpload]?
    let retainedAttachmentIDs: [String]?
    let responseMode: MailReplyMode?
    let mailboxThreadID: String?
    let sourceMessageID: String?
    let includeQuotedOriginal: Bool
    let includeOriginalAttachments: Bool
    let createdAt: String

    init(
        clientDraftID: String,
        gmailDraftID: String?,
        gmailThreadID: String?,
        to: [String],
        cc: [String],
        bcc: [String],
        subject: String,
        bodyText: String,
        bodyHTML: String?,
        attachments: [MailAttachmentUpload]?,
        retainedAttachmentIDs: [String]?,
        responseMode: MailReplyMode? = nil,
        mailboxThreadID: String? = nil,
        sourceMessageID: String? = nil,
        includeQuotedOriginal: Bool = true,
        includeOriginalAttachments: Bool = true,
        createdAt: String
    ) {
        self.clientDraftID = clientDraftID
        self.gmailDraftID = gmailDraftID
        self.gmailThreadID = gmailThreadID
        self.to = to
        self.cc = cc
        self.bcc = bcc
        self.subject = subject
        self.bodyText = bodyText
        self.bodyHTML = bodyHTML
        self.attachments = attachments
        self.retainedAttachmentIDs = retainedAttachmentIDs
        self.responseMode = responseMode
        self.mailboxThreadID = mailboxThreadID
        self.sourceMessageID = sourceMessageID
        self.includeQuotedOriginal = includeQuotedOriginal
        self.includeOriginalAttachments = includeOriginalAttachments
        self.createdAt = createdAt
    }

    enum CodingKeys: String, CodingKey {
        case clientDraftID = "client_draft_id"
        case gmailDraftID = "gmail_draft_id"
        case gmailThreadID = "gmail_thread_id"
        case to
        case cc
        case bcc
        case subject
        case bodyText = "body_text"
        case bodyHTML = "body_html"
        case attachments
        case retainedAttachmentIDs = "retained_attachment_ids"
        case responseMode = "response_mode"
        case mailboxThreadID = "mailbox_thread_id"
        case sourceMessageID = "source_message_id"
        case includeQuotedOriginal = "include_quoted_original"
        case includeOriginalAttachments = "include_original_attachments"
        case createdAt = "created_at"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        clientDraftID = try container.decode(String.self, forKey: .clientDraftID)
        gmailDraftID = try container.decodeIfPresent(String.self, forKey: .gmailDraftID)
        gmailThreadID = try container.decodeIfPresent(String.self, forKey: .gmailThreadID)
        to = try container.decodeIfPresent([String].self, forKey: .to) ?? []
        cc = try container.decodeIfPresent([String].self, forKey: .cc) ?? []
        bcc = try container.decodeIfPresent([String].self, forKey: .bcc) ?? []
        subject = try container.decodeIfPresent(String.self, forKey: .subject) ?? ""
        bodyText = try container.decodeIfPresent(String.self, forKey: .bodyText) ?? ""
        bodyHTML = try container.decodeIfPresent(String.self, forKey: .bodyHTML)
        attachments = try container.decodeIfPresent([MailAttachmentUpload].self, forKey: .attachments)
        retainedAttachmentIDs = try container.decodeIfPresent([String].self, forKey: .retainedAttachmentIDs)
        responseMode = try container.decodeIfPresent(MailReplyMode.self, forKey: .responseMode)
        mailboxThreadID = try container.decodeIfPresent(String.self, forKey: .mailboxThreadID)
        sourceMessageID = try container.decodeIfPresent(String.self, forKey: .sourceMessageID)
        includeQuotedOriginal = try container.decodeIfPresent(Bool.self, forKey: .includeQuotedOriginal) ?? true
        includeOriginalAttachments = try container.decodeIfPresent(Bool.self, forKey: .includeOriginalAttachments) ?? true
        createdAt = try container.decode(String.self, forKey: .createdAt)
    }
}

enum MailComposerPolicy {
    static let maximumAttachmentBytes = 10 * 1_024 * 1_024
    static let maximumTotalAttachmentBytes = 18 * 1_024 * 1_024

    static func hasDraftContent(textFields: [String], attachmentCount: Int) -> Bool {
        attachmentCount > 0 || textFields.contains {
            !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        }
    }

    static func requiresEmptySubjectConfirmation(_ subject: String) -> Bool {
        subject.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    static func acceptsAttachment(byteCount: Int, currentTotalBytes: Int) -> Bool {
        byteCount <= maximumAttachmentBytes
            && currentTotalBytes + byteCount <= maximumTotalAttachmentBytes
    }

    static func shouldApplyDraftSaveResponse(state: MailDraftState) -> Bool {
        state == .saved
    }

    static func shouldClearUnchangedResponseRecovery(
        force: Bool,
        hasUnresolvedSendAttempt: Bool,
        mode: MailComposerMode,
        restoredFromRecovery: Bool,
        responseIsUnchanged: Bool
    ) -> Bool {
        !force
            && !hasUnresolvedSendAttempt
            && responseMode(for: mode) != nil
            && !restoredFromRecovery
            && responseIsUnchanged
    }

    static func responseMode(for mode: MailComposerMode) -> MailReplyMode? {
        switch mode {
        case .reply:
            return .reply
        case .replyAll:
            return .replyAll
        case .forward:
            return .forward
        case .compose, .draft:
            return nil
        }
    }

    static func shouldPersistDraft(
        mode: MailComposerMode,
        hasContent: Bool,
        responseChanged: Bool,
        hasGmailDraft: Bool
    ) -> Bool {
        if hasGmailDraft {
            return true
        }
        switch mode {
        case .compose, .draft:
            return hasContent
        case .reply, .replyAll, .forward:
            return responseChanged
        }
    }
}

struct MailReplyPrefillRecipients: Equatable {
    let to: [String]
    let cc: [String]
}

enum MailReplyPrefillPolicy {
    static func recipients(
        mode: MailComposerMode,
        currentUser: String?,
        sender: String?,
        originalTo: [String],
        originalCC: [String]
    ) -> MailReplyPrefillRecipients {
        let current = currentUser?.lowercased()
        let normalizedSender = sender?.trimmingCharacters(in: .whitespacesAndNewlines)
        let senderIsCurrentUser = normalizedSender?.lowercased() == current
        let toRecipients = unique(originalTo)
        let ccRecipients = unique(originalCC)

        if senderIsCurrentUser {
            let nonSelfTo = toRecipients.filter { $0.lowercased() != current }
            switch mode {
            case .reply:
                return MailReplyPrefillRecipients(
                    to: nonSelfTo.isEmpty ? selfFallback(currentUser: currentUser, originalTo: toRecipients, originalCC: ccRecipients) : nonSelfTo,
                    cc: []
                )
            case .replyAll:
                let nonSelfCC = ccRecipients.filter {
                    $0.lowercased() != current && !nonSelfTo.map({ $0.lowercased() }).contains($0.lowercased())
                }
                let fallback = selfFallback(currentUser: currentUser, originalTo: toRecipients, originalCC: ccRecipients)
                return MailReplyPrefillRecipients(to: nonSelfTo.isEmpty ? fallback : nonSelfTo, cc: nonSelfCC)
            case .compose, .forward, .draft:
                return MailReplyPrefillRecipients(to: [], cc: [])
            }
        }

        let primary = normalizedSender.map { [$0] } ?? []
        switch mode {
        case .reply:
            return MailReplyPrefillRecipients(to: primary, cc: [])
        case .replyAll:
            let excluded = Set(([currentUser, normalizedSender].compactMap { $0 }).map { $0.lowercased() })
            let copied = unique(toRecipients + ccRecipients).filter { !excluded.contains($0.lowercased()) }
            return MailReplyPrefillRecipients(to: primary, cc: copied)
        case .compose, .forward, .draft:
            return MailReplyPrefillRecipients(to: [], cc: [])
        }
    }

    private static func selfFallback(currentUser: String?, originalTo: [String], originalCC: [String]) -> [String] {
        guard unique(originalTo + originalCC).allSatisfy({ $0.lowercased() == currentUser?.lowercased() }),
              let currentUser,
              !currentUser.isEmpty else {
            return []
        }
        return [currentUser]
    }

    private static func unique(_ values: [String]) -> [String] {
        var seen = Set<String>()
        return values.filter {
            let value = $0.trimmingCharacters(in: .whitespacesAndNewlines)
            return !value.isEmpty && seen.insert(value.lowercased()).inserted
        }
    }
}

public enum MailDraftState: String, Codable, Equatable {
    case saved
    case deleted
    case sent
    case failed
    case reauthRequired = "reauth_required"
}

public struct MailDraftResponse: Codable, Equatable {
    let clientDraftID: String
    let gmailDraftID: String?
    let gmailMessageID: String?
    let gmailThreadID: String?
    let to: [String]
    let cc: [String]
    let bcc: [String]
    let subject: String
    let bodyText: String
    let bodyHTML: String?
    let attachments: [MailDraftAttachment]
    let state: MailDraftState
    let savedAt: String?
    let error: String?
    let reauthURL: String?

    enum CodingKeys: String, CodingKey {
        case clientDraftID = "client_draft_id"
        case gmailDraftID = "gmail_draft_id"
        case gmailMessageID = "gmail_message_id"
        case gmailThreadID = "gmail_thread_id"
        case to
        case cc
        case bcc
        case subject
        case bodyText = "body_text"
        case bodyHTML = "body_html"
        case attachments
        case state
        case savedAt = "saved_at"
        case error
        case reauthURL = "reauth_url"
    }
}

public struct MailDraftAttachment: Codable, Equatable, Identifiable {
    public var id: String { "\(messageID):\(attachmentID)" }

    let filename: String
    let mimeType: String
    let messageID: String
    let attachmentID: String
    let downloadURL: String

    enum CodingKeys: String, CodingKey {
        case filename
        case mimeType = "mime_type"
        case messageID = "message_id"
        case attachmentID = "attachment_id"
        case downloadURL = "download_url"
    }
}

public struct MailDraftSendRequest: Codable, Equatable {
    let clientSendID: String
    let clientDraftID: String

    enum CodingKeys: String, CodingKey {
        case clientSendID = "client_send_id"
        case clientDraftID = "client_draft_id"
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

public struct ThreadAttachment: Codable, Equatable, Identifiable {
    public let id: String
    let filename: String
    let mimeType: String?
    let size: Int?
    let attachmentID: String
    let partID: String?
    let downloadURL: String?

    enum CodingKeys: String, CodingKey {
        case id
        case filename
        case mimeType = "mime_type"
        case size
        case attachmentID = "attachment_id"
        case partID = "part_id"
        case downloadURL = "download_url"
    }
}

public struct ThreadMessage: Codable, Equatable, Identifiable {
    public let id: String
    let renderRevision: UInt64
    let source: SourceType
    let threadID: String?
    let fromAddress: String?
    let replyTo: String?
    let to: String?
    let cc: String?
    let bcc: String?
    let subject: String?
    let body: String
    let htmlBody: String?
    let htmlRenderDocument: String?
    let reader: ThreadMessageReader?
    let snippet: String?
    let attachments: [ThreadAttachment]
    let labelIDs: [String]
    let receivedAt: String

    enum CodingKeys: String, CodingKey {
        case id
        case source
        case threadID = "thread_id"
        case fromAddress = "from_address"
        case replyTo = "reply_to"
        case to
        case cc
        case bcc
        case subject
        case body
        case htmlBody = "html_body"
        case htmlRenderDocument = "html_render_document"
        case reader
        case snippet
        case attachments
        case labelIDs = "label_ids"
        case receivedAt = "received_at"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let decodedID = try container.decode(String.self, forKey: .id)
        let decodedBody = try container.decode(String.self, forKey: .body)
        let decodedHTMLBody = try container.decodeIfPresent(String.self, forKey: .htmlBody)
        let decodedHTMLRenderDocument = try container.decodeIfPresent(String.self, forKey: .htmlRenderDocument)
        let decodedReader = try container.decodeIfPresent(ThreadMessageReader.self, forKey: .reader)

        id = decodedID
        source = try container.decode(SourceType.self, forKey: .source)
        threadID = try container.decodeIfPresent(String.self, forKey: .threadID)
        fromAddress = try container.decodeIfPresent(String.self, forKey: .fromAddress)
        replyTo = try container.decodeIfPresent(String.self, forKey: .replyTo)
        to = try container.decodeIfPresent(String.self, forKey: .to)
        cc = try container.decodeIfPresent(String.self, forKey: .cc)
        bcc = try container.decodeIfPresent(String.self, forKey: .bcc)
        subject = try container.decodeIfPresent(String.self, forKey: .subject)
        body = decodedBody
        htmlBody = decodedHTMLBody
        htmlRenderDocument = decodedHTMLRenderDocument
        reader = decodedReader
        snippet = try container.decodeIfPresent(String.self, forKey: .snippet)
        attachments = try container.decodeIfPresent([ThreadAttachment].self, forKey: .attachments) ?? []
        labelIDs = try container.decodeIfPresent([String].self, forKey: .labelIDs) ?? []
        receivedAt = try container.decode(String.self, forKey: .receivedAt)
        renderRevision = Self.makeRenderRevision(
            id: decodedID,
            body: decodedBody,
            html: decodedHTMLRenderDocument ?? decodedHTMLBody,
            reader: decodedReader
        )
    }

    init(
        id: String,
        source: SourceType,
        threadID: String?,
        fromAddress: String?,
        replyTo: String? = nil,
        to: String?,
        cc: String?,
        bcc: String?,
        subject: String?,
        body: String,
        htmlBody: String?,
        htmlRenderDocument: String?,
        reader: ThreadMessageReader? = nil,
        snippet: String?,
        attachments: [ThreadAttachment] = [],
        labelIDs: [String],
        receivedAt: String
    ) {
        self.id = id
        self.source = source
        self.threadID = threadID
        self.fromAddress = fromAddress
        self.replyTo = replyTo
        self.to = to
        self.cc = cc
        self.bcc = bcc
        self.subject = subject
        self.body = body
        self.htmlBody = htmlBody
        self.htmlRenderDocument = htmlRenderDocument
        self.reader = reader
        self.snippet = snippet
        self.attachments = attachments
        self.labelIDs = labelIDs
        self.receivedAt = receivedAt
        self.renderRevision = Self.makeRenderRevision(
            id: id,
            body: body,
            html: htmlRenderDocument ?? htmlBody,
            reader: reader
        )
    }

    private static func makeRenderRevision(
        id: String,
        body: String,
        html: String?,
        reader: ThreadMessageReader?
    ) -> UInt64 {
        var hash: UInt64 = 14_695_981_039_346_656_037

        func mix(_ value: String?) {
            guard let value else {
                hash ^= 0
                hash &*= 1_099_511_628_211
                return
            }
            for byte in value.utf8 {
                hash ^= UInt64(byte)
                hash &*= 1_099_511_628_211
            }
            hash ^= 255
            hash &*= 1_099_511_628_211
        }

        mix(id)
        mix(body)
        mix(html)
        mix(reader?.primaryText)
        mix(reader?.renderMode)
        mix(reader?.signatureText)
        mix(reader?.quotedText)
        mix(reader?.footerText)
        mix(reader.map { $0.originalHTMLAvailable ? "html-available" : "html-unavailable" })
        mix(reader?.htmlIsRich.map { $0 ? "html-rich" : "html-plain" })
        mix(reader?.quoteDetected.map { $0 ? "quote-detected" : "quote-absent" })
        for marker in reader?.markers ?? [] {
            mix(marker.kind)
            mix(marker.label)
            mix(marker.text)
        }
        return hash
    }
}

public struct ThreadMessageReader: Codable, Equatable {
    let primaryText: String
    let renderMode: String?
    let markers: [ThreadMessageReaderMarker]
    let signatureText: String?
    let quotedText: String?
    let footerText: String?
    let originalHTMLAvailable: Bool
    let htmlIsRich: Bool?
    let quoteDetected: Bool?

    enum CodingKeys: String, CodingKey {
        case primaryText = "primary_text"
        case renderMode = "render_mode"
        case markers
        case signatureText = "signature_text"
        case quotedText = "quoted_text"
        case footerText = "footer_text"
        case originalHTMLAvailable = "original_html_available"
        case htmlIsRich = "html_is_rich"
        case quoteDetected = "quote_detected"
    }

    init(
        primaryText: String,
        renderMode: String? = nil,
        markers: [ThreadMessageReaderMarker],
        signatureText: String?,
        quotedText: String?,
        footerText: String?,
        originalHTMLAvailable: Bool,
        htmlIsRich: Bool? = nil,
        quoteDetected: Bool? = nil
    ) {
        self.primaryText = primaryText
        self.renderMode = renderMode
        self.markers = markers
        self.signatureText = signatureText
        self.quotedText = quotedText
        self.footerText = footerText
        self.originalHTMLAvailable = originalHTMLAvailable
        self.htmlIsRich = htmlIsRich
        self.quoteDetected = quoteDetected
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
