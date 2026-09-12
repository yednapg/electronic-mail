import Foundation

public enum APIError: Error, Equatable, LocalizedError {
    case invalidURL
    case httpStatus(Int)
    case emptyResponse
    case unboundMobileAuthentication

    public var errorDescription: String? {
        switch self {
        case .invalidURL:
            return "The app could not contact the mail service. Check the server address and try again."
        case .emptyResponse:
            return "The mail service returned no data. Try again."
        case .unboundMobileAuthentication:
            return "Google sign-in could not be verified. Start sign-in again from this app."
        case .httpStatus(let status):
            switch status {
            case 401:
                return "Your session expired. Sign in again."
            case 403:
                return "Google permission is required. Reconnect your account and try again."
            case 404:
                return "That email could not be found. Refresh your mailbox and try again."
            case 408:
                return "The request timed out. Check your connection and try again."
            case 409:
                return "This email changed elsewhere. Refresh your mailbox and try again."
            case 413:
                return "The attachment is too large. Remove it or choose a smaller file."
            case 429:
                return "Too many requests were made. Wait a moment and try again."
            case 500...599:
                return "The mail service is temporarily unavailable. Try again shortly."
            case 400...499:
                return "The request could not be completed. Check the details and try again."
            default:
                return "The mail service returned an unexpected response. Try again."
            }
        }
    }
}

public enum AppRunMode: String, Equatable {
    case demo
    case localBackend
}

public struct DownloadedAttachment: Equatable {
    public let filename: String
    public let mimeType: String?
    public let data: Data
    public let etag: String?

    public init(filename: String, mimeType: String?, data: Data, etag: String? = nil) {
        self.filename = filename
        self.mimeType = mimeType
        self.data = data
        self.etag = etag
    }
}

public struct MailboxServerEvent: Equatable {
    public let id: String?
    public let event: String
    public let data: String
}

public struct ServerSentEventParser {
    private var id: String?
    private var event: String?
    private var dataLines: [String] = []

    public init() {}

    public mutating func feed(line: String) -> MailboxServerEvent? {
        if line.isEmpty {
            return dispatch()
        }
        if line.hasPrefix(":") {
            return nil
        }
        let parts = line.split(separator: ":", maxSplits: 1, omittingEmptySubsequences: false)
        let field = String(parts.first ?? "")
        var value = parts.count > 1 ? String(parts[1]) : ""
        if value.hasPrefix(" ") {
            value.removeFirst()
        }
        switch field {
        case "id":
            id = value
        case "event":
            event = value
        case "data":
            dataLines.append(value)
        default:
            break
        }
        return nil
    }

    private mutating func dispatch() -> MailboxServerEvent? {
        guard id != nil || event != nil || !dataLines.isEmpty else {
            reset()
            return nil
        }
        let output = MailboxServerEvent(
            id: id,
            event: event ?? "message",
            data: dataLines.joined(separator: "\n")
        )
        reset()
        return output
    }

    private mutating func reset() {
        id = nil
        event = nil
        dataLines = []
    }
}

public protocol AppClient: AnyObject {
    var baseURL: URL { get set }
    var sessionToken: String? { get set }
    var mode: AppRunMode { get }
    var supportsRealtimeMailboxUpdates: Bool { get }
    var supportsFolderCountPrefetch: Bool { get }
    var canPersistCurrentMailboxCache: Bool { get }

    func exchangeMobileSession(loginCode: String) async throws -> MobileSessionExchangeResponse
    func exchangeMobileSession(grant: MobileAuthenticationGrant) async throws -> MobileSessionExchangeResponse
    func logout() async throws
    func disconnectGoogle(deleteData: Bool, revokeSessions: Bool) async throws
    func deleteGoogleData() async throws
    func deleteAccount() async throws
    func appSession() async throws -> AppSessionResponse
    func gmailAccounts() async throws -> GmailAccountsResponse
    func startGmailAccountLink(redirectTo: String) async throws -> GmailAccountLinkStartResponse
    func setupGmailAccount(accountID: String) async throws -> GmailAccount
    func configureMailboxScope(_ scope: MailboxViewScope, accounts: GmailAccountsResponse)
    func mailbox(label: MailboxLabel, limit: Int, cursor: String?) async throws -> MailboxResponse
    func mailboxFolderCount(label: MailboxLabel) async throws -> MailboxResponse
    func searchMailbox(
        query: String,
        label: MailboxLabel?,
        limit: Int,
        cursor: String?,
        hydrateInBackground: Bool
    ) async throws -> MailboxResponse
    func thread(threadID: String, limit: Int, offset: Int) async throws -> ThreadReaderResponse
    func batchThreads(threadIDs: [String]) async throws -> MailboxThreadBatchResponse
    func observeHydratedThreads(_ threads: [MailboxHydratedThreadState], userID: String) async
    func mailboxSyncState() async throws -> MailboxSyncStateResponse
    func triggerMailboxSync() async throws -> MailboxSyncTriggerResponse
    func syncMailboxNow() async throws -> MailboxSyncTriggerResponse
    func archiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse
    func unarchiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse
    func markThreadRead(_ threadID: String) async throws -> GmailThreadMutationResponse
    func enqueueThreadAction(_ request: QueuedThreadActionRequest) async throws -> QueuedThreadActionResponse
    func sendCompose(_ request: MailComposeRequest) async throws -> MailSendResponse
    func sendReply(threadID: String, request: MailReplyRequest) async throws -> MailSendResponse
    func outbox(limit: Int) async throws -> MailOutboxResponse
    func sendStatus(serverSendID: String) async throws -> MailSendResponse
    func retrySend(serverSendID: String) async throws -> MailSendResponse
    func createDraft(_ request: MailDraftSaveRequest) async throws -> MailDraftResponse
    func draft(mailboxThreadID: String) async throws -> MailDraftResponse
    func updateDraft(gmailDraftID: String, request: MailDraftSaveRequest) async throws -> MailDraftResponse
    func deleteDraft(gmailDraftID: String) async throws
    func sendDraft(gmailDraftID: String, request: MailDraftSendRequest) async throws -> MailSendResponse
    func downloadAttachment(messageID: String, attachment: ThreadAttachment) async throws -> DownloadedAttachment
    func createTask(_ request: TaskCreateRequest) async throws -> TaskResponse
    func updateTask(_ taskID: String, request: TaskUpdateRequest) async throws -> TaskResponse
    func completeEntity(_ entityID: String, request: EntityOutcomeRequest) async throws -> EntityOutcomeResponse
    func aiInbox(query: String?) async throws -> AIInboxResponse
    func aiTodos(limit: Int) async throws -> AITodoResponse
    func updateAITodo(_ todoID: String, gmailAccountID: String?, request: AITodoUpdateRequest) async throws -> AITodoItem
    func aiOrganizationProfile() async throws -> AIOrganizationProfile
    func updateAIOrganizationProfile(_ patch: AIOrganizationProfilePatch) async throws -> AIOrganizationProfile
    func aiMatter(_ matterID: String) async throws -> AIMatterDetail
    func aiGroupingExplanation(matterID: String, messageID: String) async throws -> AIGroupingExplanation
    func applyMatterDecision(_ request: MatterDecisionRequest) async throws -> MatterDecisionResponse
    func applyMatterAction(_ request: MatterEntityActionRequest) async throws -> MatterEntityActionResponse
    func deleteAIOrganizationData() async throws
}

