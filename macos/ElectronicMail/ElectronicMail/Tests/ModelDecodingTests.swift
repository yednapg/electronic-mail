import AppKit
import SwiftUI
import WebKit
import XCTest
import Security
@testable import ElectronicMailCore

final class ModelDecodingTests: XCTestCase {
    func testOriginalBrandAccentRemainsElectronicMailBlue() throws {
        let color = try XCTUnwrap(
            NSColor(ElectronicMailDesign.appleBlue).usingColorSpace(.sRGB)
        )

        XCTAssertEqual(color.redComponent, 0, accuracy: 0.001)
        XCTAssertEqual(color.greenComponent, 90.0 / 255.0, accuracy: 0.001)
        XCTAssertEqual(color.blueComponent, 205.0 / 255.0, accuracy: 0.001)
        XCTAssertEqual(color.alphaComponent, 1, accuracy: 0.001)
    }

    func testOriginalTypographyScaleRemainsProductIdentity() {
        XCTAssertEqual(ElectronicMailType.titleSize, 22)
        XCTAssertEqual(ElectronicMailType.sectionTitleSize, 22)
        XCTAssertEqual(ElectronicMailType.bodySize, 20)
        XCTAssertEqual(ElectronicMailType.bodyLineHeight, 40)
    }

    func testUnreadTextIsPureWhiteInDarkMode() throws {
        let color = try XCTUnwrap(
            NSColor(ElectronicMailDesign.unreadText(for: .dark)).usingColorSpace(.sRGB)
        )

        XCTAssertEqual(color.redComponent, 1, accuracy: 0.001)
        XCTAssertEqual(color.greenComponent, 1, accuracy: 0.001)
        XCTAssertEqual(color.blueComponent, 1, accuracy: 0.001)
        XCTAssertEqual(color.alphaComponent, 1, accuracy: 0.001)
    }

    func testKeychainSessionPolicyIsNonSynchronizableAndDeviceOnly() {
        let policy = KeychainSessionTokenStore.securityPolicyAttributes

        XCTAssertEqual(policy[kSecUseDataProtectionKeychain as String] as? Bool, true)
        XCTAssertEqual(policy[kSecAttrSynchronizable as String] as? Bool, false)
        XCTAssertEqual(
            policy[kSecAttrAccessible as String] as? String,
            kSecAttrAccessibleWhenUnlockedThisDeviceOnly as String
        )
    }

    func testClassicMacKeychainFallbackIsLocalTestingOnlyAndNonSynchronizable() {
        let policy = KeychainSessionTokenStore.classicMacLocalTestingPolicyAttributes

        XCTAssertNil(policy[kSecUseDataProtectionKeychain as String])
        XCTAssertEqual(policy[kSecAttrSynchronizable as String] as? Bool, false)
        XCTAssertNil(policy[kSecAttrAccessible as String])
        XCTAssertTrue(KeychainSessionTokenStore.classicMacFallbackEnabled)
        XCTAssertTrue(
            KeychainSessionTokenStore.shouldUseClassicMacFallback(
                for: errSecMissingEntitlement,
                debugBuild: true
            )
        )
        XCTAssertFalse(
            KeychainSessionTokenStore.shouldUseClassicMacFallback(
                for: errSecAuthFailed,
                debugBuild: true
            )
        )
    }

    func testReleaseKeychainPolicyNeverAllowsClassicMacFallback() {
        XCTAssertFalse(
            KeychainSessionTokenStore.shouldUseClassicMacFallback(
                for: errSecMissingEntitlement,
                debugBuild: false
            )
        )
    }

    func testKeychainFailureProvidesAnActionableSystemStatus() {
        let message = KeychainError.status(errSecMissingEntitlement).localizedDescription

        XCTAssertTrue(message.contains("signing entitlement"))
        XCTAssertTrue(message.contains(String(errSecMissingEntitlement)))
        XCTAssertFalse(message.contains("KeychainError error 0"))
    }

    func testAppSessionCachePurgesAllLegacyPreferencesAndStaysMemoryOnly() throws {
        let defaults = UserDefaults.ephemeralTokenStoreDefaults()
        let encodedSession = try JSONEncoder.backend.encode(DemoAppFixtures.appSession)
        let legacyKeys = [
            "electronic-mail-app-session:live:v1:demo-user",
            "electronic-mail-app-session:preview:v8:other-user",
            "electronic-mail-current-user:live:v1",
            "electronic-mail-current-user:preview:v8",
        ]
        defaults.set(encodedSession, forKey: legacyKeys[0])
        defaults.set(encodedSession, forKey: legacyKeys[1])
        defaults.set("demo-user", forKey: legacyKeys[2])
        defaults.set("other-user", forKey: legacyKeys[3])
        defaults.set("keep", forKey: "unrelated-preference")

        let cache = AppSessionCache(defaults: defaults, namespace: "new-namespace")

        XCTAssertNil(cache.read())
        XCTAssertEqual(defaults.string(forKey: "unrelated-preference"), "keep")
        XCTAssertFalse(defaults.dictionaryRepresentation().keys.contains(where: isLegacySessionPreference))

        cache.write(DemoAppFixtures.appSession)

        XCTAssertEqual(cache.read(), DemoAppFixtures.appSession)
        XCTAssertEqual(cache.read(userID: DemoAppFixtures.appSession.user.id), DemoAppFixtures.appSession)
        XCTAssertNil(cache.read(userID: "different-user"))
        XCTAssertFalse(defaults.dictionaryRepresentation().keys.contains(where: isLegacySessionPreference))

        defaults.set(encodedSession, forKey: "electronic-mail-app-session:late:v3:demo-user")
        defaults.set("demo-user", forKey: "electronic-mail-current-user:late:v3")
        cache.clear()

        XCTAssertNil(cache.read())
        XCTAssertFalse(defaults.dictionaryRepresentation().keys.contains(where: isLegacySessionPreference))
    }

