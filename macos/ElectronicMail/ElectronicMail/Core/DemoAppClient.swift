import Foundation

public final class DemoAppClient: AppClient {
    public var baseURL: URL
    public var sessionToken: String?
    public let mode: AppRunMode = .demo

    private var sessionState: AppSessionResponse
    private var threads: [String: ThreadReaderResponse]

    public init(baseURL: URL = AppConfiguration.defaultBackendURL) {
        self.baseURL = baseURL
        self.sessionState = DemoAppFixtures.appSession
        self.threads = DemoAppFixtures.threads
    }

    public func exchangeMobileSession(loginCode: String) async throws -> MobileSessionExchangeResponse {
        MobileSessionExchangeResponse(
            sessionToken: "demo-session-token",
            expiresAt: "2026-06-15T00:00:00+00:00",
            user: AuthUserResponse(id: DemoAppFixtures.userID, email: "demo@example.com", displayName: "Gaurav", accessEnabled: true)
        )
    }

    public func appSession() async throws -> AppSessionResponse {
        sessionState
    }

    public func mailbox(label: MailboxLabel = .inbox, limit: Int = 100, cursor: String? = nil) async throws -> MailboxResponse {
        sessionState.mailbox
    }

    public func thread(threadID: String, limit: Int = 50, offset: Int = 0) async throws -> ThreadReaderResponse {
        guard let thread = threads[threadID] else {
            throw APIError.httpStatus(404)
        }
        return thread
    }

    public func triggerMailboxSync() async throws -> MailboxSyncTriggerResponse {
        MailboxSyncTriggerResponse(
            status: "queued",
            state: MailboxSyncStateResponse(
                connected: true,
                lastHistoryID: nil,
                lastFullSyncAt: sessionState.sync.lastSyncAt,
                watchExpirationAt: nil,
                lastSyncStartedAt: sessionState.sync.lastSyncAt,
                lastSyncCompletedAt: sessionState.sync.lastSyncAt,
                lastSyncError: nil,
                totalThreads: sessionState.mailbox.totalThreads
            ),
            jobID: "demo-sync",
            queuedAt: sessionState.sync.lastSyncAt
        )
    }

    public func syncMailboxNow() async throws -> MailboxSyncTriggerResponse {
        MailboxSyncTriggerResponse(
            status: "synced",
            state: MailboxSyncStateResponse(
                connected: true,
                lastHistoryID: nil,
                lastFullSyncAt: sessionState.sync.lastSyncAt,
                watchExpirationAt: nil,
                lastSyncStartedAt: sessionState.sync.lastSyncAt,
                lastSyncCompletedAt: sessionState.sync.lastSyncAt,
                lastSyncError: nil,
                totalThreads: sessionState.mailbox.totalThreads
            ),
            jobID: nil,
            queuedAt: nil
        )
    }

    public func archiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        markThreadReadLocally(threadID)
        return GmailThreadMutationResponse(threadID: threadID, action: .archive)
    }

    public func unarchiveThread(_ threadID: String) async throws -> GmailThreadMutationResponse {
        GmailThreadMutationResponse(threadID: threadID, action: .unarchive)
    }

    public func markThreadRead(_ threadID: String) async throws -> GmailThreadMutationResponse {
        markThreadReadLocally(threadID)
        return GmailThreadMutationResponse(threadID: threadID, action: .markRead)
    }

    private func markThreadReadLocally(_ threadID: String) {
        sessionState = sessionState.replacingMailboxRows { row in
            guard row.threadID == threadID else {
                return row
            }
            return row.copy(unread: false, labelIDs: row.labelIDs.filter { $0 != "UNREAD" })
        }
    }
}

enum DemoAppFixtures {
    static let now = "2026-05-16T09:30:00+05:30"
    static let userID = "demo-user"

