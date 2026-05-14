import AuthenticationServices
import Foundation
import UIKit

public protocol OAuthServicing: AnyObject {
    func startGoogleAuthentication(baseURL: URL, mobileRedirectURI: String) async throws -> String
}

public enum OAuthError: Error, Equatable {
    case invalidURL
    case missingPresentationAnchor
    case missingLoginCode
}

@MainActor
public final class GoogleOAuthService: NSObject, OAuthServicing, ASWebAuthenticationPresentationContextProviding {
    private var session: ASWebAuthenticationSession?

    public override init() {
        super.init()
    }

    public func startGoogleAuthentication(baseURL: URL, mobileRedirectURI: String) async throws -> String {
        guard var components = URLComponents(url: baseURL.appendingPathComponent("auth/google"), resolvingAgainstBaseURL: false) else {
            throw OAuthError.invalidURL
        }

        components.queryItems = [
            URLQueryItem(name: "redirect_to", value: mobileRedirectURI)
        ]

        guard let url = components.url else {
            throw OAuthError.invalidURL
        }

        guard let callbackScheme = URLComponents(string: mobileRedirectURI)?.scheme else {
            throw OAuthError.invalidURL
        }

        return try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<String, Error>) in
            var didResume = false
            func resumeOnce(_ result: Result<String, Error>) {
                guard !didResume else {
                    return
                }

                didResume = true

                Task { @MainActor in
                    self.session = nil

                    switch result {
                    case .success(let loginCode):
                        continuation.resume(returning: loginCode)
                    case .failure(let error):
                        continuation.resume(throwing: error)
                    }
                }
            }

            let authSession = ASWebAuthenticationSession(
                url: url,
                callbackURLScheme: callbackScheme
            ) { callbackURL, error in
                if let error {
                    resumeOnce(.failure(error))
                } else if let callbackURL, let loginCode = Self.loginCode(from: callbackURL) {
                    resumeOnce(.success(loginCode))
                } else {
                    resumeOnce(.failure(OAuthError.missingLoginCode))
                }
            }

            authSession.presentationContextProvider = self
            authSession.prefersEphemeralWebBrowserSession = false
            self.session = authSession

            if !authSession.start() {
                resumeOnce(.failure(OAuthError.missingPresentationAnchor))
            }
        }
    }

    public func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        let window = scenes.flatMap(\.windows).first { $0.isKeyWindow }
        return window ?? ASPresentationAnchor()
    }

    private static func loginCode(from url: URL) -> String? {
        URLComponents(url: url, resolvingAgainstBaseURL: false)?
            .queryItems?
            .first { $0.name == "login_code" }?
            .value
    }
}