    func testThreadCachePurgesEveryLegacyPreferenceAndNeverPersistsBodies() {
        let defaults = UserDefaults.ephemeralTokenStoreDefaults()
        let legacyKeys = [
            "electronic-mail-thread:live:v1:user-a:thread-a",
            "electronic-mail-thread:live:v4:user-a:thread-b",
            "electronic-mail-thread:preview:v99:user-b:thread-c",
            "electronic-mail-thread:unversioned",
        ]
        let bodyMarker = "PRIVATE-THREAD-BODY-MUST-NOT-ENTER-PREFERENCES"
        for key in legacyKeys {
            defaults.set(Data(bodyMarker.utf8), forKey: key)
        }
        defaults.set("keep", forKey: "unrelated-preference")

        let cache = ThreadCache(defaults: defaults, namespace: "new-namespace")

        XCTAssertEqual(defaults.string(forKey: "unrelated-preference"), "keep")
        XCTAssertFalse(defaults.dictionaryRepresentation().keys.contains { $0.hasPrefix("electronic-mail-thread:") })

        let thread = makeThreadReader(body: bodyMarker)
        cache.write(thread, userID: "user-a", threadID: "thread-a")

        XCTAssertEqual(cache.read(userID: "user-a", threadID: "thread-a"), thread)
        XCTAssertFalse(defaults.dictionaryRepresentation().keys.contains { $0.hasPrefix("electronic-mail-thread:") })
        XCTAssertNil(preferenceData(defaults).range(of: Data(bodyMarker.utf8)))

        defaults.set(Data(bodyMarker.utf8), forKey: "electronic-mail-thread:late:v2:user-a:thread-a")
        cache.clearMemory()

        XCTAssertNil(cache.read(userID: "user-a", threadID: "thread-a"))
        XCTAssertFalse(defaults.dictionaryRepresentation().keys.contains { $0.hasPrefix("electronic-mail-thread:") })
    }

    func testSQLiteClearAllRemovesRowsAndPurgesCachedBodyBytes() throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("ElectronicMailStorageTests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }

        let databaseURL = directory.appendingPathComponent("LocalMail.sqlite3")
        let store = try XCTUnwrap(SQLiteLocalMailStore(databaseURL: databaseURL))
        let bodyMarker = "PRIVATE-BODY-\(UUID().uuidString)-END"
        let privateBody = Array(repeating: bodyMarker, count: 128).joined(separator: "|")
        let thread = makeThreadReader(body: privateBody)
        let userID = DemoAppFixtures.appSession.user.id

        store.writeSession(DemoAppFixtures.appSession)
        store.writeMailbox(
            DemoAppFixtures.appSession.mailbox,
            userID: userID,
            label: DemoAppFixtures.appSession.mailbox.label
        )
        store.writeThread(thread, userID: userID, threadID: thread.entityID)
        store.writePendingThreadAction(
            LocalPendingThreadAction(
                clientActionID: "pending-private-action",
                userID: userID,
                mailboxThreadID: thread.entityID,
                targetMessageID: thread.messages.first?.id,
                action: .moveTrash,
                createdAt: "2026-07-13T00:00:00Z",
                error: nil
            )
        )

        XCTAssertNotNil(store.readSession())
        XCTAssertNotNil(store.readMailbox(userID: userID, label: DemoAppFixtures.appSession.mailbox.label))
        XCTAssertEqual(store.readThread(userID: userID, threadID: thread.entityID), thread)
        XCTAssertEqual(store.pendingThreadActions().count, 1)
        XCTAssertNotNil(sqliteArtifactData(databaseURL: databaseURL).range(of: Data(bodyMarker.utf8)))

        store.clearAll()

        XCTAssertNil(store.readSession())
        XCTAssertNil(store.readMailbox(userID: userID, label: DemoAppFixtures.appSession.mailbox.label))
        XCTAssertNil(store.readThread(userID: userID, threadID: thread.entityID))
        XCTAssertTrue(store.pendingThreadActions().isEmpty)
        XCTAssertNil(sqliteArtifactData(databaseURL: databaseURL).range(of: Data(bodyMarker.utf8)))

        // Clearing must leave the same connection usable after checkpointing and VACUUM.
        store.writeThread(thread, userID: userID, threadID: thread.entityID)
        XCTAssertEqual(store.readThread(userID: userID, threadID: thread.entityID), thread)
    }

    func testSQLiteMailboxCachePreservesAuthoritativeNonDateOrder() throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("ElectronicMailMailboxOrderTests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }

        let store = try XCTUnwrap(SQLiteLocalMailStore(databaseURL: directory.appendingPathComponent("LocalMail.sqlite3")))
        let demoRows = DemoAppFixtures.sections[0].rows
        let mailbox = MailboxResponse(
            label: .inbox,
            totalThreads: 4,
            nextCursor: "generation-cursor-2",
            loadedThreads: 4,
            sections: [
                GmailThreadSection(
                    id: "today:rank-rbi",
                    title: "Today",
                    rows: [demoRows[3], demoRows[0]]
                ),
                GmailThreadSection(
                    id: "yesterday:rank-github",
                    title: "Yesterday",
                    rows: [demoRows[1]]
                ),
                GmailThreadSection(
                    id: "today:rank-apple",
                    title: "Today",
                    rows: [demoRows[2]]
                )
            ],
            mailboxRevision: "revision-ranked"
        )
        let base = DemoAppFixtures.appSession
        let session = AppSessionResponse(
            user: base.user,
            readiness: base.readiness,
            dashboard: base.dashboard,
            mailbox: mailbox,
            sync: base.sync
        )

        store.writeSession(session)
        store.writeMailbox(mailbox, userID: base.user.id, label: .inbox)

        let cachedSession = try XCTUnwrap(store.readSession())
        let cachedMailbox = try XCTUnwrap(store.readMailbox(userID: base.user.id, label: .inbox))
        let expectedIDs = ["demo-rbi-today", "demo-google-today", "demo-github-today", "demo-apple-today"]
        XCTAssertEqual(cachedSession.mailbox.sections.flatMap(\.rows).map(\.threadID), expectedIDs)
        XCTAssertEqual(cachedMailbox.sections.map(\.title), ["Today", "Yesterday", "Today"])
        XCTAssertEqual(cachedMailbox.sections.flatMap(\.rows).map(\.threadID), expectedIDs)
        XCTAssertEqual(cachedMailbox.nextCursor, "generation-cursor-2")
    }

    func testRemoteImagesLoadAutomatically() {
        let html = #"<html><head></head><body><img src="https://tracker.example/pixel.png"><img src="data:image/png;base64,AA=="></body></html>"#

        let rendered = EmailRemoteImagePolicy.renderDocument(from: html)

        XCTAssertTrue(rendered.contains("Content-Security-Policy"))
        XCTAssertTrue(rendered.contains("img-src https: data: cid:"))
        XCTAssertTrue(rendered.contains("script-src 'none'"))
    }

