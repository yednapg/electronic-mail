import Foundation

public enum APIError: Error, Equatable {
    case invalidURL
    case httpStatus(Int)
    case emptyResponse
}

public enum AppRunMode: String, Equatable {
    case demo
    case localBackend
}

public protocol AppClient: AnyObject {
    var baseURL: URL { get set }
    var sessionToken: String? { get set }
    var mode: AppRunMode { get }

    func exchangeMobileSession(loginCode: String) async throws -> MobileSessionExchangeResponse
    func appSession() async throws -> AppSessionResponse
    func mailbox(label: MailboxLabel, limit: Int, cursor: String?) async throws -> MailboxResponse
    func thread(threadID: String, limit: Int, offset: Int) async throws -> ThreadReaderResponse
    func triggerMailboxSync() async throws -> MailboxSyncTriggerResponse
    func syncMailboxNow() async throws -> MailboxSyncTriggerResponse
    func archiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse
    func unarchiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse
    func markThreadRead(_ threadID: String) async throws -> GmailThreadMutationResponse
}

public final class LiveBackendAppClient: AppClient {
    public var baseURL: URL
    public var sessionToken: String?
    public let mode: AppRunMode = .localBackend

    private let session: URLSession
    private let decoder: JSONDecoder

    public init(baseURL: URL, session: URLSession = .shared, decoder: JSONDecoder = .backend) {
        self.baseURL = baseURL
        self.session = session
        self.decoder = decoder
    }

    public func appSession() async throws -> AppSessionResponse {
        try await request(path: "/v1/app/session")
    }

    public func exchangeMobileSession(loginCode: String) async throws -> MobileSessionExchangeResponse {
        let payload = MobileSessionExchangeRequest(loginCode: loginCode)
        let body = try JSONEncoder.backend.encode(payload)
        return try await request(path: "/v1/auth/mobile/exchange", method: "POST", body: body)
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

    public func thread(threadID: String, limit: Int = 50, offset: Int = 0) async throws -> ThreadReaderResponse {
        try await request(
            path: "/v1/mailbox/threads/\(threadID.urlPathEncoded)",
            queryItems: [
                URLQueryItem(name: "limit", value: String(limit)),
                URLQueryItem(name: "offset", value: String(offset)),
            ]
        )
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

    private func request<Response: Decodable>(
        path: String,
        queryItems: [URLQueryItem] = [],
        method: String = "GET",
        body: Data? = nil
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

        var request = URLRequest(url: url)
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
}

private extension String {
    var urlPathEncoded: String {
        var allowedCharacters = CharacterSet.urlPathAllowed
        allowedCharacters.remove(charactersIn: "/")
        return addingPercentEncoding(withAllowedCharacters: allowedCharacters) ?? self
    }
}
