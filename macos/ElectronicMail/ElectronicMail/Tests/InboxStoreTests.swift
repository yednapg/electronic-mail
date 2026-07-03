import XCTest
@testable import ElectronicMailCore

@MainActor
final class InboxStoreTests: XCTestCase {
    func testDemoAppSessionRoundTripsThroughBackendDecoder() throws {
        let data = try JSONEncoder.backend.encode(DemoAppFixtures.appSession)
        let decoded = try JSONDecoder.backend.decode(AppSessionResponse.self, from: data)

        XCTAssertEqual(decoded.user.id, "demo-user")
        XCTAssertEqual(decoded.mailbox.totalThreads, 16)
    }

    func testSectionOrderMatchesInboxBuckets() async {
        let store = InboxStore(client: DemoAppClient(), sessionCache: AppSessionCache(defaults: .ephemeral()), threadCache: ThreadCache(defaults: .ephemeral()))

        await store.load()

        XCTAssertEqual(store.sections.map(\.title), ["Today", "Past 7 days", "Earlier this month"])
    }

    func testMainInterfaceReadinessUsesInboxRowsNotDashboardFeed() async {
        let mailbox = makeSingleRowMailbox(threadID: "live-inbox-thread", title: "Apple order delivered")
        let current = DemoAppFixtures.appSession
        let session = AppSessionResponse(
            user: current.user,
            readiness: PostLoginReadinessResponse(
                mode: "returning",
                stage: "welcome_back",
                readyToEnter: true,
                dashboardReady: false,
                mailboxReady: true,
                readyDashboardCount: 0,
                readyMailGroupCount: 1,
                fullImportRunning: true,
                fullImportCompleted: false,
                userDisplayName: current.user.displayName,
                errorMessage: nil
            ),
            dashboard: current.dashboard.replacingFeed(FeedResponse(now: [], today: [], worthKnowing: [])),
            mailbox: mailbox,
            sync: current.sync
        )
        let store = InboxStore(
            client: SmartSessionAppClient(session: session, mailbox: mailbox),
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral())
        )
        store.setSessionToken("live-session-token")

        await store.load()

