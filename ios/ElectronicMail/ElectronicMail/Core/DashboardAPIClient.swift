import Foundation

public protocol DashboardAPIProviding: AnyObject {
    var baseURL: URL { get set }

    func health() async throws
    func dashboard() async throws -> DashboardResponse
    func trace(entityID: String) async throws -> TraceReplayResponse
    func createTask(_ request: TaskCreateRequest) async throws -> TaskResponse
    func completeEntity(_ entityID: String) async throws -> EntityOutcomeResponse
    func snoozeEntity(_ entityID: String, until snoozeUntil: String) async throws -> EntityOutcomeResponse
    func dismissEntity(_ entityID: String) async throws -> EntityOutcomeResponse
    func thread(entityID: String) async throws -> ThreadReaderResponse
    func createDraft(_ request: GmailDraftRequest) async throws -> GmailDraftResponse
    func sendDraft(_ draftID: String) async throws -> GmailDraftResponse
    func archiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse
    func unarchiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse
    func markThreadRead(_ threadID: String) async throws -> GmailThreadMutationResponse
}

public enum APIError: Error, Equatable {
    case invalidURL
    case httpStatus(Int)
    case emptyResponse
}

public final class DashboardAPIClient: DashboardAPIProviding {
    public var baseURL: URL

    private let session: URLSession
    private let decoder: JSONDecoder

    public init(baseURL: URL, session: URLSession = .shared, decoder: JSONDecoder = .backend) {
        self.baseURL = baseURL
        self.session = session
        self.decoder = decoder
    }

    public func health() async throws {
        let _: [String: String] = try await request(path: "/health")
    }

    public func dashboard() async throws -> DashboardResponse {
        try await request(path: "/dashboard")
    }

    public func trace(entityID: String) async throws -> TraceReplayResponse {
        try await request(path: "/trace/\(entityID.urlPathEncoded)")
    }

    public func createTask(_ requestBody: TaskCreateRequest) async throws -> TaskResponse {
        try await request(path: "/v1/tasks", method: "POST", body: requestBody)
    }

    public func completeEntity(_ entityID: String) async throws -> EntityOutcomeResponse {
        try await request(path: "/v1/entities/\(entityID.urlPathEncoded)/complete", method: "POST", body: EmptyBody())
    }

    public func snoozeEntity(_ entityID: String, until snoozeUntil: String) async throws -> EntityOutcomeResponse {
        try await request(
            path: "/v1/entities/\(entityID.urlPathEncoded)/snooze",
            method: "POST",
            body: ["snooze_until": snoozeUntil]
        )
    }

    public func dismissEntity(_ entityID: String) async throws -> EntityOutcomeResponse {
        try await request(path: "/v1/entities/\(entityID.urlPathEncoded)/dismiss", method: "POST", body: EmptyBody())
    }

    public func thread(entityID: String) async throws -> ThreadReaderResponse {
        try await request(path: "/v1/entities/\(entityID.urlPathEncoded)/thread")
    }

    public func createDraft(_ requestBody: GmailDraftRequest) async throws -> GmailDraftResponse {
        try await request(path: "/v1/gmail/drafts", method: "POST", body: requestBody)
    }

    public func sendDraft(_ draftID: String) async throws -> GmailDraftResponse {
        try await request(path: "/v1/gmail/drafts/\(draftID.urlPathEncoded)/send", method: "POST", body: EmptyBody())
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
        method: String = "GET"
    ) async throws -> Response {
        try await request(path: path, method: method, body: Optional<EmptyBody>.none)
    }

    private func request<Response: Decodable, RequestBody: Encodable>(
        path: String,
        method: String = "GET",
        body: RequestBody?
    ) async throws -> Response {
        guard let url = URL(string: path, relativeTo: baseURL)?.absoluteURL else {
            throw APIError.invalidURL
        }

        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let body {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONEncoder.backend.encode(body)
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

private struct EmptyBody: Codable {}

private extension String {
    var urlPathEncoded: String {
        var allowedCharacters = CharacterSet.urlPathAllowed
        allowedCharacters.remove(charactersIn: "/")
        return addingPercentEncoding(withAllowedCharacters: allowedCharacters) ?? self
    }
}