public extension AppClient {
    var supportsRealtimeMailboxUpdates: Bool { false }
    var supportsFolderCountPrefetch: Bool { false }
    var canPersistCurrentMailboxCache: Bool { true }

    func configureMailboxScope(_ scope: MailboxViewScope, accounts: GmailAccountsResponse) {}

    func setupGmailAccount(accountID: String) async throws -> GmailAccount {
        throw APIError.httpStatus(501)
    }

    func mailboxFolderCount(label: MailboxLabel) async throws -> MailboxResponse {
        try await mailbox(label: label, limit: 1, cursor: nil)
    }

    func gmailAccounts() async throws -> GmailAccountsResponse {
        throw APIError.httpStatus(501)
    }

    func startGmailAccountLink(redirectTo: String) async throws -> GmailAccountLinkStartResponse {
        throw APIError.httpStatus(501)
    }

    func exchangeMobileSession(grant: MobileAuthenticationGrant) async throws -> MobileSessionExchangeResponse {
        try await exchangeMobileSession(loginCode: grant.loginCode)
    }

    func logout() async throws {
        throw APIError.httpStatus(501)
    }

    func disconnectGoogle(deleteData: Bool, revokeSessions: Bool) async throws {
        throw APIError.httpStatus(501)
    }

    func deleteGoogleData() async throws {
        throw APIError.httpStatus(501)
    }

    func deleteAccount() async throws {
        throw APIError.httpStatus(501)
    }

    func mailboxSyncState() async throws -> MailboxSyncStateResponse {
        throw APIError.httpStatus(501)
    }

    func batchThreads(threadIDs: [String]) async throws -> MailboxThreadBatchResponse {
        throw APIError.httpStatus(501)
    }

    func observeHydratedThreads(_ threads: [MailboxHydratedThreadState], userID: String) async {}

    func sendCompose(_ request: MailComposeRequest) async throws -> MailSendResponse {
        throw APIError.httpStatus(501)
    }

    func sendReply(threadID: String, request: MailReplyRequest) async throws -> MailSendResponse {
        throw APIError.httpStatus(501)
    }

    func outbox(limit: Int) async throws -> MailOutboxResponse {
        throw APIError.httpStatus(501)
    }

    func sendStatus(serverSendID: String) async throws -> MailSendResponse {
        throw APIError.httpStatus(501)
    }

    func retrySend(serverSendID: String) async throws -> MailSendResponse {
        throw APIError.httpStatus(501)
    }

    func searchMailbox(
        query: String,
        label: MailboxLabel?,
        limit: Int,
        cursor: String?,
        hydrateInBackground: Bool = true
    ) async throws -> MailboxResponse {
        throw APIError.httpStatus(501)
    }

    func createDraft(_ request: MailDraftSaveRequest) async throws -> MailDraftResponse {
        throw APIError.httpStatus(501)
    }

    func draft(mailboxThreadID: String) async throws -> MailDraftResponse {
        throw APIError.httpStatus(501)
    }

    func updateDraft(gmailDraftID: String, request: MailDraftSaveRequest) async throws -> MailDraftResponse {
        throw APIError.httpStatus(501)
    }

    func deleteDraft(gmailDraftID: String) async throws {
        throw APIError.httpStatus(501)
    }

    func sendDraft(gmailDraftID: String, request: MailDraftSendRequest) async throws -> MailSendResponse {
        throw APIError.httpStatus(501)
    }

    func downloadAttachment(messageID: String, attachment: ThreadAttachment) async throws -> DownloadedAttachment {
        throw APIError.httpStatus(501)
    }

    func aiInbox(query: String? = nil) async throws -> AIInboxResponse {
        throw APIError.httpStatus(501)
    }

    func aiTodos(limit: Int = 100) async throws -> AITodoResponse {
        throw APIError.httpStatus(501)
    }

    func updateAITodo(
        _ todoID: String,
        gmailAccountID: String? = nil,
        request: AITodoUpdateRequest
    ) async throws -> AITodoItem {
        throw APIError.httpStatus(501)
    }

    func aiOrganizationProfile() async throws -> AIOrganizationProfile {
        throw APIError.httpStatus(501)
    }

    func updateAIOrganizationProfile(_ patch: AIOrganizationProfilePatch) async throws -> AIOrganizationProfile {
        throw APIError.httpStatus(501)
    }

    func aiMatter(_ matterID: String) async throws -> AIMatterDetail {
        throw APIError.httpStatus(501)
    }