        XCTAssertEqual(store.mailboxVisibleRowCount, 1)
        XCTAssertEqual(store.dashboardFeedCount, 0)
        XCTAssertTrue(store.isReadyForMainInterface)
    }

    func testMainInterfaceWaitsForReadinessEvenWhenMailboxRowsExist() async {
        let mailbox = makeSingleRowMailbox(threadID: "pending-title-thread", title: "Preparing Apple order title")
        let current = DemoAppFixtures.appSession
        let session = AppSessionResponse(
            user: current.user,
            readiness: PostLoginReadinessResponse(
                mode: "first_time",
                stage: "preparing_inbox",
                readyToEnter: false,
                dashboardReady: false,
                mailboxReady: true,
                readyDashboardCount: 0,
                readyMailGroupCount: 1,
                fullImportRunning: true,
                fullImportCompleted: false,
                userDisplayName: current.user.displayName,
                errorMessage: nil
            ),
            dashboard: current.dashboard,
            mailbox: mailbox,
            sync: current.sync
        )
        let store = InboxStore(
            client: SmartSessionAppClient(session: session, mailbox: mailbox),
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral())
        )
        store.setSessionToken("live-session-token")

        await store.load()

        XCTAssertEqual(store.mailboxVisibleRowCount, 1)
        XCTAssertFalse(store.isReadyForMainInterface)
    }

    func testNativeCommandPaletteDefaultsAreInboxFirst() throws {
        let sourceURL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("Core/SignedInShellView.swift")
        let source = try String(contentsOf: sourceURL)

        guard
            let inboxRange = source.range(of: #"id: "nav:inbox","#),
            let todoRange = source.range(of: #"id: "nav:todo","#)
        else {
            XCTFail("Command palette navigation commands are missing")
            return
        }

        XCTAssertLessThan(
            source.distance(from: source.startIndex, to: inboxRange.lowerBound),
            source.distance(from: source.startIndex, to: todoRange.lowerBound)
        )
        let inboxCommandSource = String(source[inboxRange.lowerBound..<todoRange.lowerBound])
        XCTAssertTrue(inboxCommandSource.contains(#"title: "Inbox""#))
        XCTAssertTrue(inboxCommandSource.contains("priority: 20"))
    }

    func testReaderSummaryLoadsOnlyAfterExplicitRequest() async {
        let mailbox = makeSingleRowMailbox(threadID: "summary-thread", title: "Apple order delivered")
        let current = DemoAppFixtures.appSession
        let baseThread = DemoAppFixtures.threads["demo-google-today"]!
        let initialThread = ThreadReaderResponse(
            entityID: "summary-thread",
            userID: current.user.id,
            source: .gmail,
            gmailThreadID: "summary-thread",
            subject: "Apple order delivered",
            title: "Apple order delivered",
            summary: nil,
            totalMessages: baseThread.messages.count,
            limit: 50,
            offset: 0,
            hasMore: false,
            messages: baseThread.messages
        )
        let summarizedThread = ThreadReaderResponse(
            entityID: "summary-thread",
            userID: current.user.id,
            source: .gmail,
            gmailThreadID: "summary-thread",
            subject: "Apple order delivered",
            title: "Apple order delivered",
            summary: "Apple delivered order W123456789.",
            totalMessages: baseThread.messages.count,
            limit: 50,
            offset: 0,
            hasMore: false,
            messages: baseThread.messages
        )
        let session = AppSessionResponse(
            user: current.user,
            readiness: current.readiness,
            dashboard: current.dashboard.replacingFeed(FeedResponse(now: [], today: [], worthKnowing: [])),
            mailbox: mailbox,
            sync: current.sync
        )
        let client = SmartSessionAppClient(
            session: session,
            mailbox: mailbox,
            threads: ["summary-thread": initialThread],
            summaryThreads: ["summary-thread": summarizedThread]
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral())
        )
        store.setSessionToken("live-session-token")

        await store.load()
        await store.openReader(threadID: "summary-thread").value

        XCTAssertNil(store.readerThread?.summary)
        XCTAssertEqual(client.threadSummaryCallCount, 0)

        await store.requestReaderSummary(threadID: "summary-thread")

        XCTAssertEqual(client.threadSummaryCallCount, 1)
        XCTAssertEqual(store.readerThread?.summary, "Apple delivered order W123456789.")
        XCTAssertFalse(store.readerSummaryLoading)
        XCTAssertNil(store.readerSummaryError)
    }

    func testSectionsDropBucketsWithNoVisibleRowsAfterMailboxFiltering() async {
        let hiddenSentRow = makeMailboxRow(
            threadID: "sent-thread",
            latestSourceRecordID: "sent-message",
            receivedAt: "2026-05-29T09:30:00+05:30",
            title: "Sent-only row",
            labelIDs: ["SENT"],
            labels: ["SENT"]
        )
        let visibleInboxRow = makeMailboxRow(
            threadID: "inbox-thread",
            latestSourceRecordID: "inbox-message",
            receivedAt: "2026-05-28T18:00:00+05:30",
            title: "Inbox row"
        )
        let mailbox = MailboxResponse(
            label: .inbox,
            totalThreads: 2,
            sections: [
                GmailThreadSection(id: "today", title: "Today", rows: [hiddenSentRow]),
                GmailThreadSection(id: "yesterday", title: "Yesterday", rows: [visibleInboxRow])
            ],
            fullImportRunning: false,
            fullImportCompleted: true
        )
        let store = InboxStore(
            client: FixedMailboxAppClient(mailbox: mailbox),
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral())
        )
        store.setSessionToken("live-session-token")

        await store.load()

        XCTAssertEqual(store.sections.map(\.title), ["Yesterday"])
        XCTAssertEqual(store.flatRows.map(\.threadID), ["inbox-thread"])
    }

    func testInitialLoadUsesLiveMailboxInsteadOfSessionMailboxSnapshot() async {
        let staleSessionMailbox = makeSingleRowMailbox(threadID: "stale-session-thread", title: "Stale session row")
        let liveMailbox = makeSingleRowMailbox(threadID: "live-thread", title: "Live mailbox row")
        let client = RealtimeEventAppClient(sessionMailbox: staleSessionMailbox, mailboxResponses: [liveMailbox])
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral())
        )
        store.setSessionToken("live-session-token")

        await store.load()

        XCTAssertEqual(store.flatRows.map(\.threadID), ["live-thread"])
        XCTAssertEqual(store.flatRows.map(\.title), ["Live mailbox row"])
        XCTAssertEqual(client.mailboxCallCount, 1)
    }

    func testSwitchingBackToInboxDoesNotUseSessionMailboxSnapshot() async {
        let staleSessionMailbox = makeSingleRowMailbox(threadID: "stale-session-thread", title: "Stale session row")
        let firstInboxMailbox = makeSingleRowMailbox(threadID: "first-live-inbox", title: "First live inbox")
        let sentRow = makeMailboxRow(
            threadID: "sent-thread",
            latestSourceRecordID: "sent-message",
            receivedAt: "2026-05-29T11:00:00+05:30",
            title: "Live sent row",
            labelIDs: ["SENT"],
            labels: ["SENT"]
        )
        let sentMailbox = MailboxResponse(
            label: .sent,
            totalThreads: 1,
            loadedThreads: 1,
            sections: [GmailThreadSection(id: "today", title: "Today", rows: [sentRow])],
            fullImportRunning: false,
            fullImportCompleted: true
        )
        let refreshedInboxMailbox = makeSingleRowMailbox(threadID: "refreshed-live-inbox", title: "Refreshed live inbox")
        let client = RealtimeEventAppClient(
            sessionMailbox: staleSessionMailbox,
            mailboxResponses: [firstInboxMailbox, sentMailbox, refreshedInboxMailbox]
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral())
        )
        store.setSessionToken("live-session-token")

        await store.load()
        await store.setMailboxLabel(.sent)
        XCTAssertEqual(store.flatRows.map(\.threadID), ["sent-thread"])

        await store.setMailboxLabel(.inbox)

        XCTAssertEqual(store.flatRows.map(\.threadID), ["refreshed-live-inbox"])
        XCTAssertFalse(store.flatRows.map(\.threadID).contains("stale-session-thread"))
        XCTAssertEqual(client.mailboxCallCount, 3)
    }

    func testOpeningUnreadRowMarksItReadLocally() async {
        let unreadMailbox = MailboxResponse(
            label: .inbox,
            totalThreads: 1,
            loadedThreads: 1,
            sections: [
                GmailThreadSection(
                    id: "today",
                    title: "Today",
                    rows: [
                        makeMailboxRow(
                            threadID: "unread-thread",
                            latestSourceRecordID: "unread-message",
                            receivedAt: "2026-05-29T11:00:00+05:30",
                            title: "Unread mail",
                            labelIDs: ["INBOX", "UNREAD"],
                            labels: ["INBOX", "UNREAD"]
                        )
                    ]
                )
            ],
            fullImportRunning: false,
            fullImportCompleted: true
        )
        let client = ActionMailboxAppClient(mailbox: unreadMailbox)
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral())
        )
        store.setSessionToken("live-session-token")

        await store.load()
        XCTAssertEqual(store.flatRows.first?.isUnread, true)

        _ = store.openReader(threadID: "unread-thread")
        try? await Task.sleep(nanoseconds: 250_000_000)

        XCTAssertEqual(client.enqueuedActions.map(\.action), [.markRead])
        XCTAssertEqual(store.flatRows.first?.isUnread, false)
    }

    func testExpandingGroupedRowInsertsChildRowsAndOpensFocusedMessage() async {
        let children = [
            GmailThreadChildRow(
                messageID: "msg-1",
                gmailThreadID: "gmail-thread-1",
                sender: "First <first@example.com>",
                subject: "First message",
                snippet: "First snippet",
                receivedAt: "2026-05-29T09:30:00+05:30",
                labelIDs: ["INBOX"],
                labels: ["INBOX"],
                unread: false
            ),
            GmailThreadChildRow(
                messageID: "msg-2",
                gmailThreadID: "gmail-thread-1",
                sender: "Second <second@example.com>",
                subject: "Second message",
                snippet: "Second snippet",
                receivedAt: "2026-05-29T10:00:00+05:30",
                labelIDs: ["INBOX", "UNREAD"],
                labels: ["INBOX", "UNREAD"],
                unread: true
            )
        ]
        let groupedRow = makeMailboxRow(
            threadID: "parent-thread",
            latestSourceRecordID: "msg-2",
            receivedAt: "2026-05-29T10:00:00+05:30",
            title: "Grouped message",
            children: children
        )
        let mailbox = MailboxResponse(
            label: .inbox,
            totalThreads: 1,
            sections: [GmailThreadSection(id: "today", title: "Today", rows: [groupedRow])],
            fullImportRunning: false,
            fullImportCompleted: true
        )
        let store = InboxStore(
            client: FixedMailboxAppClient(mailbox: mailbox),
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral())
        )
        store.setSessionToken("live-session-token")

        await store.load()
        XCTAssertEqual(store.flatRows.map(\.id), ["parent-thread"])
        XCTAssertEqual(store.flatRows.first?.isExpandable, true)

        store.toggleExpansion(threadID: "parent-thread")
        XCTAssertEqual(store.flatRows.map(\.id), [
            "parent-thread",
            "parent-thread::message::msg-1",
            "parent-thread::message::msg-2"
        ])
        XCTAssertEqual(store.flatRows[1].sender, "First")
        XCTAssertEqual(store.flatRows[2].visualTone, .unread)

        store.select(threadID: "parent-thread", focusedMessageID: "msg-2", prefetch: false)
        store.openActiveSelection()

        XCTAssertEqual(store.readerThreadID, "parent-thread")
        XCTAssertEqual(store.readerFocusedMessageID, "msg-2")

        store.toggleExpansion(threadID: "parent-thread")
        XCTAssertEqual(store.flatRows.map(\.id), ["parent-thread"])
        XCTAssertNil(store.selectedMessageID)
    }

    func testRowVisualStateMapping() async {
        let store = InboxStore(client: DemoAppClient(), sessionCache: AppSessionCache(defaults: .ephemeral()), threadCache: ThreadCache(defaults: .ephemeral()))

        await store.load()
        store.select(threadID: "demo-rbi-today")

        let rows = Dictionary(uniqueKeysWithValues: store.flatRows.map { ($0.threadID, $0) })
        XCTAssertEqual(rows["demo-google-today"]?.visualTone, .unread)
        XCTAssertEqual(rows["demo-github-today"]?.visualTone, .read)
        XCTAssertEqual(rows["demo-rbi-today"]?.visualTone, .selected)
        XCTAssertEqual(rows["demo-apple-today"]?.isGrouped, true)
    }

    func testInboxListRowsRemainSingleLineEvenWhenSummaryExists() {
        let row = InboxRowViewModel(
            id: "smart-row",
            sender: "Amazon Web Services",
            title: "AWS Bedrock access case resolved",
            summary: "AWS support reviewed the Bedrock access issue, escalated it internally, and later resolved the case.",
            receivedAt: "2026-06-13T02:04:00+05:30",
            section: "Today",
            isUnread: false,
            isGrouped: true,
            isSelected: false,
            threadID: "smart-row",
            focusedMessageID: nil,
            messageCount: 2,
            timeLabel: "02:04 AM",
            hasAttachments: false,
            presentationStatus: "ai_ready",
            isChild: false,
            isExpandable: true,
            isExpanded: false
        )
        let metrics = InboxLayoutMetrics(windowSize: CGSize(width: 1440, height: 900))

        XCTAssertEqual(row.summary, "AWS support reviewed the Bedrock access issue, escalated it internally, and later resolved the case.")
        XCTAssertEqual(metrics.rowHeight(for: row), ElectronicMailType.bodyLineHeight)
    }

    func testCacheMergeKeepsNonEmptyMailboxWhenRefreshReturnsEmpty() {
        let cache = AppSessionCache(defaults: .ephemeral())
        let current = DemoAppFixtures.appSession
        let next = AppSessionResponse(
            user: current.user,
            readiness: current.readiness,
            dashboard: current.dashboard,
            mailbox: MailboxResponse(
                label: .inbox,
                totalThreads: 0,
                nextCursor: nil,
                sections: [],
                readyCount: 0,
                pendingCount: 0,
                oldestImportedAt: nil,
                fullImportRunning: false,
                fullImportCompleted: false
            ),
            sync: current.sync
        )

        let merged = cache.merge(current: current, next: next)

        XCTAssertEqual(merged.mailbox.totalThreads, current.mailbox.totalThreads)
    }

    func testCacheMergeAcceptsEmptyDashboardFromSuccessfulRefresh() {
        let cache = AppSessionCache(defaults: .ephemeral())
        let current = DemoAppFixtures.appSession
        let next = AppSessionResponse(
            user: current.user,
            readiness: current.readiness,
            dashboard: current.dashboard.replacingFeed(FeedResponse(now: [], today: [], worthKnowing: [])),
            mailbox: current.mailbox,
            sync: current.sync
        )

        let merged = cache.merge(current: current, next: next)

        XCTAssertTrue(merged.dashboard.feed.isEmpty)
        XCTAssertEqual(merged.mailbox.totalThreads, current.mailbox.totalThreads)
    }

    func testCacheMergeKeepsNonEmptySmartInboxAndWorkQueueWhenRefreshReturnsEmpty() {
        let cache = AppSessionCache(defaults: .ephemeral())
        let current = DemoAppFixtures.appSession.withSmartProjection()
        let next = AppSessionResponse(
            user: current.user,
            readiness: current.readiness,
            dashboard: current.dashboard,
            mailbox: current.mailbox,
            sync: current.sync,
            smartInbox: makeSmartInbox(rows: []),
            smartWorkQueue: makeSmartWorkQueue(needsAction: []),
            smartReadiness: makeSmartReadiness(readyRows: 0)
        )

        let merged = cache.merge(current: current, next: next)

        XCTAssertEqual(merged.smartInbox?.totalRows, 1)
        XCTAssertEqual(merged.smartWorkQueue?.totalOpen, 1)
        XCTAssertEqual(merged.smartReadiness?.readyRows, 1)
    }

    func testCacheMergeAcceptsEmptySmartWorkQueueWhenSmartInboxRefreshIsNonEmpty() {
        let cache = AppSessionCache(defaults: .ephemeral())
        let current = DemoAppFixtures.appSession.withSmartProjection()
        let next = AppSessionResponse(
            user: current.user,
            readiness: current.readiness,
            dashboard: current.dashboard,
            mailbox: current.mailbox,
            sync: current.sync,
            smartInbox: makeSmartInbox(rows: [makeSmartInboxRow()]),
            smartWorkQueue: makeSmartWorkQueue(needsAction: []),
            smartReadiness: makeSmartReadiness(readyRows: 1)
        )

        let merged = cache.merge(current: current, next: next)

        XCTAssertEqual(merged.smartInbox?.totalRows, 1)
        XCTAssertEqual(merged.smartWorkQueue?.totalOpen, 0)
        XCTAssertTrue(merged.smartWorkQueue?.needsAction.isEmpty ?? false)
    }

    func testSmartInboxRowsLeadInboxWithoutHidingUncoveredMailboxRows() async {
        let normalMailbox = makeSingleRowMailbox(threadID: "normal-thread", title: "Normal imported row")
        let session = DemoAppFixtures.appSession.withSmartProjection(mailbox: normalMailbox)
        let store = InboxStore(
            client: SmartSessionAppClient(session: session, mailbox: normalMailbox),
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral())
        )
        store.setSessionToken("live-session-token")

        await store.load()

        XCTAssertEqual(store.flatRows.map(\.threadID), ["demo-apple-today", "normal-thread"])
        XCTAssertEqual(store.flatRows.first?.title, "Apple review needs one screenshot")
        XCTAssertNil(store.flatRows.first?.summary)
        XCTAssertEqual(store.flatRows.first?.isGrouped, true)
        XCTAssertEqual(store.flatRows.last?.title, "Normal imported row")
        XCTAssertNil(store.flatRows.last?.summary)
    }

    func testSmartInboxGroupedRowsBorrowMailboxExpansionAndAttachmentState() async {
        let children = [
            GmailThreadChildRow(
                messageID: "message-1",
                gmailThreadID: "gmail-thread-1",
                sender: "Flipkart <no-reply@flipkart.com>",
                subject: "Offer one",
                snippet: "First related offer",
                receivedAt: "2026-06-10T09:00:00+05:30",
                labelIDs: ["INBOX"],
                labels: ["INBOX"],
                unread: false
            ),
            GmailThreadChildRow(
                messageID: "message-2",
                gmailThreadID: "gmail-thread-2",
                sender: "Flipkart <no-reply@flipkart.com>",
                subject: "Offer two",
                snippet: "Second related offer",
                receivedAt: "2026-06-10T10:00:00+05:30",
                labelIDs: ["INBOX", "UNREAD"],
                labels: ["INBOX", "UNREAD"],
                unread: true
            )
        ]
        let backingRow = makeMailboxRow(
            threadID: "mailbox-cluster:flipkart",
            latestSourceRecordID: "message-2",
            receivedAt: "2026-06-10T10:00:00+05:30",
            title: "Raw Flipkart cluster",
            children: children,
            hasAttachments: true,
            attachmentCount: 1
        )
        let mailbox = MailboxResponse(
            label: .inbox,
            totalThreads: 1,
            loadedThreads: 1,
            sections: [GmailThreadSection(id: "today", title: "Today", rows: [backingRow])],
            fullImportRunning: false,
            fullImportCompleted: true
        )
        let smartRow = SmartInboxRow(
            id: "smart-row-flipkart",
            rowKey: "mailbox-cluster:flipkart",
            rowType: "related_bundle",
            title: "Flipkart updates",
            summary: "2 related Flipkart emails.",
            primarySender: "Flipkart <no-reply@flipkart.com>",
            latestMessageAt: "2026-06-10T10:00:00+05:30",
            latestMessageID: "message-2",
            readerThreadID: "smart-row:smart-row-flipkart",
            sourceThreadIDs: ["gmail-thread-1", "gmail-thread-2"],
            sourceMessageIDs: ["message-1", "message-2"],
            confidenceTier: "medium",
            confidence: 0.7,
            groupingReason: ["source": .string("mailbox_projection")],
            offlineStatus: "ready",
            readiness: "ready",
            actionType: "review",
            priority: 10
        )
        let session = DemoAppFixtures.appSession.withSmartProjection(mailbox: mailbox, smartRows: [smartRow])
        let store = InboxStore(
            client: SmartSessionAppClient(session: session, mailbox: mailbox),
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral())
        )
        store.setSessionToken("live-session-token")

        await store.load()

        XCTAssertEqual(store.flatRows.map(\.id), ["smart-row-flipkart"])
        XCTAssertEqual(store.flatRows.first?.threadID, "smart-row:smart-row-flipkart")
        XCTAssertEqual(store.flatRows.first?.title, "Flipkart updates")
        XCTAssertEqual(store.flatRows.first?.isExpandable, true)
        XCTAssertEqual(store.flatRows.first?.hasAttachments, true)
        XCTAssertNil(store.flatRows.first?.summary)

        store.toggleExpansion(threadID: "smart-row:smart-row-flipkart")

        XCTAssertEqual(store.flatRows.map(\.id), [
            "smart-row-flipkart",
            "smart-row:smart-row-flipkart::message::message-1",
            "smart-row:smart-row-flipkart::message::message-2"
        ])
        XCTAssertEqual(store.flatRows[2].visualTone, .unread)
        XCTAssertEqual(store.flatRows[2].focusedMessageID, "message-2")
        XCTAssertNil(store.flatRows[1].summary)
        XCTAssertNil(store.flatRows[2].summary)
    }

    func testSmartInboxGroupAcrossSeparateMailboxRowsExpandsWithSyntheticChildren() async {
        let firstRawRow = makeMailboxRow(
            threadID: "hdfc-wire-thread",
            latestSourceRecordID: "hdfc-wire-message",
            receivedAt: "2026-06-07T10:00:00+05:30",
            title: "HDFC international wire registered"
        )
        let secondRawRow = makeMailboxRow(
            threadID: "hdfc-response-thread",
            latestSourceRecordID: "hdfc-response-message",
            receivedAt: "2026-06-07T11:00:00+05:30",
            title: "HDFC response on wire request",
            hasAttachments: true,
            attachmentCount: 1
        )
        let mailbox = MailboxResponse(
            label: .inbox,
            totalThreads: 2,
            loadedThreads: 2,
            sections: [GmailThreadSection(id: "today", title: "Today", rows: [firstRawRow, secondRawRow])],
            fullImportRunning: false,
            fullImportCompleted: true
        )
        let smartRow = SmartInboxRow(
            id: "smart-row-hdfc-wire",
            rowKey: "mail-object:hdfc-wire",
            rowType: "verified_group",
            title: "Wire transfer status with HDFC Bank",
            summary: "Two HDFC messages track the same wire transfer request.",
            primarySender: "HDFC Bank Care <support@hdfcbank.com>",
            latestMessageAt: "2026-06-07T11:00:00+05:30",
            latestMessageID: "hdfc-response-message",
            readerThreadID: "smart-row:smart-row-hdfc-wire",
            sourceThreadIDs: ["hdfc-wire-thread", "hdfc-response-thread"],
            sourceMessageIDs: ["hdfc-wire-message", "hdfc-response-message"],
            confidenceTier: "exact",
            confidence: 0.99,
            groupingReason: ["source": .string("mail_object")],
            offlineStatus: "ready",
            readiness: "ready",
            actionType: "review",
            priority: 70
        )
        let session = DemoAppFixtures.appSession.withSmartProjection(mailbox: mailbox, smartRows: [smartRow])
        let store = InboxStore(
            client: SmartSessionAppClient(session: session, mailbox: mailbox),
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral())
        )
        store.setSessionToken("live-session-token")

        await store.load()

        XCTAssertEqual(store.flatRows.map(\.id), ["smart-row-hdfc-wire"])
        XCTAssertEqual(store.flatRows.first?.threadID, "smart-row:smart-row-hdfc-wire")
        XCTAssertEqual(store.flatRows.first?.title, "Wire transfer status with HDFC Bank")
        XCTAssertEqual(store.flatRows.first?.isGrouped, true)
        XCTAssertEqual(store.flatRows.first?.isExpandable, true)
        XCTAssertEqual(store.flatRows.first?.messageCount, 2)
        XCTAssertEqual(store.flatRows.first?.hasAttachments, true)

        store.toggleExpansion(threadID: "smart-row:smart-row-hdfc-wire")

        XCTAssertEqual(store.flatRows.map(\.id), [
            "smart-row-hdfc-wire",
            "smart-row:smart-row-hdfc-wire::message::hdfc-wire-message",
            "smart-row:smart-row-hdfc-wire::message::hdfc-response-message"
        ])
        XCTAssertEqual(store.flatRows[1].title, "HDFC international wire registered")
        XCTAssertEqual(store.flatRows[2].title, "HDFC response on wire request")
        XCTAssertEqual(store.flatRows[2].focusedMessageID, "hdfc-response-message")
    }

    func testSmartInboxIsAuthoritativeAndDoesNotAppendRawInboxPaginationRows() async {
        let smartChildren = [
            GmailThreadChildRow(
                messageID: "smart-message-1",
                gmailThreadID: "smart-thread-1",
                sender: "Support <support@example.com>",
                subject: "Case update",
                snippet: "First case update",
                receivedAt: "2026-06-10T09:00:00+05:30",
                labelIDs: ["INBOX"],
                labels: ["INBOX"],
                unread: false
            )
        ]
        let smartBackingRow = makeMailboxRow(
            threadID: "mailbox-cluster:support",
            latestSourceRecordID: "smart-message-1",
            receivedAt: "2026-06-10T09:00:00+05:30",
            title: "Raw support cluster",
            children: smartChildren
        )
        let normalFirstPageRow = makeMailboxRow(
            threadID: "normal-thread-1",
            latestSourceRecordID: "normal-message-1",
            receivedAt: "2026-06-10T08:00:00+05:30",
            title: "Normal first page row"
        )
        let normalSecondPageRow = makeMailboxRow(
            threadID: "normal-thread-2",
            latestSourceRecordID: "normal-message-2",
            receivedAt: "2026-06-09T08:00:00+05:30",
            title: "Normal second page row"
        )
        let firstPage = MailboxResponse(
            label: .inbox,
            totalThreads: 3,
            nextCursor: "cursor-2",
            loadedThreads: 2,
            sections: [GmailThreadSection(id: "today", title: "Today", rows: [smartBackingRow, normalFirstPageRow])],
            fullImportRunning: false,
            fullImportCompleted: true
        )
        let secondPage = MailboxResponse(
            label: .inbox,
            totalThreads: 3,
            nextCursor: nil,
            loadedThreads: 1,
            sections: [GmailThreadSection(id: "yesterday", title: "Yesterday", rows: [normalSecondPageRow])],
            fullImportRunning: false,
            fullImportCompleted: true
        )
        let smartRow = SmartInboxRow(
            id: "smart-row-support",
            rowKey: "mailbox-cluster:support",
            rowType: "verified_group",
            title: "Support case updates",
            summary: "Grouped support updates.",
            primarySender: "Support <support@example.com>",
            latestMessageAt: "2026-06-10T09:00:00+05:30",
            latestMessageID: "smart-message-1",
            readerThreadID: "smart-thread-1",
            sourceThreadIDs: ["smart-thread-1"],
            sourceMessageIDs: ["smart-message-1"],
            confidenceTier: "strong",
            confidence: 0.9,
            groupingReason: ["source": .string("mailbox_projection")],
            offlineStatus: "ready",
            readiness: "ready",
            actionType: "review",
            priority: 20
        )
        let session = DemoAppFixtures.appSession.withSmartProjection(mailbox: firstPage, smartRows: [smartRow])
        let client = SmartSessionAppClient(session: session, mailbox: firstPage, mailboxPages: ["cursor-2": secondPage])
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral())
        )
        store.setSessionToken("live-session-token")

        await store.load()

        XCTAssertEqual(store.flatRows.map(\.threadID), ["smart-thread-1"])
        XCTAssertEqual(store.flatRows.map(\.title), ["Support case updates"])
        XCTAssertEqual(store.flatRows.filter { $0.threadID == "smart-thread-1" }.count, 1)
        XCTAssertFalse(store.canLoadMoreMailbox)
        XCTAssertNil(store.mailboxFooter)

        await store.loadMoreMailbox()

        XCTAssertEqual(client.mailboxCursors, [nil])
        XCTAssertEqual(store.flatRows.map(\.threadID), ["smart-thread-1"])
        XCTAssertFalse(store.canLoadMoreMailbox)
    }

    func testSmartInboxDoesNotReplaceNonInboxLabels() async {
        let sentRow = makeMailboxRow(
            threadID: "sent-thread",
            latestSourceRecordID: "sent-message",
            receivedAt: "2026-05-29T11:00:00+05:30",
            title: "Sent row",
            labelIDs: ["SENT"],
            labels: ["SENT"]
        )
        let sentMailbox = MailboxResponse(
            label: .sent,
            totalThreads: 1,
            loadedThreads: 1,
            sections: [GmailThreadSection(id: "today", title: "Today", rows: [sentRow])],
            fullImportRunning: false,
            fullImportCompleted: true
        )
        let session = DemoAppFixtures.appSession.withSmartProjection(mailbox: DemoAppFixtures.mailbox)
        let store = InboxStore(
            client: SmartSessionAppClient(session: session, mailbox: sentMailbox),
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral())
        )
        store.setSessionToken("live-session-token")

        await store.load()
        await store.setMailboxLabel(.sent)

        XCTAssertEqual(store.flatRows.map(\.threadID), ["sent-thread"])
        XCTAssertEqual(store.flatRows.first?.title, "Sent row")
    }

    func testInitialDemoLoadShowsRowsAndSelection() async {
        let store = InboxStore(client: DemoAppClient(), sessionCache: AppSessionCache(defaults: .ephemeral()), threadCache: ThreadCache(defaults: .ephemeral()))

        await store.load()

        XCTAssertFalse(store.flatRows.isEmpty)
        XCTAssertEqual(store.selectedThreadID, "demo-google-today")
    }

    func testOpenActiveSelectionSetsReaderThreadID() async {
        let store = InboxStore(client: DemoAppClient(), sessionCache: AppSessionCache(defaults: .ephemeral()), threadCache: ThreadCache(defaults: .ephemeral()))

        await store.load()
        store.select(threadID: "demo-rbi-today", prefetch: false)
        store.openActiveSelection()

        XCTAssertEqual(store.readerThreadID, "demo-rbi-today")
        XCTAssertEqual(store.selectedThreadID, "demo-rbi-today")
    }

    func testOpenReaderLoadsSingleMessageThread() async {
        let store = InboxStore(client: DemoAppClient(), sessionCache: AppSessionCache(defaults: .ephemeral()), threadCache: ThreadCache(defaults: .ephemeral()))

        await store.load()
        let task = store.openReader(threadID: "demo-google-today")
        await task.value

        XCTAssertEqual(store.readerThreadID, "demo-google-today")
        XCTAssertEqual(store.readerRow?.threadID, "demo-google-today")
        XCTAssertEqual(store.readerThread?.messages.count, 1)
        XCTAssertNil(store.readerError)
    }

    func testOpenReaderLoadsGroupedThread() async {
        let store = InboxStore(client: DemoAppClient(), sessionCache: AppSessionCache(defaults: .ephemeral()), threadCache: ThreadCache(defaults: .ephemeral()))

        await store.load()
        let task = store.openReader(threadID: "demo-apple-today")
        await task.value

        XCTAssertEqual(store.readerThreadID, "demo-apple-today")
        XCTAssertEqual(store.readerRow?.isGrouped, true)
        XCTAssertEqual(store.readerThread?.messages.count, 5)
    }

    func testCloseReaderPreservesSelection() async {
        let store = InboxStore(client: DemoAppClient(), sessionCache: AppSessionCache(defaults: .ephemeral()), threadCache: ThreadCache(defaults: .ephemeral()))

        await store.load()
        let task = store.openReader(threadID: "demo-rbi-today")
        await task.value
        store.closeReader()

        XCTAssertNil(store.readerThreadID)
        XCTAssertEqual(store.selectedThreadID, "demo-rbi-today")
    }

    func testOpenReaderFailureRecordsErrorAndKeepsInboxVisible() async {
        let store = InboxStore(client: FailingThreadAppClient(), sessionCache: AppSessionCache(defaults: .ephemeral()), threadCache: ThreadCache(defaults: .ephemeral()))

        await store.load()
        let task = store.openReader(threadID: "demo-google-today")
        await task.value

        XCTAssertEqual(store.readerThreadID, "demo-google-today")
        XCTAssertNil(store.readerThread)
        XCTAssertNotNil(store.readerError)
        XCTAssertNotNil(store.threadErrors["demo-google-today"])
        XCTAssertFalse(store.flatRows.isEmpty)
    }

    func testOpenReaderUsesLocalThreadStoreWhenNetworkThreadFetchFails() async {
        let localStore = MemoryLocalMailStore()
        let userID = DemoAppFixtures.appSession.user.id
        let cachedThread = DemoAppFixtures.threads["demo-google-today"]!
        localStore.writeThread(cachedThread, userID: userID, threadID: "demo-google-today")
        let store = InboxStore(
            client: FailingThreadAppClient(),
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            localMailStore: localStore
        )

        await store.load()
        let task = store.openReader(threadID: "demo-google-today")
        await task.value

        XCTAssertEqual(store.readerThreadID, "demo-google-today")
        XCTAssertEqual(store.readerThread, cachedThread)
        XCTAssertNil(store.readerError)
        XCTAssertNil(store.threadErrors["demo-google-today"])
    }

    func testFailedRefreshKeepsCachedInboxVisible() async {
        let defaults = UserDefaults.ephemeral()
        let cache = AppSessionCache(defaults: defaults)
        cache.write(DemoAppFixtures.appSession.withSmartProjection())
        let store = InboxStore(client: FailingAppClient(), sessionCache: cache, threadCache: ThreadCache(defaults: defaults))
        store.setSessionToken("live-session-token")

        await store.load()

        XCTAssertEqual(store.phase, .loaded)
        XCTAssertEqual(store.session?.mailbox.totalThreads, DemoAppFixtures.appSession.mailbox.totalThreads)
        XCTAssertEqual(store.flatRows.first?.title, "Apple review needs one screenshot")
        XCTAssertEqual(store.refreshFailed, true)
    }

    func testFailedRefreshDoesNotShowLegacyCachedRawInboxWithoutSmartProjection() async {
        let defaults = UserDefaults.ephemeral()
        let cache = AppSessionCache(defaults: defaults)
        cache.write(DemoAppFixtures.appSession)
        let store = InboxStore(client: FailingAppClient(), sessionCache: cache, threadCache: ThreadCache(defaults: defaults))
        store.setSessionToken("live-session-token")

        await store.load()

        XCTAssertTrue(store.flatRows.isEmpty)
        XCTAssertEqual(store.refreshFailed, true)
        if case .failed = store.phase {
            // Expected: the stale raw cache is not a valid smart Inbox fallback.
        } else {
            XCTFail("Expected legacy raw cache to be rejected for the smart Inbox.")
        }
    }

    func testUnauthorizedRefreshClearsCachedSessionInsteadOfShowingStaleSetup() async {
        let defaults = UserDefaults.ephemeral()
        let cache = AppSessionCache(defaults: defaults)
        let localStore = MemoryLocalMailStore()
        cache.write(DemoAppFixtures.appSession)
        localStore.writeSession(DemoAppFixtures.appSession)
        let client = FailingAppClient(statusCode: 401)
        let store = InboxStore(
            client: client,
            sessionCache: cache,
            threadCache: ThreadCache(defaults: defaults),
            localMailStore: localStore
        )
        store.setSessionToken("stale-session-token")

        await store.load()

        XCTAssertEqual(store.phase, .failed("Sign in with Google to load your mailbox."))
        XCTAssertNil(store.session)
        XCTAssertNil(cache.read())
        XCTAssertNil(localStore.readSession())
        XCTAssertNil(client.sessionToken)
    }

    func testNonInboxLabelDoesNotFallBackToSessionInboxWhenMailboxFetchFails() async {
        let client = MailboxFailingAfterSessionAppClient()
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")

        await store.load()
        XCTAssertFalse(store.flatRows.isEmpty)

        await store.setMailboxLabel(.drafts)

        XCTAssertEqual(store.mailboxTitle, "Drafts")
        XCTAssertTrue(store.flatRows.isEmpty)
        XCTAssertEqual(client.mailboxLabels.last, .drafts)
    }

    func testActiveMailboxFiltersRowsThatDoNotBelongToSelectedLabel() async {
        let sentRow = makeMailboxRow(
            threadID: "sent-thread",
            latestSourceRecordID: "sent-message",
            receivedAt: "2026-05-26T19:31:00+05:30",
            title: "Sent message",
            labelIDs: ["SENT"],
            labels: ["SENT"]
        )
        let client = MismatchedMailboxRowsAppClient(row: sentRow)
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")

        await store.load()
        await store.setMailboxLabel(.drafts)

        XCTAssertEqual(store.mailboxTitle, "Drafts")
        XCTAssertTrue(store.flatRows.isEmpty)
        XCTAssertEqual(client.mailboxLabels.last, .drafts)
    }

    func testLocalMailboxCacheRejectsPayloadStoredUnderWrongLabel() {
        let store = MemoryLocalMailStore()
        let userID = DemoAppFixtures.appSession.user.id
        let sentMailbox = MailboxResponse(label: .sent, totalThreads: 1, sections: DemoAppFixtures.mailbox.sections)

        store.writeMailbox(sentMailbox, userID: userID, label: .drafts)

        XCTAssertNil(store.readMailbox(userID: userID, label: .drafts))
    }

    func testThreadPrefetchDedupesInFlightRequests() async {
        let client = SlowThreadAppClient()
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )

        await store.load()
        async let first: Void = store.prefetchThread(threadID: "demo-special", force: true, silent: false)
        async let second: Void = store.prefetchThread(threadID: "demo-special", force: true, silent: false)
        _ = await (first, second)

        XCTAssertEqual(client.threadCallCounts["demo-special"], 1)
    }

    func testLiveModeRequiresSessionTokenBeforeLoading() async {
        let store = InboxStore(
            client: FailingAppClient(),
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral())
        )

        await store.load()

        XCTAssertEqual(store.phase, .failed("Sign in with Google to load your mailbox."))
        XCTAssertNil(store.session)
    }

    func testTodoMapperSplitsDashboardFeedSections() {
        let snapshot = TodoHomeMapper.snapshot(from: DemoAppFixtures.appSession, now: Date(timeIntervalSince1970: 0))

        XCTAssertEqual(snapshot.now.rows.map(\.entityID), ["demo-apple-today"])
        XCTAssertEqual(snapshot.laterToday.rows.map(\.entityID), ["demo-github-today", "manual-task:demo-manual-seed"])
        XCTAssertEqual(snapshot.worthKnowing.rows.map(\.entityID), ["demo-rbi-today"])
        XCTAssertEqual(snapshot.now.title, "Now")
        XCTAssertEqual(snapshot.laterToday.title, "Later Today")
        XCTAssertEqual(snapshot.worthKnowing.title, "Worth Knowing")
        XCTAssertEqual(
            snapshot.agenda.map(\.id),
            [
                "demo-calendar-scrum",
                "demo-calendar-pairing",
                "demo-calendar-lunch",
                "demo-calendar-office-hours",
                "demo-calendar-update",
            ]
        )
        XCTAssertEqual(snapshot.agenda.map(\.time), ["10:00", "12:00", "13:30", "14:45", "15:00"])
    }

    func testTodoMapperUsesEmptySmartWorkQueueInsteadOfLegacyDashboardFeed() {
        let current = DemoAppFixtures.appSession
        let session = AppSessionResponse(
            user: current.user,
            readiness: current.readiness,
            dashboard: current.dashboard,
            mailbox: current.mailbox,
            sync: current.sync,
            smartInbox: makeSmartInbox(rows: [makeSmartInboxRow()]),
            smartWorkQueue: makeSmartWorkQueue(needsAction: []),
            smartReadiness: makeSmartReadiness(readyRows: 1)
        )

        let snapshot = TodoHomeMapper.snapshot(from: session, now: Date(timeIntervalSince1970: 0))

        XCTAssertEqual([snapshot.now.title, snapshot.laterToday.title, snapshot.worthKnowing.title], ["Needs Action", "Waiting", "Conversation Updates"])
        XCTAssertTrue(snapshot.now.rows.isEmpty)
        XCTAssertTrue(snapshot.laterToday.rows.isEmpty)
        XCTAssertTrue(snapshot.worthKnowing.rows.isEmpty)
    }

    func testTodoMapperDoesNotExposeSmartWorkSummaryAsDetailText() {
        let session = DemoAppFixtures.appSession.withSmartProjection()

        let snapshot = TodoHomeMapper.snapshot(from: session, now: Date(timeIntervalSince1970: 0))

        let row = snapshot.now.rows.first
        XCTAssertEqual(row?.title, "Reply to Apple Developer review")
        XCTAssertEqual(row?.detailText, "")
        XCTAssertEqual(row?.actionLabel, "Reply")
        XCTAssertEqual(row?.gmailThreadID, "demo-apple-today")
    }

    func testTodoRowsUseInboxDerivedMetadata() async {
        let store = InboxStore(client: DemoAppClient(), sessionCache: AppSessionCache(defaults: .ephemeral()), threadCache: ThreadCache(defaults: .ephemeral()))

        await store.load()
        let snapshot = TodoHomeMapper.snapshot(from: DemoAppFixtures.appSession, inboxRows: store.flatRows, now: Date(timeIntervalSince1970: 0))

        let nowRow = snapshot.now.rows.first
        XCTAssertEqual(nowRow?.sender, "Apple Developer")
        XCTAssertEqual(nowRow?.title, "App Review needs one more screenshot for macOS")
        XCTAssertEqual(nowRow?.timeLabel, "12:46 PM")
        XCTAssertEqual(nowRow?.detailText, "Apple Developer needs one more screenshot before review can continue. Confirm the slot or move it out of today's work.")
        XCTAssertEqual(nowRow?.actionLabel, "Open source")
        XCTAssertEqual(nowRow?.gmailThreadID, "demo-apple-today")

        let manualRow = snapshot.laterToday.rows.first { $0.entityID == "manual-task:demo-manual-seed" }
        XCTAssertEqual(manualRow?.sender, "Manual")
        XCTAssertEqual(manualRow?.title, "New to-do")
        XCTAssertEqual(manualRow?.timeLabel, "")
        XCTAssertNil(manualRow?.gmailThreadID)
    }

    func testTodoRowsHandleDuplicateInboxThreadIDs() {
        let rawRow = InboxRowViewModel(
            id: "demo-apple-today",
            sender: "Apple Developer",
            title: "Raw Apple thread title",
            summary: "Raw summary",
            receivedAt: "2026-05-29T12:46:00+05:30",
            section: "Today",
            isUnread: false,
            isGrouped: false,
            isSelected: false,
            threadID: "demo-apple-today",
            focusedMessageID: nil,
            messageCount: 1,
            timeLabel: "12:46 PM",
            hasAttachments: false,
            presentationStatus: nil,
            isChild: false,
            isExpandable: false,
            isExpanded: false
        )
        let smartRow = InboxRowViewModel(
            id: "smart-row-demo-apple-today",
            sender: "Apple Developer",
            title: "Smart Apple review request",
            summary: "AI summary",
            receivedAt: "2026-05-29T12:46:00+05:30",
            section: "Today",
            isUnread: false,
            isGrouped: true,
            isSelected: false,
            threadID: "demo-apple-today",
            focusedMessageID: nil,
            messageCount: 3,
            timeLabel: "12:46 PM",
            hasAttachments: false,
            presentationStatus: "ai_ready",
            isChild: false,
            isExpandable: true,
            isExpanded: false
        )

        let snapshot = TodoHomeMapper.snapshot(
            from: DemoAppFixtures.appSession,
            inboxRows: [rawRow, smartRow],
            now: Date(timeIntervalSince1970: 0)
        )

        let nowRow = snapshot.now.rows.first
        XCTAssertEqual(nowRow?.sender, "Apple Developer")
        XCTAssertEqual(nowRow?.title, "Smart Apple review request")
        XCTAssertEqual(nowRow?.timeLabel, "12:46 PM")
        XCTAssertEqual(nowRow?.gmailThreadID, "demo-apple-today")
    }

    func testTodoRowsFallbackWhenInboxRowIsMissing() {
        let snapshot = TodoHomeMapper.snapshot(from: DemoAppFixtures.appSession, inboxRows: [], now: Date(timeIntervalSince1970: 0))

        let nowRow = snapshot.now.rows.first
        XCTAssertEqual(nowRow?.sender, "3 emails from Apple Developer")
        XCTAssertEqual(nowRow?.title, "RSVP within 72 hrs to confirm your macOS review slot")
        XCTAssertEqual(nowRow?.timeLabel, "")
        XCTAssertEqual(nowRow?.sourceLabel, "3 emails from Apple Developer")
    }

    func testTodoSnapshotKeepsThreeSectionsWhenFeedIsEmpty() {
        let current = DemoAppFixtures.appSession
        let emptySession = AppSessionResponse(
            user: current.user,
            readiness: current.readiness,
            dashboard: current.dashboard.replacingFeed(FeedResponse(now: [], today: [], worthKnowing: [])),
            mailbox: current.mailbox,
            sync: current.sync
        )

        let snapshot = TodoHomeMapper.snapshot(from: emptySession, now: Date(timeIntervalSince1970: 0))

        XCTAssertEqual([snapshot.now.title, snapshot.laterToday.title, snapshot.worthKnowing.title], ["Now", "Later Today", "Worth Knowing"])
        XCTAssertTrue(snapshot.now.rows.isEmpty)
        XCTAssertTrue(snapshot.laterToday.rows.isEmpty)
        XCTAssertTrue(snapshot.worthKnowing.rows.isEmpty)
        XCTAssertTrue(snapshot.agenda.isEmpty)
        XCTAssertNil(snapshot.inboxBuildStatus)
    }

    func testTodoSnapshotDoesNotTreatDashboardReadinessAsInboxSetup() {
        let current = DemoAppFixtures.appSession
        let inboxReadySession = AppSessionResponse(
            user: current.user,
            readiness: PostLoginReadinessResponse(
                mode: "returning",
                stage: "welcome_back",
                readyToEnter: true,
                dashboardReady: false,
                mailboxReady: true,
                readyDashboardCount: 0,
                readyMailGroupCount: 3,
                fullImportRunning: false,
                fullImportCompleted: false,
                userDisplayName: current.user.displayName,
                errorMessage: nil
            ),
            dashboard: current.dashboard.replacingFeed(FeedResponse(now: [], today: [], worthKnowing: [])),
            mailbox: current.mailbox,
            sync: AppSessionSyncState(
                lastSyncAt: current.sync.lastSyncAt,
                lastError: nil,
                enrichmentPendingCount: 0,
                readyGroupCount: 3,
                oldestImportedAt: current.sync.oldestImportedAt,
                fullImportRunning: false,
                fullImportCompleted: false
            )
        )

        let snapshot = TodoHomeMapper.snapshot(from: inboxReadySession, now: Date(timeIntervalSince1970: 0))

        XCTAssertNil(snapshot.inboxBuildStatus)
    }

    func testTodoSnapshotShowsAITitleBuildStateWhenAllGroupsArePending() {
        let current = DemoAppFixtures.appSession
        let pendingSession = AppSessionResponse(
            user: current.user,
            readiness: current.readiness,
            dashboard: current.dashboard.replacingFeed(FeedResponse(now: [], today: [], worthKnowing: [])),
            mailbox: current.mailbox,
            sync: AppSessionSyncState(
                lastSyncAt: current.sync.lastSyncAt,
                lastError: nil,
                enrichmentPendingCount: 7,
                readyGroupCount: 0,
                oldestImportedAt: current.sync.oldestImportedAt,
                fullImportRunning: false,
                fullImportCompleted: false
            )
        )

        let snapshot = TodoHomeMapper.snapshot(from: pendingSession, now: Date(timeIntervalSince1970: 0))

        XCTAssertEqual(snapshot.aiBuildStatus, "Building AI titles from your inbox")
        XCTAssertEqual(snapshot.inboxBuildStatus, "Preparing your inbox")
    }

    func testTodoSnapshotShowsInboxBuildStateDuringFirstRunImport() {
        let current = DemoAppFixtures.appSession
        let importingSession = AppSessionResponse(
            user: current.user,
            readiness: PostLoginReadinessResponse(
                mode: "first_time",
                stage: "importing_recent_gmail",
                readyToEnter: false,
                dashboardReady: false,
                mailboxReady: false,
                readyDashboardCount: 0,
                readyMailGroupCount: 0,
                fullImportRunning: true,
                fullImportCompleted: false,
                userDisplayName: current.user.displayName,
                errorMessage: nil
            ),
            dashboard: current.dashboard.replacingFeed(FeedResponse(now: [], today: [], worthKnowing: [])),
            mailbox: MailboxResponse(label: .inbox, totalThreads: 0, nextCursor: nil, sections: [], fullImportRunning: true, fullImportCompleted: false),
            sync: AppSessionSyncState(
                lastSyncAt: nil,
                lastError: nil,
                enrichmentPendingCount: 0,
                readyGroupCount: 0,
                oldestImportedAt: nil,
                fullImportRunning: true,
                fullImportCompleted: false
            )
        )

        let snapshot = TodoHomeMapper.snapshot(from: importingSession, now: Date(timeIntervalSince1970: 0))

        XCTAssertEqual(snapshot.inboxBuildStatus, "Syncing Gmail to prepare your inbox")
    }

    func testManualTaskCreationRefreshesDashboard() async throws {
        let store = InboxStore(client: DemoAppClient(), sessionCache: AppSessionCache(defaults: .ephemeral()), threadCache: ThreadCache(defaults: .ephemeral()))

        await store.load()
        let task = try await store.createManualTask(title: "Ship macOS to-do page", notes: "Use backend tasks", section: "today")

        XCTAssertEqual(task.status, "open")
        XCTAssertTrue(store.session?.dashboard.feed.today.contains { $0.entityID == task.entityID } ?? false)
    }

    func testCompletionRemovesItemFromDashboard() async throws {
        let store = InboxStore(client: DemoAppClient(), sessionCache: AppSessionCache(defaults: .ephemeral()), threadCache: ThreadCache(defaults: .ephemeral()))

        await store.load()
        let entityID = "demo-apple-today"
        XCTAssertTrue(store.session?.dashboard.feed.now.contains { $0.entityID == entityID } ?? false)

        _ = try await store.completeEntity(entityID: entityID)

        XCTAssertFalse(store.session?.dashboard.feed.now.contains { $0.entityID == entityID } ?? true)
    }

    func testManualSyncRunsImmediateBackendSyncAndRefreshesActiveMailbox() async {
        let client = ManualSyncAppClient()
        let store = InboxStore(client: client, sessionCache: AppSessionCache(defaults: .ephemeral()), threadCache: ThreadCache(defaults: .ephemeral()))
        store.setSessionToken("live-session-token")

        await store.load()
        await store.setMailboxLabel(.spam)
        await store.syncNow()

        XCTAssertEqual(client.syncNowCallCount, 1)
        XCTAssertEqual(client.mailboxLabels.last, .spam)
        XCTAssertEqual(store.mailboxTitle, "Spam")
        XCTAssertFalse(store.manualSyncInProgress)
    }

    func testMailboxPaginationFooterAndLoadMoreAppendRows() async {
        let client = PaginatedMailboxAppClient()
        let store = InboxStore(client: client, sessionCache: AppSessionCache(defaults: .ephemeral()), threadCache: ThreadCache(defaults: .ephemeral()))
        store.setSessionToken("live-session-token")

        await store.load()

        XCTAssertEqual(store.flatRows.map(\.threadID), ["demo-google-today"])
        XCTAssertEqual(store.mailboxFooterText, "Showing 1 of 2. Load more...")

        await store.loadMoreMailbox()

        XCTAssertEqual(store.flatRows.map(\.threadID), ["demo-google-today", "demo-github-today"])
        XCTAssertNil(store.mailboxFooterText)
        XCTAssertEqual(client.mailboxCursors, [nil, "cursor-2"])
    }

    func testRefreshAfterPaginationPreservesLoadedRowsAndDoesNotAutoReloadSameCursor() async {
        let client = PaginatedMailboxAppClient()
        let store = InboxStore(client: client, sessionCache: AppSessionCache(defaults: .ephemeral()), threadCache: ThreadCache(defaults: .ephemeral()))
        store.setSessionToken("live-session-token")

        await store.load()
        await store.loadMoreMailbox(automatic: true)
        await store.refresh()
        await store.loadMoreMailbox(automatic: true)

        XCTAssertEqual(store.flatRows.map(\.threadID), ["demo-google-today", "demo-github-today"])
        XCTAssertNil(store.mailboxFooterText)
        XCTAssertEqual(client.mailboxCursors, [nil, "cursor-2"])
    }

    func testManualSyncDropsPreviouslyLoadedPagesSoStaleRowsDoNotSurvive() async {
        let client = PaginatedMailboxAppClient()
        let store = InboxStore(client: client, sessionCache: AppSessionCache(defaults: .ephemeral()), threadCache: ThreadCache(defaults: .ephemeral()))
        store.setSessionToken("live-session-token")

        await store.load()
        await store.loadMoreMailbox()

        XCTAssertEqual(store.flatRows.map(\.threadID), ["demo-google-today", "demo-github-today"])

        await store.syncNow()

        XCTAssertEqual(store.flatRows.map(\.threadID), ["demo-google-today"])
        XCTAssertEqual(store.mailboxFooterText, "Showing 1 of 2. Load more...")
        XCTAssertEqual(client.mailboxCursors, [nil, "cursor-2", nil])
    }

    func testMailboxRefreshDropsRawRowsReplacedByEnrichedGroups() {
        let rawRow = makeMailboxRow(
            threadID: "gmail-thread-1",
            latestSourceRecordID: "msg-1",
            receivedAt: "2026-05-26T19:31:00+05:30",
            title: "Raw Gmail subject"
        )
        let olderRow = makeMailboxRow(
            threadID: "gmail-thread-older",
            latestSourceRecordID: "msg-older",
            receivedAt: "2026-05-25T09:00:00+05:30",
            title: "Older row"
        )
        let enrichedRow = makeMailboxRow(
            threadID: "mail-group-1",
            entityID: "mail-group-1",
            latestSourceRecordID: "msg-1",
            receivedAt: "2026-05-26T19:33:00+05:30",
            title: "AI grouped title",
            lifecycleSourceIDs: ["msg-1", "msg-2"]
        )
        let current = MailboxResponse(
            label: .inbox,
            totalThreads: 2,
            nextCursor: nil,
            loadedThreads: 2,
            windowDays: 90,
            sections: [GmailThreadSection(id: "today", title: "Today", rows: [rawRow, olderRow])],
            fullImportCompleted: false
        )
        let firstPage = MailboxResponse(
            label: .inbox,
            totalThreads: 2,
            nextCursor: nil,
            loadedThreads: 1,
            windowDays: 90,
            sections: [GmailThreadSection(id: "today", title: "Today", rows: [enrichedRow])],
            fullImportCompleted: false
        )

        let merged = current.preservingLoadedPages(afterRefreshingFirstPage: firstPage)

        XCTAssertEqual(merged.sections.flatMap(\.rows).map(\.threadID), ["mail-group-1", "gmail-thread-older"])
    }

    func testMailboxRefreshDropsStaleCachedGroupsInsideRefreshedFirstPageWindow() {
        let staleGroupedRow = makeMailboxRow(
            threadID: "mailbox-cluster:psu",
            latestSourceRecordID: "psu-stale-group",
            receivedAt: "2026-06-13T00:59:24+05:30",
            title: "PSU updates"
        )
        let olderLoadedRow = makeMailboxRow(
            threadID: "gmail-thread-older",
            latestSourceRecordID: "msg-older",
            receivedAt: "2026-06-10T09:32:31+05:30",
            title: "Older loaded row"
        )
        let refreshedRow = makeMailboxRow(
            threadID: "penn-state-thread",
            latestSourceRecordID: "penn-state-message",
            receivedAt: "2026-06-13T00:36:25+05:30",
            title: "Penn State admissions visa document next steps"
        )
        let current = MailboxResponse(
            label: .inbox,
            totalThreads: 2,
            nextCursor: nil,
            loadedThreads: 2,
            windowDays: 90,
            sections: [GmailThreadSection(id: "today", title: "Today", rows: [staleGroupedRow, olderLoadedRow])],
            fullImportCompleted: false
        )
        let firstPage = MailboxResponse(
            label: .inbox,
            totalThreads: 2,
            nextCursor: nil,
            loadedThreads: 1,
            windowDays: 90,
            sections: [GmailThreadSection(id: "today", title: "Today", rows: [refreshedRow])],
            fullImportCompleted: false
        )

        let merged = current.preservingLoadedPages(afterRefreshingFirstPage: firstPage)

        XCTAssertEqual(merged.sections.flatMap(\.rows).map(\.threadID), ["penn-state-thread", "gmail-thread-older"])
    }

    func testComposeAndReplyDoNotCallBackendWhenSendScopeIsMissing() async throws {
        let client = SendTrackingAppClient()
        let store = InboxStore(client: client, sessionCache: AppSessionCache(defaults: .ephemeral()), threadCache: ThreadCache(defaults: .ephemeral()))

        await store.load()

        let compose = try await store.sendCompose(to: ["person@example.com"], subject: "Subject", bodyText: "Body")
        let reply = try await store.sendReply(threadID: "demo-google-today", bodyText: "Reply")

        XCTAssertEqual(compose.state, .reauthRequired)
        XCTAssertEqual(reply.state, .reauthRequired)
        XCTAssertEqual(reply.mailboxThreadID, "demo-google-today")
        XCTAssertEqual(client.composeCallCount, 0)
        XCTAssertEqual(client.replyCallCount, 0)
    }

    func testMailboxChangedSSEForcesActiveMailboxRefresh() async {
        let initialMailbox = makeSingleRowMailbox(threadID: "old-thread", title: "Old mailbox row", receivedAt: "2026-05-29T09:30:00+05:30")
        let refreshedMailbox = makeSingleRowMailbox(threadID: "new-thread", title: "Fresh mailbox row", receivedAt: "2026-05-29T10:30:00+05:30")
        let client = RealtimeEventAppClient(sessionMailbox: initialMailbox, mailboxResponses: [initialMailbox, refreshedMailbox])
        let store = InboxStore(client: client, sessionCache: AppSessionCache(defaults: .ephemeral()), threadCache: ThreadCache(defaults: .ephemeral()))

        await store.load()
        XCTAssertEqual(store.flatRows.map(\.title), ["Old mailbox row"])

        store.handleMailboxServerEvent(
            MailboxServerEvent(
                id: "7",
                event: "mailbox-changed",
                data: #"{"mailbox_label":"inbox","payload":{"mailbox_revision":"rev-2","mailbox_labels":["inbox"]}}"#
            )
        )
        try? await Task.sleep(nanoseconds: 1_200_000_000)

        XCTAssertEqual(store.flatRows.map(\.title), ["Fresh mailbox row"])
        XCTAssertEqual(client.mailboxCallCount, 2)
        XCTAssertEqual(store.lastSSEEventID, "7")
        XCTAssertEqual(store.lastSSEEventType, "mailbox-changed")
        XCTAssertNotNil(store.lastForcedMailboxRefreshAt)
        XCTAssertNil(store.lastForcedMailboxRefreshError)
    }

    func testMailboxChangedSSEIgnoresUnrelatedMailboxLabel() async {
        let initialMailbox = makeSingleRowMailbox(threadID: "inbox-thread", title: "Inbox row")
        let refreshedMailbox = makeSingleRowMailbox(threadID: "sent-thread", title: "Sent row")
        let client = RealtimeEventAppClient(sessionMailbox: initialMailbox, mailboxResponses: [initialMailbox, refreshedMailbox])
        let store = InboxStore(client: client, sessionCache: AppSessionCache(defaults: .ephemeral()), threadCache: ThreadCache(defaults: .ephemeral()))

        await store.load()

        store.handleMailboxServerEvent(
            MailboxServerEvent(
                id: "8",
                event: "mailbox-changed",
                data: #"{"mailbox_label":"sent","payload":{"mailbox_revision":"rev-3","mailbox_labels":["sent"]}}"#
            )
        )
        try? await Task.sleep(nanoseconds: 1_200_000_000)

        XCTAssertEqual(store.flatRows.map(\.title), ["Inbox row"])
        XCTAssertEqual(client.mailboxCallCount, 1)
        XCTAssertNil(store.lastForcedMailboxRefreshAt)
    }

    func testDashboardChangedSSERefreshesSessionEvenForDuplicateRevision() async {
        let mailbox = makeSingleRowMailbox(threadID: "inbox-thread", title: "Inbox row")
        let client = RealtimeEventAppClient(sessionMailbox: mailbox, mailboxResponses: [mailbox, mailbox, mailbox])
        let store = InboxStore(client: client, sessionCache: AppSessionCache(defaults: .ephemeral()), threadCache: ThreadCache(defaults: .ephemeral()))

        await store.load()
        let initialSessionCalls = client.appSessionCallCount

        store.handleMailboxServerEvent(
            MailboxServerEvent(
                id: nil,
                event: "sync-state",
                data: #"{"mailbox_revision":"rev-1"}"#
            )
        )
        try? await Task.sleep(nanoseconds: 1_200_000_000)
        XCTAssertEqual(client.appSessionCallCount, initialSessionCalls)

        store.handleMailboxServerEvent(
            MailboxServerEvent(
                id: "9",
                event: "dashboard-changed",
                data: #"{"mailbox_label":null,"payload":{"mailbox_revision":"rev-1"}}"#
            )
        )
        try? await Task.sleep(nanoseconds: 1_200_000_000)

        XCTAssertGreaterThan(client.appSessionCallCount, initialSessionCalls)
        XCTAssertEqual(store.lastSSEEventID, "9")
    }
}

