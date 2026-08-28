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
    let contactPhotosAvailable: Bool
    let missingOptionalScopes: [String]

    init(
        available: Bool,
        connected: Bool,
        connectURL: String?,
        canSendMail: Bool = false,
        missingScopes: [String] = [],
        contactPhotosAvailable: Bool = false,
        missingOptionalScopes: [String] = []
    ) {
        self.available = available
        self.connected = connected
        self.connectURL = connectURL
        self.canSendMail = canSendMail
        self.missingScopes = missingScopes
        self.contactPhotosAvailable = contactPhotosAvailable
        self.missingOptionalScopes = missingOptionalScopes
    }

    enum CodingKeys: String, CodingKey {
        case available
        case connected
        case connectURL = "connect_url"
        case canSendMail = "can_send_mail"
        case missingScopes = "missing_scopes"
        case contactPhotosAvailable = "contact_photos_available"
        case missingOptionalScopes = "missing_optional_scopes"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        available = try container.decode(Bool.self, forKey: .available)
        connected = try container.decode(Bool.self, forKey: .connected)
        connectURL = try container.decodeIfPresent(String.self, forKey: .connectURL)
        canSendMail = try container.decodeIfPresent(Bool.self, forKey: .canSendMail) ?? false
        missingScopes = try container.decodeIfPresent([String].self, forKey: .missingScopes) ?? []
        contactPhotosAvailable = try container.decodeIfPresent(Bool.self, forKey: .contactPhotosAvailable) ?? false
        missingOptionalScopes = try container.decodeIfPresent([String].self, forKey: .missingOptionalScopes) ?? []
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
    var syncGeneration: String? = nil
    var phase: String? = nil
    var initialTargetCount: Int? = nil
    var initialMetadataCount: Int? = nil
    var initialBodyTargetCount: Int? = nil
    var initialBodyReadyCount: Int? = nil
    var historyMetadataCount: Int? = nil
    var historyBodyReadyCount: Int? = nil
    var estimatedTotalCount: Int? = nil
    var initialWindowComplete: Bool? = nil
    var historyMetadataComplete: Bool? = nil
    var historyBodyComplete: Bool? = nil
    var lastProgressAt: String? = nil

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
        case syncGeneration = "sync_generation"
        case phase
        case initialTargetCount = "initial_target_count"
        case initialMetadataCount = "initial_metadata_count"
        case initialBodyTargetCount = "initial_body_target_count"
        case initialBodyReadyCount = "initial_body_ready_count"
        case historyMetadataCount = "history_metadata_count"
        case historyBodyReadyCount = "history_body_ready_count"
        case estimatedTotalCount = "estimated_total_count"
        case initialWindowComplete = "initial_window_complete"
        case historyMetadataComplete = "history_metadata_complete"
        case historyBodyComplete = "history_body_complete"
        case lastProgressAt = "last_progress_at"
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
    public var syncGeneration: String? = nil
    public var phase: String? = nil
    public var initialTargetCount: Int? = nil
    public var initialMetadataCount: Int? = nil
    public var initialBodyTargetCount: Int? = nil
    public var initialBodyReadyCount: Int? = nil
    public var historyMetadataCount: Int? = nil
    public var historyBodyReadyCount: Int? = nil
    public var estimatedTotalCount: Int? = nil
    public var initialWindowComplete: Bool? = nil
    public var historyMetadataComplete: Bool? = nil
    public var historyBodyComplete: Bool? = nil
    public var lastProgressAt: String? = nil

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
        case syncGeneration = "sync_generation"
        case phase
        case initialTargetCount = "initial_target_count"
        case initialMetadataCount = "initial_metadata_count"
        case initialBodyTargetCount = "initial_body_target_count"
        case initialBodyReadyCount = "initial_body_ready_count"
        case historyMetadataCount = "history_metadata_count"
        case historyBodyReadyCount = "history_body_ready_count"
        case estimatedTotalCount = "estimated_total_count"
        case initialWindowComplete = "initial_window_complete"
        case historyMetadataComplete = "history_metadata_complete"
        case historyBodyComplete = "history_body_complete"
        case lastProgressAt = "last_progress_at"
    }
}