    func aiGroupingExplanation(matterID: String, messageID: String) async throws -> AIGroupingExplanation {
        throw APIError.httpStatus(501)
    }

    func applyMatterDecision(_ request: MatterDecisionRequest) async throws -> MatterDecisionResponse {
        throw APIError.httpStatus(501)
    }

    func applyMatterAction(_ request: MatterEntityActionRequest) async throws -> MatterEntityActionResponse {
        throw APIError.httpStatus(501)
    }

    func deleteAIOrganizationData() async throws {
        throw APIError.httpStatus(501)
    }
}

public final class LiveBackendAppClient: AppClient {
    public var baseURL: URL
    public var sessionToken: String?
    public let mode: AppRunMode = .localBackend
    public let supportsRealtimeMailboxUpdates = true
    public var supportsFolderCountPrefetch: Bool {
        guard let accounts = configuredGmailAccounts else { return true }
        return activeMailboxScope.gmailAccountID == accounts.primaryGmailAccountID
    }
    public var canPersistCurrentMailboxCache: Bool {
        guard let accounts = configuredGmailAccounts else { return true }
        return activeMailboxScope.gmailAccountID == accounts.primaryGmailAccountID
    }

    private let session: URLSession
    private let decoder: JSONDecoder
    private var activeMailboxScope: MailboxViewScope = .combined
    private var configuredGmailAccounts: GmailAccountsResponse?
    private var sendAccountIDs: [String: String] = [:]
    private var draftAccountIDs: [String: String] = [:]
    private var matterAccountIDs: [String: String] = [:]

    public init(baseURL: URL, session: URLSession? = nil, decoder: JSONDecoder = .backend) {
        self.baseURL = baseURL
        self.session = session ?? URLSession(configuration: Self.authenticatedSessionConfiguration())
        self.decoder = decoder
    }

    static func authenticatedSessionConfiguration() -> URLSessionConfiguration {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.urlCache = nil
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        configuration.urlCredentialStorage = nil
        configuration.httpCookieStorage = nil
        configuration.httpShouldSetCookies = false
        return configuration
    }

    public func appSession() async throws -> AppSessionResponse {
        try await request(path: "/v1/app/session")
    }

    public func gmailAccounts() async throws -> GmailAccountsResponse {
        try await request(path: "/v1/gmail-accounts")
    }

    public func startGmailAccountLink(redirectTo: String) async throws -> GmailAccountLinkStartResponse {
        let payload = GmailAccountLinkStartRequest(redirectTo: redirectTo)
        let body = try JSONEncoder.backend.encode(payload)
        return try await request(path: "/v1/gmail-accounts/link", method: "POST", body: body)
    }

    public func setupGmailAccount(accountID: String) async throws -> GmailAccount {
        try await request(
            path: "/v1/gmail-accounts/\(accountID.urlPathEncoded)/setup",
            method: "POST"
        )
    }

    public func configureMailboxScope(_ scope: MailboxViewScope, accounts: GmailAccountsResponse) {
        activeMailboxScope = scope
        configuredGmailAccounts = accounts
    }

    public func exchangeMobileSession(loginCode: String) async throws -> MobileSessionExchangeResponse {
        throw APIError.unboundMobileAuthentication
    }

    public func exchangeMobileSession(grant: MobileAuthenticationGrant) async throws -> MobileSessionExchangeResponse {
        let payload = MobileSessionExchangeRequest(
            loginCode: grant.loginCode,
            handoffID: grant.handoffID,
            codeVerifier: grant.codeVerifier
        )
        let body = try JSONEncoder.backend.encode(payload)
        return try await request(path: "/v1/auth/mobile/exchange", method: "POST", body: body)
    }

    public func logout() async throws {
        try await emptyRequest(path: "/v1/auth/logout", method: "POST")
    }

    public func disconnectGoogle(deleteData: Bool, revokeSessions: Bool) async throws {
        try await emptyRequest(
            path: "/v1/auth/google",
            queryItems: [
                URLQueryItem(name: "deleteData", value: deleteData ? "true" : "false"),
                URLQueryItem(name: "revokeSessions", value: revokeSessions ? "true" : "false"),
            ],
            method: "DELETE"
        )
    }

    public func deleteGoogleData() async throws {
        try await emptyRequest(path: "/v1/auth/google/data", method: "DELETE")
    }

    public func deleteAccount() async throws {
        try await emptyRequest(path: "/v1/auth/account", method: "DELETE")
    }

    public func mailbox(label: MailboxLabel = .inbox, limit: Int = 100, cursor: String? = nil) async throws -> MailboxResponse {
        if let accounts = configuredGmailAccounts {
            switch activeMailboxScope {
            case .gmail(let accountID):
                if accountID != accounts.primaryGmailAccountID {
                    let providerCursor = try mailboxCursor(
                        cursor,
                        expectedRoute: "account:\(accountID)"
                    )
                    var response = try await accountMailbox(
                        accountID: accountID,
                        label: label,
                        limit: limit,
                        cursor: providerCursor
                    )
                    response.nextCursor = wrappedMailboxCursor(
                        response.nextCursor,
                        route: "account:\(accountID)"
                    )
                    return response
                }
                let primaryCursor = try mailboxCursor(cursor, expectedRoute: "primary")
                var response = try await unscopedMailbox(
                    label: label,
                    limit: limit,
                    cursor: primaryCursor
                )
                response.nextCursor = wrappedMailboxCursor(response.nextCursor, route: "primary")
                return response
            case .combined:
                // Only the primary cursor is continued. Secondary Gmail pages
                // are independent and are never presented to the backend as a
                // synthetic combined mailbox.
                if cursor == nil {
                    var responses: [MailboxResponse] = []
                    var warnings: [String] = []
                    var primaryNextCursor: String?
                    do {
                        let primary = try await unscopedMailbox(label: label, limit: limit, cursor: nil)
                        responses.append(primary)
                        primaryNextCursor = primary.nextCursor
                    } catch {
                        let email = accounts.accounts.first(where: { $0.isPrimary })?.email ?? "primary Gmail"
                        warnings.append("Could not load \(email). Other accounts remain available.")
                    }
                    for account in accounts.accounts where account.state.isMailboxReadable && !account.isPrimary {
                        do {
                            responses.append(try await accountMailbox(
                                accountID: account.id,
                                label: label,
                                limit: min(limit, 50),
                                cursor: nil
                            ))
                        } catch {
                            warnings.append("Could not load \(account.email). Other accounts remain available.")
                        }
                    }
                    guard !responses.isEmpty else { throw APIError.httpStatus(503) }
                    var combined = MailboxResponse.combinedForPresentation(
                        responses,
                        primaryNextCursor: primaryNextCursor,
                        accountWarnings: warnings
                    )
                    combined.nextCursor = wrappedMailboxCursor(primaryNextCursor, route: "primary")
                    return combined
                }
                let primaryCursor = try mailboxCursor(cursor, expectedRoute: "primary")
                var response = try await unscopedMailbox(
                    label: label,
                    limit: limit,
                    cursor: primaryCursor
                )
                response.nextCursor = wrappedMailboxCursor(response.nextCursor, route: "primary")
                return response
            }
        }
        return try await unscopedMailbox(label: label, limit: limit, cursor: cursor)
    }

