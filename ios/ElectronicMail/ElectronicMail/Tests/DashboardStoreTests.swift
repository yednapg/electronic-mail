import XCTest
@testable import ElectronicMailCore

@MainActor
final class DashboardStoreTests: XCTestCase {
    func testRefreshLoadsDashboard() async {
        let client = MockDashboardAPIClient()
        client.dashboardResponse = .fixture(connected: true)
        let store = DashboardStore(apiClient: client, oauthService: MockOAuthService(), sessionTokenStore: InMemorySessionTokenStore())

        await store.refresh()

        XCTAssertEqual(store.dashboard?.profile?.email, "person@example.com")
        XCTAssertEqual(store.loadState, .loaded)
    }

    func testConnectRunsOAuthThenRefreshesDashboard() async {
        let client = MockDashboardAPIClient()
        client.dashboardResponse = .fixture(connected: true)
        let oauth = MockOAuthService()
        let tokenStore = InMemorySessionTokenStore()
        let store = DashboardStore(apiClient: client, oauthService: oauth, sessionTokenStore: tokenStore)

        await store.connectGoogle()

        XCTAssertTrue(oauth.didStart)
        XCTAssertEqual(client.sessionToken, "ios-session-token")
        XCTAssertEqual(tokenStore.token, "ios-session-token")
        XCTAssertEqual(store.dashboard?.auth.connected, true)
    }

    func testArchiveActionCallsClientAndRefreshes() async {
        let item = AttentionItem.fixture(gmailThreadAction: .archive)
        let client = MockDashboardAPIClient()
        client.dashboardResponse = .fixture(connected: true)
        let store = DashboardStore(apiClient: client, oauthService: MockOAuthService(), sessionTokenStore: InMemorySessionTokenStore())

        await store.runGmailAction(for: item)

        XCTAssertEqual(client.archivedThreadID, "thread-1")
        XCTAssertEqual(store.actionStates[item.id], .done)
        XCTAssertEqual(client.dashboardCallCount, 1)
    }
}

final class MockDashboardAPIClient: DashboardAPIProviding {
    var baseURL = URL(string: "http://localhost:3001")!
    var sessionToken: String?
    var dashboardResponse = DashboardResponse.fixture(connected: false)
    var dashboardCallCount = 0
    var archivedThreadID: String?
    var unarchivedThreadID: String?

    func health() async throws {}

    func exchangeMobileLoginCode(_ loginCode: String) async throws -> MobileSessionExchangeResponse {
        MobileSessionExchangeResponse(
            sessionToken: "ios-session-token",
            expiresAt: "2026-05-14T00:00:00+00:00",
            user: AuthUserResponse(id: "user-1", email: "person@example.com", displayName: "Person", accessEnabled: true)
        )
    }

    func dashboard() async throws -> DashboardResponse {
        dashboardCallCount += 1
        return dashboardResponse
    }

    func trace(entityID: String) async throws -> TraceReplayResponse {
        TraceReplayResponse(entityID: entityID, sourceRecordIDs: [], items: [])
    }

    func createTask(_ request: TaskCreateRequest) async throws -> TaskResponse {
        TaskResponse(
            id: "task-1",
            userID: "google-dev-user",
            entityID: "entity-task-1",
            title: request.title,
            notes: request.notes,
            section: request.section ?? "today",
            dueAt: request.dueAt,
            status: "open",
            createdAt: "2026-05-05T00:00:00+00:00",
            updatedAt: "2026-05-05T00:00:00+00:00"
        )
    }

    func completeEntity(_ entityID: String) async throws -> EntityOutcomeResponse {
        EntityOutcomeResponse(id: "outcome-1", userID: "google-dev-user", entityID: entityID, outcomeType: "complete", snoozeUntil: nil, note: nil, createdAt: "2026-05-05T00:00:00+00:00")
    }

    func snoozeEntity(_ entityID: String, until snoozeUntil: String) async throws -> EntityOutcomeResponse {
        EntityOutcomeResponse(id: "outcome-1", userID: "google-dev-user", entityID: entityID, outcomeType: "snooze", snoozeUntil: snoozeUntil, note: nil, createdAt: "2026-05-05T00:00:00+00:00")
    }