/// A normalized view of the optional progressive Gmail sync counters exposed
/// by app-session, readiness, mailbox, and sync-state responses. Older servers
/// omit every field, so callers must continue to treat `nil` as unknown.
public struct MailboxSyncProgress: Equatable, Sendable {
    public let syncGeneration: String?
    public let phase: String?
    public let initialTargetCount: Int?
    public let initialMetadataCount: Int?
    public let initialBodyTargetCount: Int?
    public let initialBodyReadyCount: Int?
    public let historyMetadataCount: Int?
    public let historyBodyReadyCount: Int?
    public let estimatedTotalCount: Int?
    public let initialWindowComplete: Bool?
    public let historyMetadataComplete: Bool?
    public let historyBodyComplete: Bool?
    public let lastProgressAt: String?

    public init(
        syncGeneration: String? = nil,
        phase: String? = nil,
        initialTargetCount: Int? = nil,
        initialMetadataCount: Int? = nil,
        initialBodyTargetCount: Int? = nil,
        initialBodyReadyCount: Int? = nil,
        historyMetadataCount: Int? = nil,
        historyBodyReadyCount: Int? = nil,
        estimatedTotalCount: Int? = nil,
        initialWindowComplete: Bool? = nil,
        historyMetadataComplete: Bool? = nil,
        historyBodyComplete: Bool? = nil,
        lastProgressAt: String? = nil
    ) {
        self.syncGeneration = syncGeneration
        self.phase = phase
        self.initialTargetCount = initialTargetCount
        self.initialMetadataCount = initialMetadataCount
        self.initialBodyTargetCount = initialBodyTargetCount
        self.initialBodyReadyCount = initialBodyReadyCount
        self.historyMetadataCount = historyMetadataCount
        self.historyBodyReadyCount = historyBodyReadyCount
        self.estimatedTotalCount = estimatedTotalCount
        self.initialWindowComplete = initialWindowComplete
        self.historyMetadataComplete = historyMetadataComplete
        self.historyBodyComplete = historyBodyComplete
        self.lastProgressAt = lastProgressAt
    }

    public var hasReportedProgress: Bool {
        syncGeneration != nil
            || phase != nil
            || initialTargetCount != nil
            || initialMetadataCount != nil
            || initialBodyTargetCount != nil
            || initialBodyReadyCount != nil
            || historyMetadataCount != nil
            || historyBodyReadyCount != nil
            || estimatedTotalCount != nil
            || initialWindowComplete != nil
            || historyMetadataComplete != nil
            || historyBodyComplete != nil
            || lastProgressAt != nil
    }

    /// Chooses the newest generation as one atomic progress stream, then
    /// merges monotonic counters reported by different endpoints for that
    /// generation. This prevents a stale app-session snapshot from masking a
    /// fresher sync-state or mailbox response.
    static func newestMerged(_ candidates: [MailboxSyncProgress]) -> MailboxSyncProgress {
        let reported = candidates.enumerated().filter { $0.element.hasReportedProgress }
        guard var newest = reported.first else {
            return MailboxSyncProgress()
        }
        for candidate in reported.dropFirst() where isNewer(candidate, than: newest) {
            newest = candidate
        }

        let generation = newest.element.syncGeneration
        let compatible = reported.map(\.element).filter { $0.syncGeneration == generation }
        return MailboxSyncProgress(
            syncGeneration: generation,
            phase: newest.element.phase,
            initialTargetCount: compatible.compactMap(\.initialTargetCount).max(),
            initialMetadataCount: compatible.compactMap(\.initialMetadataCount).max(),
            initialBodyTargetCount: compatible.compactMap(\.initialBodyTargetCount).max(),
            initialBodyReadyCount: compatible.compactMap(\.initialBodyReadyCount).max(),
            historyMetadataCount: compatible.compactMap(\.historyMetadataCount).max(),
            historyBodyReadyCount: compatible.compactMap(\.historyBodyReadyCount).max(),
            estimatedTotalCount: compatible.compactMap(\.estimatedTotalCount).max(),
            initialWindowComplete: monotonicCompletion(compatible.map(\.initialWindowComplete)),
            historyMetadataComplete: monotonicCompletion(compatible.map(\.historyMetadataComplete)),
            historyBodyComplete: monotonicCompletion(compatible.map(\.historyBodyComplete)),
            lastProgressAt: newest.element.lastProgressAt
        )
    }

