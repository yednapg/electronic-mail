import XCTest
@testable import DecisionPipelineCore

final class APIClientTests: XCTestCase {
    override func tearDown() {
        MockURLProtocol.requestHandler = nil
        super.tearDown()
    }

    func testDashboardRequestDecodesResponse() async throws {
        let client = makeClient { request in
            XCTAssertEqual(request.url?.path(percentEncoded: true), "/dashboard")
            let data = try JSONEncoder.backend.encode(DashboardResponse.fixture(connected: true))
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, data)
        }

        let dashboard = try await client.dashboard()

        XCTAssertEqual(dashboard.auth.connected, true)
    }

    func testArchiveThreadUsesPostEndpoint() async throws {
        let client = makeClient { request in
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(request.url?.path(percentEncoded: true), "/gmail/threads/thread-1/archive")
            let data = try JSONEncoder.backend.encode(GmailThreadMutationResponse(threadID: "thread-1", action: .archive))
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, data)
        }

        let response = try await client.archiveThread("thread-1")

        XCTAssertEqual(response.action, .archive)
    }

    func testTraceEncodesEntityIDAsSinglePathSegment() async throws {
        let client = makeClient { request in
            XCTAssertEqual(request.url?.path(percentEncoded: true), "/trace/entity%2Fwith%20space")
            let data = try JSONEncoder.backend.encode(
                TraceReplayResponse(entityID: "entity-1", sourceRecordIDs: ["source-1"], items: [])
            )
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, data)
        }

        let response = try await client.trace(entityID: "entity/with space")

        XCTAssertEqual(response.entityID, "entity-1")
    }

    func testArchiveEncodesThreadIDAsSinglePathSegment() async throws {
        let client = makeClient { request in
            XCTAssertEqual(request.url?.path(percentEncoded: true), "/gmail/threads/thread%2Fwith%20space/archive")
            let data = try JSONEncoder.backend.encode(GmailThreadMutationResponse(threadID: "thread/with space", action: .archive))
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, data)
        }

        let response = try await client.archiveThread("thread/with space")

        XCTAssertEqual(response.threadID, "thread/with space")
    }

    func testNonSuccessStatusThrows() async {
        let client = makeClient { request in
            (HTTPURLResponse(url: request.url!, statusCode: 500, httpVersion: nil, headerFields: nil)!, Data())
        }

        do {
            _ = try await client.dashboard()
            XCTFail("Expected request to throw")
        } catch APIError.httpStatus(let status) {
            XCTAssertEqual(status, 500)
        } catch {
            XCTFail("Unexpected error: \(error)")
        }
    }

    private func makeClient(
        handler: @escaping (URLRequest) throws -> (HTTPURLResponse, Data)
    ) -> DashboardAPIClient {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [MockURLProtocol.self]
        MockURLProtocol.requestHandler = handler

        return DashboardAPIClient(
            baseURL: URL(string: "http://localhost:3001")!,
            session: URLSession(configuration: configuration)
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
