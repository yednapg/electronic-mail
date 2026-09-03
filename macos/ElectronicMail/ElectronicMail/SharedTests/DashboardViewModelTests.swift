import XCTest
@testable import ElectronicMailShared

final class DashboardViewModelTests: XCTestCase {
    override func tearDown() {
        MobileAuthMockURLProtocol.responseData = nil
        MobileAuthMockURLProtocol.transientFailure = nil
        super.tearDown()
    }

    func testDashboardFixtureBuildsSummarySectionsAndGmailAction() throws {
        let dashboard = try JSONDecoder.backend.decode(
            DashboardResponse.self,
            from: contractFixtureData("dashboard.json")
        )

        let summary = DashboardViewModelBuilder.summary(from: dashboard)
        let sections = DashboardViewModelBuilder.sections(from: dashboard.feed)

        XCTAssertEqual(summary.headline, "Good morning.")
        XCTAssertEqual(sections.map(\.title), ["Now", "Today", "Worth Knowing"])
        XCTAssertEqual(sections[0].items.first?.title, "Groww confirmed your demat account has been closed and sent the client master report.")
        XCTAssertEqual(sections[0].items.first?.entityID, "entity-1")
        XCTAssertEqual(sections[0].items.first?.action?.operation, .archive)
        XCTAssertEqual(sections[0].items.first?.action?.gmailThreadID, "thread-1")
    }

    func testHiddenItemsAreRemovedLocally() throws {
        let dashboard = try JSONDecoder.backend.decode(
            DashboardResponse.self,
            from: contractFixtureData("dashboard.json")
        )

        let sections = DashboardViewModelBuilder.sections(from: dashboard.feed, hiddenItemIDs: ["item-1"])

        XCTAssertTrue(sections[0].items.isEmpty)
        XCTAssertEqual(sections[2].items.map(\.id), ["item-2"])
    }

    func testAgendaSortsCalendarItemsBeforeSectionRendering() throws {
        let feed = try JSONDecoder.backend.decode(
            FeedResponse.self,
            from: Data(
                """
                {
                  "now": [
                    {
                      "id": "cal-2",
                      "entity_id": "cal-2",
                      "user_id": "user-1",
                      "need_type": "awareness",
                      "action_type": "none",
                      "effort_level": "quick",
                      "timing_band": "today",
                      "action_confidence": "high",
                      "primary_action": "join",
                      "fallback_action": "open",
                      "title": "Design review",
                      "why_this_is_here": "Meeting soon.",
                      "detail": null,
                      "due_at": "2026-05-16T15:00:00+05:30",
                      "importance_level": "medium",
                      "lifecycle_state": "active",
                      "current_state": "open",
                      "source": "calendar",
                      "gmail_thread_id": null,
                      "gmail_thread_action": null,
                      "trace_id": "trace-cal-2",
                      "created_at": "2026-05-16T09:00:00+05:30"
                    },
                    {
                      "id": "cal-1",
                      "entity_id": "cal-1",
                      "user_id": "user-1",
                      "need_type": "awareness",
                      "action_type": "none",
                      "effort_level": "quick",
                      "timing_band": "now",
                      "action_confidence": "high",
                      "primary_action": "join",
                      "fallback_action": "open",
                      "title": "Standup",
                      "why_this_is_here": "Meeting now.",
                      "detail": null,
                      "due_at": "2026-05-16T10:00:00+05:30",
                      "importance_level": "medium",
                      "lifecycle_state": "active",
                      "current_state": "open",
                      "source": "calendar",
                      "gmail_thread_id": null,
                      "gmail_thread_action": null,
                      "trace_id": "trace-cal-1",
                      "created_at": "2026-05-16T09:00:00+05:30"
                    }
                  ],
                  "today": [],
                  "worth_knowing": []
                }
                """.utf8
            )
        )

        let agenda = DashboardViewModelBuilder.agenda(from: feed)

        XCTAssertEqual(agenda.map(\.id), ["cal-1", "cal-2"])
        XCTAssertEqual(agenda[0].title, "Standup")
        XCTAssertEqual(agenda[0].tone, .blue)
    }