    func testRemoteImagePolicyKeepsScriptsAndFramesDisabled() {
        let html = #"<html><body style="background-image:url('https://images.example/background.png')"></body></html>"#

        let allowed = EmailRemoteImagePolicy.renderDocument(from: html)
        XCTAssertTrue(allowed.contains("img-src https: data: cid:"))
        XCTAssertTrue(allowed.contains("script-src 'none'"))
        XCTAssertTrue(allowed.contains("frame-src 'none'"))
    }

    func testEmailHTMLWebViewUsesEphemeralStorageAndDisablesPageScripts() {
        let configuration = WKWebViewConfiguration()

        EmailHTMLWebViewPolicy.configure(configuration)

        XCTAssertFalse(configuration.websiteDataStore.isPersistent)
        XCTAssertFalse(configuration.defaultWebpagePreferences.allowsContentJavaScript)
    }

    func testExternalLinkPolicyAllowsOnlySafeSchemes() {
        XCTAssertTrue(EmailExternalLinkPolicy.canOpen(URL(string: "https://example.com")!))
        XCTAssertTrue(EmailExternalLinkPolicy.canOpen(URL(string: "mailto:person@example.com")!))
        XCTAssertFalse(EmailExternalLinkPolicy.canOpen(URL(string: "javascript:alert(1)")!))
        XCTAssertFalse(EmailExternalLinkPolicy.canOpen(URL(string: "file:///etc/passwd")!))
        XCTAssertFalse(EmailExternalLinkPolicy.canOpen(URL(string: "data:text/html,hello")!))
    }

    func testThreadReaderResponseDecodesSharedContractFixture() throws {
        let thread = try JSONDecoder.backend.decode(
            ThreadReaderResponse.self,
            from: contractFixtureData("thread-reader.json")
        )

        XCTAssertEqual(thread.gmailThreadID, "thread-1")
        XCTAssertEqual(thread.messages.first?.id, "source-1")
        XCTAssertEqual(thread.messages.first?.bodyComplete, true)
    }

    func testLegacyPlainTextMessageInfersCompleteWhenBodyDiffersFromSnippet() throws {
        let data = """
        {
          "id": "legacy-full-text",
          "source": "gmail",
          "body": "This is the complete plain-text message with additional detail.",
          "snippet": "This is the preview.",
          "reader": {
            "primary_text": "This is the complete plain-text message with additional detail.",
            "markers": [],
            "original_html_available": false
          },
          "label_ids": ["INBOX"],
          "received_at": "2026-07-23T10:00:00Z"
        }
        """.data(using: .utf8)!

        let message = try JSONDecoder.backend.decode(ThreadMessage.self, from: data)

        XCTAssertTrue(message.bodyComplete)
    }

    func testLegacySnippetOnlyMessageInfersIncompleteWithoutHTML() throws {
        let data = """
        {
          "id": "legacy-snippet-only",
          "source": "gmail",
          "body": "This is the metadata preview.",
          "snippet": "This is the metadata preview.",
          "reader": {
            "primary_text": "This is the metadata preview.",
            "markers": [],
            "original_html_available": false
          },
          "label_ids": ["INBOX"],
          "received_at": "2026-07-23T10:00:00Z"
        }
        """.data(using: .utf8)!

        let message = try JSONDecoder.backend.decode(ThreadMessage.self, from: data)

        XCTAssertFalse(message.bodyComplete)
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
              "body_complete": false,
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
        XCTAssertFalse(thread.messages[0].bodyComplete)
    }

    func testGmailMutationResponseDecodesSharedContractFixture() throws {
        let archive = try JSONDecoder.backend.decode(
            GmailThreadMutationResponse.self,
            from: contractFixtureData("gmail-thread-archive.json")
        )

        XCTAssertEqual(archive.threadID, "thread-1")
        XCTAssertEqual(archive.action, .archive)
    }

    func testMailboxRowPresentationUsesOriginalSubjectAndSnippet() {
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

        XCTAssertEqual(row.displayTitle, "Raw Gmail subject")
        XCTAssertEqual(row.displaySummary, "Raw Gmail snippet")
    }

    func testComposerPolicyProtectsDraftContentAndBlankSubjects() {
        XCTAssertFalse(MailComposerPolicy.hasDraftContent(textFields: [" ", "\n"], attachmentCount: 0))
        XCTAssertTrue(MailComposerPolicy.hasDraftContent(textFields: ["", "Draft body"], attachmentCount: 0))
        XCTAssertTrue(MailComposerPolicy.hasDraftContent(textFields: [""], attachmentCount: 1))
        XCTAssertTrue(MailComposerPolicy.requiresEmptySubjectConfirmation("  "))
        XCTAssertFalse(MailComposerPolicy.requiresEmptySubjectConfirmation("Hello"))
    }

    func testComposerPolicyEnforcesAttachmentLimits() {
        XCTAssertTrue(MailComposerPolicy.acceptsAttachment(byteCount: 10 * 1_024 * 1_024, currentTotalBytes: 8 * 1_024 * 1_024))
        XCTAssertFalse(MailComposerPolicy.acceptsAttachment(byteCount: 10 * 1_024 * 1_024 + 1, currentTotalBytes: 0))
        XCTAssertFalse(MailComposerPolicy.acceptsAttachment(byteCount: 2 * 1_024 * 1_024, currentTotalBytes: 17 * 1_024 * 1_024))
    }

    func testComposerPolicyOnlyAppliesSuccessfulDraftSaveResponses() {
        XCTAssertTrue(MailComposerPolicy.shouldApplyDraftSaveResponse(state: .saved))
        XCTAssertFalse(MailComposerPolicy.shouldApplyDraftSaveResponse(state: .failed))
        XCTAssertFalse(MailComposerPolicy.shouldApplyDraftSaveResponse(state: .reauthRequired))
    }

    func testComposerPolicyRetriesUnchangedUnresolvedDraftWithoutSaving() {
        XCTAssertEqual(
            MailComposerPolicy.draftSendPreparation(
                hasUnchangedUnresolvedSendAttempt: true,
                gmailDraftID: "gmail-draft-1",
                clientSendID: "client-send-1"
            ),
            .retryExistingDraft(
                gmailDraftID: "gmail-draft-1",
                clientSendID: "client-send-1"
            )
        )
    }

