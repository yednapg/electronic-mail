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

    func testSmartWorkQueueBuildsNativeDashboardSections() {
        let session = makeSmartAppSession()

        let snapshot = DashboardViewModelBuilder.snapshot(from: session, now: Date(timeIntervalSince1970: 0))

        XCTAssertEqual(snapshot.sections.map(\.title), ["Needs Action"])
        XCTAssertEqual(snapshot.sections.first?.items.first?.entityID, "thread-smart-1")
        XCTAssertEqual(snapshot.sections.first?.items.first?.detail?.body, ["Reply to Apple with the missing screenshot."])
    }

    func testEmptySmartWorkQueueSuppressesLegacyDashboardFeedSections() throws {
        let legacyFeed = try JSONDecoder.backend.decode(
            FeedResponse.self,
            from: Data(
                """
                {
                  "now": [
                    {
                      "id": "legacy-1",
                      "entity_id": "legacy-thread-1",
                      "user_id": "user-1",
                      "need_type": "decision",
                      "action_type": "external",
                      "effort_level": "quick",
                      "timing_band": "now",
                      "action_confidence": "high",
                      "primary_action": "open",
                      "fallback_action": "open",
                      "title": "Legacy dashboard item",
                      "why_this_is_here": "Old dashboard feed item.",
                      "detail": null,
                      "due_at": null,
                      "importance_level": "high",
                      "lifecycle_state": "active",
                      "current_state": "open",
                      "source": "gmail",
                      "gmail_thread_id": "legacy-thread-1",
                      "gmail_thread_action": null,
                      "trace_id": "trace-legacy-1",
                      "created_at": "2026-05-16T09:00:00+05:30"
                    }
                  ],
                  "today": [],
                  "worth_knowing": []
                }
                """.utf8
            )
        )
        let session = makeSmartAppSession(feed: legacyFeed, smartWorkQueue: emptySmartWorkQueue())

        let snapshot = DashboardViewModelBuilder.snapshot(from: session, now: Date(timeIntervalSince1970: 0))

        XCTAssertTrue(snapshot.sections.isEmpty)
    }

    func testMobileInboxSnapshotUsesSmartTitleAndSourceThreadWithoutSummaryPreview() {
        let session = makeSmartAppSession()

        let snapshot = MobileInboxViewModelBuilder.snapshot(from: session)

        XCTAssertEqual(snapshot.totalThreads, 1)
        XCTAssertEqual(snapshot.sections.first?.rows.first?.threadID, "thread-smart-1")
        XCTAssertNil(snapshot.sections.first?.rows.first?.summary)
        XCTAssertEqual(snapshot.sections.first?.rows.first?.badgeLabel, "2 grouped")
        XCTAssertEqual(snapshot.sections.first?.rows.first?.offlineReady, true)
    }

    func testMobileInboxSnapshotHidesRawMailboxSummaryPreview() {
        let mailbox = MailboxResponse(
            label: .inbox,
            totalThreads: 1,
            sections: [
                GmailThreadSection(
                    id: "today",
                    title: "Today",
                    rows: [
                        GmailThreadRow(
                            threadID: "thread-apple",
                            entityID: "thread-apple",
                            title: "Apple order W123456789 delivered",
                            href: "/v1/mailbox/threads/thread-apple",
                            latestSourceRecordID: "msg-apple",
                            latestReceivedAt: "2026-05-16T12:46:00+05:30",
                            latestMessageAt: "2026-05-16T12:46:00+05:30",
                            latestSubject: "Apple order delivered",
                            latestSender: "Apple <orders@apple.com>",
                            sender: "Apple",
                            participants: ["Apple"],
                            messageCount: 1,
                            summary: "This summary must stay hidden in the inbox list.",
                            aiGroupID: nil,
                            aiTitle: "Apple order W123456789 delivered",
                            aiSummary: "This AI summary must also stay hidden in the inbox list.",
                            snippet: "This snippet must not become inbox preview text.",
                            labelIDs: ["INBOX"],
                            labels: ["INBOX"],
                            unread: false,
                            actionNeeded: false,
                            actionType: "open",
                            actionTypeKey: "open",
                            priority: 20,
                            dashboardVisible: false,
                            currentState: .waiting,
                            lifecycleState: "active",
                            outcomeType: nil,
                            lifecycleUpdates: [],
                            enrichmentStatus: "ready"
                        )
                    ]
                )
            ]
        )

        let snapshot = MobileInboxViewModelBuilder.snapshot(from: mailbox)

        XCTAssertEqual(snapshot.sections.first?.rows.first?.subject, "Apple order W123456789 delivered")
        XCTAssertNil(snapshot.sections.first?.rows.first?.summary)
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

    private func makeSmartAppSession(
        feed: FeedResponse = FeedResponse(now: [], today: [], worthKnowing: []),
        smartWorkQueue: SmartWorkQueueResponse? = nil
    ) -> AppSessionResponse {
        AppSessionResponse(
            user: AppSessionUser(id: "user-1", email: "user@example.com", firstName: "Gaurav", displayName: "Gaurav"),
            readiness: PostLoginReadinessResponse(
                mode: "returning",
                stage: "welcome_back",
                readyToEnter: true,
                dashboardReady: true,
                mailboxReady: true,
                readyDashboardCount: 0,
                readyMailGroupCount: 1,
                fullImportRunning: false,
                fullImportCompleted: true,
                userDisplayName: "Gaurav",
                errorMessage: nil
            ),
            dashboard: DashboardResponse(
                auth: GoogleAuthState(available: true, connected: true, connectURL: nil),
                profile: DashboardProfile(email: "user@example.com", displayName: "Gaurav"),
                briefing: nil,
                feed: feed
            ),
            mailbox: MailboxResponse(label: .inbox, totalThreads: 1, sections: []),
            sync: AppSessionSyncState(
                lastSyncAt: "2026-06-11T10:00:00+00:00",
                lastError: nil,
                enrichmentPendingCount: 0,
                readyGroupCount: 1,
                oldestImportedAt: nil,
                fullImportRunning: false,
                fullImportCompleted: true
            ),
            smartInbox: SmartInboxResponse(
                totalRows: 1,
                sections: [
                    SmartInboxSection(
                        id: "today",
                        title: "Today",
                        rows: [
                            SmartInboxRow(
                                id: "smart-row-1",
                                rowKey: "mail-object:apple-review",
                                rowType: "verified_group",
                                title: "Apple review needs one screenshot",
                                summary: "Two Apple Developer messages belong to the same review thread.",
                                primarySender: "Apple Developer <developer@apple.com>",
                                latestMessageAt: "2026-05-16T12:46:00+05:30",
                                latestMessageID: "message-smart-latest",
                                readerThreadID: "thread-smart-1",
                                sourceThreadIDs: ["thread-smart-1"],
                                sourceMessageIDs: ["message-smart-1", "message-smart-latest"],
                                confidenceTier: "exact",
                                confidence: 1,
                                groupingReason: ["source": .string("mail_object")],
                                offlineStatus: "ready",
                                readiness: "ready",
                                actionType: "reply",
                                priority: 90
                            )
                        ]
                    )
                ],
                relatedSuggestions: [],
                readyCount: 1,
                partialCount: 0,
                failedCount: 0,
                generatedAt: "2026-06-11T10:00:00+00:00",
                hotWindowDays: 30,
                hotWindowMessageCap: 0,
                hotWindowThreadCap: 0
            ),
            smartWorkQueue: smartWorkQueue ?? SmartWorkQueueResponse(
                needsAction: [
                    SmartWorkItem(
                        id: "smart-work-1",
                        kind: "needs_action",
                        title: "Reply to Apple Developer review",
                        summary: "Reply to Apple with the missing screenshot.",
                        status: "open",
                        smartRowID: "smart-row-1",
                        sourceThreadIDs: ["thread-smart-1"],
                        sourceMessageIDs: ["message-smart-latest"],
                        dueAt: nil,
                        priority: 90,
                        confidence: 0.96,
                        reason: ["action": .string("reply")],
                        createdAt: "2026-06-11T10:00:00+00:00",
                        updatedAt: "2026-06-11T10:00:00+00:00"
                    )
                ],
                waiting: [],
                activeConversations: [],
                importantUpdates: [],
                manualReminders: [],
                totalOpen: 1,
                generatedAt: "2026-06-11T10:00:00+00:00"
            ),
            smartReadiness: SmartReadinessResponse(
                stage: "offline_ready",
                firstReadyComplete: true,
                hotWindowComplete: true,
                offlineReady: true,
                firstReadyTargetMessages: 300,
                hotWindowMessageCap: 0,
                hotWindowThreadCap: 0,
                processedMessages: 1,
                processedThreads: 1,
                readyRows: 1,
                partialRows: 0,
                failedRows: 0,
                offlineReadyRows: 1,
                offlinePartialRows: 0,
                offlineFailedRows: 0,
                lastError: nil
            )
        )
    }

    private func emptySmartWorkQueue() -> SmartWorkQueueResponse {
        SmartWorkQueueResponse(
            needsAction: [],
            waiting: [],
            activeConversations: [],
            importantUpdates: [],
            manualReminders: [],
            totalOpen: 0,
            generatedAt: "2026-06-11T10:00:00+00:00"
        )
    }
}
