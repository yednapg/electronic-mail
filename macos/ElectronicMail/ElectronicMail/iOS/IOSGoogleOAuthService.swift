import AuthenticationServices
import ElectronicMailShared
import Network
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

    func startGoogleAuthentication(baseURL: URL) async throws -> MobileAuthenticationGrant {
        let loopbackRelay = try await IOSLoopbackOAuthRelay.startIfNeeded(for: baseURL)
        defer { loopbackRelay?.stop() }

        let handoffID = UUID().uuidString
        let pkce = MobileAuthFlow.makePKCEPair()
        let redirectURL = try MobileAuthFlow.handoffCompletionRedirectURL(
            baseURL: IOSLoopbackOAuthRelay.redirectBaseURL(for: baseURL),
            handoffID: handoffID,
            codeChallenge: pkce.challenge
        )
        let authorizationURL = try MobileAuthFlow.authenticationURL(
            baseURL: baseURL,
            redirectURL: redirectURL
        )

        let loginCode: String
        do {
            loginCode = try await authenticateWithCallback(authorizationURL: authorizationURL)
        } catch IOSGoogleOAuthError.cancelled {
            throw IOSGoogleOAuthError.cancelled
        } catch {
            loginCode = try await authenticateWithHandoffFallback(
                authorizationURL: authorizationURL,
                baseURL: baseURL,
                handoffID: handoffID
            )
        }

        return MobileAuthenticationGrant(
            loginCode: loginCode,
            handoffID: handoffID,
            codeVerifier: pkce.verifier
        )
    }

    func startGoogleAccountLink(
        authorizationURL: URL,
        backendBaseURL: URL
    ) async throws -> String {
        let loopbackRelay = try await IOSLoopbackOAuthRelay.startIfNeeded(for: backendBaseURL)
        defer { loopbackRelay?.stop() }

        return try await withCheckedThrowingContinuation { continuation in
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

    private func authenticateWithCallback(authorizationURL: URL) async throws -> String {
        return try await withCheckedThrowingContinuation { continuation in
            let session = ASWebAuthenticationSession(
                url: authorizationURL,
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

    private func authenticateWithHandoffFallback(
        authorizationURL: URL,
        baseURL: URL,
        handoffID: String
    ) async throws -> String {
        let opened = await open(authorizationURL)
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

/// Bridges the OAuth redirect registered for local development back to the
/// Mac-hosted API. It is only active for physical-device builds that use an
/// HTTP LAN address; Simulator localhost and production HTTPS never start it.
final class IOSLoopbackOAuthRelay: @unchecked Sendable {
    private static let callbackPort: UInt16 = 3001
    private static let callbackPath = "/auth/google/callback"

    private let backendBaseURL: URL
    private let queue = DispatchQueue(label: "app.electronicmail.ios.oauth-loopback")
    private var listener: NWListener?

    private init(backendBaseURL: URL) {
        self.backendBaseURL = backendBaseURL
    }

    static func startIfNeeded(for backendBaseURL: URL) async throws -> IOSLoopbackOAuthRelay? {
        guard requiresRelay(for: backendBaseURL) else { return nil }
        let relay = IOSLoopbackOAuthRelay(backendBaseURL: backendBaseURL)
        try await relay.start()
        return relay
    }

    static func requiresRelay(for backendBaseURL: URL) -> Bool {
        guard backendBaseURL.scheme?.lowercased() == "http",
              let host = backendBaseURL.host?.lowercased() else {
            return false
        }
        return host != "localhost" && host != "127.0.0.1" && host != "::1"
    }

    static func redirectBaseURL(for backendBaseURL: URL) -> URL {
        guard requiresRelay(for: backendBaseURL) else { return backendBaseURL }
        return URL(string: "http://localhost:\(callbackPort)")!
    }

    private func start() async throws {
        guard let port = NWEndpoint.Port(rawValue: Self.callbackPort) else {
            throw IOSGoogleOAuthError.browserStartFailed
        }

        let listener = try NWListener(using: .tcp, on: port)
        self.listener = listener
        listener.newConnectionHandler = { [weak self] connection in
            self?.accept(connection)
        }

        try await withCheckedThrowingContinuation { continuation in
            listener.stateUpdateHandler = { state in
                switch state {
                case .ready:
                    listener.stateUpdateHandler = nil
                    continuation.resume()
                case .failed(let error):
                    listener.stateUpdateHandler = nil
                    continuation.resume(throwing: error)
                case .cancelled:
                    listener.stateUpdateHandler = nil
                    continuation.resume(throwing: CancellationError())
                default:
                    break
                }
            }
            listener.start(queue: queue)
        }
    }

    func stop() {
        listener?.cancel()
        listener = nil
    }

    private func accept(_ connection: NWConnection) {
        connection.start(queue: queue)
        receiveRequest(from: connection, accumulated: Data())
    }

    private func receiveRequest(from connection: NWConnection, accumulated: Data) {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 16_384) { [weak self] data, _, isComplete, error in
            guard let self else {
                connection.cancel()
                return
            }

            var request = accumulated
            if let data {
                request.append(data)
            }

            if request.count > 65_536 {
                self.respond(status: "413 Payload Too Large", redirectURL: nil, on: connection)
                return
            }

            if request.range(of: Data("\r\n\r\n".utf8)) != nil || isComplete {
                self.handle(request, on: connection)
                return
            }

            if error != nil {
                connection.cancel()
                return
            }

            self.receiveRequest(from: connection, accumulated: request)
        }
    }

    private func handle(_ request: Data, on connection: NWConnection) {
        guard let text = String(data: request, encoding: .utf8),
              let requestLine = text.components(separatedBy: "\r\n").first,
              requestLine.hasPrefix("GET "),
              let target = requestLine.split(separator: " ", maxSplits: 2).dropFirst().first,
              let incomingURL = URL(string: String(target), relativeTo: URL(string: "http://localhost:\(Self.callbackPort)"))?.absoluteURL,
              let incomingURL = URL(string: String(target), relativeTo: URL(string: "http://localhost:\(Self.callbackPort)"))?.absoluteURL,
              let destinationURL = forwardedDestination(for: incomingURL),
              var destination = URLComponents(url: destinationURL, resolvingAgainstBaseURL: false) else {
            respond(status: "400 Bad Request", redirectURL: nil, on: connection)
            return
        }

        destination.percentEncodedQuery = URLComponents(
            url: incomingURL,
            resolvingAgainstBaseURL: false
        )?.percentEncodedQuery
        respond(status: "302 Found", redirectURL: destination.url, on: connection)
    }

    private func forwardedDestination(for incomingURL: URL) -> URL? {
        switch incomingURL.path {
        case Self.callbackPath:
            return backendBaseURL
                .appendingPathComponent("auth")
                .appendingPathComponent("google")
                .appendingPathComponent("callback")
        case "/auth/mobile/complete":
            return backendBaseURL
                .appendingPathComponent("auth")
                .appendingPathComponent("mobile")
                .appendingPathComponent("complete")
        default:
            return nil
        }
    }

    private func respond(status: String, redirectURL: URL?, on connection: NWConnection) {
        var headers = [
            "HTTP/1.1 \(status)",
            "Cache-Control: no-store",
            "Content-Length: 0",
            "Connection: close",
        ]
        if let redirectURL {
            headers.insert("Location: \(redirectURL.absoluteString)", at: 1)
        }
        let response = Data((headers.joined(separator: "\r\n") + "\r\n\r\n").utf8)
        connection.send(content: response, completion: .contentProcessed { _ in
            connection.cancel()
        })
    }
}