private func makeMailboxRow(
    threadID: String,
    entityID: String? = nil,
    latestSourceRecordID: String,
    receivedAt: String,
    title: String,
    lifecycleSourceIDs: [String]? = nil,
    children: [GmailThreadChildRow]? = nil,
    hasAttachments: Bool? = nil,
    attachmentCount: Int? = nil,
    labelIDs: [String] = ["INBOX"],
    labels: [String] = ["INBOX"]
) -> GmailThreadRow {
    let updates = (lifecycleSourceIDs ?? [latestSourceRecordID]).map {
        GmailThreadUpdate(sourceRecordID: $0, receivedAt: receivedAt, subject: title, sender: "Sender", summary: title)
    }
    return GmailThreadRow(
        threadID: threadID,
        entityID: entityID ?? threadID,
        title: title,
        href: "/v1/mailbox/threads/\(threadID)",
        latestSourceRecordID: latestSourceRecordID,
        latestReceivedAt: receivedAt,
        latestMessageAt: receivedAt,
        latestSubject: title,
        latestSender: "Sender",
        sender: "Sender",
        participants: ["Sender"],
        messageCount: updates.count,
        summary: title,
        snippet: title,
        hasAttachments: hasAttachments,
        attachmentCount: attachmentCount,
        labelIDs: labelIDs,
        labels: labels,
        unread: false,
        actionNeeded: false,
        actionType: "open",
        actionTypeKey: "open",
        priority: 0,
        dashboardVisible: false,
        currentState: .waiting,
        lifecycleState: "active",
        outcomeType: nil,
        lifecycleUpdates: updates,
        children: children,
        enrichmentStatus: "ready"
    )
}

