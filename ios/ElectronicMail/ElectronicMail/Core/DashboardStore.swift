import Foundation

enum AsyncContentState: Equatable {
    case idle
    case loading
    case loaded
    case failed(String)
}

enum ItemActionState: Equatable {
    case idle
    case loading
    case done
    case failed(String)
}

@MainActor
public final class DashboardStore: ObservableObject {
    @Published private(set) var dashboard: DashboardResponse?
    @Published private(set) var loadState: AsyncContentState = .idle
    @Published private(set) var actionStates: [String: ItemActionState] = [:]
    @Published var backendBaseURLString: String {
        didSet {
            guard let url = URL(string: backendBaseURLString), url.scheme != nil else {
                return
            }

            apiClient.baseURL = url
            UserDefaults.standard.set(backendBaseURLString, forKey: AppConfiguration.backendURLDefaultsKey)
        }
    }

    private let apiClient: DashboardAPIProviding
    private let oauthService: OAuthServicing

    public init(apiClient: DashboardAPIProviding, oauthService: OAuthServicing) {
        self.apiClient = apiClient
        self.oauthService = oauthService
        let savedURL = UserDefaults.standard.string(forKey: AppConfiguration.backendURLDefaultsKey)
        self.backendBaseURLString = savedURL ?? apiClient.baseURL.absoluteString.trimmingCharacters(in: CharacterSet(charactersIn: "/"))

        if let savedURL, let url = URL(string: savedURL) {
            self.apiClient.baseURL = url
        }
    }

    func refresh() async {
        loadState = .loading

        do {
            dashboard = try await apiClient.dashboard()
            loadState = .loaded
        } catch {
            loadState = .failed(error.localizedDescription)
        }
    }

    func connectGoogle() async {
        do {
            try await oauthService.startGoogleAuthentication(
                baseURL: apiClient.baseURL,
                mobileRedirectURI: AppConfiguration.mobileRedirectURI
            )
            await refresh()
        } catch {
            loadState = .failed(error.localizedDescription)
        }
    }

    func runGmailAction(for item: AttentionItem) async {
        guard let threadID = item.gmailThreadID, let action = item.gmailThreadAction else {
            return
        }

        actionStates[item.id] = .loading

        do {
            switch action {
            case .archive:
                _ = try await apiClient.archiveThread(threadID)
            case .unarchive:
                _ = try await apiClient.unarchiveThread(threadID)
            case .markRead:
                _ = try await apiClient.markThreadRead(threadID)
            }

            actionStates[item.id] = .done
            await refresh()
        } catch {
            actionStates[item.id] = .failed(error.localizedDescription)
        }
    }

    func loadTrace(entityID: String) async throws -> TraceReplayResponse {
        try await apiClient.trace(entityID: entityID)
    }

    func saveBackendURL(_ value: String) {
        backendBaseURLString = value.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
