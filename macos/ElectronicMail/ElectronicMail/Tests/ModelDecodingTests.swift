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
              "from_address": "HDFCFXclearretail <hdfcfxclearretail@hdfc.bank.in>",
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
                "signature_text": "Best Regards\\nAshwin",
                "quoted_text": "On Tue, Gaurav wrote:",
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
        XCTAssertEqual(thread.messages[0].reader?.signatureText, "Best Regards\nAshwin")
        XCTAssertEqual(thread.messages[0].reader?.quotedText, "On Tue, Gaurav wrote:")
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

    func testMailboxRowPresentationPrefersAITitleAndSummary() {
        let row = GmailThreadRow(
            threadID: "group-1",
            entityID: "group-1",
            title: "Raw Gmail subject",
            href: "/v1/mailbox/threads/group-1",
            latestSourceRecordID: "msg-1",
            latestReceivedAt: "2026-05-23T12:00:00+00:00",
            latestMessageAt: "2026-05-23T12:00:00+00:00",
            latestSubject: "Raw Gmail subject",
            latestSender: "Sender <sender@example.com>",
            sender: "Sender <sender@example.com>",
            participants: ["Sender"],
            messageCount: 2,
            summary: "Raw Gmail snippet",
            aiGroupID: "group-1",
            aiTitle: "AI grouped title",
            aiSummary: "AI grouped summary",
            snippet: "Raw Gmail snippet",
            labelIDs: ["INBOX"],
            labels: ["INBOX"],
            unread: false,
            actionNeeded: false,
            actionType: "open",
            actionTypeKey: "open",
            priority: 20,
            dashboardVisible: true,
            currentState: .waiting,
            lifecycleState: "active",
            outcomeType: nil,
            lifecycleUpdates: [],
            enrichmentStatus: "ready"
        )

        XCTAssertEqual(row.displayTitle, "AI grouped title")
        XCTAssertEqual(row.displaySummary, "AI grouped summary")
    }

    func testMailboxRowPresentationShowsPendingTitleState() throws {
        let data = """
        {
          "thread_id": "group-1",
          "entity_id": "group-1",
          "title": "Raw Gmail subject",
          "href": "/v1/mailbox/threads/group-1",
          "latest_source_record_id": "msg-1",
          "latest_received_at": "2026-05-23T12:00:00+00:00",
          "latest_message_at": "2026-05-23T12:00:00+00:00",
          "latest_subject": "Raw Gmail subject",
          "latest_sender": "Sender <sender@example.com>",
          "sender": "Sender <sender@example.com>",
          "participants": ["Sender"],
          "message_count": 1,
          "summary": "Raw Gmail snippet",
          "ai_group_id": null,
          "ai_title": null,
          "ai_summary": null,
          "snippet": "Raw Gmail snippet",
          "label_ids": ["INBOX"],
          "labels": ["INBOX"],
          "unread": false,
          "action_needed": false,
          "action_type": "open",
          "action_type_key": "open",
          "priority": 20,
          "dashboard_visible": true,
          "current_state": "waiting",
          "lifecycle_state": "active",
          "outcome_type": null,
          "lifecycle_updates": [],
          "enrichment_status": "pending",
          "presentation_status": "ai_pending"
        }
        """.data(using: .utf8)!

        let row = try JSONDecoder.backend.decode(GmailThreadRow.self, from: data)

        XCTAssertEqual(row.displayTitle, "Building title... Raw Gmail subject")
        XCTAssertEqual(row.childRows, [])
    }

    func testMailboxRowDecodesLightweightChildren() throws {
        let data = """
        {
          "thread_id": "group-1",
          "entity_id": "group-1",
          "title": "Grouped subject",
          "href": "/v1/mailbox/threads/group-1",
          "latest_source_record_id": "msg-2",
          "latest_received_at": "2026-05-23T12:10:00+00:00",
          "latest_message_at": "2026-05-23T12:10:00+00:00",
          "latest_subject": "Grouped subject",
          "latest_sender": "Sender <sender@example.com>",
          "sender": "Sender <sender@example.com>",
          "participants": ["Sender"],
          "message_count": 2,
          "summary": "Grouped snippet",
          "snippet": "Grouped snippet",
          "label_ids": ["INBOX"],
          "labels": ["INBOX"],
          "unread": false,
          "action_needed": false,
          "action_type": "open",
          "action_type_key": "open",
          "priority": 20,
          "dashboard_visible": true,
          "current_state": "waiting",
          "lifecycle_state": "active",
          "outcome_type": null,
          "lifecycle_updates": [],
          "children": [
            {
              "message_id": "msg-1",
              "gmail_thread_id": "gmail-thread-1",
              "sender": "First <first@example.com>",
              "subject": "First subject",
              "snippet": "First snippet",
              "received_at": "2026-05-23T12:00:00+00:00",
              "label_ids": ["INBOX"],
              "labels": ["INBOX"],
              "unread": false
            },
            {
              "message_id": "msg-2",
              "gmail_thread_id": "gmail-thread-1",
              "sender": "Second <second@example.com>",
              "subject": "Second subject",
              "ai_title": "Clean second title",
              "snippet": "Second snippet",
              "received_at": "2026-05-23T12:10:00+00:00",
              "label_ids": ["INBOX", "UNREAD"],
              "labels": ["INBOX", "UNREAD"],
              "unread": true
            }
          ],
          "enrichment_status": "ready",
          "presentation_status": "ai_ready"
        }
        """.data(using: .utf8)!

        let row = try JSONDecoder.backend.decode(GmailThreadRow.self, from: data)

        XCTAssertEqual(row.childRows.map(\.messageID), ["msg-1", "msg-2"])
        XCTAssertEqual(row.childRows[0].displaySender, "First")
        XCTAssertEqual(row.childRows[1].displayTitle, "Clean second title")
        XCTAssertTrue(row.childRows[1].isUnread)
    }

    func testGoogleAuthStateDecodesSendScopeFieldsAndDefaults() throws {
        let scopedData = """
        {
          "available": true,
          "connected": true,
          "connect_url": null,
          "can_send_mail": false,
          "missing_scopes": ["https://www.googleapis.com/auth/gmail.send"]
        }
        """.data(using: .utf8)!
        let legacyData = """
        {
          "available": true,
          "connected": true,
          "connect_url": null
        }
        """.data(using: .utf8)!

        let scoped = try JSONDecoder.backend.decode(GoogleAuthState.self, from: scopedData)
        let legacy = try JSONDecoder.backend.decode(GoogleAuthState.self, from: legacyData)

        XCTAssertFalse(scoped.canSendMail)
        XCTAssertEqual(scoped.missingScopes, ["https://www.googleapis.com/auth/gmail.send"])
        XCTAssertFalse(legacy.canSendMail)
        XCTAssertEqual(legacy.missingScopes, [])
    }

    func testMailSendResponseDecodesReauthRequired() throws {
        let data = """
        {
          "client_send_id": "client-send-1",
          "server_send_id": null,
          "mailbox_thread_id": "group-1",
          "gmail_thread_id": null,
          "gmail_message_id": null,
          "state": "reauth_required",
          "queued_at": null,
          "sent_at": null,
          "error": "Google needs permission to send mail.",
          "reauth_url": "http://127.0.0.1:3001/auth/google"
        }
        """.data(using: .utf8)!

        let response = try JSONDecoder.backend.decode(MailSendResponse.self, from: data)

        XCTAssertEqual(response.state, .reauthRequired)
        XCTAssertEqual(response.reauthURL, "http://127.0.0.1:3001/auth/google")
    }

    func testMailboxResponseDecodesPaginationProgressFields() throws {
        let data = """
        {
          "label": "inbox",
          "total_threads": 273,
          "next_cursor": "cursor-2",
          "loaded_threads": 100,
          "window_days": 90,
          "sections": [],
          "full_import_running": true,
          "full_import_completed": false
        }
        """.data(using: .utf8)!

        let mailbox = try JSONDecoder.backend.decode(MailboxResponse.self, from: data)

        XCTAssertEqual(mailbox.nextCursor, "cursor-2")
        XCTAssertEqual(mailbox.loadedThreads, 100)
        XCTAssertEqual(mailbox.windowDays, 90)
        XCTAssertEqual(mailbox.totalThreads, 273)
    }

    func testGoogleOAuthServiceParsesCustomCallbackLoginCode() throws {
        let url = try XCTUnwrap(URL(string: "electronicmail://auth/callback?login_code=abc123"))

        XCTAssertEqual(try GoogleOAuthService.loginCode(from: url), "abc123")
    }

    func testEmailBodyResolverRoutesBasicTextLinkHTMLToHTML() {
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

        guard case .html(let resolvedHTML, let fallbackText) = bodyKind else {
            return XCTFail("Expected Gmail HTML to render as the primary body")
        }
        XCTAssertEqual(resolvedHTML, html)
        XCTAssertEqual(fallbackText, "Hello,\n\n205759 is your one-time password.")
    }

    func testEmailBodyResolverRoutesSinglePresentationTableWithTrackingPixelToHTML() {
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

        guard case .html(let resolvedHTML, let fallbackText) = bodyKind else {
            return XCTFail("Expected Gmail HTML to render as the primary body")
        }
        XCTAssertEqual(resolvedHTML, html)
        XCTAssertEqual(
            fallbackText,
            "Hey,\n\nLooks like you started a speedrun application but didn't hit submit.\n\nFinish your app here: SR007"
        )
    }

    func testEmailBodyResolverRendersSimpleHTMLParagraphsAsHTML() {
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

        guard case .html(let resolvedHTML, let fallbackText) = bodyKind else {
            return XCTFail("Expected Gmail HTML to render as the primary body")
        }
        XCTAssertEqual(resolvedHTML, html)
        XCTAssertEqual(
            fallbackText,
            "First paragraph with normal email copy.\n\nSecond paragraph keeps its own readable break."
        )
    }

    func testEmailBodyResolverPreservesPlainTextParagraphs() {
        let body = """
        Hi Gaurav,

        Thank you for applying to Neo Residency. Unfortunately, we've decided not to move forward with your application at this time.

        Regards,
        Connor

        P.S. To follow what our community members are up to, consider joining the Neo News mailing list: http://neo.substack.com/
        """
        let message = makeThreadMessage(id: "neo", body: body)

        let bodyKind = EmailReaderBodyResolver.bodyKind(message: message, fallbackText: "")

        XCTAssertEqual(bodyKind, .text(body))
    }

    func testEmailBodyResolverUsesExplicitFullEmailLoadingCopy() {
        let message = makeThreadMessage(id: "missing-body", body: "")

        XCTAssertEqual(
            EmailReaderBodyResolver.bodyKind(message: message, fallbackText: ""),
            .text("Loading full email...")
        )
    }

    func testEmailBodyResolverRestoresCommonParagraphsWhenPlainTextWasFlattened() {
        let body = "Hi Gaurav, Thank you for applying to Neo Residency. Unfortunately, we've decided not to move forward with your application at this time. We've enjoyed learning about you, and hope our paths cross again. If you have any feedback on the application process, please reach out. Regards, Connor P.S. To follow what our community members are up to, consider joining the Neo News mailing list: http://neo.substack.com/"
        let message = makeThreadMessage(id: "neo-flattened", body: body)

        let bodyKind = EmailReaderBodyResolver.bodyKind(message: message, fallbackText: "")

        XCTAssertEqual(
            bodyKind,
            .text("Hi Gaurav,\n\nThank you for applying to Neo Residency. Unfortunately, we've decided not to move forward with your application at this time. We've enjoyed learning about you, and hope our paths cross again. If you have any feedback on the application process, please reach out.\n\nRegards,\nConnor\n\nP.S. To follow what our community members are up to, consider joining the Neo News mailing list: http://neo.substack.com/")
        )
    }

    func testEmailBodyResolverRoutesImageOnlyHTMLToHTML() {
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

        guard case .html(let resolvedHTML, let fallbackText) = bodyKind else {
            return XCTFail("Expected Gmail HTML to render as the primary body")
        }
        XCTAssertEqual(resolvedHTML, html)
        XCTAssertEqual(fallbackText, body)
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

    func testEmailBodyResolverPrefersHTMLOverCleanReaderText() {
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
            .html(html, fallbackText: "Noisy HTML")
        )
        XCTAssertEqual(EmailReaderBodyResolver.originalHTML(from: message), html)
    }

    func testThreadPresentationOrdersNewestToOldestAndExpandsLatestFirst() {
        let newest = makeThreadMessage(id: "newest", receivedAt: "2026-05-19T19:33:06+00:00")
        let oldest = makeThreadMessage(id: "oldest", receivedAt: "2026-05-19T19:31:42+00:00")

        let ordered = EmailThreadPresentation.orderedMessages([newest, oldest])
        let items = EmailThreadPresentation.items(from: ordered)
        let latestKey = EmailThreadPresentation.latestMessageKey(in: items)

        XCTAssertEqual(ordered.map(\.id), ["newest", "oldest"])
        XCTAssertEqual(latestKey, "0::newest")
        XCTAssertFalse(
            EmailThreadPresentation.isExpanded(
                messageKey: "1::oldest",
                latestMessageKey: latestKey,
                userExpandedMessageKeys: []
            )
        )
        XCTAssertTrue(
            EmailThreadPresentation.isExpanded(
                messageKey: "0::newest",
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
                messageKey: "1::duplicate",
                latestMessageKey: latestKey,
                userExpandedMessageKeys: []
            )
        )
        XCTAssertTrue(
            EmailThreadPresentation.isExpanded(
                messageKey: "0::duplicate",
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
