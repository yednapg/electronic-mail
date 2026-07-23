import AuthenticationServices
import AppKit
import CryptoKit
import Foundation

public protocol OAuthServicing: AnyObject {
    func startGoogleAuthentication(baseURL: URL, authRedirectURI: String) async throws -> String
}

public enum OAuthError: LocalizedError, Equatable {
    case invalidURL
    case browserOpenFailed
    case missingLoginCode
    case authenticationCancelled
    case handoffTimedOut
    case handoffFailed(Int)
    case handoffRejected(String)

    public var errorDescription: String? {
        switch self {
        case .invalidURL:
            return "Google sign-in could not start because the sign-in URL is invalid."
        case .browserOpenFailed:
            return "Google sign-in could not open in your browser."
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

public extension Notification.Name {
    static let electronicMailOAuthCallback = Notification.Name("ElectronicMailOAuthCallback")
}

@MainActor
public final class GoogleOAuthService: NSObject, OAuthServicing, ASWebAuthenticationPresentationContextProviding {
    private var authenticationSession: ASWebAuthenticationSession?

    public override init() {
        super.init()
    }

    public func startGoogleAuthentication(baseURL: URL, authRedirectURI: String) async throws -> String {
        guard let callbackScheme = URLComponents(string: authRedirectURI)?.scheme, !callbackScheme.isEmpty else {
            throw OAuthError.invalidURL
        }
        let url = try Self.googleAuthURL(baseURL: baseURL, redirectURI: authRedirectURI)
        let loginCode: String = try await withCheckedThrowingContinuation { continuation in
            let session = ASWebAuthenticationSession(url: url, callbackURLScheme: callbackScheme) { [weak self] callbackURL, error in
                Task { @MainActor in
                    self?.authenticationSession = nil
                }
                if let error = error as? ASWebAuthenticationSessionError, error.code == .canceledLogin {
                    continuation.resume(throwing: OAuthError.authenticationCancelled)
                    return
                }
                if let error {
                    continuation.resume(throwing: error)
                    return
                }
                guard let callbackURL else {
                    continuation.resume(throwing: OAuthError.missingLoginCode)
                    return
                }
                do {
                    continuation.resume(returning: try Self.loginCode(from: callbackURL))
                } catch {
                    continuation.resume(throwing: error)
                }
            }
            session.presentationContextProvider = self
            session.prefersEphemeralWebBrowserSession = false
            authenticationSession = session
            if !session.start() {
                authenticationSession = nil
                continuation.resume(throwing: OAuthError.browserOpenFailed)
            }
        }
        NSApp.activate(ignoringOtherApps: true)
        return loginCode
    }

    public func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        NSApp.keyWindow ?? NSApplication.shared.windows.first ?? ASPresentationAnchor()
    }

    public func startGoogleAuthenticationWithBrowserHandoff(baseURL: URL) async throws -> MobileAuthenticationGrant {
        let handoffID = UUID().uuidString
        let pkce = Self.makePKCEPair()
        let redirectURI = try Self.mobileHandoffRedirectURL(
            baseURL: baseURL,
            handoffID: handoffID,
            codeChallenge: pkce.challenge
        )
        let url = try Self.googleAuthURL(baseURL: baseURL, redirectURI: redirectURI.absoluteString)

        if !NSWorkspace.shared.open(url) {
            throw OAuthError.browserOpenFailed
        }

        let loginCode = try await withThrowingTaskGroup(of: String.self) { group in
            group.addTask {
                try await Self.pollForLoginCode(baseURL: baseURL, handoffID: handoffID)
            }
            group.addTask {
                try await Self.waitForCallbackLoginCode(handoffID: handoffID)
            }
            guard let loginCode = try await group.next() else {
                throw OAuthError.missingLoginCode
            }
            group.cancelAll()
            return loginCode
        }
        NSApp.activate(ignoringOtherApps: true)
        return MobileAuthenticationGrant(
            loginCode: loginCode,
            handoffID: handoffID,
            codeVerifier: pkce.verifier
        )
    }

    public static func handleCallbackURL(_ url: URL) {
        NotificationCenter.default.post(name: .electronicMailOAuthCallback, object: url)
    }

    nonisolated private static func googleAuthURL(baseURL: URL, redirectURI: String) throws -> URL {
        guard var components = URLComponents(url: baseURL.appendingPathComponent("auth/google"), resolvingAgainstBaseURL: false) else {
            throw OAuthError.invalidURL
        }

        components.queryItems = [
            URLQueryItem(name: "redirect_to", value: redirectURI)
        ]

        guard let url = components.url else {
            throw OAuthError.invalidURL
        }
        return url
    }

    nonisolated static func loginCode(from callbackURL: URL) throws -> String {
        guard let components = URLComponents(url: callbackURL, resolvingAgainstBaseURL: false) else {
            throw OAuthError.missingLoginCode
        }
        let queryItems = components.queryItems ?? []
        if let loginCode = queryItems.first(where: { $0.name == "login_code" })?.value,
           !loginCode.isEmpty {
            return loginCode
        }
        let status = queryItems.first(where: { $0.name == "status" })?.value?.lowercased()
        let message = queryItems.first(where: { $0.name == "error" })?.value
        if status == "cancelled" {
            throw OAuthError.authenticationCancelled
        }
        if status == "failed" {
            throw OAuthError.handoffRejected(message ?? "Google sign-in failed. Please try again.")
        }
        throw OAuthError.missingLoginCode
    }

    nonisolated static func handoffID(from callbackURL: URL) -> String? {
        URLComponents(url: callbackURL, resolvingAgainstBaseURL: false)?
            .queryItems?
            .first(where: { $0.name == "handoff_id" })?
            .value
    }

    nonisolated static func mobileHandoffRedirectURL(
        baseURL: URL,
        handoffID: String,
        codeChallenge: String
    ) throws -> URL {
        guard var components = URLComponents(url: baseURL.appendingPathComponent("auth/mobile/complete"), resolvingAgainstBaseURL: false) else {
            throw OAuthError.invalidURL
        }
        components.queryItems = [
            URLQueryItem(name: "handoff_id", value: handoffID),
            URLQueryItem(name: "code_challenge", value: codeChallenge),
        ]
        guard let url = components.url else {
            throw OAuthError.invalidURL
        }
        return url
    }

    nonisolated static func makePKCEPair() -> (verifier: String, challenge: String) {
        var generator = SystemRandomNumberGenerator()
        let bytes = (0..<32).map { _ in UInt8.random(in: .min ... .max, using: &generator) }
        let verifier = base64URLEncoded(Data(bytes))
        let digest = SHA256.hash(data: Data(verifier.utf8))
        return (verifier, base64URLEncoded(Data(digest)))
    }

    nonisolated private static func base64URLEncoded(_ data: Data) -> String {
        data.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }

    nonisolated private static func pollForLoginCode(baseURL: URL, handoffID: String) async throws -> String {
        let url = baseURL
            .appendingPathComponent("v1/auth/mobile/handoff")
            .appendingPathComponent(handoffID)

        for _ in 0..<300 {
            do {
                let (data, response) = try await URLSession.shared.data(from: url)
                guard let httpResponse = response as? HTTPURLResponse else {
                    throw OAuthError.missingLoginCode
                }

                if httpResponse.statusCode == 200 {
                    let handoff = try JSONDecoder().decode(MobileHandoffResponse.self, from: data)
                    guard handoff.handoffID == handoffID else {
                        throw OAuthError.handoffRejected("Google sign-in returned an unrelated handoff. Please try again.")
                    }
                    switch handoff.status.lowercased() {
                    case "ready":
                        guard let loginCode = handoff.loginCode, !loginCode.isEmpty else {
                            throw OAuthError.missingLoginCode
                        }
                        return loginCode
                    case "cancelled":
                        throw OAuthError.authenticationCancelled
                    case "failed":
                        throw OAuthError.handoffRejected(handoff.error ?? "Google sign-in failed. Please try again.")
                    default:
                        throw OAuthError.missingLoginCode
                    }
                }

                if httpResponse.statusCode != 202,
                   !isTransientHTTPStatus(httpResponse.statusCode) {
                    throw OAuthError.handoffFailed(httpResponse.statusCode)
                }
            } catch is CancellationError {
                throw CancellationError()
            } catch let error as URLError where isTransientURLFailure(error) {
                // Keep waiting for either the handoff endpoint or app callback.
            }

            try await Task.sleep(nanoseconds: 1_000_000_000)
        }

        throw OAuthError.handoffTimedOut
    }

    nonisolated private static func waitForCallbackLoginCode(handoffID: String) async throws -> String {
        for await notification in NotificationCenter.default.notifications(named: .electronicMailOAuthCallback) {
            guard let url = notification.object as? URL else {
                continue
            }
            guard Self.handoffID(from: url) == handoffID else {
                continue
            }
            do {
                let loginCode = try loginCode(from: url)
                return loginCode
            } catch OAuthError.missingLoginCode {
                continue
            }
        }
        throw OAuthError.missingLoginCode
    }

    nonisolated private static func isTransientHTTPStatus(_ status: Int) -> Bool {
        status == 408 || status == 429 || status >= 500
    }

    nonisolated private static func isTransientURLFailure(_ error: URLError) -> Bool {
        error.code != .cancelled && error.code != .badURL && error.code != .unsupportedURL
    }
}

private struct MobileHandoffResponse: Codable {
    let status: String
    let loginCode: String?
    let handoffID: String?
    let error: String?

    enum CodingKeys: String, CodingKey {
        case status
        case loginCode = "login_code"
        case handoffID = "handoff_id"
        case error
    }
}