    private static func isNewer(
        _ lhs: (offset: Int, element: MailboxSyncProgress),
        than rhs: (offset: Int, element: MailboxSyncProgress)
    ) -> Bool {
        let lhsDate = progressDate(lhs.element.lastProgressAt)
        let rhsDate = progressDate(rhs.element.lastProgressAt)
        switch (lhsDate, rhsDate) {
        case let (lhsDate?, rhsDate?):
            return lhsDate == rhsDate ? lhs.offset > rhs.offset : lhsDate > rhsDate
        case (_?, nil):
            return true
        case (nil, _?):
            return false
        case (nil, nil):
            return lhs.offset > rhs.offset
        }
    }

    private static func progressDate(_ value: String?) -> Date? {
        guard let value else { return nil }
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = fractional.date(from: value) {
            return date
        }
        return ISO8601DateFormatter().date(from: value)
    }

    private static func monotonicCompletion(_ values: [Bool?]) -> Bool? {
        if values.contains(where: { $0 == true }) {
            return true
        }
        return values.contains(where: { $0 == false }) ? false : nil
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
    var bodyReady: Bool? = nil
    var contentRevision: String? = nil
    var initialWindowPosition: Int? = nil
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
        case bodyReady = "body_ready"
        case contentRevision = "content_revision"
        case initialWindowPosition = "initial_window_position"
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

enum EmailAddressDisplayFormatter {
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
            .map(String.init)
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
    var syncGeneration: String? = nil
    var phase: String? = nil
    var initialTargetCount: Int? = nil
    var initialMetadataCount: Int? = nil
    var initialBodyTargetCount: Int? = nil
    var initialBodyReadyCount: Int? = nil
    var historyMetadataCount: Int? = nil
    var historyBodyReadyCount: Int? = nil
    var estimatedTotalCount: Int? = nil
    var initialWindowComplete: Bool? = nil
    var historyMetadataComplete: Bool? = nil
    var historyBodyComplete: Bool? = nil
    var lastProgressAt: String? = nil

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
        fullImportCompleted: Bool? = nil,
        syncGeneration: String? = nil,
        phase: String? = nil,
        initialTargetCount: Int? = nil,
        initialMetadataCount: Int? = nil,
        initialBodyTargetCount: Int? = nil,
        initialBodyReadyCount: Int? = nil,
        historyMetadataCount: Int? = nil,
        historyBodyReadyCount: Int? = nil,
        estimatedTotalCount: Int? = nil,
        initialWindowComplete: Bool? = nil,
        historyMetadataComplete: Bool? = nil,
        historyBodyComplete: Bool? = nil,
        lastProgressAt: String? = nil
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
        self.syncGeneration = syncGeneration
        self.phase = phase
        self.initialTargetCount = initialTargetCount
        self.initialMetadataCount = initialMetadataCount
        self.initialBodyTargetCount = initialBodyTargetCount
        self.initialBodyReadyCount = initialBodyReadyCount
        self.historyMetadataCount = historyMetadataCount
        self.historyBodyReadyCount = historyBodyReadyCount
        self.estimatedTotalCount = estimatedTotalCount
        self.initialWindowComplete = initialWindowComplete
        self.historyMetadataComplete = historyMetadataComplete
        self.historyBodyComplete = historyBodyComplete
        self.lastProgressAt = lastProgressAt
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
        case syncGeneration = "sync_generation"
        case phase
        case initialTargetCount = "initial_target_count"
        case initialMetadataCount = "initial_metadata_count"
        case initialBodyTargetCount = "initial_body_target_count"
        case initialBodyReadyCount = "initial_body_ready_count"
        case historyMetadataCount = "history_metadata_count"
        case historyBodyReadyCount = "history_body_ready_count"
        case estimatedTotalCount = "estimated_total_count"
        case initialWindowComplete = "initial_window_complete"
        case historyMetadataComplete = "history_metadata_complete"
        case historyBodyComplete = "history_body_complete"
        case lastProgressAt = "last_progress_at"
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
    var syncGeneration: String? = nil
    var phase: String? = nil
    var initialTargetCount: Int? = nil
    var initialMetadataCount: Int? = nil
    var initialBodyTargetCount: Int? = nil
    var initialBodyReadyCount: Int? = nil
    var historyMetadataCount: Int? = nil
    var historyBodyReadyCount: Int? = nil
    var estimatedTotalCount: Int? = nil
    var initialWindowComplete: Bool? = nil
    var historyMetadataComplete: Bool? = nil
    var historyBodyComplete: Bool? = nil
    var lastProgressAt: String? = nil

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
        case syncGeneration = "sync_generation"
        case phase
        case initialTargetCount = "initial_target_count"
        case initialMetadataCount = "initial_metadata_count"
        case initialBodyTargetCount = "initial_body_target_count"
        case initialBodyReadyCount = "initial_body_ready_count"
        case historyMetadataCount = "history_metadata_count"
        case historyBodyReadyCount = "history_body_ready_count"
        case estimatedTotalCount = "estimated_total_count"
        case initialWindowComplete = "initial_window_complete"
        case historyMetadataComplete = "history_metadata_complete"
        case historyBodyComplete = "history_body_complete"
        case lastProgressAt = "last_progress_at"
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

extension AppSessionSyncState {
    var mailboxSyncProgress: MailboxSyncProgress {
        MailboxSyncProgress(
            syncGeneration: syncGeneration,
            phase: phase,
            initialTargetCount: initialTargetCount,
            initialMetadataCount: initialMetadataCount,
            initialBodyTargetCount: initialBodyTargetCount,
            initialBodyReadyCount: initialBodyReadyCount,
            historyMetadataCount: historyMetadataCount,
            historyBodyReadyCount: historyBodyReadyCount,
            estimatedTotalCount: estimatedTotalCount,
            initialWindowComplete: initialWindowComplete,
            historyMetadataComplete: historyMetadataComplete,
            historyBodyComplete: historyBodyComplete,
            lastProgressAt: lastProgressAt
        )
    }
}

extension PostLoginReadinessResponse {
    var mailboxSyncProgress: MailboxSyncProgress {
        MailboxSyncProgress(
            syncGeneration: syncGeneration,
            phase: phase,
            initialTargetCount: initialTargetCount,
            initialMetadataCount: initialMetadataCount,
            initialBodyTargetCount: initialBodyTargetCount,
            initialBodyReadyCount: initialBodyReadyCount,
            historyMetadataCount: historyMetadataCount,
            historyBodyReadyCount: historyBodyReadyCount,
            estimatedTotalCount: estimatedTotalCount,
            initialWindowComplete: initialWindowComplete,
            historyMetadataComplete: historyMetadataComplete,
            historyBodyComplete: historyBodyComplete,
            lastProgressAt: lastProgressAt
        )
    }
}

extension MailboxResponse {
    var mailboxSyncProgress: MailboxSyncProgress {
        MailboxSyncProgress(
            syncGeneration: syncGeneration,
            phase: phase,
            initialTargetCount: initialTargetCount,
            initialMetadataCount: initialMetadataCount,
            initialBodyTargetCount: initialBodyTargetCount,
            initialBodyReadyCount: initialBodyReadyCount,
            historyMetadataCount: historyMetadataCount,
            historyBodyReadyCount: historyBodyReadyCount,
            estimatedTotalCount: estimatedTotalCount,
            initialWindowComplete: initialWindowComplete,
            historyMetadataComplete: historyMetadataComplete,
            historyBodyComplete: historyBodyComplete,
            lastProgressAt: lastProgressAt
        )
    }
}

extension MailboxSyncStateResponse {
    var mailboxSyncProgress: MailboxSyncProgress {
        MailboxSyncProgress(
            syncGeneration: syncGeneration,
            phase: phase,
            initialTargetCount: initialTargetCount,
            initialMetadataCount: initialMetadataCount,
            initialBodyTargetCount: initialBodyTargetCount,
            initialBodyReadyCount: initialBodyReadyCount,
            historyMetadataCount: historyMetadataCount,
            historyBodyReadyCount: historyBodyReadyCount,
            estimatedTotalCount: estimatedTotalCount,
            initialWindowComplete: initialWindowComplete,
            historyMetadataComplete: historyMetadataComplete,
            historyBodyComplete: historyBodyComplete,
            lastProgressAt: lastProgressAt
        )
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
    case definiteFailure(message: String)
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
            return .definiteFailure(message: response.error ?? "Send failed. You can retry safely.")
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

    static func sanitizedSubject(_ subject: String) -> String {
        let lines = subject.components(separatedBy: .newlines)
        guard lines.count > 1 else { return subject }
        return lines
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
            .joined(separator: " ")
    }

    static func acceptsAttachment(byteCount: Int, currentTotalBytes: Int) -> Bool {
        byteCount <= maximumAttachmentBytes
            && currentTotalBytes + byteCount <= maximumTotalAttachmentBytes
    }

    static func shouldApplyDraftSaveResponse(state: MailDraftState) -> Bool {
        state == .saved
    }

    static func draftSendPreparation(
        hasUnchangedUnresolvedSendAttempt: Bool,
        gmailDraftID: String?,
        clientSendID: String
    ) -> MailComposerDraftSendPreparation {
        guard hasUnchangedUnresolvedSendAttempt,
              let gmailDraftID,
              !gmailDraftID.isEmpty else {
            return .saveDraft
        }
        return .retryExistingDraft(
            gmailDraftID: gmailDraftID,
            clientSendID: clientSendID
        )
    }

    static func contentChangeDecision(
        hasUnresolvedSendAttempt: Bool,
        existingDraftAttachmentCount: Int = 0
    ) -> MailComposerContentChangeDecision {
        hasUnresolvedSendAttempt
            ? .forkDraftForNewSendAttempt(
                discardExistingDraftAttachments: existingDraftAttachmentCount > 0
            )
            : .keepCurrentSendAttempt
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

    static func shouldRestorePendingRecovery(
        recoveryAccountUserID: String,
        currentAccountUserID: String?
    ) -> Bool {
        guard let currentAccountUserID else {
            return false
        }
        return recoveryAccountUserID == currentAccountUserID
    }

    static func shouldRestoreRecovery(
        mode: MailComposerMode,
        hasUnresolvedSendAttempt: Bool,
        hasGmailDraft: Bool,
        hasEditedResponseField: Bool,
        authoredTextFields: [String],
        attachmentCount: Int
    ) -> Bool {
        if hasUnresolvedSendAttempt || hasGmailDraft {
            return true
        }
        if responseMode(for: mode) != nil, hasEditedResponseField {
            return true
        }
        return hasDraftContent(
            textFields: authoredTextFields,
            attachmentCount: attachmentCount
        )
    }

    static func closeDecision(
        recoveryPersisted: Bool,
        requiresGmailDraftSave: Bool,
        gmailDraftSaveState: MailDraftState?
    ) -> MailComposerExitDecision {
        exitDecision(
            recoveryPersisted: recoveryPersisted,
            requiresGmailDraftSave: requiresGmailDraftSave,
            gmailDraftSaveState: gmailDraftSaveState
        )
    }

    static func shutdownDecision(
        recoveryPersisted: Bool,
        requiresGmailDraftSave: Bool,
        gmailDraftSaveState: MailDraftState?
    ) -> MailComposerExitDecision {
        exitDecision(
            recoveryPersisted: recoveryPersisted,
            requiresGmailDraftSave: requiresGmailDraftSave,
            gmailDraftSaveState: gmailDraftSaveState
        )
    }

    private static func exitDecision(
        recoveryPersisted: Bool,
        requiresGmailDraftSave: Bool,
        gmailDraftSaveState: MailDraftState?
    ) -> MailComposerExitDecision {
        guard recoveryPersisted else {
            return .block
        }
        guard requiresGmailDraftSave else {
            return .finishAndClearRecovery
        }
        return gmailDraftSaveState == .saved
            ? .finishAndClearRecovery
            : .finishPreservingRecovery
    }
}

struct MailComposerDraftChangeTracker: Equatable {
    private(set) var synchronizedFingerprint: String?

    mutating func synchronize(fingerprint: String) {
        synchronizedFingerprint = fingerprint
    }

    mutating func shouldHandleChange(fingerprint: String) -> Bool {
        guard fingerprint != synchronizedFingerprint else {
            return false
        }
        synchronizedFingerprint = fingerprint
        return true
    }
}

struct MailComposerRecoveryLoadGate<Presentation> {
    private(set) var isComplete = false
    private var pendingPresentation: Presentation?

    mutating func request(_ presentation: Presentation) -> Presentation? {
        guard isComplete else {
            if pendingPresentation == nil {
                pendingPresentation = presentation
            }
            return nil
        }
        return presentation
    }

    mutating func complete() -> Presentation? {
        isComplete = true
        defer { pendingPresentation = nil }
        return pendingPresentation
    }
}

enum MailComposerExitDecision: Equatable {
    case block
    case finishAndClearRecovery
    case finishPreservingRecovery
}

enum MailComposerResponseTransitionPolicy {
    static let modes: [MailComposerMode] = [.reply, .replyAll, .forward]

    static func isResponseMode(_ mode: MailComposerMode) -> Bool {
        modes.contains(mode)
    }

    static func crossesForwardBoundary(from current: MailComposerMode, to next: MailComposerMode) -> Bool {
        (current == .forward) != (next == .forward)
    }

}

enum MailComposerResponseField: String, Codable, CaseIterable, Sendable {
    case to
    case cc
    case subject
}

/// Sticky provenance for response fields whose generated defaults change when
/// the composer moves between Reply, Reply All, and Forward.
///
/// A user edit remains authoritative across subsequent mode transitions even
/// when its value happens to equal another mode's generated default. Callers
/// should mark edits from user-facing bindings, not from programmatic prefill
/// or transition assignments.
struct MailComposerResponseFieldProvenance: Codable, Equatable, Sendable {
    private(set) var userEditedFields: Set<MailComposerResponseField>

    init(userEditedFields: Set<MailComposerResponseField> = []) {
        self.userEditedFields = userEditedFields
    }

    mutating func markUserEdited(_ field: MailComposerResponseField) {
        userEditedFields.insert(field)
    }

    mutating func resetToGenerated(_ field: MailComposerResponseField) {
        userEditedFields.remove(field)
    }

    func isUserEdited(_ field: MailComposerResponseField) -> Bool {
        userEditedFields.contains(field)
    }

    func transitionedValue(
        for field: MailComposerResponseField,
        current: String,
        nextDefault: String
    ) -> String {
        isUserEdited(field) ? current : nextDefault
    }
}

enum MailComposerDraftSendPreparation: Equatable {
    case saveDraft
    case retryExistingDraft(gmailDraftID: String, clientSendID: String)
}

enum MailComposerContentChangeDecision: Equatable {
    case keepCurrentSendAttempt
    case forkDraftForNewSendAttempt(discardExistingDraftAttachments: Bool)
}

struct MailReplyPrefillRecipients: Equatable {
    let to: [String]
    let cc: [String]
}

enum MailAddressParser {
    static func addresses(in value: String?) -> [String] {
        guard let value, !value.isEmpty else {
            return []
        }

        var mailboxes: [String] = []
        var mailboxStart = value.startIndex
        var index = value.startIndex
        var insideQuotes = false
        var escapingQuotedCharacter = false
        var angleDepth = 0

        func appendMailbox(endingAt end: String.Index) {
            let mailbox = String(value[mailboxStart..<end])
            if let address = address(from: mailbox) {
                mailboxes.append(address)
            }
        }

        while index < value.endIndex {
            let character = value[index]
            if insideQuotes {
                if escapingQuotedCharacter {
                    escapingQuotedCharacter = false
                } else if character == "\\" {
                    escapingQuotedCharacter = true
                } else if character == "\"" {
                    insideQuotes = false
                }
            } else {
                switch character {
                case "\"":
                    insideQuotes = true
                case "<":
                    angleDepth += 1
                case ">":
                    angleDepth = max(0, angleDepth - 1)
                case ",", ";", "\n", "\r":
                    if angleDepth == 0 {
                        appendMailbox(endingAt: index)
                        mailboxStart = value.index(after: index)
                    }
                default:
                    break
                }
            }
            index = value.index(after: index)
        }
        appendMailbox(endingAt: value.endIndex)
        return mailboxes
    }

    static func firstAddress(in value: String?) -> String? {
        addresses(in: value).first
    }

    private static func address(from mailbox: String) -> String? {
        let trimmed = mailbox.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            return nil
        }

        var insideQuotes = false
        var escapingQuotedCharacter = false
        var angleStart: String.Index?
        var index = trimmed.startIndex
        while index < trimmed.endIndex {
            let character = trimmed[index]
            if insideQuotes {
                if escapingQuotedCharacter {
                    escapingQuotedCharacter = false
                } else if character == "\\" {
                    escapingQuotedCharacter = true
                } else if character == "\"" {
                    insideQuotes = false
                }
            } else if character == "\"" {
                insideQuotes = true
            } else if character == "<" {
                angleStart = trimmed.index(after: index)
            } else if character == ">", let angleStart, angleStart <= index {
                let address = trimmed[angleStart..<index].trimmingCharacters(in: .whitespacesAndNewlines)
                return address.isEmpty ? nil : address
            }
            index = trimmed.index(after: index)
        }
        return trimmed
    }
}

enum MailReplyPrefillPolicy {
    static func recipients(
        mode: MailComposerMode,
        currentUser: String?,
        senderHeader: String?,
        originalToHeader: String?,
        originalCCHeader: String?
    ) -> MailReplyPrefillRecipients {
        recipients(
            mode: mode,
            currentUser: currentUser,
            sender: MailAddressParser.firstAddress(in: senderHeader),
            originalTo: MailAddressParser.addresses(in: originalToHeader),
            originalCC: MailAddressParser.addresses(in: originalCCHeader)
        )
    }

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
    var contentRevision: String? = nil

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
        case contentRevision = "content_revision"
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
        messages: [ThreadMessage],
        contentRevision: String? = nil
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
        self.contentRevision = contentRevision
    }
}

public struct MailboxThreadBatchResponse: Codable, Equatable {
    public let threads: [ThreadReaderResponse]
    public let pendingThreadIDs: [String]
    public let missingThreadIDs: [String]

    public init(
        threads: [ThreadReaderResponse],
        pendingThreadIDs: [String] = [],
        missingThreadIDs: [String] = []
    ) {
        self.threads = threads
        self.pendingThreadIDs = pendingThreadIDs
        self.missingThreadIDs = missingThreadIDs
    }

    enum CodingKeys: String, CodingKey {
        case threads
        case pendingThreadIDs = "pending_thread_ids"
        case missingThreadIDs = "missing_thread_ids"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        threads = try container.decodeIfPresent([ThreadReaderResponse].self, forKey: .threads) ?? []
        pendingThreadIDs = try container.decodeIfPresent([String].self, forKey: .pendingThreadIDs) ?? []
        missingThreadIDs = try container.decodeIfPresent([String].self, forKey: .missingThreadIDs) ?? []
    }
}

public struct MailboxHydratedThreadState: Codable, Equatable {
    public let threadID: String
    public let bodyReady: Bool
    public let contentRevision: String?
    public let initialWindowPosition: Int?

    public init(
        threadID: String,
        bodyReady: Bool,
        contentRevision: String? = nil,
        initialWindowPosition: Int? = nil
    ) {
        self.threadID = threadID
        self.bodyReady = bodyReady
        self.contentRevision = contentRevision
        self.initialWindowPosition = initialWindowPosition
    }

    enum CodingKeys: String, CodingKey {
        case threadID = "thread_id"
        case bodyReady = "body_ready"
        case contentRevision = "content_revision"
        case initialWindowPosition = "initial_window_position"
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
    let senderAvatarAssetID: String?
    let replyTo: String?
    let to: String?
    let cc: String?
    let bcc: String?
    let subject: String?
    let body: String
    let bodyComplete: Bool
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
        case senderAvatarAssetID = "sender_avatar_asset_id"
        case replyTo = "reply_to"
        case to
        case cc
        case bcc
        case subject
        case body
        case bodyComplete = "body_complete"
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
        let decodedSnippet = try container.decodeIfPresent(String.self, forKey: .snippet)

        id = decodedID
        source = try container.decode(SourceType.self, forKey: .source)
        threadID = try container.decodeIfPresent(String.self, forKey: .threadID)
        fromAddress = try container.decodeIfPresent(String.self, forKey: .fromAddress)
        senderAvatarAssetID = try container.decodeIfPresent(String.self, forKey: .senderAvatarAssetID)
        replyTo = try container.decodeIfPresent(String.self, forKey: .replyTo)
        to = try container.decodeIfPresent(String.self, forKey: .to)
        cc = try container.decodeIfPresent(String.self, forKey: .cc)
        bcc = try container.decodeIfPresent(String.self, forKey: .bcc)
        subject = try container.decodeIfPresent(String.self, forKey: .subject)
        body = decodedBody
        bodyComplete = try container.decodeIfPresent(Bool.self, forKey: .bodyComplete)
            ?? Self.inferredBodyCompleteness(
                body: decodedBody,
                snippet: decodedSnippet,
                htmlBody: decodedHTMLBody,
                htmlRenderDocument: decodedHTMLRenderDocument,
                reader: decodedReader
            )
        htmlBody = decodedHTMLBody
        htmlRenderDocument = decodedHTMLRenderDocument
        reader = decodedReader
        snippet = decodedSnippet
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
        senderAvatarAssetID: String? = nil,
        replyTo: String? = nil,
        to: String?,
        cc: String?,
        bcc: String?,
        subject: String?,
        body: String,
        bodyComplete: Bool = true,
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
        self.senderAvatarAssetID = senderAvatarAssetID
        self.replyTo = replyTo
        self.to = to
        self.cc = cc
        self.bcc = bcc
        self.subject = subject
        self.body = body
        self.bodyComplete = bodyComplete
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

    init(copying message: ThreadMessage, labelIDs: [String]) {
        id = message.id
        renderRevision = message.renderRevision
        source = message.source
        threadID = message.threadID
        fromAddress = message.fromAddress
        senderAvatarAssetID = message.senderAvatarAssetID
        replyTo = message.replyTo
        to = message.to
        cc = message.cc
        bcc = message.bcc
        subject = message.subject
        body = message.body
        bodyComplete = message.bodyComplete
        htmlBody = message.htmlBody
        htmlRenderDocument = message.htmlRenderDocument
        reader = message.reader
        snippet = message.snippet
        attachments = message.attachments
        self.labelIDs = labelIDs
        receivedAt = message.receivedAt
    }

    private static func inferredBodyCompleteness(
        body: String,
        snippet: String?,
        htmlBody: String?,
        htmlRenderDocument: String?,
        reader: ThreadMessageReader?
    ) -> Bool {
        let normalizedBody = body.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !normalizedBody.isEmpty else {
            return false
        }
        let hasHTML = htmlBody?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty == false
            || htmlRenderDocument?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty == false
        if hasHTML || reader?.originalHTMLAvailable == true {
            return true
        }
        let normalizedSnippet = snippet?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return normalizedSnippet.isEmpty || normalizedBody != normalizedSnippet
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
