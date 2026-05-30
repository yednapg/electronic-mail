import XCTest
@testable import ElectronicMailCore

final class APIClientTests: XCTestCase {
    override func tearDown() {
        MockURLProtocol.requestHandler = nil
        super.tearDown()
    }

    func testServerSentEventParserHandlesNamedEventsAndMultilineData() {
        var parser = ServerSentEventParser()

        XCTAssertNil(parser.feed(line: ": keepalive"))
        XCTAssertNil(parser.feed(line: "id: 42"))
        XCTAssertNil(parser.feed(line: "event: mailbox-changed"))
        XCTAssertNil(parser.feed(line: "data: {\"one\":true,"))
        XCTAssertNil(parser.feed(line: "data: \"two\":true}"))
        let event = parser.feed(line: "")

        XCTAssertEqual(event?.id, "42")
        XCTAssertEqual(event?.event, "mailbox-changed")
        XCTAssertEqual(event?.data, "{\"one\":true,\n\"two\":true}")
    }

    func testAppSessionRequestUsesBackendSessionEndpoint() async throws {
        let client = makeClient { request in
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(request.url?.path(percentEncoded: true), "/v1/app/session")
            let data = try JSONEncoder.backend.encode(DemoAppFixtures.appSession)
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, data)
        }

        let session = try await client.appSession()

