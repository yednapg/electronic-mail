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
    let filename: String
    let mimeType: String?
    let data: Data
    let etag: String?

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

    func exchangeMobileSession(loginCode: String) async throws -> MobileSessionExchangeResponse
    func exchangeMobileSession(grant: MobileAuthenticationGrant) async throws -> MobileSessionExchangeResponse
    func logout() async throws
    func disconnectGoogle(deleteData: Bool, revokeSessions: Bool) async throws
    func deleteGoogleData() async throws
    func deleteAccount() async throws
    func appSession() async throws -> AppSessionResponse
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

    func mailboxFolderCount(label: MailboxLabel) async throws -> MailboxResponse {
        try await mailbox(label: label, limit: 1, cursor: nil)
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
    public let supportsFolderCountPrefetch = true

    private let session: URLSession
    private let decoder: JSONDecoder

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
        var queryItems = [
            URLQueryItem(name: "label", value: label.rawValue),
            URLQueryItem(name: "limit", value: String(limit)),
        ]
        if let cursor, !cursor.isEmpty {
            queryItems.append(URLQueryItem(name: "cursor", value: cursor))
        }
        return try await request(path: "/v1/mailbox", queryItems: queryItems)
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
        return try await request(path: "/v1/mailbox/search", queryItems: queryItems)
    }

    public func thread(threadID: String, limit: Int = 50, offset: Int = 0) async throws -> ThreadReaderResponse {
        try await request(
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
        try await request(path: "/v1/mailbox/sync-state")
    }

    public func triggerMailboxSync() async throws -> MailboxSyncTriggerResponse {
        try await request(path: "/v1/mailbox/sync", method: "POST")
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
        return try await self.request(path: "/v1/mailbox/thread-actions", method: "POST", body: body)
    }

    public func sendCompose(_ request: MailComposeRequest) async throws -> MailSendResponse {
        let body = try JSONEncoder.backend.encode(request)
        return try await self.request(path: "/v1/mailbox/compose", method: "POST", body: body)
    }

    public func sendReply(threadID: String, request: MailReplyRequest) async throws -> MailSendResponse {
        let body = try JSONEncoder.backend.encode(request)
        return try await self.request(path: "/v1/mailbox/threads/\(threadID.urlPathEncoded)/reply", method: "POST", body: body)
    }

    public func outbox(limit: Int = 100) async throws -> MailOutboxResponse {
        try await request(
            path: "/v1/mailbox/outbox",
            queryItems: [URLQueryItem(name: "limit", value: String(limit))]
        )
    }

    public func sendStatus(serverSendID: String) async throws -> MailSendResponse {
        try await request(
            path: "/v1/mailbox/sends/\(serverSendID.urlPathEncoded)",
            timeoutInterval: 3
        )
    }

    public func retrySend(serverSendID: String) async throws -> MailSendResponse {
        try await request(path: "/v1/mailbox/sends/\(serverSendID.urlPathEncoded)/retry", method: "POST")
    }


    public func createDraft(_ request: MailDraftSaveRequest) async throws -> MailDraftResponse {
        let body = try JSONEncoder.backend.encode(request)
        return try await self.request(path: "/v1/mailbox/drafts", method: "POST", body: body)
    }

    public func draft(mailboxThreadID: String) async throws -> MailDraftResponse {
        try await self.request(path: "/v1/mailbox/drafts/\(mailboxThreadID.urlPathEncoded)")
    }

    public func updateDraft(gmailDraftID: String, request: MailDraftSaveRequest) async throws -> MailDraftResponse {
        let body = try JSONEncoder.backend.encode(request)
        return try await self.request(path: "/v1/mailbox/drafts/\(gmailDraftID.urlPathEncoded)", method: "PUT", body: body)
    }

    public func deleteDraft(gmailDraftID: String) async throws {
        try await emptyRequest(path: "/v1/mailbox/drafts/\(gmailDraftID.urlPathEncoded)", method: "DELETE")
    }

    public func sendDraft(gmailDraftID: String, request: MailDraftSendRequest) async throws -> MailSendResponse {
        let body = try JSONEncoder.backend.encode(request)
        return try await self.request(path: "/v1/mailbox/drafts/\(gmailDraftID.urlPathEncoded)/send", method: "POST", body: body)
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
        if let query, !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return try await request(
                path: "/v1/ai-inbox/search",
                queryItems: [URLQueryItem(name: "q", value: query)]
            )
        }
        return try await request(path: "/v1/ai-inbox")
    }

    public func aiOrganizationProfile() async throws -> AIOrganizationProfile {
        try await request(path: "/v1/ai-organization/profile")
    }

    public func updateAIOrganizationProfile(_ patch: AIOrganizationProfilePatch) async throws -> AIOrganizationProfile {
        let body = try JSONEncoder.backend.encode(patch)
        return try await request(path: "/v1/ai-organization/profile", method: "PATCH", body: body)
    }

    public func aiMatter(_ matterID: String) async throws -> AIMatterDetail {
        try await request(path: "/v1/matters/\(matterID.urlPathEncoded)")
    }

    public func aiGroupingExplanation(matterID: String, messageID: String) async throws -> AIGroupingExplanation {
        try await request(
            path: "/v1/matters/\(matterID.urlPathEncoded)/messages/\(messageID.urlPathEncoded)/grouping-reason"
        )
    }

    public func applyMatterDecision(_ request: MatterDecisionRequest) async throws -> MatterDecisionResponse {
        let body = try JSONEncoder.backend.encode(request)
        return try await self.request(path: "/v1/matter-decisions", method: "POST", body: body)
    }

    public func applyMatterAction(_ request: MatterEntityActionRequest) async throws -> MatterEntityActionResponse {
        let body = try JSONEncoder.backend.encode(request)
        return try await self.request(path: "/v1/mailbox/entity-actions", method: "POST", body: body)
    }

    public func deleteAIOrganizationData() async throws {
        try await emptyRequest(path: "/v1/ai-organization/data", method: "DELETE")
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

private extension String {
    var urlPathEncoded: String {
        var allowedCharacters = CharacterSet.urlPathAllowed
        allowedCharacters.remove(charactersIn: "/")
        return addingPercentEncoding(withAllowedCharacters: allowedCharacters) ?? self
    }
}
