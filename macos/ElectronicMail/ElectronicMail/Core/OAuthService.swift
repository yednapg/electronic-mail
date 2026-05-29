import AuthenticationServices
import AppKit
import Foundation

public protocol OAuthServicing: AnyObject {
    func startGoogleAuthentication(baseURL: URL, authRedirectURI: String) async throws -> String
}

public enum OAuthError: Error, Equatable {
    case invalidURL
    case browserOpenFailed
    case missingLoginCode
    case authenticationCancelled
    case handoffTimedOut
    case handoffFailed(Int)
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

    public func startGoogleAuthenticationWithBrowserHandoff(baseURL: URL) async throws -> String {
        let handoffID = UUID().uuidString
        let redirectURI = try Self.mobileHandoffRedirectURL(baseURL: baseURL, handoffID: handoffID)
        let url = try Self.googleAuthURL(baseURL: baseURL, redirectURI: redirectURI.absoluteString)

        if !NSWorkspace.shared.open(url) {
            throw OAuthError.browserOpenFailed
        }

        let loginCode = try await withThrowingTaskGroup(of: String.self) { group in
            group.addTask {
                try await Self.pollForLoginCode(baseURL: baseURL, handoffID: handoffID)
            }
            group.addTask {
                try await Self.waitForCallbackLoginCode()
            }
            guard let loginCode = try await group.next() else {
                throw OAuthError.missingLoginCode
            }
            group.cancelAll()
            return loginCode
        }
        NSApp.activate(ignoringOtherApps: true)
        return loginCode
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
        guard let components = URLComponents(url: callbackURL, resolvingAgainstBaseURL: false),
              let loginCode = components.queryItems?.first(where: { $0.name == "login_code" })?.value,
              !loginCode.isEmpty
        else {
            throw OAuthError.missingLoginCode
        }
        return loginCode
    }

    nonisolated private static func mobileHandoffRedirectURL(baseURL: URL, handoffID: String) throws -> URL {
        guard var components = URLComponents(url: baseURL.appendingPathComponent("auth/mobile/complete"), resolvingAgainstBaseURL: false) else {
            throw OAuthError.invalidURL
        }
        components.queryItems = [URLQueryItem(name: "handoff_id", value: handoffID)]
        guard let url = components.url else {
            throw OAuthError.invalidURL
        }
        return url
    }

    nonisolated private static func pollForLoginCode(baseURL: URL, handoffID: String) async throws -> String {
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

    nonisolated private static func waitForCallbackLoginCode() async throws -> String {
        for await notification in NotificationCenter.default.notifications(named: .electronicMailOAuthCallback) {
            guard let url = notification.object as? URL else {
                continue
            }
            if let loginCode = try? loginCode(from: url) {
                return loginCode
            }
        }
        throw OAuthError.missingLoginCode
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
