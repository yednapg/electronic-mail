import Foundation
import WebKit

struct LoadedRemoteImage: Sendable {
    let data: Data
    let mimeType: String
    let etag: String?
}

/// Authenticated privacy-image client shared by ephemeral reader web views.
actor EmailRemoteImageLoader {
    static let shared = EmailRemoteImageLoader()

    private struct ActiveLoad {
        let userID: String
        let userGeneration: UInt64
        let globalGeneration: UInt64
        let task: Task<LoadedRemoteImage, Error>
    }

    private let cache: EncryptedRemoteImageCache
    private let session: URLSession
    private var baseURL: URL?
    private var sessionToken: String?
    /// Identity allowed to decrypt account-scoped bytes. This survives an
    /// offline relaunch even though there is no usable bearer token yet.
    private var cacheUserID: String?
    /// Identity most recently verified by `/v1/app/session`. Network cache
    /// misses are allowed only when it matches `cacheUserID`.
    private var networkUserID: String?
    private var configurationRevision: UInt64 = 0
    private var inFlight: [String: ActiveLoad] = [:]
    private var userGenerations: [String: UInt64] = [:]
    private var globalGeneration: UInt64 = 0
    private var blockedUsers: Set<String> = []
    private var allUsersBlocked = false

    init(
        session: URLSession? = nil,
        cache: EncryptedRemoteImageCache = EncryptedRemoteImageCache()
    ) {
        self.cache = cache
        if let session {
            self.session = session
        } else {
            let configuration = URLSessionConfiguration.ephemeral
            configuration.urlCache = nil
            configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
            configuration.urlCredentialStorage = nil
            configuration.httpCookieStorage = nil
            configuration.httpShouldSetCookies = false
            self.session = URLSession(configuration: configuration)
        }
    }

    func configure(
        baseURL: URL,
        sessionToken: String?,
        cacheUserID: String?,
        networkUserID: String?,
        configurationRevision: UInt64
    ) {
        guard configurationRevision >= self.configurationRevision else {
            return
        }
        self.configurationRevision = configurationRevision
        self.baseURL = baseURL
        self.sessionToken = sessionToken
        self.cacheUserID = cacheUserID
        self.networkUserID = networkUserID
        if let networkUserID,
           networkUserID == cacheUserID,
           sessionToken?.isEmpty == false {
            allUsersBlocked = false
            blockedUsers.remove(networkUserID)
        }
    }

    func load(assetID: String) async throws -> LoadedRemoteImage {
        guard let userID = cacheUserID,
              !userID.isEmpty,
              !allUsersBlocked,
              !blockedUsers.contains(userID) else {
            throw URLError(.userAuthenticationRequired)
        }
        let userGeneration = userGenerations[userID, default: 0]
        let capturedGlobalGeneration = globalGeneration
        if let cached = await cache.read(userID: userID, assetID: assetID),
           let mimeType = cached.mimeType {
            try Task.checkCancellation()
            guard isCurrent(
                userID: userID,
                userGeneration: userGeneration,
                globalGeneration: capturedGlobalGeneration
            ) else {
                throw CancellationError()
            }
            return LoadedRemoteImage(data: cached.data, mimeType: mimeType, etag: cached.etag)
        }
        guard let baseURL,
              let sessionToken,
              !sessionToken.isEmpty,
              networkUserID == userID else {
            // Cached bytes remain available offline, but a miss must never
            // escape to the network until the backend verifies this account.
            throw URLError(.userAuthenticationRequired)
        }
        let requestIdentity = "\(userID)\u{0}\(assetID)"
        if let existing = inFlight[requestIdentity] {
            let loaded = try await existing.task.value
            try Task.checkCancellation()
            guard isCurrent(
                userID: userID,
                userGeneration: userGeneration,
                globalGeneration: capturedGlobalGeneration
            ) else {
                throw CancellationError()
            }
            return loaded
        }
        let task = Task { [weak self] () throws -> LoadedRemoteImage in
            guard let self else { throw CancellationError() }
            return try await self.fetchAndCache(
                assetID: assetID,
                baseURL: baseURL,
                sessionToken: sessionToken,
                userID: userID,
                userGeneration: userGeneration,
                globalGeneration: capturedGlobalGeneration
            )
        }
        inFlight[requestIdentity] = ActiveLoad(
            userID: userID,
            userGeneration: userGeneration,
            globalGeneration: capturedGlobalGeneration,
            task: task
        )
        do {
            let loaded = try await task.value
            try Task.checkCancellation()
            guard isCurrent(
                userID: userID,
                userGeneration: userGeneration,
                globalGeneration: capturedGlobalGeneration
            ) else {
                throw CancellationError()
            }
            complete(
                requestIdentity: requestIdentity,
                userGeneration: userGeneration,
                globalGeneration: capturedGlobalGeneration
            )
            return loaded
        } catch {
            complete(
                requestIdentity: requestIdentity,
                userGeneration: userGeneration,
                globalGeneration: capturedGlobalGeneration
            )
            throw error
        }
    }

    private func fetchAndCache(
        assetID: String,
        baseURL: URL,
        sessionToken: String,
        userID: String,
        userGeneration: UInt64,
        globalGeneration: UInt64
    ) async throws -> LoadedRemoteImage {
        guard let url = URL(
            string: "/v1/mailbox/remote-images/\(assetID.urlPathComponentEncoded)",
            relativeTo: baseURL
        )?.absoluteURL else {
            throw APIError.invalidURL
        }
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 15)
        request.httpMethod = "GET"
        request.setValue("image/avif,image/webp,image/*", forHTTPHeaderField: "Accept")
        request.setValue("Bearer \(sessionToken)", forHTTPHeaderField: "Authorization")
        request.setValue("no-referrer", forHTTPHeaderField: "Referrer-Policy")

        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.emptyResponse
        }
        guard (200 ..< 300).contains(httpResponse.statusCode) else {
            throw APIError.httpStatus(httpResponse.statusCode)
        }
        guard data.count <= 5 * 1_024 * 1_024 else {
            throw APIError.httpStatus(413)
        }
        let mimeType = (httpResponse.value(forHTTPHeaderField: "Content-Type") ?? "")
            .split(separator: ";", maxSplits: 1)
            .first
            .map(String.init)?
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased() ?? ""
        guard mimeType.hasPrefix("image/"), mimeType != "image/svg+xml" else {
            throw URLError(.cannotDecodeContentData)
        }
        try Task.checkCancellation()
        guard isCurrent(
            userID: userID,
            userGeneration: userGeneration,
            globalGeneration: globalGeneration
        ) else {
            throw CancellationError()
        }
        let etag = httpResponse.value(forHTTPHeaderField: "ETag")
        do {
            try await cache.write(
                CachedMediaPayload(data: data, mimeType: mimeType, filename: nil, etag: etag),
                userID: userID,
                assetID: assetID
            )
        } catch {
            await MainActor.run {
                NotificationCenter.default.post(
                    name: .offlineContentSyncStorageWarning,
                    object: nil,
                    userInfo: [
                        "user_id": userID,
                        "message": "Email images could not be cached. They will continue to load online.",
                    ]
                )
            }
        }
        try Task.checkCancellation()
        guard isCurrent(
            userID: userID,
            userGeneration: userGeneration,
            globalGeneration: globalGeneration
        ) else {
            throw CancellationError()
        }
        return LoadedRemoteImage(data: data, mimeType: mimeType, etag: etag)
    }

    func purge(userID: String?) async {
        let matching: [(String, ActiveLoad)]
        if let userID {
            blockedUsers.insert(userID)
            userGenerations[userID, default: 0] &+= 1
            matching = inFlight.filter { $0.value.userID == userID }
        } else {
            allUsersBlocked = true
            globalGeneration &+= 1
            matching = Array(inFlight)
        }
        if cacheUserID == userID || userID == nil {
            sessionToken = nil
            cacheUserID = nil
        }
        if networkUserID == userID || userID == nil {
            networkUserID = nil
        }
        for (_, active) in matching {
            active.task.cancel()
        }
        for (_, active) in matching {
            _ = try? await active.task.value
        }
        for (identity, active) in matching {
            guard let current = inFlight[identity],
                  current.userGeneration == active.userGeneration,
                  current.globalGeneration == active.globalGeneration else {
                continue
            }
            inFlight[identity] = nil
        }
        if let userID {
            await cache.purge(userID: userID)
        } else {
            await cache.purgeAll()
        }
    }

    /// Cancels authenticated network work for a credential transition while
    /// retaining encrypted bytes for offline reading and reauthorization.
    func deactivateNetwork(userID: String?) async {
        let matching: [(String, ActiveLoad)]
        if let userID {
            userGenerations[userID, default: 0] &+= 1
            matching = inFlight.filter { $0.value.userID == userID }
            if networkUserID == userID {
                networkUserID = nil
                sessionToken = nil
            }
        } else {
            globalGeneration &+= 1
            matching = Array(inFlight)
            networkUserID = nil
            sessionToken = nil
        }
        for (_, active) in matching {
            active.task.cancel()
        }
        for (_, active) in matching {
            _ = try? await active.task.value
        }
        for (identity, active) in matching {
            guard let current = inFlight[identity],
                  current.userGeneration == active.userGeneration,
                  current.globalGeneration == active.globalGeneration else {
                continue
            }
            inFlight[identity] = nil
        }
    }

    private func isCurrent(
        userID: String,
        userGeneration: UInt64,
        globalGeneration: UInt64
    ) -> Bool {
        !allUsersBlocked
            && !blockedUsers.contains(userID)
            && cacheUserID == userID
            && self.globalGeneration == globalGeneration
            && userGenerations[userID, default: 0] == userGeneration
    }

    private func complete(
        requestIdentity: String,
        userGeneration: UInt64,
        globalGeneration: UInt64
    ) {
        guard let active = inFlight[requestIdentity],
              active.userGeneration == userGeneration,
              active.globalGeneration == globalGeneration else {
            return
        }
        inFlight[requestIdentity] = nil
    }
}