private func makeSingleRowMailbox(threadID: String, title: String, receivedAt: String = "2026-05-29T09:30:00+05:30") -> MailboxResponse {
    let row = makeMailboxRow(
        threadID: threadID,
        latestSourceRecordID: "\(threadID)-message",
        receivedAt: receivedAt,
        title: title
    )
    return MailboxResponse(
        label: .inbox,
        totalThreads: 1,
        loadedThreads: 1,
        sections: [GmailThreadSection(id: "today", title: "Today", rows: [row])],
        fullImportRunning: false,
        fullImportCompleted: true
    )
}

private extension AppSessionResponse {
    func withSmartProjection(mailbox: MailboxResponse? = nil, smartRows: [SmartInboxRow]? = nil) -> AppSessionResponse {
        let rows = smartRows ?? [makeSmartInboxRow()]
        return AppSessionResponse(
            user: user,
            readiness: readiness,
            dashboard: dashboard,
            mailbox: mailbox ?? self.mailbox,
            sync: sync,
            smartInbox: makeSmartInbox(rows: rows),
            smartWorkQueue: makeSmartWorkQueue(needsAction: [makeSmartWorkItem()]),
            smartReadiness: makeSmartReadiness(readyRows: rows.count)
        )
    }
}

private func makeSmartInbox(rows: [SmartInboxRow]) -> SmartInboxResponse {
    SmartInboxResponse(
        totalRows: rows.count,
        sections: rows.isEmpty ? [] : [
            SmartInboxSection(id: "today", title: "Today", rows: rows)
        ],
        relatedSuggestions: [],
        readyCount: rows.count,
        partialCount: 0,
        failedCount: 0,
        generatedAt: "2026-06-11T10:00:00+00:00",
        hotWindowDays: 30,
        hotWindowMessageCap: 0,
        hotWindowThreadCap: 0
    )
}