    func testComposerPolicyEditingUnresolvedAttemptRequiresNewSaveAndSendIdentity() {
        XCTAssertEqual(
            MailComposerPolicy.contentChangeDecision(
                hasUnresolvedSendAttempt: true,
                existingDraftAttachmentCount: 2
            ),
            .forkDraftForNewSendAttempt(discardExistingDraftAttachments: true)
        )
        XCTAssertEqual(
            MailComposerPolicy.contentChangeDecision(hasUnresolvedSendAttempt: false),
            .keepCurrentSendAttempt
        )
        XCTAssertEqual(
            MailComposerPolicy.contentChangeDecision(
                hasUnresolvedSendAttempt: true,
                existingDraftAttachmentCount: 0
            ),
            .forkDraftForNewSendAttempt(discardExistingDraftAttachments: false)
        )
        XCTAssertEqual(
            MailComposerPolicy.draftSendPreparation(
                hasUnchangedUnresolvedSendAttempt: false,
                gmailDraftID: "gmail-draft-1",
                clientSendID: "new-client-send-2"
            ),
            .saveDraft
        )
    }

    func testComposerPolicyMissingGmailDraftRequiresSaveBeforeRetry() {
        XCTAssertEqual(
            MailComposerPolicy.draftSendPreparation(
                hasUnchangedUnresolvedSendAttempt: true,
                gmailDraftID: nil,
                clientSendID: "client-send-1"
            ),
            .saveDraft
        )
        XCTAssertEqual(
            MailComposerPolicy.draftSendPreparation(
                hasUnchangedUnresolvedSendAttempt: true,
                gmailDraftID: "",
                clientSendID: "client-send-1"
            ),
            .saveDraft
        )
    }

    func testComposerHydrationDoesNotForkAnUnresolvedSendUntilContentChanges() {
        var changeTracker = MailComposerDraftChangeTracker()
        changeTracker.synchronize(fingerprint: "recovered-content")

        XCTAssertFalse(changeTracker.shouldHandleChange(fingerprint: "recovered-content"))
        XCTAssertTrue(changeTracker.shouldHandleChange(fingerprint: "edited-content"))
        XCTAssertEqual(
            MailComposerPolicy.contentChangeDecision(
                hasUnresolvedSendAttempt: true,
                existingDraftAttachmentCount: 1
            ),
            .forkDraftForNewSendAttempt(discardExistingDraftAttachments: true)
        )
        XCTAssertFalse(changeTracker.shouldHandleChange(fingerprint: "edited-content"))

        changeTracker.synchronize(fingerprint: "internally-forked-content")
        XCTAssertFalse(changeTracker.shouldHandleChange(fingerprint: "internally-forked-content"))
    }

    func testComposerPolicyPersistsEditedResponsesAsGmailDrafts() {
        XCTAssertNil(MailComposerPolicy.responseMode(for: .compose))
        XCTAssertNil(MailComposerPolicy.responseMode(for: .draft))
        XCTAssertEqual(MailComposerPolicy.responseMode(for: .reply), .reply)
        XCTAssertEqual(MailComposerPolicy.responseMode(for: .replyAll), .replyAll)
        XCTAssertEqual(MailComposerPolicy.responseMode(for: .forward), .forward)

        XCTAssertFalse(
            MailComposerPolicy.shouldPersistDraft(
                mode: .reply,
                hasContent: true,
                responseChanged: false,
                hasGmailDraft: false
            )
        )
        XCTAssertTrue(
            MailComposerPolicy.shouldPersistDraft(
                mode: .reply,
                hasContent: true,
                responseChanged: true,
                hasGmailDraft: false
            )
        )
        XCTAssertTrue(
            MailComposerPolicy.shouldPersistDraft(
                mode: .forward,
                hasContent: false,
                responseChanged: false,
                hasGmailDraft: true
            )
        )
    }

    func testComposerClosePreservesLocalRecoveryWhenOfflineDraftSaveFails() {
        XCTAssertEqual(
            MailComposerPolicy.closeDecision(
                recoveryPersisted: true,
                requiresGmailDraftSave: true,
                gmailDraftSaveState: .failed
            ),
            .finishPreservingRecovery
        )
        XCTAssertEqual(
            MailComposerPolicy.closeDecision(
                recoveryPersisted: true,
                requiresGmailDraftSave: true,
                gmailDraftSaveState: nil
            ),
            .finishPreservingRecovery
        )
        XCTAssertEqual(
            MailComposerPolicy.closeDecision(
                recoveryPersisted: false,
                requiresGmailDraftSave: true,
                gmailDraftSaveState: .failed
            ),
            .block
        )
        XCTAssertEqual(
            MailComposerPolicy.closeDecision(
                recoveryPersisted: true,
                requiresGmailDraftSave: true,
                gmailDraftSaveState: .saved
            ),
            .finishAndClearRecovery
        )
    }

    func testPendingOfflineRecoveryTakesPrecedenceOverOpeningAnotherComposer() {
        XCTAssertTrue(
            MailComposerPolicy.shouldRestorePendingRecovery(
                recoveryAccountUserID: "user-1",
                currentAccountUserID: "user-1"
            )
        )
        XCTAssertFalse(
            MailComposerPolicy.shouldRestorePendingRecovery(
                recoveryAccountUserID: "user-1",
                currentAccountUserID: "user-2"
            )
        )
        XCTAssertFalse(
            MailComposerPolicy.shouldRestorePendingRecovery(
                recoveryAccountUserID: "user-1",
                currentAccountUserID: nil
            )
        )
    }

    func testComposerPresentationWaitsForDelayedRecoveryLoad() {
        var gate = MailComposerRecoveryLoadGate<String>()

        XCTAssertFalse(gate.isComplete)
        XCTAssertNil(gate.request("new composer"))
        XCTAssertNil(gate.request("later command"))
        XCTAssertEqual(
            gate.complete(recoveredPresentation: "offline recovered draft"),
            "offline recovered draft"
        )
        XCTAssertTrue(gate.isComplete)
        XCTAssertEqual(gate.request("new composer after recovery"), "new composer after recovery")
    }

    func testDeferredComposerOpensAfterRecoveryLoadFindsNothing() {
        var gate = MailComposerRecoveryLoadGate<String>()

        XCTAssertNil(gate.request("queued composer"))
        XCTAssertEqual(
            gate.complete(recoveredPresentation: nil),
            "queued composer"
        )
    }

