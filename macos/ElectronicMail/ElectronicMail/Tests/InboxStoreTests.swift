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
        let mailbox = makeSingleRowMailbox(threadID: "offline-thread", title: "Offline mail")
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
        async let first: Void = store.prefetchThread(threadID: "demo-special", force: true, silent: false)
        async let second: Void = store.prefetchThread(threadID: "demo-special", force: true, silent: false)
        _ = await (first, second)

        XCTAssertEqual(client.threadCallCounts["demo-special"], 1)
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
        await fetch.value

        XCTAssertTrue(store.openedThreads.isEmpty)
        XCTAssertNil(
            localStore.readThread(
                userID: DemoAppFixtures.userID,
                threadID: "demo-google-today"
            )
        )
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

        let firstTrigger = Task { await store.loadMoreMailbox(automatic: true) }
        let duplicateTrigger = Task { await store.loadMoreMailbox() }
        await firstTrigger.value
        await duplicateTrigger.value

        let secondTrigger = Task { await store.loadMoreMailbox(automatic: true) }
        let secondDuplicateTrigger = Task { await store.loadMoreMailbox(automatic: true) }
        await secondTrigger.value
        await secondDuplicateTrigger.value
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
        XCTAssertNil(store.mailboxFooterText)
    }

    func testSwitchingMailboxesCancelsAndIsolatesOldPagination() async {
        let client = MailboxPaginationRaceAppClient()
        let store = InboxStore(
            client: client,
            sessionCache: AppSessionCache(defaults: .ephemeral()),
            threadCache: ThreadCache(defaults: .ephemeral()),
            automaticallyPrefetchThreads: false
        )
        store.setSessionToken("live-session-token")

        await store.load()
        let staleInboxPage = Task { await store.loadMoreMailbox() }
        for _ in 0..<100 {
            if client.mailboxCalls.contains(MailboxPageCall(label: .inbox, cursor: "inbox-cursor-2")) {
                break
            }
            try? await Task.sleep(nanoseconds: 1_000_000)
        }
        XCTAssertTrue(client.mailboxCalls.contains(MailboxPageCall(label: .inbox, cursor: "inbox-cursor-2")))
        XCTAssertTrue(store.mailboxPageLoading)

        await store.setMailboxLabel(.sent)
        let currentSentPage = Task { await store.loadMoreMailbox() }
        for _ in 0..<100 {
            if client.mailboxCalls.contains(MailboxPageCall(label: .sent, cursor: "sent-cursor-2")) {
                break
            }
            try? await Task.sleep(nanoseconds: 1_000_000)
        }
        XCTAssertTrue(client.mailboxCalls.contains(MailboxPageCall(label: .sent, cursor: "sent-cursor-2")))

        await staleInboxPage.value
        XCTAssertEqual(store.activeMailboxLabel, .sent)
        XCTAssertEqual(store.flatRows.map(\.threadID), ["sent-1"])
        XCTAssertTrue(store.mailboxPageLoading, "A late Inbox completion must not clear the active Sent loading state")

        await currentSentPage.value
        XCTAssertEqual(store.flatRows.map(\.threadID), ["sent-1", "sent-2"])
        XCTAssertFalse(store.flatRows.map(\.threadID).contains("inbox-2"))
        XCTAssertFalse(store.mailboxPageLoading)
        XCTAssertEqual(
            client.mailboxCalls,
            [
                MailboxPageCall(label: .inbox, cursor: nil),
                MailboxPageCall(label: .inbox, cursor: "inbox-cursor-2"),
                MailboxPageCall(label: .sent, cursor: nil),
                MailboxPageCall(label: .sent, cursor: "sent-cursor-2")
            ]
        )
    }

    func testEmptyMailboxNeverShowsPaginationOrSyncingFooter() async {
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
        XCTAssertNil(store.mailboxFooter)
        XCTAssertFalse(store.canLoadMoreMailbox)
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

    func testMailboxRevisionChangeDiscardsStaleLaterPageRows() {
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

        XCTAssertEqual(merged.sections.flatMap(\.rows).map(\.threadID), ["first-page"])
        XCTAssertEqual(merged.loadedThreads, 1)
        XCTAssertNil(merged.nextCursor)
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

private func makeThreadAttachment(filename: String) -> ThreadAttachment {
    ThreadAttachment(
        id: "attachment-1",
        filename: filename,
        mimeType: "application/pdf",
        size: 42,
        attachmentID: "gmail-attachment-1",
        partID: "part-1",
        downloadURL: nil
    )
}

private func makeMailboxRow(
    threadID: String,
    entityID: String? = nil,
    latestSourceRecordID: String,
    receivedAt: String,
    title: String,
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
        messageCount: updates.count,
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
                to: "demo@example.test",
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

private final class AttachmentDownloadingAppClient: AppClient {
    var baseURL = AppConfiguration.defaultBackendURL
    var sessionToken: String?
    let mode: AppRunMode = .demo

    private let demo = DemoAppClient()
    private let downloadedAttachment: DownloadedAttachment
    private(set) var requestedMessageID: String?
    private(set) var requestedAttachment: ThreadAttachment?

    init(downloadedAttachment: DownloadedAttachment) {
        self.downloadedAttachment = downloadedAttachment
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
        requestedMessageID = messageID
        requestedAttachment = attachment
        return downloadedAttachment
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
    private(set) var searchCalls: [MailboxSearchCall] = []

    init(
        searchResponses: [MailboxResponse],
        suppressedResponseDelayNanoseconds: UInt64 = 0
    ) {
        self.searchResponses = searchResponses
        self.suppressedResponseDelayNanoseconds = suppressedResponseDelayNanoseconds
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
            importCompleted: false
        )
    }

    private var inboxSecondPage: MailboxResponse {
        page(
            label: .inbox,
            threadID: "inbox-2",
            receivedAt: "2026-07-13T12:00:00+00:00",
            totalThreads: 3,
            nextCursor: "inbox-cursor-3",
            importCompleted: false
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
            importCompleted: false
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