    private func wrappedMailboxCursor(_ cursor: String?, route: String) -> String? {
        guard let cursor, !cursor.isEmpty else { return nil }
        return "scope|\(route)|\(cursor)"
    }

    private func mailboxCursor(_ cursor: String?, expectedRoute: String) throws -> String? {
        guard let cursor, !cursor.isEmpty else { return nil }
        guard cursor.hasPrefix("scope|") else {
            // A cursor produced before a scope change must never be replayed
            // against a different Gmail provider mailbox.
            throw CancellationError()
        }
        let parts = cursor.split(separator: "|", maxSplits: 2, omittingEmptySubsequences: false)
        guard parts.count == 3, parts[1] == Substring(expectedRoute) else {
            throw CancellationError()
        }
        return String(parts[2])
    }

    private func unscopedMailbox(label: MailboxLabel, limit: Int, cursor: String?) async throws -> MailboxResponse {
        var queryItems = [
            URLQueryItem(name: "label", value: label.rawValue),
            URLQueryItem(name: "limit", value: String(limit)),
        ]
        if let cursor, !cursor.isEmpty {
            queryItems.append(URLQueryItem(name: "cursor", value: cursor))
        }
        return try await request(path: "/v1/mailbox", queryItems: queryItems)
    }

    private func accountMailbox(
        accountID: String,
        label: MailboxLabel,
        limit: Int,
        cursor: String?
    ) async throws -> MailboxResponse {
        var queryItems = [
            URLQueryItem(name: "label", value: label.rawValue),
            URLQueryItem(name: "limit", value: String(limit)),
        ]
        if let cursor, !cursor.isEmpty {
            queryItems.append(URLQueryItem(name: "cursor", value: cursor))
        }
        return try await request(
            path: "/v1/gmail-accounts/\(accountID.urlPathEncoded)/mailbox",
            queryItems: queryItems
        )
    }

    public func searchMailbox(
        query: String,
        label: MailboxLabel?,
        limit: Int = 100,
        cursor: String? = nil,
        hydrateInBackground: Bool = true
    ) async throws -> MailboxResponse {
        var queryItems = [
            URLQueryItem(name: "q", value: query),
            URLQueryItem(name: "limit", value: String(limit)),
        ]
        if let label {
            queryItems.append(URLQueryItem(name: "label", value: label.rawValue))
        }
        if let cursor, !cursor.isEmpty {
            queryItems.append(URLQueryItem(name: "cursor", value: cursor))
        }
        if !hydrateInBackground {
            queryItems.append(URLQueryItem(name: "hydrate", value: "false"))
        }
        if let accounts = configuredGmailAccounts {
            if let accountID = activeMailboxScope.gmailAccountID {
                return try await request(
                    path: "/v1/gmail-accounts/\(accountID.urlPathEncoded)/mailbox/search",
                    queryItems: queryItems
                )
            }
            var responses: [MailboxResponse] = []
            var warnings: [String] = []
            for account in accounts.accounts where account.state.isMailboxReadable {
                do {
                    responses.append(try await request(
                        path: "/v1/gmail-accounts/\(account.id.urlPathEncoded)/mailbox/search",
                        queryItems: queryItems
                    ))
                } catch {
                    warnings.append("Could not search \(account.email). Other accounts remain available.")
                }
            }
            guard !responses.isEmpty else { throw APIError.httpStatus(503) }
            return MailboxResponse.combinedForPresentation(
                responses,
                primaryNextCursor: nil,
                accountWarnings: warnings
            )
        }
        return try await request(path: "/v1/mailbox/search", queryItems: queryItems)
    }

    public func thread(threadID: String, limit: Int = 50, offset: Int = 0) async throws -> ThreadReaderResponse {
        if let scoped = ScopedGmailThreadID(threadID) {
            return try await request(
                path: "/v1/gmail-accounts/\(scoped.accountID.urlPathEncoded)/threads/\(scoped.gmailThreadID.urlPathEncoded)",
                queryItems: [
                    URLQueryItem(name: "limit", value: String(limit)),
                    URLQueryItem(name: "offset", value: String(offset)),
                ]
            )
        }
        return try await request(
            path: "/v1/mailbox/threads/\(threadID.urlPathEncoded)",
            queryItems: [
                URLQueryItem(name: "limit", value: String(limit)),
                URLQueryItem(name: "offset", value: String(offset)),
            ]
        )
    }