    func testComposerShutdownAllowsOfflineExitOnlyAfterLocalRecovery() {
        XCTAssertEqual(
            MailComposerPolicy.shutdownDecision(
                recoveryPersisted: true,
                requiresGmailDraftSave: true,
                gmailDraftSaveState: .reauthRequired
            ),
            .finishPreservingRecovery
        )
        XCTAssertEqual(
            MailComposerPolicy.shutdownDecision(
                recoveryPersisted: true,
                requiresGmailDraftSave: true,
                gmailDraftSaveState: .saved
            ),
            .finishAndClearRecovery
        )
        XCTAssertEqual(
            MailComposerPolicy.shutdownDecision(
                recoveryPersisted: false,
                requiresGmailDraftSave: true,
                gmailDraftSaveState: nil
            ),
            .block
        )
        XCTAssertEqual(
            MailComposerPolicy.shutdownDecision(
                recoveryPersisted: true,
                requiresGmailDraftSave: false,
                gmailDraftSaveState: nil
            ),
            .finishAndClearRecovery
        )
    }

    func testDraftSaveRequestDecodesResponseFieldsWithBackwardCompatibleDefaults() throws {
        let data = Data(
            #"{"client_draft_id":"legacy-draft","gmail_draft_id":null,"gmail_thread_id":null,"to":[],"cc":[],"bcc":[],"subject":"","body_text":"","body_html":null,"attachments":null,"retained_attachment_ids":null,"created_at":"2026-07-23T00:00:00Z"}"#.utf8
        )

        let request = try JSONDecoder.backend.decode(MailDraftSaveRequest.self, from: data)

        XCTAssertNil(request.responseMode)
        XCTAssertNil(request.mailboxThreadID)
        XCTAssertNil(request.sourceMessageID)
        XCTAssertTrue(request.includeQuotedOriginal)
        XCTAssertTrue(request.includeOriginalAttachments)
    }

    func testComposerPolicyNeverClearsUnchangedResponseRecoveryDuringUnresolvedSend() {
        XCTAssertTrue(
            MailComposerPolicy.shouldClearUnchangedResponseRecovery(
                force: false,
                hasUnresolvedSendAttempt: false,
                mode: .reply,
                restoredFromRecovery: false,
                responseIsUnchanged: true
            )
        )
        XCTAssertFalse(
            MailComposerPolicy.shouldClearUnchangedResponseRecovery(
                force: false,
                hasUnresolvedSendAttempt: true,
                mode: .reply,
                restoredFromRecovery: false,
                responseIsUnchanged: true
            )
        )
    }

    func testDurableSendConfirmationPolicyOnlyClosesForConfirmedSent() {
        let queued = makeSendResponse(state: .queued, serverSendID: "server-send-1")
        let sending = makeSendResponse(state: .sending, serverSendID: "server-send-1")
        let sent = makeSendResponse(state: .sent, serverSendID: "server-send-1")
        let failed = makeSendResponse(state: .failed, serverSendID: "server-send-1", error: "Try again.")
        let untrackedQueue = makeSendResponse(state: .queued, serverSendID: nil)

        XCTAssertEqual(
            DurableSendConfirmationPolicy.decision(for: queued),
            .poll(serverSendID: "server-send-1")
        )
        XCTAssertEqual(
            DurableSendConfirmationPolicy.decision(for: sending),
            .poll(serverSendID: "server-send-1")
        )
        XCTAssertEqual(DurableSendConfirmationPolicy.decision(for: sent), .confirmedSent)
        XCTAssertEqual(
            DurableSendConfirmationPolicy.decision(for: failed),
            .definiteFailure(message: "Try again.")
        )
        guard case .preserveForRetry = DurableSendConfirmationPolicy.decision(for: untrackedQueue) else {
            return XCTFail("A queued response without a durable server ID must stay recoverable.")
        }
    }

    func testDefiniteSendFailureDoesNotRequireDraftForkOnNextEdit() {
        guard case .definiteFailure = DurableSendConfirmationPolicy.decision(
            for: makeSendResponse(state: .failed, serverSendID: "server-send-1")
        ) else {
            return XCTFail("A conclusive backend failure must clear ambiguous-send recovery.")
        }

        XCTAssertEqual(
            MailComposerPolicy.contentChangeDecision(
                hasUnresolvedSendAttempt: false,
                existingDraftAttachmentCount: 2
            ),
            .keepCurrentSendAttempt
        )
        XCTAssertEqual(
            MailComposerPolicy.contentChangeDecision(
                hasUnresolvedSendAttempt: true,
                existingDraftAttachmentCount: 2
            ),
            .forkDraftForNewSendAttempt(discardExistingDraftAttachments: true)
        )
    }

    func testDurableSendConfirmationPolicyUsesBoundedBackoffAndMatchingIdentity() {
        let response = makeSendResponse(state: .sending, serverSendID: "server-send-1")
        let delays = DurableSendConfirmationPolicy.pollDelayNanoseconds

        XCTAssertEqual(delays.count, 5)
        XCTAssertEqual(delays, delays.sorted())
        XCTAssertLessThanOrEqual(delays.reduce(0, +), 7_000_000_000)
        XCTAssertTrue(
            DurableSendConfirmationPolicy.matches(
                response,
                expectedClientSendID: "client-send-1",
                expectedServerSendID: "server-send-1"
            )
        )
        XCTAssertFalse(
            DurableSendConfirmationPolicy.matches(
                response,
                expectedClientSendID: "different-client",
                expectedServerSendID: "server-send-1"
            )
        )
        XCTAssertFalse(
            DurableSendConfirmationPolicy.matches(
                response,
                expectedClientSendID: "client-send-1",
                expectedServerSendID: "different-server"
            )
        )
    }

    func testSignedInDestinationsCoverMailboxLabelsInSidebarOrder() {
        let labels: [MailboxLabel] = [.inbox, .starred, .drafts, .sent, .spam, .trash, .archive, .all]
        let titles = ["Inbox", "Starred", "Drafts", "Sent", "Spam", "Trash", "Archive", "All Mail"]

        XCTAssertEqual(SignedInDestination.allCases.map(\.mailboxLabel), labels)
        XCTAssertEqual(SignedInDestination.allCases.map(\.title), titles)
        XCTAssertEqual(labels.map(SignedInDestination.init(mailboxLabel:)), SignedInDestination.allCases)
    }

