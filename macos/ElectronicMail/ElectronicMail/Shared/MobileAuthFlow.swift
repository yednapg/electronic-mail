import CryptoKit
import Foundation

public enum MobileAuthFlowError: LocalizedError, Equatable {
    case invalidURL
    case missingLoginCode
    case authenticationCancelled
    case handoffTimedOut
    case handoffFailed(Int)
    case handoffRejected(String)

    public var errorDescription: String? {
        switch self {
        case .invalidURL:
            return "Google sign-in could not start because the sign-in URL is invalid."
        case .missingLoginCode:
            return "Google sign-in did not return a valid login code."
        case .authenticationCancelled:
            return "Google sign-in was cancelled."
        case .handoffTimedOut:
            return "Google sign-in timed out. Please try again."
        case .handoffFailed(let statusCode):
            return "Google sign-in failed with server response \(statusCode)."
        case .handoffRejected(let message):
            return message
        }
    }
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
        guard let components = URLComponents(url: callbackURL, resolvingAgainstBaseURL: false) else {
            throw MobileAuthFlowError.missingLoginCode
        }
        let queryItems = components.queryItems ?? []
        if let code = queryItems.first(where: { $0.name == "login_code" })?.value,
           !code.isEmpty {
            return code
        }
        let status = queryItems.first(where: { $0.name == "status" })?.value?.lowercased()
        let message = queryItems.first(where: { $0.name == "error" })?.value
        if status == "cancelled" {
            throw MobileAuthFlowError.authenticationCancelled
        }
        if status == "failed" {
            throw MobileAuthFlowError.handoffRejected(message ?? "Google sign-in failed. Please try again.")
        }
        throw MobileAuthFlowError.missingLoginCode
    }

    public static func handoffCompletionRedirectURL(
        baseURL: URL,
        handoffID: String,
        codeChallenge: String
    ) throws -> URL {
        guard var components = URLComponents(url: baseURL.appendingPathComponent("auth/mobile/complete"), resolvingAgainstBaseURL: false) else {
            throw MobileAuthFlowError.invalidURL
        }
        components.queryItems = [
            URLQueryItem(name: "handoff_id", value: handoffID),
            URLQueryItem(name: "code_challenge", value: codeChallenge),
        ]
        guard let url = components.url else {
            throw MobileAuthFlowError.invalidURL
        }
        return url
    }

    public static func makePKCEPair() -> (verifier: String, challenge: String) {
        var generator = SystemRandomNumberGenerator()
        let bytes = (0..<32).map { _ in UInt8.random(in: .min ... .max, using: &generator) }
        let verifier = base64URLEncoded(Data(bytes))
        let digest = SHA256.hash(data: Data(verifier.utf8))
        return (verifier, base64URLEncoded(Data(digest)))
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
            do {
                let (data, response) = try await session.data(from: url)
                guard let httpResponse = response as? HTTPURLResponse else {
                    throw MobileAuthFlowError.missingLoginCode
                }

                if httpResponse.statusCode == 200 {
                    let handoff = try JSONDecoder().decode(MobileHandoffResponse.self, from: data)
                    switch handoff.status.lowercased() {
                    case "ready":
                        guard let loginCode = handoff.loginCode, !loginCode.isEmpty else {
                            throw MobileAuthFlowError.missingLoginCode
                        }
                        return loginCode
                    case "cancelled":
                        throw MobileAuthFlowError.authenticationCancelled
                    case "failed":
                        throw MobileAuthFlowError.handoffRejected(handoff.error ?? "Google sign-in failed. Please try again.")
                    default:
                        throw MobileAuthFlowError.missingLoginCode
                    }
                }

                if httpResponse.statusCode != 202,
                   !isTransientHTTPStatus(httpResponse.statusCode) {
                    throw MobileAuthFlowError.handoffFailed(httpResponse.statusCode)
                }
            } catch is CancellationError {
                throw CancellationError()
            } catch let error as URLError where isTransientURLFailure(error) {
                // Keep the browser callback race alive while connectivity recovers.
            }

            try await Task.sleep(nanoseconds: retryDelayNanoseconds)
        }

        throw MobileAuthFlowError.handoffTimedOut
    }

    private static func isTransientHTTPStatus(_ status: Int) -> Bool {
        status == 408 || status == 429 || status >= 500
    }

    private static func isTransientURLFailure(_ error: URLError) -> Bool {
        error.code != .cancelled && error.code != .badURL && error.code != .unsupportedURL
    }

    private static func base64URLEncoded(_ data: Data) -> String {
        data.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
}

private struct MobileHandoffResponse: Codable {
    let status: String
    let loginCode: String?
    let error: String?

    enum CodingKeys: String, CodingKey {
        case status
        case loginCode = "login_code"
        case error
    }
}
