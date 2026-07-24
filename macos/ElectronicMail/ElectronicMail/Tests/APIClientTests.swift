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
            let body = self.requestBodyData(request)
            let payload = try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: String])
            XCTAssertEqual(payload["login_code"], "one-time-code")
            XCTAssertEqual(payload["handoff_id"], "handoff-123")
            XCTAssertEqual(payload["code_verifier"], "verifier-456")
            let response = MobileSessionExchangeResponse(
                sessionToken: "live-session-token",
                expiresAt: "2026-06-15T00:00:00+00:00",
                user: AuthUserResponse(id: "user-1", email: "gaurav@example.com", displayName: "Gaurav", accessEnabled: true)
            )
            let data = try JSONEncoder.backend.encode(response)
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, data)
        }

        let response = try await client.exchangeMobileSession(
            grant: MobileAuthenticationGrant(
                loginCode: "one-time-code",
                handoffID: "handoff-123",
                codeVerifier: "verifier-456"
            )
        )

        XCTAssertEqual(response.sessionToken, "live-session-token")
    }

    func testLiveClientRejectsUnboundMobileExchangeWithoutCallingBackend() async {
        let client = makeClient { request in
            XCTFail("Unbound exchange must not reach the backend: \(request)")
            throw APIError.httpStatus(500)
        }

        do {
            _ = try await client.exchangeMobileSession(loginCode: "interceptable-code")
            XCTFail("Expected an unbound authentication error")
        } catch {
            XCTAssertEqual(error as? APIError, .unboundMobileAuthentication)
        }
    }

    func testPermanentAccountDeletionUsesAuthenticatedEndpoint() async throws {
        let client = makeClient { request in
            XCTAssertEqual(request.httpMethod, "DELETE")
            XCTAssertEqual(request.url?.path(percentEncoded: true), "/v1/auth/account")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer live-session-token")
            XCTAssertEqual(request.cachePolicy, .reloadIgnoringLocalCacheData)
            return (HTTPURLResponse(url: request.url!, statusCode: 204, httpVersion: nil, headerFields: nil)!, Data())
        }
        client.sessionToken = "live-session-token"

        try await client.deleteAccount()
    }

    func testBearerSessionTokenIsSentToBackend() async throws {
        let client = makeClient { request in
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer live-session-token")
            XCTAssertEqual(request.cachePolicy, .reloadIgnoringLocalCacheData)
            let data = try JSONEncoder.backend.encode(DemoAppFixtures.appSession)
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, data)
        }
        client.sessionToken = "live-session-token"

        _ = try await client.appSession()
    }

    func testDefaultAuthenticatedSessionConfigurationDoesNotPersistResponsesOrCredentials() {
        let configuration = LiveBackendAppClient.authenticatedSessionConfiguration()

        XCTAssertNil(configuration.urlCache)
        XCTAssertEqual(configuration.requestCachePolicy, .reloadIgnoringLocalCacheData)
        XCTAssertNil(configuration.urlCredentialStorage)
        XCTAssertNil(configuration.httpCookieStorage)
        XCTAssertFalse(configuration.httpShouldSetCookies)
    }

    func testAttachmentDownloadAllowsSameOriginAbsoluteURLUsingEffectivePort() async throws {
        let client = makeClient { request in
            XCTAssertEqual(request.url?.scheme, "https")
            XCTAssertEqual(request.url?.host?.lowercased(), "localhost")
            XCTAssertEqual(request.url?.port, 443)
            XCTAssertEqual(request.url?.path, "/v1/attachments/attachment-1")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer live-session-token")
            XCTAssertEqual(request.cachePolicy, .reloadIgnoringLocalCacheData)
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, Data("attachment".utf8))
        }
        client.baseURL = URL(string: "https://localhost")!
        client.sessionToken = "live-session-token"

        let attachment = makeAttachment(downloadURL: "https://LOCALHOST:443/v1/attachments/attachment-1")
        let result = try await client.downloadAttachment(messageID: "message-1", attachment: attachment)

        XCTAssertEqual(result.data, Data("attachment".utf8))
    }

    func testAttachmentDownloadRejectsCrossOriginURLsBeforeCreatingAuthenticatedRequest() async {
        let client = makeClient { request in
            XCTFail("Cross-origin attachment URL must not create a request: \(request)")
            throw APIError.httpStatus(500)
        }
        client.sessionToken = "live-session-token"

        for downloadURL in [
            "https://localhost:3001/v1/attachments/attachment-1",
            "http://attacker.example:3001/v1/attachments/attachment-1",
            "http://localhost:4000/v1/attachments/attachment-1",
            "//attacker.example/v1/attachments/attachment-1",
        ] {
            do {
                _ = try await client.downloadAttachment(
                    messageID: "message-1",
                    attachment: makeAttachment(downloadURL: downloadURL)
                )
                XCTFail("Expected \(downloadURL) to be rejected")
            } catch {
                XCTAssertEqual(error as? APIError, .invalidURL, downloadURL)
            }
        }
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

    func testMailboxSearchUsesQueryLabelAndCursor() async throws {
        let client = makeClient { request in
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(request.url?.path(percentEncoded: true), "/v1/mailbox/search")
            XCTAssertEqual(request.url?.query(percentEncoded: false), "q=invoice&limit=25&label=sent&cursor=next-page")
            let data = try JSONEncoder.backend.encode(DemoAppFixtures.mailbox)
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, data)
        }

        _ = try await client.searchMailbox(query: "invoice", label: .sent, limit: 25, cursor: "next-page")
    }

    func testMailboxSearchCanSuppressBackgroundHydration() async throws {
        let client = makeClient { request in
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(request.url?.path(percentEncoded: true), "/v1/mailbox/search")
            let queryItems = try XCTUnwrap(URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems)
            XCTAssertEqual(
                Dictionary(uniqueKeysWithValues: queryItems.compactMap { item in
                    item.value.map { (item.name, $0) }
                }),
                ["q": "invoice", "limit": "25", "label": "sent", "hydrate": "false"]
            )
            let data = try JSONEncoder.backend.encode(DemoAppFixtures.mailbox)
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, data)
        }

        _ = try await client.searchMailbox(
            query: "invoice",
            label: .sent,
            limit: 25,
            cursor: nil,
            hydrateInBackground: false
        )
    }

    func testDraftUpdateEncodesRetainedAttachments() async throws {
        let client = makeClient { request in
            XCTAssertEqual(request.httpMethod, "PUT")
            XCTAssertEqual(request.url?.path(percentEncoded: true), "/v1/mailbox/drafts/draft%2Fone")
            let payload = try JSONDecoder.backend.decode(MailDraftSaveRequest.self, from: self.requestBodyData(request))
            XCTAssertEqual(payload.retainedAttachmentIDs, ["attachment-1"])
            XCTAssertEqual(payload.attachments?.first?.filename, "new.txt")
            let data = Data(
                #"{"client_draft_id":"client-draft","gmail_draft_id":"draft/one","gmail_message_id":null,"gmail_thread_id":"thread-1","to":["to@example.com"],"cc":[],"bcc":[],"subject":"Subject","body_text":"Body","body_html":null,"attachments":[],"state":"saved","saved_at":"2026-07-13T00:00:00Z","error":null,"reauth_url":null}"#.utf8
            )
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, data)
        }
        let request = MailDraftSaveRequest(
            clientDraftID: "client-draft",
            gmailDraftID: "draft/one",
            gmailThreadID: "thread-1",
            to: ["to@example.com"],
            cc: [],
            bcc: [],
            subject: "Subject",
            bodyText: "Body",
            bodyHTML: nil,
            attachments: [MailAttachmentUpload(filename: "new.txt", mimeType: "text/plain", dataBase64: "bmV3")],
            retainedAttachmentIDs: ["attachment-1"],
            createdAt: "2026-07-13T00:00:00Z"
        )

        let response = try await client.updateDraft(gmailDraftID: "draft/one", request: request)

        XCTAssertEqual(response.state, .saved)
    }

    func testResponseDraftCreateEncodesThreadContextAndForwardOptions() async throws {
        let client = makeClient { request in
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(request.url?.path(percentEncoded: true), "/v1/mailbox/drafts")
            let body = self.requestBodyData(request)
            let payload = try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: Any])
            XCTAssertEqual(payload["client_draft_id"] as? String, "stable-response-draft")
            XCTAssertEqual(payload["response_mode"] as? String, "forward")
            XCTAssertEqual(payload["mailbox_thread_id"] as? String, "group/thread 1")
            XCTAssertEqual(payload["source_message_id"] as? String, "message-9")
            XCTAssertEqual(payload["include_quoted_original"] as? Bool, true)
            XCTAssertEqual(payload["include_original_attachments"] as? Bool, false)
            let data = Data(
                #"{"client_draft_id":"stable-response-draft","gmail_draft_id":"gmail-draft-1","gmail_message_id":"gmail-message-1","gmail_thread_id":"gmail-thread-new","to":["to@example.com"],"cc":[],"bcc":[],"subject":"Fwd: Subject","body_text":"Body","body_html":null,"attachments":[],"state":"saved","saved_at":"2026-07-23T00:00:00Z","error":null,"reauth_url":null}"#.utf8
            )
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, data)
        }
        let request = MailDraftSaveRequest(
            clientDraftID: "stable-response-draft",
            gmailDraftID: nil,
            gmailThreadID: nil,
            to: ["to@example.com"],
            cc: [],
            bcc: [],
            subject: "Fwd: Subject",
            bodyText: "Body",
            bodyHTML: nil,
            attachments: nil,
            retainedAttachmentIDs: nil,
            responseMode: .forward,
            mailboxThreadID: "group/thread 1",
            sourceMessageID: "message-9",
            includeQuotedOriginal: true,
            includeOriginalAttachments: false,
            createdAt: "2026-07-23T00:00:00Z"
        )

        let response = try await client.createDraft(request)

        XCTAssertEqual(response.state, .saved)
        XCTAssertEqual(response.gmailDraftID, "gmail-draft-1")
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
            XCTAssertEqual(payload.sourceMessageID, "message-older-1")
            XCTAssertEqual(payload.bodyText, "Reply body")
            XCTAssertFalse(payload.includeOriginalAttachments)
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
                sourceMessageID: "message-older-1",
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

    func testOfflineFirstClientOutboxUsesMailboxEndpointAndLimit() async throws {
        let backend = makeClient { request in
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(request.url?.path(percentEncoded: true), "/v1/mailbox/outbox")
            XCTAssertEqual(request.url?.query(percentEncoded: false), "limit=25")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer live-session-token")
            let response: MailOutboxResponse = [
                MailSendResponse(
                    clientSendID: "client-send-1",
                    serverSendID: "server-send-1",
                    mailboxThreadID: nil,
                    gmailThreadID: nil,
                    gmailMessageID: nil,
                    state: .queued,
                    queuedAt: "2026-05-21T09:00:00Z",
                    sentAt: nil,
                    error: nil,
                    reauthURL: nil
                )
            ]
            let data = try JSONEncoder.backend.encode(response)
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, data)
        }
        backend.sessionToken = "live-session-token"
        let client = OfflineFirstAppClient(backend: backend, localMailStore: MemoryLocalMailStore())

        let response = try await client.outbox(limit: 25)

        XCTAssertEqual(response.map(\.serverSendID), ["server-send-1"])
        XCTAssertEqual(response.map(\.state), [.queued])
    }

    func testSendStatusUsesEncodedServerSendID() async throws {
        let client = makeClient { request in
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(request.url?.path(percentEncoded: true), "/v1/mailbox/sends/server%2Fsend%201")
            XCTAssertEqual(request.timeoutInterval, 3)
            let response = MailSendResponse(
                clientSendID: "client-send-1",
                serverSendID: "server/send 1",
                mailboxThreadID: "thread-1",
                gmailThreadID: "gmail-thread-1",
                gmailMessageID: "gmail-message-1",
                state: .sent,
                queuedAt: "2026-05-21T09:00:00Z",
                sentAt: "2026-05-21T09:00:02Z",
                error: nil,
                reauthURL: nil
            )
            let data = try JSONEncoder.backend.encode(response)
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, data)
        }

        let response = try await client.sendStatus(serverSendID: "server/send 1")

        XCTAssertEqual(response.state, .sent)
        XCTAssertEqual(response.gmailMessageID, "gmail-message-1")
    }

    func testRetrySendUsesEncodedMailboxEndpoint() async throws {
        let client = makeClient { request in
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(request.url?.path(percentEncoded: true), "/v1/mailbox/sends/server%2Fsend%201/retry")
            XCTAssertNil(request.httpBody)
            let response = MailSendResponse(
                clientSendID: "client-send-1",
                serverSendID: "server/send 1",
                mailboxThreadID: nil,
                gmailThreadID: nil,
                gmailMessageID: nil,
                state: .queued,
                queuedAt: "2026-05-21T09:05:00Z",
                sentAt: nil,
                error: nil,
                reauthURL: nil
            )
            let data = try JSONEncoder.backend.encode(response)
            return (HTTPURLResponse(url: request.url!, statusCode: 202, httpVersion: nil, headerFields: nil)!, data)
        }

        let response = try await client.retrySend(serverSendID: "server/send 1")

        XCTAssertEqual(response.state, .queued)
        XCTAssertNil(response.error)
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

    func testOfflineFirstClientDoesNotRestoreSessionCacheAfterTokenChangesMidRefresh() async throws {
        let requestStarted = expectation(description: "Session request started")
        let allowResponse = DispatchSemaphore(value: 0)
        let backend = makeClient { request in
            requestStarted.fulfill()
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer token-a")
            XCTAssertEqual(allowResponse.wait(timeout: .now() + 2), .success)
            let data = try JSONEncoder.backend.encode(DemoAppFixtures.appSession)
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, data)
        }
        let localStore = MemoryLocalMailStore()
        let client = OfflineFirstAppClient(backend: backend, localMailStore: localStore)
        client.sessionToken = "token-a"

        let refresh = Task {
            try await client.appSession()
        }
        await fulfillment(of: [requestStarted], timeout: 1)
        client.sessionToken = nil
        allowResponse.signal()

        do {
            _ = try await refresh.value
            XCTFail("A response from the cleared session must be discarded")
        } catch is CancellationError {
            // Expected: the old response cannot repopulate local email data.
        } catch {
            XCTFail("Unexpected error: \(error)")
        }
        XCTAssertNil(localStore.readSession())
        XCTAssertNil(localStore.readMailbox(userID: DemoAppFixtures.userID, label: .inbox))
    }

    func testOfflineFirstClientDoesNotQueueNonRetryableThreadActionFailures() async throws {
        for status in [400, 401, 403] {
            let backend = ToggleThreadActionAppClient()
            backend.shouldFailThreadAction = true
            backend.threadActionFailureStatus = status
            let localStore = MemoryLocalMailStore()
            localStore.writeSession(DemoAppFixtures.appSession)
            let client = OfflineFirstAppClient(backend: backend, localMailStore: localStore)

            do {
                _ = try await client.archiveThread("group-1")
                XCTFail("Expected HTTP \(status) to be rethrown")
            } catch APIError.httpStatus(let receivedStatus) {
                XCTAssertEqual(receivedStatus, status)
            } catch {
                XCTFail("Unexpected error: \(error)")
            }
            XCTAssertTrue(localStore.pendingThreadActions().isEmpty)
        }
    }

    func testOfflineFirstClientRetainsPendingActionWhenReplayNeedsAuthenticationOrRetry() async throws {
        for status in [401, 403, 408, 429, 500, 503] {
            let backend = ToggleThreadActionAppClient()
            backend.shouldFailThreadAction = true
            backend.threadActionFailureStatus = status
            let localStore = MemoryLocalMailStore()
            localStore.writeSession(DemoAppFixtures.appSession)
            localStore.writePendingThreadAction(
                LocalPendingThreadAction(
                    clientActionID: "pending-\(status)",
                    userID: DemoAppFixtures.userID,
                    mailboxThreadID: "group-1",
                    targetMessageID: nil,
                    action: .archive,
                    createdAt: "2026-07-23T00:00:00.000Z",
                    error: "offline"
                )
            )
            let client = OfflineFirstAppClient(backend: backend, localMailStore: localStore)

            _ = try await client.appSession()

            let pending = try XCTUnwrap(localStore.pendingThreadActions().first)
            XCTAssertEqual(pending.clientActionID, "pending-\(status)")
            XCTAssertNotNil(pending.error)
        }
    }

    func testOfflineFirstClientDiscardsPendingActionAfterPermanentReplayFailure() async throws {
        for status in [400, 404, 409, 410, 413, 422] {
            let backend = ToggleThreadActionAppClient()
            backend.shouldFailThreadAction = true
            backend.threadActionFailureStatus = status
            let localStore = MemoryLocalMailStore()
            localStore.writeSession(DemoAppFixtures.appSession)
            localStore.writePendingThreadAction(
                LocalPendingThreadAction(
                    clientActionID: "pending-\(status)",
                    userID: DemoAppFixtures.userID,
                    mailboxThreadID: "group-1",
                    targetMessageID: nil,
                    action: .archive,
                    createdAt: "2026-07-23T00:00:00.000Z",
                    error: "offline"
                )
            )
            let client = OfflineFirstAppClient(backend: backend, localMailStore: localStore)

            _ = try await client.appSession()

            XCTAssertTrue(localStore.pendingThreadActions().isEmpty, "HTTP \(status) should discard a permanently invalid action")
        }
    }

    func testOfflineFirstClientDoesNotPersistTransportPageAfterSessionSwitch() async throws {
        let localStore = MemoryLocalMailStore()
        let userAMailbox = singleRowMailbox(threadID: "user-a-thread", title: "User A row")
        let userBMailbox = singleRowMailbox(threadID: "user-b-thread", title: "User B row")
        localStore.writeSession(appSession(userID: "user-a", email: "user-a@example.com", mailbox: userAMailbox))
        localStore.writeMailbox(userAMailbox, userID: "user-a", label: .inbox)
        let backend = FixedUserMailboxAppClient(
            session: appSession(userID: "user-b", email: "user-b@example.com", mailbox: userBMailbox),
            mailbox: userBMailbox
        )
        let client = OfflineFirstAppClient(backend: backend, localMailStore: localStore)

        client.sessionToken = "user-b-token"
        let mailbox = try await client.mailbox(label: .inbox, limit: 100, cursor: nil)

        XCTAssertEqual(mailbox.sections.first?.rows.first?.threadID, "user-b-thread")
        XCTAssertEqual(localStore.readSession()?.user.id, "user-b")
        XCTAssertEqual(localStore.readMailbox(userID: "user-a", label: .inbox)?.sections.first?.rows.first?.threadID, "user-a-thread")
        XCTAssertNil(localStore.readMailbox(userID: "user-b", label: .inbox))
        XCTAssertEqual(backend.appSessionCallCount, 1)
    }

    func testOfflineFirstClientRejectsThreadPayloadFromAnotherUser() async throws {
        let localStore = MemoryLocalMailStore()
        let userBMailbox = singleRowMailbox(threadID: "user-b-thread", title: "User B row")
        let backend = FixedUserMailboxAppClient(
            session: appSession(userID: "user-b", email: "user-b@example.com", mailbox: userBMailbox),
            mailbox: userBMailbox
        )
        let client = OfflineFirstAppClient(backend: backend, localMailStore: localStore)
        client.sessionToken = "user-b-token"
        _ = try await client.appSession()

        do {
            _ = try await client.thread(threadID: "demo-google-today", limit: 50, offset: 0)
            XCTFail("A thread payload belonging to another user must be rejected")
        } catch APIError.emptyResponse {
            // Expected: never persist cross-account message content.
        } catch {
            XCTFail("Unexpected error: \(error)")
        }

        XCTAssertNil(localStore.readThread(userID: DemoAppFixtures.userID, threadID: "demo-google-today"))
        XCTAssertNil(localStore.readThread(userID: "user-b", threadID: "demo-google-today"))
    }

    func testOfflineFirstClientReplaysOnlyCurrentUsersPendingThreadActions() async throws {
        let localStore = MemoryLocalMailStore()
        localStore.writeSession(appSession(userID: "user-a", email: "user-a@example.com", mailbox: singleRowMailbox(threadID: "user-a-thread", title: "User A row")))
        localStore.writePendingThreadAction(
            LocalPendingThreadAction(
                clientActionID: "action-user-a",
                userID: "user-a",
                mailboxThreadID: "user-a-thread",
                targetMessageID: nil,
                action: .archive,
                createdAt: "2026-07-03T00:00:00Z",
                error: nil
            )
        )
        localStore.writePendingThreadAction(
            LocalPendingThreadAction(
                clientActionID: "action-user-b",
                userID: "user-b",
                mailboxThreadID: "user-b-thread",
                targetMessageID: nil,
                action: .archive,
                createdAt: "2026-07-03T00:00:01Z",
                error: nil
            )
        )
        let userBMailbox = singleRowMailbox(threadID: "user-b-thread", title: "User B row")
        let backend = FixedUserMailboxAppClient(
            session: appSession(userID: "user-b", email: "user-b@example.com", mailbox: userBMailbox),
            mailbox: userBMailbox
        )
        let client = OfflineFirstAppClient(backend: backend, localMailStore: localStore)

        client.sessionToken = "user-b-token"
        _ = try await client.appSession()

        XCTAssertEqual(backend.enqueuedActions.map(\.clientActionID), ["action-user-b"])
        XCTAssertEqual(localStore.pendingThreadActions().map(\.clientActionID), ["action-user-a"])
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

    private func makeAttachment(downloadURL: String?) -> ThreadAttachment {
        ThreadAttachment(
            id: "attachment-1",
            filename: "attachment.txt",
            mimeType: "text/plain",
            size: 10,
            attachmentID: "gmail-attachment-1",
            partID: "part-1",
            downloadURL: downloadURL
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

private func appSession(userID: String, email: String, mailbox: MailboxResponse) -> AppSessionResponse {
    let current = DemoAppFixtures.appSession
    return AppSessionResponse(
        user: AppSessionUser(id: userID, email: email, firstName: nil, displayName: userID),
        readiness: current.readiness,
        dashboard: current.dashboard,
        mailbox: mailbox,
        sync: current.sync
    )
}

private func singleRowMailbox(threadID: String, title: String) -> MailboxResponse {
    MailboxResponse(
        label: .inbox,
        totalThreads: 1,
        loadedThreads: 1,
        sections: [
            GmailThreadSection(
                id: "today",
                title: "Today",
                rows: [
                    GmailThreadRow(
                        threadID: threadID,
                        entityID: nil,
                        title: title,
                        href: nil,
                        latestSourceRecordID: "\(threadID)-message",
                        latestReceivedAt: "2026-07-03T00:00:00+00:00",
                        latestMessageAt: "2026-07-03T00:00:00+00:00",
                        latestSubject: title,
                        latestSender: "\(threadID)@example.com",
                        sender: "\(threadID)@example.com",
                        participants: [],
                        messageCount: 1,
                        summary: nil,
                        aiGroupID: nil,
                        aiTitle: nil,
                        aiSummary: nil,
                        snippet: nil,
                        hasAttachments: false,
                        attachmentCount: 0,
                        labelIDs: ["INBOX"],
                        labels: ["INBOX"],
                        unread: false,
                        actionNeeded: false,
                        actionType: "none",
                        actionTypeKey: "none",
                        priority: 0,
                        dashboardVisible: false,
                        currentState: nil,
                        lifecycleState: nil,
                        outcomeType: nil,
                        lifecycleUpdates: [],
                        children: [],
                        enrichmentStatus: "ready",
                        presentationStatus: "ai_ready",
                        pendingAction: nil
                    )
                ]
            )
        ],
        fullImportRunning: false,
        fullImportCompleted: true
    )
}

private final class FixedUserMailboxAppClient: AppClient {
    var baseURL = URL(string: "http://localhost:3001")!
    var sessionToken: String?
    let mode: AppRunMode = .localBackend
    let session: AppSessionResponse
    let fixedMailbox: MailboxResponse
    private(set) var appSessionCallCount = 0
    private(set) var enqueuedActions: [QueuedThreadActionRequest] = []

    init(session: AppSessionResponse, mailbox: MailboxResponse) {
        self.session = session
        self.fixedMailbox = mailbox
    }

    func exchangeMobileSession(loginCode: String) async throws -> MobileSessionExchangeResponse {
        MobileSessionExchangeResponse(
            sessionToken: "session",
            expiresAt: "2026-08-03T00:00:00+00:00",
            user: AuthUserResponse(id: session.user.id, email: session.user.email, displayName: session.user.displayName, accessEnabled: true)
        )
    }

    func appSession() async throws -> AppSessionResponse {
        appSessionCallCount += 1
        return session
    }

    func mailbox(label: MailboxLabel, limit: Int, cursor: String?) async throws -> MailboxResponse {
        fixedMailbox
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

private final class ToggleThreadActionAppClient: AppClient {
    var baseURL = URL(string: "http://localhost:3001")!
    var sessionToken: String?
    let mode: AppRunMode = .localBackend
    var shouldFailThreadAction = false
    var threadActionFailureStatus = 503
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
            throw APIError.httpStatus(threadActionFailureStatus)
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