    func testFullScreenNavigationKeepsTodosInThePrimaryNavigationList() {
        let destinations = ShellPrimaryNavigationDestination.allCases

        XCTAssertEqual(
            destinations.compactMap(\.mailboxDestination),
            SignedInDestination.allCases
        )
        XCTAssertEqual(
            destinations.map(\.title),
            ["Inbox", "Starred", "Drafts", "Sent", "Spam", "Trash", "Archive", "All Mail", "To-dos"]
        )
        XCTAssertFalse(destinations.map(\.title).contains("Calendar"))
    }

    func testMailboxSearchFieldFocusesOnAttachmentAndRoutesEscape() {
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 420, height: 80),
            styleMask: [.titled],
            backing: .buffered,
            defer: false
        )
        let container = NSView(frame: window.contentLayoutRect)
        window.contentView = container
        let field = ElectronicMailSearchField(frame: NSRect(x: 10, y: 20, width: 400, height: 30))
        var cancelCount = 0
        field.onCancel = { cancelCount += 1 }

        container.addSubview(field)

        XCTAssertTrue(window.firstResponder === field.currentEditor())
        field.cancelOperation(nil)
        XCTAssertEqual(cancelCount, 1)
    }

    func testReplyAllPrefillKeepsSenderInToAndCopiesOtherRecipients() {
        let recipients = MailReplyPrefillPolicy.recipients(
            mode: .replyAll,
            currentUser: "me@example.com",
            sender: "sender@example.com",
            originalTo: ["me@example.com", "other@example.com"],
            originalCC: ["sender@example.com", "copy@example.com", "other@example.com"]
        )

        XCTAssertEqual(recipients.to, ["sender@example.com"])
        XCTAssertEqual(recipients.cc, ["other@example.com", "copy@example.com"])
    }

    func testMailAddressParserPreservesQuotedNamesEscapesAndPlainSeparators() {
        let addresses = MailAddressParser.addresses(
            in: #""Doe, \"Johnny\" John" <john@example.com>, jane@example.com; "Smith, Alice" <alice@example.com>"#
        )

        XCTAssertEqual(addresses, ["john@example.com", "jane@example.com", "alice@example.com"])
        XCTAssertFalse(addresses.contains { !$0.contains("@") })
    }

    func testReplyAllHeaderPrefillDoesNotCreateDisplayNameFragments() {
        let recipients = MailReplyPrefillPolicy.recipients(
            mode: .replyAll,
            currentUser: "me@example.com",
            senderHeader: #""Sender, Sam" <sender@example.com>"#,
            originalToHeader: #""Doe, John" <john@example.com>, me@example.com"#,
            originalCCHeader: #""Smith, Alice" <alice@example.com>; copy@example.com"#
        )

        XCTAssertEqual(recipients.to, ["sender@example.com"])
        XCTAssertEqual(recipients.cc, ["john@example.com", "alice@example.com", "copy@example.com"])
        XCTAssertFalse((recipients.to + recipients.cc).contains { !$0.contains("@") })
    }

    func testReplyPrefillForSentMessageTargetsOriginalRecipients() {
        let reply = MailReplyPrefillPolicy.recipients(
            mode: .reply,
            currentUser: "me@example.com",
            sender: "me@example.com",
            originalTo: ["person@example.com"],
            originalCC: ["copy@example.com"]
        )
        let replyAll = MailReplyPrefillPolicy.recipients(
            mode: .replyAll,
            currentUser: "me@example.com",
            sender: "me@example.com",
            originalTo: ["person@example.com"],
            originalCC: ["copy@example.com"]
        )
        let selfOnly = MailReplyPrefillPolicy.recipients(
            mode: .reply,
            currentUser: "me@example.com",
            sender: "me@example.com",
            originalTo: ["me@example.com"],
            originalCC: []
        )

        XCTAssertEqual(reply, MailReplyPrefillRecipients(to: ["person@example.com"], cc: []))
        XCTAssertEqual(replyAll, MailReplyPrefillRecipients(to: ["person@example.com"], cc: ["copy@example.com"]))
        XCTAssertEqual(selfOnly, MailReplyPrefillRecipients(to: ["me@example.com"], cc: []))
    }

    func testMailboxRowDisplaySenderCleansQuotedDisplayName() {
        let row = makeMailboxPresentationRow(
            sender: "\"Airtel Updates\" <update@airtel.com>",
            latestSender: "fallback@example.com"
        )

        XCTAssertEqual(row.displaySender, "Airtel Updates")
    }

    func testMailboxRowDisplaySenderFormatsEmailOnlySenderWithDomain() {
        let row = makeMailboxPresentationRow(sender: "update@airtel.com")

        XCTAssertEqual(row.displaySender, "Update - airtel.com")
    }

    func testMailboxRowDisplaySenderFormatsBareAngleAddress() {
        let row = makeMailboxPresentationRow(sender: "<alerts@long-subdomain.example.co.in>")

        XCTAssertEqual(row.displaySender, "Alerts - long-subdomain.example.co.in")
    }

    func testMailboxRowDisplaySenderPreservesPersonalGmailAddress() {
        let row = makeMailboxPresentationRow(sender: "yednapg@gmail.com")

        XCTAssertEqual(row.displaySender, "yednapg@gmail.com")
    }

    func testMailboxRowDisplaySenderUsesParticipantsFallback() {
        let row = makeMailboxPresentationRow(
            sender: nil,
            latestSender: nil,
            participants: ["no.reply+statements@mailer.example.com"]
        )

        XCTAssertEqual(row.displaySender, "No Reply Statements - mailer.example.com")
    }

    func testMailboxRowPresentationIgnoresPendingAITitleState() throws {
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

        XCTAssertEqual(row.displayTitle, "Raw Gmail subject")
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
        XCTAssertEqual(row.childRows[1].displayTitle, "Second subject")
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

    func testMailOutboxResponseDecodesDurableSendStates() throws {
        let data = """
        [
          {
            "client_send_id": "client-send-queued",
            "server_send_id": "server-send-queued",
            "mailbox_thread_id": null,
            "gmail_thread_id": null,
            "gmail_message_id": null,
            "state": "queued",
            "queued_at": "2026-05-21T09:00:00Z",
            "sent_at": null,
            "error": null,
            "reauth_url": null
          },
          {
            "client_send_id": "client-send-failed",
            "server_send_id": "server-send-failed",
            "mailbox_thread_id": "thread-1",
            "gmail_thread_id": null,
            "gmail_message_id": null,
            "state": "failed",
            "queued_at": "2026-05-21T09:01:00Z",
            "sent_at": null,
            "error": "Temporary Gmail failure.",
            "reauth_url": null
          }
        ]
        """.data(using: .utf8)!

        let response = try JSONDecoder.backend.decode(MailOutboxResponse.self, from: data)

        XCTAssertEqual(response.map(\.serverSendID), ["server-send-queued", "server-send-failed"])
        XCTAssertEqual(response.map(\.state), [.queued, .failed])
        XCTAssertEqual(response[1].mailboxThreadID, "thread-1")
        XCTAssertEqual(response[1].error, "Temporary Gmail failure.")
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
        let url = try XCTUnwrap(URL(string: "electronicmail://auth/callback?login_code=abc123&handoff_id=handoff-1"))

        XCTAssertEqual(try GoogleOAuthService.loginCode(from: url), "abc123")
        XCTAssertEqual(GoogleOAuthService.handoffID(from: url), "handoff-1")
    }

    func testGoogleOAuthServiceBuildsVerifierBoundHandoffRedirect() throws {
        let pkce = GoogleOAuthService.makePKCEPair()

        XCTAssertGreaterThanOrEqual(pkce.verifier.count, 43)
        XCTAssertFalse(pkce.verifier.contains("="))
        XCTAssertEqual(pkce.challenge.count, 43)
        XCTAssertFalse(pkce.challenge.contains("="))

        let redirect = try GoogleOAuthService.mobileHandoffRedirectURL(
            baseURL: URL(string: "https://mail.example")!,
            handoffID: "handoff-1",
            codeChallenge: pkce.challenge
        )
        let query = try XCTUnwrap(URLComponents(url: redirect, resolvingAgainstBaseURL: false)?.queryItems)
        XCTAssertEqual(query.first(where: { $0.name == "handoff_id" })?.value, "handoff-1")
        XCTAssertEqual(query.first(where: { $0.name == "code_challenge" })?.value, pkce.challenge)
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

    func testEmailBodyResolverRendersSimpleHTMLParagraphsAsText() {
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

    func testEmailBodyResolverRoutesStructuredLongPlainHTMLToText() {
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

        guard case .text(let bodyText) = bodyKind else {
            return XCTFail("Expected text-like table HTML to render through the native text reader")
        }
        XCTAssertTrue(bodyText.contains("FX-Retail platform"))
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

    func testEmailBodyResolverPrefersCleanReaderTextOverTextLikeHTML() {
        let html = #"<html><body><table><tr><td style="background:#fff;color:#000">Noisy HTML</td></tr></table></body></html>"#
        let reader = ThreadMessageReader(
            primaryText: "Clean body only.",
            renderMode: "plain_conversation",
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
        XCTAssertNil(EmailReaderBodyResolver.renderableHTML(from: message))
        XCTAssertEqual(EmailReaderBodyResolver.originalHTML(from: message), html)
    }

    func testEmailBodyResolverHonorsBackendRichHTMLMode() {
        let html = #"<html><body><table><tr><td style="background:#fff;color:#000">Designed body</td></tr></table></body></html>"#
        let reader = ThreadMessageReader(
            primaryText: "Designed body",
            renderMode: "rich_html",
            markers: [],
            signatureText: nil,
            quotedText: nil,
            footerText: nil,
            originalHTMLAvailable: true,
            htmlIsRich: true,
            quoteDetected: false
        )
        let message = makeThreadMessage(
            body: "Fallback",
            htmlBody: html,
            htmlRenderDocument: html,
            reader: reader
        )

        XCTAssertEqual(
            EmailReaderBodyResolver.bodyKind(message: message, fallbackText: ""),
            .html(html, fallbackText: "Designed body")
        )
        XCTAssertEqual(EmailReaderBodyResolver.renderableHTML(from: message), html)
    }

    func testThreadPresentationOrdersOldestToNewestAndExpandsLatestFirst() {
        let newest = makeThreadMessage(id: "newest", receivedAt: "2026-05-19T19:33:06+00:00")
        let oldest = makeThreadMessage(id: "oldest", receivedAt: "2026-05-19T19:31:42+00:00")

        let presentation = EmailThreadPresentation.snapshot(from: [newest, oldest])
        let latestKey = presentation.latestMessageKey

        XCTAssertEqual(presentation.items.map(\.message.id), ["oldest", "newest"])
        XCTAssertEqual(latestKey, .unique(messageID: "newest"))
        XCTAssertFalse(
            EmailThreadPresentation.isExpanded(
                messageKey: .unique(messageID: "oldest"),
                latestMessageKey: latestKey,
                userExpandedMessageKeys: []
            )
        )
        XCTAssertTrue(
            EmailThreadPresentation.isExpanded(
                messageKey: .unique(messageID: "newest"),
                latestMessageKey: latestKey,
                userExpandedMessageKeys: []
            )
        )
    }

    func testThreadPresentationParsesEachTimestampOnceWhileOrdering() {
        let messages = [
            makeThreadMessage(id: "newest", receivedAt: "2026-05-19T19:33:06+00:00"),
            makeThreadMessage(id: "oldest", receivedAt: "2026-05-19T19:31:42+00:00"),
            makeThreadMessage(id: "middle", receivedAt: "2026-05-19T19:32:18+00:00"),
        ]
        var parseCount = 0
        let formatter = ISO8601DateFormatter()

        let ordered = EmailThreadPresentation.orderedMessages(messages) { value in
            parseCount += 1
            return formatter.date(from: value)
        }

        XCTAssertEqual(ordered.map(\.id), ["oldest", "middle", "newest"])
        XCTAssertEqual(parseCount, messages.count)
    }

    func testThreadPresentationUsesUniqueKeysWhenMessageIDsRepeat() {
        let older = makeThreadMessage(id: "duplicate", receivedAt: "2026-05-19T19:31:42+00:00")
        let newer = makeThreadMessage(id: "duplicate", receivedAt: "2026-05-19T19:33:06+00:00")

        let presentation = EmailThreadPresentation.snapshot(from: [newer, older])
        let items = presentation.items
        let latestKey = presentation.latestMessageKey

        XCTAssertEqual(items.map(\.id.messageID), ["duplicate", "duplicate"])
        XCTAssertEqual(Set(items.map(\.id)).count, 2)
        XCTAssertFalse(
            EmailThreadPresentation.isExpanded(
                messageKey: items[0].id,
                latestMessageKey: latestKey,
                userExpandedMessageKeys: []
            )
        )
        XCTAssertTrue(
            EmailThreadPresentation.isExpanded(
                messageKey: items[1].id,
                latestMessageKey: latestKey,
                userExpandedMessageKeys: []
            )
        )
    }

    func testThreadPresentationKeepsExistingKeysStableAcrossInsertionAndInputReordering() {
        let oldest = makeThreadMessage(id: "oldest", receivedAt: "2026-05-19T19:31:42+00:00")
        let newest = makeThreadMessage(id: "newest", receivedAt: "2026-05-19T19:33:06+00:00")
        let inserted = makeThreadMessage(id: "inserted", receivedAt: "2026-05-19T19:30:00+00:00")

        let initial = EmailThreadPresentation.snapshot(from: [newest, oldest])
        let updated = EmailThreadPresentation.snapshot(from: [oldest, inserted, newest])
        let initialIDs = Dictionary(uniqueKeysWithValues: initial.items.map { ($0.message.id, $0.id) })
        let updatedIDs = Dictionary(uniqueKeysWithValues: updated.items.map { ($0.message.id, $0.id) })

        XCTAssertEqual(updated.items.map(\.message.id), ["inserted", "oldest", "newest"])
        XCTAssertEqual(updatedIDs["oldest"], initialIDs["oldest"])
        XCTAssertEqual(updatedIDs["newest"], initialIDs["newest"])
    }

    func testThreadPresentationKeepsDuplicateKeysStableWhenUnrelatedMessagesAreInserted() {
        let older = makeThreadMessage(
            id: "duplicate",
            body: "Older body",
            receivedAt: "2026-05-19T19:31:42+00:00"
        )
        let newer = makeThreadMessage(
            id: "duplicate",
            body: "Newer body",
            receivedAt: "2026-05-19T19:33:06+00:00"
        )
        let inserted = makeThreadMessage(id: "inserted", receivedAt: "2026-05-19T19:30:00+00:00")

        let initial = EmailThreadPresentation.snapshot(from: [newer, older])
        let updated = EmailThreadPresentation.snapshot(from: [older, inserted, newer])
        let initialDuplicateIDs = initial.items.filter { $0.message.id == "duplicate" }.map(\.id)
        let updatedDuplicateIDs = updated.items.filter { $0.message.id == "duplicate" }.map(\.id)

        XCTAssertEqual(updatedDuplicateIDs, initialDuplicateIDs)
        XCTAssertEqual(Set(updated.items.map(\.id)).count, updated.items.count)
    }

    func testThreadPresentationDecodesNativeSubjectEntities() {
        let message = makeThreadMessage(subject: "You&#39;ve successfully modified card controls")

        XCTAssertEqual(
            EmailThreadPresentation.displaySubject(for: message),
            "You've successfully modified card controls"
        )
    }

    func testThreadPresentationDecodesCommonNamedHTMLEntities() {
        let message = makeThreadMessage(subject: "Caf&eacute; &amp; M&uuml;nchen &mdash; d&eacute;j&agrave; vu")

        XCTAssertEqual(
            EmailThreadPresentation.displaySubject(for: message),
            "Café & München — déjà vu"
        )
    }

    func testThreadMessageRenderRevisionIncludesReaderDetailFields() {
        let baseReader = ThreadMessageReader(
            primaryText: "Primary",
            markers: [],
            signatureText: "First signature",
            quotedText: "Quoted",
            footerText: "Footer",
            originalHTMLAvailable: true,
            htmlIsRich: true,
            quoteDetected: true
        )
        let updatedReader = ThreadMessageReader(
            primaryText: "Primary",
            markers: [],
            signatureText: "Updated signature",
            quotedText: "Quoted",
            footerText: "Footer",
            originalHTMLAvailable: true,
            htmlIsRich: true,
            quoteDetected: true
        )

        XCTAssertNotEqual(
            makeThreadMessage(reader: baseReader).renderRevision,
            makeThreadMessage(reader: updatedReader).renderRevision
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

    private func makeMailboxPresentationRow(
        sender: String?,
        latestSender: String? = "Sender <sender@example.com>",
        participants: [String] = ["Sender"]
    ) -> GmailThreadRow {
        GmailThreadRow(
            threadID: "group-1",
            entityID: "group-1",
            title: "Raw Gmail subject",
            href: "/v1/mailbox/threads/group-1",
            latestSourceRecordID: "msg-1",
            latestReceivedAt: "2026-05-23T12:00:00+00:00",
            latestMessageAt: "2026-05-23T12:00:00+00:00",
            latestSubject: "Raw Gmail subject",
            latestSender: latestSender,
            sender: sender,
            participants: participants,
            messageCount: 1,
            summary: "Raw Gmail snippet",
            aiGroupID: "group-1",
            aiTitle: nil,
            aiSummary: nil,
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

    private func makeSendResponse(
        state: MailSendState,
        serverSendID: String?,
        error: String? = nil
    ) -> MailSendResponse {
        MailSendResponse(
            clientSendID: "client-send-1",
            serverSendID: serverSendID,
            mailboxThreadID: "thread-1",
            gmailThreadID: nil,
            gmailMessageID: state == .sent ? "gmail-message-1" : nil,
            state: state,
            queuedAt: "2026-05-21T09:00:00Z",
            sentAt: state == .sent ? "2026-05-21T09:00:01Z" : nil,
            error: error,
            reauthURL: nil
        )
    }

    private func makeThreadReader(body: String) -> ThreadReaderResponse {
        ThreadReaderResponse(
            entityID: "private-thread",
            userID: "private-user",
            source: .gmail,
            gmailThreadID: "gmail-private-thread",
            subject: "Private subject",
            totalMessages: 1,
            messages: [makeThreadMessage(id: "private-message", body: body)]
        )
    }

    private func preferenceData(_ defaults: UserDefaults) -> Data {
        defaults.dictionaryRepresentation().values.reduce(into: Data()) { result, value in
            if let data = value as? Data {
                result.append(data)
            } else if let string = value as? String {
                result.append(Data(string.utf8))
            }
        }
    }

    private func isLegacySessionPreference(_ key: String) -> Bool {
        key.hasPrefix("electronic-mail-app-session:") || key.hasPrefix("electronic-mail-current-user:")
    }

    private func sqliteArtifactData(databaseURL: URL) -> Data {
        [databaseURL, URL(fileURLWithPath: databaseURL.path + "-wal"), URL(fileURLWithPath: databaseURL.path + "-shm")]
            .reduce(into: Data()) { result, url in
                if let data = try? Data(contentsOf: url) {
                    result.append(data)
                }
            }
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
