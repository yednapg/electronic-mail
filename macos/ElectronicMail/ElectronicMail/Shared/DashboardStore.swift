import Foundation

public enum DashboardLoadPhase: Equatable {
    case idle
    case loading
    case loaded
    case failed(String)
}

public enum DashboardActionState: Equatable {
    case idle
    case loading
    case done
    case failed(String)
}

public struct ThreadDetailViewModel: Equatable, Identifiable {
    public let id: String
    public let title: String
    public let summary: String?
    public let sourceLabel: String
    public let messages: [ThreadMessageViewModel]
}

public struct ThreadMessageViewModel: Equatable, Identifiable {
    public let id: String
    public let sender: String
    public let receivedAt: String
    public let subject: String
    public let body: String
    public let snippet: String?
}

@MainActor
public final class DashboardStore: ObservableObject {
    @Published public private(set) var phase: DashboardLoadPhase = .idle
    @Published public private(set) var snapshot: DashboardSnapshot?
    @Published public private(set) var inboxSnapshot: MobileInboxSnapshot?
    @Published public private(set) var refreshFailed = false
    @Published public private(set) var selectedThread: ThreadDetailViewModel?
    @Published public private(set) var selectedItemID: String?
    @Published public private(set) var actionStates: [String: DashboardActionState] = [:]
    @Published public private(set) var syncing = false

    private let client: AppClient
    private let sessionCache: AppSessionCache
    private let threadCache: ThreadCache
    private let now: () -> Date

    private var session: AppSessionResponse?
    private var hiddenItemIDs: Set<String> = []
    private var inFlightSessionRefresh: Task<AppSessionResponse, Error>?
    private var inFlightThreads: [String: Task<ThreadReaderResponse, Error>] = [:]

    public init(
        client: AppClient = LiveBackendAppClient(baseURL: AppConfiguration.defaultBackendURL),
        sessionCache: AppSessionCache = AppSessionCache(),
        threadCache: ThreadCache = ThreadCache(),
        now: @escaping () -> Date = Date.init
    ) {
        self.client = client
        self.sessionCache = sessionCache
        self.threadCache = threadCache
        self.now = now
    }

    public var backendURL: URL {
        client.baseURL
    }

    public var hasSessionToken: Bool {
        client.sessionToken?.isEmpty == false
    }

    public func setSessionToken(_ token: String?) {
        client.sessionToken = token
        if token == nil {
            session = nil
            snapshot = nil
            inboxSnapshot = nil
            selectedThread = nil
            selectedItemID = nil
            refreshFailed = false
            hiddenItemIDs = []
            actionStates = [:]
            sessionCache.clear()
            threadCache.clearMemory()
            phase = .idle
        }
    }

    public func exchangeMobileSession(loginCode: String) async throws -> MobileSessionExchangeResponse {
        let response = try await client.exchangeMobileSession(loginCode: loginCode)
        setSessionToken(response.sessionToken)
        return response
    }

    public func load() async {
        guard hasSessionToken else {
            phase = .failed("Sign in with Google to load your dashboard.")
            return
        }

        if let cached = sessionCache.read() {
            session = cached
            rebuildSnapshot(refreshWarning: nil)
            phase = .loaded
        } else {
            phase = .loading
        }

        await refresh()
    }

    public func refresh() async {
        guard hasSessionToken else {
            phase = .failed("Sign in with Google to load your dashboard.")
            return
        }

        do {
            let task = inFlightSessionRefresh ?? Task { [client] in
                try await client.appSession()
            }
            inFlightSessionRefresh = task
            let next = try await task.value
            inFlightSessionRefresh = nil

            let merged = sessionCache.merge(current: session ?? sessionCache.read(), next: next)
            session = merged
            sessionCache.write(merged)
            refreshFailed = false
            rebuildSnapshot(refreshWarning: nil)
            phase = .loaded
        } catch is CancellationError {
            inFlightSessionRefresh = nil
        } catch {
            inFlightSessionRefresh = nil
            refreshFailed = true
            if session == nil, let cached = sessionCache.read() {
                session = cached
                rebuildSnapshot(refreshWarning: "Dashboard could not refresh. Showing last saved state.")
                phase = .loaded
            } else if session != nil {
                rebuildSnapshot(refreshWarning: "Dashboard could not refresh. Showing last saved state.")
                phase = .loaded
            } else {
                phase = .failed(error.localizedDescription)
            }
        }
    }