private func makeSmartInboxRow() -> SmartInboxRow {
    SmartInboxRow(
        id: "smart-row-apple-review",
        rowKey: "mail-object:apple-review",
        rowType: "verified_group",
        title: "Apple review needs one screenshot",
        summary: "Two Apple Developer messages belong to the same review thread.",
        primarySender: "Apple Developer <developer@apple.com>",
        latestMessageAt: "2026-05-16T12:46:00+05:30",
        latestMessageID: "apple-review-latest",
        readerThreadID: "demo-apple-today",
        sourceThreadIDs: ["demo-apple-today"],
        sourceMessageIDs: ["apple-review-1", "apple-review-latest"],
        confidenceTier: "exact",
        confidence: 1,
        groupingReason: ["source": .string("mail_object")],
        offlineStatus: "ready",
        readiness: "ready",
        actionType: "reply",
        priority: 90
    )
}

private func makeSmartWorkQueue(needsAction: [SmartWorkItem]) -> SmartWorkQueueResponse {
    SmartWorkQueueResponse(
        needsAction: needsAction,
        waiting: [],
        activeConversations: [],
        importantUpdates: [],
        manualReminders: [],
        totalOpen: needsAction.count,
        generatedAt: "2026-06-11T10:00:00+00:00"
    )
}

