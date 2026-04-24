import XCTest
@testable import ElectronicMailCore

final class ModelDecodingTests: XCTestCase {
    func testDashboardResponseDecodesSharedContractFixture() throws {
        let dashboard = try JSONDecoder.backend.decode(
            DashboardResponse.self,
            from: contractFixtureData("dashboard.json")
        )

        XCTAssertTrue(dashboard.auth.connected)
        XCTAssertEqual(dashboard.profile?.email, "person@example.com")
        XCTAssertEqual(dashboard.feed.now.first?.entityID, "entity-1")
        XCTAssertEqual(dashboard.feed.now.first?.gmailThreadAction, .archive)
    }

    func testGoogleAuthStateDecodesSharedContractFixture() throws {
        let auth = try JSONDecoder.backend.decode(
            GoogleAuthState.self,
            from: contractFixtureData("google-auth-state.json")
        )

        XCTAssertTrue(auth.available)
        XCTAssertTrue(auth.connected)
    }

    func testTraceResponseDecodesSharedContractFixture() throws {
        let trace = try JSONDecoder.backend.decode(
            TraceReplayResponse.self,
            from: contractFixtureData("trace.json")
        )

        XCTAssertEqual(trace.entityID, "entity-1")
        XCTAssertEqual(trace.items.first?.output["merged"], .bool(true))
    }

    func testGmailMutationResponseDecodesSharedContractFixture() throws {
        let archive = try JSONDecoder.backend.decode(
            GmailThreadMutationResponse.self,
            from: contractFixtureData("gmail-thread-archive.json")
        )

        XCTAssertEqual(archive.threadID, "thread-1")
        XCTAssertEqual(archive.action, .archive)
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