    public func triggerSyncAndRefresh() async {
        guard hasSessionToken else {
            return
        }
        syncing = true
        defer { syncing = false }

        do {
            _ = try await client.triggerMailboxSync()
        } catch {}

        await refresh()
    }

    public func dismissItem(_ itemID: String) {
        hiddenItemIDs.insert(itemID)
        actionStates[itemID] = .done
        rebuildSnapshot(refreshWarning: refreshFailed ? "Dashboard could not refresh. Showing last saved state." : nil)
    }

    public func openThread(for item: DashboardSectionItemViewModel) async {
        selectedItemID = item.id
        await openThread(entityID: item.entityID)
    }

    public func openThread(for row: MobileInboxRowViewModel) async {
        selectedItemID = row.id
        await openThread(entityID: row.id)
    }

    public func clearSelectedThread() {
        selectedThread = nil
        selectedItemID = nil
    }

    public func performAction(_ action: DashboardItemActionViewModel, for itemID: String) async {
        guard let operation = action.operation, let threadID = action.gmailThreadID, !threadID.isEmpty else {
            return
        }

        actionStates[itemID] = .loading

        do {
            switch operation {
            case .archive:
                _ = try await client.archiveThread(threadID)
            case .unarchive:
                _ = try await client.unarchiveThread(threadID)
            case .markRead:
                _ = try await client.markThreadRead(threadID)
            }

            actionStates[itemID] = .done
            hiddenItemIDs.insert(itemID)
            rebuildSnapshot(refreshWarning: nil)
            await refresh()
        } catch {
            actionStates[itemID] = .failed(error.localizedDescription)
        }
    }

    public func logout() async {
        await BackendSessionService.logout(baseURL: client.baseURL, sessionToken: client.sessionToken)
        setSessionToken(nil)
    }

    private func openThread(entityID: String) async {
        guard let userID = session?.user.id else {
            return
        }

        if let cached = threadCache.read(userID: userID, threadID: entityID) {
            selectedThread = ThreadDetailViewModel(response: cached)
            return
        }

        if let inFlight = inFlightThreads[entityID] {
            if let response = try? await inFlight.value {
                selectedThread = ThreadDetailViewModel(response: response)
            }
            return
        }

        let task = Task { [client] in
            try await client.thread(threadID: entityID, limit: 50, offset: 0)
        }
        inFlightThreads[entityID] = task

        do {
            let response = try await task.value
            inFlightThreads[entityID] = nil
            threadCache.write(response, userID: userID, threadID: entityID)
            selectedThread = ThreadDetailViewModel(response: response)
        } catch {
            inFlightThreads[entityID] = nil
        }
    }

    private func rebuildSnapshot(refreshWarning: String?) {
        guard let session else {
            snapshot = nil
            inboxSnapshot = nil
            return
        }

        snapshot = DashboardViewModelBuilder.snapshot(
            from: session,
            now: now(),
            hiddenItemIDs: hiddenItemIDs,
            refreshWarning: refreshWarning
        )
        inboxSnapshot = MobileInboxViewModelBuilder.snapshot(from: session.mailbox)
    }
}

public enum BackendSessionService {
    public static func logout(baseURL: URL, sessionToken: String?) async {
        guard let url = URL(string: "/v1/auth/logout", relativeTo: baseURL)?.absoluteURL else {
            return
        }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let sessionToken, !sessionToken.isEmpty {
            request.setValue("Bearer \(sessionToken)", forHTTPHeaderField: "Authorization")
        }

        _ = try? await URLSession.shared.data(for: request)
    }
}

private extension ThreadReaderResponse {
    func mapToDetail() -> ThreadDetailViewModel {
        ThreadDetailViewModel(response: self)
    }
}

private extension ThreadDetailViewModel {
    init(response: ThreadReaderResponse) {
        self.init(
            id: response.entityID,
            title: response.title ?? response.subject ?? "Email thread",
            summary: response.summary,
            sourceLabel: response.source?.rawValue.capitalized ?? "Email",
            messages: response.messages.map { ThreadMessageViewModel(message: $0) }
        )
    }
}

private extension ThreadMessageViewModel {
    init(message: ThreadMessage) {
        self.init(
            id: message.id,
            sender: message.fromAddress ?? "Unknown sender",
            receivedAt: message.receivedAt,
            subject: message.subject ?? "No subject",
            body: message.body,
            snippet: message.snippet
        )
    }
}