private func makeSmartWorkItem() -> SmartWorkItem {
    SmartWorkItem(
        id: "smart-work-apple-review",
        kind: "needs_action",
        title: "Reply to Apple Developer review",
        summary: "Reply to Apple with the missing screenshot.",
        status: "open",
        smartRowID: "smart-row-apple-review",
        sourceThreadIDs: ["demo-apple-today"],
        sourceMessageIDs: ["apple-review-latest"],
        dueAt: nil,
        priority: 90,
        confidence: 0.96,
        reason: ["action": .string("reply")],
        createdAt: "2026-06-11T10:00:00+00:00",
        updatedAt: "2026-06-11T10:00:00+00:00"
    )
}

private func makeSmartReadiness(readyRows: Int) -> SmartReadinessResponse {
    SmartReadinessResponse(
        stage: readyRows > 0 ? "offline_ready" : "empty",
        firstReadyComplete: readyRows > 0,
        hotWindowComplete: readyRows > 0,
        offlineReady: readyRows > 0,
        firstReadyTargetMessages: 300,
        hotWindowMessageCap: 0,
        hotWindowThreadCap: 0,
        processedMessages: readyRows,
        processedThreads: readyRows,
        readyRows: readyRows,
        partialRows: 0,
        failedRows: 0,
        offlineReadyRows: readyRows,
        offlinePartialRows: 0,
        offlineFailedRows: 0,
        lastError: nil
    )
}

private final class FailingAppClient: AppClient {
    var baseURL = AppConfiguration.defaultBackendURL
    var sessionToken: String?
    let mode: AppRunMode = .localBackend
    private let statusCode: Int

    init(statusCode: Int = 503) {
        self.statusCode = statusCode
    }

    func exchangeMobileSession(loginCode: String) async throws -> MobileSessionExchangeResponse { throw APIError.httpStatus(statusCode) }
    func appSession() async throws -> AppSessionResponse { throw APIError.httpStatus(statusCode) }
    func mailbox(label: MailboxLabel, limit: Int, cursor: String?) async throws -> MailboxResponse { throw APIError.httpStatus(statusCode) }
    func thread(threadID: String, limit: Int, offset: Int) async throws -> ThreadReaderResponse { throw APIError.httpStatus(statusCode) }
    func triggerMailboxSync() async throws -> MailboxSyncTriggerResponse { throw APIError.httpStatus(statusCode) }
    func syncMailboxNow() async throws -> MailboxSyncTriggerResponse { throw APIError.httpStatus(statusCode) }
    func archiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse { throw APIError.httpStatus(statusCode) }
    func unarchiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse { throw APIError.httpStatus(statusCode) }
    func markThreadRead(_ threadID: String) async throws -> GmailThreadMutationResponse { throw APIError.httpStatus(statusCode) }
    func enqueueThreadAction(_ request: QueuedThreadActionRequest) async throws -> QueuedThreadActionResponse { throw APIError.httpStatus(statusCode) }
    func createTask(_ request: TaskCreateRequest) async throws -> TaskResponse { throw APIError.httpStatus(statusCode) }
    func updateTask(_ taskID: String, request: TaskUpdateRequest) async throws -> TaskResponse { throw APIError.httpStatus(statusCode) }
    func completeEntity(_ entityID: String, request: EntityOutcomeRequest) async throws -> EntityOutcomeResponse { throw APIError.httpStatus(statusCode) }
}

private final class FixedMailboxAppClient: AppClient {
    var baseURL = AppConfiguration.defaultBackendURL
    var sessionToken: String?
    let mode: AppRunMode = .localBackend
    private let fixedMailbox: MailboxResponse

    init(mailbox: MailboxResponse) {
        self.fixedMailbox = mailbox
    }

    func exchangeMobileSession(loginCode: String) async throws -> MobileSessionExchangeResponse {
        try await DemoAppClient().exchangeMobileSession(loginCode: loginCode)
    }

    func appSession() async throws -> AppSessionResponse {
        let current = DemoAppFixtures.appSession
        return AppSessionResponse(
            user: current.user,
            readiness: current.readiness,
            dashboard: current.dashboard,
            mailbox: fixedMailbox,
            sync: current.sync
        )
    }

    func mailbox(label: MailboxLabel, limit: Int, cursor: String?) async throws -> MailboxResponse {
        fixedMailbox
    }

    func thread(threadID: String, limit: Int, offset: Int) async throws -> ThreadReaderResponse {
        DemoAppFixtures.threads[threadID] ?? DemoAppFixtures.threads["demo-google-today"]!
    }

    func triggerMailboxSync() async throws -> MailboxSyncTriggerResponse {
        try await DemoAppClient().triggerMailboxSync()
    }

    func syncMailboxNow() async throws -> MailboxSyncTriggerResponse {
        try await DemoAppClient().syncMailboxNow()
    }

    func archiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        GmailThreadMutationResponse(threadID: threadID, action: .archive)
    }

    func unarchiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        GmailThreadMutationResponse(threadID: threadID, action: .unarchive)
    }

    func markThreadRead(_ threadID: String) async throws -> GmailThreadMutationResponse {
        GmailThreadMutationResponse(threadID: threadID, action: .markRead)
    }

    func enqueueThreadAction(_ request: QueuedThreadActionRequest) async throws -> QueuedThreadActionResponse {
        try await DemoAppClient().enqueueThreadAction(request)
    }

    func createTask(_ request: TaskCreateRequest) async throws -> TaskResponse {
        try await DemoAppClient().createTask(request)
    }

    func updateTask(_ taskID: String, request: TaskUpdateRequest) async throws -> TaskResponse {
        try await DemoAppClient().updateTask(taskID, request: request)
    }

    func completeEntity(_ entityID: String, request: EntityOutcomeRequest) async throws -> EntityOutcomeResponse {
        try await DemoAppClient().completeEntity(entityID, request: request)
    }
}

private final class SmartSessionAppClient: AppClient {
    var baseURL = AppConfiguration.defaultBackendURL
    var sessionToken: String?
    let mode: AppRunMode = .localBackend
    private let fixedSession: AppSessionResponse
    private let fixedMailbox: MailboxResponse
    private let mailboxPages: [String: MailboxResponse]
    private let threads: [String: ThreadReaderResponse]
    private let summaryThreads: [String: ThreadReaderResponse]
    private(set) var mailboxCursors: [String?] = []
    private(set) var threadSummaryCallCount = 0

    init(
        session: AppSessionResponse,
        mailbox: MailboxResponse,
        mailboxPages: [String: MailboxResponse] = [:],
        threads: [String: ThreadReaderResponse] = [:],
        summaryThreads: [String: ThreadReaderResponse] = [:]
    ) {
        self.fixedSession = session
        self.fixedMailbox = mailbox
        self.mailboxPages = mailboxPages
        self.threads = threads
        self.summaryThreads = summaryThreads
    }

    func exchangeMobileSession(loginCode: String) async throws -> MobileSessionExchangeResponse {
        try await DemoAppClient().exchangeMobileSession(loginCode: loginCode)
    }

    func appSession() async throws -> AppSessionResponse {
        fixedSession
    }

    func mailbox(label: MailboxLabel, limit: Int, cursor: String?) async throws -> MailboxResponse {
        mailboxCursors.append(cursor)
        if let cursor, let page = mailboxPages[cursor] {
            return page
        }
        return fixedMailbox
    }

    func thread(threadID: String, limit: Int, offset: Int) async throws -> ThreadReaderResponse {
        threads[threadID] ?? DemoAppFixtures.threads[threadID] ?? DemoAppFixtures.threads["demo-google-today"]!
    }

    func threadSummary(threadID: String, limit: Int, offset: Int) async throws -> ThreadReaderResponse {
        threadSummaryCallCount += 1
        return summaryThreads[threadID] ?? threads[threadID] ?? DemoAppFixtures.threads[threadID] ?? DemoAppFixtures.threads["demo-google-today"]!
    }