    func dismissEntity(_ entityID: String) async throws -> EntityOutcomeResponse {
        EntityOutcomeResponse(id: "outcome-1", userID: "google-dev-user", entityID: entityID, outcomeType: "dismiss", snoozeUntil: nil, note: nil, createdAt: "2026-05-05T00:00:00+00:00")
    }

    func thread(entityID: String) async throws -> ThreadReaderResponse {
        ThreadReaderResponse(entityID: entityID, userID: "google-dev-user", source: nil, gmailThreadID: nil, subject: nil, messages: [])
    }

    func createDraft(_ request: GmailDraftRequest) async throws -> GmailDraftResponse {
        GmailDraftResponse(id: "draft-1", userID: "google-dev-user", entityID: request.entityID, gmailDraftID: "gmail-draft-1", gmailMessageID: nil, threadID: request.threadID, to: request.to, cc: request.cc, bcc: request.bcc, subject: request.subject, body: request.body, status: "draft", createdAt: "2026-05-05T00:00:00+00:00", updatedAt: "2026-05-05T00:00:00+00:00")
    }

    func sendDraft(_ draftID: String) async throws -> GmailDraftResponse {
        GmailDraftResponse(id: draftID, userID: "google-dev-user", entityID: nil, gmailDraftID: "gmail-draft-1", gmailMessageID: "message-1", threadID: nil, to: "to@example.com", cc: nil, bcc: nil, subject: "Subject", body: "Body", status: "sent", createdAt: "2026-05-05T00:00:00+00:00", updatedAt: "2026-05-05T00:00:00+00:00")
    }

    func archiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        archivedThreadID = threadID
        return GmailThreadMutationResponse(threadID: threadID, action: .archive)
    }

    func unarchiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        unarchivedThreadID = threadID
        return GmailThreadMutationResponse(threadID: threadID, action: .unarchive)
    }

    func markThreadRead(_ threadID: String) async throws -> GmailThreadMutationResponse {
        GmailThreadMutationResponse(threadID: threadID, action: .markRead)
    }
}

@MainActor
final class MockOAuthService: OAuthServicing {
    var didStart = false

    func startGoogleAuthentication(baseURL: URL, mobileRedirectURI: String) async throws -> String {
        didStart = true
        return "mobile-login-code"
    }
}

final class InMemorySessionTokenStore: SessionTokenStoring {
    var token: String?

    func load() -> String? {
        token
    }

    func save(_ token: String) throws {
        self.token = token
    }

    func clear() {
        token = nil
    }
}

extension DashboardResponse {
    static func fixture(connected: Bool) -> DashboardResponse {
        DashboardResponse(
            auth: GoogleAuthState(available: true, connected: connected, connectURL: nil),
            profile: DashboardProfile(email: "person@example.com", displayName: "Person"),
            briefing: DashboardBriefing(headline: "Good morning.", brief: "You have one item."),
            feed: FeedResponse(now: [AttentionItem.fixture()], today: [], worthKnowing: [])
        )
    }
}

extension AttentionItem {
    static func fixture(gmailThreadAction: GmailThreadAction? = nil) -> AttentionItem {
        AttentionItem(
            id: "item-1",
            entityID: "entity-1",
            userID: "local-user",
            needType: .awareness,
            actionType: "none",
            effortLevel: "quick",
            timingBand: .now,
            actionConfidence: "high",
            primaryAction: "none",
            fallbackAction: "open",
            title: "A useful status title.",
            whyThisIsHere: "This item matters.",
            detail: nil,
            dueAt: nil,
            importanceLevel: "high",
            lifecycleState: "active",
            currentState: .waiting,
            source: .gmail,
            gmailThreadID: gmailThreadAction == nil ? nil : "thread-1",
            gmailThreadAction: gmailThreadAction,
            traceID: "entity-1",
            createdAt: "2026-04-24T10:00:00+00:00"
        )
    }
}