    static let appSession = AppSessionResponse(
        user: AppSessionUser(id: userID, email: "demo@example.com", firstName: "Gaurav", displayName: "Gaurav"),
        readiness: PostLoginReadinessResponse(
            mode: "returning",
            stage: "welcome_back",
            readyToEnter: true,
            dashboardReady: true,
            mailboxReady: true,
            readyDashboardCount: 0,
            readyMailGroupCount: mailbox.totalThreads,
            fullImportRunning: false,
            fullImportCompleted: true,
            userDisplayName: "Gaurav",
            errorMessage: nil
        ),
        dashboard: DashboardResponse(
            auth: GoogleAuthState(available: true, connected: true, connectURL: nil),
            profile: DashboardProfile(email: "demo@example.com", displayName: "Gaurav"),
            briefing: DashboardBriefing(headline: "Inbox", brief: "Demo inbox is ready."),
            feed: FeedResponse(now: [], today: [], worthKnowing: [])
        ),
        mailbox: mailbox,
        sync: AppSessionSyncState(
            lastSyncAt: now,
            lastError: nil,
            enrichmentPendingCount: 0,
            readyGroupCount: mailbox.totalThreads,
            oldestImportedAt: "2026-05-04T12:46:00+05:30",
            fullImportRunning: false,
            fullImportCompleted: true
        )
    )

    static let mailbox = MailboxResponse(
        label: .inbox,
        totalThreads: sections.reduce(0) { $0 + $1.rows.count },
        nextCursor: nil,
        sections: sections,
        readyCount: sections.reduce(0) { $0 + $1.rows.count },
        pendingCount: 0,
        oldestImportedAt: "2026-05-04T12:46:00+05:30",
        fullImportRunning: false,
        fullImportCompleted: true
    )

    static let sections: [GmailThreadSection] = [
        GmailThreadSection(
            id: "today",
            title: "Today",
            rows: [
                row(id: "demo-google-today", sender: "Google", title: "Security alert: new sign-in on your Mac", receivedAt: "2026-05-16T18:35:00+05:30", messageCount: 1, unread: true),
                row(id: "demo-github-today", sender: "GitHub Education", title: "Your Student Developer Pack benefits are ready", receivedAt: "2026-05-16T17:04:00+05:30", messageCount: 1, unread: false),
                row(id: "demo-apple-today", sender: "Apple Developer", title: "App Review needs one more screenshot for macOS", receivedAt: "2026-05-16T12:46:00+05:30", messageCount: 5, unread: false),
                row(id: "demo-rbi-today", sender: "RBI Retail Direct", title: "New Sovereign Gold Bond tranche opens today", receivedAt: "2026-05-16T09:46:00+05:30", messageCount: 1, unread: false),
            ]
        ),
        GmailThreadSection(
            id: "past-seven-days",
            title: "Past 7 days",
            rows: [
                row(id: "demo-google-may15", sender: "Google Calendar", title: "Reminder: Product review starts at 4:30 PM", receivedAt: "2026-05-15T18:35:00+05:30", messageCount: 1, unread: false),
                row(id: "demo-github-may14", sender: "GitHub", title: "Dependabot found 2 vulnerabilities in electronic-mail", receivedAt: "2026-05-14T17:04:00+05:30", messageCount: 1, unread: false),
                row(id: "demo-apple-may11", sender: "Apple Store", title: "Your MacBook Pro shipment is out for delivery", receivedAt: "2026-05-11T12:46:00+05:30", messageCount: 4, unread: false),
                row(id: "demo-yc-may10", sender: "Gaurav & Sam", title: "Notes from today's investor update call", receivedAt: "2026-05-10T09:46:00+05:30", messageCount: 1, unread: false),
            ]
        ),
        GmailThreadSection(
            id: "earlier-this-month",
            title: "Earlier this month",
            rows: [
                row(id: "demo-google-may9", sender: "Google Cloud", title: "Your May invoice is ready to review", receivedAt: "2026-05-09T18:35:00+05:30", messageCount: 1, unread: false),
                row(id: "demo-github-may8", sender: "GitHub Education", title: "Renew your Student Developer Pack before June 1", receivedAt: "2026-05-08T17:04:00+05:30", messageCount: 1, unread: true),
                row(id: "demo-apple-may7", sender: "Apple Developer", title: "Certificate expires in 30 days", receivedAt: "2026-05-07T12:46:00+05:30", messageCount: 3, unread: false),
                row(id: "demo-rbi-may6", sender: "RBI Retail Direct", title: "Your Treasury Bill auction order was accepted", receivedAt: "2026-05-06T09:46:00+05:30", messageCount: 1, unread: false),
                row(id: "demo-google-may6", sender: "Google Workspace", title: "Storage report for gauravpandey.com", receivedAt: "2026-05-06T18:35:00+05:30", messageCount: 1, unread: false),
                row(id: "demo-github-may5", sender: "GitHub Actions", title: "Build failed on main: backend contract tests", receivedAt: "2026-05-05T17:04:00+05:30", messageCount: 1, unread: false),
                row(id: "demo-apple-may4", sender: "TestFlight", title: "Build 42 is ready for internal testing", receivedAt: "2026-05-04T12:46:00+05:30", messageCount: 3, unread: false),
                row(id: "demo-yc-may5", sender: "Sam from YC", title: "Final details for your Startup School session", receivedAt: "2026-05-05T09:46:00+05:30", messageCount: 1, unread: false),
            ]
        ),
    ]

