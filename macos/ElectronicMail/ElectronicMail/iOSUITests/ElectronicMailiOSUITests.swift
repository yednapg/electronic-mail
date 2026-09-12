import XCTest

final class ElectronicMailiOSUITests: XCTestCase {
    private var app: XCUIApplication!

    override func setUpWithError() throws {
        continueAfterFailure = false
        app = XCUIApplication()
        app.launchArguments = [
            "-ElectronicMailDemo",
            "-AppleLanguages", "(en)",
            "-AppleLocale", "en_US",
        ]
        app.launch()
        XCTAssertTrue(app.navigationBars["Inbox"].waitForExistence(timeout: 10))
    }

    func testDrawerContainsEveryPrimaryDestination() {
        app.buttons["mailbox.menu"].tap()
        XCTAssertTrue(app.otherElements["mailbox.drawer"].waitForExistence(timeout: 2))

        for identifier in [
            "drawer.inbox", "drawer.ai-inbox", "drawer.starred", "drawer.drafts",
            "drawer.sent", "drawer.spam", "drawer.trash", "drawer.archive",
            "drawer.all-mail", "drawer.to-dos",
        ] {
            XCTAssertTrue(app.buttons[identifier].exists, "Missing \(identifier)")
        }
    }

    func testOpensAIInboxFromDrawer() {
        app.buttons["mailbox.menu"].tap()
        app.buttons["drawer.ai-inbox"].tap()
        XCTAssertTrue(app.navigationBars["AI Inbox"].waitForExistence(timeout: 5))
    }

    func testComposeUsesFullScreenComposer() {
        app.buttons["mailbox.compose"].tap()
        XCTAssertTrue(app.navigationBars["New Message"].waitForExistence(timeout: 3))
        XCTAssertTrue(app.buttons["Send"].exists)
        XCTAssertTrue(app.buttons["Cancel"].exists)
    }
}