    func triggerMailboxSync() async throws -> MailboxSyncTriggerResponse {
        try await DemoAppClient().triggerMailboxSync()
    }

    func syncMailboxNow() async throws -> MailboxSyncTriggerResponse {
        try await DemoAppClient().syncMailboxNow()
    }

    func archiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        GmailThreadMutationResponse(threadID: threadID, action: .archive)
    }

    func unarchiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        GmailThreadMutationResponse(threadID: threadID, action: .unarchive)
    }

    func markThreadRead(_ threadID: String) async throws -> GmailThreadMutationResponse {
        GmailThreadMutationResponse(threadID: threadID, action: .markRead)
    }

    func enqueueThreadAction(_ request: QueuedThreadActionRequest) async throws -> QueuedThreadActionResponse {
        try await DemoAppClient().enqueueThreadAction(request)
    }

    func createTask(_ request: TaskCreateRequest) async throws -> TaskResponse {
        try await DemoAppClient().createTask(request)
    }

    func updateTask(_ taskID: String, request: TaskUpdateRequest) async throws -> TaskResponse {
        try await DemoAppClient().updateTask(taskID, request: request)
    }

    func completeEntity(_ entityID: String, request: EntityOutcomeRequest) async throws -> EntityOutcomeResponse {
        try await DemoAppClient().completeEntity(entityID, request: request)
    }
}

private final class ActionMailboxAppClient: AppClient {
    var baseURL = AppConfiguration.defaultBackendURL
    var sessionToken: String?
    let mode: AppRunMode = .localBackend
    private var mailboxResponse: MailboxResponse
    private(set) var enqueuedActions: [QueuedThreadActionRequest] = []

    init(mailbox: MailboxResponse) {
        self.mailboxResponse = mailbox
    }

    func exchangeMobileSession(loginCode: String) async throws -> MobileSessionExchangeResponse {
        try await DemoAppClient().exchangeMobileSession(loginCode: loginCode)
    }

    func appSession() async throws -> AppSessionResponse {
        let current = DemoAppFixtures.appSession
        return AppSessionResponse(
            user: current.user,
            readiness: current.readiness,
            dashboard: current.dashboard,
            mailbox: mailboxResponse,
            sync: current.sync
        )
    }

    func mailbox(label: MailboxLabel, limit: Int, cursor: String?) async throws -> MailboxResponse {
        mailboxResponse
    }

    func thread(threadID: String, limit: Int, offset: Int) async throws -> ThreadReaderResponse {
        DemoAppFixtures.threads[threadID] ?? DemoAppFixtures.threads["demo-google-today"]!
    }

    func triggerMailboxSync() async throws -> MailboxSyncTriggerResponse {
        try await DemoAppClient().triggerMailboxSync()
    }

    func syncMailboxNow() async throws -> MailboxSyncTriggerResponse {
        try await DemoAppClient().syncMailboxNow()
    }

    func archiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        GmailThreadMutationResponse(threadID: threadID, action: .archive)
    }

    func unarchiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        GmailThreadMutationResponse(threadID: threadID, action: .unarchive)
    }

    func markThreadRead(_ threadID: String) async throws -> GmailThreadMutationResponse {
        GmailThreadMutationResponse(threadID: threadID, action: .markRead)
    }

    func enqueueThreadAction(_ request: QueuedThreadActionRequest) async throws -> QueuedThreadActionResponse {
        enqueuedActions.append(request)
        if request.action == .markRead {
            mailboxResponse = mailboxResponse.replacingRows { row in
                row.threadID == request.mailboxThreadID ? row.markedRead(targetMessageID: request.targetMessageID) : row
            }
        }
        return QueuedThreadActionResponse(
            clientActionID: request.clientActionID,
            serverActionID: "server-\(request.clientActionID)",
            mailboxThreadID: request.mailboxThreadID,
            targetMessageID: request.targetMessageID,
            action: request.action,
            state: .queued,
            queuedAt: request.createdAt,
            appliedAt: nil,
            error: nil
        )
    }

    func createTask(_ request: TaskCreateRequest) async throws -> TaskResponse {
        try await DemoAppClient().createTask(request)
    }

    func updateTask(_ taskID: String, request: TaskUpdateRequest) async throws -> TaskResponse {
        try await DemoAppClient().updateTask(taskID, request: request)
    }

    func completeEntity(_ entityID: String, request: EntityOutcomeRequest) async throws -> EntityOutcomeResponse {
        try await DemoAppClient().completeEntity(entityID, request: request)
    }
}

private final class RealtimeEventAppClient: AppClient {
    var baseURL = AppConfiguration.defaultBackendURL
    var sessionToken: String?
    let mode: AppRunMode = .demo
    private let sessionMailbox: MailboxResponse
    private var mailboxResponses: [MailboxResponse]
    private(set) var appSessionCallCount = 0
    private(set) var mailboxCallCount = 0

    init(sessionMailbox: MailboxResponse, mailboxResponses: [MailboxResponse]) {
        self.sessionMailbox = sessionMailbox
        self.mailboxResponses = mailboxResponses
    }

    func exchangeMobileSession(loginCode: String) async throws -> MobileSessionExchangeResponse {
        try await DemoAppClient().exchangeMobileSession(loginCode: loginCode)
    }

    func appSession() async throws -> AppSessionResponse {
        appSessionCallCount += 1
        let current = DemoAppFixtures.appSession
        return AppSessionResponse(
            user: current.user,
            readiness: current.readiness,
            dashboard: current.dashboard,
            mailbox: sessionMailbox,
            sync: current.sync
        )
    }

    func mailbox(label: MailboxLabel, limit: Int, cursor: String?) async throws -> MailboxResponse {
        mailboxCallCount += 1
        if mailboxResponses.isEmpty {
            return sessionMailbox
        }
        return mailboxResponses.removeFirst()
    }

    func thread(threadID: String, limit: Int, offset: Int) async throws -> ThreadReaderResponse {
        DemoAppFixtures.threads[threadID] ?? DemoAppFixtures.threads["demo-google-today"]!
    }

    func triggerMailboxSync() async throws -> MailboxSyncTriggerResponse {
        try await DemoAppClient().triggerMailboxSync()
    }

    func syncMailboxNow() async throws -> MailboxSyncTriggerResponse {
        try await DemoAppClient().syncMailboxNow()
    }

    func archiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        GmailThreadMutationResponse(threadID: threadID, action: .archive)
    }

    func unarchiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        GmailThreadMutationResponse(threadID: threadID, action: .unarchive)
    }

    func markThreadRead(_ threadID: String) async throws -> GmailThreadMutationResponse {
        GmailThreadMutationResponse(threadID: threadID, action: .markRead)
    }

    func enqueueThreadAction(_ request: QueuedThreadActionRequest) async throws -> QueuedThreadActionResponse {
        try await DemoAppClient().enqueueThreadAction(request)
    }

    func createTask(_ request: TaskCreateRequest) async throws -> TaskResponse {
        try await DemoAppClient().createTask(request)
    }

    func updateTask(_ taskID: String, request: TaskUpdateRequest) async throws -> TaskResponse {
        try await DemoAppClient().updateTask(taskID, request: request)
    }

    func completeEntity(_ entityID: String, request: EntityOutcomeRequest) async throws -> EntityOutcomeResponse {
        try await DemoAppClient().completeEntity(entityID, request: request)
    }
}

private final class ManualSyncAppClient: AppClient {
    var baseURL = AppConfiguration.defaultBackendURL
    var sessionToken: String?
    let mode: AppRunMode = .localBackend
    private(set) var syncNowCallCount = 0
    private(set) var mailboxLabels: [MailboxLabel] = []

    func exchangeMobileSession(loginCode: String) async throws -> MobileSessionExchangeResponse {
        try await DemoAppClient().exchangeMobileSession(loginCode: loginCode)
    }

    func appSession() async throws -> AppSessionResponse {
        DemoAppFixtures.appSession
    }

    func mailbox(label: MailboxLabel, limit: Int, cursor: String?) async throws -> MailboxResponse {
        mailboxLabels.append(label)
        let mailbox = DemoAppFixtures.mailbox
        return MailboxResponse(
            label: label,
            totalThreads: mailbox.totalThreads,
            nextCursor: mailbox.nextCursor,
            sections: mailbox.sections,
            readyCount: mailbox.readyCount,
            pendingCount: mailbox.pendingCount,
            oldestImportedAt: mailbox.oldestImportedAt,
            fullImportRunning: mailbox.fullImportRunning,
            fullImportCompleted: mailbox.fullImportCompleted
        )
    }

    func thread(threadID: String, limit: Int, offset: Int) async throws -> ThreadReaderResponse {
        DemoAppFixtures.threads[threadID] ?? DemoAppFixtures.threads["demo-google-today"]!
    }

    func triggerMailboxSync() async throws -> MailboxSyncTriggerResponse {
        try await DemoAppClient().triggerMailboxSync()
    }

    func syncMailboxNow() async throws -> MailboxSyncTriggerResponse {
        syncNowCallCount += 1
        return try await DemoAppClient().syncMailboxNow()
    }

    func archiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        GmailThreadMutationResponse(threadID: threadID, action: .archive)
    }

    func unarchiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        GmailThreadMutationResponse(threadID: threadID, action: .unarchive)
    }

    func markThreadRead(_ threadID: String) async throws -> GmailThreadMutationResponse {
        GmailThreadMutationResponse(threadID: threadID, action: .markRead)
    }

    func enqueueThreadAction(_ request: QueuedThreadActionRequest) async throws -> QueuedThreadActionResponse {
        try await DemoAppClient().enqueueThreadAction(request)
    }

    func createTask(_ request: TaskCreateRequest) async throws -> TaskResponse {
        try await DemoAppClient().createTask(request)
    }

    func updateTask(_ taskID: String, request: TaskUpdateRequest) async throws -> TaskResponse {
        try await DemoAppClient().updateTask(taskID, request: request)
    }

    func completeEntity(_ entityID: String, request: EntityOutcomeRequest) async throws -> EntityOutcomeResponse {
        try await DemoAppClient().completeEntity(entityID, request: request)
    }
}

private final class MailboxFailingAfterSessionAppClient: AppClient {
    var baseURL = AppConfiguration.defaultBackendURL
    var sessionToken: String?
    let mode: AppRunMode = .localBackend
    private(set) var mailboxLabels: [MailboxLabel] = []

    func exchangeMobileSession(loginCode: String) async throws -> MobileSessionExchangeResponse {
        try await DemoAppClient().exchangeMobileSession(loginCode: loginCode)
    }

    func appSession() async throws -> AppSessionResponse {
        DemoAppFixtures.appSession
    }

    func mailbox(label: MailboxLabel, limit: Int, cursor: String?) async throws -> MailboxResponse {
        mailboxLabels.append(label)
        throw APIError.httpStatus(503)
    }

    func thread(threadID: String, limit: Int, offset: Int) async throws -> ThreadReaderResponse {
        DemoAppFixtures.threads[threadID] ?? DemoAppFixtures.threads["demo-google-today"]!
    }

    func triggerMailboxSync() async throws -> MailboxSyncTriggerResponse {
        try await DemoAppClient().triggerMailboxSync()
    }

    func syncMailboxNow() async throws -> MailboxSyncTriggerResponse {
        try await DemoAppClient().syncMailboxNow()
    }

    func archiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        GmailThreadMutationResponse(threadID: threadID, action: .archive)
    }

    func unarchiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        GmailThreadMutationResponse(threadID: threadID, action: .unarchive)
    }

    func markThreadRead(_ threadID: String) async throws -> GmailThreadMutationResponse {
        GmailThreadMutationResponse(threadID: threadID, action: .markRead)
    }

    func enqueueThreadAction(_ request: QueuedThreadActionRequest) async throws -> QueuedThreadActionResponse {
        try await DemoAppClient().enqueueThreadAction(request)
    }

    func createTask(_ request: TaskCreateRequest) async throws -> TaskResponse {
        try await DemoAppClient().createTask(request)
    }

    func updateTask(_ taskID: String, request: TaskUpdateRequest) async throws -> TaskResponse {
        try await DemoAppClient().updateTask(taskID, request: request)
    }

    func completeEntity(_ entityID: String, request: EntityOutcomeRequest) async throws -> EntityOutcomeResponse {
        try await DemoAppClient().completeEntity(entityID, request: request)
    }
}

private final class MismatchedMailboxRowsAppClient: AppClient {
    var baseURL = AppConfiguration.defaultBackendURL
    var sessionToken: String?
    let mode: AppRunMode = .localBackend
    private let row: GmailThreadRow
    private(set) var mailboxLabels: [MailboxLabel] = []

    init(row: GmailThreadRow) {
        self.row = row
    }

    func exchangeMobileSession(loginCode: String) async throws -> MobileSessionExchangeResponse {
        try await DemoAppClient().exchangeMobileSession(loginCode: loginCode)
    }

    func appSession() async throws -> AppSessionResponse {
        DemoAppFixtures.appSession
    }

