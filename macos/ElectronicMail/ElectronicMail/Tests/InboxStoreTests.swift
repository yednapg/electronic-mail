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

    func testThreadPrefetchDedupesInFlightRequests() async {
        let client = SlowThreadAppClient()
        let store = InboxStore(client: client, sessionCache: AppSessionCache(defaults: .ephemeral()), threadCache: ThreadCache(defaults: .ephemeral()))

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

        XCTAssertEqual(snapshot.now.map(\.entityID), ["demo-apple-today"])
        XCTAssertEqual(snapshot.today.map(\.entityID), ["demo-github-today", "manual-task:demo-manual-seed"])
        XCTAssertEqual(snapshot.worthKnowing.map(\.entityID), ["demo-rbi-today"])
        XCTAssertTrue(snapshot.agenda.isEmpty)
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
}

private final class FailingAppClient: AppClient {
    var baseURL = AppConfiguration.defaultBackendURL
    var sessionToken: String?
    let mode: AppRunMode = .localBackend

    func exchangeMobileSession(loginCode: String) async throws -> MobileSessionExchangeResponse { throw APIError.httpStatus(503) }
    func appSession() async throws -> AppSessionResponse { throw APIError.httpStatus(503) }
    func mailbox(label: MailboxLabel, limit: Int, cursor: String?) async throws -> MailboxResponse { throw APIError.httpStatus(503) }
    func thread(threadID: String, limit: Int, offset: Int) async throws -> ThreadReaderResponse { throw APIError.httpStatus(503) }
    func triggerMailboxSync() async throws -> MailboxSyncTriggerResponse { throw APIError.httpStatus(503) }
    func syncMailboxNow() async throws -> MailboxSyncTriggerResponse { throw APIError.httpStatus(503) }
    func archiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse { throw APIError.httpStatus(503) }
    func unarchiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse { throw APIError.httpStatus(503) }
    func markThreadRead(_ threadID: String) async throws -> GmailThreadMutationResponse { throw APIError.httpStatus(503) }
    func createTask(_ request: TaskCreateRequest) async throws -> TaskResponse { throw APIError.httpStatus(503) }
    func updateTask(_ taskID: String, request: TaskUpdateRequest) async throws -> TaskResponse { throw APIError.httpStatus(503) }
    func completeEntity(_ entityID: String, request: EntityOutcomeRequest) async throws -> EntityOutcomeResponse { throw APIError.httpStatus(503) }
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
