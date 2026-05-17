import XCTest
@testable import ElectronicMailShared

final class DashboardViewModelTests: XCTestCase {
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