public final class EmailRemoteImageSchemeHandler: NSObject, WKURLSchemeHandler {
    public static let scheme = "electronicmail-image"

    private let lock = NSLock()
    private var tasks: [ObjectIdentifier: Task<Void, Never>] = [:]

    public override init() {
        super.init()
    }

    public func webView(_ webView: WKWebView, start urlSchemeTask: WKURLSchemeTask) {
        let identifier = ObjectIdentifier(urlSchemeTask as AnyObject)
        let task = Task { [weak self, weak urlSchemeTask] in
            guard let self,
                  let urlSchemeTask,
                  let url = urlSchemeTask.request.url,
                  url.scheme?.lowercased() == Self.scheme,
                  url.host?.lowercased() == "asset" else {
                return
            }
            let assetID = url.path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
            guard !assetID.isEmpty else {
                finish(
                    urlSchemeTask,
                    identifier: identifier,
                    result: .failure(URLError(.badURL))
                )
                return
            }
            do {
                let image = try await EmailRemoteImageLoader.shared.load(assetID: assetID)
                finish(urlSchemeTask, identifier: identifier, result: .success((url, image)))
            } catch {
                finish(urlSchemeTask, identifier: identifier, result: .failure(error))
            }
        }
        lock.withMediaCacheLock {
            tasks[identifier] = task
        }
    }

