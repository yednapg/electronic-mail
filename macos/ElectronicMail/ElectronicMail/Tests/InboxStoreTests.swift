import Combine
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

    func testLabelOnlyMessageCopyPreservesRenderRevision() {
        let message = ThreadMessage(
            id: "message-1",
            source: .gmail,
            threadID: "thread-1",
            fromAddress: "sender@example.com",
            to: "reader@example.com",
            cc: nil,
            bcc: nil,
            subject: "Large message",
            body: String(repeating: "body", count: 25_000),
            htmlBody: "<p>Body</p>",
            htmlRenderDocument: "<html><body><p>Body</p></body></html>",
            snippet: "Body",
            labelIDs: ["INBOX"],
            receivedAt: "2026-07-23T12:00:00Z"
        )

        let copy = ThreadMessage(copying: message, labelIDs: ["INBOX", "STARRED"])

        XCTAssertEqual(copy.renderRevision, message.renderRevision)
        XCTAssertEqual(copy.labelIDs, ["INBOX", "STARRED"])
        XCTAssertEqual(copy.body, message.body)
        XCTAssertEqual(copy.htmlRenderDocument, message.htmlRenderDocument)
    }

    func testSectionOrderMatchesInboxBuckets() async {
        let store = InboxStore(client: DemoAppClient(), sessionCache: AppSessionCache(defaults: .ephemeral()), threadCache: ThreadCache(defaults: .ephemeral()))

        await store.load()

        XCTAssertEqual(store.sections.map(\.title), ["Today", "Past 7 days", "Earlier this month"])
    }

    func testInboxChronologyIncludesExactSeventhCalendarDayAndPreservesParentChildOrder() {
        InboxChronologyPresenter.resetCacheForTesting()
        let calendar = chronologyCalendar(timeZoneIdentifier: "Asia/Kolkata")
        let source = InboxSectionViewModel(
            id: "source-a",
            title: "Legacy source",
            rows: [
                makeChronologyTestRow(
                    id: "today-parent",
                    receivedAt: "2026-07-24T09:10:11.123+05:30",
                    isExpandable: true,
                    isExpanded: true
                ),
                makeChronologyTestRow(
                    id: "today-parent::message::child",
                    receivedAt: "2025-01-01T00:00:00Z",
                    threadID: "today-parent",
                    isChild: true
                ),
                makeChronologyTestRow(
                    id: "recent",
                    receivedAt: "2026-07-23T08:00:00+05:30"
                ),
                makeChronologyTestRow(
                    id: "exactly-seven-days-old",
                    receivedAt: "2026-07-17T00:00:00+05:30"
                ),
                makeChronologyTestRow(
                    id: "one-second-too-old",
                    receivedAt: "2026-07-16T23:59:59+05:30"
                ),
                makeChronologyTestRow(
                    id: "prior-month",
                    receivedAt: "2026-06-30T23:59:59+05:30"
                ),
                makeChronologyTestRow(
                    id: "malformed",
                    receivedAt: "not-an-iso-date"
                )
            ]
        )

        let sections = InboxChronologyPresenter.sections(
            from: [source],
            cacheOwner: self,
            revision: 1,
            now: chronologyDate("2026-07-24T14:00:00+05:30"),
            calendar: calendar,
            locale: Locale(identifier: "en_US_POSIX")
        )

        XCTAssertEqual(
            sections.map(\.id),
            [
                "chronology::today",
                "chronology::past-seven-days",
                "chronology::earlier-this-month",
                "chronology::month::gregorian::1-2026-6",
                "chronology::fallback::source-a"
            ]
        )
        XCTAssertEqual(
            sections.map(\.title),
            ["Today", "Past 7 days", "Earlier this month", "June 2026", "Legacy source"]
        )
        XCTAssertEqual(sections[0].rows.map(\.id), ["today-parent", "today-parent::message::child"])
        XCTAssertEqual(
            sections.flatMap(\.rows).map(\.id),
            [
                "today-parent",
                "today-parent::message::child",
                "recent",
                "exactly-seven-days-old",
                "one-second-too-old",
                "prior-month",
                "malformed"
            ]
        )
    }

    func testInboxChronologyHandlesSevenDayBoundaryAcrossMonthAndYear() {
        InboxChronologyPresenter.resetCacheForTesting()
        let calendar = chronologyCalendar(timeZoneIdentifier: "UTC")
        let source = InboxSectionViewModel(
            id: "new-year",
            title: "Server bucket",
            rows: [
                makeChronologyTestRow(id: "today", receivedAt: "2027-01-03T10:00:00Z"),
                makeChronologyTestRow(id: "seven-days", receivedAt: "2026-12-27T00:00:00Z"),
                makeChronologyTestRow(id: "older", receivedAt: "2026-12-26T23:59:59Z")
            ]
        )

        let sections = InboxChronologyPresenter.sections(
            from: [source],
            cacheOwner: self,
            revision: 1,
            now: chronologyDate("2027-01-03T12:00:00Z"),
            calendar: calendar,
            locale: Locale(identifier: "en_US_POSIX")
        )

        XCTAssertEqual(sections.map(\.title), ["Today", "Past 7 days", "December 2026"])
        XCTAssertEqual(
            sections.map(\.id),
            [
                "chronology::today",
                "chronology::past-seven-days",
                "chronology::month::gregorian::1-2026-12"
            ]
        )
        XCTAssertEqual(sections.flatMap(\.rows).map(\.id), ["today", "seven-days", "older"])
    }

    func testInboxChronologyUsesClientTimeZoneForBucketAndRowLabel() {
        InboxChronologyPresenter.resetCacheForTesting()
        let calendar = chronologyCalendar(timeZoneIdentifier: "Asia/Kolkata")
        let source = InboxSectionViewModel(
            id: "server-yesterday",
            title: "Yesterday",
            rows: [
                makeChronologyTestRow(
                    id: "local-today",
                    receivedAt: "2026-07-23T19:00:00Z",
                    timeLabel: "Jul 23"
                ),
                makeChronologyTestRow(
                    id: "local-yesterday",
                    receivedAt: "2026-07-23T17:30:00Z",
                    timeLabel: "11:00 PM"
                ),
                makeChronologyTestRow(
                    id: "malformed",
                    receivedAt: "not-an-iso-date",
                    timeLabel: "server fallback"
                )
            ]
        )

        let sections = InboxChronologyPresenter.sections(
            from: [source],
            cacheOwner: self,
            revision: 1,
            now: chronologyDate("2026-07-23T19:30:00Z"),
            calendar: calendar,
            locale: Locale(identifier: "en_US_POSIX")
        )

        XCTAssertEqual(sections.map(\.title), ["Today", "Past 7 days", "Yesterday"])
        let localTodayLabel = sections[0].rows[0].timeLabel
        XCTAssertTrue(localTodayLabel.contains(":"))
        XCTAssertFalse(localTodayLabel.contains("Jul"))
        let localYesterdayLabel = sections[1].rows[0].timeLabel
        XCTAssertFalse(localYesterdayLabel.contains(":"))
        XCTAssertTrue(localYesterdayLabel.contains("23"))
        XCTAssertEqual(sections[2].rows[0].timeLabel, "server fallback")
    }

    func testInboxChronologyConsolidatesNoncontiguousBucketsAndSortsThemGlobally() {
        InboxChronologyPresenter.resetCacheForTesting()
        let calendar = chronologyCalendar(timeZoneIdentifier: "UTC")
        let original = InboxSectionViewModel(
            id: "ranked",
            title: "Server bucket",
            rows: [
                makeChronologyTestRow(id: "original-today", receivedAt: "2026-07-24T08:00:00Z"),
                makeChronologyTestRow(id: "june", receivedAt: "2026-06-15T08:00:00Z"),
                makeChronologyTestRow(id: "second-today-run", receivedAt: "2026-07-24T07:00:00Z")
            ]
        )
        let changed = InboxSectionViewModel(
            id: "ranked",
            title: "Server bucket",
            rows: [
                makeChronologyTestRow(id: "new-leading-today", receivedAt: "2026-07-24T09:00:00Z"),
                makeChronologyTestRow(id: "original-today", receivedAt: "2026-07-24T08:00:00Z"),
                makeChronologyTestRow(id: "june", receivedAt: "2026-06-15T08:00:00Z"),
                makeChronologyTestRow(id: "second-today-run", receivedAt: "2026-07-24T07:00:00Z")
            ]
        )
        let now = chronologyDate("2026-07-24T12:00:00Z")
        let first = InboxChronologyPresenter.sections(
            from: [original],
            cacheOwner: self,
            revision: 10,
            now: now,
            calendar: calendar,
            locale: Locale(identifier: "en_US_POSIX")
        )
        let second = InboxChronologyPresenter.sections(
            from: [changed],
            cacheOwner: self,
            revision: 11,
            now: now,
            calendar: calendar,
            locale: Locale(identifier: "en_US_POSIX")
        )

        XCTAssertEqual(
            first.map(\.id),
            [
                "chronology::today",
                "chronology::month::gregorian::1-2026-6"
            ]
        )
        XCTAssertEqual(second.map(\.id), first.map(\.id))
        XCTAssertEqual(
            second.flatMap(\.rows).map(\.id),
            ["new-leading-today", "original-today", "second-today-run", "june"]
        )
    }

    func testInboxChronologyCombinesRepeatedServerBatchesIntoOneDateSection() {
        InboxChronologyPresenter.resetCacheForTesting()
        let calendar = chronologyCalendar(timeZoneIdentifier: "Asia/Kolkata")
        let source = [
            InboxSectionViewModel(
                id: "initial-batch",
                title: "Today",
                rows: [
                    makeChronologyTestRow(id: "today-0807", receivedAt: "2026-08-28T08:07:00+05:30"),
                    makeChronologyTestRow(id: "today-1309", receivedAt: "2026-08-28T13:09:00+05:30"),
                    makeChronologyTestRow(id: "aug-25", receivedAt: "2026-08-25T10:00:00+05:30")
                ]
            ),
            InboxSectionViewModel(
                id: "next-batch",
                title: "Today",
                rows: [
                    makeChronologyTestRow(id: "today-0457", receivedAt: "2026-08-28T04:57:00+05:30"),
                    makeChronologyTestRow(id: "aug-27", receivedAt: "2026-08-27T10:00:00+05:30")
                ]
            )
        ]

        let sections = InboxChronologyPresenter.sections(
            from: source,
            cacheOwner: self,
            revision: 1,
            now: chronologyDate("2026-08-28T19:20:00+05:30"),
            calendar: calendar,
            locale: Locale(identifier: "en_US_POSIX")
        )

        XCTAssertEqual(sections.map(\.title), ["Today", "Past 7 days"])
        XCTAssertEqual(
            sections[0].rows.map(\.id),
            ["today-1309", "today-0807", "today-0457"]
        )
        XCTAssertEqual(sections[1].rows.map(\.id), ["aug-27", "aug-25"])
    }

    func testInboxChronologyCacheInvalidatesOnlyForPresentationContextChanges() {
        InboxChronologyPresenter.resetCacheForTesting()
        let source = InboxSectionViewModel(
            id: "cached",
            title: "Server bucket",
            rows: [makeChronologyTestRow(id: "today", receivedAt: "2026-07-24T08:00:00Z")]
        )
        let now = chronologyDate("2026-07-24T12:00:00Z")
        let utcCalendar = chronologyCalendar(timeZoneIdentifier: "UTC")
        let locale = Locale(identifier: "en_US_POSIX")

        let first = InboxChronologyPresenter.sections(
            from: [source],
            cacheOwner: self,
            revision: 20,
            now: now,
            calendar: utcCalendar,
            locale: locale
        )
        let broadInvalidation = InboxChronologyPresenter.sections(
            from: [source],
            cacheOwner: self,
            revision: 20,
            now: now,
            calendar: utcCalendar,
            locale: locale
        )

        XCTAssertEqual(first, broadInvalidation)
        XCTAssertEqual(InboxChronologyPresenter.cacheMissCount, 1)
        XCTAssertEqual(InboxChronologyPresenter.cacheEntryCount, 1)

        _ = InboxChronologyPresenter.sections(
            from: [source],
            cacheOwner: self,
            revision: 21,
            now: now,
            calendar: utcCalendar,
            locale: locale
        )
        XCTAssertEqual(InboxChronologyPresenter.cacheMissCount, 2)
        XCTAssertEqual(InboxChronologyPresenter.cacheEntryCount, 1)

        _ = InboxChronologyPresenter.sections(
            from: [source],
            cacheOwner: self,
            revision: 21,
            now: chronologyDate("2026-07-25T12:00:00Z"),
            calendar: utcCalendar,
            locale: locale
        )
        XCTAssertEqual(InboxChronologyPresenter.cacheMissCount, 3)
        XCTAssertEqual(InboxChronologyPresenter.cacheEntryCount, 1)

        _ = InboxChronologyPresenter.sections(
            from: [source],
            cacheOwner: self,
            revision: 21,
            now: now,
            calendar: chronologyCalendar(timeZoneIdentifier: "America/Los_Angeles"),
            locale: locale
        )
        XCTAssertEqual(InboxChronologyPresenter.cacheMissCount, 4)
        XCTAssertEqual(InboxChronologyPresenter.cacheEntryCount, 1)

        _ = InboxChronologyPresenter.sections(
            from: [source],
            cacheOwner: self,
            revision: 21,
            now: now,
            calendar: utcCalendar,
            locale: Locale(identifier: "fr_FR")
        )
        XCTAssertEqual(InboxChronologyPresenter.cacheMissCount, 5)
        XCTAssertEqual(InboxChronologyPresenter.cacheEntryCount, 1)

        let otherStoreIdentity = NSObject()
        _ = InboxChronologyPresenter.sections(
            from: [source],
            cacheOwner: otherStoreIdentity,
            revision: 21,
            now: now,
            calendar: utcCalendar,
            locale: locale
        )
        XCTAssertEqual(InboxChronologyPresenter.cacheMissCount, 6)
        XCTAssertEqual(InboxChronologyPresenter.cacheEntryCount, 2)
    }

    private func chronologyCalendar(timeZoneIdentifier: String) -> Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.locale = Locale(identifier: "en_US_POSIX")
        calendar.timeZone = TimeZone(identifier: timeZoneIdentifier)!
        return calendar
    }

    private func chronologyDate(_ value: String) -> Date {
        let formatter = ISO8601DateFormatter()
        guard let date = formatter.date(from: value) else {
            XCTFail("Invalid test date: \(value)")
            return Date(timeIntervalSince1970: 0)
        }
        return date
    }

    private func makeChronologyTestRow(
        id: String,
        receivedAt: String,
        threadID: String? = nil,
        isChild: Bool = false,
        isExpandable: Bool = false,
        isExpanded: Bool = false,
        timeLabel: String = ""
    ) -> InboxRowViewModel {
        InboxRowViewModel(
            id: id,
            sender: "Sender \(id)",
            title: "Subject \(id)",
            summary: nil,
            receivedAt: receivedAt,
            section: "Server bucket",
            isUnread: true,
            isGrouped: false,
            threadID: threadID ?? id,
            focusedMessageID: isChild ? id : nil,
            messageCount: 1,
            timeLabel: timeLabel,
            hasAttachments: false,
            presentationStatus: nil,
            isChild: isChild,
            isExpandable: isExpandable,
            isExpanded: isExpanded
        )
    }

    func testInboxMappingPreservesAuthoritativeOrderAcrossDateRuns() async {
        let demoRows = DemoAppFixtures.sections[0].rows
        let mailbox = MailboxResponse(
            label: .inbox,
            totalThreads: 4,
            sections: [
                GmailThreadSection(id: "today:rank-rbi", title: "Today", rows: [demoRows[3], demoRows[0]]),
                GmailThreadSection(id: "yesterday:rank-github", title: "Yesterday", rows: [demoRows[1]]),
                GmailThreadSection(id: "today:rank-apple", title: "Today", rows: [demoRows[2]])
            ],
            fullImportRunning: false,
            fullImportCompleted: true
        )
        let store = InboxStore(
            client: FixedMailboxAppClient(mailbox: mailbox),
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")

        await store.load()

        XCTAssertEqual(store.sections.map(\.id), ["today:rank-rbi", "yesterday:rank-github", "today:rank-apple"])
        XCTAssertEqual(store.sections.map(\.title), ["Today", "Yesterday", "Today"])
        XCTAssertEqual(
            store.flatRows.map(\.threadID),
            ["demo-rbi-today", "demo-google-today", "demo-github-today", "demo-apple-today"]
        )
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

    func testRapidMailboxSwitchKeepsNewestRefreshOwnedWhenCancelledRequestFails() async {
        let inbox = makeSingleRowMailbox(threadID: "inbox-thread", title: "Inbox")
        let archive = makeEmptyMailbox(label: .archive, totalThreads: 3, unreadThreads: 1)
        let sentGate = ReaderActionRequestGate()
        let archiveGate = ReaderActionRequestGate()
        let client = RealtimeEventAppClient(
            sessionMailbox: inbox,
            mailboxResponses: [inbox, archive],
            mailboxRequestGates: [.sent: sentGate, .archive: archiveGate],
            mailboxFailureLabels: [.sent]
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")

        await store.load()
        let sentSwitch = Task {
            await store.setMailboxLabel(.sent)
        }
        await sentGate.waitUntilRequestStarts()

        let archiveSwitch = Task {
            await store.setMailboxLabel(.archive)
        }
        await archiveGate.waitUntilRequestStarts()

        await sentGate.releaseRequest()
        await sentSwitch.value

        XCTAssertEqual(store.activeMailboxLabel, .archive)
        XCTAssertFalse(store.refreshFailed)

        let overlappingRefresh = Task {
            await store.refresh()
        }
        try? await Task.sleep(nanoseconds: 20_000_000)

        XCTAssertEqual(client.mailboxLabels.filter { $0 == .archive }.count, 1)
        XCTAssertFalse(store.refreshFailed)

        await archiveGate.releaseRequest()
        await archiveSwitch.value
        await overlappingRefresh.value

        XCTAssertEqual(store.activeMailboxLabel, .archive)
        XCTAssertEqual(store.activeMailboxCount, MailboxFolderCount(total: 3, unread: 1))
        XCTAssertFalse(store.refreshFailed)
    }

    func testForcedMailboxRefreshSupersedesSlowOrdinaryRefresh() async {
        let initial = makeSingleRowMailbox(threadID: "initial-thread", title: "Initial mailbox")
        let stale = makeSingleRowMailbox(threadID: "stale-thread", title: "Stale mailbox")
        let fresh = makeSingleRowMailbox(threadID: "fresh-thread", title: "Fresh mailbox")
        let ordinaryRefreshGate = ReaderActionRequestGate()
        let client = RealtimeEventAppClient(
            sessionMailbox: initial,
            mailboxResponses: [],
            mailboxRequestGates: [.inbox: ordinaryRefreshGate],
            mailboxRequestGateCalls: [.inbox: 2],
            mailboxResponsesByCall: [1: initial, 2: stale, 3: fresh]
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )

        await store.load()
        let ordinaryRefresh = Task {
            await store.performTargetedThreadAction(
                .star,
                threadID: "initial-thread",
                messageID: "initial-thread-message"
            )
        }
        await ordinaryRefreshGate.waitUntilRequestStarts()

        store.handleMailboxServerEvent(
            MailboxServerEvent(
                id: "force-over-ordinary",
                event: "mailbox-changed",
                data: #"{"mailbox_label":"inbox","payload":{"mailbox_revision":"forced-revision","mailbox_labels":["inbox"]}}"#
            )
        )
        for _ in 0..<400 where client.mailboxCallCount < 3 {
            try? await Task.sleep(nanoseconds: 5_000_000)
        }

        XCTAssertEqual(client.mailboxCallCount, 3)
        XCTAssertEqual(store.flatRows.map(\.title), ["Fresh mailbox"])

        await ordinaryRefreshGate.releaseRequest()
        await ordinaryRefresh.value

        XCTAssertEqual(store.flatRows.map(\.title), ["Fresh mailbox"])
        XCTAssertNotNil(store.lastForcedMailboxRefreshAt)
        XCTAssertNil(store.lastForcedMailboxRefreshError)
    }

    func testHiddenSidebarLoadAvoidsFolderCountAndPriorityThreadPrefetch() async {
        let mailbox = makeSingleRowMailbox(threadID: "visible-thread", title: "Visible row")
        let client = RealtimeEventAppClient(
            sessionMailbox: mailbox,
            mailboxResponses: [mailbox],
            supportsFolderCountPrefetch: true
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral())
        )

        await store.load()
        try? await Task.sleep(nanoseconds: 850_000_000)

        XCTAssertEqual(client.appSessionCallCount, 1)
        XCTAssertEqual(client.mailboxLabels, [.inbox])
        XCTAssertEqual(client.mailboxLimits, [100])
        XCTAssertEqual(client.threadCallCount, 0)
    }

    func testVisibleSidebarFetchesSmallFolderCountsOnlyWhileEnabled() async {
        let mailbox = makeSingleRowMailbox(threadID: "visible-thread", title: "Visible row")
        let client = RealtimeEventAppClient(
            sessionMailbox: mailbox,
            mailboxResponses: [mailbox],
            supportsFolderCountPrefetch: true
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral())
        )

        await store.load()
        await store.setFolderCountPrefetchEnabled(true)

        XCTAssertEqual(
            client.mailboxLabels,
            [.inbox, .starred, .drafts, .sent, .spam, .trash, .archive, .all]
        )
        XCTAssertEqual(client.mailboxLimits, [100] + Array(repeating: 1, count: 7))
        XCTAssertTrue(client.mailboxCursors.allSatisfy { $0 == nil })

        await store.setFolderCountPrefetchEnabled(false)
        await store.refreshFolderCounts()

        XCTAssertEqual(client.mailboxLabels.count, 8)
    }

    func testVisibleSidebarPublishesCompletedFolderCountSweepOnce() async {
        let mailbox = makeSingleRowMailbox(threadID: "visible-thread", title: "Visible row")
        let client = RealtimeEventAppClient(
            sessionMailbox: mailbox,
            mailboxResponses: [mailbox],
            supportsFolderCountPrefetch: true
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral())
        )

        await store.load()
        var publishedCounts: [[MailboxLabel: MailboxFolderCount]] = []
        let cancellable = store.$mailboxCounts
            .dropFirst()
            .sink { publishedCounts.append($0) }

        await store.setFolderCountPrefetchEnabled(true)

        XCTAssertEqual(publishedCounts.count, 1)
        XCTAssertEqual(publishedCounts[0].count, 8)
        withExtendedLifetime(cancellable) {}
    }

    func testHidingSidebarCancelsInFlightFolderCountSweep() async {
        let mailbox = makeSingleRowMailbox(threadID: "visible-thread", title: "Visible row")
        let countGate = ReaderActionRequestGate()
        let client = RealtimeEventAppClient(
            sessionMailbox: mailbox,
            mailboxResponses: [mailbox],
            supportsFolderCountPrefetch: true,
            mailboxCountGate: countGate
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral())
        )

        await store.load()
        let countTask = Task {
            await store.setFolderCountPrefetchEnabled(true)
        }
        await countGate.waitUntilRequestStarts()

        await store.setFolderCountPrefetchEnabled(false)
        await countGate.releaseRequest()
        await countTask.value

        XCTAssertEqual(client.mailboxLabels, [.inbox, .starred])
        XCTAssertEqual(client.mailboxLimits, [100, 1])
        XCTAssertNil(store.mailboxCounts[.starred])

        await store.setFolderCountPrefetchEnabled(true)

        XCTAssertEqual(
            client.mailboxLabels,
            [.inbox, .starred, .starred, .drafts, .sent, .spam, .trash, .archive, .all]
        )
        XCTAssertEqual(client.mailboxLimits, [100] + Array(repeating: 1, count: 8))
        XCTAssertNotNil(store.mailboxCounts[.all])
    }

    func testFolderNavigationRestartsCancelledVisibleSidebarCountSweep() async {
        let inbox = makeSingleRowMailbox(threadID: "inbox-thread", title: "Inbox row")
        let sent = MailboxResponse(
            label: .sent,
            totalThreads: 0,
            loadedThreads: 0,
            sections: [],
            fullImportRunning: false,
            fullImportCompleted: true
        )
        let countGate = ReaderActionRequestGate()
        let client = RealtimeEventAppClient(
            sessionMailbox: inbox,
            mailboxResponses: [inbox, sent],
            supportsFolderCountPrefetch: true,
            mailboxCountGate: countGate
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral())
        )

        await store.load()
        let initialCountTask = Task {
            await store.setFolderCountPrefetchEnabled(true)
        }
        await countGate.waitUntilRequestStarts()

        await store.setMailboxLabel(.sent)

        XCTAssertEqual(
            client.mailboxLabels,
            [.inbox, .starred, .sent, .starred, .drafts, .spam, .trash, .archive, .all]
        )
        XCTAssertEqual(client.mailboxLimits, [100, 1, 100] + Array(repeating: 1, count: 6))
        XCTAssertNotNil(store.mailboxCounts[.all])

        await countGate.releaseRequest()
        await initialCountTask.value
    }

    func testRepeatedSidebarEnableDoesNotRestartAnActiveCountSweep() async {
        let mailbox = makeSingleRowMailbox(threadID: "visible-thread", title: "Visible row")
        let countGate = ReaderActionRequestGate()
        let client = RealtimeEventAppClient(
            sessionMailbox: mailbox,
            mailboxResponses: [],
            supportsFolderCountPrefetch: true,
            mailboxCountGate: countGate
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral())
        )

        await store.load()
        let initialCountTask = Task {
            await store.setFolderCountPrefetchEnabled(true)
        }
        await countGate.waitUntilRequestStarts()

        await store.setFolderCountPrefetchEnabled(true)

        XCTAssertEqual(client.mailboxLabels, [.inbox, .starred])
        XCTAssertNil(store.mailboxCounts[.all])

        await countGate.releaseRequest()
        await initialCountTask.value

        XCTAssertEqual(
            client.mailboxLabels,
            [.inbox, .starred, .drafts, .sent, .spam, .trash, .archive, .all]
        )
        XCTAssertNotNil(store.mailboxCounts[.all])
    }

    func testFolderNavigationDoesNotRestartCompletedFreshCountSweep() async {
        let inbox = makeSingleRowMailbox(threadID: "inbox-thread", title: "Inbox row")
        let sent = makeEmptyMailbox(label: .sent, totalThreads: 4, unreadThreads: 1)
        let countResponses = [
            makeEmptyMailbox(label: .starred),
            makeEmptyMailbox(label: .drafts),
            makeEmptyMailbox(label: .sent),
            makeEmptyMailbox(label: .spam),
            makeEmptyMailbox(label: .trash),
            makeEmptyMailbox(label: .archive),
            makeEmptyMailbox(label: .all),
        ]
        let client = RealtimeEventAppClient(
            sessionMailbox: inbox,
            mailboxResponses: [inbox] + countResponses + [sent],
            supportsFolderCountPrefetch: true
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral())
        )

        await store.load()
        await store.setFolderCountPrefetchEnabled(true)
        let callsAfterCompletedSweep = client.mailboxCallCount

        await store.setMailboxLabel(.sent)

        XCTAssertEqual(client.mailboxCallCount, callsAfterCompletedSweep + 1)
        XCTAssertEqual(Array(client.mailboxLabels.suffix(1)), [.sent])
        XCTAssertEqual(Array(client.mailboxLimits.suffix(1)), [100])
        XCTAssertEqual(store.mailboxCounts[.sent], MailboxFolderCount(total: 4, unread: 1))
    }

    func testFolderNavigationFetchesOnlyChangedFolderWithoutPriorityThreadPrefetch() async {
        let inbox = makeSingleRowMailbox(threadID: "inbox-thread", title: "Inbox row")
        let sent = MailboxResponse(
            label: .sent,
            totalThreads: 0,
            loadedThreads: 0,
            sections: [],
            fullImportRunning: false,
            fullImportCompleted: true
        )
        let client = RealtimeEventAppClient(
            sessionMailbox: inbox,
            mailboxResponses: [inbox, sent],
            supportsFolderCountPrefetch: true
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral())
        )

        await store.load()
        await store.setMailboxLabel(.sent)
        await store.setMailboxLabel(.sent)
        try? await Task.sleep(nanoseconds: 850_000_000)

        XCTAssertEqual(client.mailboxLabels, [.inbox, .sent])
        XCTAssertEqual(client.mailboxLimits, [100, 100])
        XCTAssertEqual(client.threadCallCount, 0)
    }

    func testFolderCountFetchDoesNotReplaceOfflineFullMailboxCache() async throws {
        let fullMailbox = DemoAppFixtures.mailbox
        let countResponse = makeSingleRowMailbox(threadID: "count-row", title: "Count row")
        let backend = RealtimeEventAppClient(
            sessionMailbox: fullMailbox,
            mailboxResponses: [countResponse]
        )
        let localStore = MemoryLocalMailStore()
        let client = OfflineFirstAppClient(backend: backend, localMailStore: localStore)

        _ = try await client.appSession()
        localStore.writeMailbox(fullMailbox, userID: DemoAppFixtures.userID, label: .inbox)

        _ = try await client.mailboxFolderCount(label: .inbox)

        XCTAssertEqual(
            localStore.readMailbox(userID: DemoAppFixtures.userID, label: .inbox),
            fullMailbox
        )
        XCTAssertEqual(backend.mailboxLimits, [1])
    }

    func testSyncPollingPolicyDefersToFreshRealtimeEvents() {
        let now = Date(timeIntervalSince1970: 10_000)

        XCTAssertFalse(
            MailboxBackgroundRefreshPolicy.shouldPollSyncState(
                supportsRealtimeUpdates: true,
                realtimeConnected: true,
                lastRealtimeEventAt: now.addingTimeInterval(-24),
                now: now
            )
        )
    }

    func testSyncPollingPolicyPollsWhenConnectedStreamIsStaleOrMissingEvents() {
        let now = Date(timeIntervalSince1970: 10_000)

        XCTAssertTrue(
            MailboxBackgroundRefreshPolicy.shouldPollSyncState(
                supportsRealtimeUpdates: true,
                realtimeConnected: true,
                lastRealtimeEventAt: now.addingTimeInterval(-26),
                now: now
            )
        )
        XCTAssertTrue(
            MailboxBackgroundRefreshPolicy.shouldPollSyncState(
                supportsRealtimeUpdates: true,
                realtimeConnected: true,
                lastRealtimeEventAt: nil,
                now: now
            )
        )
    }

    func testSyncPollingPolicyPollsWithoutRealtimeConnectionOrSupport() {
        let now = Date(timeIntervalSince1970: 10_000)

        XCTAssertTrue(
            MailboxBackgroundRefreshPolicy.shouldPollSyncState(
                supportsRealtimeUpdates: true,
                realtimeConnected: false,
                lastRealtimeEventAt: now,
                now: now
            )
        )
        XCTAssertTrue(
            MailboxBackgroundRefreshPolicy.shouldPollSyncState(
                supportsRealtimeUpdates: false,
                realtimeConnected: true,
                lastRealtimeEventAt: now,
                now: now
            )
        )
    }

    func testThreadTaskOwnerRegistryIgnoresLateReleaseFromCancelledOwner() {
        var owners = ThreadTaskOwnerRegistry()
        let oldOwner = UUID(uuidString: "11111111-1111-1111-1111-111111111111")!
        let newOwner = UUID(uuidString: "22222222-2222-2222-2222-222222222222")!

        _ = owners.claim(threadID: "thread-1", ownerID: oldOwner)
        owners.cancel(threadID: "thread-1")
        _ = owners.claim(threadID: "thread-1", ownerID: newOwner)

        XCTAssertFalse(owners.release(threadID: "thread-1", ownerID: oldOwner))
        XCTAssertEqual(owners.ownerID(for: "thread-1"), newOwner)
        XCTAssertTrue(owners.release(threadID: "thread-1", ownerID: newOwner))
        XCTAssertNil(owners.ownerID(for: "thread-1"))
    }

    func testDefaultReaderBodyRefreshScheduleOutlivesFirstBackendRetryWindow() {
        let delays = ReaderBodyRefreshPolicy.defaultDelaysNanoseconds

        XCTAssertGreaterThanOrEqual(delays.last ?? 0, 30_000_000_000)
        XCTAssertGreaterThanOrEqual(delays.reduce(0, +), 60_000_000_000)
    }

    func testCompletedEmptyMailboxCanEnterMainInterface() async {
        let emptyMailbox = MailboxResponse(
            label: .inbox,
            totalThreads: 0,
            loadedThreads: 0,
            sections: [],
            fullImportRunning: false,
            fullImportCompleted: true
        )
        let store = InboxStore(
            client: FixedMailboxAppClient(mailbox: emptyMailbox),
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral())
        )
        store.setSessionToken("live-session-token")

        await store.load()

        XCTAssertEqual(store.mailboxVisibleRowCount, 0)
        XCTAssertTrue(store.isReadyForMainInterface)
        XCTAssertTrue(store.canEnterWithBuildingDashboard)
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

    func testOfflineQueuedArchiveDoesNotReappearFromStaleMailboxCache() async {
        let baseMailbox = makeSingleRowMailbox(threadID: "offline-thread", title: "Offline mail")
        let mailbox = MailboxResponse(
            label: baseMailbox.label,
            totalThreads: baseMailbox.totalThreads,
            unreadThreads: baseMailbox.unreadThreads,
            nextCursor: baseMailbox.nextCursor,
            loadedThreads: baseMailbox.loadedThreads,
            windowDays: baseMailbox.windowDays,
            sections: baseMailbox.sections,
            readyCount: baseMailbox.readyCount,
            pendingCount: baseMailbox.pendingCount,
            mailboxRevision: "revision-offline-action",
            generatedAt: "2026-07-25T12:00:00Z",
            oldestImportedAt: baseMailbox.oldestImportedAt,
            fullImportRunning: true,
            fullImportCompleted: false,
            syncGeneration: "generation-offline-action",
            phase: "syncing_history",
            initialTargetCount: 100,
            initialMetadataCount: 25,
            initialBodyTargetCount: 25,
            initialBodyReadyCount: 10,
            historyMetadataCount: 25,
            historyBodyReadyCount: 10,
            estimatedTotalCount: 358,
            initialWindowComplete: false,
            historyMetadataComplete: false,
            historyBodyComplete: false,
            lastProgressAt: "2026-07-25T12:00:00Z"
        )
        let backend = ActionMailboxAppClient(mailbox: mailbox)
        let localStore = MemoryLocalMailStore()
        let client = OfflineFirstAppClient(backend: backend, localMailStore: localStore)
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            localMailStore: localStore,
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")

        await store.load()
        XCTAssertEqual(store.flatRows.map(\.threadID), ["offline-thread"])

        backend.isOffline = true
        await store.performTargetedThreadAction(.archive, threadID: "offline-thread", messageID: nil)

        XCTAssertTrue(store.flatRows.isEmpty)
        XCTAssertEqual(store.pendingLocalActionCount, 1)
        XCTAssertEqual(localStore.pendingThreadActions().map(\.mailboxThreadID), ["offline-thread"])
        XCTAssertTrue(
            localStore
                .readMailbox(userID: DemoAppFixtures.userID, label: .inbox)?
                .sections
                .flatMap(\.rows)
                .isEmpty == true
        )
        let cachedMailbox = localStore.readMailbox(userID: DemoAppFixtures.userID, label: .inbox)
        XCTAssertEqual(cachedMailbox?.syncGeneration, "generation-offline-action")
        XCTAssertEqual(cachedMailbox?.initialMetadataCount, 25)
        XCTAssertEqual(cachedMailbox?.historyMetadataCount, 25)
        XCTAssertEqual(cachedMailbox?.estimatedTotalCount, 358)
    }

    func testReaderCardTargetsMessageActionsButKeepsArchiveThreadWide() async {
        let mailbox = makeSingleRowMailbox(threadID: "thread-1", title: "Thread")
        let client = ActionMailboxAppClient(mailbox: mailbox)
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral())
        )
        store.setSessionToken("live-session-token")
        await store.load()

        await store.performReaderThreadAction(.star, threadID: "thread-1", messageID: "message-older-1")
        await store.performReaderThreadAction(.moveTrash, threadID: "thread-1", messageID: "message-older-1")
        await store.performReaderThreadAction(.deleteForever, threadID: "thread-1", messageID: "message-older-1")
        await store.performReaderThreadAction(.archive, threadID: "thread-1", messageID: "message-older-1")

        XCTAssertEqual(client.enqueuedActions.map(\.action), [.star, .moveTrash, .deleteForever, .archive])
        XCTAssertEqual(
            client.enqueuedActions.map(\.targetMessageID),
            ["message-older-1", "message-older-1", "message-older-1", nil]
        )
    }

    func testSuccessfulReaderTrashClosesReaderWhenEmailLeavesMailbox() async {
        let threadID = "reader-trash-thread"
        let messageID = "reader-trash-message"
        let mailbox = makeSingleRowMailbox(threadID: threadID, title: "Reader trash")
        let thread = makeReaderActionThread(threadID: threadID, messageID: messageID, labelIDs: ["INBOX"])
        let client = ActionMailboxAppClient(mailbox: mailbox, threadResponse: thread)
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")
        await store.load()
        await store.openReader(threadID: threadID, focusedMessageID: messageID).value

        await store.performReaderThreadAction(.moveTrash, threadID: threadID, messageID: messageID)

        XCTAssertNil(store.readerThreadID)
        XCTAssertNil(store.readerFocusedMessageID)
        XCTAssertEqual(client.enqueuedActions.last?.action, .moveTrash)
    }

    func testFailedReaderTrashKeepsReaderOpen() async {
        let threadID = "failed-reader-trash-thread"
        let messageID = "failed-reader-trash-message"
        let mailbox = makeSingleRowMailbox(threadID: threadID, title: "Failed reader trash")
        let thread = makeReaderActionThread(threadID: threadID, messageID: messageID, labelIDs: ["INBOX"])
        let client = ActionMailboxAppClient(
            mailbox: mailbox,
            threadResponse: thread,
            shouldFailActions: true
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")
        await store.load()
        await store.openReader(threadID: threadID, focusedMessageID: messageID).value

        await store.performReaderThreadAction(.moveTrash, threadID: threadID, messageID: messageID)

        XCTAssertEqual(store.readerThreadID, threadID)
        XCTAssertEqual(store.readerFocusedMessageID, messageID)
        XCTAssertTrue(store.refreshFailed)
    }

    func testReaderLabelActionsImmediatelyUpdateTheLoadedMessage() async {
        let threadID = "reader-action-thread"
        let messageID = "reader-action-message"
        let mailbox = makeSingleRowMailbox(threadID: threadID, title: "Reader action")
        let thread = makeReaderActionThread(threadID: threadID, messageID: messageID, labelIDs: ["INBOX"])
        let client = ActionMailboxAppClient(mailbox: mailbox, threadResponse: thread)
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")
        await store.load()
        await store.openReader(threadID: threadID, focusedMessageID: messageID).value

        await store.performReaderThreadAction(.star, threadID: threadID, messageID: messageID)
        XCTAssertTrue(store.readerThread?.messages.first?.labelIDs.contains("STARRED") == true)

        await store.performReaderThreadAction(.unstar, threadID: threadID, messageID: messageID)
        XCTAssertFalse(store.readerThread?.messages.first?.labelIDs.contains("STARRED") == true)

        await store.performReaderThreadAction(.markUnread, threadID: threadID, messageID: messageID)
        XCTAssertTrue(store.readerThread?.messages.first?.labelIDs.contains("UNREAD") == true)

        await store.performReaderThreadAction(.markRead, threadID: threadID, messageID: messageID)
        XCTAssertFalse(store.readerThread?.messages.first?.labelIDs.contains("UNREAD") == true)
    }

    func testFailedReaderLabelActionRollsBackOnlyItsOptimisticChange() async {
        let threadID = "reader-action-thread"
        let messageID = "reader-action-message"
        let mailbox = makeSingleRowMailbox(threadID: threadID, title: "Reader action")
        let thread = makeReaderActionThread(
            threadID: threadID,
            messageID: messageID,
            labelIDs: ["INBOX", "IMPORTANT"]
        )
        let actionGate = ReaderActionRequestGate()
        let client = ActionMailboxAppClient(
            mailbox: mailbox,
            threadResponse: thread,
            actionGate: actionGate,
            shouldFailActions: true
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")
        await store.load()
        await store.openReader(threadID: threadID, focusedMessageID: messageID).value

        let actionTask = Task {
            await store.performReaderThreadAction(.star, threadID: threadID, messageID: messageID)
        }
        await actionGate.waitUntilRequestStarts()

        XCTAssertEqual(store.readerThread?.messages.first?.labelIDs, ["INBOX", "IMPORTANT", "STARRED"])

        await actionGate.releaseRequest()
        await actionTask.value

        XCTAssertEqual(store.readerThread?.messages.first?.labelIDs, ["INBOX", "IMPORTANT"])
        XCTAssertTrue(store.refreshFailed)
    }

    func testAcceptedReaderLabelActionUpdatesThreadLoadedWhileRequestWasInFlight() async {
        let threadID = "reader-action-thread"
        let messageID = "reader-action-message"
        let mailbox = makeSingleRowMailbox(threadID: threadID, title: "Reader action")
        let thread = makeReaderActionThread(threadID: threadID, messageID: messageID, labelIDs: ["INBOX"])
        let actionGate = ReaderActionRequestGate()
        let client = ActionMailboxAppClient(
            mailbox: mailbox,
            threadResponse: thread,
            actionGate: actionGate
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")
        await store.load()

        let actionTask = Task {
            await store.performReaderThreadAction(.star, threadID: threadID, messageID: messageID)
        }
        await actionGate.waitUntilRequestStarts()
        await store.openReader(threadID: threadID, focusedMessageID: messageID).value
        XCTAssertFalse(store.readerThread?.messages.first?.labelIDs.contains("STARRED") == true)

        await actionGate.releaseRequest()
        await actionTask.value

        XCTAssertTrue(store.readerThread?.messages.first?.labelIDs.contains("STARRED") == true)
    }

    func testStaleReaderFetchCannotOverwriteLabelsMutatedWhileRequestWasInFlight() async {
        let threadID = "reader-label-race-thread"
        let messageID = "reader-label-race-message"
        let mailbox = makeSingleRowMailbox(threadID: threadID, title: "Reader label race")
        let cachedThread = makeReaderActionThread(
            threadID: threadID,
            messageID: messageID,
            labelIDs: ["INBOX"]
        )
        let staleFetchedThread = makeReaderActionThread(
            threadID: threadID,
            messageID: messageID,
            labelIDs: ["INBOX"]
        )
        let requestGate = ReaderActionRequestGate()
        let client = RealtimeEventAppClient(
            sessionMailbox: mailbox,
            mailboxResponses: [mailbox, mailbox],
            threadResponses: [staleFetchedThread],
            threadRequestGate: requestGate
        )
        let threadCache = ThreadCache(defaults: .ephemeral())
        threadCache.write(cachedThread, userID: DemoAppFixtures.userID, threadID: threadID)
        let localStore = MemoryLocalMailStore()
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: threadCache,
            localMailStore: localStore,
            automaticallyPrefetchThreads: false
        )

        await store.load()
        let openTask = store.openReader(threadID: threadID, focusedMessageID: messageID)
        await requestGate.waitUntilRequestStarts()

        await store.performReaderThreadAction(.star, threadID: threadID, messageID: messageID)
        await store.performReaderThreadAction(.markUnread, threadID: threadID, messageID: messageID)
        XCTAssertTrue(store.readerThread?.messages.first?.labelIDs.contains("STARRED") == true)
        XCTAssertTrue(store.readerThread?.messages.first?.labelIDs.contains("UNREAD") == true)

        await requestGate.releaseRequest()
        await openTask.value

        XCTAssertTrue(store.readerThread?.messages.first?.labelIDs.contains("STARRED") == true)
        XCTAssertTrue(store.readerThread?.messages.first?.labelIDs.contains("UNREAD") == true)
        XCTAssertTrue(
            threadCache
                .read(userID: DemoAppFixtures.userID, threadID: threadID)?
                .messages.first?
                .labelIDs.contains("STARRED") == true
        )
        XCTAssertTrue(
            threadCache
                .read(userID: DemoAppFixtures.userID, threadID: threadID)?
                .messages.first?
                .labelIDs.contains("UNREAD") == true
        )
        XCTAssertTrue(
            localStore
                .readThread(userID: DemoAppFixtures.userID, threadID: threadID)?
                .messages.first?
                .labelIDs.contains("STARRED") == true
        )
        XCTAssertTrue(
            localStore
                .readThread(userID: DemoAppFixtures.userID, threadID: threadID)?
                .messages.first?
                .labelIDs.contains("UNREAD") == true
        )
    }

    func testReaderFetchStartedDuringLabelMutationPreservesSettledLabel() async {
        let threadID = "reader-label-settlement-race"
        let messageID = "reader-label-settlement-message"
        let mailbox = makeSingleRowMailbox(threadID: threadID, title: "Reader label settlement")
        let initialThread = makeReaderActionThread(
            threadID: threadID,
            messageID: messageID,
            labelIDs: ["INBOX"]
        )
        let staleFetchedThread = makeReaderActionThread(
            threadID: threadID,
            messageID: messageID,
            labelIDs: ["INBOX"]
        )
        let actionGate = ReaderActionRequestGate()
        let readerGate = ReaderActionRequestGate()
        let client = RealtimeEventAppClient(
            sessionMailbox: mailbox,
            mailboxResponses: [mailbox, mailbox],
            threadResponses: [initialThread, staleFetchedThread],
            threadRequestGate: readerGate,
            threadRequestGateCall: 2,
            actionRequestGate: actionGate
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )

        await store.load()
        await store.openReader(threadID: threadID, focusedMessageID: messageID).value

        let actionTask = Task {
            await store.performReaderThreadAction(.star, threadID: threadID, messageID: messageID)
        }
        await actionGate.waitUntilRequestStarts()
        XCTAssertTrue(store.readerThread?.messages.first?.labelIDs.contains("STARRED") == true)

        let readerTask = Task {
            await store.prefetchThread(threadID: threadID, force: true, silent: false)
        }
        await readerGate.waitUntilRequestStarts()

        await actionGate.releaseRequest()
        await actionTask.value
        await readerGate.releaseRequest()
        _ = await readerTask.value

        XCTAssertTrue(store.readerThread?.messages.first?.labelIDs.contains("STARRED") == true)
    }

    func testLabelActionCompletedBeforeColdReaderFetchCannotBeOverwrittenByStaleResponse() async {
        let threadID = "cold-reader-label-race"
        let messageID = "cold-reader-label-message"
        let mailbox = makeSingleRowMailbox(threadID: threadID, title: "Cold reader label race")
        let staleFetchedThread = makeReaderActionThread(
            threadID: threadID,
            messageID: messageID,
            labelIDs: ["INBOX", "UNREAD"]
        )
        let actionGate = ReaderActionRequestGate()
        let readerGate = ReaderActionRequestGate()
        let client = RealtimeEventAppClient(
            sessionMailbox: mailbox,
            mailboxResponses: [mailbox, mailbox],
            threadResponses: [staleFetchedThread],
            threadRequestGate: readerGate,
            actionRequestGate: actionGate
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )

        await store.load()
        let readerTask = store.openReader(threadID: threadID, focusedMessageID: messageID)
        await readerGate.waitUntilRequestStarts()
        XCTAssertNil(store.readerThread)

        let actionTask = Task {
            await store.performReaderThreadAction(.markRead, threadID: threadID, messageID: messageID)
        }
        await actionGate.waitUntilRequestStarts()
        await actionGate.releaseRequest()
        await actionTask.value

        await readerGate.releaseRequest()
        await readerTask.value

        XCTAssertFalse(store.readerThread?.messages.first?.labelIDs.contains("UNREAD") == true)
    }

    func testLoadedOppositeLabelActionSupersedesSettledColdReaderIntent() async {
        let threadID = "cold-reader-opposite-action"
        let messageID = "cold-reader-opposite-message"
        let mailbox = makeSingleRowMailbox(threadID: threadID, title: "Cold reader opposite action")
        let unreadThread = makeReaderActionThread(
            threadID: threadID,
            messageID: messageID,
            labelIDs: ["INBOX", "UNREAD"]
        )
        let readerGate = ReaderActionRequestGate()
        let client = RealtimeEventAppClient(
            sessionMailbox: mailbox,
            mailboxResponses: [mailbox, mailbox, mailbox],
            threadResponses: [unreadThread, unreadThread],
            threadRequestGate: readerGate
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )

        await store.load()
        let readerTask = store.openReader(threadID: threadID, focusedMessageID: messageID)
        await readerGate.waitUntilRequestStarts()
        await store.performReaderThreadAction(.markRead, threadID: threadID, messageID: messageID)
        await readerGate.releaseRequest()
        await readerTask.value
        XCTAssertFalse(store.readerThread?.messages.first?.labelIDs.contains("UNREAD") == true)

        await store.performReaderThreadAction(.markUnread, threadID: threadID, messageID: messageID)
        XCTAssertTrue(store.readerThread?.messages.first?.labelIDs.contains("UNREAD") == true)

        await store.prefetchThread(threadID: threadID, force: true, silent: false)

        XCTAssertTrue(store.readerThread?.messages.first?.labelIDs.contains("UNREAD") == true)
    }

    func testDiskPersistenceDelayCannotOverwriteNewerOptimisticReaderLabel() async {
        let threadID = "reader-persistence-race"
        let messageID = "reader-persistence-message"
        let mailbox = makeSingleRowMailbox(threadID: threadID, title: "Reader persistence race")
        let initialThread = makeReaderActionThread(
            threadID: threadID,
            messageID: messageID,
            labelIDs: ["INBOX"]
        )
        let staleFetchedThread = makeReaderActionThread(
            threadID: threadID,
            messageID: messageID,
            labelIDs: ["INBOX"]
        )
        let client = RealtimeEventAppClient(
            sessionMailbox: mailbox,
            mailboxResponses: [mailbox, mailbox],
            threadResponses: [initialThread, staleFetchedThread]
        )
        let localStore = BlockingThreadWriteLocalMailStore(blockOnThreadWriteCall: 2)
        defer { localStore.releaseBlockedThreadWrite() }
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            localMailStore: localStore,
            automaticallyPrefetchThreads: false
        )

        await store.load()
        await store.openReader(threadID: threadID, focusedMessageID: messageID).value

        let refreshTask = Task {
            await store.prefetchThread(threadID: threadID, force: true, silent: false)
        }
        for _ in 0..<100 where !localStore.hasBlockedThreadWrite {
            try? await Task.sleep(nanoseconds: 5_000_000)
        }
        XCTAssertTrue(localStore.hasBlockedThreadWrite)

        let actionTask = Task {
            await store.performReaderThreadAction(.star, threadID: threadID, messageID: messageID)
        }
        for _ in 0..<100 where store.readerThread?.messages.first?.labelIDs.contains("STARRED") != true {
            try? await Task.sleep(nanoseconds: 5_000_000)
        }
        XCTAssertTrue(store.readerThread?.messages.first?.labelIDs.contains("STARRED") == true)

        localStore.releaseBlockedThreadWrite()
        _ = await refreshTask.value
        await actionTask.value

        XCTAssertTrue(store.readerThread?.messages.first?.labelIDs.contains("STARRED") == true)
    }

    func testCapturedPermanentDeleteTargetDoesNotFollowLaterSelection() async {
        let mailbox = makeSingleRowMailbox(threadID: "selected-thread", title: "Selected")
        let client = ActionMailboxAppClient(mailbox: mailbox)
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral())
        )
        store.setSessionToken("live-session-token")
        await store.load()

        let capturedThreadID = "captured-thread"
        let capturedMessageID = "captured-message"
        store.select(threadID: "different-thread", focusedMessageID: "different-message", prefetch: false)

        await store.performTargetedThreadAction(
            .deleteForever,
            threadID: capturedThreadID,
            messageID: capturedMessageID
        )

        XCTAssertEqual(client.enqueuedActions.last?.mailboxThreadID, capturedThreadID)
        XCTAssertEqual(client.enqueuedActions.last?.targetMessageID, capturedMessageID)
        XCTAssertEqual(client.enqueuedActions.last?.action, .deleteForever)
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
            messageCount: 2,
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
        XCTAssertEqual(store.flatRows[2].visualTone(), .unread)

        store.select(threadID: "parent-thread", focusedMessageID: "msg-2", prefetch: false)
        store.openActiveSelection()

        XCTAssertEqual(store.readerThreadID, "parent-thread")
        XCTAssertEqual(store.readerFocusedMessageID, "msg-2")

        store.toggleExpansion(threadID: "parent-thread")
        XCTAssertEqual(store.flatRows.map(\.id), ["parent-thread"])
        XCTAssertNil(store.selectedMessageID)
    }

    func testProjectedConversationWithOneSyntheticChildKeepsDisclosureVisible() async {
        let threadID = "projected-conversation"
        let newest = makeConversationChild(
            id: "message-2",
            threadID: threadID,
            receivedAt: "2026-05-29T10:00:00+05:30"
        )
        let row = makeMailboxRow(
            threadID: threadID,
            latestSourceRecordID: newest.messageID,
            receivedAt: newest.receivedAt,
            title: "Projected conversation",
            messageCount: 2,
            children: [newest]
        )
        let store = InboxStore(
            client: FixedMailboxAppClient(mailbox: makeConversationMailbox(row: row)),
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral())
        )
        store.setSessionToken("live-session-token")

        await store.load()

        XCTAssertEqual(store.flatRows.map(\.id), [threadID])
        XCTAssertEqual(store.flatRows.first?.messageCount, 2)
        XCTAssertEqual(store.flatRows.first?.isGrouped, true)
        XCTAssertEqual(store.flatRows.first?.isExpandable, true)
    }

    func testLazyConversationExpansionUsesCompleteThreadWithoutDuplicatesInChronologicalOrder() async {
        let threadID = "lazy-conversation"
        let newestChild = makeConversationChild(
            id: "message-3",
            threadID: threadID,
            receivedAt: "2026-05-29T10:00:00+05:30"
        )
        let row = makeMailboxRow(
            threadID: threadID,
            latestSourceRecordID: newestChild.messageID,
            receivedAt: newestChild.receivedAt,
            title: "Lazy conversation",
            messageCount: 3,
            children: [newestChild]
        )
        let completeThread = makeConversationThread(
            threadID: threadID,
            messages: [
                makeConversationMessage(
                    id: "message-3",
                    threadID: threadID,
                    receivedAt: "2026-05-29T04:30:00Z"
                ),
                makeConversationMessage(
                    id: "message-2",
                    threadID: threadID,
                    receivedAt: "2026-05-29T09:45:00+05:30"
                ),
                makeConversationMessage(
                    id: "message-1",
                    threadID: threadID,
                    receivedAt: "2026-05-29T03:30:00Z"
                ),
                makeConversationMessage(
                    id: "message-2",
                    threadID: threadID,
                    receivedAt: "2026-05-29T09:30:00+05:30"
                ),
            ],
            totalMessages: 3
        )
        let client = FixedMailboxAppClient(
            mailbox: makeConversationMailbox(row: row),
            threadResponses: [completeThread]
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral())
        )
        store.setSessionToken("live-session-token")
        await store.load()

        store.toggleExpansion(threadID: threadID)
        await waitForConversationState {
            store.flatRows.first?.isExpanded == true
        }

        XCTAssertEqual(client.threadCallCount, 1)
        XCTAssertEqual(
            store.flatRows.map(\.id),
            [
                threadID,
                "\(threadID)::message::message-1",
                "\(threadID)::message::message-2",
                "\(threadID)::message::message-3",
            ]
        )
        XCTAssertEqual(Set(store.flatRows.map(\.id)).count, store.flatRows.count)
    }

    func testLazyConversationExpansionRefetchesWhenCachedContentRevisionIsStale() async {
        let threadID = "revision-conversation"
        let newest = makeConversationChild(
            id: "current-message-2",
            threadID: threadID,
            receivedAt: "2026-05-29T10:00:00+05:30"
        )
        let row = makeMailboxRow(
            threadID: threadID,
            latestSourceRecordID: newest.messageID,
            receivedAt: newest.receivedAt,
            title: "Revision conversation",
            messageCount: 2,
            contentRevision: "revision-current",
            children: [newest]
        )
        let staleThread = makeConversationThread(
            threadID: threadID,
            messages: [
                makeConversationMessage(
                    id: "stale-message-1",
                    threadID: threadID,
                    receivedAt: "2026-05-29T08:00:00+05:30"
                ),
                makeConversationMessage(
                    id: "stale-message-2",
                    threadID: threadID,
                    receivedAt: "2026-05-29T09:00:00+05:30"
                ),
            ],
            contentRevision: "revision-stale"
        )
        let currentThread = makeConversationThread(
            threadID: threadID,
            messages: [
                makeConversationMessage(
                    id: "current-message-1",
                    threadID: threadID,
                    receivedAt: "2026-05-29T09:00:00+05:30"
                ),
                makeConversationMessage(
                    id: "current-message-2",
                    threadID: threadID,
                    receivedAt: "2026-05-29T10:00:00+05:30"
                ),
            ],
            contentRevision: "revision-current"
        )
        let client = FixedMailboxAppClient(
            mailbox: makeConversationMailbox(row: row),
            threadResponses: [currentThread]
        )
        let threadCache = ThreadCache(defaults: .ephemeral())
        threadCache.write(staleThread, userID: DemoAppFixtures.userID, threadID: threadID)
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: threadCache,
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")
        await store.load()

        store.toggleExpansion(threadID: threadID)
        await waitForConversationState {
            store.flatRows.first?.isExpanded == true
        }

        XCTAssertEqual(client.threadCallCount, 1)
        XCTAssertEqual(
            store.flatRows.map(\.id),
            [
                threadID,
                "\(threadID)::message::current-message-1",
                "\(threadID)::message::current-message-2",
            ]
        )
        XCTAssertEqual(store.openedThreads[threadID]?.contentRevision, "revision-current")
    }

    func testLazyConversationExpansionAcceptsNewerAuthoritativeNetworkRevision() async {
        let threadID = "newer-network-revision-conversation"
        let projectedChild = makeConversationChild(
            id: "revision-a-message",
            threadID: threadID,
            receivedAt: "2026-05-29T09:00:00+05:30"
        )
        let row = makeMailboxRow(
            threadID: threadID,
            latestSourceRecordID: projectedChild.messageID,
            receivedAt: projectedChild.receivedAt,
            title: "Newer network revision",
            messageCount: 2,
            contentRevision: "revision-a",
            children: [projectedChild]
        )
        let newerThread = makeConversationThread(
            threadID: threadID,
            messages: [
                makeConversationMessage(
                    id: "revision-b-message-1",
                    threadID: threadID,
                    receivedAt: "2026-05-29T09:00:00+05:30"
                ),
                makeConversationMessage(
                    id: "revision-b-message-2",
                    threadID: threadID,
                    receivedAt: "2026-05-29T10:00:00+05:30"
                ),
            ],
            contentRevision: "revision-b"
        )
        let client = FixedMailboxAppClient(
            mailbox: makeConversationMailbox(row: row),
            threadResponses: [newerThread]
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")
        await store.load()

        store.toggleExpansion(threadID: threadID)
        await waitForConversationState {
            store.flatRows.first?.isExpanded == true
        }

        XCTAssertEqual(client.threadCallCount, 1)
        XCTAssertEqual(store.openedThreads[threadID]?.contentRevision, "revision-b")
        XCTAssertEqual(
            store.flatRows.map(\.id),
            [
                threadID,
                "\(threadID)::message::revision-b-message-1",
                "\(threadID)::message::revision-b-message-2",
            ]
        )
    }

    func testConversationExpansionDoesNotBlessResponseAfterMailboxRowAdvancesMidflight() async {
        let threadID = "midflight-row-advance"
        let rowA = makeMailboxRow(
            threadID: threadID,
            latestSourceRecordID: "revision-a-message-2",
            receivedAt: "2026-05-29T10:00:00+05:30",
            title: "Revision A",
            messageCount: 2,
            contentRevision: "revision-a",
            children: [
                makeConversationChild(
                    id: "revision-a-message-2",
                    threadID: threadID,
                    receivedAt: "2026-05-29T10:00:00+05:30"
                )
            ]
        )
        let rowC = makeMailboxRow(
            threadID: threadID,
            latestSourceRecordID: "revision-c-message-2",
            receivedAt: "2026-05-29T11:00:00+05:30",
            title: "Revision C",
            messageCount: 2,
            contentRevision: "revision-c",
            children: [
                makeConversationChild(
                    id: "revision-c-message-2",
                    threadID: threadID,
                    receivedAt: "2026-05-29T11:00:00+05:30"
                )
            ]
        )
        let responseB = makeConversationThread(
            threadID: threadID,
            messages: [
                makeConversationMessage(
                    id: "revision-b-message-1",
                    threadID: threadID,
                    receivedAt: "2026-05-29T09:30:00+05:30"
                ),
                makeConversationMessage(
                    id: "revision-b-message-2",
                    threadID: threadID,
                    receivedAt: "2026-05-29T10:30:00+05:30"
                ),
            ],
            contentRevision: "revision-b"
        )
        let requestGate = ReaderActionRequestGate()
        let mailboxA = makeConversationMailbox(row: rowA).withTestMailboxRevision("mailbox-a")
        let mailboxC = makeConversationMailbox(row: rowC).withTestMailboxRevision("mailbox-c")
        let client = RealtimeEventAppClient(
            sessionMailbox: mailboxA,
            mailboxResponses: [mailboxA, mailboxC],
            threadResponses: [responseB],
            threadRequestGate: requestGate
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )

        await store.load()
        store.toggleExpansion(threadID: threadID)
        await requestGate.waitUntilRequestStarts()

        store.handleMailboxServerEvent(
            MailboxServerEvent(
                id: "advance-to-c",
                event: "mailbox-changed",
                data: #"{"mailbox_label":"inbox","payload":{"mailbox_revision":"mailbox-c","mailbox_labels":["inbox"]}}"#
            )
        )
        try? await Task.sleep(nanoseconds: 1_200_000_000)
        XCTAssertEqual(store.flatRows.first?.title, "Revision C")
        XCTAssertFalse(store.pendingConversationExpansionThreadIDs.contains(threadID))
        await requestGate.releaseRequest()
        await waitForConversationState {
            store.openedThreads[threadID]?.contentRevision == "revision-b"
        }

        XCTAssertEqual(store.flatRows.map(\.id), [threadID])
        XCTAssertEqual(store.flatRows.first?.isExpanded, false)
        XCTAssertEqual(store.flatRows.first?.isExpandable, true)
    }

    func testConversationExpansionRetriesPreexistingJoinedMismatchWithFreshRequest() async {
        let threadID = "joined-mismatch"
        let row = makeMailboxRow(
            threadID: threadID,
            latestSourceRecordID: "revision-a-message-2",
            receivedAt: "2026-05-29T10:00:00+05:30",
            title: "Joined mismatch",
            messageCount: 2,
            contentRevision: "revision-a",
            children: [
                makeConversationChild(
                    id: "revision-a-message-2",
                    threadID: threadID,
                    receivedAt: "2026-05-29T10:00:00+05:30"
                )
            ]
        )
        let joinedResponse = makeConversationThread(
            threadID: threadID,
            messages: [
                makeConversationMessage(
                    id: "joined-message-1",
                    threadID: threadID,
                    receivedAt: "2026-05-29T09:00:00+05:30"
                ),
                makeConversationMessage(
                    id: "joined-message-2",
                    threadID: threadID,
                    receivedAt: "2026-05-29T10:00:00+05:30"
                ),
            ],
            contentRevision: "revision-b"
        )
        let freshResponse = makeConversationThread(
            threadID: threadID,
            messages: [
                makeConversationMessage(
                    id: "fresh-message-1",
                    threadID: threadID,
                    receivedAt: "2026-05-29T09:30:00+05:30"
                ),
                makeConversationMessage(
                    id: "fresh-message-2",
                    threadID: threadID,
                    receivedAt: "2026-05-29T10:30:00+05:30"
                ),
            ],
            contentRevision: "revision-c"
        )
        let requestGate = ReaderActionRequestGate()
        let client = RealtimeEventAppClient(
            sessionMailbox: makeConversationMailbox(row: row),
            mailboxResponses: [makeConversationMailbox(row: row)],
            threadResponses: [joinedResponse, freshResponse, freshResponse],
            threadRequestGate: requestGate
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )

        await store.load()
        let preexistingFetch = Task {
            await store.prefetchThread(threadID: threadID, force: true, silent: true)
        }
        await requestGate.waitUntilRequestStarts()
        store.toggleExpansion(threadID: threadID)
        await requestGate.releaseRequest()
        _ = await preexistingFetch.value
        await waitForConversationState {
            store.flatRows.first?.isExpanded == true
                && client.threadCallCount == 2
        }

        XCTAssertEqual(
            store.flatRows.map(\.id),
            [
                threadID,
                "\(threadID)::message::fresh-message-1",
                "\(threadID)::message::fresh-message-2",
            ]
        )
        XCTAssertEqual(store.openedThreads[threadID]?.contentRevision, "revision-c")

        let subsequentFetchSucceeded = await store.prefetchThread(
            threadID: threadID,
            force: true,
            silent: true
        )
        XCTAssertTrue(subsequentFetchSucceeded)
        XCTAssertEqual(client.threadCallCount, 3)
    }

    func testAuthoritativeNetworkExpansionAcceptsConversationShrinkingFromThreeToTwoMessages() async {
        let threadID = "conversation-shrank"
        let row = makeMailboxRow(
            threadID: threadID,
            latestSourceRecordID: "revision-a-message-3",
            receivedAt: "2026-05-29T10:00:00+05:30",
            title: "Three messages",
            messageCount: 3,
            contentRevision: "revision-a",
            children: [
                makeConversationChild(
                    id: "revision-a-message-3",
                    threadID: threadID,
                    receivedAt: "2026-05-29T10:00:00+05:30"
                )
            ]
        )
        let currentThread = makeConversationThread(
            threadID: threadID,
            messages: [
                makeConversationMessage(
                    id: "revision-b-message-1",
                    threadID: threadID,
                    receivedAt: "2026-05-29T09:00:00+05:30"
                ),
                makeConversationMessage(
                    id: "revision-b-message-2",
                    threadID: threadID,
                    receivedAt: "2026-05-29T10:00:00+05:30"
                ),
            ],
            totalMessages: 2,
            contentRevision: "revision-b"
        )
        let client = FixedMailboxAppClient(
            mailbox: makeConversationMailbox(row: row),
            threadResponses: [currentThread]
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")

        await store.load()
        store.toggleExpansion(threadID: threadID)
        await waitForConversationState {
            store.flatRows.first?.isExpanded == true
        }

        XCTAssertEqual(client.threadCallCount, 1)
        XCTAssertEqual(store.flatRows.count, 3)
        XCTAssertEqual(
            store.flatRows.dropFirst().map(\.id),
            [
                "\(threadID)::message::revision-b-message-1",
                "\(threadID)::message::revision-b-message-2",
            ]
        )
    }

    func testConversationExpansionAuthorityPrunesAsMailboxIdentityCatchesUpThenAdvances() async {
        let threadID = "authority-pruning"
        func row(revision: String, latestID: String, title: String) -> GmailThreadRow {
            makeMailboxRow(
                threadID: threadID,
                latestSourceRecordID: latestID,
                receivedAt: "2026-05-29T10:00:00+05:30",
                title: title,
                messageCount: 2,
                contentRevision: revision,
                children: [
                    makeConversationChild(
                        id: latestID,
                        threadID: threadID,
                        receivedAt: "2026-05-29T10:00:00+05:30"
                    )
                ]
            )
        }
        let rowA = row(revision: "revision-a", latestID: "revision-a-message-2", title: "Revision A")
        let rowB = row(revision: "revision-b", latestID: "revision-b-message-2", title: "Revision B")
        let rowC = row(revision: "revision-c", latestID: "revision-c-message-2", title: "Revision C")
        let threadB = makeConversationThread(
            threadID: threadID,
            messages: [
                makeConversationMessage(
                    id: "revision-b-message-1",
                    threadID: threadID,
                    receivedAt: "2026-05-29T09:00:00+05:30"
                ),
                makeConversationMessage(
                    id: "revision-b-message-2",
                    threadID: threadID,
                    receivedAt: "2026-05-29T10:00:00+05:30"
                ),
            ],
            contentRevision: "revision-b"
        )
        let mailboxA = makeConversationMailbox(row: rowA).withTestMailboxRevision("mailbox-a")
        let mailboxB = makeConversationMailbox(row: rowB).withTestMailboxRevision("mailbox-b")
        let mailboxC = makeConversationMailbox(row: rowC).withTestMailboxRevision("mailbox-c")
        let client = RealtimeEventAppClient(
            sessionMailbox: mailboxA,
            mailboxResponses: [mailboxA, mailboxB, mailboxC],
            threadResponses: [threadB]
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )

        await store.load()
        store.toggleExpansion(threadID: threadID)
        await waitForConversationState {
            store.flatRows.first?.isExpanded == true
        }

        store.handleMailboxServerEvent(
            MailboxServerEvent(
                id: "catch-up-to-b",
                event: "mailbox-changed",
                data: #"{"mailbox_label":"inbox","payload":{"mailbox_revision":"mailbox-b","mailbox_labels":["inbox"]}}"#
            )
        )
        try? await Task.sleep(nanoseconds: 1_200_000_000)
        XCTAssertEqual(store.flatRows.first?.title, "Revision B")
        XCTAssertEqual(store.flatRows.first?.isExpanded, true)
        XCTAssertEqual(store.flatRows.count, 3)

        store.handleMailboxServerEvent(
            MailboxServerEvent(
                id: "advance-to-c",
                event: "mailbox-changed",
                data: #"{"mailbox_label":"inbox","payload":{"mailbox_revision":"mailbox-c","mailbox_labels":["inbox"]}}"#
            )
        )
        try? await Task.sleep(nanoseconds: 1_200_000_000)
        XCTAssertEqual(store.flatRows.first?.title, "Revision C")

        XCTAssertEqual(store.flatRows.map(\.id), [threadID])
        XCTAssertEqual(store.flatRows.first?.isExpanded, false)
        XCTAssertFalse(store.expandedThreadIDs.contains(threadID))
    }

    func testDiskCacheReadCannotOverwriteConcurrentlyOpenedNewerThread() async {
        let threadID = "disk-race-conversation"
        let projectedChild = makeConversationChild(
            id: "revision-b-message-2",
            threadID: threadID,
            receivedAt: "2026-05-29T10:00:00+05:30"
        )
        let row = makeMailboxRow(
            threadID: threadID,
            latestSourceRecordID: projectedChild.messageID,
            receivedAt: projectedChild.receivedAt,
            title: "Disk race conversation",
            messageCount: 2,
            contentRevision: "revision-b",
            children: [projectedChild]
        )
        let staleDiskThread = makeConversationThread(
            threadID: threadID,
            messages: [
                makeConversationMessage(
                    id: "revision-a-message-1",
                    threadID: threadID,
                    receivedAt: "2026-05-29T08:00:00+05:30"
                ),
                makeConversationMessage(
                    id: "revision-a-message-2",
                    threadID: threadID,
                    receivedAt: "2026-05-29T09:00:00+05:30"
                ),
            ],
            contentRevision: "revision-a"
        )
        let newerThread = makeConversationThread(
            threadID: threadID,
            messages: [
                makeConversationMessage(
                    id: "revision-b-message-1",
                    threadID: threadID,
                    receivedAt: "2026-05-29T09:00:00+05:30"
                ),
                makeConversationMessage(
                    id: "revision-b-message-2",
                    threadID: threadID,
                    receivedAt: "2026-05-29T10:00:00+05:30"
                ),
            ],
            contentRevision: "revision-b"
        )
        let localStore = BlockingThreadWriteLocalMailStore(
            blockOnThreadWriteCall: .max,
            blockOnThreadReadCall: 1
        )
        localStore.writeThread(
            staleDiskThread,
            userID: DemoAppFixtures.userID,
            threadID: threadID
        )
        let client = FixedMailboxAppClient(
            mailbox: makeConversationMailbox(row: row),
            threadResponses: [newerThread]
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            localMailStore: localStore,
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")
        await store.load()

        store.toggleExpansion(threadID: threadID)
        await waitForConversationState {
            localStore.hasBlockedThreadRead
        }

        let newerFetch = Task {
            await store.prefetchThread(threadID: threadID, force: true, silent: true)
        }
        await waitForConversationState {
            store.openedThreads[threadID]?.contentRevision == "revision-b"
        }
        localStore.releaseBlockedThreadRead()

        let newerFetchSucceeded = await newerFetch.value
        XCTAssertTrue(newerFetchSucceeded)
        await waitForConversationState {
            store.flatRows.first?.isExpanded == true
        }

        XCTAssertEqual(localStore.recordedThreadReadCallCount, 1)
        XCTAssertEqual(client.threadCallCount, 1)
        XCTAssertEqual(store.openedThreads[threadID]?.contentRevision, "revision-b")
        XCTAssertEqual(
            store.flatRows.map(\.id),
            [
                threadID,
                "\(threadID)::message::revision-b-message-1",
                "\(threadID)::message::revision-b-message-2",
            ]
        )
    }

    func testLazyConversationExpansionReadsCompleteDiskCacheOnlyOnce() async {
        let threadID = "disk-cached-conversation"
        let newest = makeConversationChild(
            id: "message-2",
            threadID: threadID,
            receivedAt: "2026-05-29T10:00:00+05:30"
        )
        let row = makeMailboxRow(
            threadID: threadID,
            latestSourceRecordID: newest.messageID,
            receivedAt: newest.receivedAt,
            title: "Disk cached conversation",
            messageCount: 2,
            contentRevision: "revision-disk",
            children: [newest]
        )
        let completeThread = makeConversationThread(
            threadID: threadID,
            messages: [
                makeConversationMessage(
                    id: "message-1",
                    threadID: threadID,
                    receivedAt: "2026-05-29T09:00:00+05:30"
                ),
                makeConversationMessage(
                    id: "message-2",
                    threadID: threadID,
                    receivedAt: "2026-05-29T10:00:00+05:30"
                ),
            ],
            contentRevision: "revision-disk"
        )
        let localStore = BlockingThreadWriteLocalMailStore(blockOnThreadWriteCall: .max)
        localStore.writeThread(
            completeThread,
            userID: DemoAppFixtures.userID,
            threadID: threadID
        )
        let client = FixedMailboxAppClient(mailbox: makeConversationMailbox(row: row))
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            localMailStore: localStore,
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")
        await store.load()
        localStore.resetThreadReadCallCount()

        store.toggleExpansion(threadID: threadID)
        await waitForConversationState {
            store.flatRows.first?.isExpanded == true
        }

        XCTAssertEqual(localStore.recordedThreadReadCallCount, 1)
        XCTAssertEqual(client.threadCallCount, 0)
        XCTAssertEqual(store.flatRows.count, 3)
    }

    func testFailedLazyConversationExpansionStaysCollapsedAndCanRetry() async {
        let threadID = "retry-conversation"
        let newest = makeConversationChild(
            id: "message-2",
            threadID: threadID,
            receivedAt: "2026-05-29T10:00:00+05:30"
        )
        let row = makeMailboxRow(
            threadID: threadID,
            latestSourceRecordID: newest.messageID,
            receivedAt: newest.receivedAt,
            title: "Retry conversation",
            messageCount: 2,
            children: [newest]
        )
        let completeThread = makeConversationThread(
            threadID: threadID,
            messages: [
                makeConversationMessage(
                    id: "message-1",
                    threadID: threadID,
                    receivedAt: "2026-05-29T09:00:00+05:30"
                ),
                makeConversationMessage(
                    id: "message-2",
                    threadID: threadID,
                    receivedAt: "2026-05-29T10:00:00+05:30"
                ),
            ]
        )
        let client = FixedMailboxAppClient(
            mailbox: makeConversationMailbox(row: row),
            threadResponses: [completeThread],
            threadFailuresBeforeSuccess: 1
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral())
        )
        store.setSessionToken("live-session-token")
        await store.load()

        store.toggleExpansion(threadID: threadID)
        await waitForConversationState {
            client.threadCallCount == 1
                && !store.pendingConversationExpansionThreadIDs.contains(threadID)
        }
        XCTAssertEqual(store.flatRows.first?.isExpandable, true)
        XCTAssertEqual(store.flatRows.first?.isExpanded, false)
        XCTAssertEqual(store.flatRows.map(\.id), [threadID])

        store.toggleExpansion(threadID: threadID)
        await waitForConversationState {
            store.flatRows.first?.isExpanded == true
        }
        XCTAssertEqual(client.threadCallCount, 2)
        XCTAssertEqual(store.flatRows.count, 3)
    }

    func testCollapsingPendingConversationPreventsLateFetchFromReopeningIt() async {
        let threadID = "cancelled-conversation"
        let newest = makeConversationChild(
            id: "message-2",
            threadID: threadID,
            receivedAt: "2026-05-29T10:00:00+05:30"
        )
        let row = makeMailboxRow(
            threadID: threadID,
            latestSourceRecordID: newest.messageID,
            receivedAt: newest.receivedAt,
            title: "Cancelled conversation",
            messageCount: 2,
            children: [newest]
        )
        let completeThread = makeConversationThread(
            threadID: threadID,
            messages: [
                makeConversationMessage(
                    id: "message-1",
                    threadID: threadID,
                    receivedAt: "2026-05-29T09:00:00+05:30"
                ),
                makeConversationMessage(
                    id: "message-2",
                    threadID: threadID,
                    receivedAt: "2026-05-29T10:00:00+05:30"
                ),
            ]
        )
        let requestGate = ReaderActionRequestGate()
        let client = FixedMailboxAppClient(
            mailbox: makeConversationMailbox(row: row),
            threadResponses: [completeThread],
            threadRequestGate: requestGate
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral())
        )
        store.setSessionToken("live-session-token")
        await store.load()

        store.toggleExpansion(threadID: threadID)
        await requestGate.waitUntilRequestStarts()
        XCTAssertTrue(store.pendingConversationExpansionThreadIDs.contains(threadID))

        store.toggleExpansion(threadID: threadID)
        XCTAssertFalse(store.pendingConversationExpansionThreadIDs.contains(threadID))
        await requestGate.releaseRequest()
        await waitForConversationState {
            store.openedThreads[threadID] != nil
        }

        XCTAssertEqual(store.flatRows.map(\.id), [threadID])
        XCTAssertEqual(store.flatRows.first?.isExpanded, false)
        XCTAssertEqual(store.flatRows.first?.isExpandable, true)
    }

    func testSingleMessageRowNeverShowsConversationDisclosure() async {
        let threadID = "single-message"
        let child = makeConversationChild(
            id: "message-1",
            threadID: threadID,
            receivedAt: "2026-05-29T10:00:00+05:30"
        )
        let row = makeMailboxRow(
            threadID: threadID,
            latestSourceRecordID: child.messageID,
            receivedAt: child.receivedAt,
            title: "Single message",
            messageCount: 1,
            lifecycleSourceIDs: ["message-1", "legacy-update"],
            children: [child]
        )
        let store = InboxStore(
            client: FixedMailboxAppClient(mailbox: makeConversationMailbox(row: row)),
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral())
        )
        store.setSessionToken("live-session-token")

        await store.load()

        XCTAssertEqual(store.flatRows.first?.isGrouped, true)
        XCTAssertEqual(store.flatRows.first?.messageCount, 1)
        XCTAssertEqual(store.flatRows.first?.isExpandable, false)
    }

    func testInboxConversationExpansionKeepsCanonicalSentReply() async {
        let threadID = "cross-label-conversation"
        let inboxChild = makeConversationChild(
            id: "message-inbox",
            threadID: threadID,
            receivedAt: "2026-05-29T09:00:00+05:30",
            labelIDs: ["INBOX"]
        )
        let sentChild = makeConversationChild(
            id: "message-sent",
            threadID: threadID,
            receivedAt: "2026-05-29T10:00:00+05:30",
            sender: "Me <me@example.com>",
            labelIDs: ["SENT"]
        )
        let row = makeMailboxRow(
            threadID: threadID,
            latestSourceRecordID: sentChild.messageID,
            receivedAt: sentChild.receivedAt,
            title: "Inbox conversation with reply",
            messageCount: 2,
            children: [inboxChild, sentChild],
            labelIDs: ["INBOX", "SENT"],
            labels: ["INBOX", "SENT"]
        )
        let client = FixedMailboxAppClient(mailbox: makeConversationMailbox(row: row))
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral())
        )
        store.setSessionToken("live-session-token")
        await store.load()

        store.toggleExpansion(threadID: threadID)

        XCTAssertEqual(
            store.flatRows.map(\.id),
            [
                threadID,
                "\(threadID)::message::message-inbox",
                "\(threadID)::message::message-sent",
            ]
        )
        XCTAssertEqual(store.flatRows.last?.sender, "Me")
        XCTAssertEqual(client.threadCallCount, 0)
    }

    private func waitForConversationState(
        file: StaticString = #filePath,
        line: UInt = #line,
        _ condition: @escaping @MainActor () -> Bool
    ) async {
        for _ in 0..<500 {
            if condition() {
                return
            }
            try? await Task.sleep(nanoseconds: 2_000_000)
        }
        XCTFail("Timed out waiting for conversation state", file: file, line: line)
    }

    func testRowVisualStateMapping() async {
        let store = InboxStore(client: DemoAppClient(), sessionCache: AppSessionCache(defaults: .ephemeral()), threadCache: ThreadCache(defaults: .ephemeral()))

        await store.load()
        store.select(threadID: "demo-rbi-today")

        let rows = Dictionary(uniqueKeysWithValues: store.flatRows.map { ($0.threadID, $0) })
        XCTAssertEqual(rows["demo-google-today"]?.visualTone(), .unread)
        XCTAssertEqual(rows["demo-github-today"]?.visualTone(), .read)
        XCTAssertEqual(rows["demo-rbi-today"]?.visualTone(isSelected: true), .selected)
        XCTAssertEqual(rows["demo-apple-today"]?.isGrouped, true)
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

    func testInitialDemoLoadShowsRowsAndSelection() async {
        let store = InboxStore(client: DemoAppClient(), sessionCache: AppSessionCache(defaults: .ephemeral()), threadCache: ThreadCache(defaults: .ephemeral()))

        await store.load()

        XCTAssertFalse(store.flatRows.isEmpty)
        XCTAssertEqual(store.selectedThreadID, "demo-google-today")
    }

    func testUnchangedRefreshDoesNotPublishVisibleState() async {
        let store = InboxStore(
            client: DemoAppClient(),
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )
        await store.load()

        var changeCount = 0
        let observation = store.objectWillChange.sink {
            changeCount += 1
        }

        await store.refresh()

        XCTAssertEqual(changeCount, 0)
        observation.cancel()
    }

    func testClearSelectionClearsSelectedAndActiveIdentifiers() async {
        let store = InboxStore(client: DemoAppClient(), sessionCache: AppSessionCache(defaults: .ephemeral()), threadCache: ThreadCache(defaults: .ephemeral()))

        await store.load()
        store.select(threadID: "demo-rbi-today", focusedMessageID: "demo-rbi-message", prefetch: false)
        store.clearSelection()

        XCTAssertNil(store.selectedThreadID)
        XCTAssertNil(store.selectedMessageID)
        XCTAssertNil(store.activeThreadID)
        XCTAssertNil(store.activeMessageID)
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
        let threadID = "demo-github-today"
        let cachedThread = DemoAppFixtures.threads[threadID]!
        localStore.writeThread(cachedThread, userID: userID, threadID: threadID)
        let store = InboxStore(
            client: FailingThreadAppClient(),
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            localMailStore: localStore
        )

        await store.load()
        let task = store.openReader(threadID: threadID)
        await task.value

        XCTAssertEqual(store.readerThreadID, threadID)
        XCTAssertEqual(store.readerThread, cachedThread)
        XCTAssertNil(store.readerError)
        XCTAssertNil(store.threadErrors[threadID])
    }

    func testFailedRefreshKeepsCachedInboxVisible() async {
        let defaults = UserDefaults.ephemeral()
        let cache = AppSessionCache(defaults: defaults)
        cache.write(DemoAppFixtures.appSession)
        let store = InboxStore(client: FailingAppClient(), sessionCache: cache, threadCache: ThreadCache(defaults: defaults))
        store.setSessionToken("live-session-token")

        await store.load()

        XCTAssertEqual(store.phase, .loaded)
        XCTAssertEqual(store.session?.mailbox.totalThreads, DemoAppFixtures.appSession.mailbox.totalThreads)
        XCTAssertEqual(store.refreshFailed, true)
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
        XCTAssertFalse(store.hasSessionToken)
    }

    func testTransientRefreshFailureRetainsSessionTokenForRetry() async {
        let defaults = UserDefaults.ephemeral()
        let client = FailingAppClient(statusCode: 503)
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: defaults),
            threadCache: ThreadCache(defaults: defaults)
        )
        store.setSessionToken("retryable-session-token")

        await store.load()

        guard case .failed = store.phase else {
            return XCTFail("Expected a transient load failure without cached state")
        }
        XCTAssertEqual(client.sessionToken, "retryable-session-token")
        XCTAssertTrue(store.hasSessionToken)
    }

    func testUnauthorizedRefreshPreservesPendingOfflineActionsForReauthentication() async {
        let defaults = UserDefaults.ephemeral()
        let cache = AppSessionCache(defaults: defaults)
        let localStore = MemoryLocalMailStore()
        let session = DemoAppFixtures.appSession
        cache.write(session)
        localStore.writeSession(session)
        localStore.writeMailbox(session.mailbox, userID: session.user.id, label: .inbox)
        let cachedThread = DemoAppFixtures.threads["demo-google-today"]!
        localStore.writeThread(cachedThread, userID: session.user.id, threadID: "demo-google-today")
        let pendingAction = LocalPendingThreadAction(
            clientActionID: "pending-after-auth-expiry",
            userID: session.user.id,
            mailboxThreadID: "demo-google-today",
            targetMessageID: nil,
            action: .archive,
            createdAt: "2026-07-14T02:45:00.000Z",
            error: "offline"
        )
        localStore.writePendingThreadAction(pendingAction)
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
        XCTAssertEqual(localStore.pendingThreadActions(), [pendingAction])
        XCTAssertEqual(localStore.readMailbox(userID: session.user.id, label: .inbox), session.mailbox)
        XCTAssertEqual(
            localStore.readThread(userID: session.user.id, threadID: "demo-google-today"),
            cachedThread
        )

        // The app root may clear an already-expired token while returning to sign-in.
        // That idempotent cleanup must not discard the action preserved for reauth.
        store.setSessionToken(nil)
        XCTAssertEqual(localStore.pendingThreadActions(), [pendingAction])
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

    func testArchiveTrustsEndpointEligibilityForMixedLabelThread() async {
        let mixedRow = makeMailboxRow(
            threadID: "mixed-thread",
            latestSourceRecordID: "mixed-message",
            receivedAt: "2026-05-29T09:30:00+05:30",
            title: "Mixed archive thread",
            labelIDs: ["INBOX", "SENT"],
            labels: ["INBOX", "SENT"]
        )
        let archiveMailbox = MailboxResponse(
            label: .archive,
            totalThreads: 1,
            loadedThreads: 1,
            sections: [GmailThreadSection(id: "today", title: "Today", rows: [mixedRow])],
            fullImportRunning: false,
            fullImportCompleted: true
        )
        let store = InboxStore(
            client: FixedMailboxAppClient(mailbox: archiveMailbox),
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral())
        )
        store.setSessionToken("live-session-token")

        await store.load()
        await store.setMailboxLabel(.archive)

        XCTAssertEqual(store.flatRows.map(\.threadID), ["mixed-thread"])
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
        async let first = store.prefetchThread(threadID: "demo-special", force: true, silent: false)
        async let second = store.prefetchThread(threadID: "demo-special", force: true, silent: false)
        let networkResults = await (first, second)

        XCTAssertEqual(client.threadCallCounts["demo-special"], 1)
        XCTAssertTrue(networkResults.0)
        XCTAssertTrue(networkResults.1)
    }

    func testThreadPrefetchLoadsEveryPageForLongConversation() async {
        let client = SlowThreadAppClient()
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )

        await store.load()
        await store.prefetchThread(threadID: "paged-thread", force: true, silent: false)

        XCTAssertEqual(client.threadCallCounts["paged-thread"], 2)
        XCTAssertEqual(store.openedThreads["paged-thread"]?.messages.map(\.id), ["paged-0", "paged-1", "paged-2"])
        XCTAssertEqual(store.openedThreads["paged-thread"]?.hasMore, false)
    }

    func testThreadPrefetchAggregatesDuplicatePagesInLinearOrder() async {
        let client = SlowThreadAppClient()
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )

        await store.load()
        await store.prefetchThread(threadID: "duplicate-paged-thread", force: true, silent: false)

        XCTAssertEqual(client.threadCallCounts["duplicate-paged-thread"], 3)
        XCTAssertEqual(
            store.openedThreads["duplicate-paged-thread"]?.messages.map(\.id),
            ["duplicate-0", "duplicate-1", "duplicate-2", "duplicate-3"]
        )
        XCTAssertEqual(store.openedThreads["duplicate-paged-thread"]?.hasMore, false)
    }

    func testLateThreadFetchCannotRestoreEmailDataAfterSessionIsCleared() async {
        let client = SlowThreadAppClient()
        client.ignoresThreadCancellation = true
        let localStore = MemoryLocalMailStore()
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            localMailStore: localStore,
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")
        await store.load()

        let fetch = Task {
            await store.prefetchThread(threadID: "demo-google-today", force: true, silent: true)
        }
        while client.threadCallCounts["demo-google-today"] == nil {
            await Task.yield()
        }

        store.setSessionToken(nil)
        _ = await fetch.value

        XCTAssertTrue(store.openedThreads.isEmpty)
        XCTAssertNil(
            localStore.readThread(
                userID: DemoAppFixtures.userID,
                threadID: "demo-google-today"
            )
        )
    }

    func testSessionTokenClearKeepsMainActorResponsiveWhileLocalPurgeBlocks() async {
        let purgeStarted = expectation(description: "local purge started")
        let purgeFinished = expectation(description: "local purge finished")
        let localStore = BlockingClearAllLocalMailStore(
            purgeStarted: purgeStarted,
            purgeFinished: purgeFinished
        )
        let store = InboxStore(
            client: FailingAppClient(),
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            localMailStore: localStore
        )
        store.setSessionToken("live-session-token")

        // Keep a failed implementation from deadlocking the test process. The
        // normal path releases immediately after proving the MainActor moved.
        DispatchQueue.global(qos: .userInitiated).asyncAfter(deadline: .now() + 1) {
            localStore.releasePurge()
        }

        let startedAt = Date()
        store.setSessionToken(nil)
        XCTAssertLessThan(Date().timeIntervalSince(startedAt), 0.1)
        await fulfillment(of: [purgeStarted], timeout: 1)

        let mainActorHeartbeat = expectation(description: "main actor remained responsive")
        Task { @MainActor in
            mainActorHeartbeat.fulfill()
        }
        await fulfillment(of: [mainActorHeartbeat], timeout: 0.05)
        XCTAssertFalse(localStore.purgeRanOnMainThread)

        localStore.releasePurge()
        await fulfillment(of: [purgeFinished], timeout: 1)
    }

    func testFastResignInWaitsForOlderLocalPurgeBeforeReadingDiskCache() async {
        let purgeStarted = expectation(description: "older local purge started")
        let purgeFinished = expectation(description: "older local purge finished")
        let localStore = BlockingClearAllLocalMailStore(
            purgeStarted: purgeStarted,
            purgeFinished: purgeFinished
        )
        localStore.writeSession(DemoAppFixtures.appSession)
        let store = InboxStore(
            client: FailingAppClient(),
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            localMailStore: localStore
        )
        store.setSessionToken("old-session-token")
        store.setSessionToken(nil)
        await fulfillment(of: [purgeStarted], timeout: 1)

        store.setSessionToken("new-session-token")
        let restore = Task { @MainActor in
            await store.restoreLocalCache()
        }
        try? await Task.sleep(nanoseconds: 30_000_000)
        XCTAssertEqual(localStore.readSessionCallCount, 0)

        localStore.releasePurge()
        await fulfillment(of: [purgeFinished], timeout: 1)
        let restored = await restore.value
        XCTAssertFalse(restored)
        XCTAssertEqual(localStore.readSessionCallCount, 1)
        XCTAssertNil(localStore.readSession())
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

    func testTodoMapperSplitsDashboardFeedSections() throws {
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
        let parser = ISO8601DateFormatter()
        parser.formatOptions = [.withInternetDateTime, .withColonSeparatorInTimeZone]
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = .current
        let expectedAgendaTimes = try [
            "2026-05-16T10:00:00+05:30",
            "2026-05-16T12:00:00+05:30",
            "2026-05-16T13:30:00+05:30",
            "2026-05-16T14:45:00+05:30",
            "2026-05-16T15:00:00+05:30",
        ].map { value in
            let date = try XCTUnwrap(parser.date(from: value))
            let components = calendar.dateComponents([.hour, .minute], from: date)
            return String(
                format: "%02d:%02d",
                try XCTUnwrap(components.hour),
                try XCTUnwrap(components.minute)
            )
        }
        XCTAssertEqual(snapshot.agenda.map(\.time), expectedAgendaTimes)
    }

    func testTodoRowsUseInboxDerivedMetadata() async {
        let store = InboxStore(client: DemoAppClient(), sessionCache: AppSessionCache(defaults: .ephemeral()), threadCache: ThreadCache(defaults: .ephemeral()))

        await store.load()
        let snapshot = TodoHomeMapper.snapshot(from: DemoAppFixtures.appSession, inboxRows: store.flatRows, now: Date(timeIntervalSince1970: 0))

        let sourceInboxRow = store.flatRows.first { $0.threadID == "demo-apple-today" }
        let nowRow = snapshot.now.rows.first
        XCTAssertNotNil(sourceInboxRow)
        XCTAssertEqual(nowRow?.sender, "Apple Developer")
        XCTAssertEqual(nowRow?.title, "App Review needs one more screenshot for macOS")
        XCTAssertEqual(nowRow?.timeLabel, sourceInboxRow?.timeLabel)
        XCTAssertEqual(nowRow?.detailText, "Apple Developer needs one more screenshot before review can continue. Confirm the slot or move it out of today's work.")
        XCTAssertEqual(nowRow?.actionLabel, "Open source")
        XCTAssertEqual(nowRow?.gmailThreadID, "demo-apple-today")

        let manualRow = snapshot.laterToday.rows.first { $0.entityID == "manual-task:demo-manual-seed" }
        XCTAssertEqual(manualRow?.sender, "Manual")
        XCTAssertEqual(manualRow?.title, "New to-do")
        XCTAssertEqual(manualRow?.timeLabel, "")
        XCTAssertNil(manualRow?.gmailThreadID)
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
        XCTAssertNil(snapshot.dashboardBuildStatus)
    }

    func testTodoSnapshotDoesNotExposeAIStatusWhenGroupsArePending() {
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

        XCTAssertNil(snapshot.aiBuildStatus)
        XCTAssertEqual(snapshot.dashboardBuildStatus, "Syncing Gmail")
    }

    func testTodoSnapshotShowsDashboardBuildStateDuringFirstRunImport() {
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

        XCTAssertEqual(snapshot.dashboardBuildStatus, "Syncing Gmail")
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

    func testManualSyncTransitionsExpiredHTTPSessionToReauthentication() async {
        let client = ManualSyncAppClient()
        let localStore = MemoryLocalMailStore()
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            localMailStore: localStore
        )
        store.setSessionToken("expired-session-token")
        await store.load()
        client.syncNowFailureStatus = 401

        await store.syncNow()

        XCTAssertFalse(store.hasSessionToken)
        XCTAssertNil(store.session)
        XCTAssertNil(localStore.readSession())
        XCTAssertEqual(store.phase, .failed("Sign in with Google to load your mailbox."))
        XCTAssertFalse(store.manualSyncInProgress)
    }

    func testManualSyncNotConnectedResponseTransitionsToReauthenticationAndPreservesPendingActions() async {
        let client = ManualSyncAppClient()
        let localStore = MemoryLocalMailStore()
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            localMailStore: localStore
        )
        store.setSessionToken("revoked-google-token-session")
        await store.load()
        let pendingAction = LocalPendingThreadAction(
            clientActionID: "pending-during-google-reauth",
            userID: DemoAppFixtures.userID,
            mailboxThreadID: "demo-google-today",
            targetMessageID: nil,
            action: .archive,
            createdAt: "2026-07-23T00:00:00.000Z",
            error: "offline"
        )
        localStore.writePendingThreadAction(pendingAction)
        client.syncNowStatus = "not_connected"
        client.syncNowConnected = false

        await store.syncNow()

        XCTAssertFalse(store.hasSessionToken)
        XCTAssertNil(store.session)
        XCTAssertNil(localStore.readSession())
        XCTAssertEqual(localStore.pendingThreadActions(), [pendingAction])
        XCTAssertEqual(store.phase, .failed("Sign in with Google to load your mailbox."))
    }

    func testSyncStatePollingTransitionsDisconnectedGoogleAccountToReauthentication() async {
        let client = ManualSyncAppClient()
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral())
        )
        store.setSessionToken("revoked-google-token-session")
        await store.load()
        client.syncStateConnected = false

        await store.triggerMailboxSyncIfNeeded()

        XCTAssertFalse(store.hasSessionToken)
        XCTAssertNil(store.session)
        XCTAssertEqual(store.phase, .failed("Sign in with Google to load your mailbox."))
    }

    func testSyncStatePollingTransitionsForbiddenSessionToReauthentication() async {
        let client = ManualSyncAppClient()
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral())
        )
        store.setSessionToken("forbidden-session-token")
        await store.load()
        client.syncStateFailureStatus = 403

        await store.triggerMailboxSyncIfNeeded()

        XCTAssertFalse(store.hasSessionToken)
        XCTAssertNil(store.session)
        XCTAssertEqual(store.phase, .failed("Sign in with Google to load your mailbox."))
    }

    func testSignOutIsBlockedWhileOfflineActionsRemainPending() async {
        let client = ManualSyncAppClient()
        let localStore = MemoryLocalMailStore()
        let userID = DemoAppFixtures.appSession.user.id
        localStore.writeSession(DemoAppFixtures.appSession)
        localStore.writePendingThreadAction(
            LocalPendingThreadAction(
                clientActionID: "pending-signout-action",
                userID: userID,
                mailboxThreadID: "demo-google-today",
                targetMessageID: nil,
                action: .archive,
                createdAt: "2026-07-13T18:00:00.000Z",
                error: "offline"
            )
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            localMailStore: localStore
        )
        store.setSessionToken("live-session-token")

        await store.load()
        XCTAssertEqual(store.pendingLocalActionCount, 1)

        do {
            try await store.logoutRemoteSession()
            XCTFail("Sign out should not discard a pending offline action")
        } catch {
            XCTAssertTrue(error.localizedDescription.contains("1 email change is still waiting to sync"))
        }

        XCTAssertEqual(client.syncNowCallCount, 1)
        XCTAssertEqual(client.logoutCallCount, 0)
        XCTAssertEqual(localStore.pendingThreadActions().count, 1)
    }

    func testProgressiveInitialWindowEntersAfter100MetadataAnd25BodiesAndPersistsIt() async {
        let mailbox = makeProgressiveMailbox(
            metadataCount: 100,
            bodyReadyCount: 25,
            estimatedTotalCount: 640
        )
        let session = makeProgressiveSession(mailbox: mailbox)
        let localStore = MemoryLocalMailStore()
        let store = InboxStore(
            client: FixedMailboxAppClient(mailbox: mailbox, session: session),
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            localMailStore: localStore,
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")

        await store.load()

        XCTAssertEqual(store.flatRows.count, 100)
        XCTAssertEqual(store.setupProgress.initialMetadataCount, 100)
        XCTAssertEqual(store.setupProgress.initialBodyReadyCount, 25)
        XCTAssertTrue(store.setupProgress.initialTargetReady)
        XCTAssertTrue(store.mailboxPresentationReady)
        XCTAssertTrue(store.isReadyForMainInterface)
        XCTAssertEqual(store.setupProgress.progressFraction, 1)
        XCTAssertEqual(
            localStore.readMailbox(userID: session.user.id, label: .inbox)?.sections.flatMap(\.rows).count,
            100
        )
    }

    func testSetupDeadlineUsesInjectedTimeAndStopsAtExactly30Seconds() {
        let startedAt = Date(timeIntervalSinceReferenceDate: 10_000)
        let policy = MailboxSetupDeadlinePolicy(
            startedAt: startedAt,
            minimumDisplaySeconds: 0,
            maximumWaitSeconds: 30
        )

        XCTAssertEqual(
            policy.decision(
                now: startedAt.addingTimeInterval(29.999),
                initialWindowReady: false,
                committedBatchReady: false
            ),
            .wait
        )
        XCTAssertEqual(
            policy.decision(
                now: startedAt.addingTimeInterval(30),
                initialWindowReady: false,
                committedBatchReady: false
            ),
            .retry
        )
        XCTAssertEqual(
            policy.decision(
                now: startedAt.addingTimeInterval(30),
                initialWindowReady: false,
                committedBatchReady: true
            ),
            .enter
        )
        XCTAssertEqual(
            policy.decision(
                now: startedAt.addingTimeInterval(2),
                initialWindowReady: true,
                committedBatchReady: false
            ),
            .enter
        )
    }

    func testDeadlineFallbackRequiresOneCommitted25MetadataBatch() async {
        let verifiedMailbox = makeProgressiveMailbox(
            metadataCount: 25,
            bodyReadyCount: 25,
            estimatedTotalCount: 640
        )
        let verifiedSession = makeProgressiveSession(mailbox: verifiedMailbox)
        let verifiedLocalStore = MemoryLocalMailStore()
        let verifiedStore = InboxStore(
            client: FixedMailboxAppClient(mailbox: verifiedMailbox, session: verifiedSession),
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            localMailStore: verifiedLocalStore,
            automaticallyPrefetchThreads: false
        )
        verifiedStore.setSessionToken("live-session-token")

        await verifiedStore.load()

        XCTAssertFalse(verifiedStore.isReadyForMainInterface)
        XCTAssertTrue(verifiedStore.canEnterWithBuildingDashboard)
        XCTAssertTrue(verifiedStore.mailboxPresentationReady)
        XCTAssertEqual(
            verifiedLocalStore.readMailbox(userID: verifiedSession.user.id, label: .inbox)?.sections.flatMap(\.rows).count,
            25
        )

        let unverifiedMailbox = makeProgressiveMailbox(
            metadataCount: 24,
            bodyReadyCount: 24,
            estimatedTotalCount: 640
        )
        let unverifiedSession = makeProgressiveSession(mailbox: unverifiedMailbox)
        let unverifiedLocalStore = MemoryLocalMailStore()
        let unverifiedStore = InboxStore(
            client: FixedMailboxAppClient(mailbox: unverifiedMailbox, session: unverifiedSession),
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            localMailStore: unverifiedLocalStore,
            automaticallyPrefetchThreads: false
        )
        unverifiedStore.setSessionToken("live-session-token")

        await unverifiedStore.load()

        XCTAssertFalse(unverifiedStore.mailboxPresentationReady)
        XCTAssertFalse(unverifiedStore.canEnterWithBuildingDashboard)
        XCTAssertNil(unverifiedLocalStore.readMailbox(userID: unverifiedSession.user.id, label: .inbox))
    }

    func testInitialWindowDoesNotEnterBeforeNewest25BodiesAreReady() async {
        let mailbox = makeProgressiveMailbox(
            metadataCount: 100,
            bodyReadyCount: 24,
            estimatedTotalCount: 640,
            initialWindowComplete: true
        )
        let session = makeProgressiveSession(mailbox: mailbox)
        let store = InboxStore(
            client: FixedMailboxAppClient(mailbox: mailbox, session: session),
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")

        await store.load()

        XCTAssertTrue(store.mailboxPresentationReady)
        XCTAssertTrue(store.canEnterWithBuildingDashboard)
        XCTAssertFalse(store.setupProgress.initialTargetReady)
        XCTAssertFalse(store.isReadyForMainInterface)
    }

    func testCommittedGlobalMetadataBatchAllowsSparseInboxFallback() async {
        let mailbox = makeProgressiveMailbox(
            metadataCount: 25,
            bodyReadyCount: 0,
            estimatedTotalCount: 640,
            visibleRowCount: 0
        )
        let session = makeProgressiveSession(mailbox: mailbox)
        let localStore = MemoryLocalMailStore()
        let store = InboxStore(
            client: FixedMailboxAppClient(mailbox: mailbox, session: session),
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            localMailStore: localStore,
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")

        await store.load()

        XCTAssertTrue(store.mailboxPresentationReady)
        XCTAssertTrue(store.canEnterWithBuildingDashboard)
        XCTAssertFalse(store.isReadyForMainInterface)
        XCTAssertTrue(store.flatRows.isEmpty)
        XCTAssertNotNil(localStore.readMailbox(userID: session.user.id, label: .inbox))
    }

    func testProgressiveInitialWindowCanExplicitlyConfirmAnEmptyMailbox() async {
        let mailbox = makeProgressiveMailbox(
            metadataCount: 0,
            bodyReadyCount: 0,
            estimatedTotalCount: 0,
            initialWindowComplete: true
        )
        let session = makeProgressiveSession(mailbox: mailbox)
        let store = InboxStore(
            client: FixedMailboxAppClient(mailbox: mailbox, session: session),
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")

        await store.load()

        XCTAssertTrue(store.setupProgress.confirmedEmpty)
        XCTAssertTrue(store.mailboxPresentationReady)
        XCTAssertTrue(store.isReadyForMainInterface)
        XCTAssertTrue(store.canEnterWithBuildingDashboard)
        XCTAssertTrue(store.flatRows.isEmpty)
    }

    func testLaterRefreshFailureKeepsVerifiedProgressiveRowsCacheAndSelection() async {
        let mailbox = makeProgressiveMailbox(
            metadataCount: 100,
            bodyReadyCount: 25,
            estimatedTotalCount: 640
        )
        let session = makeProgressiveSession(mailbox: mailbox)
        let localStore = MemoryLocalMailStore()
        let client = FixedMailboxAppClient(
            mailbox: mailbox,
            session: session,
            successfulMailboxCallsBeforeFailure: 1
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            localMailStore: localStore,
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")
        await store.load()
        store.select(threadID: "progressive-10", prefetch: false)

        await store.syncNow()

        XCTAssertTrue(store.refreshFailed)
        XCTAssertTrue(store.mailboxPresentationReady)
        XCTAssertEqual(store.flatRows.count, 100)
        XCTAssertEqual(store.selectedThreadID, "progressive-10")
        XCTAssertEqual(
            localStore.readMailbox(userID: session.user.id, label: .inbox)?.sections.flatMap(\.rows).count,
            100
        )
    }

    func testHistoryFooterUsesReliableEstimateWithoutSummingInitialAndHistoryCounts() async {
        let mailbox = MailboxResponse(
            label: .inbox,
            totalThreads: 1_200,
            loadedThreads: 300,
            sections: [
                GmailThreadSection(
                    id: "progressive",
                    title: "Recent",
                    rows: (0..<300).map { index in
                        makeMailboxRow(
                            threadID: "footer-\(index)",
                            latestSourceRecordID: "footer-message-\(index)",
                            receivedAt: "2026-07-25T12:00:00Z",
                            title: "Footer message \(index)",
                            bodyReady: index < 25,
                            contentRevision: "sha256:footer-\(index)"
                        )
                    }
                )
            ],
            fullImportRunning: true,
            fullImportCompleted: false,
            syncGeneration: "footer-generation-1",
            phase: "importing_history_metadata",
            initialTargetCount: 100,
            initialMetadataCount: 100,
            initialBodyTargetCount: 25,
            initialBodyReadyCount: 25,
            historyMetadataCount: 300,
            historyBodyReadyCount: 25,
            estimatedTotalCount: 1_200,
            initialWindowComplete: true,
            historyMetadataComplete: false,
            historyBodyComplete: false,
            lastProgressAt: "2026-07-25T12:00:00Z"
        )
        let session = makeProgressiveSession(mailbox: mailbox)
        let store = InboxStore(
            client: FixedMailboxAppClient(mailbox: mailbox, session: session),
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")

        await store.load()

        XCTAssertEqual(store.mailboxFooterProgressText, "Loading older mail… 300 of 1,200")
    }

    func testHistoryFooterWithoutEstimateReportsOnlyReadyConversationCount() async {
        let mailbox = makeProgressiveMailbox(
            metadataCount: 100,
            bodyReadyCount: 25,
            estimatedTotalCount: 640,
            reportsEstimatedTotal: false
        )
        let session = makeProgressiveSession(mailbox: mailbox)
        let store = InboxStore(
            client: FixedMailboxAppClient(mailbox: mailbox, session: session),
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")

        await store.load()

        XCTAssertEqual(store.mailboxFooterProgressText, "100 conversations ready")
    }

    func testOfflineGlobalMetadataCrawlsFetchOnlyReadyBodiesAndResumeTheirTails() async throws {
        let backend = GlobalOfflineMetadataAppClient()
        let localStore = MemoryLocalMailStore()
        let client = OfflineFirstAppClient(backend: backend, localMailStore: localStore)
        client.sessionToken = "live-session-token"

        _ = try await client.appSession()
        for _ in 0..<2_000 {
            if localStore.readThread(userID: DemoAppFixtures.userID, threadID: "global-ready-1") != nil,
               localStore.readThread(userID: DemoAppFixtures.userID, threadID: "global-ready-2") != nil,
               backend.mailboxCalls.count >= 4 {
                break
            }
            try? await Task.sleep(nanoseconds: 1_000_000)
        }

        XCTAssertEqual(
            backend.mailboxCalls.filter { $0.label == .all }.map(\.cursor),
            [nil, "all-cursor-2"]
        )
        XCTAssertEqual(backend.mailboxCalls.filter { $0.label == .spam }.map(\.cursor), [nil])
        XCTAssertEqual(backend.mailboxCalls.filter { $0.label == .trash }.map(\.cursor), [nil])
        XCTAssertNotNil(localStore.readThread(userID: DemoAppFixtures.userID, threadID: "global-ready-1"))
        XCTAssertNotNil(localStore.readThread(userID: DemoAppFixtures.userID, threadID: "global-ready-2"))
        XCTAssertNil(localStore.readThread(userID: DemoAppFixtures.userID, threadID: "global-cold-1"))
        XCTAssertNil(localStore.readThread(userID: DemoAppFixtures.userID, threadID: "global-cold-2"))
        XCTAssertFalse(backend.batchedThreadIDs.contains("inbox-cold"))
        XCTAssertFalse(backend.batchedThreadIDs.contains("global-cold-1"))
        XCTAssertFalse(backend.batchedThreadIDs.contains("global-cold-2"))

        let callsBeforeHydrationEvent = backend.mailboxCalls.count
        await client.observeHydratedThreads(
            [
                MailboxHydratedThreadState(
                    threadID: "global-cold-1",
                    bodyReady: true,
                    contentRevision: "sha256:global-cold-1",
                    initialWindowPosition: 30
                ),
                MailboxHydratedThreadState(
                    threadID: "global-cold-2",
                    bodyReady: true,
                    contentRevision: "sha256:global-cold-2",
                    initialWindowPosition: 3
                ),
            ],
            userID: DemoAppFixtures.userID
        )
        for _ in 0..<2_000 {
            if localStore.readThread(userID: DemoAppFixtures.userID, threadID: "global-cold-1") != nil,
               localStore.readThread(userID: DemoAppFixtures.userID, threadID: "global-cold-2") != nil {
                break
            }
            try? await Task.sleep(nanoseconds: 1_000_000)
        }

        XCTAssertEqual(backend.mailboxCalls.count, callsBeforeHydrationEvent)
        XCTAssertNotNil(localStore.readThread(userID: DemoAppFixtures.userID, threadID: "global-cold-1"))
        XCTAssertNotNil(localStore.readThread(userID: DemoAppFixtures.userID, threadID: "global-cold-2"))
        XCTAssertTrue(backend.batchedThreadIDs.contains("global-cold-1"))
        XCTAssertTrue(backend.batchedThreadIDs.contains("global-cold-2"))

        backend.advanceProgress()
        _ = try await client.mailboxSyncState()
        for _ in 0..<2_000 {
            if localStore.readThread(userID: DemoAppFixtures.userID, threadID: "global-new-tail") != nil,
               backend.mailboxCalls.count >= 7 {
                break
            }
            try? await Task.sleep(nanoseconds: 1_000_000)
        }

        XCTAssertEqual(
            backend.mailboxCalls.filter { $0.label == .all }.map(\.cursor),
            [nil, "all-cursor-2", "all-cursor-2"]
        )
        XCTAssertEqual(backend.mailboxCalls.filter { $0.label == .spam }.map(\.cursor), [nil, nil])
        XCTAssertEqual(backend.mailboxCalls.filter { $0.label == .trash }.map(\.cursor), [nil, nil])
        XCTAssertNotNil(localStore.readThread(userID: DemoAppFixtures.userID, threadID: "global-new-tail"))
    }

    func testOfflineBodyPriorityUsesServerGlobalInitialWindowPositionOnly() {
        XCTAssertNil(
            OfflineContentPriorityPolicy.priority(
                bodyReady: false,
                initialWindowPosition: 0
            )
        )
        XCTAssertEqual(
            OfflineContentPriorityPolicy.priority(bodyReady: true, initialWindowPosition: 0),
            90
        )
        XCTAssertEqual(
            OfflineContentPriorityPolicy.priority(bodyReady: true, initialWindowPosition: 24),
            90
        )
        XCTAssertEqual(
            OfflineContentPriorityPolicy.priority(bodyReady: true, initialWindowPosition: 25),
            10
        )
        XCTAssertEqual(
            OfflineContentPriorityPolicy.priority(
                bodyReady: false,
                initialWindowPosition: nil,
                selected: true
            ),
            100
        )
    }

    func testOversizedOfflineThreadFallsBackToBoundedPagesAndPersistsOnlyCompleteSnapshot() async throws {
        let userID = DemoAppFixtures.userID
        let threadID = "oversized-offline-thread"
        let finalPageGate = ReaderActionRequestGate()
        let backend = OversizedOfflineThreadAppClient(
            mode: .stable(totalMessages: 250),
            gatedOffset: 200,
            pageGate: finalPageGate
        )
        let localStore = MemoryLocalMailStore()
        let coordinator = OfflineContentSyncCoordinator(
            backend: backend,
            localMailStore: localStore,
            pendingResponsesBeforePaginatedFallback: 2,
            retryDelayOverride: .milliseconds(1)
        )

        await coordinator.observe(
            mailbox: makeOversizedOfflineMailbox(threadID: threadID),
            userID: userID
        )
        await finalPageGate.waitUntilRequestStarts()

        XCTAssertNil(
            localStore.readThread(userID: userID, threadID: threadID),
            "No prefix of a large conversation may be committed as an offline snapshot."
        )

        await finalPageGate.releaseRequest()
        for _ in 0..<2_000 {
            if localStore.readThread(userID: userID, threadID: threadID) != nil {
                break
            }
            try? await Task.sleep(nanoseconds: 1_000_000)
        }

        let cached = try XCTUnwrap(localStore.readThread(userID: userID, threadID: threadID))
        XCTAssertFalse(cached.hasMore)
        XCTAssertEqual(cached.messages.count, 250)
        XCTAssertEqual(Set(cached.messages.map(\.id)).count, 250)
        XCTAssertEqual(backend.batchCallCount, 2)
        XCTAssertEqual(
            backend.threadCalls,
            [
                OversizedThreadPageCall(limit: 100, offset: 0),
                OversizedThreadPageCall(limit: 100, offset: 100),
                OversizedThreadPageCall(limit: 100, offset: 200),
            ]
        )
    }

    func testOversizedOfflineThreadRejectsRepeatedPagesAndRevisionChanges() async {
        for mode in [
            OversizedOfflineThreadAppClient.Mode.repeatedPage,
            OversizedOfflineThreadAppClient.Mode.revisionChange,
        ] {
            let userID = DemoAppFixtures.userID
            let threadID = "unstable-oversized-thread"
            let backend = OversizedOfflineThreadAppClient(mode: mode)
            let localStore = MemoryLocalMailStore()
            let coordinator = OfflineContentSyncCoordinator(
                backend: backend,
                localMailStore: localStore,
                pendingResponsesBeforePaginatedFallback: 1,
                retryDelayOverride: .seconds(60)
            )

            await coordinator.observe(
                mailbox: makeOversizedOfflineMailbox(threadID: threadID),
                userID: userID
            )
            for _ in 0..<2_000 {
                if backend.threadCalls.count >= 2 {
                    break
                }
                try? await Task.sleep(nanoseconds: 1_000_000)
            }
            try? await Task.sleep(nanoseconds: 20_000_000)

            XCTAssertNil(
                localStore.readThread(userID: userID, threadID: threadID),
                "An unstable page sequence must never replace the last complete offline snapshot."
            )
            await coordinator.reset(userID: userID)
        }
    }

    func testLogoutResetCancelsOversizedThreadFallbackBeforeLatePageCanWrite() async {
        let userID = DemoAppFixtures.userID
        let threadID = "logout-oversized-thread"
        let pageGate = ReaderActionRequestGate()
        let backend = OversizedOfflineThreadAppClient(
            mode: .stable(totalMessages: 250),
            gatedOffset: 100,
            pageGate: pageGate
        )
        let localStore = MemoryLocalMailStore()
        let coordinator = OfflineContentSyncCoordinator(
            backend: backend,
            localMailStore: localStore,
            pendingResponsesBeforePaginatedFallback: 1,
            retryDelayOverride: .milliseconds(1)
        )

        await coordinator.observe(
            mailbox: makeOversizedOfflineMailbox(threadID: threadID),
            userID: userID
        )
        await pageGate.waitUntilRequestStarts()

        await coordinator.reset(userID: userID)
        await pageGate.releaseRequest()
        try? await Task.sleep(nanoseconds: 100_000_000)

        XCTAssertNil(localStore.readThread(userID: userID, threadID: threadID))
    }

    func testExplicitLogoutFencesLateOfflineBodyBatchBeforeEncryptedPurge() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("ElectronicMailOfflinePurgeRace-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }

        let userID = "purge-race-\(UUID().uuidString)"
        let keyStore = AccountContentKeyStore.shared
        keyStore.removeKey(userID: userID)
        let localStore = try XCTUnwrap(
            SQLiteLocalMailStore(databaseURL: directory.appendingPathComponent("LocalMail.sqlite3"))
        )
        let batchGate = ReaderActionRequestGate()
        let backend = GlobalOfflineMetadataAppClient(userID: userID, batchGate: batchGate)
        let client = OfflineFirstAppClient(backend: backend, localMailStore: localStore)
        client.sessionToken = "live-session-token"

        _ = try await client.appSession()
        await batchGate.waitUntilRequestStarts()

        try await client.logout()
        XCTAssertNil(localStore.readSession())
        XCTAssertNil(keyStore.loadKey(userID: userID))

        await batchGate.releaseRequest()
        try? await Task.sleep(nanoseconds: 100_000_000)

        XCTAssertNil(localStore.readThread(userID: userID, threadID: "global-ready-1"))
        XCTAssertNil(localStore.readThread(userID: userID, threadID: "global-ready-2"))
        XCTAssertNil(keyStore.loadKey(userID: userID))
    }

    func testAccountPurgeDrainsActiveInboxThreadWriterBeforeDeletingRowsAndKey() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("ElectronicMailThreadWriterPurgeRace-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }

        let userID = "thread-writer-purge-\(UUID().uuidString)"
        let threadID = "demo-google-today"
        let keyStore = AccountContentKeyStore.shared
        keyStore.removeKey(userID: userID)
        defer { keyStore.removeKey(userID: userID) }
        let sqliteStore = try XCTUnwrap(
            SQLiteLocalMailStore(databaseURL: directory.appendingPathComponent("LocalMail.sqlite3"))
        )
        let blockingStore = BlockingThreadWriteLocalMailStore(
            blockOnThreadWriteCall: 1,
            base: sqliteStore
        )
        defer { blockingStore.releaseBlockedThreadWrite() }
        let client = PurgingAccountActionAppClient(
            localMailStore: blockingStore,
            userID: userID
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            localMailStore: blockingStore,
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")
        await store.load()

        let prefetchTask = Task {
            await store.prefetchThread(threadID: threadID, force: true, silent: false)
        }
        for _ in 0..<2_000 where !blockingStore.hasBlockedThreadWrite {
            try? await Task.sleep(nanoseconds: 1_000_000)
        }
        XCTAssertTrue(blockingStore.hasBlockedThreadWrite)

        let purgeTask = Task {
            try await store.disconnectGoogleAndDeleteData()
        }
        for _ in 0..<100 {
            await Task.yield()
        }
        XCTAssertEqual(
            client.purgeCallCount,
            0,
            "The destructive client purge must wait for the active writer drain barrier."
        )

        blockingStore.releaseBlockedThreadWrite()
        _ = await prefetchTask.value
        try await purgeTask.value

        XCTAssertEqual(client.purgeCallCount, 1)
        XCTAssertNil(sqliteStore.readSession())
        XCTAssertNil(sqliteStore.readThread(userID: userID, threadID: threadID))
        XCTAssertNil(keyStore.loadKey(userID: userID))
    }

    func testAccountPurgeDrainsActiveInboxMailboxWriterBeforeDeletingRows() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("ElectronicMailMailboxWriterPurgeRace-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }

        let userID = "mailbox-writer-purge-\(UUID().uuidString)"
        let sqliteStore = try XCTUnwrap(
            SQLiteLocalMailStore(databaseURL: directory.appendingPathComponent("LocalMail.sqlite3"))
        )
        let blockingStore = BlockingMailboxWriteLocalMailStore(base: sqliteStore)
        defer { blockingStore.releaseBlockedMailboxWrite() }
        let client = PurgingAccountActionAppClient(
            localMailStore: blockingStore,
            userID: userID
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            localMailStore: blockingStore,
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")

        let loadTask = Task {
            await store.load()
        }
        for _ in 0..<2_000 where !blockingStore.hasBlockedMailboxWrite {
            try? await Task.sleep(nanoseconds: 1_000_000)
        }
        XCTAssertTrue(blockingStore.hasBlockedMailboxWrite)

        let purgeTask = Task {
            try await store.disconnectGoogleAndDeleteData()
        }
        for _ in 0..<100 {
            await Task.yield()
        }
        XCTAssertEqual(
            client.purgeCallCount,
            0,
            "The destructive client purge must not overtake an active mailbox commit."
        )

        blockingStore.releaseBlockedMailboxWrite()
        await loadTask.value
        try await purgeTask.value

        XCTAssertEqual(client.purgeCallCount, 1)
        XCTAssertNil(sqliteStore.readSession())
        XCTAssertNil(sqliteStore.readMailbox(userID: userID, label: .inbox))
    }

    func testMailboxAutomaticallyAccumulatesEveryPageBeforePresentation() async {
        let client = PaginatedMailboxAppClient()
        let store = InboxStore(client: client, sessionCache: AppSessionCache(defaults: .ephemeral()), threadCache: ThreadCache(defaults: .ephemeral()))
        store.setSessionToken("live-session-token")

        await store.load()

        XCTAssertEqual(store.flatRows.map(\.threadID), ["demo-google-today", "demo-github-today"])
        XCTAssertTrue(store.mailboxPresentationReady)
        XCTAssertFalse(store.mailboxPageLoading)
        XCTAssertEqual(client.mailboxCursors, [nil, "cursor-2"])
    }

    func testCompletedMailboxLoadsAll357ThreadsAcrossFourPagesAndCachesOnlyFinalSnapshot() async {
        let client = BulkPaginatedMailboxAppClient()
        let localStore = MemoryLocalMailStore()
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            localMailStore: localStore,
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")

        await store.load()

        XCTAssertTrue(store.mailboxPresentationReady)
        XCTAssertEqual(store.flatRows.count, 357)
        XCTAssertEqual(store.flatRows.first?.threadID, "bulk-0")
        XCTAssertEqual(store.flatRows.last?.threadID, "bulk-356")
        XCTAssertEqual(client.mailboxCursors, [nil, "bulk-cursor-100", "bulk-cursor-200", "bulk-cursor-300"])

        let cached = localStore.readMailbox(userID: DemoAppFixtures.userID, label: .inbox)
        XCTAssertEqual(cached?.sections.flatMap(\.rows).count, 357)
        XCTAssertEqual(cached?.loadedThreads, 357)
        XCTAssertNil(cached?.nextCursor)
        XCTAssertEqual(cached?.fullImportCompleted, true)
    }

    func testOfflineFirstSQLiteCacheStaysAt357AcrossSessionThrottleAndFinalTransportPage() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("ElectronicMailAtomicMailboxTests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }

        let localStore = try XCTUnwrap(
            SQLiteLocalMailStore(databaseURL: directory.appendingPathComponent("LocalMail.sqlite3"))
        )
        let backend = BulkPaginatedMailboxAppClient()
        let client = OfflineFirstAppClient(backend: backend, localMailStore: localStore)
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            localMailStore: localStore,
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")

        await store.load()

        XCTAssertEqual(
            localStore.readMailbox(userID: DemoAppFixtures.userID, label: .inbox)?.sections.flatMap(\.rows).count,
            357
        )
        XCTAssertEqual(backend.mailboxCursors, [nil, "bulk-cursor-100", "bulk-cursor-200", "bulk-cursor-300"])

        // This refresh is inside the mailbox throttle window. Its embedded
        // 100-row first page must not replace the complete snapshot in either
        // cache table; session and per-label mailbox are committed together.
        await store.refresh()

        XCTAssertEqual(
            localStore.readSession()?.mailbox.sections.flatMap(\.rows).count,
            357
        )
        XCTAssertEqual(
            localStore.readMailbox(userID: DemoAppFixtures.userID, label: .inbox)?.sections.flatMap(\.rows).count,
            357
        )
        XCTAssertEqual(backend.mailboxCursors, [nil, "bulk-cursor-100", "bulk-cursor-200", "bulk-cursor-300"])

        let finalTransportPage = try await client.mailbox(
            label: .inbox,
            limit: 100,
            cursor: "bulk-cursor-300"
        )

        XCTAssertEqual(finalTransportPage.sections.flatMap(\.rows).count, 57)
        XCTAssertEqual(
            localStore.readMailbox(userID: DemoAppFixtures.userID, label: .inbox)?.sections.flatMap(\.rows).count,
            357
        )
    }

    func testOfflineFirstSQLitePageFailureCannotDegradeComplete357Cache() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("ElectronicMailFailedMailboxTests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }

        let localStore = try XCTUnwrap(
            SQLiteLocalMailStore(databaseURL: directory.appendingPathComponent("LocalMail.sqlite3"))
        )
        do {
            let primingClient = OfflineFirstAppClient(
                backend: BulkPaginatedMailboxAppClient(),
                localMailStore: localStore
            )
            let primingStore = InboxStore(
                client: primingClient,
                sessionCache: AppSessionCache(defaults: .ephemeral()),
                threadCache: ThreadCache(defaults: .ephemeral()),
                localMailStore: localStore,
                automaticallyPrefetchThreads: false
            )
            primingStore.setSessionToken("live-session-token")
            await primingStore.load()
            XCTAssertEqual(primingStore.flatRows.count, 357)
        }

        let backend = BulkPaginatedMailboxAppClient(failOnceAtCursor: "bulk-cursor-200")
        let client = OfflineFirstAppClient(backend: backend, localMailStore: localStore)
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            localMailStore: localStore,
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")

        await store.load()

        XCTAssertTrue(store.mailboxPresentationReady)
        XCTAssertEqual(store.flatRows.count, 357)
        XCTAssertFalse(store.refreshFailed)
        XCTAssertEqual(backend.mailboxCursors, [nil])
        let cached = try XCTUnwrap(localStore.readMailbox(userID: DemoAppFixtures.userID, label: .inbox))
        XCTAssertEqual(cached.sections.flatMap(\.rows).count, 357)
        XCTAssertEqual(cached.loadedThreads, 357)
        XCTAssertNil(cached.nextCursor)
    }

    func testOfflineFirstSQLiteCancelledPageCannotDegradeComplete357Cache() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("ElectronicMailCancelledMailboxTests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }

        let localStore = try XCTUnwrap(
            SQLiteLocalMailStore(databaseURL: directory.appendingPathComponent("LocalMail.sqlite3"))
        )
        do {
            let primingClient = OfflineFirstAppClient(
                backend: BulkPaginatedMailboxAppClient(),
                localMailStore: localStore
            )
            let primingStore = InboxStore(
                client: primingClient,
                sessionCache: AppSessionCache(defaults: .ephemeral()),
                threadCache: ThreadCache(defaults: .ephemeral()),
                localMailStore: localStore,
                automaticallyPrefetchThreads: false
            )
            primingStore.setSessionToken("live-session-token")
            await primingStore.load()
            XCTAssertEqual(primingStore.flatRows.count, 357)
        }

        let backend = BulkPaginatedMailboxAppClient()
        backend.delayedCursor = "bulk-cursor-100"
        let client = OfflineFirstAppClient(backend: backend, localMailStore: localStore)
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            localMailStore: localStore,
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")

        await store.load()
        await store.setMailboxLabel(.sent)

        let cached = try XCTUnwrap(localStore.readMailbox(userID: DemoAppFixtures.userID, label: .inbox))
        XCTAssertEqual(cached.sections.flatMap(\.rows).count, 357)
        XCTAssertEqual(cached.loadedThreads, 357)
        XCTAssertNil(cached.nextCursor)
        XCTAssertEqual(backend.mailboxCursors, [nil, nil])
    }

    func testFolderSwitchKeepsInboxHistoryPaginationAliveAndPersistsOffscreen() async throws {
        let localStore = MemoryLocalMailStore()
        let client = BulkPaginatedMailboxAppClient()
        client.delayedCursor = "bulk-cursor-100"
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            localMailStore: localStore,
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")

        let loadingInbox = Task { await store.load() }
        for _ in 0..<400 where !client.mailboxCursors.contains("bulk-cursor-100") {
            try? await Task.sleep(nanoseconds: 1_000_000)
        }
        XCTAssertTrue(client.mailboxCursors.contains("bulk-cursor-100"))

        await store.setMailboxLabel(.sent)
        await loadingInbox.value

        XCTAssertEqual(store.activeMailboxLabel, .sent)
        XCTAssertEqual(
            client.mailboxCursors.filter { $0 == "bulk-cursor-100" }.count,
            1
        )
        XCTAssertTrue(client.mailboxCursors.contains("bulk-cursor-200"))
        XCTAssertTrue(client.mailboxCursors.contains("bulk-cursor-300"))
        let cached = try XCTUnwrap(localStore.readMailbox(userID: DemoAppFixtures.userID, label: .inbox))
        XCTAssertEqual(cached.sections.flatMap(\.rows).count, 357)
        XCTAssertNil(cached.nextCursor)
    }

    func testFailedAutomaticPageNeverExposesPartialRowsAndRetryCompletesAll357Threads() async {
        let client = BulkPaginatedMailboxAppClient(failOnceAtCursor: "bulk-cursor-200")
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")

        await store.load()

        XCTAssertFalse(store.mailboxPresentationReady)
        XCTAssertTrue(store.flatRows.isEmpty)
        XCTAssertTrue(store.refreshFailed)
        XCTAssertEqual(client.mailboxCursors, [nil, "bulk-cursor-100", "bulk-cursor-200"])

        await store.syncNow()

        XCTAssertTrue(store.mailboxPresentationReady)
        XCTAssertFalse(store.refreshFailed)
        XCTAssertEqual(store.flatRows.count, 357)
        XCTAssertEqual(
            client.mailboxCursors,
            [
                nil,
                "bulk-cursor-100",
                "bulk-cursor-200",
                nil,
                "bulk-cursor-100",
                "bulk-cursor-200",
                "bulk-cursor-300"
            ]
        )
    }

    func testCompleteCached357ThreadSnapshotSurvivesOfflineRefresh() async {
        let localStore = MemoryLocalMailStore()
        let primingStore = InboxStore(
            client: BulkPaginatedMailboxAppClient(),
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            localMailStore: localStore,
            automaticallyPrefetchThreads: false
        )
        primingStore.setSessionToken("live-session-token")
        await primingStore.load()
        XCTAssertEqual(primingStore.flatRows.count, 357)

        let restoredStore = InboxStore(
            client: FailingAppClient(statusCode: 503),
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            localMailStore: localStore,
            automaticallyPrefetchThreads: false
        )
        restoredStore.setSessionToken("live-session-token")

        await restoredStore.load()

        XCTAssertTrue(restoredStore.mailboxPresentationReady)
        XCTAssertEqual(restoredStore.flatRows.count, 357)
        XCTAssertEqual(restoredStore.flatRows.first?.threadID, "bulk-0")
        XCTAssertEqual(restoredStore.flatRows.last?.threadID, "bulk-356")
        XCTAssertTrue(restoredStore.refreshFailed)
    }

    func testForcedRefreshRetainsCompleteSameGenerationSnapshotAndSelection() async {
        let client = BulkPaginatedMailboxAppClient()
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")
        await store.load()
        store.select(threadID: "bulk-150", prefetch: false)
        client.delayedCursor = "bulk-cursor-100"

        await store.syncNow()

        XCTAssertFalse(store.mailboxPageLoading)
        XCTAssertTrue(store.mailboxPresentationReady)
        XCTAssertEqual(store.flatRows.count, 357)
        XCTAssertEqual(store.selectedThreadID, "bulk-150")
        XCTAssertEqual(
            client.mailboxCursors,
            [nil, "bulk-cursor-100", "bulk-cursor-200", "bulk-cursor-300", nil]
        )
    }

    func testChangedRevisionKeepsVerifiedRowsVisibleUntilFreshCursorChainAtomicallyReplacesThem() async throws {
        let localStore = MemoryLocalMailStore()
        let client = BulkPaginatedMailboxAppClient(
            authoritativeGeneration: "authoritative-generation-1",
            mailboxTitlePrefix: "Old message"
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            localMailStore: localStore,
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")
        await store.load()
        store.select(threadID: "bulk-150", prefetch: false)

        XCTAssertEqual(store.flatRows.count, 357)
        XCTAssertEqual(store.flatRows.first?.title, "Old message 0")

        let pageGate = ReaderActionRequestGate()
        client.stageAuthoritativeRefresh(
            revision: "bulk-revision-2",
            titlePrefix: "Fresh message",
            totalThreads: 257,
            gateAtCursor: "bulk-cursor-100",
            gate: pageGate
        )

        let refresh = Task { await store.syncNow() }
        await pageGate.waitUntilRequestStarts()

        XCTAssertTrue(store.mailboxPageLoading)
        XCTAssertTrue(store.mailboxPresentationReady)
        XCTAssertEqual(store.flatRows.count, 357)
        XCTAssertEqual(store.flatRows.first?.title, "Old message 0")
        XCTAssertEqual(store.selectedThreadID, "bulk-150")
        XCTAssertEqual(
            localStore.readMailbox(userID: DemoAppFixtures.userID, label: .inbox)?
                .sections.flatMap(\.rows).count,
            357
        )

        await pageGate.releaseRequest()
        await refresh.value

        XCTAssertFalse(store.mailboxPageLoading)
        XCTAssertFalse(store.refreshFailed)
        XCTAssertTrue(store.mailboxPresentationReady)
        XCTAssertEqual(store.flatRows.count, 257)
        XCTAssertEqual(store.flatRows.first?.title, "Fresh message 0")
        XCTAssertEqual(store.flatRows.last?.threadID, "bulk-256")
        XCTAssertEqual(store.selectedThreadID, "bulk-150")
        let cached = try XCTUnwrap(
            localStore.readMailbox(userID: DemoAppFixtures.userID, label: .inbox)
        )
        XCTAssertEqual(cached.sections.flatMap(\.rows).count, 257)
        XCTAssertEqual(cached.sections.flatMap(\.rows).first?.displayTitle, "Fresh message 0")
        XCTAssertNil(cached.nextCursor)
    }

    func testChangedRevisionLaterPageFailureRetainsVerifiedRowsSelectionAndRetryState() async throws {
        let localStore = MemoryLocalMailStore()
        let client = BulkPaginatedMailboxAppClient(
            authoritativeGeneration: "authoritative-generation-1",
            mailboxTitlePrefix: "Old message"
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            localMailStore: localStore,
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")
        await store.load()
        store.select(threadID: "bulk-150", prefetch: false)

        let pageGate = ReaderActionRequestGate()
        client.stageAuthoritativeRefresh(
            revision: "bulk-revision-2",
            titlePrefix: "Fresh message",
            totalThreads: 257,
            gateAtCursor: "bulk-cursor-100",
            gate: pageGate,
            failOnceAtCursor: "bulk-cursor-200"
        )

        let refresh = Task { await store.syncNow() }
        await pageGate.waitUntilRequestStarts()

        XCTAssertEqual(store.flatRows.count, 357)
        XCTAssertEqual(store.flatRows.first?.title, "Old message 0")
        XCTAssertEqual(store.selectedThreadID, "bulk-150")

        await pageGate.releaseRequest()
        await refresh.value

        XCTAssertFalse(store.mailboxPageLoading)
        XCTAssertTrue(store.refreshFailed)
        XCTAssertTrue(store.mailboxPresentationReady)
        XCTAssertTrue(store.mailboxFooterShowsRetry)
        XCTAssertFalse(store.mailboxFooterShowsProgress)
        XCTAssertEqual(store.flatRows.count, 357)
        XCTAssertEqual(store.flatRows.first?.title, "Old message 0")
        XCTAssertEqual(store.selectedThreadID, "bulk-150")
        let cached = try XCTUnwrap(
            localStore.readMailbox(userID: DemoAppFixtures.userID, label: .inbox)
        )
        XCTAssertEqual(cached.sections.flatMap(\.rows).count, 357)
        XCTAssertEqual(cached.sections.flatMap(\.rows).first?.displayTitle, "Old message 0")
        XCTAssertNil(cached.nextCursor)
    }

    func testOverlappingRefreshCannotClearCompleteSameGenerationSnapshot() async {
        let client = BulkPaginatedMailboxAppClient()
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")
        await store.load()
        client.delayedCursor = "bulk-cursor-100"

        let ownerRefresh = Task { await store.syncNow() }
        let overlappingRefresh = Task { await store.refresh() }
        await ownerRefresh.value
        await overlappingRefresh.value

        XCTAssertFalse(store.mailboxPageLoading)
        XCTAssertTrue(store.mailboxPresentationReady)
        XCTAssertEqual(store.flatRows.count, 357)
        XCTAssertEqual(
            client.mailboxCursors,
            [
                nil,
                "bulk-cursor-100",
                "bulk-cursor-200",
                "bulk-cursor-300",
                nil
            ]
        )
    }

    func testRevisionMismatchDuringPaginationRestartsFromPageOneAndPublishesOneGeneration() async {
        let client = BulkPaginatedMailboxAppClient(revisionMismatchOnceAtCursor: "bulk-cursor-200")
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")

        await store.load()

        XCTAssertTrue(store.mailboxPresentationReady)
        XCTAssertFalse(store.refreshFailed)
        XCTAssertEqual(store.flatRows.count, 357)
        XCTAssertEqual(Set(store.flatRows.map(\.threadID)).count, 357)
        XCTAssertEqual(
            client.mailboxCursors,
            [
                nil,
                "bulk-cursor-100",
                "bulk-cursor-200",
                nil,
                "bulk-cursor-100",
                "bulk-cursor-200",
                "bulk-cursor-300",
            ]
        )
    }

    func testMultipleRevisionChurnsRestartFromPageOneThenCompleteWithoutPartialCache() async {
        let client = BulkPaginatedMailboxAppClient(
            revisionMismatchOnceAtCursor: "bulk-cursor-100",
            revisionChurnCount: 2
        )
        let localStore = MemoryLocalMailStore()
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            localMailStore: localStore,
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")

        await store.load()

        XCTAssertTrue(store.mailboxPresentationReady)
        XCTAssertFalse(store.refreshFailed)
        XCTAssertEqual(store.flatRows.count, 357)
        XCTAssertEqual(Set(store.flatRows.map(\.threadID)).count, 357)
        XCTAssertEqual(
            client.mailboxCursors,
            [
                nil,
                "bulk-cursor-100",
                nil,
                "bulk-cursor-100",
                nil,
                "bulk-cursor-100",
                "bulk-cursor-200",
                "bulk-cursor-300",
            ]
        )
        let cached = localStore.readMailbox(userID: DemoAppFixtures.userID, label: .inbox)
        XCTAssertEqual(cached?.sections.flatMap(\.rows).count, 357)
        XCTAssertNil(cached?.nextCursor)
    }

    func testRevisionChurnRetryExhaustionStaysLoadingWithoutRowsFailureOrPartialCache() async {
        let client = BulkPaginatedMailboxAppClient(
            revisionMismatchOnceAtCursor: "bulk-cursor-100",
            revisionChurnCount: 10
        )
        let localStore = MemoryLocalMailStore()
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            localMailStore: localStore,
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")

        await store.load()

        XCTAssertEqual(store.phase, .loading)
        XCTAssertFalse(store.mailboxPresentationReady)
        XCTAssertFalse(store.refreshFailed)
        XCTAssertTrue(store.flatRows.isEmpty)
        XCTAssertEqual(client.mailboxCursors.filter { $0 == nil }.count, 4)
        XCTAssertEqual(client.mailboxCursors.filter { $0 == "bulk-cursor-100" }.count, 4)
        XCTAssertNil(localStore.readMailbox(userID: DemoAppFixtures.userID, label: .inbox))
    }

    func testRevisionChurnRetryExhaustionPreservesPriorCompleteSnapshotAndCache() async {
        let localStore = MemoryLocalMailStore()
        do {
            let primingStore = InboxStore(
                client: BulkPaginatedMailboxAppClient(),
                sessionCache: AppSessionCache(defaults: .ephemeral()),
                threadCache: ThreadCache(defaults: .ephemeral()),
                localMailStore: localStore,
                automaticallyPrefetchThreads: false
            )
            primingStore.setSessionToken("live-session-token")
            await primingStore.load()
            XCTAssertEqual(primingStore.flatRows.count, 357)
        }

        let client = BulkPaginatedMailboxAppClient(
            revisionMismatchOnceAtCursor: "bulk-cursor-100",
            revisionChurnCount: 10
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            localMailStore: localStore,
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")

        await store.load()

        XCTAssertEqual(store.phase, .loaded)
        XCTAssertTrue(store.mailboxPresentationReady)
        XCTAssertFalse(store.refreshFailed)
        XCTAssertEqual(store.flatRows.count, 357)
        let cached = localStore.readMailbox(userID: DemoAppFixtures.userID, label: .inbox)
        XCTAssertEqual(cached?.sections.flatMap(\.rows).count, 357)
        XCTAssertNil(cached?.nextCursor)
    }

    func testRepeatedCursorNeverPublishesPartialSnapshotAndCanRetry() async {
        let client = BulkPaginatedMailboxAppClient(repeatCursorOnceAtCursor: "bulk-cursor-200")
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")

        await store.load()

        XCTAssertFalse(store.mailboxPresentationReady)
        XCTAssertTrue(store.flatRows.isEmpty)
        XCTAssertTrue(store.refreshFailed)

        await store.syncNow()

        XCTAssertTrue(store.mailboxPresentationReady)
        XCTAssertEqual(store.flatRows.count, 357)
    }

    func testImportRunningPaginationAllowsGrowingRevisionAndPersistsAcrossLabelSwitch() async throws {
        let localStore = MemoryLocalMailStore()
        let client = BulkPaginatedMailboxAppClient(
            revisionMismatchOnceAtCursor: "bulk-cursor-100",
            importInProgress: true,
            growingImportTotals: true
        )
        client.delayedCursor = "bulk-cursor-100"
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            localMailStore: localStore,
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")

        let loadingInbox = Task { await store.load() }
        for _ in 0..<400 where !client.mailboxCursors.contains("bulk-cursor-100") {
            try? await Task.sleep(nanoseconds: 1_000_000)
        }

        XCTAssertTrue(client.mailboxCursors.contains("bulk-cursor-100"))
        XCTAssertTrue(store.mailboxPresentationReady)
        XCTAssertEqual(store.flatRows.count, 100)
        XCTAssertEqual(store.mailboxFooterProgressText, "Loading older mail… 100 of 357")

        await store.setMailboxLabel(.sent)
        await loadingInbox.value

        XCTAssertEqual(store.activeMailboxLabel, .sent)
        XCTAssertEqual(
            client.mailboxCursors.compactMap { $0 },
            ["bulk-cursor-100", "bulk-cursor-200", "bulk-cursor-300"]
        )
        XCTAssertEqual(client.mailboxCursors.filter { $0 == nil }.count, 2)
        XCTAssertFalse(store.refreshFailed)
        let cached = try XCTUnwrap(localStore.readMailbox(userID: DemoAppFixtures.userID, label: .inbox))
        XCTAssertEqual(cached.sections.flatMap(\.rows).count, 357)
        XCTAssertEqual(cached.totalThreads, 357)
        XCTAssertEqual(cached.loadedThreads, 357)
        XCTAssertEqual(cached.syncGeneration, "bulk-import-generation-1")
        XCTAssertNil(cached.nextCursor)
    }

    func testImportContinuationFailureRetainsCommittedRowsAndShowsCompactRetry() async throws {
        let localStore = MemoryLocalMailStore()
        let client = BulkPaginatedMailboxAppClient(
            failOnceAtCursor: "bulk-cursor-200",
            importInProgress: true,
            growingImportTotals: true
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            localMailStore: localStore,
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")

        await store.load()

        XCTAssertTrue(store.mailboxPresentationReady)
        XCTAssertTrue(store.refreshFailed)
        XCTAssertEqual(store.flatRows.count, 200)
        XCTAssertEqual(store.mailboxFooterProgressText, "Sync paused. Your saved email is still available.")
        XCTAssertTrue(store.mailboxFooterShowsRetry)
        XCTAssertFalse(store.mailboxFooterShowsProgress)
        XCTAssertEqual(
            localStore.readMailbox(userID: DemoAppFixtures.userID, label: .inbox)?.sections.flatMap(\.rows).count,
            200
        )

        await store.syncNow()

        XCTAssertFalse(store.refreshFailed)
        XCTAssertEqual(store.flatRows.count, 357)
        XCTAssertEqual(
            localStore.readMailbox(userID: DemoAppFixtures.userID, label: .inbox)?.sections.flatMap(\.rows).count,
            357
        )
    }

    func testFreshProgressiveGenerationAtomicallyReplacesPriorLegacySnapshotAfterVerifiedBatch() async {
        let localStore = MemoryLocalMailStore()
        do {
            let primingStore = InboxStore(
                client: BulkPaginatedMailboxAppClient(),
                sessionCache: AppSessionCache(defaults: .ephemeral()),
                threadCache: ThreadCache(defaults: .ephemeral()),
                localMailStore: localStore,
                automaticallyPrefetchThreads: false
            )
            primingStore.setSessionToken("live-session-token")
            await primingStore.load()
            XCTAssertEqual(primingStore.flatRows.count, 357)
        }

        let client = BulkPaginatedMailboxAppClient(importInProgress: true)
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            localMailStore: localStore,
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")

        await store.load()

        XCTAssertTrue(store.mailboxPresentationReady)
        XCTAssertEqual(store.flatRows.count, 357)
        XCTAssertFalse(store.refreshFailed)
        let cached = localStore.readMailbox(userID: DemoAppFixtures.userID, label: .inbox)
        XCTAssertEqual(cached?.sections.flatMap(\.rows).count, 357)
        XCTAssertEqual(cached?.syncGeneration, "bulk-import-generation-1")
        XCTAssertEqual(cached?.fullImportRunning, true)
    }

    func testMailboxPageMergePreservesServerRankAndOrderedDateRuns() {
        let firstPage = MailboxResponse(
            label: .inbox,
            totalThreads: 5,
            nextCursor: "cursor-2",
            loadedThreads: 3,
            sections: [
                GmailThreadSection(
                    id: "yesterday:rank-1",
                    title: "Yesterday",
                    rows: [
                        makeMailboxRow(
                            threadID: "rank-1",
                            latestSourceRecordID: "message-rank-1",
                            receivedAt: "2026-07-10T08:00:00+00:00",
                            title: "First by Gmail rank"
                        ),
                        makeMailboxRow(
                            threadID: "rank-2",
                            latestSourceRecordID: "message-rank-2",
                            receivedAt: "2026-07-14T18:00:00+00:00",
                            title: "Newer but ranked second"
                        )
                    ]
                ),
                GmailThreadSection(
                    id: "today:rank-3",
                    title: "Today",
                    rows: [
                        makeMailboxRow(
                            threadID: "rank-3",
                            latestSourceRecordID: "message-rank-3",
                            receivedAt: "2026-07-12T12:00:00+00:00",
                            title: "Third by Gmail rank"
                        )
                    ]
                )
            ]
        )
        let secondPage = MailboxResponse(
            label: .inbox,
            totalThreads: 5,
            nextCursor: nil,
            loadedThreads: 2,
            sections: [
                GmailThreadSection(
                    id: "yesterday:rank-4",
                    title: "Yesterday",
                    rows: [
                        makeMailboxRow(
                            threadID: "rank-4",
                            latestSourceRecordID: "message-rank-4",
                            receivedAt: "2026-07-15T09:00:00+00:00",
                            title: "Fourth despite newest date"
                        ),
                        makeMailboxRow(
                            threadID: "rank-5",
                            latestSourceRecordID: "message-rank-5",
                            receivedAt: "2026-07-01T09:00:00+00:00",
                            title: "Fifth by Gmail rank"
                        )
                    ]
                )
            ]
        )

        let merged = firstPage.appendingPage(secondPage)

        XCTAssertEqual(merged.sections.map(\.id), ["yesterday:rank-1", "today:rank-3", "yesterday:rank-4"])
        XCTAssertEqual(merged.sections.map(\.title), ["Yesterday", "Today", "Yesterday"])
        XCTAssertEqual(merged.sections.flatMap(\.rows).map(\.threadID), ["rank-1", "rank-2", "rank-3", "rank-4", "rank-5"])
    }

    func testMailboxRefreshPreservesFreshFirstPageThenCachedTailRank() {
        let freshLead = makeMailboxRow(
            threadID: "rank-1",
            latestSourceRecordID: "message-rank-1",
            receivedAt: "2026-07-10T08:00:00+00:00",
            title: "Fresh first"
        )
        let freshSecond = makeMailboxRow(
            threadID: "rank-2",
            latestSourceRecordID: "message-rank-2",
            receivedAt: "2026-07-15T18:00:00+00:00",
            title: "Fresh second despite newer date"
        )
        let cachedTailFirst = makeMailboxRow(
            threadID: "rank-3",
            latestSourceRecordID: "message-rank-3",
            receivedAt: "2026-07-02T09:00:00+00:00",
            title: "Cached tail first"
        )
        let cachedTailSecond = makeMailboxRow(
            threadID: "rank-4",
            latestSourceRecordID: "message-rank-4",
            receivedAt: "2026-07-14T09:00:00+00:00",
            title: "Cached tail second despite newer date"
        )
        let cachedMailbox = MailboxResponse(
            label: .inbox,
            totalThreads: 4,
            nextCursor: "cursor-3",
            loadedThreads: 4,
            sections: [
                GmailThreadSection(id: "today:rank-1", title: "Today", rows: [freshLead, freshSecond]),
                GmailThreadSection(id: "earlier:rank-3", title: "Earlier", rows: [cachedTailFirst, cachedTailSecond])
            ],
            mailboxRevision: "revision-1"
        )
        let refreshedFirstPage = MailboxResponse(
            label: .inbox,
            totalThreads: 4,
            nextCursor: "cursor-2-new-generation",
            loadedThreads: 2,
            sections: [
                GmailThreadSection(id: "today:rank-1", title: "Today", rows: [freshLead, freshSecond])
            ],
            mailboxRevision: "revision-1"
        )

        let merged = cachedMailbox.preservingLoadedPages(afterRefreshingFirstPage: refreshedFirstPage)

        XCTAssertEqual(merged.sections.map(\.id), ["today:rank-1", "earlier:rank-3"])
        XCTAssertEqual(merged.sections.flatMap(\.rows).map(\.threadID), ["rank-1", "rank-2", "rank-3", "rank-4"])
        XCTAssertEqual(merged.nextCursor, "cursor-3")
    }

    func testRefreshAfterAutomaticPaginationPreservesLoadedRowsAndDoesNotReloadSameCursor() async {
        let client = PaginatedMailboxAppClient()
        let store = InboxStore(client: client, sessionCache: AppSessionCache(defaults: .ephemeral()), threadCache: ThreadCache(defaults: .ephemeral()))
        store.setSessionToken("live-session-token")

        await store.load()
        await store.refresh()
        await store.loadMoreMailbox(automatic: true)

        XCTAssertEqual(store.flatRows.map(\.threadID), ["demo-google-today", "demo-github-today"])
        XCTAssertTrue(store.mailboxPresentationReady)
        XCTAssertEqual(client.mailboxCursors, [nil, "cursor-2"])
    }

    func testMailboxPaginationLoadsEachCursorExactlyOnce() async {
        let client = MailboxPaginationRaceAppClient()
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")

        await store.load()
        let redundantAutomaticTrigger = Task { await store.loadMoreMailbox(automatic: true) }
        let redundantManualTrigger = Task { await store.loadMoreMailbox() }
        await redundantAutomaticTrigger.value
        await redundantManualTrigger.value
        await store.loadMoreMailbox()

        XCTAssertEqual(store.flatRows.map(\.threadID), ["inbox-1", "inbox-2", "inbox-3"])
        XCTAssertEqual(
            client.mailboxCalls,
            [
                MailboxPageCall(label: .inbox, cursor: nil),
                MailboxPageCall(label: .inbox, cursor: "inbox-cursor-2"),
                MailboxPageCall(label: .inbox, cursor: "inbox-cursor-3")
            ]
        )
        XCTAssertFalse(store.mailboxPageLoading)
        XCTAssertTrue(store.mailboxPresentationReady)
    }

    func testSearchCancelsActiveMailboxPageChainAndPublishesCompleteSearch() async {
        let client = BulkPaginatedMailboxAppClient()
        client.delayedCursor = "bulk-cursor-100"
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")

        let loadingMailbox = Task { await store.load() }
        for _ in 0..<200 where !client.mailboxCursors.contains("bulk-cursor-100") {
            try? await Task.sleep(nanoseconds: 1_000_000)
        }
        XCTAssertTrue(client.mailboxCursors.contains("bulk-cursor-100"))

        await store.searchMailbox("invoice")
        await loadingMailbox.value

        XCTAssertEqual(store.searchQuery, "invoice")
        XCTAssertEqual(store.flatRows.map(\.threadID), ["search-invoice"])
        XCTAssertTrue(store.mailboxPresentationReady)
        XCTAssertFalse(store.mailboxPageLoading)
        XCTAssertEqual(client.searchQueries, ["invoice"])
    }

    func testSwitchingMailboxesKeepsAndIsolatesOldPagination() async {
        let client = MailboxPaginationRaceAppClient()
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")

        let staleInboxPage = Task { await store.load() }
        for _ in 0..<100 {
            if client.mailboxCalls.contains(MailboxPageCall(label: .inbox, cursor: "inbox-cursor-2")) {
                break
            }
            try? await Task.sleep(nanoseconds: 1_000_000)
        }
        XCTAssertTrue(client.mailboxCalls.contains(MailboxPageCall(label: .inbox, cursor: "inbox-cursor-2")))
        XCTAssertTrue(store.mailboxPageLoading)
        XCTAssertFalse(store.mailboxPresentationReady)
        XCTAssertTrue(store.flatRows.isEmpty, "A known partial first page must not be exposed")

        let currentSentPage = Task { await store.setMailboxLabel(.sent) }
        for _ in 0..<100 {
            if client.mailboxCalls.contains(MailboxPageCall(label: .sent, cursor: "sent-cursor-2")) {
                break
            }
            try? await Task.sleep(nanoseconds: 1_000_000)
        }
        XCTAssertTrue(client.mailboxCalls.contains(MailboxPageCall(label: .sent, cursor: "sent-cursor-2")))

        await staleInboxPage.value
        XCTAssertEqual(store.activeMailboxLabel, .sent)
        XCTAssertTrue(store.flatRows.isEmpty)
        XCTAssertTrue(store.mailboxPageLoading, "A late Inbox completion must not clear the active Sent loading state")
        XCTAssertFalse(store.mailboxPresentationReady)

        await currentSentPage.value
        XCTAssertEqual(store.flatRows.map(\.threadID), ["sent-1", "sent-2"])
        XCTAssertFalse(store.flatRows.map(\.threadID).contains("inbox-2"))
        XCTAssertFalse(store.mailboxPageLoading)
        XCTAssertTrue(store.mailboxPresentationReady)
        XCTAssertEqual(
            client.mailboxCalls,
            [
                MailboxPageCall(label: .inbox, cursor: nil),
                MailboxPageCall(label: .inbox, cursor: "inbox-cursor-2"),
                MailboxPageCall(label: .sent, cursor: nil),
                MailboxPageCall(label: .sent, cursor: "sent-cursor-2"),
                MailboxPageCall(label: .inbox, cursor: "inbox-cursor-3")
            ]
        )
    }

    func testIncompleteEmptyMailboxShowsLoadingInsteadOfAnEmptyCompleteState() async {
        let emptyMailbox = MailboxResponse(
            label: .inbox,
            totalThreads: 0,
            nextCursor: nil,
            loadedThreads: 0,
            sections: [],
            fullImportRunning: true,
            fullImportCompleted: false
        )
        let store = InboxStore(
            client: FixedMailboxAppClient(mailbox: emptyMailbox),
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")

        await store.load()

        XCTAssertEqual(store.mailboxVisibleRowCount, 0)
        XCTAssertFalse(store.mailboxPresentationReady)
        XCTAssertEqual(store.mailboxLoadingProgressText, "Syncing your mailbox...")
        XCTAssertTrue(store.flatRows.isEmpty)
    }

    func testIncompleteImportFlagHidesRowsEvenWhenEveryReportedThreadIsLoaded() async {
        let row = makeMailboxRow(
            threadID: "not-yet-proven-complete",
            latestSourceRecordID: "not-yet-proven-complete-message",
            receivedAt: "2026-07-24T12:00:00Z",
            title: "Do not reveal yet"
        )
        let incompleteMailbox = MailboxResponse(
            label: .inbox,
            totalThreads: 1,
            nextCursor: nil,
            loadedThreads: 1,
            sections: [GmailThreadSection(id: "today", title: "Today", rows: [row])],
            fullImportRunning: false,
            fullImportCompleted: false
        )
        let store = InboxStore(
            client: FixedMailboxAppClient(mailbox: incompleteMailbox),
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")

        await store.load()

        XCTAssertFalse(store.mailboxPresentationReady)
        XCTAssertTrue(store.flatRows.isEmpty)
        XCTAssertEqual(store.mailboxLoadingProgressText, "Finishing mailbox sync...")
    }

    func testServerClaimedCompletionCannotExposeFewerRowsThanTotal() async {
        let row = makeMailboxRow(
            threadID: "only-visible-row",
            latestSourceRecordID: "only-visible-message",
            receivedAt: "2026-07-24T12:00:00Z",
            title: "Only one of two"
        )
        let inconsistentMailbox = MailboxResponse(
            label: .inbox,
            totalThreads: 2,
            nextCursor: nil,
            loadedThreads: 2,
            sections: [GmailThreadSection(id: "today", title: "Today", rows: [row])],
            fullImportRunning: false,
            fullImportCompleted: true
        )
        let store = InboxStore(
            client: FixedMailboxAppClient(mailbox: inconsistentMailbox),
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")

        await store.load()

        XCTAssertFalse(store.mailboxPresentationReady)
        XCTAssertTrue(store.flatRows.isEmpty)
        XCTAssertTrue(store.refreshFailed)
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

    func testMailboxRevisionChangeReplacesWithCompleteAuthoritativeSnapshot() {
        let firstRow = makeMailboxRow(
            threadID: "first-page",
            latestSourceRecordID: "first-message",
            receivedAt: "2026-05-29T10:00:00+05:30",
            title: "First page"
        )
        let staleLaterRow = makeMailboxRow(
            threadID: "stale-later-page",
            latestSourceRecordID: "stale-message",
            receivedAt: "2026-05-20T10:00:00+05:30",
            title: "Stale later page"
        )
        let current = MailboxResponse(
            label: .inbox,
            totalThreads: 2,
            nextCursor: "cursor-3",
            loadedThreads: 2,
            sections: [GmailThreadSection(id: "today", title: "Today", rows: [firstRow, staleLaterRow])],
            mailboxRevision: "revision-1"
        )
        let refreshedFirstPage = MailboxResponse(
            label: .inbox,
            totalThreads: 1,
            nextCursor: nil,
            loadedThreads: 1,
            sections: [GmailThreadSection(id: "today", title: "Today", rows: [firstRow])],
            mailboxRevision: "revision-2"
        )

        let merged = current.preservingLoadedPages(afterRefreshingFirstPage: refreshedFirstPage)

        XCTAssertEqual(
            merged.sections.flatMap(\.rows).map(\.threadID),
            ["first-page"]
        )
        XCTAssertEqual(merged.loadedThreads, 1)
        XCTAssertNil(merged.nextCursor)
    }

    func testMailboxGenerationChangeAtomicallyReplacesPriorRows() {
        let oldLead = makeMailboxRow(
            threadID: "old-lead",
            latestSourceRecordID: "old-lead-message",
            receivedAt: "2026-05-29T10:00:00+05:30",
            title: "Old lead"
        )
        let oldTail = makeMailboxRow(
            threadID: "old-tail",
            latestSourceRecordID: "old-tail-message",
            receivedAt: "2026-05-20T10:00:00+05:30",
            title: "Old tail"
        )
        let newLead = makeMailboxRow(
            threadID: "new-lead",
            latestSourceRecordID: "new-lead-message",
            receivedAt: "2026-07-25T10:00:00Z",
            title: "New lead"
        )
        let current = MailboxResponse(
            label: .inbox,
            totalThreads: 2,
            nextCursor: "old-cursor",
            loadedThreads: 2,
            sections: [GmailThreadSection(id: "today", title: "Today", rows: [oldLead, oldTail])],
            syncGeneration: "generation-1"
        )
        let nextGeneration = MailboxResponse(
            label: .inbox,
            totalThreads: 1,
            loadedThreads: 1,
            sections: [GmailThreadSection(id: "today", title: "Today", rows: [newLead])],
            syncGeneration: "generation-2"
        )

        let replaced = current.preservingLoadedPages(afterRefreshingFirstPage: nextGeneration)

        XCTAssertEqual(replaced.sections.flatMap(\.rows).map(\.threadID), ["new-lead"])
        XCTAssertEqual(replaced.loadedThreads, 1)
        XCTAssertNil(replaced.nextCursor)
    }

    func testComposeAndReplyDoNotCallBackendWhenSendScopeIsMissing() async throws {
        let client = SendTrackingAppClient()
        let store = InboxStore(client: client, sessionCache: AppSessionCache(defaults: .ephemeral()), threadCache: ThreadCache(defaults: .ephemeral()))

        await store.load()

        let compose = try await store.sendCompose(
            clientSendID: "compose-client-send",
            to: ["person@example.com"],
            subject: "Subject",
            bodyText: "Body"
        )
        let reply = try await store.sendReply(
            clientSendID: "reply-client-send",
            threadID: "demo-google-today",
            bodyText: "Reply"
        )

        XCTAssertEqual(compose.state, .reauthRequired)
        XCTAssertEqual(compose.clientSendID, "compose-client-send")
        XCTAssertEqual(reply.state, .reauthRequired)
        XCTAssertEqual(reply.clientSendID, "reply-client-send")
        XCTAssertEqual(reply.mailboxThreadID, "demo-google-today")
        XCTAssertEqual(client.composeCallCount, 0)
        XCTAssertEqual(client.replyCallCount, 0)
    }

    func testDurableSendStatusAndRetryForwardThroughInboxStore() async throws {
        let client = SendTrackingAppClient()
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral())
        )

        let status = try await store.sendStatus(serverSendID: "server-send-1")
        let retry = try await store.retrySend(serverSendID: "server-send-1")

        XCTAssertEqual(client.statusServerSendIDs, ["server-send-1"])
        XCTAssertEqual(client.retryServerSendIDs, ["server-send-1"])
        XCTAssertEqual(status.state, .failed)
        XCTAssertEqual(retry.state, .queued)
    }

    func testMailboxChangedSSEForcesActiveMailboxRefresh() async {
        let initialMailbox = makeSingleRowMailbox(
            threadID: "old-thread",
            title: "Old mailbox row",
            receivedAt: "2026-05-29T09:30:00+05:30"
        ).withTestMailboxRevision("rev-1")
        let refreshedMailbox = makeSingleRowMailbox(
            threadID: "new-thread",
            title: "Fresh mailbox row",
            receivedAt: "2026-05-29T10:30:00+05:30"
        ).withTestMailboxRevision("rev-2")
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
        let initialForcedRefreshAt = store.lastForcedMailboxRefreshAt

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
        XCTAssertEqual(store.lastForcedMailboxRefreshAt, initialForcedRefreshAt)
    }

    func testThreadContentHydratedSSEImmediatelyReplacesSnippetWithFullBodyAndAttachments() async {
        let mailbox = makeSingleRowMailbox(threadID: "hydrating-thread", title: "Hydrating")
        let incomplete = makeHydrationThread(body: "Metadata snippet", bodyComplete: false)
        let hydrated = makeHydrationThread(
            body: "Complete Gmail message body",
            bodyComplete: true,
            attachments: [
                ThreadAttachment(
                    id: "message-1:attachment-1",
                    filename: "statement.pdf",
                    mimeType: "application/pdf",
                    size: 1_024,
                    attachmentID: "attachment-1",
                    partID: "1",
                    downloadURL: "/v1/mailbox/messages/message-1/attachments/attachment-1"
                )
            ]
        )
        let client = RealtimeEventAppClient(
            sessionMailbox: mailbox,
            mailboxResponses: [mailbox],
            threadResponses: [incomplete, hydrated]
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false,
            bodyRefreshDelaysNanoseconds: [60_000_000_000]
        )

        await store.load()
        await store.openReader(threadID: "hydrating-thread").value
        XCTAssertEqual(store.readerThread?.messages.first?.body, "Metadata snippet")
        XCTAssertEqual(client.threadCallCount, 1)

        store.handleMailboxServerEvent(
            MailboxServerEvent(
                id: "hydrate-1",
                event: "thread-content-hydrated",
                data: #"{"payload":{"thread_ids":["hydrating-thread"],"hydrated_message_count":1}}"#
            )
        )
        for _ in 0..<20 where client.threadCallCount < 2 {
            try? await Task.sleep(nanoseconds: 5_000_000)
        }

        XCTAssertEqual(client.threadCallCount, 2)
        XCTAssertEqual(store.readerThread?.messages.first?.body, "Complete Gmail message body")
        XCTAssertEqual(store.readerThread?.messages.first?.attachments.map(\.filename), ["statement.pdf"])
        XCTAssertEqual(store.readerThread?.messages.first?.bodyComplete, true)
    }

    func testNonselectedHydrationEventRefreshesAndPersistsMailboxRowObservations() async {
        let initialRow = makeMailboxRow(
            threadID: "background-hydration",
            latestSourceRecordID: "background-message",
            receivedAt: "2026-07-25T12:00:00Z",
            title: "Background hydration",
            bodyReady: false,
            contentRevision: "revision-1"
        )
        let hydratedRow = makeMailboxRow(
            threadID: "background-hydration",
            latestSourceRecordID: "background-message",
            receivedAt: "2026-07-25T12:00:00Z",
            title: "Background hydration",
            bodyReady: true,
            contentRevision: "revision-2"
        )
        let initialMailbox = MailboxResponse(
            label: .inbox,
            totalThreads: 1,
            loadedThreads: 1,
            sections: [GmailThreadSection(id: "today", title: "Today", rows: [initialRow])],
            fullImportRunning: false,
            fullImportCompleted: true
        )
        let hydratedMailbox = MailboxResponse(
            label: .inbox,
            totalThreads: 1,
            loadedThreads: 1,
            sections: [GmailThreadSection(id: "today", title: "Today", rows: [hydratedRow])],
            fullImportRunning: false,
            fullImportCompleted: true
        )
        let client = RealtimeEventAppClient(
            sessionMailbox: initialMailbox,
            mailboxResponses: [initialMailbox, hydratedMailbox]
        )
        let localStore = MemoryLocalMailStore()
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            localMailStore: localStore,
            automaticallyPrefetchThreads: false
        )

        await store.load()
        XCTAssertNil(store.readerThreadID)

        store.handleMailboxServerEvent(
            MailboxServerEvent(
                id: "hydrate-background-1",
                event: "thread-content-hydrated",
                data: #"{"payload":{"threads":[{"thread_id":"background-hydration","body_ready":true,"content_revision":"revision-2","initial_window_position":7}],"hydrated_message_count":1}}"#
            )
        )
        for _ in 0..<300 {
            let cachedBodyReady = localStore
                .readMailbox(userID: DemoAppFixtures.userID, label: .inbox)?
                .sections.flatMap(\.rows).first?.bodyReady
            if client.mailboxCallCount >= 2,
               !client.observedHydratedThreads.isEmpty,
               cachedBodyReady == true {
                break
            }
            try? await Task.sleep(nanoseconds: 5_000_000)
        }

        XCTAssertEqual(client.mailboxCallCount, 2)
        XCTAssertEqual(
            client.observedHydratedThreads,
            [
                MailboxHydratedThreadState(
                    threadID: "background-hydration",
                    bodyReady: true,
                    contentRevision: "revision-2",
                    initialWindowPosition: 7
                )
            ]
        )
        let cachedRow = localStore
            .readMailbox(userID: DemoAppFixtures.userID, label: .inbox)?
            .sections.flatMap(\.rows).first
        XCTAssertEqual(cachedRow?.bodyReady, true)
        XCTAssertEqual(cachedRow?.contentRevision, "revision-2")
    }

    func testReadinessPollingNeverStartsAnotherMailboxTraversal() async {
        let mailbox = makeProgressiveMailbox(
            metadataCount: 25,
            bodyReadyCount: 0,
            estimatedTotalCount: 640
        )
        let client = RealtimeEventAppClient(sessionMailbox: mailbox, mailboxResponses: [mailbox])
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )

        await store.load()
        XCTAssertEqual(client.mailboxCallCount, 1)

        for _ in 0..<30 {
            await store.refreshForReadiness()
        }

        XCTAssertEqual(client.mailboxCallCount, 1)
    }

    func testHydrationEventDuringInFlightStaleReaderFetchStartsOneImmediateFollowUp() async {
        let mailbox = makeSingleRowMailbox(threadID: "racing-thread", title: "Racing hydration")
        let requestGate = ReaderActionRequestGate()
        let incomplete = makeHydrationThread(
            body: "Metadata snippet",
            bodyComplete: false,
            threadID: "racing-thread"
        )
        let hydrated = makeHydrationThread(
            body: "Hydrated after the in-flight response",
            bodyComplete: true,
            threadID: "racing-thread"
        )
        let client = RealtimeEventAppClient(
            sessionMailbox: mailbox,
            mailboxResponses: [mailbox],
            threadResponses: [incomplete, hydrated],
            threadRequestGate: requestGate
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false,
            bodyRefreshDelaysNanoseconds: [60_000_000_000]
        )

        await store.load()
        let openTask = store.openReader(threadID: "racing-thread")
        await requestGate.waitUntilRequestStarts()

        store.handleMailboxServerEvent(
            MailboxServerEvent(
                id: "hydrate-race-1",
                event: "thread-content-hydrated",
                data: #"{"payload":{"thread_ids":["racing-thread"],"hydrated_message_count":1}}"#
            )
        )
        XCTAssertEqual(client.threadCallCount, 1)

        await requestGate.releaseRequest()
        await openTask.value
        for _ in 0..<40 where store.readerThread?.messages.first?.bodyComplete != true {
            try? await Task.sleep(nanoseconds: 5_000_000)
        }

        XCTAssertEqual(client.threadCallCount, 2)
        XCTAssertEqual(
            store.readerThread?.messages.first?.body,
            "Hydrated after the in-flight response"
        )
        XCTAssertTrue(store.readerThread?.messages.first?.bodyComplete == true)

        try? await Task.sleep(nanoseconds: 20_000_000)
        XCTAssertEqual(client.threadCallCount, 2)
    }

    func testIncompleteReaderUsesBoundedRefreshFallbackWhenSSEIsUnavailable() async {
        let mailbox = makeSingleRowMailbox(threadID: "fallback-thread", title: "Fallback")
        let incomplete = makeHydrationThread(body: "Metadata snippet", bodyComplete: false, threadID: "fallback-thread")
        let hydrated = makeHydrationThread(body: "Hydrated by fallback", bodyComplete: true, threadID: "fallback-thread")
        let client = RealtimeEventAppClient(
            sessionMailbox: mailbox,
            mailboxResponses: [mailbox],
            threadResponses: [incomplete, hydrated]
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false,
            bodyRefreshDelaysNanoseconds: [1_000_000]
        )

        await store.load()
        await store.openReader(threadID: "fallback-thread").value
        for _ in 0..<20 where client.threadCallCount < 2 {
            try? await Task.sleep(nanoseconds: 5_000_000)
        }

        XCTAssertEqual(client.threadCallCount, 2)
        XCTAssertEqual(store.readerThread?.messages.first?.body, "Hydrated by fallback")
        XCTAssertEqual(store.readerThread?.messages.first?.bodyComplete, true)
    }

    func testSyncStateSSEUpdatesFullStateAndMarksRealtimeRecovered() async throws {
        let mailbox = makeSingleRowMailbox(threadID: "inbox-thread", title: "Inbox row")
        let client = RealtimeEventAppClient(sessionMailbox: mailbox, mailboxResponses: [mailbox])
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral())
        )
        await store.load()
        var state = makeMailboxSyncStateResponse(connected: true, mailboxRevision: "sse-revision-1")
        state.watchStatus = "active"
        state.pollerOnline = true
        state.pendingActionCount = 2
        let data = String(decoding: try JSONEncoder.backend.encode(state), as: UTF8.self)

        store.handleMailboxServerEvent(
            MailboxServerEvent(id: nil, event: "sync-state", data: data)
        )

        XCTAssertEqual(store.syncState, state)
        XCTAssertTrue(store.realtimeConnected)
        XCTAssertNotNil(store.lastSSEConnectedAt)
        XCTAssertNotNil(store.lastSSEEventAt)
        store.stopLiveRefreshLoop()
    }

    func testDisconnectedSyncStateSSETransitionsToReauthentication() async throws {
        let mailbox = makeSingleRowMailbox(threadID: "inbox-thread", title: "Inbox row")
        let client = RealtimeEventAppClient(sessionMailbox: mailbox, mailboxResponses: [mailbox])
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral())
        )
        store.setSessionToken("revoked-google-token-session")
        await store.load()
        let state = makeMailboxSyncStateResponse(connected: false, mailboxRevision: "sse-revision-2")
        let data = String(decoding: try JSONEncoder.backend.encode(state), as: UTF8.self)

        store.handleMailboxServerEvent(
            MailboxServerEvent(id: nil, event: "sync-state", data: data)
        )

        XCTAssertFalse(store.hasSessionToken)
        XCTAssertNil(store.session)
        XCTAssertFalse(store.realtimeConnected)
        XCTAssertEqual(store.phase, .failed("Sign in with Google to load your mailbox."))
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
                data: String(
                    decoding: try! JSONEncoder.backend.encode(
                        makeMailboxSyncStateResponse(connected: true, mailboxRevision: "rev-1")
                    ),
                    as: UTF8.self
                )
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

    func testSearchHydratedSSERefreshesMatchingSearchWithoutRehydrating() async {
        let initial = makeSingleRowMailbox(threadID: "local-result", title: "Local result")
        let hydrated = makeSingleRowMailbox(threadID: "gmail-result", title: "Hydrated Gmail result")
        let client = SearchHydrationAppClient(searchResponses: [initial, hydrated])
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")

        await store.load()
        await store.searchMailbox("  invoice  ")
        XCTAssertEqual(store.flatRows.map(\.title), ["Local result"])

        store.handleMailboxServerEvent(
            MailboxServerEvent(
                id: "search-1",
                event: "mailbox-search-hydrated",
                data: #"{"mailbox_label":"inbox","payload":{"search_key":"9fb325127f025e4a05adad65c3dd226004fb3b6e659a83a39041f8f717cd2e66"}}"#
            )
        )
        try? await Task.sleep(nanoseconds: 80_000_000)

        XCTAssertEqual(store.flatRows.map(\.title), ["Hydrated Gmail result"])
        XCTAssertEqual(
            client.searchCalls,
            [
                MailboxSearchCall(query: "invoice", label: .inbox, limit: 100, cursor: nil, hydrateInBackground: true),
                MailboxSearchCall(query: "invoice", label: .inbox, limit: 100, cursor: nil, hydrateInBackground: false),
            ]
        )
        XCTAssertEqual(store.lastSSEEventType, "mailbox-search-hydrated")
    }

    func testServerClaimedCompleteSearchWithMissingRowsBecomesErrorInsteadOfLoadingForever() async {
        let row = makeMailboxRow(
            threadID: "only-search-row",
            latestSourceRecordID: "only-search-message",
            receivedAt: "2026-07-24T12:00:00Z",
            title: "Only one search result"
        )
        let inconsistent = MailboxResponse(
            label: .inbox,
            totalThreads: 2,
            nextCursor: nil,
            loadedThreads: 2,
            sections: [GmailThreadSection(id: "search", title: "Search", rows: [row])],
            fullImportRunning: false,
            fullImportCompleted: true
        )
        let store = InboxStore(
            client: SearchHydrationAppClient(searchResponses: [inconsistent]),
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")

        await store.load()
        await store.searchMailbox("invoice")

        XCTAssertFalse(store.searchInProgress)
        XCTAssertNotNil(store.searchError)
        XCTAssertNil(store.searchResults)
        XCTAssertFalse(store.mailboxPresentationReady)
        XCTAssertTrue(store.flatRows.isEmpty)
    }

    func testMailboxImportCompletionEventRerunsExactIncompleteVisibleSearch() async {
        let row = makeMailboxRow(
            threadID: "import-search-result",
            latestSourceRecordID: "import-search-message",
            receivedAt: "2026-07-24T12:00:00Z",
            title: "Imported search result"
        )
        let incomplete = MailboxResponse(
            label: .inbox,
            totalThreads: 1,
            nextCursor: nil,
            loadedThreads: 1,
            sections: [GmailThreadSection(id: "search", title: "Search", rows: [row])],
            mailboxRevision: "import-revision-1",
            fullImportRunning: true,
            fullImportCompleted: false
        )
        let complete = MailboxResponse(
            label: .inbox,
            totalThreads: 1,
            nextCursor: nil,
            loadedThreads: 1,
            sections: [GmailThreadSection(id: "search", title: "Search", rows: [row])],
            mailboxRevision: "import-revision-2",
            fullImportRunning: false,
            fullImportCompleted: true
        )
        let client = SearchHydrationAppClient(searchResponses: [incomplete, complete])
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")

        await store.load()
        await store.searchMailbox("  exact invoice  ")

        XCTAssertEqual(store.searchQuery, "exact invoice")
        XCTAssertFalse(store.mailboxPresentationReady)
        XCTAssertTrue(store.flatRows.isEmpty)
        XCTAssertNil(store.searchError)

        store.handleMailboxServerEvent(
            MailboxServerEvent(
                id: "import-complete-search",
                event: "mailbox-changed",
                data: #"{"mailbox_label":"inbox","payload":{"mailbox_revision":"import-revision-2"}}"#
            )
        )
        try? await Task.sleep(nanoseconds: 1_250_000_000)

        XCTAssertEqual(store.searchQuery, "exact invoice")
        XCTAssertEqual(store.flatRows.map(\.threadID), ["import-search-result"])
        XCTAssertTrue(store.mailboxPresentationReady)
        XCTAssertNil(store.searchError)
        XCTAssertEqual(client.searchCalls.map(\.query), ["exact invoice", "exact invoice"])
        XCTAssertEqual(client.searchCalls.map(\.cursor), [nil, nil])
    }

    func testSearchPaginationRevisionChurnRestartsFromPageOneThenPublishesCompleteResults() async {
        let firstRevision = makeSearchPaginationPage(
            threadID: "search-result-1",
            title: "First result",
            nextCursor: "search-cursor-2",
            revision: "search-revision-1"
        )
        let firstMismatch = makeSearchPaginationPage(
            threadID: "search-result-2",
            title: "Second result",
            nextCursor: nil,
            revision: "search-revision-2"
        )
        let secondRevision = makeSearchPaginationPage(
            threadID: "search-result-1",
            title: "First result",
            nextCursor: "search-cursor-2",
            revision: "search-revision-3"
        )
        let secondMismatch = makeSearchPaginationPage(
            threadID: "search-result-2",
            title: "Second result",
            nextCursor: nil,
            revision: "search-revision-4"
        )
        let stableFirstPage = makeSearchPaginationPage(
            threadID: "search-result-1",
            title: "First result",
            nextCursor: "search-cursor-2",
            revision: "search-revision-5"
        )
        let stableFinalPage = makeSearchPaginationPage(
            threadID: "search-result-2",
            title: "Second result",
            nextCursor: nil,
            revision: "search-revision-5"
        )
        let client = SearchHydrationAppClient(
            searchResponses: [
                firstRevision,
                firstMismatch,
                secondRevision,
                secondMismatch,
                stableFirstPage,
                stableFinalPage,
            ]
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")

        await store.load()
        await store.searchMailbox("  exact invoice  ")

        XCTAssertFalse(store.searchInProgress)
        XCTAssertNil(store.searchError)
        XCTAssertTrue(store.mailboxPresentationReady)
        XCTAssertEqual(store.flatRows.map(\.threadID), ["search-result-1", "search-result-2"])
        XCTAssertEqual(Set(store.flatRows.map(\.threadID)).count, 2)
        XCTAssertEqual(client.searchCalls.map(\.query), Array(repeating: "exact invoice", count: 6))
        XCTAssertEqual(
            client.searchCalls.map(\.cursor),
            [nil, "search-cursor-2", nil, "search-cursor-2", nil, "search-cursor-2"]
        )
    }

    func testSearchPaginationRevisionChurnExhaustionStaysLoadingWithoutPartialPresentation() async {
        var responses: [MailboxResponse] = []
        for attempt in 0..<4 {
            responses.append(
                makeSearchPaginationPage(
                    threadID: "search-result-1",
                    title: "First result",
                    nextCursor: "search-cursor-2",
                    revision: "search-first-revision-\(attempt)"
                )
            )
            responses.append(
                makeSearchPaginationPage(
                    threadID: "search-result-2",
                    title: "Second result",
                    nextCursor: nil,
                    revision: "search-mismatch-revision-\(attempt)"
                )
            )
        }
        let client = SearchHydrationAppClient(searchResponses: responses)
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")

        await store.load()
        await store.searchMailbox("invoice")

        XCTAssertFalse(store.searchInProgress)
        XCTAssertNil(store.searchError)
        XCTAssertNotNil(store.searchResults)
        XCTAssertFalse(store.mailboxPresentationReady)
        XCTAssertTrue(store.flatRows.isEmpty)
        XCTAssertEqual(client.searchCalls.filter { $0.cursor == nil }.count, 4)
        XCTAssertEqual(client.searchCalls.filter { $0.cursor == "search-cursor-2" }.count, 4)
    }

    func testImportCompletionWhileSearchIsRunningQueuesOneFollowUpWithoutCancellingOwner() async {
        let gate = ReaderActionRequestGate()
        let incomplete = MailboxResponse(
            label: .inbox,
            totalThreads: 1,
            nextCursor: nil,
            loadedThreads: 1,
            sections: [],
            mailboxRevision: "search-import-1",
            fullImportRunning: true,
            fullImportCompleted: false
        )
        let complete = makeSingleRowMailbox(threadID: "completed-search", title: "Completed search")
        let client = SearchHydrationAppClient(
            searchResponses: [incomplete, complete],
            initialSearchRequestGate: gate
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")
        await store.load()

        let initialSearch = Task { await store.searchMailbox("invoice") }
        await gate.waitUntilRequestStarts()

        await store.triggerMailboxSyncIfNeeded()

        XCTAssertTrue(store.searchInProgress)
        XCTAssertEqual(client.searchCalls.count, 1)

        await gate.releaseRequest()
        await initialSearch.value
        try? await Task.sleep(nanoseconds: 1_250_000_000)

        XCTAssertFalse(store.searchInProgress)
        XCTAssertNil(store.searchError)
        XCTAssertTrue(store.mailboxPresentationReady)
        XCTAssertEqual(store.flatRows.map(\.threadID), ["completed-search"])
        XCTAssertEqual(client.searchCalls.map(\.query), ["invoice", "invoice"])
        XCTAssertEqual(client.searchCalls.map(\.cursor), [nil, nil])
    }

    func testSearchHydratedSSEIgnoresStaleSearchKeyAfterQueryChanges() async {
        let invoice = makeSingleRowMailbox(threadID: "invoice", title: "Invoice")
        let receipt = makeSingleRowMailbox(threadID: "receipt", title: "Receipt")
        let client = SearchHydrationAppClient(searchResponses: [invoice, receipt])
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")

        await store.load()
        await store.searchMailbox("invoice")
        await store.searchMailbox("receipt")
        store.handleMailboxServerEvent(
            MailboxServerEvent(
                id: "search-stale",
                event: "mailbox-search-hydrated",
                data: #"{"payload":{"search_key":"9fb325127f025e4a05adad65c3dd226004fb3b6e659a83a39041f8f717cd2e66"}}"#
            )
        )
        try? await Task.sleep(nanoseconds: 30_000_000)

        XCTAssertEqual(store.searchQuery, "receipt")
        XCTAssertEqual(store.flatRows.map(\.title), ["Receipt"])
        XCTAssertEqual(client.searchCalls.count, 2)
    }

    func testSearchHydratedSSEDoesNothingAfterSearchIsCleared() async {
        let initial = makeSingleRowMailbox(threadID: "invoice", title: "Invoice")
        let client = SearchHydrationAppClient(searchResponses: [initial])
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")

        await store.load()
        await store.searchMailbox("invoice")
        store.clearSearch()
        store.handleMailboxServerEvent(
            MailboxServerEvent(
                id: "search-cleared",
                event: "mailbox-search-hydrated",
                data: #"{"payload":{"search_key":"9fb325127f025e4a05adad65c3dd226004fb3b6e659a83a39041f8f717cd2e66"}}"#
            )
        )
        try? await Task.sleep(nanoseconds: 30_000_000)

        XCTAssertFalse(store.isSearchActive)
        XCTAssertNil(store.searchResults)
        XCTAssertEqual(client.searchCalls.count, 1)
    }

    func testSearchHydrationRefreshCannotPublishAfterSearchIsCleared() async {
        let initial = makeSingleRowMailbox(threadID: "invoice", title: "Invoice")
        let hydrated = makeSingleRowMailbox(threadID: "stale-hydrated", title: "Stale hydrated result")
        let client = SearchHydrationAppClient(
            searchResponses: [initial, hydrated],
            suppressedResponseDelayNanoseconds: 180_000_000
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")

        await store.load()
        await store.searchMailbox("invoice")
        store.handleMailboxServerEvent(
            MailboxServerEvent(
                id: "search-race",
                event: "mailbox-search-hydrated",
                data: #"{"payload":{"search_key":"9fb325127f025e4a05adad65c3dd226004fb3b6e659a83a39041f8f717cd2e66"}}"#
            )
        )
        try? await Task.sleep(nanoseconds: 30_000_000)
        store.clearSearch()
        try? await Task.sleep(nanoseconds: 230_000_000)

        XCTAssertFalse(store.isSearchActive)
        XCTAssertNil(store.searchResults)
        XCTAssertEqual(client.searchCalls.last?.hydrateInBackground, false)
    }

    func testAttachmentFileHandlerSavesQuarantinesThenOpensOffMainActorWithinSecurityScope() async throws {
        let destination = URL(fileURLWithPath: "/tmp/electronic-mail-attachment.pdf")
        let payload = Data("attachment payload".utf8)
        let recorder = AttachmentOperationRecorder()
        var suggestedFilename: String?

        let handler = AttachmentFileHandler(
            chooseDestination: { filename in
                suggestedFilename = filename
                recorder.record("choose")
                return destination
            },
            startAccessingSecurityScope: { url in
                XCTAssertEqual(url, destination)
                recorder.recordFileOperation("start-access")
                return true
            },
            stopAccessingSecurityScope: { url in
                XCTAssertEqual(url, destination)
                recorder.recordFileOperation("stop-access")
            },
            writeData: { data, url in
                recorder.recordWrite(data: data, url: url)
            },
            quarantineFile: { url in
                XCTAssertEqual(url, destination)
                recorder.recordFileOperation("quarantine")
            },
            openFile: { url in
                XCTAssertEqual(url, destination)
                recorder.recordFileOperation("open")
                return true
            }
        )

        let result = try await handler.saveAndOpen(
            DownloadedAttachment(filename: "report.pdf", mimeType: "application/pdf", data: payload),
            suggestedFilename: "report.pdf"
        )

        XCTAssertEqual(result, .opened(destination))
        XCTAssertEqual(suggestedFilename, "report.pdf")
        XCTAssertEqual(recorder.writtenData, payload)
        XCTAssertEqual(recorder.writtenURL, destination)
        XCTAssertEqual(recorder.events, ["choose", "start-access", "write", "quarantine", "open", "stop-access"])
        XCTAssertEqual(recorder.fileOperationMainThreadFlags, [false, false, false, false, false])
    }

    func testAttachmentFileHandlerDestinationCancellationDoesNotWriteQuarantineOrOpen() async throws {
        let recorder = AttachmentOperationRecorder()
        let handler = AttachmentFileHandler(
            chooseDestination: { _ in nil },
            startAccessingSecurityScope: { _ in
                recorder.recordFileOperation("start-access")
                return true
            },
            stopAccessingSecurityScope: { _ in recorder.recordFileOperation("stop-access") },
            writeData: { _, _ in recorder.recordFileOperation("write") },
            quarantineFile: { _ in recorder.recordFileOperation("quarantine") },
            openFile: { _ in
                recorder.recordFileOperation("open")
                return true
            }
        )

        let result = try await handler.saveAndOpen(
            DownloadedAttachment(filename: "report.pdf", mimeType: nil, data: Data()),
            suggestedFilename: "report.pdf"
        )

        XCTAssertEqual(result, .cancelled)
        XCTAssertTrue(recorder.events.isEmpty)
    }

    func testAttachmentFileHandlerReportsOpenFailureAfterSavingAndQuarantiningAndReleasesScope() async {
        let destination = URL(fileURLWithPath: "/tmp/electronic-mail-attachment.pdf")
        let recorder = AttachmentOperationRecorder()
        let handler = AttachmentFileHandler(
            chooseDestination: { _ in destination },
            startAccessingSecurityScope: { _ in true },
            stopAccessingSecurityScope: { _ in recorder.recordFileOperation("stop-access") },
            writeData: { _, _ in recorder.recordFileOperation("write") },
            quarantineFile: { _ in recorder.recordFileOperation("quarantine") },
            openFile: { _ in
                recorder.recordFileOperation("open")
                return false
            }
        )

        do {
            _ = try await handler.saveAndOpen(
                DownloadedAttachment(filename: "report.pdf", mimeType: nil, data: Data()),
                suggestedFilename: "report.pdf"
            )
            XCTFail("Expected an attachment file handling error")
        } catch {
            guard let handlingError = error as? AttachmentFileHandlingError else {
                return XCTFail("Expected an attachment file handling error")
            }
            guard case .couldNotOpen = handlingError else {
                return XCTFail("Expected a could-not-open error")
            }
            XCTAssertTrue(error.localizedDescription.contains("was saved"))
        }
        XCTAssertEqual(recorder.events, ["write", "quarantine", "open", "stop-access"])
    }

    func testAttachmentFileHandlerDoesNotQuarantineOrOpenAfterWriteFailureAndReleasesScope() async {
        let destination = URL(fileURLWithPath: "/tmp/electronic-mail-attachment.pdf")
        let recorder = AttachmentOperationRecorder()
        let handler = AttachmentFileHandler(
            chooseDestination: { _ in destination },
            startAccessingSecurityScope: { _ in true },
            stopAccessingSecurityScope: { _ in recorder.recordFileOperation("stop-access") },
            writeData: { _, _ in
                recorder.recordFileOperation("write")
                throw AttachmentTestError.diskFull
            },
            quarantineFile: { _ in recorder.recordFileOperation("quarantine") },
            openFile: { _ in
                recorder.recordFileOperation("open")
                return true
            }
        )

        do {
            _ = try await handler.saveAndOpen(
                DownloadedAttachment(filename: "report.pdf", mimeType: nil, data: Data()),
                suggestedFilename: "report.pdf"
            )
            XCTFail("Expected an attachment file handling error")
        } catch {
            guard let handlingError = error as? AttachmentFileHandlingError else {
                return XCTFail("Expected an attachment file handling error")
            }
            guard case .couldNotSave = handlingError else {
                return XCTFail("Expected a could-not-save error")
            }
            XCTAssertTrue(error.localizedDescription.contains("Disk is full"))
        }
        XCTAssertEqual(recorder.events, ["write", "stop-access"])
    }

    func testAttachmentFileHandlerDoesNotOpenAfterQuarantineFailureAndReleasesScope() async {
        let destination = URL(fileURLWithPath: "/tmp/electronic-mail-attachment.pdf")
        let recorder = AttachmentOperationRecorder()
        let handler = AttachmentFileHandler(
            chooseDestination: { _ in destination },
            startAccessingSecurityScope: { _ in true },
            stopAccessingSecurityScope: { _ in recorder.recordFileOperation("stop-access") },
            writeData: { _, _ in recorder.recordFileOperation("write") },
            quarantineFile: { _ in
                recorder.recordFileOperation("quarantine")
                throw AttachmentTestError.quarantineDenied
            },
            openFile: { _ in
                recorder.recordFileOperation("open")
                return true
            }
        )

        do {
            _ = try await handler.saveAndOpen(
                DownloadedAttachment(filename: "report.pdf", mimeType: nil, data: Data()),
                suggestedFilename: "report.pdf"
            )
            XCTFail("Expected an attachment file handling error")
        } catch {
            guard let handlingError = error as? AttachmentFileHandlingError else {
                return XCTFail("Expected an attachment file handling error")
            }
            guard case .couldNotQuarantine = handlingError else {
                return XCTFail("Expected a could-not-quarantine error")
            }
            XCTAssertTrue(error.localizedDescription.contains("did not open"))
            XCTAssertTrue(error.localizedDescription.contains("Quarantine metadata was rejected"))
        }
        XCTAssertEqual(recorder.events, ["write", "quarantine", "stop-access"])
    }

    func testAttachmentFileHandlerTaskCancellationBeforeDestinationDoesNotPromptOrTouchFile() async {
        let recorder = AttachmentOperationRecorder()
        var didChooseDestination = false
        let handler = AttachmentFileHandler(
            chooseDestination: { _ in
                didChooseDestination = true
                return URL(fileURLWithPath: "/tmp/electronic-mail-attachment.pdf")
            },
            startAccessingSecurityScope: { _ in
                recorder.recordFileOperation("start-access")
                return true
            },
            stopAccessingSecurityScope: { _ in recorder.recordFileOperation("stop-access") },
            writeData: { _, _ in recorder.recordFileOperation("write") },
            quarantineFile: { _ in recorder.recordFileOperation("quarantine") },
            openFile: { _ in
                recorder.recordFileOperation("open")
                return true
            }
        )

        let task = Task {
            try await handler.saveAndOpen(
                DownloadedAttachment(filename: "report.pdf", mimeType: nil, data: Data()),
                suggestedFilename: "report.pdf"
            )
        }
        task.cancel()

        do {
            _ = try await task.value
            XCTFail("Expected cancellation")
        } catch is CancellationError {
            // Expected.
        } catch {
            XCTFail("Expected cancellation, got \(error)")
        }
        XCTAssertFalse(didChooseDestination)
        XCTAssertTrue(recorder.events.isEmpty)
    }

    func testAttachmentFileHandlerCancellationAfterWriteDoesNotQuarantineOrOpenAndReleasesScope() async {
        let destination = URL(fileURLWithPath: "/tmp/electronic-mail-attachment.pdf")
        let recorder = AttachmentOperationRecorder()
        let allowWriteToFinish = DispatchSemaphore(value: 0)
        let handler = AttachmentFileHandler(
            chooseDestination: { _ in destination },
            startAccessingSecurityScope: { _ in
                recorder.recordFileOperation("start-access")
                return true
            },
            stopAccessingSecurityScope: { _ in recorder.recordFileOperation("stop-access") },
            writeData: { _, _ in
                recorder.recordFileOperation("write")
                allowWriteToFinish.wait()
            },
            quarantineFile: { _ in recorder.recordFileOperation("quarantine") },
            openFile: { _ in
                recorder.recordFileOperation("open")
                return true
            }
        )

        let task = Task {
            try await handler.saveAndOpen(
                DownloadedAttachment(filename: "report.pdf", mimeType: nil, data: Data()),
                suggestedFilename: "report.pdf"
            )
        }
        for _ in 0..<1_000 where !recorder.events.contains("write") {
            try? await Task.sleep(nanoseconds: 1_000_000)
        }
        guard recorder.events.contains("write") else {
            allowWriteToFinish.signal()
            task.cancel()
            return XCTFail("The background writer did not start")
        }

        task.cancel()
        allowWriteToFinish.signal()
        do {
            _ = try await task.value
            XCTFail("Expected cancellation")
        } catch is CancellationError {
            // Expected.
        } catch {
            XCTFail("Expected cancellation, got \(error)")
        }
        XCTAssertEqual(recorder.events, ["start-access", "write", "stop-access"])
    }

    func testEmailAttachmentQuarantineWritesVerifiableMetadata() throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("ElectronicMailQuarantineTests-\(UUID().uuidString)", isDirectory: true)
        let fileURL = directory.appendingPathComponent("attachment.txt")
        defer { try? FileManager.default.removeItem(at: directory) }

        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        try Data("attachment".utf8).write(to: fileURL)
        try AttachmentFileQuarantine.apply(to: fileURL)

        let properties = try fileURL
            .resourceValues(forKeys: [.quarantinePropertiesKey])
            .quarantineProperties
        XCTAssertEqual(
            properties?[AttachmentFileQuarantine.typeKey] as? String,
            AttachmentFileQuarantine.emailAttachmentType
        )
    }

    func testOpenAttachmentDownloadsSanitizesSavesAndOpens() async {
        let payload = Data("launch attachment".utf8)
        let downloaded = DownloadedAttachment(
            filename: "folder/report:name.pdf",
            mimeType: "application/pdf",
            data: payload
        )
        let client = AttachmentDownloadingAppClient(downloadedAttachment: downloaded)
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral())
        )
        let attachment = makeThreadAttachment(filename: "original-name.pdf")
        let destination = URL(fileURLWithPath: "/tmp/saved-report.pdf")
        var suggestedFilename: String?
        let recorder = AttachmentOperationRecorder()
        let handler = AttachmentFileHandler(
            chooseDestination: { filename in
                suggestedFilename = filename
                return destination
            },
            startAccessingSecurityScope: { _ in false },
            stopAccessingSecurityScope: { _ in XCTFail("Inactive security scope must not be stopped") },
            writeData: { data, url in recorder.recordWrite(data: data, url: url) },
            quarantineFile: { _ in recorder.recordFileOperation("quarantine") },
            openFile: { url in
                recorder.recordOpenedURL(url)
                return true
            }
        )

        await store.openAttachment(attachment, messageID: "message-42", fileHandler: handler)

        XCTAssertEqual(client.requestedMessageID, "message-42")
        XCTAssertEqual(client.requestedAttachment, attachment)
        XCTAssertEqual(suggestedFilename, "folder_report_name.pdf")
        XCTAssertEqual(recorder.writtenData, payload)
        XCTAssertEqual(recorder.openedURL, destination)
        XCTAssertEqual(recorder.events, ["write", "quarantine", "open"])
        XCTAssertNil(store.attachmentErrorMessage)
    }

    func testOpenAttachmentPublishesProgressAndDeduplicatesRepeatedClicks() async {
        let gate = ReaderActionRequestGate()
        let client = AttachmentDownloadingAppClient(
            downloadedAttachment: DownloadedAttachment(
                filename: "report.pdf",
                mimeType: "application/pdf",
                data: Data("attachment".utf8)
            ),
            downloadGate: gate
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral())
        )
        let attachment = makeThreadAttachment(filename: "report.pdf")
        let handler = AttachmentFileHandler(
            chooseDestination: { _ in nil },
            startAccessingSecurityScope: { _ in false },
            stopAccessingSecurityScope: { _ in },
            writeData: { _, _ in },
            quarantineFile: { _ in },
            openFile: { _ in true }
        )

        let first = Task {
            await store.openAttachment(attachment, messageID: "message-42", fileHandler: handler)
        }
        await gate.waitUntilRequestStarts()

        XCTAssertTrue(store.isAttachmentDownloading(attachment, messageID: "message-42"))
        XCTAssertEqual(store.downloadingAttachmentIDs.count, 1)

        await store.openAttachment(attachment, messageID: "message-42", fileHandler: handler)
        XCTAssertEqual(client.downloadCallCount, 1)

        await gate.releaseRequest()
        await first.value

        XCTAssertFalse(store.isAttachmentDownloading(attachment, messageID: "message-42"))
        XCTAssertTrue(store.downloadingAttachmentIDs.isEmpty)
    }

    func testOpenAttachmentReportsDownloadFailureBeforeShowingSaveDestination() async {
        let store = InboxStore(
            client: FailingAppClient(statusCode: 503),
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral())
        )
        var didChooseDestination = false
        let handler = AttachmentFileHandler(
            chooseDestination: { _ in
                didChooseDestination = true
                return nil
            },
            startAccessingSecurityScope: { _ in false },
            stopAccessingSecurityScope: { _ in },
            writeData: { _, _ in },
            quarantineFile: { _ in },
            openFile: { _ in true }
        )

        await store.openAttachment(
            makeThreadAttachment(filename: "report.pdf"),
            messageID: "message-42",
            fileHandler: handler
        )

        XCTAssertFalse(didChooseDestination)
        XCTAssertTrue(store.attachmentErrorMessage?.contains("could not be downloaded") == true)
    }

    func testAttachmentForegroundDownloadCoalescesWithActivePrefetch() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("ElectronicMailAttachmentCoalescing-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let gate = ReaderActionRequestGate()
        let downloaded = DownloadedAttachment(
            filename: "report.pdf",
            mimeType: "application/pdf",
            data: Data("coalesced attachment".utf8),
            etag: "etag-1"
        )
        let backend = AttachmentDownloadingAppClient(downloadedAttachment: downloaded, downloadGate: gate)
        let cache = EncryptedAttachmentCache(rootURL: directory)
        let coordinator = AttachmentPrefetchCoordinator(backend: backend, cache: cache)
        let attachment = makeThreadAttachment(filename: "report.pdf")
        let thread = makeHydrationThread(body: "Body", bodyComplete: true, attachments: [attachment])

        await coordinator.enqueue(thread: thread, userID: DemoAppFixtures.userID, selected: false)
        await gate.waitUntilRequestStarts()
        let foreground = Task {
            try await coordinator.download(
                messageID: "message-1",
                attachment: attachment,
                userID: DemoAppFixtures.userID
            )
        }
        try? await Task.sleep(nanoseconds: 20_000_000)
        await gate.releaseRequest()

        let foregroundResult = try await foreground.value
        XCTAssertEqual(foregroundResult, downloaded)
        XCTAssertEqual(backend.downloadCallCount, 1)
        let cached = await cache.read(
            userID: DemoAppFixtures.userID,
            messageID: "message-1",
            attachmentID: attachment.attachmentID
        )
        XCTAssertEqual(cached, downloaded)
        await coordinator.reset(userID: DemoAppFixtures.userID)
        await cache.purge(userID: DemoAppFixtures.userID)
    }

    func testAutomaticAttachmentPrefetchRejectsActualBytesAboveFiveMegabytes() async {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("ElectronicMailAttachmentLimit-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let downloaded = DownloadedAttachment(
            filename: "deceptive.pdf",
            mimeType: "application/pdf",
            data: Data(count: EncryptedAttachmentCache.automaticFileLimit + 1),
            etag: "etag-large"
        )
        let backend = AttachmentDownloadingAppClient(downloadedAttachment: downloaded)
        let cache = EncryptedAttachmentCache(rootURL: directory)
        let coordinator = AttachmentPrefetchCoordinator(backend: backend, cache: cache)
        let attachment = makeThreadAttachment(filename: "deceptive.pdf")
        let thread = makeHydrationThread(body: "Body", bodyComplete: true, attachments: [attachment])

        await coordinator.enqueue(thread: thread, userID: DemoAppFixtures.userID, selected: false)
        for _ in 0..<100 where backend.downloadCallCount == 0 {
            try? await Task.sleep(nanoseconds: 2_000_000)
        }
        try? await Task.sleep(nanoseconds: 30_000_000)

        XCTAssertEqual(backend.downloadCallCount, 1)
        let cached = await cache.read(
            userID: DemoAppFixtures.userID,
            messageID: "message-1",
            attachmentID: attachment.attachmentID
        )
        XCTAssertNil(cached)
        await coordinator.reset(userID: DemoAppFixtures.userID)
        await cache.purge(userID: DemoAppFixtures.userID)
    }

    func testAttachmentResetAwaitsLateResponseAndPreventsCacheRecreation() async {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("ElectronicMailAttachmentReset-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let gate = ReaderActionRequestGate()
        let backend = AttachmentDownloadingAppClient(
            downloadedAttachment: DownloadedAttachment(
                filename: "late.pdf",
                mimeType: "application/pdf",
                data: Data("late bytes".utf8)
            ),
            downloadGate: gate
        )
        let cache = EncryptedAttachmentCache(rootURL: directory)
        let coordinator = AttachmentPrefetchCoordinator(backend: backend, cache: cache)
        let attachment = makeThreadAttachment(filename: "late.pdf")
        let thread = makeHydrationThread(body: "Body", bodyComplete: true, attachments: [attachment])

        await coordinator.enqueue(thread: thread, userID: DemoAppFixtures.userID, selected: false)
        await gate.waitUntilRequestStarts()
        let reset = Task {
            await coordinator.reset(userID: DemoAppFixtures.userID)
        }
        try? await Task.sleep(nanoseconds: 20_000_000)
        await gate.releaseRequest()
        await reset.value
        await cache.purge(userID: DemoAppFixtures.userID)
        try? await Task.sleep(nanoseconds: 20_000_000)

        let cached = await cache.read(
            userID: DemoAppFixtures.userID,
            messageID: "message-1",
            attachmentID: attachment.attachmentID
        )
        XCTAssertNil(cached)
    }

    func testForegroundAttachmentStillReturnsWhenCacheWriteFailsAndWarns() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("ElectronicMailAttachmentWarning-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let downloaded = DownloadedAttachment(
            filename: "report.pdf",
            mimeType: "application/pdf",
            data: Data("online bytes".utf8)
        )
        let backend = AttachmentDownloadingAppClient(downloadedAttachment: downloaded)
        let cache = EncryptedAttachmentCache(rootURL: directory, byteLimit: 0)
        let coordinator = AttachmentPrefetchCoordinator(backend: backend, cache: cache)
        let attachment = makeThreadAttachment(filename: "report.pdf")
        let warned = expectation(description: "Nonblocking attachment storage warning")
        let observer = NotificationCenter.default.addObserver(
            forName: .offlineContentSyncStorageWarning,
            object: nil,
            queue: .main
        ) { notification in
            if notification.userInfo?["user_id"] as? String == DemoAppFixtures.userID {
                warned.fulfill()
            }
        }
        defer { NotificationCenter.default.removeObserver(observer) }

        let result = try await coordinator.download(
            messageID: "message-1",
            attachment: attachment,
            userID: DemoAppFixtures.userID
        )

        XCTAssertEqual(result, downloaded)
        await fulfillment(of: [warned], timeout: 1)
        await coordinator.reset(userID: DemoAppFixtures.userID)
    }

    func testAttachmentDownloadCapIncludesForegroundPriority() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("ElectronicMailAttachmentConcurrency-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let backend = AttachmentDownloadingAppClient(
            downloadedAttachment: DownloadedAttachment(
                filename: "download.pdf",
                mimeType: "application/pdf",
                data: Data("downloaded".utf8)
            ),
            downloadDelayNanoseconds: 150_000_000
        )
        let cache = EncryptedAttachmentCache(rootURL: directory)
        let coordinator = AttachmentPrefetchCoordinator(
            backend: backend,
            cache: cache,
            maximumConcurrentDownloads: 2
        )
        let attachments = (1...3).map {
            makeThreadAttachment(filename: "report-\($0).pdf", index: $0)
        }
        let thread = makeHydrationThread(
            body: "Body",
            bodyComplete: true,
            attachments: attachments
        )

        await coordinator.enqueue(thread: thread, userID: DemoAppFixtures.userID, selected: false)
        for _ in 0..<300 where backend.currentDownloadCount < 2 {
            try? await Task.sleep(nanoseconds: 1_000_000)
        }
        XCTAssertEqual(backend.currentDownloadCount, 2)

        _ = try await coordinator.download(
            messageID: "message-1",
            attachment: attachments[2],
            userID: DemoAppFixtures.userID
        )

        XCTAssertEqual(backend.maximumObservedDownloadCount, 2)
        await coordinator.reset(userID: DemoAppFixtures.userID)
        await cache.purge(userID: DemoAppFixtures.userID)
    }

    func testOfflineRemoteImageCacheSurvivesRelaunchBeforeBackendVerification() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("ElectronicMailRemoteImageRelaunch-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let userID = DemoAppFixtures.userID
        let keyStore = AccountContentKeyStore(service: "app.electronicmail.tests.remote-relaunch.\(UUID().uuidString)")
        defer { keyStore.removeAllKeys() }
        let cache = EncryptedRemoteImageCache(rootURL: directory, keyStore: keyStore)
        let expected = Data("offline image".utf8)
        try await cache.write(
            CachedMediaPayload(data: expected, mimeType: "image/png", filename: nil, etag: "image-v1"),
            userID: userID,
            assetID: "asset-1"
        )
        let encryptedFiles = (FileManager.default.enumerator(
            at: directory,
            includingPropertiesForKeys: [.isRegularFileKey]
        )?.allObjects as? [URL] ?? []).filter {
            (try? $0.resourceValues(forKeys: [.isRegularFileKey]).isRegularFile) == true
        }
        XCTAssertFalse(encryptedFiles.isEmpty)
        for fileURL in encryptedFiles {
            let attributes = try FileManager.default.attributesOfItem(atPath: fileURL.path)
            let permissions = try XCTUnwrap(attributes[.posixPermissions] as? NSNumber)
            XCTAssertEqual(permissions.intValue & 0o777, 0o600, fileURL.path)
        }
        let loader = EmailRemoteImageLoader(cache: cache)
        let localStore = MemoryLocalMailStore()
        localStore.writeSession(DemoAppFixtures.appSession)
        let client = OfflineFirstAppClient(
            backend: DemoAppClient(),
            localMailStore: localStore,
            remoteImageLoader: loader
        )
        client.sessionToken = "restored-session-token"

        var loaded: LoadedRemoteImage?
        for _ in 0..<100 where loaded == nil {
            loaded = try? await loader.load(assetID: "asset-1")
            if loaded == nil {
                try? await Task.sleep(nanoseconds: 1_000_000)
            }
        }
        XCTAssertEqual(loaded?.data, expected)
        XCTAssertEqual(loaded?.mimeType, "image/png")

        do {
            _ = try await loader.load(assetID: "uncached-asset")
            XCTFail("A cache miss must wait for a backend-verified account")
        } catch let error as URLError {
            XCTAssertEqual(error.code, .userAuthenticationRequired)
        }
        await loader.purge(userID: userID)
    }

    func testRemoteImageConfigurationRevisionRejectsLateStaleConfiguration() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("ElectronicMailRemoteImageOrdering-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let recorder = RemoteImageRequestRecorder()
        RemoteImageURLProtocol.requestHandler = { request in
            recorder.record(request)
            return (
                HTTPURLResponse(
                    url: request.url!,
                    statusCode: 200,
                    httpVersion: nil,
                    headerFields: ["Content-Type": "image/png", "ETag": "image-v2"]
                )!,
                Data("network image".utf8)
            )
        }
        defer { RemoteImageURLProtocol.requestHandler = nil }
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [RemoteImageURLProtocol.self]
        let loader = EmailRemoteImageLoader(
            session: URLSession(configuration: configuration),
            cache: EncryptedRemoteImageCache(rootURL: directory)
        )
        await loader.configure(
            baseURL: URL(string: "https://mail.test")!,
            sessionToken: "verified-token",
            cacheUserID: "user-1",
            networkUserID: "user-1",
            configurationRevision: 2
        )
        await loader.configure(
            baseURL: URL(string: "https://stale.test")!,
            sessionToken: nil,
            cacheUserID: "user-1",
            networkUserID: nil,
            configurationRevision: 1
        )

        let loaded = try await loader.load(assetID: "asset-ordered")

        XCTAssertEqual(loaded.data, Data("network image".utf8))
        XCTAssertEqual(recorder.authorization, "Bearer verified-token")
        XCTAssertEqual(recorder.host, "mail.test")
        await loader.purge(userID: "user-1")
    }

    func testRemoteImageCacheHitIsCancelledWhenPurgeInterleavesWithRead() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("ElectronicMailRemoteImageReadRace-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let gate = ReaderActionRequestGate()
        let userID = "remote-race-\(UUID().uuidString)"
        let keyStore = AccountContentKeyStore(service: "app.electronicmail.tests.remote-race.\(UUID().uuidString)")
        defer { keyStore.removeAllKeys() }
        let cache = EncryptedRemoteImageCache(
            rootURL: directory,
            keyStore: keyStore,
            beforeReturningRead: { await gate.suspendRequest() }
        )
        try await cache.write(
            CachedMediaPayload(data: Data("secret".utf8), mimeType: "image/png", filename: nil, etag: nil),
            userID: userID,
            assetID: "asset-race"
        )
        let loader = EmailRemoteImageLoader(cache: cache)
        await loader.configure(
            baseURL: URL(string: "https://mail.test")!,
            sessionToken: nil,
            cacheUserID: userID,
            networkUserID: nil,
            configurationRevision: 1
        )

        let load = Task { try await loader.load(assetID: "asset-race") }
        await gate.waitUntilRequestStarts()
        await loader.purge(userID: userID)
        await gate.releaseRequest()

        do {
            _ = try await load.value
            XCTFail("Purged decrypted bytes must not escape to the reader")
        } catch is CancellationError {
            // Expected.
        }
    }

    func testAttachmentCacheHitIsCancelledWhenResetInterleavesWithRead() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("ElectronicMailAttachmentReadRace-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let gate = ReaderActionRequestGate()
        let userID = "attachment-race-\(UUID().uuidString)"
        let keyStore = AccountContentKeyStore(service: "app.electronicmail.tests.attachment-race.\(UUID().uuidString)")
        defer { keyStore.removeAllKeys() }
        let cache = EncryptedAttachmentCache(
            rootURL: directory,
            keyStore: keyStore,
            beforeReturningRead: { await gate.suspendRequest() }
        )
        let attachment = makeThreadAttachment(filename: "secret.pdf")
        try await cache.write(
            DownloadedAttachment(filename: "secret.pdf", mimeType: "application/pdf", data: Data("secret".utf8)),
            userID: userID,
            messageID: "message-race",
            attachmentID: attachment.attachmentID
        )
        let coordinator = AttachmentPrefetchCoordinator(
            backend: AttachmentDownloadingAppClient(
                downloadedAttachment: DownloadedAttachment(
                    filename: "network.pdf",
                    mimeType: "application/pdf",
                    data: Data("network".utf8)
                )
            ),
            cache: cache
        )

        let download = Task {
            try await coordinator.download(
                messageID: "message-race",
                attachment: attachment,
                userID: userID
            )
        }
        await gate.waitUntilRequestStarts()
        await coordinator.reset(userID: userID)
        await gate.releaseRequest()

        do {
            _ = try await download.value
            XCTFail("Reset must fence a decrypted cache hit")
        } catch is CancellationError {
            // Expected.
        }
        await cache.purge(userID: userID)
    }

    func testOpenAttachmentDoesNotPromptOrOpenAfterAccountInvalidation() async {
        let gate = ReaderActionRequestGate()
        let client = AttachmentDownloadingAppClient(
            downloadedAttachment: DownloadedAttachment(
                filename: "late.pdf",
                mimeType: "application/pdf",
                data: Data("late".utf8)
            ),
            downloadGate: gate
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral())
        )
        store.setSessionToken("live-session-token")
        let recorder = AttachmentOperationRecorder()
        var didChooseDestination = false
        let handler = AttachmentFileHandler(
            chooseDestination: { _ in
                didChooseDestination = true
                return URL(fileURLWithPath: "/tmp/late.pdf")
            },
            startAccessingSecurityScope: { _ in false },
            stopAccessingSecurityScope: { _ in },
            writeData: { data, url in recorder.recordWrite(data: data, url: url) },
            quarantineFile: { _ in recorder.recordFileOperation("quarantine") },
            openFile: { url in
                recorder.recordOpenedURL(url)
                return true
            }
        )
        let open = Task {
            await store.openAttachment(
                makeThreadAttachment(filename: "late.pdf"),
                messageID: "message-late",
                fileHandler: handler
            )
        }
        await gate.waitUntilRequestStarts()
        store.setSessionToken(nil)
        await gate.releaseRequest()
        await open.value

        XCTAssertFalse(didChooseDestination)
        XCTAssertTrue(recorder.events.isEmpty)
        XCTAssertNil(store.attachmentErrorMessage)
    }

    func testMailboxPageAccumulatorProcessesTenThousandRowsOnce() async {
        let total = 10_000
        let pageSize = 100
        let initial = makeLargeHistoryPage(offset: 0, total: total, pageSize: pageSize)
        let accumulator = MailboxPageAccumulator(initial)
        for offset in stride(from: pageSize, to: total, by: pageSize) {
            await accumulator.append(
                makeLargeHistoryPage(offset: offset, total: total, pageSize: pageSize)
            )
        }

        let snapshot = await accumulator.snapshot()
        let diagnostics = await accumulator.diagnostics()
        let rows = snapshot.sections.flatMap(\.rows)
        XCTAssertEqual(rows.count, total)
        XCTAssertEqual(Set(rows.map(\.threadID)).count, total)
        XCTAssertEqual(rows.first?.threadID, "large-0")
        XCTAssertEqual(rows.last?.threadID, "large-9999")
        XCTAssertEqual(diagnostics.processedRowCount, total)
        XCTAssertEqual(diagnostics.snapshotBuildCount, 1)
    }

    func testInterruptedGenerationRestorePrefersNewerSessionMailbox() async {
        let oldMailbox = makeGenerationMailbox(
            generation: "generation-a",
            revision: "revision-a",
            threadIDs: ["old-1", "old-2"],
            lastProgressAt: "2026-07-25T10:00:00Z"
        )
        let newMailbox = makeGenerationMailbox(
            generation: "generation-b",
            revision: "revision-b",
            threadIDs: ["new-1"],
            lastProgressAt: "2026-07-25T10:01:00Z"
        )
        let localStore = MemoryLocalMailStore()
        localStore.writeSession(makeProgressiveSession(mailbox: newMailbox))
        // Simulates process death after the session commit but before the
        // per-label mailbox row was replaced.
        localStore.writeMailbox(oldMailbox, userID: DemoAppFixtures.userID, label: .inbox)
        let store = InboxStore(
            client: FailingAppClient(statusCode: 503),
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            localMailStore: localStore,
            automaticallyPrefetchThreads: false
        )

        let restored = await store.restoreLocalCache()
        XCTAssertTrue(restored)
        XCTAssertEqual(store.flatRows.map(\.threadID), ["new-1"])
    }

    func testAppSessionMergeAcceptsNewGenerationConfirmedEmptyMailbox() {
        let currentMailbox = makeGenerationMailbox(
            generation: "generation-a",
            revision: "revision-a",
            threadIDs: ["old-1"],
            lastProgressAt: "2026-07-25T10:00:00Z"
        )
        let emptyMailbox = makeGenerationMailbox(
            generation: "generation-b",
            revision: "revision-b",
            threadIDs: [],
            lastProgressAt: "2026-07-25T10:01:00Z"
        )
        let cache = AppSessionCache(defaults: .ephemeral())

        let merged = cache.merge(
            current: makeProgressiveSession(mailbox: currentMailbox),
            next: makeProgressiveSession(mailbox: emptyMailbox)
        )

        XCTAssertEqual(merged.mailbox.syncGeneration, "generation-b")
        XCTAssertTrue(merged.mailbox.isEmpty)
    }

    func testOfflineFirstThreadResponseCannotRecreateBodyAfterTokenTransition() async throws {
        let threadGate = ReaderActionRequestGate()
        let backend = GatedThreadBatchAppClient(threadGate: threadGate)
        let localStore = MemoryLocalMailStore()
        localStore.writeSession(DemoAppFixtures.appSession)
        let client = OfflineFirstAppClient(backend: backend, localMailStore: localStore)
        client.sessionToken = "live-session-token"

        let request = Task {
            try await client.thread(threadID: "demo-google-today", limit: 50, offset: 0)
        }
        await threadGate.waitUntilRequestStarts()
        client.sessionToken = nil
        await threadGate.releaseRequest()

        do {
            _ = try await request.value
            XCTFail("A prior-account thread response must be cancelled")
        } catch is CancellationError {
            // Expected.
        }
        XCTAssertNil(
            localStore.readThread(userID: DemoAppFixtures.userID, threadID: "demo-google-today")
        )
    }

    func testOfflineFirstBatchResponseCannotRecreateBodiesAfterTokenTransition() async throws {
        let batchGate = ReaderActionRequestGate()
        let backend = GatedThreadBatchAppClient(batchGate: batchGate)
        let localStore = MemoryLocalMailStore()
        localStore.writeSession(DemoAppFixtures.appSession)
        let client = OfflineFirstAppClient(backend: backend, localMailStore: localStore)
        client.sessionToken = "live-session-token"

        let request = Task {
            try await client.batchThreads(threadIDs: ["demo-google-today"])
        }
        await batchGate.waitUntilRequestStarts()
        client.sessionToken = nil
        await batchGate.releaseRequest()

        do {
            _ = try await request.value
            XCTFail("A prior-account body batch must be cancelled")
        } catch is CancellationError {
            // Expected.
        }
        XCTAssertNil(
            localStore.readThread(userID: DemoAppFixtures.userID, threadID: "demo-google-today")
        )
    }

    func testMailboxChangedSSEPrunesSameGenerationLabelMove() async {
        let initial = makeGenerationMailbox(
            generation: "generation-shared",
            revision: "revision-1",
            threadIDs: ["remaining", "moved-out"],
            lastProgressAt: "2026-07-25T10:00:00Z"
        )
        let refreshed = makeGenerationMailbox(
            generation: "generation-shared",
            revision: "revision-2",
            threadIDs: ["remaining"],
            lastProgressAt: "2026-07-25T10:01:00Z"
        )
        let localStore = MemoryLocalMailStore()
        let client = RealtimeEventAppClient(
            sessionMailbox: initial,
            mailboxResponses: [initial, refreshed]
        )
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            localMailStore: localStore,
            automaticallyPrefetchThreads: false
        )
        await store.load()

        store.handleMailboxServerEvent(
            MailboxServerEvent(
                id: "label-move",
                event: "mailbox-changed",
                data: #"{"mailbox_label":"inbox","payload":{"mailbox_revision":"revision-2","mailbox_labels":["inbox"]}}"#
            )
        )
        try? await Task.sleep(nanoseconds: 1_250_000_000)

        XCTAssertEqual(store.flatRows.map(\.threadID), ["remaining"])
        XCTAssertEqual(
            localStore.readMailbox(userID: DemoAppFixtures.userID, label: .inbox)?
                .sections.flatMap(\.rows).map(\.threadID),
            ["remaining"]
        )
    }
}

private enum AttachmentTestError: LocalizedError {
    case diskFull
    case quarantineDenied

    var errorDescription: String? {
        switch self {
        case .diskFull:
            return "Disk is full."
        case .quarantineDenied:
            return "Quarantine metadata was rejected."
        }
    }
}

private final class AttachmentOperationRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private var storedEvents: [String] = []
    private var storedFileOperationMainThreadFlags: [Bool] = []
    private var storedWrittenData: Data?
    private var storedWrittenURL: URL?
    private var storedOpenedURL: URL?

    var events: [String] {
        withLock { storedEvents }
    }

    var fileOperationMainThreadFlags: [Bool] {
        withLock { storedFileOperationMainThreadFlags }
    }

    var writtenData: Data? {
        withLock { storedWrittenData }
    }

    var writtenURL: URL? {
        withLock { storedWrittenURL }
    }

    var openedURL: URL? {
        withLock { storedOpenedURL }
    }

    func record(_ event: String) {
        withLock {
            storedEvents.append(event)
        }
    }

    func recordFileOperation(_ event: String) {
        withLock {
            storedEvents.append(event)
            storedFileOperationMainThreadFlags.append(Thread.isMainThread)
        }
    }

    func recordWrite(data: Data, url: URL) {
        withLock {
            storedWrittenData = data
            storedWrittenURL = url
            storedEvents.append("write")
            storedFileOperationMainThreadFlags.append(Thread.isMainThread)
        }
    }

    func recordOpenedURL(_ url: URL) {
        withLock {
            storedOpenedURL = url
            storedEvents.append("open")
            storedFileOperationMainThreadFlags.append(Thread.isMainThread)
        }
    }

    @discardableResult
    private func withLock<T>(_ body: () -> T) -> T {
        lock.lock()
        defer { lock.unlock() }
        return body()
    }
}

private func makeThreadAttachment(filename: String, index: Int = 1) -> ThreadAttachment {
    ThreadAttachment(
        id: "attachment-\(index)",
        filename: filename,
        mimeType: "application/pdf",
        size: 42,
        attachmentID: "gmail-attachment-\(index)",
        partID: "part-\(index)",
        downloadURL: nil
    )
}

private func makeMailboxRow(
    threadID: String,
    entityID: String? = nil,
    latestSourceRecordID: String,
    receivedAt: String,
    title: String,
    messageCount: Int? = nil,
    bodyReady: Bool? = nil,
    contentRevision: String? = nil,
    initialWindowPosition: Int? = nil,
    lifecycleSourceIDs: [String]? = nil,
    children: [GmailThreadChildRow]? = nil,
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
        messageCount: messageCount ?? updates.count,
        bodyReady: bodyReady,
        contentRevision: contentRevision,
        initialWindowPosition: initialWindowPosition,
        summary: title,
        snippet: title,
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

private func makeConversationMailbox(
    row: GmailThreadRow,
    label: MailboxLabel = .inbox
) -> MailboxResponse {
    MailboxResponse(
        label: label,
        totalThreads: 1,
        loadedThreads: 1,
        sections: [GmailThreadSection(id: "today", title: "Today", rows: [row])],
        fullImportRunning: false,
        fullImportCompleted: true
    )
}

private func makeConversationThread(
    threadID: String,
    messages: [ThreadMessage],
    totalMessages: Int? = nil,
    hasMore: Bool = false,
    contentRevision: String? = "conversation-revision"
) -> ThreadReaderResponse {
    ThreadReaderResponse(
        entityID: threadID,
        userID: DemoAppFixtures.userID,
        source: .gmail,
        gmailThreadID: threadID,
        subject: messages.last?.subject,
        totalMessages: totalMessages ?? messages.count,
        limit: max(1, messages.count),
        offset: 0,
        hasMore: hasMore,
        messages: messages,
        contentRevision: contentRevision
    )
}

private func makeConversationMessage(
    id: String,
    threadID: String,
    receivedAt: String,
    fromAddress: String = "Sender <sender@example.com>",
    to: String = "Me <me@example.com>",
    labelIDs: [String] = ["INBOX"]
) -> ThreadMessage {
    ThreadMessage(
        id: id,
        source: .gmail,
        threadID: threadID,
        fromAddress: fromAddress,
        to: to,
        cc: nil,
        bcc: nil,
        subject: "Subject \(id)",
        body: "Body \(id)",
        htmlBody: nil,
        htmlRenderDocument: nil,
        snippet: "Snippet \(id)",
        labelIDs: labelIDs,
        receivedAt: receivedAt
    )
}

private func makeConversationChild(
    id: String,
    threadID: String,
    receivedAt: String,
    sender: String = "Sender <sender@example.com>",
    labelIDs: [String] = ["INBOX"]
) -> GmailThreadChildRow {
    GmailThreadChildRow(
        messageID: id,
        gmailThreadID: threadID,
        sender: sender,
        subject: "Subject \(id)",
        snippet: "Snippet \(id)",
        receivedAt: receivedAt,
        labelIDs: labelIDs,
        labels: labelIDs,
        unread: labelIDs.contains("UNREAD")
    )
}

private func makeOversizedOfflineMailbox(threadID: String) -> MailboxResponse {
    MailboxResponse(
        label: .all,
        totalThreads: 1,
        loadedThreads: 1,
        sections: [
            GmailThreadSection(
                id: "all",
                title: "All Mail",
                rows: [
                    makeMailboxRow(
                        threadID: threadID,
                        latestSourceRecordID: "\(threadID)-message",
                        receivedAt: "2026-07-25T12:00:00Z",
                        title: "Oversized offline thread",
                        bodyReady: true,
                        contentRevision: "sha256:stable",
                        initialWindowPosition: 30
                    )
                ]
            )
        ],
        fullImportRunning: true,
        fullImportCompleted: false
    )
}

private func makeProgressiveMailbox(
    metadataCount: Int,
    bodyReadyCount: Int,
    estimatedTotalCount: Int,
    initialWindowComplete: Bool = false,
    visibleRowCount: Int? = nil,
    reportsEstimatedTotal: Bool = true
) -> MailboxResponse {
    let safeMetadataCount = max(0, metadataCount)
    let safeBodyReadyCount = max(0, min(bodyReadyCount, safeMetadataCount))
    let safeVisibleRowCount = max(0, min(visibleRowCount ?? safeMetadataCount, safeMetadataCount))
    let rows = (0..<safeVisibleRowCount).map { index in
        makeMailboxRow(
            threadID: "progressive-\(index)",
            latestSourceRecordID: "progressive-message-\(index)",
            receivedAt: "2026-07-25T12:00:00Z",
            title: "Progressive message \(index)",
            bodyReady: index < safeBodyReadyCount,
            contentRevision: "sha256:progressive-\(index)"
        )
    }
    let initialTarget = min(100, max(0, estimatedTotalCount))
    let bodyTarget = min(25, max(0, estimatedTotalCount))
    return MailboxResponse(
        label: .inbox,
        totalThreads: max(0, estimatedTotalCount),
        loadedThreads: safeMetadataCount,
        sections: [GmailThreadSection(id: "progressive", title: "Recent", rows: rows)],
        readyCount: safeBodyReadyCount,
        pendingCount: max(0, bodyTarget - safeBodyReadyCount),
        mailboxRevision: "progressive-revision-1",
        fullImportRunning: true,
        fullImportCompleted: false,
        syncGeneration: "progressive-generation-1",
        phase: initialWindowComplete ? "usable" : "hydrating_priority_content",
        initialTargetCount: initialTarget,
        initialMetadataCount: safeMetadataCount,
        initialBodyTargetCount: bodyTarget,
        initialBodyReadyCount: safeBodyReadyCount,
        historyMetadataCount: 0,
        historyBodyReadyCount: 0,
        estimatedTotalCount: reportsEstimatedTotal ? max(0, estimatedTotalCount) : nil,
        initialWindowComplete: initialWindowComplete,
        historyMetadataComplete: false,
        historyBodyComplete: false,
        lastProgressAt: "2026-07-25T12:00:00Z"
    )
}

private func makeProgressiveSession(mailbox: MailboxResponse) -> AppSessionResponse {
    let base = DemoAppFixtures.appSession
    let readiness = PostLoginReadinessResponse(
        mode: "progressive",
        stage: mailbox.phase ?? "starting",
        readyToEnter: false,
        dashboardReady: true,
        mailboxReady: false,
        readyDashboardCount: 0,
        readyMailGroupCount: mailbox.initialBodyReadyCount ?? 0,
        fullImportRunning: true,
        fullImportCompleted: false,
        userDisplayName: base.user.displayName,
        errorMessage: nil,
        syncGeneration: mailbox.syncGeneration,
        phase: mailbox.phase,
        initialTargetCount: mailbox.initialTargetCount,
        initialMetadataCount: mailbox.initialMetadataCount,
        initialBodyTargetCount: mailbox.initialBodyTargetCount,
        initialBodyReadyCount: mailbox.initialBodyReadyCount,
        historyMetadataCount: mailbox.historyMetadataCount,
        historyBodyReadyCount: mailbox.historyBodyReadyCount,
        estimatedTotalCount: mailbox.estimatedTotalCount,
        initialWindowComplete: mailbox.initialWindowComplete,
        historyMetadataComplete: mailbox.historyMetadataComplete,
        historyBodyComplete: mailbox.historyBodyComplete,
        lastProgressAt: mailbox.lastProgressAt
    )
    return AppSessionResponse(
        user: base.user,
        readiness: readiness,
        dashboard: base.dashboard,
        mailbox: mailbox,
        sync: base.sync
    )
}

private func makeGenerationMailbox(
    generation: String,
    revision: String,
    threadIDs: [String],
    lastProgressAt: String
) -> MailboxResponse {
    let rows = threadIDs.enumerated().map { index, threadID in
        makeMailboxRow(
            threadID: threadID,
            latestSourceRecordID: "\(threadID)-message",
            receivedAt: "2026-07-25T10:00:\(String(format: "%02d", index))Z",
            title: threadID,
            bodyReady: true,
            contentRevision: "sha256:\(threadID)"
        )
    }
    let initialTarget = min(100, rows.count)
    let bodyTarget = min(25, rows.count)
    return MailboxResponse(
        label: .inbox,
        totalThreads: rows.count,
        loadedThreads: rows.count,
        sections: rows.isEmpty ? [] : [GmailThreadSection(id: "history", title: "History", rows: rows)],
        readyCount: rows.count,
        pendingCount: 0,
        mailboxRevision: revision,
        fullImportRunning: false,
        fullImportCompleted: true,
        syncGeneration: generation,
        phase: "complete",
        initialTargetCount: initialTarget,
        initialMetadataCount: initialTarget,
        initialBodyTargetCount: bodyTarget,
        initialBodyReadyCount: bodyTarget,
        historyMetadataCount: rows.count,
        historyBodyReadyCount: rows.count,
        estimatedTotalCount: rows.count,
        initialWindowComplete: true,
        historyMetadataComplete: true,
        historyBodyComplete: true,
        lastProgressAt: lastProgressAt
    )
}

private func makeLargeHistoryPage(offset: Int, total: Int, pageSize: Int) -> MailboxResponse {
    let count = min(pageSize, total - offset)
    let rows = (offset..<(offset + count)).map { index in
        makeMailboxRow(
            threadID: "large-\(index)",
            latestSourceRecordID: "large-message-\(index)",
            receivedAt: "2026-07-25T10:00:00Z",
            title: "Large history \(index)"
        )
    }
    let nextOffset = offset + count
    return MailboxResponse(
        label: .all,
        totalThreads: total,
        nextCursor: nextOffset < total ? "large-cursor-\(nextOffset)" : nil,
        loadedThreads: count,
        sections: [GmailThreadSection(id: "history", title: "History", rows: rows)],
        mailboxRevision: "large-revision",
        fullImportRunning: nextOffset < total,
        fullImportCompleted: nextOffset >= total,
        syncGeneration: "large-generation",
        historyMetadataCount: nextOffset,
        estimatedTotalCount: total,
        historyMetadataComplete: nextOffset >= total,
        lastProgressAt: "2026-07-25T10:00:00Z"
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

private extension MailboxResponse {
    func withTestMailboxRevision(_ revision: String) -> MailboxResponse {
        MailboxResponse(
            label: label,
            totalThreads: totalThreads,
            unreadThreads: unreadThreads,
            nextCursor: nextCursor,
            loadedThreads: loadedThreads,
            windowDays: windowDays,
            sections: sections,
            readyCount: readyCount,
            pendingCount: pendingCount,
            mailboxRevision: revision,
            generatedAt: generatedAt,
            oldestImportedAt: oldestImportedAt,
            fullImportRunning: fullImportRunning,
            fullImportCompleted: fullImportCompleted,
            syncGeneration: syncGeneration,
            phase: phase,
            initialTargetCount: initialTargetCount,
            initialMetadataCount: initialMetadataCount,
            initialBodyTargetCount: initialBodyTargetCount,
            initialBodyReadyCount: initialBodyReadyCount,
            historyMetadataCount: historyMetadataCount,
            historyBodyReadyCount: historyBodyReadyCount,
            estimatedTotalCount: estimatedTotalCount,
            initialWindowComplete: initialWindowComplete,
            historyMetadataComplete: historyMetadataComplete,
            historyBodyComplete: historyBodyComplete,
            lastProgressAt: lastProgressAt
        )
    }
}

private func makeSearchPaginationPage(
    threadID: String,
    title: String,
    nextCursor: String?,
    revision: String
) -> MailboxResponse {
    let row = makeMailboxRow(
        threadID: threadID,
        latestSourceRecordID: "\(threadID)-message",
        receivedAt: "2026-07-24T12:00:00Z",
        title: title
    )
    return MailboxResponse(
        label: .inbox,
        totalThreads: 2,
        nextCursor: nextCursor,
        loadedThreads: 1,
        sections: [GmailThreadSection(id: "search", title: "Search", rows: [row])],
        mailboxRevision: revision,
        fullImportRunning: false,
        fullImportCompleted: true
    )
}

private func makeEmptyMailbox(
    label: MailboxLabel,
    totalThreads: Int = 0,
    unreadThreads: Int = 0
) -> MailboxResponse {
    let rows = (0..<totalThreads).map { index in
        makeMailboxRow(
            threadID: "\(label.rawValue)-complete-\(index)",
            latestSourceRecordID: "\(label.rawValue)-message-\(index)",
            receivedAt: "2026-07-24T12:00:00Z",
            title: "\(label.rawValue) message \(index)",
            labelIDs: label == .sent ? ["SENT"] : ["INBOX"],
            labels: label == .sent ? ["SENT"] : ["INBOX"]
        )
    }
    return MailboxResponse(
        label: label,
        totalThreads: totalThreads,
        unreadThreads: unreadThreads,
        loadedThreads: totalThreads,
        sections: rows.isEmpty ? [] : [GmailThreadSection(id: "complete", title: "Complete", rows: rows)],
        fullImportRunning: false,
        fullImportCompleted: true
    )
}

private func makeMailboxSyncStateResponse(
    connected: Bool,
    mailboxRevision: String?
) -> MailboxSyncStateResponse {
    var state = MailboxSyncStateResponse(
        connected: connected,
        lastHistoryID: connected ? "history-sse" : nil,
        lastFullSyncAt: connected ? "2026-07-23T12:00:00Z" : nil,
        watchExpirationAt: connected ? "2026-07-23T13:00:00Z" : nil,
        lastSyncStartedAt: "2026-07-23T11:59:59Z",
        lastSyncCompletedAt: connected ? "2026-07-23T12:00:00Z" : nil,
        lastSyncError: connected ? nil : "Google is not connected.",
        totalThreads: 1
    )
    state.mailboxRevision = mailboxRevision
    return state
}

private func makeHydrationThread(
    body: String,
    bodyComplete: Bool,
    threadID: String = "hydrating-thread",
    attachments: [ThreadAttachment] = []
) -> ThreadReaderResponse {
    ThreadReaderResponse(
        entityID: threadID,
        userID: DemoAppFixtures.userID,
        source: .gmail,
        gmailThreadID: threadID,
        subject: "Hydration test",
        totalMessages: 1,
        messages: [
            ThreadMessage(
                id: "message-1",
                source: .gmail,
                threadID: threadID,
                fromAddress: "sender@example.com",
                to: "gaurav@example.com",
                cc: nil,
                bcc: nil,
                subject: "Hydration test",
                body: body,
                bodyComplete: bodyComplete,
                htmlBody: nil,
                htmlRenderDocument: nil,
                snippet: "Metadata snippet",
                attachments: attachments,
                labelIDs: ["INBOX"],
                receivedAt: "2026-05-29T09:30:00+05:30"
            )
        ]
    )
}

private func makeReaderActionThread(threadID: String, messageID: String, labelIDs: [String]) -> ThreadReaderResponse {
    ThreadReaderResponse(
        entityID: threadID,
        userID: DemoAppFixtures.userID,
        source: .gmail,
        gmailThreadID: threadID,
        subject: "Reader action",
        totalMessages: 1,
        messages: [
            ThreadMessage(
                id: messageID,
                source: .gmail,
                threadID: threadID,
                fromAddress: "sender@example.com",
                to: "gaurav@example.com",
                cc: nil,
                bcc: nil,
                subject: "Reader action",
                body: "Reader action body",
                htmlBody: nil,
                htmlRenderDocument: nil,
                snippet: "Reader action body",
                labelIDs: labelIDs,
                receivedAt: "2026-05-29T09:30:00+05:30"
            )
        ]
    )
}

private actor ReaderActionRequestGate {
    private var requestStarted = false
    private var startWaiters: [CheckedContinuation<Void, Never>] = []
    private var releaseContinuation: CheckedContinuation<Void, Never>?

    func suspendRequest() async {
        requestStarted = true
        startWaiters.forEach { $0.resume() }
        startWaiters = []
        await withCheckedContinuation { continuation in
            releaseContinuation = continuation
        }
    }

    func waitUntilRequestStarts() async {
        guard !requestStarted else {
            return
        }
        await withCheckedContinuation { continuation in
            startWaiters.append(continuation)
        }
    }

    func releaseRequest() {
        releaseContinuation?.resume()
        releaseContinuation = nil
    }
}

private final class RemoteImageRequestRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private var storedAuthorization: String?
    private var storedHost: String?

    var authorization: String? {
        lock.lock()
        defer { lock.unlock() }
        return storedAuthorization
    }

    var host: String? {
        lock.lock()
        defer { lock.unlock() }
        return storedHost
    }

    func record(_ request: URLRequest) {
        lock.lock()
        storedAuthorization = request.value(forHTTPHeaderField: "Authorization")
        storedHost = request.url?.host
        lock.unlock()
    }
}

private final class RemoteImageURLProtocol: URLProtocol {
    private static let lock = NSLock()
    private static var storedRequestHandler: ((URLRequest) throws -> (HTTPURLResponse, Data))?

    static var requestHandler: ((URLRequest) throws -> (HTTPURLResponse, Data))? {
        get {
            lock.lock()
            defer { lock.unlock() }
            return storedRequestHandler
        }
        set {
            lock.lock()
            storedRequestHandler = newValue
            lock.unlock()
        }
    }

    override class func canInit(with request: URLRequest) -> Bool {
        true
    }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest {
        request
    }

    override func startLoading() {
        guard let handler = Self.requestHandler else {
            client?.urlProtocol(self, didFailWithError: URLError(.badServerResponse))
            return
        }
        do {
            let (response, data) = try handler(request)
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}
}

private final class GatedThreadBatchAppClient: AppClient {
    var baseURL = AppConfiguration.defaultBackendURL
    var sessionToken: String?
    let mode: AppRunMode = .demo

    private let demo = DemoAppClient()
    private let threadGate: ReaderActionRequestGate?
    private let batchGate: ReaderActionRequestGate?

    init(
        threadGate: ReaderActionRequestGate? = nil,
        batchGate: ReaderActionRequestGate? = nil
    ) {
        self.threadGate = threadGate
        self.batchGate = batchGate
    }

    func exchangeMobileSession(loginCode: String) async throws -> MobileSessionExchangeResponse {
        try await demo.exchangeMobileSession(loginCode: loginCode)
    }

    func appSession() async throws -> AppSessionResponse {
        try await demo.appSession()
    }

    func mailbox(label: MailboxLabel, limit: Int, cursor: String?) async throws -> MailboxResponse {
        try await demo.mailbox(label: label, limit: limit, cursor: cursor)
    }

    func thread(threadID: String, limit: Int, offset: Int) async throws -> ThreadReaderResponse {
        if let threadGate {
            await threadGate.suspendRequest()
        }
        return try await demo.thread(threadID: threadID, limit: limit, offset: offset)
    }

    func batchThreads(threadIDs: [String]) async throws -> MailboxThreadBatchResponse {
        guard let batchGate else {
            return MailboxThreadBatchResponse(threads: [], pendingThreadIDs: threadIDs)
        }
        await batchGate.suspendRequest()
        let threads = try await withThrowingTaskGroup(of: ThreadReaderResponse.self) { group in
            for threadID in threadIDs {
                group.addTask { [demo] in
                    try await demo.thread(threadID: threadID, limit: 100, offset: 0)
                }
            }
            var results: [ThreadReaderResponse] = []
            for try await thread in group {
                results.append(thread)
            }
            return results
        }
        return MailboxThreadBatchResponse(threads: threads)
    }

    func triggerMailboxSync() async throws -> MailboxSyncTriggerResponse {
        try await demo.triggerMailboxSync()
    }

    func syncMailboxNow() async throws -> MailboxSyncTriggerResponse {
        try await demo.syncMailboxNow()
    }

    func archiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        try await demo.archiveThread(threadID)
    }

    func unarchiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        try await demo.unarchiveThread(threadID)
    }

    func markThreadRead(_ threadID: String) async throws -> GmailThreadMutationResponse {
        try await demo.markThreadRead(threadID)
    }

    func enqueueThreadAction(_ request: QueuedThreadActionRequest) async throws -> QueuedThreadActionResponse {
        try await demo.enqueueThreadAction(request)
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

private final class BlockingClearAllLocalMailStore: LocalMailStore, @unchecked Sendable {
    private let base = MemoryLocalMailStore()
    private let purgeStarted: XCTestExpectation
    private let purgeFinished: XCTestExpectation
    private let purgeRelease = DispatchSemaphore(value: 0)
    private let lock = NSLock()
    private var purgeUsedMainThread = false
    private var sessionReads = 0

    init(purgeStarted: XCTestExpectation, purgeFinished: XCTestExpectation) {
        self.purgeStarted = purgeStarted
        self.purgeFinished = purgeFinished
    }

    var purgeRanOnMainThread: Bool {
        lock.withLock { purgeUsedMainThread }
    }

    var readSessionCallCount: Int {
        lock.withLock { sessionReads }
    }

    func releasePurge() {
        purgeRelease.signal()
    }

    func readSession() -> AppSessionResponse? {
        lock.withLock { sessionReads += 1 }
        return base.readSession()
    }

    func writeSession(_ session: AppSessionResponse) {
        base.writeSession(session)
    }

    func readMailbox(userID: String, label: MailboxLabel) -> MailboxResponse? {
        base.readMailbox(userID: userID, label: label)
    }

    func writeMailbox(_ mailbox: MailboxResponse, userID: String, label: MailboxLabel) {
        base.writeMailbox(mailbox, userID: userID, label: label)
    }

    func readThread(userID: String, threadID: String) -> ThreadReaderResponse? {
        base.readThread(userID: userID, threadID: threadID)
    }

    func writeThread(_ thread: ThreadReaderResponse, userID: String, threadID: String) {
        base.writeThread(thread, userID: userID, threadID: threadID)
    }

    func removeThread(userID: String, threadID: String) {
        base.removeThread(userID: userID, threadID: threadID)
    }

    func purgeAccount(userID: String) {
        base.purgeAccount(userID: userID)
    }

    func writePendingThreadAction(_ action: LocalPendingThreadAction) {
        base.writePendingThreadAction(action)
    }

    func pendingThreadActions() -> [LocalPendingThreadAction] {
        base.pendingThreadActions()
    }

    func removePendingThreadAction(clientActionID: String) {
        base.removePendingThreadAction(clientActionID: clientActionID)
    }

    func markPendingThreadActionFailed(clientActionID: String, error: String) {
        base.markPendingThreadActionFailed(clientActionID: clientActionID, error: error)
    }

    func clearSession() {
        base.clearSession()
    }

    func clearAll() {
        lock.withLock { purgeUsedMainThread = Thread.isMainThread }
        purgeStarted.fulfill()
        purgeRelease.wait()
        base.clearAll()
        purgeFinished.fulfill()
    }
}

private final class BlockingThreadWriteLocalMailStore: LocalMailStore {
    private let base: LocalMailStore
    private let lock = NSLock()
    private let releaseSemaphore = DispatchSemaphore(value: 0)
    private let threadReadReleaseSemaphore = DispatchSemaphore(value: 0)
    private let blockOnThreadWriteCall: Int
    private let blockOnThreadReadCall: Int?
    private var threadWriteCallCount = 0
    private var threadReadCallCount = 0
    private var blockedThreadWrite = false
    private var blockedThreadRead = false

    init(
        blockOnThreadWriteCall: Int,
        blockOnThreadReadCall: Int? = nil,
        base: LocalMailStore = MemoryLocalMailStore()
    ) {
        self.blockOnThreadWriteCall = blockOnThreadWriteCall
        self.blockOnThreadReadCall = blockOnThreadReadCall
        self.base = base
    }

    var hasBlockedThreadWrite: Bool {
        lock.lock()
        defer { lock.unlock() }
        return blockedThreadWrite
    }

    var hasBlockedThreadRead: Bool {
        lock.lock()
        defer { lock.unlock() }
        return blockedThreadRead
    }

    var recordedThreadReadCallCount: Int {
        lock.lock()
        defer { lock.unlock() }
        return threadReadCallCount
    }

    func resetThreadReadCallCount() {
        lock.lock()
        threadReadCallCount = 0
        lock.unlock()
    }

    func releaseBlockedThreadWrite() {
        releaseSemaphore.signal()
    }

    func releaseBlockedThreadRead() {
        threadReadReleaseSemaphore.signal()
    }

    func readSession() -> AppSessionResponse? {
        base.readSession()
    }

    func writeSession(_ session: AppSessionResponse) {
        base.writeSession(session)
    }

    func readMailbox(userID: String, label: MailboxLabel) -> MailboxResponse? {
        base.readMailbox(userID: userID, label: label)
    }

    func writeMailbox(_ mailbox: MailboxResponse, userID: String, label: MailboxLabel) {
        base.writeMailbox(mailbox, userID: userID, label: label)
    }

    func writeMailbox(
        _ mailbox: MailboxResponse,
        userID: String,
        label: MailboxLabel,
        removingThreadIDs: [String],
        reenteringThreadIDs: [String],
        reentryLabels: [MailboxLabel]
    ) {
        base.writeMailbox(
            mailbox,
            userID: userID,
            label: label,
            removingThreadIDs: removingThreadIDs,
            reenteringThreadIDs: reenteringThreadIDs,
            reentryLabels: reentryLabels
        )
    }

    func readThread(userID: String, threadID: String) -> ThreadReaderResponse? {
        lock.lock()
        threadReadCallCount += 1
        let shouldBlock = threadReadCallCount == blockOnThreadReadCall
        if shouldBlock {
            blockedThreadRead = true
        }
        lock.unlock()

        if shouldBlock {
            threadReadReleaseSemaphore.wait()
        }
        return base.readThread(userID: userID, threadID: threadID)
    }

    func writeThread(_ thread: ThreadReaderResponse, userID: String, threadID: String) {
        lock.lock()
        threadWriteCallCount += 1
        let shouldBlock = threadWriteCallCount == blockOnThreadWriteCall
        if shouldBlock {
            blockedThreadWrite = true
        }
        lock.unlock()

        if shouldBlock {
            releaseSemaphore.wait()
        }
        base.writeThread(thread, userID: userID, threadID: threadID)
    }

    func writePendingThreadAction(_ action: LocalPendingThreadAction) {
        base.writePendingThreadAction(action)
    }

    func removeThread(userID: String, threadID: String) {
        base.removeThread(userID: userID, threadID: threadID)
    }

    func purgeAccount(userID: String) {
        base.purgeAccount(userID: userID)
    }

    func pendingThreadActions() -> [LocalPendingThreadAction] {
        base.pendingThreadActions()
    }

    func removePendingThreadAction(clientActionID: String) {
        base.removePendingThreadAction(clientActionID: clientActionID)
    }

    func markPendingThreadActionFailed(clientActionID: String, error: String) {
        base.markPendingThreadActionFailed(clientActionID: clientActionID, error: error)
    }

    func clearSession() {
        base.clearSession()
    }

    func clearAll() {
        base.clearAll()
    }
}

private final class BlockingMailboxWriteLocalMailStore: LocalMailStore {
    private let base: LocalMailStore
    private let lock = NSLock()
    private let releaseSemaphore = DispatchSemaphore(value: 0)
    private var blockedMailboxWrite = false
    private var didBlockMailboxWrite = false

    init(base: LocalMailStore) {
        self.base = base
    }

    var hasBlockedMailboxWrite: Bool {
        lock.lock()
        defer { lock.unlock() }
        return blockedMailboxWrite
    }

    func releaseBlockedMailboxWrite() {
        releaseSemaphore.signal()
    }

    func readSession() -> AppSessionResponse? {
        base.readSession()
    }

    func writeSession(_ session: AppSessionResponse) {
        base.writeSession(session)
    }

    func readMailbox(userID: String, label: MailboxLabel) -> MailboxResponse? {
        base.readMailbox(userID: userID, label: label)
    }

    func writeMailbox(_ mailbox: MailboxResponse, userID: String, label: MailboxLabel) {
        lock.lock()
        let shouldBlock = !didBlockMailboxWrite
        if shouldBlock {
            didBlockMailboxWrite = true
            blockedMailboxWrite = true
        }
        lock.unlock()
        if shouldBlock {
            releaseSemaphore.wait()
        }
        base.writeMailbox(mailbox, userID: userID, label: label)
    }

    func writeMailbox(
        _ mailbox: MailboxResponse,
        userID: String,
        label: MailboxLabel,
        removingThreadIDs: [String],
        reenteringThreadIDs: [String],
        reentryLabels: [MailboxLabel]
    ) {
        writeMailbox(mailbox, userID: userID, label: label)
    }

    func readThread(userID: String, threadID: String) -> ThreadReaderResponse? {
        base.readThread(userID: userID, threadID: threadID)
    }

    func writeThread(_ thread: ThreadReaderResponse, userID: String, threadID: String) {
        base.writeThread(thread, userID: userID, threadID: threadID)
    }

    func removeThread(userID: String, threadID: String) {
        base.removeThread(userID: userID, threadID: threadID)
    }

    func purgeAccount(userID: String) {
        base.purgeAccount(userID: userID)
    }

    func writePendingThreadAction(_ action: LocalPendingThreadAction) {
        base.writePendingThreadAction(action)
    }

    func pendingThreadActions() -> [LocalPendingThreadAction] {
        base.pendingThreadActions()
    }

    func removePendingThreadAction(clientActionID: String) {
        base.removePendingThreadAction(clientActionID: clientActionID)
    }

    func markPendingThreadActionFailed(clientActionID: String, error: String) {
        base.markPendingThreadActionFailed(clientActionID: clientActionID, error: error)
    }

    func clearSession() {
        base.clearSession()
    }

    func clearAll() {
        base.clearAll()
    }
}

private final class PurgingAccountActionAppClient: AppClient {
    var baseURL = AppConfiguration.defaultBackendURL
    var sessionToken: String?
    let mode: AppRunMode = .localBackend

    private let demo = DemoAppClient()
    private let localMailStore: LocalMailStore
    private let userID: String
    private let lock = NSLock()
    private var storedPurgeCallCount = 0

    init(localMailStore: LocalMailStore, userID: String = DemoAppFixtures.userID) {
        self.localMailStore = localMailStore
        self.userID = userID
    }

    var purgeCallCount: Int {
        lock.lock()
        defer { lock.unlock() }
        return storedPurgeCallCount
    }

    func exchangeMobileSession(loginCode: String) async throws -> MobileSessionExchangeResponse {
        try await demo.exchangeMobileSession(loginCode: loginCode)
    }

    func appSession() async throws -> AppSessionResponse {
        let session = DemoAppFixtures.appSession
        return AppSessionResponse(
            user: AppSessionUser(
                id: userID,
                email: session.user.email,
                firstName: session.user.firstName,
                displayName: session.user.displayName
            ),
            readiness: session.readiness,
            dashboard: session.dashboard,
            mailbox: session.mailbox,
            sync: session.sync
        )
    }

    func mailbox(label: MailboxLabel, limit: Int, cursor: String?) async throws -> MailboxResponse {
        try await demo.mailbox(label: label, limit: limit, cursor: cursor)
    }

    func thread(threadID: String, limit: Int, offset: Int) async throws -> ThreadReaderResponse {
        let thread = try await demo.thread(threadID: threadID, limit: limit, offset: offset)
        return ThreadReaderResponse(
            entityID: thread.entityID,
            userID: userID,
            source: thread.source,
            gmailThreadID: thread.gmailThreadID,
            subject: thread.subject,
            title: thread.title,
            summary: thread.summary,
            totalMessages: thread.totalMessages,
            limit: thread.limit,
            offset: thread.offset,
            hasMore: thread.hasMore,
            messages: thread.messages,
            contentRevision: thread.contentRevision
        )
    }

    func logout() async throws {
        purgeAccount()
    }

    func disconnectGoogle(deleteData: Bool, revokeSessions: Bool) async throws {
        purgeAccount()
    }

    func deleteGoogleData() async throws {
        purgeAccount()
    }

    func deleteAccount() async throws {
        purgeAccount()
    }

    func triggerMailboxSync() async throws -> MailboxSyncTriggerResponse {
        try await demo.triggerMailboxSync()
    }

    func syncMailboxNow() async throws -> MailboxSyncTriggerResponse {
        try await demo.syncMailboxNow()
    }

    func archiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        try await demo.archiveThread(threadID)
    }

    func unarchiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        try await demo.unarchiveThread(threadID)
    }

    func markThreadRead(_ threadID: String) async throws -> GmailThreadMutationResponse {
        try await demo.markThreadRead(threadID)
    }

    func enqueueThreadAction(_ request: QueuedThreadActionRequest) async throws -> QueuedThreadActionResponse {
        try await demo.enqueueThreadAction(request)
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

    private func purgeAccount() {
        lock.lock()
        storedPurgeCallCount += 1
        lock.unlock()
        localMailStore.purgeAccount(userID: userID)
    }
}

private final class AttachmentDownloadingAppClient: AppClient {
    var baseURL = AppConfiguration.defaultBackendURL
    var sessionToken: String?
    let mode: AppRunMode = .demo

    private let demo = DemoAppClient()
    private let downloadedAttachment: DownloadedAttachment
    private let downloadGate: ReaderActionRequestGate?
    private let downloadDelayNanoseconds: UInt64?
    private let lock = NSLock()
    private var storedRequestedMessageID: String?
    private var storedRequestedAttachment: ThreadAttachment?
    private var storedDownloadCallCount = 0
    private var storedCurrentDownloadCount = 0
    private var storedMaximumObservedDownloadCount = 0

    init(
        downloadedAttachment: DownloadedAttachment,
        downloadGate: ReaderActionRequestGate? = nil,
        downloadDelayNanoseconds: UInt64? = nil
    ) {
        self.downloadedAttachment = downloadedAttachment
        self.downloadGate = downloadGate
        self.downloadDelayNanoseconds = downloadDelayNanoseconds
    }

    var requestedMessageID: String? {
        withLock { storedRequestedMessageID }
    }

    var requestedAttachment: ThreadAttachment? {
        withLock { storedRequestedAttachment }
    }

    var downloadCallCount: Int {
        withLock { storedDownloadCallCount }
    }

    var currentDownloadCount: Int {
        withLock { storedCurrentDownloadCount }
    }

    var maximumObservedDownloadCount: Int {
        withLock { storedMaximumObservedDownloadCount }
    }

    func exchangeMobileSession(loginCode: String) async throws -> MobileSessionExchangeResponse {
        try await demo.exchangeMobileSession(loginCode: loginCode)
    }

    func appSession() async throws -> AppSessionResponse {
        try await demo.appSession()
    }

    func mailbox(label: MailboxLabel, limit: Int, cursor: String?) async throws -> MailboxResponse {
        try await demo.mailbox(label: label, limit: limit, cursor: cursor)
    }

    func thread(threadID: String, limit: Int, offset: Int) async throws -> ThreadReaderResponse {
        try await demo.thread(threadID: threadID, limit: limit, offset: offset)
    }

    func triggerMailboxSync() async throws -> MailboxSyncTriggerResponse {
        try await demo.triggerMailboxSync()
    }

    func syncMailboxNow() async throws -> MailboxSyncTriggerResponse {
        try await demo.syncMailboxNow()
    }

    func archiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        try await demo.archiveThread(threadID)
    }

    func unarchiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        try await demo.unarchiveThread(threadID)
    }

    func markThreadRead(_ threadID: String) async throws -> GmailThreadMutationResponse {
        try await demo.markThreadRead(threadID)
    }

    func enqueueThreadAction(_ request: QueuedThreadActionRequest) async throws -> QueuedThreadActionResponse {
        try await demo.enqueueThreadAction(request)
    }

    func downloadAttachment(messageID: String, attachment: ThreadAttachment) async throws -> DownloadedAttachment {
        withLock {
            storedDownloadCallCount += 1
            storedCurrentDownloadCount += 1
            storedMaximumObservedDownloadCount = max(
                storedMaximumObservedDownloadCount,
                storedCurrentDownloadCount
            )
            storedRequestedMessageID = messageID
            storedRequestedAttachment = attachment
        }
        defer {
            withLock { storedCurrentDownloadCount -= 1 }
        }
        if let downloadGate {
            await downloadGate.suspendRequest()
        } else if let downloadDelayNanoseconds {
            try await Task.sleep(nanoseconds: downloadDelayNanoseconds)
        }
        return downloadedAttachment
    }

    @discardableResult
    private func withLock<T>(_ body: () -> T) -> T {
        lock.lock()
        defer { lock.unlock() }
        return body()
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

private struct OversizedThreadPageCall: Equatable {
    let limit: Int
    let offset: Int
}

private final class OversizedOfflineThreadAppClient: AppClient {
    enum Mode {
        case stable(totalMessages: Int)
        case repeatedPage
        case revisionChange
    }

    var baseURL = AppConfiguration.defaultBackendURL
    var sessionToken: String?
    let mode: AppRunMode = .localBackend

    private let demo = DemoAppClient()
    private let pageMode: Mode
    private let gatedOffset: Int?
    private let pageGate: ReaderActionRequestGate?
    private let lock = NSLock()
    private var recordedBatchCallCount = 0
    private var recordedThreadCalls: [OversizedThreadPageCall] = []

    init(
        mode: Mode,
        gatedOffset: Int? = nil,
        pageGate: ReaderActionRequestGate? = nil
    ) {
        pageMode = mode
        self.gatedOffset = gatedOffset
        self.pageGate = pageGate
    }

    var batchCallCount: Int {
        withLock { recordedBatchCallCount }
    }

    var threadCalls: [OversizedThreadPageCall] {
        withLock { recordedThreadCalls }
    }

    func exchangeMobileSession(loginCode: String) async throws -> MobileSessionExchangeResponse {
        try await demo.exchangeMobileSession(loginCode: loginCode)
    }

    func logout() async throws {}

    func appSession() async throws -> AppSessionResponse {
        try await demo.appSession()
    }

    func mailbox(label: MailboxLabel, limit: Int, cursor: String?) async throws -> MailboxResponse {
        try await demo.mailbox(label: label, limit: limit, cursor: cursor)
    }

    func batchThreads(threadIDs: [String]) async throws -> MailboxThreadBatchResponse {
        withLock { recordedBatchCallCount += 1 }
        return MailboxThreadBatchResponse(threads: [], pendingThreadIDs: threadIDs)
    }

    func thread(threadID: String, limit: Int, offset: Int) async throws -> ThreadReaderResponse {
        withLock { recordedThreadCalls.append(OversizedThreadPageCall(limit: limit, offset: offset)) }
        if gatedOffset == offset, let pageGate {
            await pageGate.suspendRequest()
        }
        let totalMessages: Int
        let messageIndexes: [Int]
        let hasMore: Bool
        let revision: String
        switch pageMode {
        case .stable(let total):
            totalMessages = total
            let end = min(total, offset + limit)
            messageIndexes = offset < end ? Array(offset..<end) : []
            hasMore = end < total
            revision = "sha256:stable"
        case .revisionChange:
            totalMessages = 200
            let end = min(totalMessages, offset + limit)
            messageIndexes = offset < end ? Array(offset..<end) : []
            hasMore = end < totalMessages
            revision = offset == 0 ? "sha256:first" : "sha256:changed"
        case .repeatedPage:
            totalMessages = 201
            messageIndexes = Array(0..<min(100, limit))
            hasMore = true
            revision = "sha256:stable"
        }
        return ThreadReaderResponse(
            entityID: threadID,
            userID: DemoAppFixtures.userID,
            source: .gmail,
            gmailThreadID: threadID,
            subject: "Oversized offline thread",
            totalMessages: totalMessages,
            limit: limit,
            offset: offset,
            hasMore: hasMore,
            messages: messageIndexes.map { index in
                ThreadMessage(
                    id: "oversized-message-\(index)",
                    source: .gmail,
                    threadID: threadID,
                    fromAddress: "sender@example.com",
                    to: "reader@example.com",
                    cc: nil,
                    bcc: nil,
                    subject: "Oversized offline thread",
                    body: "Complete body \(index)",
                    bodyComplete: true,
                    htmlBody: nil,
                    htmlRenderDocument: nil,
                    snippet: "Complete body \(index)",
                    labelIDs: ["INBOX"],
                    receivedAt: "2026-07-25T12:00:00Z"
                )
            },
            contentRevision: revision
        )
    }

    func triggerMailboxSync() async throws -> MailboxSyncTriggerResponse {
        try await demo.triggerMailboxSync()
    }

    func syncMailboxNow() async throws -> MailboxSyncTriggerResponse {
        try await demo.syncMailboxNow()
    }

    func archiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        try await demo.archiveThread(threadID)
    }

    func unarchiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        try await demo.unarchiveThread(threadID)
    }

    func markThreadRead(_ threadID: String) async throws -> GmailThreadMutationResponse {
        try await demo.markThreadRead(threadID)
    }

    func enqueueThreadAction(_ request: QueuedThreadActionRequest) async throws -> QueuedThreadActionResponse {
        try await demo.enqueueThreadAction(request)
    }

    func downloadAttachment(messageID: String, attachment: ThreadAttachment) async throws -> DownloadedAttachment {
        try await demo.downloadAttachment(messageID: messageID, attachment: attachment)
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

    @discardableResult
    private func withLock<T>(_ body: () -> T) -> T {
        lock.lock()
        defer { lock.unlock() }
        return body()
    }
}

private final class GlobalOfflineMetadataAppClient: AppClient {
    var baseURL = AppConfiguration.defaultBackendURL
    var sessionToken: String?
    let mode: AppRunMode = .localBackend

    private let demo = DemoAppClient()
    private let userID: String
    private let batchGate: ReaderActionRequestGate?
    private let lock = NSLock()
    private var progressRevision = 1
    private var recordedMailboxCalls: [MailboxPageCall] = []
    private var recordedBatchedThreadIDs: Set<String> = []

    init(
        userID: String = DemoAppFixtures.userID,
        batchGate: ReaderActionRequestGate? = nil
    ) {
        self.userID = userID
        self.batchGate = batchGate
    }

    var mailboxCalls: [MailboxPageCall] {
        withLock { recordedMailboxCalls }
    }

    var batchedThreadIDs: Set<String> {
        withLock { recordedBatchedThreadIDs }
    }

    func advanceProgress() {
        withLock { progressRevision += 1 }
    }

    func exchangeMobileSession(loginCode: String) async throws -> MobileSessionExchangeResponse {
        try await demo.exchangeMobileSession(loginCode: loginCode)
    }

    func appSession() async throws -> AppSessionResponse {
        let revision = withLock { progressRevision }
        let coldInboxRow = makeMailboxRow(
            threadID: "inbox-cold",
            latestSourceRecordID: "inbox-cold-message",
            receivedAt: "2026-07-25T12:00:00Z",
            title: "Cold Inbox body",
            bodyReady: false,
            contentRevision: "sha256:inbox-cold"
        )
        let mailbox = MailboxResponse(
            label: .inbox,
            totalThreads: 5,
            loadedThreads: 1,
            sections: [GmailThreadSection(id: "inbox", title: "Inbox", rows: [coldInboxRow])],
            fullImportRunning: true,
            fullImportCompleted: false,
            syncGeneration: "global-generation-1",
            phase: "hydrating_history_content",
            initialTargetCount: 5,
            initialMetadataCount: revision > 1 ? 5 : 4,
            initialBodyTargetCount: 5,
            initialBodyReadyCount: revision > 1 ? 5 : 2,
            historyMetadataCount: revision > 1 ? 5 : 4,
            historyBodyReadyCount: revision > 1 ? 5 : 2,
            estimatedTotalCount: 5,
            initialWindowComplete: true,
            historyMetadataComplete: true,
            historyBodyComplete: revision > 1,
            lastProgressAt: "2026-07-25T12:00:0\(revision)Z"
        )
        let base = makeProgressiveSession(mailbox: mailbox)
        let baseUser = base.user
        return AppSessionResponse(
            user: AppSessionUser(
                id: userID,
                email: baseUser.email,
                firstName: baseUser.firstName,
                displayName: baseUser.displayName
            ),
            readiness: base.readiness,
            dashboard: base.dashboard,
            mailbox: base.mailbox,
            sync: base.sync
        )
    }

    func mailbox(label: MailboxLabel, limit: Int, cursor: String?) async throws -> MailboxResponse {
        withLock {
            recordedMailboxCalls.append(MailboxPageCall(label: label, cursor: cursor))
        }
        guard label == .all else {
            return makeEmptyMailbox(label: label)
        }
        let revision = withLock { progressRevision }
        var identifiers: [(String, Bool, Int?)]
        let nextCursor: String?
        switch cursor {
        case nil:
            identifiers = [("global-ready-1", true, 0), ("global-cold-1", false, 30)]
            nextCursor = "all-cursor-2"
        case "all-cursor-2":
            identifiers = [
                ("global-ready-2", true, 1),
                ("global-cold-2", false, 3),
            ]
            if revision > 1 {
                identifiers.append(("global-new-tail", true, nil))
            }
            nextCursor = nil
        default:
            throw APIError.httpStatus(400)
        }
        let rows = identifiers.map { identifier, bodyReady, initialWindowPosition in
            makeMailboxRow(
                threadID: identifier,
                latestSourceRecordID: "\(identifier)-message",
                receivedAt: "2026-07-25T12:00:00Z",
                title: identifier,
                bodyReady: bodyReady,
                contentRevision: "sha256:\(identifier)",
                initialWindowPosition: initialWindowPosition
            )
        }
        return MailboxResponse(
            label: .all,
            totalThreads: revision > 1 ? 5 : 4,
            nextCursor: nextCursor,
            loadedThreads: rows.count,
            sections: [GmailThreadSection(id: "all", title: "All Mail", rows: rows)],
            fullImportRunning: true,
            fullImportCompleted: false,
            syncGeneration: "global-generation-1",
            phase: "hydrating_history_content",
            initialTargetCount: revision > 1 ? 5 : 4,
            initialMetadataCount: revision > 1 ? 5 : 4,
            initialBodyTargetCount: revision > 1 ? 5 : 4,
            initialBodyReadyCount: revision > 1 ? 3 : 2,
            historyMetadataCount: revision > 1 ? 5 : 4,
            historyBodyReadyCount: revision > 1 ? 3 : 2,
            estimatedTotalCount: revision > 1 ? 5 : 4,
            initialWindowComplete: true,
            historyMetadataComplete: true,
            historyBodyComplete: false,
            lastProgressAt: "2026-07-25T12:00:0\(revision)Z"
        )
    }

    func batchThreads(threadIDs: [String]) async throws -> MailboxThreadBatchResponse {
        withLock {
            recordedBatchedThreadIDs.formUnion(threadIDs)
        }
        if let batchGate {
            await batchGate.suspendRequest()
        }
        return MailboxThreadBatchResponse(
            threads: threadIDs.map { threadID in
                ThreadReaderResponse(
                    entityID: threadID,
                    userID: userID,
                    source: .gmail,
                    gmailThreadID: threadID,
                    subject: threadID,
                    totalMessages: 0,
                    messages: [],
                    contentRevision: "sha256:\(threadID)"
                )
            }
        )
    }

    func mailboxSyncState() async throws -> MailboxSyncStateResponse {
        let revision = withLock { progressRevision }
        var state = makeMailboxSyncStateResponse(connected: true, mailboxRevision: "global-revision-\(revision)")
        state.syncGeneration = "global-generation-1"
        state.phase = "hydrating_history_content"
        state.initialTargetCount = 5
        state.initialMetadataCount = revision > 1 ? 5 : 4
        state.initialBodyTargetCount = 5
        state.initialBodyReadyCount = revision > 1 ? 5 : 2
        state.historyMetadataCount = revision > 1 ? 5 : 4
        state.historyBodyReadyCount = revision > 1 ? 5 : 2
        state.estimatedTotalCount = 5
        state.initialWindowComplete = true
        state.historyMetadataComplete = true
        state.historyBodyComplete = revision > 1
        state.lastProgressAt = "2026-07-25T12:00:0\(revision)Z"
        return state
    }

    func thread(threadID: String, limit: Int, offset: Int) async throws -> ThreadReaderResponse {
        return DemoAppFixtures.threads[threadID] ?? DemoAppFixtures.threads["demo-google-today"]!
    }

    func logout() async throws {}

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

    func createTask(_ request: TaskCreateRequest) async throws -> TaskResponse {
        try await demo.createTask(request)
    }

    func updateTask(_ taskID: String, request: TaskUpdateRequest) async throws -> TaskResponse {
        try await demo.updateTask(taskID, request: request)
    }

    func completeEntity(_ entityID: String, request: EntityOutcomeRequest) async throws -> EntityOutcomeResponse {
        try await demo.completeEntity(entityID, request: request)
    }

    private func withLock<T>(_ body: () -> T) -> T {
        lock.lock()
        defer { lock.unlock() }
        return body()
    }
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
    private let fixedSession: AppSessionResponse?
    private let successfulMailboxCallsBeforeFailure: Int?
    private var fixedThreadResponses: [ThreadReaderResponse]
    private var threadFailuresBeforeSuccess: Int
    private let threadRequestGate: ReaderActionRequestGate?
    private var mailboxCallCount = 0
    private(set) var threadCallCount = 0

    init(
        mailbox: MailboxResponse,
        session: AppSessionResponse? = nil,
        successfulMailboxCallsBeforeFailure: Int? = nil,
        threadResponses: [ThreadReaderResponse] = [],
        threadFailuresBeforeSuccess: Int = 0,
        threadRequestGate: ReaderActionRequestGate? = nil
    ) {
        self.fixedMailbox = mailbox
        self.fixedSession = session
        self.successfulMailboxCallsBeforeFailure = successfulMailboxCallsBeforeFailure
        self.fixedThreadResponses = threadResponses
        self.threadFailuresBeforeSuccess = max(0, threadFailuresBeforeSuccess)
        self.threadRequestGate = threadRequestGate
    }

    func exchangeMobileSession(loginCode: String) async throws -> MobileSessionExchangeResponse {
        try await DemoAppClient().exchangeMobileSession(loginCode: loginCode)
    }

    func appSession() async throws -> AppSessionResponse {
        if let fixedSession {
            return fixedSession
        }
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
        if let successfulMailboxCallsBeforeFailure,
           mailboxCallCount >= successfulMailboxCallsBeforeFailure {
            throw APIError.httpStatus(503)
        }
        mailboxCallCount += 1
        return fixedMailbox
    }

    func thread(threadID: String, limit: Int, offset: Int) async throws -> ThreadReaderResponse {
        threadCallCount += 1
        if let threadRequestGate {
            await threadRequestGate.suspendRequest()
        }
        if threadFailuresBeforeSuccess > 0 {
            threadFailuresBeforeSuccess -= 1
            throw APIError.httpStatus(503)
        }
        if !fixedThreadResponses.isEmpty {
            return fixedThreadResponses.removeFirst()
        }
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

private final class ActionMailboxAppClient: AppClient {
    var baseURL = AppConfiguration.defaultBackendURL
    var sessionToken: String?
    let mode: AppRunMode = .localBackend
    var isOffline = false
    private var mailboxResponse: MailboxResponse
    private let threadResponse: ThreadReaderResponse?
    private let actionGate: ReaderActionRequestGate?
    private let shouldFailActions: Bool
    private(set) var enqueuedActions: [QueuedThreadActionRequest] = []

    init(
        mailbox: MailboxResponse,
        threadResponse: ThreadReaderResponse? = nil,
        actionGate: ReaderActionRequestGate? = nil,
        shouldFailActions: Bool = false
    ) {
        self.mailboxResponse = mailbox
        self.threadResponse = threadResponse
        self.actionGate = actionGate
        self.shouldFailActions = shouldFailActions
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
        if isOffline {
            throw URLError(.notConnectedToInternet)
        }
        return mailboxResponse
    }

    func thread(threadID: String, limit: Int, offset: Int) async throws -> ThreadReaderResponse {
        threadResponse ?? DemoAppFixtures.threads[threadID] ?? DemoAppFixtures.threads["demo-google-today"]!
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
        if isOffline {
            throw URLError(.notConnectedToInternet)
        }
        enqueuedActions.append(request)
        if let actionGate {
            await actionGate.suspendRequest()
        }
        if shouldFailActions {
            throw APIError.httpStatus(503)
        }
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
    let supportsFolderCountPrefetch: Bool
    private let sessionMailbox: MailboxResponse
    private var mailboxResponses: [MailboxResponse]
    private var threadResponses: [ThreadReaderResponse]
    private let mailboxCountGate: ReaderActionRequestGate?
    private let mailboxRequestGates: [MailboxLabel: ReaderActionRequestGate]
    private let mailboxRequestGateCalls: [MailboxLabel: Int]
    private let mailboxResponsesByCall: [Int: MailboxResponse]
    private let mailboxFailureLabels: Set<MailboxLabel>
    private let threadRequestGate: ReaderActionRequestGate?
    private let threadRequestGateCall: Int
    private let actionRequestGate: ReaderActionRequestGate?
    private var didSuspendCountRequest = false
    private var mailboxCallsByLabel: [MailboxLabel: Int] = [:]
    private var didSuspendThreadRequest = false
    private(set) var appSessionCallCount = 0
    private(set) var mailboxCallCount = 0
    private(set) var mailboxLabels: [MailboxLabel] = []
    private(set) var mailboxLimits: [Int] = []
    private(set) var mailboxCursors: [String?] = []
    private(set) var threadCallCount = 0
    private(set) var observedHydratedThreads: [MailboxHydratedThreadState] = []

    init(
        sessionMailbox: MailboxResponse,
        mailboxResponses: [MailboxResponse],
        threadResponses: [ThreadReaderResponse] = [],
        supportsFolderCountPrefetch: Bool = false,
        mailboxCountGate: ReaderActionRequestGate? = nil,
        mailboxRequestGates: [MailboxLabel: ReaderActionRequestGate] = [:],
        mailboxRequestGateCalls: [MailboxLabel: Int] = [:],
        mailboxResponsesByCall: [Int: MailboxResponse] = [:],
        mailboxFailureLabels: Set<MailboxLabel> = [],
        threadRequestGate: ReaderActionRequestGate? = nil,
        threadRequestGateCall: Int = 1,
        actionRequestGate: ReaderActionRequestGate? = nil
    ) {
        self.sessionMailbox = sessionMailbox
        self.mailboxResponses = mailboxResponses
        self.threadResponses = threadResponses
        self.supportsFolderCountPrefetch = supportsFolderCountPrefetch
        self.mailboxCountGate = mailboxCountGate
        self.mailboxRequestGates = mailboxRequestGates
        self.mailboxRequestGateCalls = mailboxRequestGateCalls
        self.mailboxResponsesByCall = mailboxResponsesByCall
        self.mailboxFailureLabels = mailboxFailureLabels
        self.threadRequestGate = threadRequestGate
        self.threadRequestGateCall = threadRequestGateCall
        self.actionRequestGate = actionRequestGate
    }

    func exchangeMobileSession(loginCode: String) async throws -> MobileSessionExchangeResponse {
        try await DemoAppClient().exchangeMobileSession(loginCode: loginCode)
    }

    func observeHydratedThreads(
        _ threads: [MailboxHydratedThreadState],
        userID: String
    ) async {
        guard userID == DemoAppFixtures.userID else { return }
        observedHydratedThreads.append(contentsOf: threads)
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
        mailboxLabels.append(label)
        mailboxLimits.append(limit)
        mailboxCursors.append(cursor)
        mailboxCallsByLabel[label, default: 0] += 1
        let labelCall = mailboxCallsByLabel[label, default: 0]
        let responseForCall = mailboxResponsesByCall[mailboxCallCount]
        if limit == 1, !didSuspendCountRequest, let mailboxCountGate {
            didSuspendCountRequest = true
            await mailboxCountGate.suspendRequest()
        }
        if limit > 1,
           labelCall == mailboxRequestGateCalls[label, default: 1],
           let gate = mailboxRequestGates[label] {
            await gate.suspendRequest()
        }
        if mailboxFailureLabels.contains(label) {
            throw APIError.httpStatus(503)
        }
        if let responseForCall {
            return responseForCall
        }
        if mailboxResponses.isEmpty {
            return sessionMailbox
        }
        return mailboxResponses.removeFirst()
    }

    func thread(threadID: String, limit: Int, offset: Int) async throws -> ThreadReaderResponse {
        threadCallCount += 1
        if !didSuspendThreadRequest,
           threadCallCount == threadRequestGateCall,
           let threadRequestGate {
            didSuspendThreadRequest = true
            await threadRequestGate.suspendRequest()
        }
        if !threadResponses.isEmpty {
            return threadResponses.removeFirst()
        }
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
        if let actionRequestGate {
            await actionRequestGate.suspendRequest()
        }
        return try await DemoAppClient().enqueueThreadAction(request)
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

private struct MailboxSearchCall: Equatable {
    let query: String
    let label: MailboxLabel?
    let limit: Int
    let cursor: String?
    let hydrateInBackground: Bool
}

private final class SearchHydrationAppClient: AppClient {
    var baseURL = AppConfiguration.defaultBackendURL
    var sessionToken: String?
    let mode: AppRunMode = .localBackend

    private let demo = DemoAppClient()
    private var searchResponses: [MailboxResponse]
    private let suppressedResponseDelayNanoseconds: UInt64
    private let initialSearchRequestGate: ReaderActionRequestGate?
    private var didSuspendInitialSearchRequest = false
    private(set) var searchCalls: [MailboxSearchCall] = []

    init(
        searchResponses: [MailboxResponse],
        suppressedResponseDelayNanoseconds: UInt64 = 0,
        initialSearchRequestGate: ReaderActionRequestGate? = nil
    ) {
        self.searchResponses = searchResponses
        self.suppressedResponseDelayNanoseconds = suppressedResponseDelayNanoseconds
        self.initialSearchRequestGate = initialSearchRequestGate
    }

    func exchangeMobileSession(loginCode: String) async throws -> MobileSessionExchangeResponse {
        try await demo.exchangeMobileSession(loginCode: loginCode)
    }

    func appSession() async throws -> AppSessionResponse {
        try await demo.appSession()
    }

    func mailbox(label: MailboxLabel, limit: Int, cursor: String?) async throws -> MailboxResponse {
        try await demo.mailbox(label: label, limit: limit, cursor: cursor)
    }

    func searchMailbox(
        query: String,
        label: MailboxLabel?,
        limit: Int,
        cursor: String?,
        hydrateInBackground: Bool = true
    ) async throws -> MailboxResponse {
        searchCalls.append(
            MailboxSearchCall(
                query: query,
                label: label,
                limit: limit,
                cursor: cursor,
                hydrateInBackground: hydrateInBackground
            )
        )
        guard !searchResponses.isEmpty else {
            throw APIError.emptyResponse
        }
        let response = searchResponses.removeFirst()
        if hydrateInBackground,
           cursor == nil,
           !didSuspendInitialSearchRequest,
           let initialSearchRequestGate {
            didSuspendInitialSearchRequest = true
            await initialSearchRequestGate.suspendRequest()
        }
        if !hydrateInBackground, suppressedResponseDelayNanoseconds > 0 {
            await withCheckedContinuation { continuation in
                DispatchQueue.global().asyncAfter(
                    deadline: .now() + .nanoseconds(Int(suppressedResponseDelayNanoseconds))
                ) {
                    continuation.resume()
                }
            }
        }
        return response
    }

    func mailboxSyncState() async throws -> MailboxSyncStateResponse {
        makeMailboxSyncStateResponse(connected: true, mailboxRevision: "search-import-complete")
    }

    func thread(threadID: String, limit: Int, offset: Int) async throws -> ThreadReaderResponse {
        try await demo.thread(threadID: threadID, limit: limit, offset: offset)
    }

    func triggerMailboxSync() async throws -> MailboxSyncTriggerResponse {
        try await demo.triggerMailboxSync()
    }

    func syncMailboxNow() async throws -> MailboxSyncTriggerResponse {
        try await demo.syncMailboxNow()
    }

    func archiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        try await demo.archiveThread(threadID)
    }

    func unarchiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        try await demo.unarchiveThread(threadID)
    }

    func markThreadRead(_ threadID: String) async throws -> GmailThreadMutationResponse {
        try await demo.markThreadRead(threadID)
    }

    func enqueueThreadAction(_ request: QueuedThreadActionRequest) async throws -> QueuedThreadActionResponse {
        try await demo.enqueueThreadAction(request)
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

private final class ManualSyncAppClient: AppClient {
    var baseURL = AppConfiguration.defaultBackendURL
    var sessionToken: String?
    let mode: AppRunMode = .localBackend
    private(set) var syncNowCallCount = 0
    private(set) var logoutCallCount = 0
    private(set) var mailboxLabels: [MailboxLabel] = []
    var syncNowFailureStatus: Int?
    var syncNowStatus = "synced"
    var syncNowConnected = true
    var syncStateFailureStatus: Int?
    var syncStateConnected = true

    func exchangeMobileSession(loginCode: String) async throws -> MobileSessionExchangeResponse {
        try await DemoAppClient().exchangeMobileSession(loginCode: loginCode)
    }

    func logout() async throws {
        logoutCallCount += 1
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

    func mailboxSyncState() async throws -> MailboxSyncStateResponse {
        if let syncStateFailureStatus {
            throw APIError.httpStatus(syncStateFailureStatus)
        }
        return makeSyncState(connected: syncStateConnected)
    }

    func syncMailboxNow() async throws -> MailboxSyncTriggerResponse {
        syncNowCallCount += 1
        if let syncNowFailureStatus {
            throw APIError.httpStatus(syncNowFailureStatus)
        }
        return MailboxSyncTriggerResponse(
            status: syncNowStatus,
            state: makeSyncState(connected: syncNowConnected),
            jobID: nil,
            queuedAt: nil
        )
    }

    private func makeSyncState(connected: Bool) -> MailboxSyncStateResponse {
        MailboxSyncStateResponse(
            connected: connected,
            lastHistoryID: connected ? "history-1" : nil,
            lastFullSyncAt: nil,
            watchExpirationAt: nil,
            lastSyncStartedAt: nil,
            lastSyncCompletedAt: nil,
            lastSyncError: connected ? nil : "Google is not connected.",
            totalThreads: DemoAppFixtures.mailbox.totalThreads
        )
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

private struct MailboxPageCall: Equatable {
    let label: MailboxLabel
    let cursor: String?
}

private final class MailboxPaginationRaceAppClient: AppClient {
    var baseURL = AppConfiguration.defaultBackendURL
    var sessionToken: String?
    let mode: AppRunMode = .localBackend

    private let demo = DemoAppClient()
    private let callLock = NSLock()
    private var recordedMailboxCalls: [MailboxPageCall] = []

    var mailboxCalls: [MailboxPageCall] {
        callLock.lock()
        defer { callLock.unlock() }
        return recordedMailboxCalls
    }

    func exchangeMobileSession(loginCode: String) async throws -> MobileSessionExchangeResponse {
        try await demo.exchangeMobileSession(loginCode: loginCode)
    }

    func appSession() async throws -> AppSessionResponse {
        let current = DemoAppFixtures.appSession
        return AppSessionResponse(
            user: current.user,
            readiness: current.readiness,
            dashboard: current.dashboard,
            mailbox: inboxFirstPage,
            sync: current.sync
        )
    }

    func mailbox(label: MailboxLabel, limit: Int, cursor: String?) async throws -> MailboxResponse {
        recordMailboxCall(label: label, cursor: cursor)

        switch (label, cursor) {
        case (.inbox, nil):
            return inboxFirstPage
        case (.inbox, "inbox-cursor-2"):
            await nonCancellableDelay(nanoseconds: 80_000_000)
            return inboxSecondPage
        case (.inbox, "inbox-cursor-3"):
            await nonCancellableDelay(nanoseconds: 40_000_000)
            return inboxThirdPage
        case (.sent, nil):
            return sentFirstPage
        case (.sent, "sent-cursor-2"):
            await nonCancellableDelay(nanoseconds: 200_000_000)
            return sentSecondPage
        default:
            return MailboxResponse(
                label: label,
                totalThreads: 0,
                nextCursor: nil,
                loadedThreads: 0,
                sections: [],
                fullImportRunning: false,
                fullImportCompleted: true
            )
        }
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

    func createTask(_ request: TaskCreateRequest) async throws -> TaskResponse {
        try await demo.createTask(request)
    }

    func updateTask(_ taskID: String, request: TaskUpdateRequest) async throws -> TaskResponse {
        try await demo.updateTask(taskID, request: request)
    }

    func completeEntity(_ entityID: String, request: EntityOutcomeRequest) async throws -> EntityOutcomeResponse {
        try await demo.completeEntity(entityID, request: request)
    }

    private var inboxFirstPage: MailboxResponse {
        // Gmail rank is intentionally non-chronological across pages.
        page(
            label: .inbox,
            threadID: "inbox-1",
            receivedAt: "2026-07-13T10:00:00+00:00",
            totalThreads: 3,
            nextCursor: "inbox-cursor-2",
            importCompleted: true
        )
    }

    private var inboxSecondPage: MailboxResponse {
        page(
            label: .inbox,
            threadID: "inbox-2",
            receivedAt: "2026-07-13T12:00:00+00:00",
            totalThreads: 3,
            nextCursor: "inbox-cursor-3",
            importCompleted: true
        )
    }

    private var inboxThirdPage: MailboxResponse {
        page(
            label: .inbox,
            threadID: "inbox-3",
            receivedAt: "2026-07-13T11:00:00+00:00",
            totalThreads: 3,
            nextCursor: nil,
            importCompleted: true
        )
    }

    private var sentFirstPage: MailboxResponse {
        page(
            label: .sent,
            threadID: "sent-1",
            receivedAt: "2026-07-13T12:00:00+00:00",
            totalThreads: 2,
            nextCursor: "sent-cursor-2",
            importCompleted: true
        )
    }

    private var sentSecondPage: MailboxResponse {
        page(
            label: .sent,
            threadID: "sent-2",
            receivedAt: "2026-07-13T11:00:00+00:00",
            totalThreads: 2,
            nextCursor: nil,
            importCompleted: true
        )
    }

    private func page(
        label: MailboxLabel,
        threadID: String,
        receivedAt: String,
        totalThreads: Int,
        nextCursor: String?,
        importCompleted: Bool
    ) -> MailboxResponse {
        let gmailLabel = label == .sent ? "SENT" : "INBOX"
        let row = makeMailboxRow(
            threadID: threadID,
            latestSourceRecordID: "\(threadID)-message",
            receivedAt: receivedAt,
            title: threadID,
            labelIDs: [gmailLabel],
            labels: [gmailLabel]
        )
        return MailboxResponse(
            label: label,
            totalThreads: totalThreads,
            nextCursor: nextCursor,
            loadedThreads: 1,
            sections: [GmailThreadSection(id: "today", title: "Today", rows: [row])],
            fullImportRunning: false,
            fullImportCompleted: importCompleted
        )
    }

    private func nonCancellableDelay(nanoseconds: UInt64) async {
        await withCheckedContinuation { continuation in
            DispatchQueue.global().asyncAfter(deadline: .now() + .nanoseconds(Int(nanoseconds))) {
                continuation.resume()
            }
        }
    }

    private func recordMailboxCall(label: MailboxLabel, cursor: String?) {
        callLock.lock()
        defer { callLock.unlock() }
        recordedMailboxCalls.append(MailboxPageCall(label: label, cursor: cursor))
    }
}

private final class BulkPaginatedMailboxAppClient: AppClient {
    var baseURL = AppConfiguration.defaultBackendURL
    var sessionToken: String?
    let mode: AppRunMode = .demo
    var delayedCursor: String?
    var gatedCursor: String?
    var mailboxRequestGate: ReaderActionRequestGate?
    private(set) var mailboxCursors: [String?] = []
    private(set) var searchQueries: [String] = []

    private let demo = DemoAppClient()
    private let failOnceAtCursor: String?
    private let revisionMismatchCursor: String?
    private let repeatCursorOnceAtCursor: String?
    private let importInProgress: Bool
    private let growingImportTotals: Bool
    private let authoritativeGeneration: String?
    private var didFailRequestedCursor = false
    private var stagedFailureCursor: String?
    private var remainingRevisionChurns: Int
    private var didRepeatRequestedCursor = false
    private var totalThreads = 357
    private var mailboxRevision = "bulk-revision-1"
    private var mailboxTitlePrefix = "Bulk message"
    private let pageSize = 100

    init(
        failOnceAtCursor: String? = nil,
        revisionMismatchOnceAtCursor: String? = nil,
        repeatCursorOnceAtCursor: String? = nil,
        importInProgress: Bool = false,
        growingImportTotals: Bool = false,
        revisionChurnCount: Int? = nil,
        authoritativeGeneration: String? = nil,
        mailboxTitlePrefix: String = "Bulk message"
    ) {
        self.failOnceAtCursor = failOnceAtCursor
        self.revisionMismatchCursor = revisionMismatchOnceAtCursor
        self.repeatCursorOnceAtCursor = repeatCursorOnceAtCursor
        self.importInProgress = importInProgress
        self.growingImportTotals = growingImportTotals
        self.authoritativeGeneration = authoritativeGeneration
        self.mailboxTitlePrefix = mailboxTitlePrefix
        self.remainingRevisionChurns = revisionChurnCount
            ?? (revisionMismatchOnceAtCursor == nil ? 0 : 1)
    }

    func stageAuthoritativeRefresh(
        revision: String,
        titlePrefix: String,
        totalThreads: Int,
        gateAtCursor: String,
        gate: ReaderActionRequestGate,
        failOnceAtCursor: String? = nil
    ) {
        mailboxRevision = revision
        mailboxTitlePrefix = titlePrefix
        self.totalThreads = totalThreads
        gatedCursor = gateAtCursor
        mailboxRequestGate = gate
        stagedFailureCursor = failOnceAtCursor
        didFailRequestedCursor = false
    }

    func exchangeMobileSession(loginCode: String) async throws -> MobileSessionExchangeResponse {
        try await demo.exchangeMobileSession(loginCode: loginCode)
    }

    func appSession() async throws -> AppSessionResponse {
        let current = DemoAppFixtures.appSession
        return AppSessionResponse(
            user: current.user,
            readiness: current.readiness,
            dashboard: current.dashboard,
            mailbox: page(offset: 0),
            sync: current.sync
        )
    }

    func mailbox(label: MailboxLabel, limit: Int, cursor: String?) async throws -> MailboxResponse {
        mailboxCursors.append(cursor)
        guard label == .inbox else {
            return makeEmptyMailbox(label: label)
        }

        if let delayedCursor, cursor == delayedCursor {
            try await Task.sleep(nanoseconds: 250_000_000)
        }
        if let gatedCursor,
           cursor == gatedCursor,
           let mailboxRequestGate {
            await mailboxRequestGate.suspendRequest()
        }
        let requestedFailureCursor = stagedFailureCursor ?? failOnceAtCursor
        if let requestedFailureCursor,
           cursor == requestedFailureCursor,
           !didFailRequestedCursor {
            didFailRequestedCursor = true
            throw APIError.httpStatus(503)
        }

        let offset = try offset(for: cursor)
        if let revisionMismatchCursor,
           cursor == revisionMismatchCursor,
           remainingRevisionChurns > 0 {
            remainingRevisionChurns -= 1
            return page(offset: offset, revision: "bulk-revision-churn-\(remainingRevisionChurns)")
        }
        if let repeatCursorOnceAtCursor,
           cursor == repeatCursorOnceAtCursor,
           !didRepeatRequestedCursor {
            didRepeatRequestedCursor = true
            return page(offset: offset, forcedNextCursor: cursor)
        }
        return page(offset: offset)
    }

    func searchMailbox(
        query: String,
        label: MailboxLabel?,
        limit: Int,
        cursor: String?,
        hydrateInBackground: Bool = true
    ) async throws -> MailboxResponse {
        searchQueries.append(query)
        return makeSingleRowMailbox(threadID: "search-\(query)", title: "Search \(query)")
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

    func createTask(_ request: TaskCreateRequest) async throws -> TaskResponse {
        try await demo.createTask(request)
    }

    func updateTask(_ taskID: String, request: TaskUpdateRequest) async throws -> TaskResponse {
        try await demo.updateTask(taskID, request: request)
    }

    func completeEntity(_ entityID: String, request: EntityOutcomeRequest) async throws -> EntityOutcomeResponse {
        try await demo.completeEntity(entityID, request: request)
    }

    private func offset(for cursor: String?) throws -> Int {
        guard let cursor else {
            return 0
        }
        guard cursor.hasPrefix("bulk-cursor-"),
              let offset = Int(cursor.dropFirst("bulk-cursor-".count)),
              offset >= 0,
              offset < totalThreads else {
            throw APIError.httpStatus(400)
        }
        return offset
    }

    private func page(
        offset: Int,
        revision: String? = nil,
        forcedNextCursor: String? = nil
    ) -> MailboxResponse {
        let count = min(pageSize, totalThreads - offset)
        let rows = (offset..<(offset + count)).map { index in
            makeMailboxRow(
                threadID: "bulk-\(index)",
                latestSourceRecordID: "bulk-message-\(index)",
                receivedAt: "2026-07-24T12:00:00Z",
                title: "\(mailboxTitlePrefix) \(index)"
            )
        }
        let nextOffset = offset + count
        let reportedTotal = growingImportTotals && importInProgress
            ? min(totalThreads, nextOffset)
            : totalThreads
        let reportsAuthoritativeProgress = authoritativeGeneration != nil
        let progressiveGeneration = authoritativeGeneration
            ?? (importInProgress ? "bulk-import-generation-1" : nil)
        return MailboxResponse(
            label: .inbox,
            totalThreads: reportedTotal,
            nextCursor: forcedNextCursor ?? (nextOffset < totalThreads ? "bulk-cursor-\(nextOffset)" : nil),
            loadedThreads: count,
            windowDays: 90,
            sections: [GmailThreadSection(id: "bulk", title: "Bulk", rows: rows)],
            readyCount: totalThreads,
            pendingCount: 0,
            mailboxRevision: revision ?? mailboxRevision,
            fullImportRunning: importInProgress,
            fullImportCompleted: !importInProgress,
            syncGeneration: progressiveGeneration,
            phase: reportsAuthoritativeProgress ? "complete" : (importInProgress ? "importing_history_metadata" : nil),
            initialTargetCount: reportsAuthoritativeProgress || importInProgress ? min(100, totalThreads) : nil,
            initialMetadataCount: reportsAuthoritativeProgress || importInProgress ? min(100, totalThreads) : nil,
            initialBodyTargetCount: reportsAuthoritativeProgress || importInProgress ? min(25, totalThreads) : nil,
            initialBodyReadyCount: reportsAuthoritativeProgress || importInProgress ? min(25, totalThreads) : nil,
            historyMetadataCount: reportsAuthoritativeProgress ? totalThreads : (importInProgress ? nextOffset : nil),
            historyBodyReadyCount: reportsAuthoritativeProgress ? totalThreads : (importInProgress ? 25 : nil),
            estimatedTotalCount: reportsAuthoritativeProgress || importInProgress ? totalThreads : nil,
            initialWindowComplete: reportsAuthoritativeProgress || importInProgress ? true : nil,
            historyMetadataComplete: reportsAuthoritativeProgress ? true : (importInProgress ? false : nil),
            historyBodyComplete: reportsAuthoritativeProgress ? true : (importInProgress ? false : nil),
            lastProgressAt: reportsAuthoritativeProgress || importInProgress
                ? "2026-07-25T12:00:\(String(format: "%02d", min(59, offset / 10)))Z"
                : nil
        )
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
            fullImportCompleted: true
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
    private(set) var statusServerSendIDs: [String] = []
    private(set) var retryServerSendIDs: [String] = []

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

    func sendStatus(serverSendID: String) async throws -> MailSendResponse {
        statusServerSendIDs.append(serverSendID)
        return MailSendResponse(
            clientSendID: "client-send-1",
            serverSendID: serverSendID,
            mailboxThreadID: nil,
            gmailThreadID: nil,
            gmailMessageID: nil,
            state: .failed,
            queuedAt: "2026-05-21T09:00:00Z",
            sentAt: nil,
            error: "Temporary failure.",
            reauthURL: nil
        )
    }

    func retrySend(serverSendID: String) async throws -> MailSendResponse {
        retryServerSendIDs.append(serverSendID)
        return MailSendResponse(
            clientSendID: "client-send-1",
            serverSendID: serverSendID,
            mailboxThreadID: nil,
            gmailThreadID: nil,
            gmailMessageID: nil,
            state: .queued,
            queuedAt: "2026-05-21T09:01:00Z",
            sentAt: nil,
            error: nil,
            reauthURL: nil
        )
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
    var ignoresThreadCancellation = false
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
        recordThreadCall(threadID)
        if ignoresThreadCancellation {
            await withCheckedContinuation { continuation in
                DispatchQueue.global().asyncAfter(deadline: .now() + .milliseconds(50)) {
                    continuation.resume()
                }
            }
        } else {
            try await Task.sleep(nanoseconds: 50_000_000)
        }
        if threadID == "duplicate-paged-thread" {
            let messageIDs: [String]
            let hasMore: Bool
            switch offset {
            case 0:
                messageIDs = ["duplicate-0", "duplicate-1"]
                hasMore = true
            case 2:
                messageIDs = ["duplicate-1", "duplicate-2"]
                hasMore = true
            case 4:
                messageIDs = ["duplicate-2", "duplicate-3"]
                hasMore = false
            default:
                messageIDs = []
                hasMore = false
            }
            let pageMessages = messageIDs.enumerated().map { index, messageID in
                ThreadMessage(
                    id: messageID,
                    source: .gmail,
                    threadID: "gmail-duplicate-paged",
                    fromAddress: "sender@example.com",
                    to: "me@example.com",
                    cc: nil,
                    bcc: nil,
                    subject: "Long thread with duplicates",
                    body: "Message \(index)",
                    htmlBody: nil,
                    htmlRenderDocument: nil,
                    snippet: nil,
                    labelIDs: ["INBOX"],
                    receivedAt: "2026-05-29T10:00:00+00:00"
                )
            }
            return ThreadReaderResponse(
                entityID: "duplicate-paged-thread",
                userID: DemoAppFixtures.userID,
                source: .gmail,
                gmailThreadID: "gmail-duplicate-paged",
                subject: "Long thread with duplicates",
                totalMessages: 4,
                limit: 2,
                offset: offset,
                hasMore: hasMore,
                messages: pageMessages
            )
        }
        if threadID == "paged-thread" {
            let allMessages = (0..<3).map { index in
                ThreadMessage(
                    id: "paged-\(index)",
                    source: .gmail,
                    threadID: "gmail-paged",
                    fromAddress: "sender@example.com",
                    to: "me@example.com",
                    cc: nil,
                    bcc: nil,
                    subject: "Long thread",
                    body: "Message \(index)",
                    htmlBody: nil,
                    htmlRenderDocument: nil,
                    snippet: nil,
                    labelIDs: ["INBOX"],
                    receivedAt: "2026-05-29T10:0\(index):00+00:00"
                )
            }
            let pageMessages = offset == 0 ? Array(allMessages.prefix(2)) : Array(allMessages.dropFirst(2))
            return ThreadReaderResponse(
                entityID: "paged-thread",
                userID: DemoAppFixtures.userID,
                source: .gmail,
                gmailThreadID: "gmail-paged",
                subject: "Long thread",
                totalMessages: allMessages.count,
                limit: 2,
                offset: offset,
                hasMore: offset == 0,
                messages: pageMessages
            )
        }
        return DemoAppFixtures.threads[threadID] ?? DemoAppFixtures.threads["demo-google-today"]!
    }

    private func recordThreadCall(_ threadID: String) {
        lock.lock()
        defer { lock.unlock() }
        lockedThreadCallCounts[threadID, default: 0] += 1
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
