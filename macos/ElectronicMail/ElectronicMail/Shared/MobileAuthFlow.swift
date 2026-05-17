import Foundation

public enum MobileAuthFlowError: Error, Equatable {
    case invalidURL
    case missingLoginCode
    case handoffTimedOut
    case handoffFailed(Int)
}

public enum MobileAuthFlow {
    public static let callbackScheme = "electronicmail"
    public static let callbackRedirectURI = "electronicmail://auth/callback"

    public static func authenticationURL(baseURL: URL, redirectURL: URL) throws -> URL {
        guard var components = URLComponents(url: baseURL.appendingPathComponent("auth/google"), resolvingAgainstBaseURL: false) else {
            throw MobileAuthFlowError.invalidURL
        }
        components.queryItems = [
            URLQueryItem(name: "redirect_to", value: redirectURL.absoluteString)
        ]
        guard let url = components.url else {
            throw MobileAuthFlowError.invalidURL
        }
        return url
    }

    public static func loginCode(from callbackURL: URL) throws -> String {
        guard let components = URLComponents(url: callbackURL, resolvingAgainstBaseURL: false),
              let code = components.queryItems?.first(where: { $0.name == "login_code" })?.value,
              !code.isEmpty else {
            throw MobileAuthFlowError.missingLoginCode
        }
        return code
    }

    public static func handoffCompletionRedirectURL(baseURL: URL, handoffID: String) throws -> URL {
        guard var components = URLComponents(url: baseURL.appendingPathComponent("auth/mobile/complete"), resolvingAgainstBaseURL: false) else {
            throw MobileAuthFlowError.invalidURL
        }
        components.queryItems = [
            URLQueryItem(name: "handoff_id", value: handoffID)
        ]
        guard let url = components.url else {
            throw MobileAuthFlowError.invalidURL
        }
        return url
    }

    public static func handoffStatusURL(baseURL: URL, handoffID: String) -> URL {
        baseURL
            .appendingPathComponent("v1/auth/mobile/handoff")
            .appendingPathComponent(handoffID)
    }

    public static func pollForLoginCode(
        baseURL: URL,
        handoffID: String,
        session: URLSession = .shared,
        maxAttempts: Int = 300,
        retryDelayNanoseconds: UInt64 = 1_000_000_000
    ) async throws -> String {
        let url = handoffStatusURL(baseURL: baseURL, handoffID: handoffID)

        for _ in 0..<maxAttempts {
            let (data, response) = try await session.data(from: url)
            guard let httpResponse = response as? HTTPURLResponse else {
                throw MobileAuthFlowError.missingLoginCode
            }

            if httpResponse.statusCode == 200 {
                let handoff = try JSONDecoder().decode(MobileHandoffResponse.self, from: data)
                guard let loginCode = handoff.loginCode, !loginCode.isEmpty else {
                    throw MobileAuthFlowError.missingLoginCode
                }
                return loginCode
            }

            if httpResponse.statusCode != 202 {
                throw MobileAuthFlowError.handoffFailed(httpResponse.statusCode)
            }

            try await Task.sleep(nanoseconds: retryDelayNanoseconds)
        }

        throw MobileAuthFlowError.handoffTimedOut
    }
}

private struct MobileHandoffResponse: Codable {
    let status: String
    let loginCode: String?

    enum CodingKeys: String, CodingKey {
        case status
        case loginCode = "login_code"
    }
}