    public func webView(_ webView: WKWebView, stop urlSchemeTask: WKURLSchemeTask) {
        let identifier = ObjectIdentifier(urlSchemeTask as AnyObject)
        let task = lock.withMediaCacheLock { tasks.removeValue(forKey: identifier) }
        task?.cancel()
    }

    @MainActor
    private func finish(
        _ schemeTask: WKURLSchemeTask,
        identifier: ObjectIdentifier,
        result: Result<(URL, LoadedRemoteImage), Error>
    ) {
        let wasActive = lock.withMediaCacheLock { tasks.removeValue(forKey: identifier) != nil }
        guard wasActive else { return }
        switch result {
        case .success(let (url, image)):
            let response = URLResponse(
                url: url,
                mimeType: image.mimeType,
                expectedContentLength: image.data.count,
                textEncodingName: nil
            )
            schemeTask.didReceive(response)
            schemeTask.didReceive(image.data)
            schemeTask.didFinish()
        case .failure(let error):
            schemeTask.didFailWithError(error)
        }
    }
}

// MARK: - Reader network isolation

/// Content rules that forbid every network load from a reader web view except
/// the authenticated privacy-image scheme and inline `data:` payloads.
///
/// The backend rewrites remote sources to `electronicmail-image://` tokens and
/// the reader document carries a meta CSP, but both depend on interpreting the
/// sender's markup the same way WebKit does. Content rules are evaluated by
/// WebKit itself for every resource load regardless of how it was expressed,
/// so they are the layer that must be in place before any sender HTML loads.
public enum EmailReaderNetworkRules {
    /// Bump whenever `encodedRules` changes; the rule store caches by identifier.
    public static let identifier = "electronicmail-reader-network-isolation-v1"