    func testInboxSnapshotPreservesAuthoritativeSectionAndRowOrder() {
        let mailbox = MailboxResponse(
            label: .inbox,
            totalThreads: 4,
            sections: [
                GmailThreadSection(
                    id: "yesterday:rank-1",
                    title: "Yesterday",
                    rows: [
                        mobileInboxRow(id: "rank-1", receivedAt: "2026-07-10T08:00:00+00:00"),
                        mobileInboxRow(id: "rank-2", receivedAt: "2026-07-15T18:00:00+00:00")
                    ]
                ),
                GmailThreadSection(
                    id: "today:rank-3",
                    title: "Today",
                    rows: [mobileInboxRow(id: "rank-3", receivedAt: "2026-07-12T12:00:00+00:00")]
                ),
                GmailThreadSection(
                    id: "yesterday:rank-4",
                    title: "Yesterday",
                    rows: [mobileInboxRow(id: "rank-4", receivedAt: "2026-07-14T12:00:00+00:00")]
                )
            ]
        )

        let snapshot = MobileInboxViewModelBuilder.snapshot(from: mailbox)

        XCTAssertEqual(snapshot.sections.map(\.id), ["yesterday:rank-1", "today:rank-3", "yesterday:rank-4"])
        XCTAssertEqual(snapshot.sections.map(\.title), ["Yesterday", "Today", "Yesterday"])
        XCTAssertEqual(snapshot.sections.flatMap(\.rows).map(\.id), ["rank-1", "rank-2", "rank-3", "rank-4"])
    }

    func testMobileAuthURLAndCallbackParsing() throws {
        let baseURL = URL(string: "https://api.example.com")!
        let callbackURL = URL(string: MobileAuthFlow.callbackRedirectURI)!
        let authURL = try MobileAuthFlow.authenticationURL(baseURL: baseURL, redirectURL: callbackURL)

        XCTAssertEqual(authURL.absoluteString, "https://api.example.com/auth/google?redirect_to=electronicmail://auth/callback")
        XCTAssertEqual(
            try MobileAuthFlow.loginCode(from: URL(string: "electronicmail://auth/callback?login_code=abc123")!),
            "abc123"
        )

        let handoffURL = try MobileAuthFlow.handoffCompletionRedirectURL(baseURL: baseURL, handoffID: "handoff-1")
        XCTAssertEqual(handoffURL.absoluteString, "https://api.example.com/auth/mobile/complete?handoff_id=handoff-1")
        XCTAssertEqual(
            MobileAuthFlow.handoffStatusURL(baseURL: baseURL, handoffID: "handoff-1").absoluteString,
            "https://api.example.com/v1/auth/mobile/handoff/handoff-1"
        )

        XCTAssertThrowsError(
            try MobileAuthFlow.loginCode(
                from: URL(string: "electronicmail://auth/callback?status=cancelled&error=Google%20sign-in%20was%20cancelled.")!
            )
        ) { error in
            XCTAssertEqual(error as? MobileAuthFlowError, .authenticationCancelled)
        }
    }

    func testMobileAuthHandoffStopsImmediatelyOnTerminalFailure() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [MobileAuthMockURLProtocol.self]
        let session = URLSession(configuration: configuration)
        MobileAuthMockURLProtocol.responseData = Data(
            #"{"status":"failed","error":"Google sign-in failed. Please try again."}"#.utf8
        )