    static let threads: [String: ThreadReaderResponse] = Dictionary(uniqueKeysWithValues: sections.flatMap(\.rows).map { row in
        (row.threadID, thread(from: row))
    })

    private static func row(
        id: String,
        sender: String,
        title: String,
        receivedAt: String,
        messageCount: Int,
        unread: Bool
    ) -> GmailThreadRow {
        GmailThreadRow(
            threadID: id,
            entityID: id,
            title: title,
            href: "/gmail/threads/\(id)",
            latestSourceRecordID: "\(id)-latest",
            latestReceivedAt: receivedAt,
            latestMessageAt: receivedAt,
            latestSubject: title,
            latestSender: sender,
            sender: sender,
            participants: [sender],
            messageCount: messageCount,
            summary: title,
            snippet: title,
            labelIDs: unread ? ["INBOX", "UNREAD"] : ["INBOX"],
            labels: unread ? ["INBOX", "UNREAD"] : ["INBOX"],
            unread: unread,
            actionNeeded: false,
            actionType: "open",
            actionTypeKey: "open",
            priority: 0,
            dashboardVisible: false,
            currentState: .waiting,
            lifecycleState: "active",
            outcomeType: nil,
            lifecycleUpdates: (0..<max(messageCount, 1)).map { index in
                GmailThreadUpdate(
                    sourceRecordID: "\(id)-\(index + 1)",
                    receivedAt: receivedAt,
                    subject: title,
                    sender: sender,
                    summary: index == 0 ? title : "\(title) update \(index + 1)"
                )
            },
            enrichmentStatus: "ready"
        )
    }

    private static func thread(from row: GmailThreadRow) -> ThreadReaderResponse {
        ThreadReaderResponse(
            entityID: row.entityID ?? row.threadID,
            userID: userID,
            source: .gmail,
            gmailThreadID: row.threadID,
            subject: row.displayTitle,
            title: row.displayTitle,
            summary: row.summary,
            totalMessages: row.lifecycleUpdates.count,
            limit: 50,
            offset: 0,
            hasMore: false,
            messages: row.lifecycleUpdates.map { update in
                ThreadMessage(
                    id: update.sourceRecordID,
                    source: .gmail,
                    threadID: row.threadID,
                    fromAddress: update.sender ?? row.sender,
                    to: "gaurav@example.com",
                    cc: nil,
                    bcc: nil,
                    subject: update.subject,
                    body: update.summary ?? row.summary ?? "Demo message body.",
                    htmlBody: nil,
                    snippet: update.summary,
                    labelIDs: row.labelIDs,
                    receivedAt: update.receivedAt
                )
            }
        )
    }
}