    public func batchThreads(threadIDs: [String]) async throws -> MailboxThreadBatchResponse {
        var seen = Set<String>()
        let normalized = threadIDs.filter { !$0.isEmpty && seen.insert($0).inserted }
        guard !normalized.isEmpty, normalized.count <= 20 else {
            throw APIError.httpStatus(422)
        }
        let body = try JSONEncoder.backend.encode(["thread_ids": normalized])
        return try await request(path: "/v1/mailbox/threads/batch", method: "POST", body: body)
    }

    public func mailboxSyncState() async throws -> MailboxSyncStateResponse {
        if let accountID = activeMailboxScope.gmailAccountID {
            return try await request(
                path: "/v1/gmail-accounts/\(accountID.urlPathEncoded)/mailbox/sync-state"
            )
        }
        return try await request(path: "/v1/mailbox/sync-state")
    }

    public func triggerMailboxSync() async throws -> MailboxSyncTriggerResponse {
        if let accountID = activeMailboxScope.gmailAccountID {
            return try await request(
                path: "/v1/gmail-accounts/\(accountID.urlPathEncoded)/mailbox/sync",
                method: "POST"
            )
        }
        return try await request(path: "/v1/mailbox/sync", method: "POST")
    }

    public func syncMailboxNow() async throws -> MailboxSyncTriggerResponse {
        try await request(path: "/v1/mailbox/sync-now", method: "POST")
    }

