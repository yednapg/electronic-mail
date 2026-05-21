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

    func testThreadReaderResponseDecodesCleanReaderPayload() throws {
        let data = """
        {
          "entity_id": "group-1",
          "user_id": "user-1",
          "source": "gmail",
          "gmail_thread_id": "thread-1",
          "subject": "FX Retail",
          "total_messages": 1,
          "limit": 50,
          "offset": 0,
          "has_more": false,
          "messages": [
            {
              "id": "msg-1",
              "source": "gmail",
              "thread_id": "thread-1",
              "from_address": "NorthstarFXclearretail <northstarfx@northstar.example>",
              "subject": "Re: FX Retail",
              "body": "Noisy fallback",
              "html_body": "<html><body>Raw</body></html>",
              "html_render_document": "<html><body>Raw</body></html>",
              "reader": {
                "primary_text": "Kindly confirm once the account is funded.",
                "markers": [
                  { "kind": "classification", "label": "Internal", "text": "Classification - Internal" },
                  { "kind": "external_warning", "label": "External", "text": "External warning text" }
                ],
                "signature_text": "Best Regards\\nTest User",
                "quoted_text": "On Tue, TestUser wrote:",
                "footer_text": "Disclaimer: confidential",
                "original_html_available": true
              },
              "label_ids": ["INBOX"],
              "received_at": "2026-05-12T05:03:05+00:00"
            }
          ]
        }
        """.data(using: .utf8)!

        let thread = try JSONDecoder.backend.decode(ThreadReaderResponse.self, from: data)

        XCTAssertEqual(thread.messages[0].reader?.primaryText, "Kindly confirm once the account is funded.")
        XCTAssertEqual(thread.messages[0].reader?.markers.map(\.label), ["Internal", "External"])
        XCTAssertEqual(thread.messages[0].reader?.signatureText, "Best Regards\nTest User")
        XCTAssertEqual(thread.messages[0].reader?.quotedText, "On Tue, TestUser wrote:")
        XCTAssertEqual(thread.messages[0].reader?.footerText, "Disclaimer: confidential")
        XCTAssertEqual(thread.messages[0].reader?.originalHTMLAvailable, true)
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

        XCTAssertEqual(bodyKind, .text("Hello,\n\n205759 is your one-time password."))
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
            .text("Hey,\n\nLooks like you started a speedrun application but didn't hit submit.\n\nFinish your app here: SR007")
        )
    }

    func testEmailBodyResolverPreservesSimpleHTMLParagraphs() {
        let html = #"""
        <html>
          <body>
            <p>First paragraph with normal email copy.</p>
            <p>Second paragraph keeps its own readable break.</p>
          </body>
        </html>
        """#
        let message = makeThreadMessage(
            id: "simple-html",
            body: "Fallback should not win",
            htmlBody: html,
            htmlRenderDocument: html
        )

        let bodyKind = EmailReaderBodyResolver.bodyKind(message: message, fallbackText: "")

        XCTAssertEqual(
            bodyKind,
            .text("First paragraph with normal email copy.\n\nSecond paragraph keeps its own readable break.")
        )
    }

    func testEmailBodyResolverPreservesPlainTextParagraphs() {
        let body = """
        Hi TestUser,

        Thank you for applying to Neo Residency. Unfortunately, we've decided not to move forward with your application at this time.

        Regards,
        Connor

        P.S. To follow what our community members are up to, consider joining the Neo News mailing list: http://neo.substack.com/
        """
        let message = makeThreadMessage(id: "neo", body: body)

        let bodyKind = EmailReaderBodyResolver.bodyKind(message: message, fallbackText: "")

        XCTAssertEqual(bodyKind, .text(body))
    }

    func testEmailBodyResolverRestoresCommonParagraphsWhenPlainTextWasFlattened() {
        let body = "Hi TestUser, Thank you for applying to Neo Residency. Unfortunately, we've decided not to move forward with your application at this time. We've enjoyed learning about you, and hope our paths cross again. If you have any feedback on the application process, please reach out. Regards, Connor P.S. To follow what our community members are up to, consider joining the Neo News mailing list: http://neo.substack.com/"
        let message = makeThreadMessage(id: "neo-flattened", body: body)

        let bodyKind = EmailReaderBodyResolver.bodyKind(message: message, fallbackText: "")

        XCTAssertEqual(
            bodyKind,
            .text("Hi TestUser,\n\nThank you for applying to Neo Residency. Unfortunately, we've decided not to move forward with your application at this time. We've enjoyed learning about you, and hope our paths cross again. If you have any feedback on the application process, please reach out.\n\nRegards,\nConnor\n\nP.S. To follow what our community members are up to, consider joining the Neo News mailing list: http://neo.substack.com/")
        )
    }

    func testEmailBodyResolverRoutesImageOnlyHTMLToTextFallback() {
        let html = #"""
        <html>
          <body>
            <table width="100%" role="presentation">
              <tr>
                <td>
                  <img src="https://assets.example.com/fx-retail-offer.png" width="640" height="420">
                  <img src="https://analytics.example.com/track/open.gif" width="1" height="1" style="display:none">
                </td>
              </tr>
            </table>
          </body>
        </html>
        """#
        let body = """
        FX Retail

        Your FX Retail account update is available. Review the latest details in your dashboard.
        """
        let message = makeThreadMessage(
            id: "fx-retail",
            body: body,
            htmlBody: html,
            htmlRenderDocument: html
        )

        let bodyKind = EmailReaderBodyResolver.bodyKind(message: message, fallbackText: "")

        XCTAssertEqual(bodyKind, .text(body))
    }

    func testEmailBodyResolverKeepsStructuredLongHTMLInHTMLRenderer() {
        let html = #"""
        <html>
          <body>
            <table width="100%" cellspacing="0" cellpadding="0" role="presentation">
              <tbody>
                <tr>
                  <td style="font-family: Arial, sans-serif; font-size: 16px; line-height: 22px;">
                    <p>Dear Sir/Madam,</p>
                    <p>We are pleased to invite you to an exclusive Live Demo Session on the FX-Retail platform a seamless and cost-effective way to transact in USD/INR currency pair for the legitimate transactions approved by the RBI.</p>
                    <p>With FX-Retail, you will get below benefits:</p>
                    <p>Direct access to the Interbank USD/INR market</p>
                    <p>Real-time pricing for buying and selling foreign exchange</p>
                    <p>Better forex rates, leading to substantial cost savings</p>
                    <p>Flexibility to trade in CASH, TOM, SPOT, and FORWARD contracts within your bank's approved limits</p>
                    <p>Join our live demo session on Webex and explore how you can get maximum benefits using FX-Retail platform.</p>
                    <p>Session Details Date: 21 May 2026 Time: 04:00 PM</p>
                    <p>Join via Webex Link: <a href="https://cdsl-ccil.webex.com/demo">https://cdsl-ccil.webex.com/demo</a></p>
                  </td>
                </tr>
              </tbody>
            </table>
          </body>
        </html>
        """#
        let message = makeThreadMessage(
            id: "fx-structured",
            body: "Fallback body should not replace structured HTML",
            htmlBody: html,
            htmlRenderDocument: html
        )

        let bodyKind = EmailReaderBodyResolver.bodyKind(message: message, fallbackText: "")

        guard case .html(let resolvedHTML, let fallbackText) = bodyKind else {
            return XCTFail("Expected raw original HTML to remain available")
        }
        XCTAssertEqual(resolvedHTML, html)
        XCTAssertTrue(fallbackText.contains("FX-Retail platform"))
        XCTAssertEqual(EmailReaderBodyResolver.originalHTML(from: message), html)
    }

    func testEmailBodyResolverKeepsRichHTMLInHTMLRenderer() {
        let richHTML = #"""
        <html>
          <head>
            <style>
              .wrapper { width: 100%; background: #f7f7f7; }
              .container { width: 640px; margin: 0 auto; }
              .hero { border-radius: 12px; overflow: hidden; }
              .eyebrow { color: #666; font-size: 12px; letter-spacing: 1px; }
              .headline { color: #111; font-size: 32px; line-height: 38px; }
              .body { color: #333; font-size: 16px; line-height: 24px; }
              .button { display: inline-block; padding: 14px 18px; background: #111; color: #fff; }
            </style>
          </head>
          <body>
            <table class="wrapper" role="presentation">
              <tr>
                <td>
                  <table class="container" role="presentation">
                    <tr>
                      <td><img class="hero" src="https://example.com/hero.png" width="640" height="260"></td>
                    </tr>
                    <tr>
                      <td class="eyebrow">PRODUCT DIGEST</td>
                    </tr>
                    <tr>
                      <td class="headline">May product digest</td>
                    </tr>
                    <tr>
                      <td class="body">A designed newsletter with a hero image, structured headline, call to action, and enough visible copy should stay in the HTML renderer.</td>
                    </tr>
                    <tr>
                      <td><a class="button" href="https://example.com/read">Read the update</a></td>
                    </tr>
                  </table>
                </td>
              </tr>
            </table>
          </body>
        </html>
        """#
        let message = makeThreadMessage(
            id: "rich",
            body: "Rich email",
            htmlBody: richHTML,
            htmlRenderDocument: richHTML
        )

        let bodyKind = EmailReaderBodyResolver.bodyKind(message: message, fallbackText: "")

        guard case .html(let html, let fallbackText) = bodyKind else {
            return XCTFail("Expected raw original HTML to remain available")
        }
        XCTAssertEqual(html, richHTML)
        XCTAssertTrue(fallbackText.contains("May product digest"))
        XCTAssertEqual(EmailReaderBodyResolver.originalHTML(from: message), richHTML)
    }

    func testEmailBodyResolverPrefersCleanReaderTextOverHTML() {
        let html = #"<html><body><table><tr><td style="background:#fff;color:#000">Noisy HTML</td></tr></table></body></html>"#
        let reader = ThreadMessageReader(
            primaryText: "Clean body only.",
            markers: [
                ThreadMessageReaderMarker(kind: "classification", label: "Internal", text: "Classification - Internal")
            ],
            signatureText: "Best",
            quotedText: "On Tue wrote:",
            footerText: "Disclaimer",
            originalHTMLAvailable: true
        )
        let message = makeThreadMessage(
            body: "Fallback",
            htmlBody: html,
            htmlRenderDocument: html,
            reader: reader
        )

        XCTAssertEqual(
            EmailReaderBodyResolver.bodyKind(message: message, fallbackText: ""),
            .text("Clean body only.")
        )
        XCTAssertEqual(EmailReaderBodyResolver.originalHTML(from: message), html)
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
        reader: ThreadMessageReader? = nil,
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
            reader: reader,
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