    func mailbox(label: MailboxLabel, limit: Int, cursor: String?) async throws -> MailboxResponse {
        mailboxLabels.append(label)
        return MailboxResponse(
            label: label,
            totalThreads: 1,
            nextCursor: nil,
            loadedThreads: 1,
            windowDays: 90,
            sections: [GmailThreadSection(id: "today", title: "Today", rows: [row])],
            fullImportRunning: false,
            fullImportCompleted: true
        )
    }

    func thread(threadID: String, limit: Int, offset: Int) async throws -> ThreadReaderResponse {
        DemoAppFixtures.threads[threadID] ?? DemoAppFixtures.threads["demo-google-today"]!
    }

    func triggerMailboxSync() async throws -> MailboxSyncTriggerResponse {
        try await DemoAppClient().triggerMailboxSync()
    }

    func syncMailboxNow() async throws -> MailboxSyncTriggerResponse {
        try await DemoAppClient().syncMailboxNow()
    }

    func archiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        GmailThreadMutationResponse(threadID: threadID, action: .archive)
    }

    func unarchiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        GmailThreadMutationResponse(threadID: threadID, action: .unarchive)
    }

    func markThreadRead(_ threadID: String) async throws -> GmailThreadMutationResponse {
        GmailThreadMutationResponse(threadID: threadID, action: .markRead)
    }

    func enqueueThreadAction(_ request: QueuedThreadActionRequest) async throws -> QueuedThreadActionResponse {
        try await DemoAppClient().enqueueThreadAction(request)
    }

    func createTask(_ request: TaskCreateRequest) async throws -> TaskResponse {
        try await DemoAppClient().createTask(request)
    }

    func updateTask(_ taskID: String, request: TaskUpdateRequest) async throws -> TaskResponse {
        try await DemoAppClient().updateTask(taskID, request: request)
    }

    func completeEntity(_ entityID: String, request: EntityOutcomeRequest) async throws -> EntityOutcomeResponse {
        try await DemoAppClient().completeEntity(entityID, request: request)
    }
}

private final class PaginatedMailboxAppClient: AppClient {
    var baseURL = AppConfiguration.defaultBackendURL
    var sessionToken: String?
    let mode: AppRunMode = .localBackend
    private(set) var mailboxCursors: [String?] = []

    private var firstPage: MailboxResponse {
        MailboxResponse(
            label: .inbox,
            totalThreads: 2,
            nextCursor: "cursor-2",
            loadedThreads: 1,
            windowDays: 90,
            sections: [
                GmailThreadSection(id: "today", title: "Today", rows: [DemoAppFixtures.sections[0].rows[0]])
            ],
            readyCount: 2,
            pendingCount: 0,
            oldestImportedAt: nil,
            fullImportRunning: false,
            fullImportCompleted: false
        )
    }

    private var secondPage: MailboxResponse {
        MailboxResponse(
            label: .inbox,
            totalThreads: 2,
            nextCursor: nil,
            loadedThreads: 1,
            windowDays: 90,
            sections: [
                GmailThreadSection(id: "today", title: "Today", rows: [DemoAppFixtures.sections[0].rows[1]])
            ],
            readyCount: 2,
            pendingCount: 0,
            oldestImportedAt: nil,
            fullImportRunning: false,
            fullImportCompleted: true
        )
    }

    func exchangeMobileSession(loginCode: String) async throws -> MobileSessionExchangeResponse {
        try await DemoAppClient().exchangeMobileSession(loginCode: loginCode)
    }

    func appSession() async throws -> AppSessionResponse {
        let current = DemoAppFixtures.appSession
        return AppSessionResponse(user: current.user, readiness: current.readiness, dashboard: current.dashboard, mailbox: firstPage, sync: current.sync)
    }

    func mailbox(label: MailboxLabel, limit: Int, cursor: String?) async throws -> MailboxResponse {
        mailboxCursors.append(cursor)
        return cursor == nil ? firstPage : secondPage
    }

    func thread(threadID: String, limit: Int, offset: Int) async throws -> ThreadReaderResponse {
        DemoAppFixtures.threads[threadID] ?? DemoAppFixtures.threads["demo-google-today"]!
    }

    func triggerMailboxSync() async throws -> MailboxSyncTriggerResponse {
        try await DemoAppClient().triggerMailboxSync()
    }

    func syncMailboxNow() async throws -> MailboxSyncTriggerResponse {
        try await DemoAppClient().syncMailboxNow()
    }

    func archiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        GmailThreadMutationResponse(threadID: threadID, action: .archive)
    }

    func unarchiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        GmailThreadMutationResponse(threadID: threadID, action: .unarchive)
    }

    func markThreadRead(_ threadID: String) async throws -> GmailThreadMutationResponse {
        GmailThreadMutationResponse(threadID: threadID, action: .markRead)
    }

    func enqueueThreadAction(_ request: QueuedThreadActionRequest) async throws -> QueuedThreadActionResponse {
        try await DemoAppClient().enqueueThreadAction(request)
    }

    func createTask(_ request: TaskCreateRequest) async throws -> TaskResponse {
        try await DemoAppClient().createTask(request)
    }

    func updateTask(_ taskID: String, request: TaskUpdateRequest) async throws -> TaskResponse {
        try await DemoAppClient().updateTask(taskID, request: request)
    }

    func completeEntity(_ entityID: String, request: EntityOutcomeRequest) async throws -> EntityOutcomeResponse {
        try await DemoAppClient().completeEntity(entityID, request: request)
    }
}

private final class SendTrackingAppClient: AppClient {
    var baseURL = AppConfiguration.defaultBackendURL
    var sessionToken: String?
    let mode: AppRunMode = .demo
    private let demo = DemoAppClient()
    private(set) var composeCallCount = 0
    private(set) var replyCallCount = 0

    func exchangeMobileSession(loginCode: String) async throws -> MobileSessionExchangeResponse {
        try await demo.exchangeMobileSession(loginCode: loginCode)
    }

    func appSession() async throws -> AppSessionResponse {
        DemoAppFixtures.appSession
    }

    func mailbox(label: MailboxLabel, limit: Int, cursor: String?) async throws -> MailboxResponse {
        DemoAppFixtures.mailbox
    }

    func thread(threadID: String, limit: Int, offset: Int) async throws -> ThreadReaderResponse {
        DemoAppFixtures.threads[threadID] ?? DemoAppFixtures.threads["demo-google-today"]!
    }

    func triggerMailboxSync() async throws -> MailboxSyncTriggerResponse {
        try await demo.triggerMailboxSync()
    }

    func syncMailboxNow() async throws -> MailboxSyncTriggerResponse {
        try await demo.syncMailboxNow()
    }

    func archiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        GmailThreadMutationResponse(threadID: threadID, action: .archive)
    }

    func unarchiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        GmailThreadMutationResponse(threadID: threadID, action: .unarchive)
    }

    func markThreadRead(_ threadID: String) async throws -> GmailThreadMutationResponse {
        GmailThreadMutationResponse(threadID: threadID, action: .markRead)
    }

    func enqueueThreadAction(_ request: QueuedThreadActionRequest) async throws -> QueuedThreadActionResponse {
        try await demo.enqueueThreadAction(request)
    }

    func sendCompose(_ request: MailComposeRequest) async throws -> MailSendResponse {
        composeCallCount += 1
        return try await demo.sendCompose(request)
    }

    func sendReply(threadID: String, request: MailReplyRequest) async throws -> MailSendResponse {
        replyCallCount += 1
        return try await demo.sendReply(threadID: threadID, request: request)
    }

    func createTask(_ request: TaskCreateRequest) async throws -> TaskResponse {
        try await demo.createTask(request)
    }

    func updateTask(_ taskID: String, request: TaskUpdateRequest) async throws -> TaskResponse {
        try await demo.updateTask(taskID, request: request)
    }

    func completeEntity(_ entityID: String, request: EntityOutcomeRequest) async throws -> EntityOutcomeResponse {
        try await demo.completeEntity(entityID, request: request)
    }
}

private final class FailingThreadAppClient: AppClient {
    var baseURL = AppConfiguration.defaultBackendURL
    var sessionToken: String?
    let mode: AppRunMode = .demo

    func exchangeMobileSession(loginCode: String) async throws -> MobileSessionExchangeResponse {
        try await DemoAppClient().exchangeMobileSession(loginCode: loginCode)
    }

    func appSession() async throws -> AppSessionResponse {
        DemoAppFixtures.appSession
    }

    func mailbox(label: MailboxLabel, limit: Int, cursor: String?) async throws -> MailboxResponse {
        DemoAppFixtures.mailbox
    }

    func thread(threadID: String, limit: Int, offset: Int) async throws -> ThreadReaderResponse {
        throw APIError.httpStatus(503)
    }

    func triggerMailboxSync() async throws -> MailboxSyncTriggerResponse {
        try await DemoAppClient().triggerMailboxSync()
    }

    func syncMailboxNow() async throws -> MailboxSyncTriggerResponse {
        try await DemoAppClient().syncMailboxNow()
    }

    func archiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        GmailThreadMutationResponse(threadID: threadID, action: .archive)
    }

    func unarchiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        GmailThreadMutationResponse(threadID: threadID, action: .unarchive)
    }

    func markThreadRead(_ threadID: String) async throws -> GmailThreadMutationResponse {
        GmailThreadMutationResponse(threadID: threadID, action: .markRead)
    }

    func enqueueThreadAction(_ request: QueuedThreadActionRequest) async throws -> QueuedThreadActionResponse {
        try await DemoAppClient().enqueueThreadAction(request)
    }

    func createTask(_ request: TaskCreateRequest) async throws -> TaskResponse {
        try await DemoAppClient().createTask(request)
    }

    func updateTask(_ taskID: String, request: TaskUpdateRequest) async throws -> TaskResponse {
        try await DemoAppClient().updateTask(taskID, request: request)
    }

    func completeEntity(_ entityID: String, request: EntityOutcomeRequest) async throws -> EntityOutcomeResponse {
        try await DemoAppClient().completeEntity(entityID, request: request)
    }
}

private final class SlowThreadAppClient: AppClient {
    var baseURL = AppConfiguration.defaultBackendURL
    var sessionToken: String?
    let mode: AppRunMode = .demo
    private let lock = NSLock()
    private var lockedThreadCallCounts: [String: Int] = [:]

    var threadCallCounts: [String: Int] {
        lock.lock()
        defer { lock.unlock() }
        return lockedThreadCallCounts
    }

    func appSession() async throws -> AppSessionResponse {
        DemoAppFixtures.appSession
    }

    func exchangeMobileSession(loginCode: String) async throws -> MobileSessionExchangeResponse {
        try await DemoAppClient().exchangeMobileSession(loginCode: loginCode)
    }

    func mailbox(label: MailboxLabel, limit: Int, cursor: String?) async throws -> MailboxResponse {
        DemoAppFixtures.mailbox
    }

    func thread(threadID: String, limit: Int, offset: Int) async throws -> ThreadReaderResponse {
        lock.lock()
        lockedThreadCallCounts[threadID, default: 0] += 1
        lock.unlock()
        try await Task.sleep(nanoseconds: 50_000_000)
        return DemoAppFixtures.threads[threadID] ?? DemoAppFixtures.threads["demo-google-today"]!
    }

    func triggerMailboxSync() async throws -> MailboxSyncTriggerResponse {
        try await DemoAppClient().triggerMailboxSync()
    }

    func syncMailboxNow() async throws -> MailboxSyncTriggerResponse {
        try await DemoAppClient().syncMailboxNow()
    }

    func archiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        GmailThreadMutationResponse(threadID: threadID, action: .archive)
    }

    func unarchiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        GmailThreadMutationResponse(threadID: threadID, action: .unarchive)
    }

    func markThreadRead(_ threadID: String) async throws -> GmailThreadMutationResponse {
        GmailThreadMutationResponse(threadID: threadID, action: .markRead)
    }

    func enqueueThreadAction(_ request: QueuedThreadActionRequest) async throws -> QueuedThreadActionResponse {
        try await DemoAppClient().enqueueThreadAction(request)
    }

    func createTask(_ request: TaskCreateRequest) async throws -> TaskResponse {
        try await DemoAppClient().createTask(request)
    }

    func updateTask(_ taskID: String, request: TaskUpdateRequest) async throws -> TaskResponse {
        try await DemoAppClient().updateTask(taskID, request: request)
    }

    func completeEntity(_ entityID: String, request: EntityOutcomeRequest) async throws -> EntityOutcomeResponse {
        try await DemoAppClient().completeEntity(entityID, request: request)
    }
}

private extension UserDefaults {
    static func ephemeral(file: StaticString = #file, line: UInt = #line) -> UserDefaults {
        let suiteName = "ElectronicMailTests.\(UUID().uuidString).\(file).\(line)"
        let defaults = UserDefaults(suiteName: suiteName)!
        defaults.removePersistentDomain(forName: suiteName)
        return defaults
    }
}
