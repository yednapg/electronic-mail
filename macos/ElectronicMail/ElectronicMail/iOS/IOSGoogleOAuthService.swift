import AuthenticationServices
import ElectronicMailShared
import UIKit

enum IOSGoogleOAuthError: LocalizedError {
    case browserStartFailed
    case browserOpenFailed
    case cancelled

    var errorDescription: String? {
        switch self {
        case .browserStartFailed:
            return "Could not start Google sign-in."
        case .browserOpenFailed:
            return "Could not open Google sign-in."
        case .cancelled:
            return "Google sign-in was cancelled."
        }
    }
}

@MainActor
final class IOSGoogleOAuthService: NSObject, ASWebAuthenticationPresentationContextProviding {
    private var webSession: ASWebAuthenticationSession?

    func startGoogleAuthentication(baseURL: URL) async throws -> String {
        do {
            return try await authenticateWithCallback(baseURL: baseURL)
        } catch IOSGoogleOAuthError.cancelled {
            throw IOSGoogleOAuthError.cancelled
        } catch {
            return try await authenticateWithHandoffFallback(baseURL: baseURL)
        }
    }

    func startGoogleAccountLink(authorizationURL: URL) async throws -> String {
        try await withCheckedThrowingContinuation { continuation in
            let session = ASWebAuthenticationSession(
                url: authorizationURL,
                callbackURLScheme: MobileAuthFlow.callbackScheme
            ) { callbackURL, error in
                self.webSession = nil
                if let error = error as? ASWebAuthenticationSessionError,
                   error.code == .canceledLogin {
                    continuation.resume(throwing: IOSGoogleOAuthError.cancelled)
                    return
                }
                if let error {
                    continuation.resume(throwing: error)
                    return
                }
                guard let callbackURL else {
                    continuation.resume(throwing: GmailAccountLinkError.missingAccountID)
                    return
                }
                do {
                    continuation.resume(
                        returning: try GmailAccountLinkError.linkedAccountID(from: callbackURL)
                    )
                } catch {
                    continuation.resume(throwing: error)
                }
            }
            session.presentationContextProvider = self
            session.prefersEphemeralWebBrowserSession = false
            webSession = session
            guard session.start() else {
                webSession = nil
                continuation.resume(throwing: IOSGoogleOAuthError.browserStartFailed)
                return
            }
        }
    }

    private func authenticateWithCallback(baseURL: URL) async throws -> String {
        let redirectURL = URL(string: MobileAuthFlow.callbackRedirectURI)!
        let url = try MobileAuthFlow.authenticationURL(baseURL: baseURL, redirectURL: redirectURL)

        return try await withCheckedThrowingContinuation { continuation in
            let session = ASWebAuthenticationSession(
                url: url,
                callbackURLScheme: MobileAuthFlow.callbackScheme
            ) { callbackURL, error in
                self.webSession = nil

                if let callbackURL {
                    do {
                        continuation.resume(returning: try MobileAuthFlow.loginCode(from: callbackURL))
                    } catch {
                        continuation.resume(throwing: error)
                    }
                    return
                }

                if let error = error as? ASWebAuthenticationSessionError,
                   error.code == .canceledLogin {
                    continuation.resume(throwing: IOSGoogleOAuthError.cancelled)
                    return
                }

                continuation.resume(throwing: error ?? MobileAuthFlowError.missingLoginCode)
            }

            session.presentationContextProvider = self
            session.prefersEphemeralWebBrowserSession = false
            webSession = session

            guard session.start() else {
                webSession = nil
                continuation.resume(throwing: IOSGoogleOAuthError.browserStartFailed)
                return
            }
        }
    }

    private func authenticateWithHandoffFallback(baseURL: URL) async throws -> String {
        let handoffID = UUID().uuidString
        let redirectURL = try MobileAuthFlow.handoffCompletionRedirectURL(baseURL: baseURL, handoffID: handoffID)
        let url = try MobileAuthFlow.authenticationURL(baseURL: baseURL, redirectURL: redirectURL)

        let opened = await open(url)
        guard opened else {
            throw IOSGoogleOAuthError.browserOpenFailed
        }

        return try await MobileAuthFlow.pollForLoginCode(baseURL: baseURL, handoffID: handoffID)
    }

    private func open(_ url: URL) async -> Bool {
        await withCheckedContinuation { continuation in
            UIApplication.shared.open(url, options: [:]) { success in
                continuation.resume(returning: success)
            }
        }
    }

    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .flatMap(\.windows)
            .first { $0.isKeyWindow } ?? ASPresentationAnchor()
    }
}