    public static let encodedRules = """
    [
      {"trigger":{"url-filter":".*"},"action":{"type":"block"}},
      {"trigger":{"url-filter":"^about:blank$"},"action":{"type":"ignore-previous-rules"}},
      {"trigger":{"url-filter":"^electronicmail-image://"},"action":{"type":"ignore-previous-rules"}},
      {"trigger":{"url-filter":"^data:"},"action":{"type":"ignore-previous-rules"}}
    ]
    """
}

/// Compiles the reader rules once per process and hands the same list to every
/// reader web view. Callers must not load sender HTML until this succeeds; a
/// compilation failure is reported so the reader can fall back to plain text.
@MainActor
public final class EmailReaderNetworkIsolation {
    public static let shared = EmailReaderNetworkIsolation()

    public enum Failure: Error, Equatable {
        case storeUnavailable
        case compilationProducedNoRules
    }

    private let identifier: String
    private let encodedRules: String
    private let store: WKContentRuleListStore?
    private var compiled: WKContentRuleList?
    private var compiling = false
    private var waiters: [CheckedContinuation<WKContentRuleList, Error>] = []

    public init(
        identifier: String = EmailReaderNetworkRules.identifier,
        encodedRules: String = EmailReaderNetworkRules.encodedRules,
        store: WKContentRuleListStore? = WKContentRuleListStore.default()
    ) {
        self.identifier = identifier
        self.encodedRules = encodedRules
        self.store = store
    }

    /// Starts compilation early so the first opened message does not wait.
    public func warmUp() {
        Task { @MainActor in
            _ = try? await ruleList()
        }
    }

    public func ruleList() async throws -> WKContentRuleList {
        if let compiled {
            return compiled
        }
        return try await withCheckedThrowingContinuation { continuation in
            waiters.append(continuation)
            guard !compiling else {
                return
            }
            compiling = true
            guard let store else {
                finishCompilation(.failure(Failure.storeUnavailable))
                return
            }
            // Always recompile instead of looking up a cached list: a cached
            // entry compiled by an older build could silently carry stale rules
            // under the same identifier if a bump were ever forgotten.
            store.compileContentRuleList(
                forIdentifier: identifier,
                encodedContentRuleList: encodedRules
            ) { [weak self] ruleList, error in
                DispatchQueue.main.async {
                    MainActor.assumeIsolated {
                        guard let self else {
                            return
                        }
                        if let error {
                            self.finishCompilation(.failure(error))
                        } else if let ruleList {
                            self.finishCompilation(.success(ruleList))
                        } else {
                            self.finishCompilation(.failure(Failure.compilationProducedNoRules))
                        }
                    }
                }
            }
        }
    }

    private func finishCompilation(_ result: Result<WKContentRuleList, Error>) {
        compiling = false
        if case .success(let ruleList) = result {
            compiled = ruleList
        }
        let pending = waiters
        waiters = []
        for waiter in pending {
            waiter.resume(with: result)
        }
    }
}

/// Main-frame navigations a reader web view may perform on its own. Sender
/// HTML is always loaded with `loadHTMLString(_:baseURL: nil)`, which WebKit
/// reports as `about:blank`; anything else (a meta refresh that survived
/// sanitization, a form, a stray frame) is cancelled.
public enum EmailReaderNavigationPolicy {
    public static func allowsDocumentLoad(_ url: URL?) -> Bool {
        guard let url else {
            return true
        }
        return url.scheme?.lowercased() == "about"
    }
}

/// Document-level CSP applied before sender HTML reaches WebKit. This is a
/// second, independent statement of the same policy the content rules enforce.
public enum EmailRemoteImagePolicy {
    public static func renderDocument(from html: String) -> String {
        let policy = "default-src 'none'; img-src electronicmail-image: data: cid:; style-src 'unsafe-inline'; font-src data:; media-src data:; frame-src 'none'; script-src 'none'"
        let meta = #"<meta http-equiv="Content-Security-Policy" content="\#(policy)">"#
        if let headRange = html.range(of: #"(?i)<head(?:\s[^>]*)?>"#, options: .regularExpression) {
            var result = html
            result.insert(contentsOf: meta, at: headRange.upperBound)
            return result
        }
        return "<html><head>\(meta)</head><body>\(html)</body></html>"
    }
}

private extension String {
    var urlPathComponentEncoded: String {
        var allowed = CharacterSet.urlPathAllowed
        allowed.remove(charactersIn: "/")
        return addingPercentEncoding(withAllowedCharacters: allowed) ?? self
    }
}

private extension NSLock {
    func withMediaCacheLock<T>(_ body: () -> T) -> T {
        lock()
        defer { unlock() }
        return body()
    }
}
