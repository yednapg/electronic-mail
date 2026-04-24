import Foundation

public protocol DashboardAPIProviding: AnyObject {
    var baseURL: URL { get set }

    func health() async throws
    func dashboard() async throws -> DashboardResponse
    func trace(entityID: String) async throws -> TraceReplayResponse
    func archiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse
    func unarchiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse
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

    public func archiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        try await request(path: "/gmail/threads/\(threadID.urlPathEncoded)/archive", method: "POST")
    }

    public func unarchiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        try await request(path: "/gmail/threads/\(threadID.urlPathEncoded)/unarchive", method: "POST")
    }

    private func request<Response: Decodable>(
        path: String,
        method: String = "GET"
    ) async throws -> Response {
        guard let url = URL(string: path, relativeTo: baseURL)?.absoluteURL else {
            throw APIError.invalidURL
        }

        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Accept")

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