        do {
            _ = try await MobileAuthFlow.pollForLoginCode(
                baseURL: URL(string: "https://api.example.com")!,
                handoffID: "handoff-1",
                session: session,
                maxAttempts: 1,
                retryDelayNanoseconds: 0
            )
            XCTFail("Expected terminal handoff failure")
        } catch {
            XCTAssertEqual(
                error as? MobileAuthFlowError,
                .handoffRejected("Google sign-in failed. Please try again.")
            )
        }
    }

    func testMobileAuthHandoffRetriesTransientNetworkFailure() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [MobileAuthMockURLProtocol.self]
        let session = URLSession(configuration: configuration)
        MobileAuthMockURLProtocol.transientFailure = URLError(.timedOut)
        MobileAuthMockURLProtocol.responseData = Data(
            #"{"status":"ready","login_code":"login-after-retry"}"#.utf8
        )

        let loginCode = try await MobileAuthFlow.pollForLoginCode(
            baseURL: URL(string: "https://api.example.com")!,
            handoffID: "handoff-1",
            session: session,
            maxAttempts: 2,
            retryDelayNanoseconds: 0
        )

        XCTAssertEqual(loginCode, "login-after-retry")
    }

    @MainActor
    func testDefaultSenderPreferenceSurvivesSettingsStoreRecreation() {
        let suiteName = "GmailAccountSettingsStoreTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suiteName)!
        defer { defaults.removePersistentDomain(forName: suiteName) }

        let firstStore = GmailAccountSettingsStore(
            client: DemoAppClient(),
            defaults: defaults
        )
        firstStore.defaultSenderAccountID = DemoAppFixtures.userID

        let restoredStore = GmailAccountSettingsStore(
            client: DemoAppClient(),
            defaults: defaults
        )

        XCTAssertEqual(restoredStore.defaultSenderAccountID, DemoAppFixtures.userID)
    }

    @MainActor
    func testUnavailableDefaultSenderFallsBackToAskEveryTime() async {
        let suiteName = "GmailAccountSettingsStoreTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suiteName)!
        defer { defaults.removePersistentDomain(forName: suiteName) }
        defaults.set(
            "missing-gmail-account",
            forKey: GmailComposingPreferences.defaultSenderAccountIDStorageKey
        )
        let store = GmailAccountSettingsStore(
            client: DemoAppClient(),
            defaults: defaults
        )

        await store.load()

        XCTAssertNil(store.defaultSenderAccountID)
        XCTAssertNil(
            defaults.string(
                forKey: GmailComposingPreferences.defaultSenderAccountIDStorageKey
            )
        )
    }

    private func contractFixtureData(_ name: String) throws -> Data {
        var directory = URL(fileURLWithPath: #filePath)

        for _ in 0..<8 {
            directory.deleteLastPathComponent()
            let candidate = directory
                .appendingPathComponent("contracts")
                .appendingPathComponent("fixtures")
                .appendingPathComponent(name)

            if FileManager.default.fileExists(atPath: candidate.path) {
                return try Data(contentsOf: candidate)
            }
        }

        throw CocoaError(.fileNoSuchFile)
    }
}

private func mobileInboxRow(id: String, receivedAt: String) -> GmailThreadRow {
    GmailThreadRow(
        threadID: id,
        entityID: id,
        title: id,
        href: "/v1/mailbox/threads/\(id)",
        latestSourceRecordID: "message-\(id)",
        latestReceivedAt: receivedAt,
        latestMessageAt: receivedAt,
        latestSubject: id,
        latestSender: "Sender",
        sender: "Sender",
        participants: ["Sender"],
        messageCount: 1,
        summary: id,
        snippet: id,
        labelIDs: ["INBOX"],
        labels: ["INBOX"],
        unread: false,
        actionNeeded: false,
        actionType: "open",
        actionTypeKey: "open",
        priority: nil,
        dashboardVisible: false,
        currentState: .waiting,
        lifecycleState: "active",
        outcomeType: nil,
        lifecycleUpdates: [],
        enrichmentStatus: "ready"
    )
}

private final class MobileAuthMockURLProtocol: URLProtocol {
    static var responseData: Data?
    static var transientFailure: URLError?

    override class func canInit(with request: URLRequest) -> Bool { true }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        if let transientFailure = Self.transientFailure {
            Self.transientFailure = nil
            client?.urlProtocol(self, didFailWithError: transientFailure)
            return
        }
        guard let responseData = Self.responseData,
              let url = request.url,
              let response = HTTPURLResponse(url: url, statusCode: 200, httpVersion: nil, headerFields: nil) else {
            client?.urlProtocol(self, didFailWithError: MobileAuthFlowError.missingLoginCode)
            return
        }
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: responseData)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}
