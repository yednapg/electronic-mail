import XCTest
@testable import ElectronicMailCore

final class APIClientTests: XCTestCase {
    override func tearDown() {
        MockURLProtocol.requestHandler = nil
        super.tearDown()
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
                user: AuthUserResponse(id: "user-1", email: "gaurav@example.com", displayName: "Gaurav", accessEnabled: true)
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
