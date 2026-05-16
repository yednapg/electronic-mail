import AppKit
import Foundation

public protocol OAuthServicing: AnyObject {
    func startGoogleAuthentication(baseURL: URL, authRedirectURI: String) async throws -> String
}

public enum OAuthError: Error, Equatable {
    case invalidURL
    case browserOpenFailed
    case missingLoginCode
    case handoffTimedOut
    case handoffFailed(Int)
}

@MainActor
public final class GoogleOAuthService: NSObject, OAuthServicing {
    public override init() {
        super.init()
    }

    public func startGoogleAuthentication(baseURL: URL, authRedirectURI: String) async throws -> String {
        let handoffID = UUID().uuidString
        let redirectURI = try Self.mobileHandoffRedirectURL(baseURL: baseURL, handoffID: handoffID)

        guard var components = URLComponents(url: baseURL.appendingPathComponent("auth/google"), resolvingAgainstBaseURL: false) else {
            throw OAuthError.invalidURL
        }

        components.queryItems = [
            URLQueryItem(name: "redirect_to", value: redirectURI.absoluteString)
        ]

        guard let url = components.url else {
            throw OAuthError.invalidURL
        }

        if !NSWorkspace.shared.open(url) {
            throw OAuthError.browserOpenFailed
        }

        return try await Self.pollForLoginCode(baseURL: baseURL, handoffID: handoffID)
    }

    private static func mobileHandoffRedirectURL(baseURL: URL, handoffID: String) throws -> URL {
        guard var components = URLComponents(url: baseURL.appendingPathComponent("auth/mobile/complete"), resolvingAgainstBaseURL: false) else {
            throw OAuthError.invalidURL
        }
        components.queryItems = [URLQueryItem(name: "handoff_id", value: handoffID)]
        guard let url = components.url else {
            throw OAuthError.invalidURL
        }
        return url
    }

    private static func pollForLoginCode(baseURL: URL, handoffID: String) async throws -> String {
        let url = baseURL
            .appendingPathComponent("v1/auth/mobile/handoff")
            .appendingPathComponent(handoffID)

        for _ in 0..<300 {
            let (data, response) = try await URLSession.shared.data(from: url)
            guard let httpResponse = response as? HTTPURLResponse else {
                throw OAuthError.missingLoginCode
            }

            if httpResponse.statusCode == 200 {
                let handoff = try JSONDecoder().decode(MobileHandoffResponse.self, from: data)
                guard let loginCode = handoff.loginCode, !loginCode.isEmpty else {
                    throw OAuthError.missingLoginCode
                }
                return loginCode
            }

            if httpResponse.statusCode != 202 {
                throw OAuthError.handoffFailed(httpResponse.statusCode)
            }

            try await Task.sleep(nanoseconds: 1_000_000_000)
        }

        throw OAuthError.handoffTimedOut
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
