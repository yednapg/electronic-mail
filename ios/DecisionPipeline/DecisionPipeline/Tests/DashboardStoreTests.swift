import XCTest
@testable import DecisionPipelineCore

@MainActor
final class DashboardStoreTests: XCTestCase {
    func testRefreshLoadsDashboard() async {
        let client = MockDashboardAPIClient()
        client.dashboardResponse = .fixture(connected: true)
        let store = DashboardStore(apiClient: client, oauthService: MockOAuthService())

        await store.refresh()

        XCTAssertEqual(store.dashboard?.profile?.email, "person@example.com")
        XCTAssertEqual(store.loadState, .loaded)
    }

    func testConnectRunsOAuthThenRefreshesDashboard() async {
        let client = MockDashboardAPIClient()
        client.dashboardResponse = .fixture(connected: true)
        let oauth = MockOAuthService()
        let store = DashboardStore(apiClient: client, oauthService: oauth)

        await store.connectGoogle()

        XCTAssertTrue(oauth.didStart)
        XCTAssertEqual(store.dashboard?.auth.connected, true)
    }

    func testArchiveActionCallsClientAndRefreshes() async {
        let item = AttentionItem.fixture(gmailThreadAction: .archive)
        let client = MockDashboardAPIClient()
        client.dashboardResponse = .fixture(connected: true)
        let store = DashboardStore(apiClient: client, oauthService: MockOAuthService())

        await store.runGmailAction(for: item)

        XCTAssertEqual(client.archivedThreadID, "thread-1")
        XCTAssertEqual(store.actionStates[item.id], .done)
        XCTAssertEqual(client.dashboardCallCount, 1)
    }
}

final class MockDashboardAPIClient: DashboardAPIProviding {
    var baseURL = URL(string: "http://localhost:3001")!
    var dashboardResponse = DashboardResponse.fixture(connected: false)
    var dashboardCallCount = 0
    var archivedThreadID: String?
    var unarchivedThreadID: String?

    func health() async throws {}

    func dashboard() async throws -> DashboardResponse {
        dashboardCallCount += 1
        return dashboardResponse
    }

    func trace(entityID: String) async throws -> TraceReplayResponse {
        TraceReplayResponse(entityID: entityID, sourceRecordIDs: [], items: [])
    }

    func archiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        archivedThreadID = threadID
        return GmailThreadMutationResponse(threadID: threadID, action: .archive)
    }

    func unarchiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        unarchivedThreadID = threadID
        return GmailThreadMutationResponse(threadID: threadID, action: .unarchive)
    }
}

@MainActor
final class MockOAuthService: OAuthServicing {
    var didStart = false

    func startGoogleAuthentication(baseURL: URL, mobileRedirectURI: String) async throws {
        didStart = true
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