    public func archiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        try await request(path: "/gmail/threads/\(threadID.urlPathEncoded)/archive", method: "POST")
    }

    public func unarchiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        try await request(path: "/gmail/threads/\(threadID.urlPathEncoded)/unarchive", method: "POST")
    }

    public func markThreadRead(_ threadID: String) async throws -> GmailThreadMutationResponse {
        try await request(path: "/v1/gmail/threads/\(threadID.urlPathEncoded)/mark-read", method: "POST")
    }

    public func enqueueThreadAction(_ request: QueuedThreadActionRequest) async throws -> QueuedThreadActionResponse {
        let body = try JSONEncoder.backend.encode(request)
        guard let accountID = writeAccountID(explicit: request.gmailAccountID, threadID: request.mailboxThreadID) else {
            throw APIError.httpStatus(409)
        }
        return try await self.request(
            path: "/v1/gmail-accounts/\(accountID.urlPathEncoded)/mailbox/thread-actions",
            method: "POST", body: body
        )
    }

    public func sendCompose(_ request: MailComposeRequest) async throws -> MailSendResponse {
        let body = try JSONEncoder.backend.encode(request)
        guard let accountID = writeAccountID(explicit: request.gmailAccountID) else {
            throw APIError.httpStatus(409)
        }
        let response: MailSendResponse = try await self.request(
            path: "/v1/gmail-accounts/\(accountID.urlPathEncoded)/mailbox/compose",
            method: "POST", body: body
        )
        rememberSend(response, fallbackAccountID: accountID)
        return response
    }

    public func sendReply(threadID: String, request: MailReplyRequest) async throws -> MailSendResponse {
        let body = try JSONEncoder.backend.encode(request)
        guard let accountID = writeAccountID(explicit: request.gmailAccountID, threadID: threadID) else {
            throw APIError.httpStatus(409)
        }
        let response: MailSendResponse = try await self.request(
            path: "/v1/gmail-accounts/\(accountID.urlPathEncoded)/mailbox/threads/\(threadID.urlPathEncoded)/reply",
            method: "POST", body: body
        )
        rememberSend(response, fallbackAccountID: accountID)
        return response
    }

    public func outbox(limit: Int = 100) async throws -> MailOutboxResponse {
        if let accountID = writeAccountID() {
            return try await request(
                path: "/v1/gmail-accounts/\(accountID.urlPathEncoded)/mailbox/outbox",
                queryItems: [URLQueryItem(name: "limit", value: String(limit))]
            )
        }
        return try await request(
            path: "/v1/mailbox/outbox",
            queryItems: [URLQueryItem(name: "limit", value: String(limit))]
        )
    }

    public func sendStatus(serverSendID: String) async throws -> MailSendResponse {
        if let accountID = sendAccountIDs[serverSendID] ?? writeAccountID() {
            return try await request(
                path: "/v1/gmail-accounts/\(accountID.urlPathEncoded)/mailbox/sends/\(serverSendID.urlPathEncoded)",
                timeoutInterval: 3
            )
        }
        return try await request(
            path: "/v1/mailbox/sends/\(serverSendID.urlPathEncoded)",
            timeoutInterval: 3
        )
    }

    public func retrySend(serverSendID: String) async throws -> MailSendResponse {
        if let accountID = sendAccountIDs[serverSendID] ?? writeAccountID() {
            return try await request(
                path: "/v1/gmail-accounts/\(accountID.urlPathEncoded)/mailbox/sends/\(serverSendID.urlPathEncoded)/retry",
                method: "POST"
            )
        }
        return try await request(path: "/v1/mailbox/sends/\(serverSendID.urlPathEncoded)/retry", method: "POST")
    }


    public func createDraft(_ request: MailDraftSaveRequest) async throws -> MailDraftResponse {
        let body = try JSONEncoder.backend.encode(request)
        guard let accountID = writeAccountID(explicit: request.gmailAccountID, threadID: request.mailboxThreadID) else {
            throw APIError.httpStatus(409)
        }
        let response: MailDraftResponse = try await self.request(
            path: "/v1/gmail-accounts/\(accountID.urlPathEncoded)/mailbox/drafts",
            method: "POST", body: body
        )
        rememberDraft(response, fallbackAccountID: accountID)
        return response
    }

    public func draft(mailboxThreadID: String) async throws -> MailDraftResponse {
        if let accountID = draftAccountIDs[mailboxThreadID] ?? writeAccountID(threadID: mailboxThreadID) {
            let rawID = ScopedGmailThreadID(mailboxThreadID)?.gmailThreadID ?? mailboxThreadID
            let response: MailDraftResponse = try await self.request(
                path: "/v1/gmail-accounts/\(accountID.urlPathEncoded)/mailbox/drafts/\(rawID.urlPathEncoded)"
            )
            rememberDraft(response, fallbackAccountID: accountID)
            return response
        }
        return try await self.request(path: "/v1/mailbox/drafts/\(mailboxThreadID.urlPathEncoded)")
    }

    public func updateDraft(gmailDraftID: String, request: MailDraftSaveRequest) async throws -> MailDraftResponse {
        let body = try JSONEncoder.backend.encode(request)
        guard let accountID = request.gmailAccountID ?? draftAccountIDs[gmailDraftID] ?? writeAccountID(threadID: request.mailboxThreadID) else {
            throw APIError.httpStatus(409)
        }
        let response: MailDraftResponse = try await self.request(
            path: "/v1/gmail-accounts/\(accountID.urlPathEncoded)/mailbox/drafts/\(gmailDraftID.urlPathEncoded)",
            method: "PUT", body: body
        )
        rememberDraft(response, fallbackAccountID: accountID)
        return response
    }

    public func deleteDraft(gmailDraftID: String) async throws {
        if let accountID = draftAccountIDs[gmailDraftID] ?? writeAccountID() {
            try await emptyRequest(
                path: "/v1/gmail-accounts/\(accountID.urlPathEncoded)/mailbox/drafts/\(gmailDraftID.urlPathEncoded)",
                method: "DELETE"
            )
            return
        }
        try await emptyRequest(path: "/v1/mailbox/drafts/\(gmailDraftID.urlPathEncoded)", method: "DELETE")
    }

    public func sendDraft(gmailDraftID: String, request: MailDraftSendRequest) async throws -> MailSendResponse {
        let body = try JSONEncoder.backend.encode(request)
        guard let accountID = draftAccountIDs[gmailDraftID] ?? writeAccountID() else {
            throw APIError.httpStatus(409)
        }
        let response: MailSendResponse = try await self.request(
            path: "/v1/gmail-accounts/\(accountID.urlPathEncoded)/mailbox/drafts/\(gmailDraftID.urlPathEncoded)/send",
            method: "POST", body: body
        )
        rememberSend(response, fallbackAccountID: accountID)
        return response
    }

    private func writeAccountID(explicit: String? = nil, threadID: String? = nil) -> String? {
        if let explicit, !explicit.isEmpty { return explicit }
        if let threadID, let scoped = ScopedGmailThreadID(threadID) { return scoped.accountID }
        if let selected = activeMailboxScope.gmailAccountID { return selected }
        return configuredGmailAccounts?.primaryGmailAccountID
    }

    private func rememberSend(_ response: MailSendResponse, fallbackAccountID: String) {
        guard let serverSendID = response.serverSendID else { return }
        sendAccountIDs[serverSendID] = response.gmailAccountID ?? fallbackAccountID
    }

    private func rememberDraft(_ response: MailDraftResponse, fallbackAccountID: String) {
        guard let gmailDraftID = response.gmailDraftID else { return }
        draftAccountIDs[gmailDraftID] = response.gmailAccountID ?? fallbackAccountID
    }

    public func downloadAttachment(messageID: String, attachment: ThreadAttachment) async throws -> DownloadedAttachment {
        let path = attachment.downloadURL
            ?? "/v1/mailbox/messages/\(messageID.urlPathEncoded)/attachments/\(attachment.attachmentID.urlPathEncoded)"
        let (data, response) = try await rawRequest(path: path)
        return DownloadedAttachment(
            filename: attachment.filename,
            mimeType: attachment.mimeType ?? response.value(forHTTPHeaderField: "Content-Type"),
            data: data,
            etag: response.value(forHTTPHeaderField: "ETag")
        )
    }

    public func createTask(_ request: TaskCreateRequest) async throws -> TaskResponse {
        let body = try JSONEncoder.backend.encode(request)
        return try await self.request(path: "/v1/tasks", method: "POST", body: body)
    }

    public func updateTask(_ taskID: String, request: TaskUpdateRequest) async throws -> TaskResponse {
        let body = try JSONEncoder.backend.encode(request)
        return try await self.request(path: "/v1/tasks/\(taskID.urlPathEncoded)", method: "PATCH", body: body)
    }

    public func completeEntity(_ entityID: String, request: EntityOutcomeRequest) async throws -> EntityOutcomeResponse {
        let body = try JSONEncoder.backend.encode(request)
        return try await self.request(path: "/v1/entities/\(entityID.urlPathEncoded)/complete", method: "POST", body: body)
    }

    public func aiInbox(query: String? = nil) async throws -> AIInboxResponse {
        if let accountID = activeMailboxScope.gmailAccountID {
            return try await accountAIInbox(accountID: accountID, query: query)
        }
        if let accounts = configuredGmailAccounts?.accounts.filter({ $0.state == .ready }),
           !accounts.isEmpty {
            var loaded: [AIInboxResponse] = []
            var failures: [String] = []
            for account in accounts {
                do {
                    loaded.append(try await accountAIInbox(accountID: account.id, query: query))
                } catch {
                    failures.append(account.email)
                }
            }
            guard let first = loaded.first else {
                throw APIError.httpStatus(503)
            }
            guard let primary = loaded.first(where: {
                $0.gmailAccountID == configuredGmailAccounts?.primaryGmailAccountID
            }) else {
                // A secondary account's disabled profile must never masquerade
                // as the global AI Inbox state when the primary request fails.
                throw APIError.httpStatus(503)
            }
            var matters = loaded.flatMap(\.matters)
            matters.sort { $0.latestMessageAt > $1.latestMessageAt }
            var organizing = loaded.flatMap(\.organizing)
            organizing.sort { $0.latestMessageAt > $1.latestMessageAt }
            return AIInboxResponse(
                profile: primary.profile,
                generationID: nil,
                revision: loaded.map(\.revision).joined(separator: ":"),
                stale: loaded.contains(where: { $0.stale }) || !failures.isEmpty,
                staleReason: failures.isEmpty ? nil : "Could not load AI Inbox for \(failures.joined(separator: ", ")).",
                matters: matters,
                organizing: organizing,
                generatedAt: loaded.map(\.generatedAt).max() ?? first.generatedAt
            )
        }
        if let query, !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return try await request(
                path: "/v1/ai-inbox/search",
                queryItems: [URLQueryItem(name: "q", value: query)]
            )
        }
        return try await request(path: "/v1/ai-inbox")
    }

    public func aiTodos(limit: Int = 100) async throws -> AITodoResponse {
        let boundedLimit = max(1, min(limit, 200))
        if let accountID = activeMailboxScope.gmailAccountID {
            return try await accountAITodos(accountID: accountID, limit: boundedLimit)
        }
        if let accounts = configuredGmailAccounts?.accounts.filter({ $0.state == .ready }),
           !accounts.isEmpty {
            var loaded: [AITodoResponse] = []
            for account in accounts {
                do {
                    var response = try await accountAITodos(accountID: account.id, limit: boundedLimit)
                    response.items = response.items.map { item in
                        var item = item
                        item.sourceAccountEmail = account.email
                        return item
                    }
                    loaded.append(response)
                } catch {
                    continue
                }
            }
            guard !loaded.isEmpty else {
                throw APIError.httpStatus(503)
            }
            return AITodoResponse(
                items: Array(
                    loaded.flatMap(\.items)
                        .sorted(by: Self.aiTodoComesFirst)
                        .prefix(boundedLimit)
                ),
                organizingCount: loaded.reduce(0) { $0 + $1.organizingCount },
                generatedAt: loaded.map(\.generatedAt).max() ?? ""
            )
        }
        return try await request(
            path: "/v1/ai-todos",
            queryItems: [URLQueryItem(name: "limit", value: String(boundedLimit))]
        )
    }

    public func updateAITodo(
        _ todoID: String,
        gmailAccountID: String? = nil,
        request update: AITodoUpdateRequest
    ) async throws -> AITodoItem {
        let body = try JSONEncoder.backend.encode(update)
        let accountID = gmailAccountID ?? activeMailboxScope.gmailAccountID
        if let accountID {
            return try await request(
                path: "/v1/gmail-accounts/\(accountID.urlPathEncoded)/ai-todos/\(todoID.urlPathEncoded)",
                method: "PATCH",
                body: body
            )
        }
        return try await request(
            path: "/v1/ai-todos/\(todoID.urlPathEncoded)",
            method: "PATCH",
            body: body
        )
    }

    public func aiOrganizationProfile() async throws -> AIOrganizationProfile {
        if let accountID = writeAccountID() {
            return try await request(
                path: "/v1/gmail-accounts/\(accountID.urlPathEncoded)/ai-organization/profile"
            )
        }
        return try await request(path: "/v1/ai-organization/profile")
    }

    public func updateAIOrganizationProfile(_ patch: AIOrganizationProfilePatch) async throws -> AIOrganizationProfile {
        let body = try JSONEncoder.backend.encode(patch)
        if let accountID = writeAccountID() {
            return try await request(
                path: "/v1/gmail-accounts/\(accountID.urlPathEncoded)/ai-organization/profile",
                method: "PATCH", body: body
            )
        }
        return try await request(path: "/v1/ai-organization/profile", method: "PATCH", body: body)
    }

    public func aiMatter(_ matterID: String) async throws -> AIMatterDetail {
        if let accountID = matterAccountIDs[matterID] ?? writeAccountID() {
            return try await request(
                path: "/v1/gmail-accounts/\(accountID.urlPathEncoded)/matters/\(matterID.urlPathEncoded)"
            )
        }
        return try await request(path: "/v1/matters/\(matterID.urlPathEncoded)")
    }

    public func aiGroupingExplanation(matterID: String, messageID: String) async throws -> AIGroupingExplanation {
        if let accountID = matterAccountIDs[matterID] ?? writeAccountID() {
            return try await request(
                path: "/v1/gmail-accounts/\(accountID.urlPathEncoded)/matters/\(matterID.urlPathEncoded)/messages/\(messageID.urlPathEncoded)/grouping-reason"
            )
        }
        return try await request(
            path: "/v1/matters/\(matterID.urlPathEncoded)/messages/\(messageID.urlPathEncoded)/grouping-reason"
        )
    }

    public func applyMatterDecision(_ request: MatterDecisionRequest) async throws -> MatterDecisionResponse {
        let body = try JSONEncoder.backend.encode(request)
        if let accountID = matterAccountIDs[request.matterID] ?? writeAccountID() {
            return try await self.request(
                path: "/v1/gmail-accounts/\(accountID.urlPathEncoded)/matter-decisions",
                method: "POST", body: body
            )
        }
        return try await self.request(path: "/v1/matter-decisions", method: "POST", body: body)
    }

    public func applyMatterAction(_ request: MatterEntityActionRequest) async throws -> MatterEntityActionResponse {
        let body = try JSONEncoder.backend.encode(request)
        if let accountID = matterAccountIDs[request.matterID] ?? writeAccountID() {
            return try await self.request(
                path: "/v1/gmail-accounts/\(accountID.urlPathEncoded)/mailbox/entity-actions",
                method: "POST", body: body
            )
        }
        return try await self.request(path: "/v1/mailbox/entity-actions", method: "POST", body: body)
    }

    public func deleteAIOrganizationData() async throws {
        if let accountID = writeAccountID() {
            try await emptyRequest(
                path: "/v1/gmail-accounts/\(accountID.urlPathEncoded)/ai-organization/data",
                method: "DELETE"
            )
            return
        }
        try await emptyRequest(path: "/v1/ai-organization/data", method: "DELETE")
    }

    private func accountAIInbox(
        accountID: String,
        query: String?
    ) async throws -> AIInboxResponse {
        let path: String
        var queryItems: [URLQueryItem] = []
        if let query, !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            path = "/v1/gmail-accounts/\(accountID.urlPathEncoded)/ai-inbox/search"
            queryItems = [URLQueryItem(name: "q", value: query)]
        } else {
            path = "/v1/gmail-accounts/\(accountID.urlPathEncoded)/ai-inbox"
        }
        var response: AIInboxResponse = try await request(path: path, queryItems: queryItems)
        let accountEmail = configuredGmailAccounts?.accounts.first(where: { $0.id == accountID })?.email
        response.gmailAccountID = accountID
        response.profile.gmailAccountID = accountID
        for index in response.matters.indices {
            response.matters[index].gmailAccountID = accountID
            response.matters[index].sourceAccountEmail = accountEmail
            matterAccountIDs[response.matters[index].id] = accountID
        }
        for index in response.organizing.indices {
            response.organizing[index].gmailAccountID = accountID
            response.organizing[index].sourceAccountEmail = accountEmail
        }
        return response
    }

    private func accountAITodos(accountID: String, limit: Int) async throws -> AITodoResponse {
        var response: AITodoResponse = try await request(
            path: "/v1/gmail-accounts/\(accountID.urlPathEncoded)/ai-todos",
            queryItems: [URLQueryItem(name: "limit", value: String(limit))]
        )
        response.gmailAccountID = accountID
        response.items = response.items.map { item in
            var item = item
            item.gmailAccountID = accountID
            return item
        }
        return response
    }

    private static func aiTodoComesFirst(_ lhs: AITodoItem, _ rhs: AITodoItem) -> Bool {
        let kindRank: (AITodoItem) -> Int = { $0.displayKind == .todo ? 0 : 1 }
        let urgencyRank: (AITodoItem) -> Int = {
            switch $0.urgency {
            case "now": 0
            case "today": 1
            case "upcoming": 2
            default: 3
            }
        }
        if kindRank(lhs) != kindRank(rhs) { return kindRank(lhs) < kindRank(rhs) }
        if urgencyRank(lhs) != urgencyRank(rhs) { return urgencyRank(lhs) < urgencyRank(rhs) }
        if lhs.dueAt != rhs.dueAt { return (lhs.dueAt ?? "9999") < (rhs.dueAt ?? "9999") }
        return lhs.confidence > rhs.confidence
    }

    private func request<Response: Decodable>(
        path: String,
        queryItems: [URLQueryItem] = [],
        method: String = "GET",
        body: Data? = nil,
        timeoutInterval: TimeInterval? = nil
    ) async throws -> Response {
        guard let requestURL = URL(string: path, relativeTo: baseURL)?.absoluteURL,
              var components = URLComponents(url: requestURL, resolvingAgainstBaseURL: false) else {
            throw APIError.invalidURL
        }
        if !queryItems.isEmpty {
            components.queryItems = queryItems
        }
        guard let url = components.url else {
            throw APIError.invalidURL
        }

        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData)
        if let timeoutInterval {
            request.timeoutInterval = timeoutInterval
        }
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        if let sessionToken, !sessionToken.isEmpty {
            request.setValue("Bearer \(sessionToken)", forHTTPHeaderField: "Authorization")
        }

        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.emptyResponse
        }
        guard (200..<300).contains(httpResponse.statusCode) else {
            throw APIError.httpStatus(httpResponse.statusCode)
        }
        return try decoder.decode(Response.self, from: data)
    }

    private func rawRequest(path: String) async throws -> (Data, HTTPURLResponse) {
        let url = try authenticatedURL(path: path)
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData)
        request.httpMethod = "GET"
        request.setValue("application/octet-stream", forHTTPHeaderField: "Accept")
        if let sessionToken, !sessionToken.isEmpty {
            request.setValue("Bearer \(sessionToken)", forHTTPHeaderField: "Authorization")
        }

        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.emptyResponse
        }
        guard (200..<300).contains(httpResponse.statusCode) else {
            throw APIError.httpStatus(httpResponse.statusCode)
        }
        return (data, httpResponse)
    }

    private func authenticatedURL(path: String) throws -> URL {
        guard let url = URL(string: path, relativeTo: baseURL)?.absoluteURL,
              let baseScheme = baseURL.scheme?.lowercased(),
              let baseHost = baseURL.host?.lowercased(),
              let requestScheme = url.scheme?.lowercased(),
              let requestHost = url.host?.lowercased(),
              baseScheme == requestScheme,
              baseHost == requestHost,
              effectivePort(for: baseURL) == effectivePort(for: url) else {
            throw APIError.invalidURL
        }
        return url
    }

    private func effectivePort(for url: URL) -> Int? {
        if let port = url.port {
            return port
        }
        switch url.scheme?.lowercased() {
        case "http":
            return 80
        case "https":
            return 443
        default:
            return nil
        }
    }

    private func emptyRequest(path: String, queryItems: [URLQueryItem] = [], method: String) async throws {
        guard let requestURL = URL(string: path, relativeTo: baseURL)?.absoluteURL,
              var components = URLComponents(url: requestURL, resolvingAgainstBaseURL: false) else {
            throw APIError.invalidURL
        }
        if !queryItems.isEmpty {
            components.queryItems = queryItems
        }
        guard let url = components.url else {
            throw APIError.invalidURL
        }
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData)
        request.httpMethod = method
        if let sessionToken, !sessionToken.isEmpty {
            request.setValue("Bearer \(sessionToken)", forHTTPHeaderField: "Authorization")
        }
        let (_, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.emptyResponse
        }
        guard (200..<300).contains(httpResponse.statusCode) else {
            throw APIError.httpStatus(httpResponse.statusCode)
        }
    }
}

extension String {
    var urlPathEncoded: String {
        var allowedCharacters = CharacterSet.urlPathAllowed
        allowedCharacters.remove(charactersIn: "/")
        return addingPercentEncoding(withAllowedCharacters: allowedCharacters) ?? self
    }
}
