import XCTest
@testable import ElectronicMailiOS

@MainActor
final class ElectronicMailiOSTests: XCTestCase {
    func testAccountChangeResetsEveryPresentedRoute() {
        let router = IOSRouter()
        router.path = [.settings(.accounts), .todoSource("thread-1")]
        router.drawerPresented = true
        router.sheet = .settings
        router.composer = .compose(gmailAccountID: "account-1")

        router.resetForAccountChange()

        XCTAssertTrue(router.path.isEmpty)
        XCTAssertFalse(router.drawerPresented)
        XCTAssertNil(router.sheet)
        XCTAssertNil(router.composer)
    }

    func testNewComposeRouteHasStableRecoveryIdentity() {
        XCTAssertEqual(IOSComposerContext.compose().id, "compose-new")
        XCTAssertEqual(
            IOSComposerContext.compose(gmailAccountID: "account-1").id,
            IOSComposerContext.compose(gmailAccountID: "account-2").id
        )
    }

    func testSettingsExposeEveryDesktopCategory() {
        XCTAssertEqual(
            IOSSettingsDestination.allCases.map(\.title),
            ["General", "Composing", "Accounts", "AI Inbox", "Account & Data", "About & Legal"]
        )
    }

    func testOAuthLoopbackRelayIsLimitedToPhysicalDeviceLANDevelopment() {
        XCTAssertTrue(
            IOSLoopbackOAuthRelay.requiresRelay(
                for: URL(string: "http://192.168.1.20:3002")!
            )
        )
        XCTAssertFalse(
            IOSLoopbackOAuthRelay.requiresRelay(
                for: URL(string: "http://localhost:3001")!
            )
        )
        XCTAssertFalse(
            IOSLoopbackOAuthRelay.requiresRelay(
                for: URL(string: "https://api.electronicmail.app")!
            )
        )
        XCTAssertEqual(
            IOSLoopbackOAuthRelay.redirectBaseURL(
                for: URL(string: "http://192.168.1.20:3002")!
            ).absoluteString,
            "http://localhost:3001"
        )
        XCTAssertEqual(
            IOSLoopbackOAuthRelay.redirectBaseURL(
                for: URL(string: "https://api.electronicmail.app")!
            ).absoluteString,
            "https://api.electronicmail.app"
        )
    }
}
