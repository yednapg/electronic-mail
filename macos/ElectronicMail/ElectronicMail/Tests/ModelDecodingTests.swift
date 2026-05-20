import XCTest
@testable import ElectronicMailCore

final class ModelDecodingTests: XCTestCase {
    func testThreadReaderResponseDecodesSharedContractFixture() throws {
        let thread = try JSONDecoder.backend.decode(
            ThreadReaderResponse.self,
            from: contractFixtureData("thread-reader.json")
        )

        XCTAssertEqual(thread.gmailThreadID, "thread-1")
        XCTAssertEqual(thread.messages.first?.id, "source-1")
    }

    func testGmailMutationResponseDecodesSharedContractFixture() throws {
        let archive = try JSONDecoder.backend.decode(
            GmailThreadMutationResponse.self,
            from: contractFixtureData("gmail-thread-archive.json")
        )

        XCTAssertEqual(archive.threadID, "thread-1")
        XCTAssertEqual(archive.action, .archive)
    }

    func testEmailBodyResolverRoutesBasicTextLinkHTMLToText() {
        let html = #"""
        <!doctype html>
        <html>
        <head>
          <meta name="viewport" content="width=device-width">
          <!--[if mso]><style>body { width: 600px; }</style><![endif]-->
        </head>
        <body>
          <p>Hello,</p>
          <p><a href="https://example.com">205759</a> is your one-time password.</p>
          <img src="https://u15089226.ct.sendgrid.net/wf/open?upn=tracking-pixel" width="1" height="1">
        </body>
        </html>
        """#
        let message = makeThreadMessage(
            id: "otp",
            body: #"Hello, 205759 (https://tracking.example.com/click) is your one-time password."#,
            htmlBody: html,
            htmlRenderDocument: html
        )

        let bodyKind = EmailReaderBodyResolver.bodyKind(message: message, fallbackText: "")

        XCTAssertEqual(bodyKind, .text("Hello, 205759 is your one-time password."))
    }

    func testEmailBodyResolverRoutesSinglePresentationTableWithTrackingPixelToText() {
        let html = #"""
        <!doctype html>
        <html>
        <head>
          <style>p { margin: 0 0 8px 0 !important; line-height: 20px !important; }</style>
        </head>
        <body>
          <div style="display:none">Just a few more fields.</div>
          <table width="100%" cellspacing="0" cellpadding="0" role="presentation">
            <tbody><tr><td style="font-family: Arial; font-size: 14px; line-height: 20px;">
              <p>Hey,</p>
              <p>Looks like you started a speedrun application but didn't hit submit.</p>
              <p>Finish your app here: <a href="https://speedrun.example">SR007</a></p>
            </td></tr></tbody>
          </table>
          <img src="https://go2.a16z.com/trk?t=1" width="1" height="1" style="display:none !important;" alt="">
        </body>
        </html>
        """#
        let message = makeThreadMessage(
            id: "speedrun",
            body: "Fallback body should not win",
            htmlBody: html,
            htmlRenderDocument: html
        )

        let bodyKind = EmailReaderBodyResolver.bodyKind(message: message, fallbackText: "")

        XCTAssertEqual(
            bodyKind,
            .text("Hey, Looks like you started a speedrun application but didn't hit submit. Finish your app here: SR007")
        )
    }

    func testEmailBodyResolverKeepsRichHTMLInHTMLRenderer() {
        let richHTML = #"<html><body><table style="width:100%"><tr><td><img src="https://example.com/logo.png">Rich email</td></tr></table></body></html>"#
        let message = makeThreadMessage(
            id: "rich",
            body: "Rich email",
            htmlBody: richHTML,
            htmlRenderDocument: richHTML
        )

        let bodyKind = EmailReaderBodyResolver.bodyKind(message: message, fallbackText: "")

        XCTAssertEqual(bodyKind, .html(richHTML))
    }

    func testThreadPresentationOrdersOldestToNewestAndExpandsLatest() {
        let newest = makeThreadMessage(id: "newest", receivedAt: "2026-05-19T19:33:06+00:00")
        let oldest = makeThreadMessage(id: "oldest", receivedAt: "2026-05-19T19:31:42+00:00")

        let ordered = EmailThreadPresentation.orderedMessages([newest, oldest])
        let items = EmailThreadPresentation.items(from: ordered)
        let latestKey = EmailThreadPresentation.latestMessageKey(in: items)

        XCTAssertEqual(ordered.map(\.id), ["oldest", "newest"])
        XCTAssertEqual(latestKey, "1::newest")
        XCTAssertFalse(
            EmailThreadPresentation.isExpanded(
                messageKey: "0::oldest",
                latestMessageKey: latestKey,
                userExpandedMessageKeys: []
            )
        )
        XCTAssertTrue(
            EmailThreadPresentation.isExpanded(
                messageKey: "1::newest",
                latestMessageKey: latestKey,
                userExpandedMessageKeys: []
            )
        )
    }

    func testThreadPresentationUsesUniqueKeysWhenMessageIDsRepeat() {
        let older = makeThreadMessage(id: "duplicate", receivedAt: "2026-05-19T19:31:42+00:00")
        let newer = makeThreadMessage(id: "duplicate", receivedAt: "2026-05-19T19:33:06+00:00")

        let items = EmailThreadPresentation.items(from: EmailThreadPresentation.orderedMessages([newer, older]))
        let latestKey = EmailThreadPresentation.latestMessageKey(in: items)

        XCTAssertEqual(items.map(\.id), ["0::duplicate", "1::duplicate"])
        XCTAssertFalse(
            EmailThreadPresentation.isExpanded(
                messageKey: "0::duplicate",
                latestMessageKey: latestKey,
                userExpandedMessageKeys: []
            )
        )
        XCTAssertTrue(
            EmailThreadPresentation.isExpanded(
                messageKey: "1::duplicate",
                latestMessageKey: latestKey,
                userExpandedMessageKeys: []
            )
        )
    }

    func testThreadPresentationDecodesNativeSubjectEntities() {
        let message = makeThreadMessage(subject: "You&#39;ve successfully modified card controls")

        XCTAssertEqual(
            EmailThreadPresentation.displaySubject(for: message),
            "You've successfully modified card controls"
        )
    }

    func testUserDefaultsSessionTokenStoreSavesLoadsAndClearsToken() throws {
        let defaults = UserDefaults.ephemeralTokenStoreDefaults()
        let store = UserDefaultsSessionTokenStore(defaults: defaults, key: "test-session")

        XCTAssertNil(store.load())

        try store.save("  session-token  ")

        XCTAssertEqual(store.load(), "session-token")

        store.clear()

        XCTAssertNil(store.load())
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

    private func makeThreadMessage(
        id: String = "message-1",
        subject: String? = "Subject",
        body: String = "Body",
        htmlBody: String? = nil,
        htmlRenderDocument: String? = nil,
        receivedAt: String = "2026-05-19T19:31:42+00:00"
    ) -> ThreadMessage {
        ThreadMessage(
            id: id,
            source: .gmail,
            threadID: "thread-1",
            fromAddress: "Sender <sender@example.com>",
            to: "me@example.com",
            cc: nil,
            bcc: nil,
            subject: subject,
            body: body,
            htmlBody: htmlBody,
            htmlRenderDocument: htmlRenderDocument,
            snippet: nil,
            labelIDs: ["INBOX"],
            receivedAt: receivedAt
        )
    }
}

private extension UserDefaults {
    static func ephemeralTokenStoreDefaults(file: StaticString = #file, line: UInt = #line) -> UserDefaults {
        let suiteName = "ElectronicMailTests.\(UUID().uuidString).\(file).\(line)"
        let defaults = UserDefaults(suiteName: suiteName)!
        defaults.removePersistentDomain(forName: suiteName)
        return defaults
    }
}