        XCTAssertEqual(session.user.id, "demo-user")
        XCTAssertEqual(session.mailbox.totalThreads, DemoAppFixtures.mailbox.totalThreads)
    }

    func testMobileSessionExchangeUsesBackendEndpoint() async throws {
        let client = makeClient { request in
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(request.url?.path(percentEncoded: true), "/v1/auth/mobile/exchange")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Content-Type"), "application/json")
            let response = MobileSessionExchangeResponse(
                sessionToken: "live-session-token",
                expiresAt: "2026-06-15T00:00:00+00:00",
                user: AuthUserResponse(id: "user-1", email: "demo@example.test", displayName: "TestUser", accessEnabled: true)
            )
            let data = try JSONEncoder.backend.encode(response)
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, data)
        }

        let response = try await client.exchangeMobileSession(loginCode: "one-time-code")

        XCTAssertEqual(response.sessionToken, "live-session-token")
    }

    func testBearerSessionTokenIsSentToBackend() async throws {
        let client = makeClient { request in
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer live-session-token")
            let data = try JSONEncoder.backend.encode(DemoAppFixtures.appSession)
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, data)
        }
        client.sessionToken = "live-session-token"

        _ = try await client.appSession()
    }

    func testMailboxRequestUsesLabelAndLimitQuery() async throws {
        let client = makeClient { request in
            XCTAssertEqual(request.url?.path(percentEncoded: true), "/v1/mailbox")
            XCTAssertEqual(request.url?.query(percentEncoded: false), "label=inbox&limit=100")
            let data = try JSONEncoder.backend.encode(DemoAppFixtures.mailbox)
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, data)
        }

        let mailbox = try await client.mailbox(label: .inbox, limit: 100, cursor: nil)

        XCTAssertEqual(mailbox.sections.map(\.title), ["Today", "Past 7 days", "Earlier this month"])
    }

    func testSyncMailboxNowUsesForegroundEndpoint() async throws {
        let client = makeClient { request in
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(request.url?.path(percentEncoded: true), "/v1/mailbox/sync-now")
            let response = try self.awaitResponse(status: "synced")
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, response)
        }

        let response = try await client.syncMailboxNow()

        XCTAssertEqual(response.status, "synced")
    }

    func testMailboxSyncStateUsesBackendSyncStateEndpoint() async throws {
        let client = makeClient { request in
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(request.url?.path(percentEncoded: true), "/v1/mailbox/sync-state")
            let response = MailboxSyncStateResponse(
                connected: true,
                lastHistoryID: "history-1",
                lastFullSyncAt: nil,
                watchExpirationAt: nil,
                lastSyncStartedAt: nil,
                lastSyncCompletedAt: nil,
                lastSyncError: nil,
                mailboxRevision: "rev-1",
                totalThreads: 12
            )
            let data = try JSONEncoder.backend.encode(response)
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, data)
        }

        let response = try await client.mailboxSyncState()

        XCTAssertEqual(response.mailboxRevision, "rev-1")
        XCTAssertEqual(response.totalThreads, 12)
    }

    func testThreadIDIsEncodedAsSinglePathSegment() async throws {
        let client = makeClient { request in
            XCTAssertEqual(request.url?.path(percentEncoded: true), "/v1/mailbox/threads/thread%2Fwith%20space")
            XCTAssertEqual(request.url?.query(percentEncoded: false), "limit=50&offset=0")
            let data = try JSONEncoder.backend.encode(DemoAppFixtures.threads["demo-google-today"]!)
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, data)
        }

        let response = try await client.thread(threadID: "thread/with space", limit: 50, offset: 0)

        XCTAssertEqual(response.gmailThreadID, "demo-google-today")
    }

    func testArchiveThreadUsesPostEndpoint() async throws {
        let client = makeClient { request in
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(request.url?.path(percentEncoded: true), "/gmail/threads/thread%2Fwith%20space/archive")
            let data = try JSONEncoder.backend.encode(GmailThreadMutationResponse(threadID: "thread/with space", action: .archive))
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, data)
        }

        let response = try await client.archiveThread("thread/with space")

        XCTAssertEqual(response.threadID, "thread/with space")
        XCTAssertEqual(response.action, .archive)
    }

    func testQueuedThreadActionUsesMailboxEndpoint() async throws {
        let client = makeClient { request in
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(request.url?.path(percentEncoded: true), "/v1/mailbox/thread-actions")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Content-Type"), "application/json")
            let payload = try JSONDecoder.backend.decode(QueuedThreadActionRequest.self, from: self.requestBodyData(request))
            XCTAssertEqual(payload.mailboxThreadID, "group-1")
            XCTAssertEqual(payload.action, .archive)
            let response = QueuedThreadActionResponse(
                clientActionID: payload.clientActionID,
                serverActionID: "server-1",
                mailboxThreadID: payload.mailboxThreadID,
                targetMessageID: payload.targetMessageID,
                action: payload.action,
                state: .queued,
                queuedAt: payload.createdAt,
                appliedAt: nil,
                error: nil
            )
            let data = try JSONEncoder.backend.encode(response)
            return (HTTPURLResponse(url: request.url!, statusCode: 202, httpVersion: nil, headerFields: nil)!, data)
        }

        let response = try await client.enqueueThreadAction(
            QueuedThreadActionRequest(
                clientActionID: "client-1",
                mailboxThreadID: "group-1",
                targetMessageID: nil,
                action: .archive,
                createdAt: "2026-05-21T09:00:00Z"
            )
        )

        XCTAssertEqual(response.serverActionID, "server-1")
        XCTAssertEqual(response.state, .queued)
    }

    func testComposeUsesMailboxSendEndpoint() async throws {
        let client = makeClient { request in
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(request.url?.path(percentEncoded: true), "/v1/mailbox/compose")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Content-Type"), "application/json")
            let payload = try JSONDecoder.backend.decode(MailComposeRequest.self, from: self.requestBodyData(request))
            XCTAssertEqual(payload.clientSendID, "client-send-1")
            XCTAssertEqual(payload.to, ["recipient@example.com"])
            XCTAssertEqual(payload.subject, "Hello")
            XCTAssertEqual(payload.bodyText, "Body")
            let response = MailSendResponse(
                clientSendID: payload.clientSendID,
                serverSendID: "server-send-1",
                mailboxThreadID: nil,
                gmailThreadID: nil,
                gmailMessageID: nil,
                state: .queued,
                queuedAt: payload.createdAt,
                sentAt: nil,
                error: nil,
                reauthURL: nil
            )
            let data = try JSONEncoder.backend.encode(response)
            return (HTTPURLResponse(url: request.url!, statusCode: 202, httpVersion: nil, headerFields: nil)!, data)
        }

        let response = try await client.sendCompose(
            MailComposeRequest(
                clientSendID: "client-send-1",
                to: ["recipient@example.com"],
                cc: [],
                bcc: [],
                subject: "Hello",
                bodyText: "Body",
                bodyHTML: nil,
                createdAt: "2026-05-21T09:00:00Z"
            )
        )

        XCTAssertEqual(response.serverSendID, "server-send-1")
        XCTAssertEqual(response.state, .queued)
    }

    func testReplyUsesMailboxThreadReplyEndpoint() async throws {
        let client = makeClient { request in
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(request.url?.path(percentEncoded: true), "/v1/mailbox/threads/group%2Fwith%20space/reply")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Content-Type"), "application/json")
            let payload = try JSONDecoder.backend.decode(MailReplyRequest.self, from: self.requestBodyData(request))
            XCTAssertEqual(payload.clientSendID, "client-send-1")
            XCTAssertEqual(payload.bodyText, "Reply body")
            let response = MailSendResponse(
                clientSendID: payload.clientSendID,
                serverSendID: "server-send-1",
                mailboxThreadID: "group/with space",
                gmailThreadID: "gmail-thread-1",
                gmailMessageID: nil,
                state: .queued,
                queuedAt: payload.createdAt,
                sentAt: nil,
                error: nil,
                reauthURL: nil
            )
            let data = try JSONEncoder.backend.encode(response)
            return (HTTPURLResponse(url: request.url!, statusCode: 202, httpVersion: nil, headerFields: nil)!, data)
        }

        let response = try await client.sendReply(
            threadID: "group/with space",
            request: MailReplyRequest(
                clientSendID: "client-send-1",
                cc: [],
                bcc: [],
                bodyText: "Reply body",
                bodyHTML: nil,
                createdAt: "2026-05-21T09:00:00Z"
            )
        )

        XCTAssertEqual(response.mailboxThreadID, "group/with space")
        XCTAssertEqual(response.gmailThreadID, "gmail-thread-1")
    }

    func testOfflineFirstClientStoresAndReplaysQueuedThreadAction() async throws {
        let backend = ToggleThreadActionAppClient()
        let localStore = MemoryLocalMailStore()
        localStore.writeSession(DemoAppFixtures.appSession)
        let client = OfflineFirstAppClient(backend: backend, localMailStore: localStore)

        backend.shouldFailThreadAction = true
        let offlineResponse = try await client.archiveThread("group-1")

        XCTAssertEqual(offlineResponse.threadID, "group-1")
        XCTAssertEqual(offlineResponse.action, .archive)
        XCTAssertEqual(localStore.pendingThreadActions().map(\.mailboxThreadID), ["group-1"])
        XCTAssertTrue(backend.enqueuedActions.isEmpty)

        backend.shouldFailThreadAction = false
        _ = try await client.appSession()

        XCTAssertTrue(localStore.pendingThreadActions().isEmpty)
        XCTAssertEqual(backend.enqueuedActions.map(\.mailboxThreadID), ["group-1"])
        XCTAssertEqual(backend.enqueuedActions.map(\.action), [.archive])
    }

    func testCreateTaskUsesBackendEndpoint() async throws {
        let client = makeClient { request in
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(request.url?.path(percentEncoded: true), "/v1/tasks")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Content-Type"), "application/json")
            let task = TaskResponse(
                id: "task-1",
                userID: "user-1",
                entityID: "manual-task:task-1",
                title: "New to-do",
                notes: "Notes",
                section: "today",
                dueAt: nil,
                status: "open",
                createdAt: "2026-05-16T09:30:00+05:30",
                updatedAt: "2026-05-16T09:30:00+05:30"
            )
            let data = try JSONEncoder.backend.encode(task)
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, data)
        }

        let response = try await client.createTask(TaskCreateRequest(title: "New to-do", notes: "Notes", section: "today", dueAt: nil))

        XCTAssertEqual(response.entityID, "manual-task:task-1")
    }

    func testCompleteEntityUsesBackendEndpoint() async throws {
        let client = makeClient { request in
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(request.url?.path(percentEncoded: true), "/v1/entities/entity%2Fwith%20space/complete")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Content-Type"), "application/json")
            let outcome = EntityOutcomeResponse(
                id: "outcome-1",
                userID: "user-1",
                entityID: "entity/with space",
                outcomeType: "complete",
                snoozeUntil: nil,
                note: "Done",
                createdAt: "2026-05-16T09:30:00+05:30"
            )
            let data = try JSONEncoder.backend.encode(outcome)
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, data)
        }

        let response = try await client.completeEntity("entity/with space", request: EntityOutcomeRequest(note: "Done"))

        XCTAssertEqual(response.outcomeType, "complete")
    }

    func testNonSuccessStatusThrows() async {
        let client = makeClient { request in
            (HTTPURLResponse(url: request.url!, statusCode: 500, httpVersion: nil, headerFields: nil)!, Data())
        }

        do {
            _ = try await client.appSession()
            XCTFail("Expected request to throw")
        } catch APIError.httpStatus(let status) {
            XCTAssertEqual(status, 500)
        } catch {
            XCTFail("Unexpected error: \(error)")
        }
    }

    private func makeClient(
        handler: @escaping (URLRequest) throws -> (HTTPURLResponse, Data)
    ) -> LiveBackendAppClient {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [MockURLProtocol.self]
        MockURLProtocol.requestHandler = handler

        return LiveBackendAppClient(
            baseURL: URL(string: "http://localhost:3001")!,
            session: URLSession(configuration: configuration)
        )
    }

    private func awaitResponse(status: String) throws -> Data {
        try JSONEncoder.backend.encode(
            MailboxSyncTriggerResponse(
                status: status,
                state: MailboxSyncStateResponse(
                    connected: true,
                    lastHistoryID: "history-1",
                    lastFullSyncAt: nil,
                    watchExpirationAt: nil,
                    lastSyncStartedAt: nil,
                    lastSyncCompletedAt: nil,
                    lastSyncError: nil,
                    totalThreads: 1
                ),
                jobID: nil,
                queuedAt: nil
            )
        )
    }

    private func requestBodyData(_ request: URLRequest) -> Data {
        if let body = request.httpBody {
            return body
        }
        guard let stream = request.httpBodyStream else {
            return Data()
        }
        stream.open()
        defer { stream.close() }

        var data = Data()
        let bufferSize = 1024
        let buffer = UnsafeMutablePointer<UInt8>.allocate(capacity: bufferSize)
        defer { buffer.deallocate() }
        while stream.hasBytesAvailable {
            let read = stream.read(buffer, maxLength: bufferSize)
            if read <= 0 {
                break
            }
            data.append(buffer, count: read)
        }
        return data
    }
}

final class MockURLProtocol: URLProtocol {
    static var requestHandler: ((URLRequest) throws -> (HTTPURLResponse, Data))?

    override class func canInit(with request: URLRequest) -> Bool {
        true
    }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest {
        request
    }

    override func startLoading() {
        guard let handler = Self.requestHandler else {
            client?.urlProtocol(self, didFailWithError: APIError.emptyResponse)
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

private final class ToggleThreadActionAppClient: AppClient {
    var baseURL = URL(string: "http://localhost:3001")!
    var sessionToken: String?
    let mode: AppRunMode = .localBackend
    var shouldFailThreadAction = false
    private(set) var enqueuedActions: [QueuedThreadActionRequest] = []

    func exchangeMobileSession(loginCode: String) async throws -> MobileSessionExchangeResponse {
        MobileSessionExchangeResponse(
            sessionToken: "session",
            expiresAt: "2026-06-15T00:00:00+00:00",
            user: AuthUserResponse(id: "demo-user", email: "demo@example.com", displayName: "Demo", accessEnabled: true)
        )
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
        if shouldFailThreadAction {
            throw APIError.httpStatus(503)
        }
        enqueuedActions.append(request)
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
