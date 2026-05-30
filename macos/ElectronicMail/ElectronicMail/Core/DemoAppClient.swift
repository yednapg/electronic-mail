import Foundation

public final class DemoAppClient: AppClient {
    public var baseURL: URL
    public var sessionToken: String?
    public let mode: AppRunMode = .demo

    private var sessionState: AppSessionResponse
    private var threads: [String: ThreadReaderResponse]
    private var manualTasks: [String: TaskResponse] = [:]

    public init(baseURL: URL = AppConfiguration.defaultBackendURL) {
        self.baseURL = baseURL
        self.sessionState = DemoAppFixtures.appSession
        self.threads = DemoAppFixtures.threads
    }

    public func exchangeMobileSession(loginCode: String) async throws -> MobileSessionExchangeResponse {
        MobileSessionExchangeResponse(
            sessionToken: "demo-session-token",
            expiresAt: "2026-06-15T00:00:00+00:00",
            user: AuthUserResponse(id: DemoAppFixtures.userID, email: "demo@example.com", displayName: "TestUser", accessEnabled: true)
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

    public func mailboxSyncState() async throws -> MailboxSyncStateResponse {
        MailboxSyncStateResponse(
            connected: true,
            lastHistoryID: nil,
            lastFullSyncAt: sessionState.sync.lastSyncAt,
            watchExpirationAt: nil,
            lastSyncStartedAt: sessionState.sync.lastSyncAt,
            lastSyncCompletedAt: sessionState.sync.lastSyncAt,
            lastSyncError: nil,
            totalThreads: sessionState.mailbox.totalThreads
        )
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

    public func enqueueThreadAction(_ request: QueuedThreadActionRequest) async throws -> QueuedThreadActionResponse {
        if request.action == .archive || request.action == .markRead {
            markThreadReadLocally(request.mailboxThreadID)
        }
        return QueuedThreadActionResponse(
            clientActionID: request.clientActionID,
            serverActionID: "demo-\(request.clientActionID)",
            mailboxThreadID: request.mailboxThreadID,
            targetMessageID: request.targetMessageID,
            action: request.action,
            state: .applied,
            queuedAt: request.createdAt,
            appliedAt: request.createdAt,
            error: nil
        )
    }

    public func sendCompose(_ request: MailComposeRequest) async throws -> MailSendResponse {
        MailSendResponse(
            clientSendID: request.clientSendID,
            serverSendID: "demo-\(request.clientSendID)",
            mailboxThreadID: nil,
            gmailThreadID: "demo-sent-\(request.clientSendID)",
            gmailMessageID: "demo-message-\(request.clientSendID)",
            state: .sent,
            queuedAt: request.createdAt,
            sentAt: request.createdAt,
            error: nil,
            reauthURL: nil
        )
    }

    public func sendReply(threadID: String, request: MailReplyRequest) async throws -> MailSendResponse {
        MailSendResponse(
            clientSendID: request.clientSendID,
            serverSendID: "demo-\(request.clientSendID)",
            mailboxThreadID: threadID,
            gmailThreadID: threadID,
            gmailMessageID: "demo-message-\(request.clientSendID)",
            state: .sent,
            queuedAt: request.createdAt,
            sentAt: request.createdAt,
            error: nil,
            reauthURL: nil
        )
    }

    public func createTask(_ request: TaskCreateRequest) async throws -> TaskResponse {
        let title = request.title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !title.isEmpty else {
            throw APIError.httpStatus(422)
        }

        let id = "demo-manual-\(manualTasks.count + 1)"
        let task = TaskResponse(
            id: id,
            userID: DemoAppFixtures.userID,
            entityID: "manual-task:\(id)",
            title: title,
            notes: request.notes,
            section: request.section ?? "today",
            dueAt: request.dueAt,
            status: "open",
            createdAt: DemoAppFixtures.now,
            updatedAt: DemoAppFixtures.now
        )
        manualTasks[id] = task
        sessionState = sessionState.replacingDashboardFeed {
            $0.appending(DemoAppFixtures.attentionItem(from: task), section: task.section)
        }
        return task
    }

    public func updateTask(_ taskID: String, request: TaskUpdateRequest) async throws -> TaskResponse {
        guard let current = manualTasks[taskID] else {
            throw APIError.httpStatus(404)
        }

        let updated = TaskResponse(
            id: current.id,
            userID: current.userID,
            entityID: current.entityID,
            title: request.title ?? current.title,
            notes: request.notes ?? current.notes,
            section: request.section ?? current.section,
            dueAt: request.dueAt ?? current.dueAt,
            status: request.status ?? current.status,
            createdAt: current.createdAt,
            updatedAt: DemoAppFixtures.now
        )
        manualTasks[taskID] = updated
        sessionState = sessionState.replacingDashboardFeed { feed in
            let withoutCurrent = feed.removingEntity(current.entityID)
            guard updated.status == "open" else {
                return withoutCurrent
            }
            return withoutCurrent.appending(DemoAppFixtures.attentionItem(from: updated), section: updated.section)
        }
        return updated
    }

    public func completeEntity(_ entityID: String, request: EntityOutcomeRequest) async throws -> EntityOutcomeResponse {
        if let task = manualTasks.values.first(where: { $0.entityID == entityID }) {
            manualTasks[task.id] = TaskResponse(
                id: task.id,
                userID: task.userID,
                entityID: task.entityID,
                title: task.title,
                notes: task.notes,
                section: task.section,
                dueAt: task.dueAt,
                status: "done",
                createdAt: task.createdAt,
                updatedAt: DemoAppFixtures.now
            )
        }
        sessionState = sessionState.replacingDashboardFeed { $0.removingEntity(entityID) }
        return EntityOutcomeResponse(
            id: "demo-outcome-\(entityID)",
            userID: DemoAppFixtures.userID,
            entityID: entityID,
            outcomeType: "complete",
            snoozeUntil: nil,
            note: request.note,
            createdAt: DemoAppFixtures.now
        )
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
        user: AppSessionUser(id: userID, email: "demo@example.com", firstName: "TestUser", displayName: "TestUser"),
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
            userDisplayName: "TestUser",
            errorMessage: nil
        ),
        dashboard: DashboardResponse(
            auth: GoogleAuthState(available: true, connected: true, connectURL: nil),
            profile: DashboardProfile(email: "demo@example.com", displayName: "TestUser"),
            briefing: DashboardBriefing(
                headline: "Good morning, TestUser.",
                brief: "You have 3 meetings, 2 tasks and 1 email to reply. One important thing: 📌 macOS review slot needs confirmation. You are mostly free after 4 pm.",
                parts: [
                    DashboardBriefingPart(type: "meetings", emoji: "📆", count: 3, text: "meetings"),
                    DashboardBriefingPart(type: "tasks", emoji: "✅", count: 2, text: "tasks"),
                    DashboardBriefingPart(type: "emails", emoji: "📨", count: 1, text: "emails to reply"),
                ],
                important: DashboardBriefingImportant(
                    emoji: "📌",
                    count: 1,
                    text: "macOS review slot needs confirmation",
                    mailGroupID: "demo-apple-today",
                    actionType: "confirm"
                ),
                calendarAvailability: DashboardCalendarAvailability(
                    emoji: "🌄",
                    kind: "mostly_free_after",
                    time: "4 pm",
                    text: "mostly free after 4 pm"
                )
            ),
            feed: FeedResponse(
                now: [
                    attentionItem(
                        id: "demo-calendar-scrum",
                        entityID: "calendar:demo-scrum",
                        title: "Scrum meeting with Team",
                        body: "Daily team scrum.",
                        source: .calendar,
                        sourceLabel: "Calendar",
                        timingBand: .now,
                        gmailThreadID: nil,
                        primaryAction: "attend",
                        dueAt: "2026-05-16T10:00:00+05:30"
                    ),
                    attentionItem(
                        id: "demo-calendar-pairing",
                        entityID: "calendar:demo-pairing",
                        title: "Pair programming session with Sam",
                        body: "Pair programming session with Sam.",
                        source: .calendar,
                        sourceLabel: "Calendar",
                        timingBand: .now,
                        gmailThreadID: nil,
                        primaryAction: "attend",
                        dueAt: "2026-05-16T12:00:00+05:30"
                    ),
                    attentionItem(
                        id: "demo-rsvp-now",
                        entityID: "demo-apple-today",
                        title: "RSVP within 72 hrs to confirm your macOS review slot",
                        body: "Apple Developer needs one more screenshot before review can continue. Confirm the slot or move it out of today's work.",
                        source: .gmail,
                        sourceLabel: "3 emails from Apple Developer",
                        timingBand: .now,
                        gmailThreadID: "demo-apple-today",
                        primaryAction: "confirm"
                    ),
                ],
                today: [
                    attentionItem(
                        id: "demo-calendar-lunch",
                        entityID: "calendar:demo-lunch",
                        title: "Lunch with Sara",
                        body: "Lunch with Sara.",
                        source: .calendar,
                        sourceLabel: "Calendar",
                        timingBand: .today,
                        gmailThreadID: nil,
                        primaryAction: "attend",
                        dueAt: "2026-05-16T13:30:00+05:30"
                    ),
                    attentionItem(
                        id: "demo-calendar-office-hours",
                        entityID: "calendar:demo-office-hours",
                        title: "a16z Office Hours with Ryan",
                        body: "a16z Office Hours with Ryan.",
                        source: .calendar,
                        sourceLabel: "Calendar",
                        timingBand: .now,
                        gmailThreadID: nil,
                        primaryAction: "attend",
                        dueAt: "2026-05-16T14:45:00+05:30"
                    ),
                    attentionItem(
                        id: "demo-calendar-update",
                        entityID: "calendar:demo-update",
                        title: "Post today's update on #engineering",
                        body: "Post today's update on #engineering.",
                        source: .calendar,
                        sourceLabel: "Calendar",
                        timingBand: .today,
                        gmailThreadID: nil,
                        primaryAction: "attend",
                        dueAt: "2026-05-16T15:00:00+05:30"
                    ),
                    attentionItem(
                        id: "demo-pycon-today",
                        entityID: "demo-github-today",
                        title: "GitHub Education benefits are ready",
                        body: "Review the pack and activate anything useful before the renewal window closes.",
                        source: .gmail,
                        sourceLabel: "GitHub Education",
                        timingBand: .today,
                        gmailThreadID: "demo-github-today",
                        primaryAction: "review"
                    ),
                    attentionItem(
                        id: "demo-manual-seed",
                        entityID: "manual-task:demo-manual-seed",
                        title: "New to-do",
                        body: "Notes",
                        source: .manual,
                        sourceLabel: "Manual",
                        timingBand: .today,
                        gmailThreadID: nil,
                        primaryAction: "open"
                    ),
                ],
                worthKnowing: [
                    attentionItem(
                        id: "demo-worth-knowing",
                        entityID: "demo-rbi-today",
                        title: "New Sovereign Gold Bond tranche opens today",
                        body: "RBI Retail Direct has a new tranche open. Read only if you plan to place an order.",
                        source: .gmail,
                        sourceLabel: "RBI Retail Direct",
                        timingBand: .later,
                        gmailThreadID: "demo-rbi-today",
                        primaryAction: "open"
                    ),
                ]
            )
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
                row(id: "demo-yc-may10", sender: "TestUser & Sam", title: "Notes from today's investor update call", receivedAt: "2026-05-10T09:46:00+05:30", messageCount: 1, unread: false),
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
                row(id: "demo-google-may6", sender: "Google Workspace", title: "Storage report for example.test", receivedAt: "2026-05-06T18:35:00+05:30", messageCount: 1, unread: false),
                row(id: "demo-github-may5", sender: "GitHub Actions", title: "Build failed on main: backend contract tests", receivedAt: "2026-05-05T17:04:00+05:30", messageCount: 1, unread: false),
                row(id: "demo-apple-may4", sender: "TestFlight", title: "Build 42 is ready for internal testing", receivedAt: "2026-05-04T12:46:00+05:30", messageCount: 3, unread: false),
                row(id: "demo-yc-may5", sender: "Sam from YC", title: "Final details for your Startup School session", receivedAt: "2026-05-05T09:46:00+05:30", messageCount: 1, unread: false),
            ]
        ),
    ]

    static let threads: [String: ThreadReaderResponse] = Dictionary(uniqueKeysWithValues: sections.flatMap(\.rows).map { row in
        (row.threadID, thread(from: row))
    })

    static func attentionItem(from task: TaskResponse) -> AttentionItem {
        attentionItem(
            id: "manual-task:\(task.id)",
            entityID: task.entityID,
            title: task.title,
            body: task.notes?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty == false ? task.notes! : "Manual to-do.",
            source: .manual,
            sourceLabel: "Manual",
            timingBand: TimingBand(rawValue: task.section) ?? .today,
            gmailThreadID: nil,
            primaryAction: "open"
        )
    }

    static func attentionItem(
        id: String,
        entityID: String,
        title: String,
        body: String,
        source: SourceType,
        sourceLabel: String,
        timingBand: TimingBand,
        gmailThreadID: String?,
        primaryAction: String,
        dueAt: String? = nil
    ) -> AttentionItem {
        AttentionItem(
            id: id,
            entityID: entityID,
            userID: userID,
            needType: source == .manual ? .decision : .awareness,
            actionType: primaryAction,
            effortLevel: "quick",
            timingBand: timingBand,
            actionConfidence: "high",
            primaryAction: primaryAction,
            fallbackAction: "open",
            title: title,
            whyThisIsHere: body,
            detail: AttentionItemDetail(
                body: [body],
                actionLabel: source == .manual ? "Mark done" : "Open source",
                sourceLabel: sourceLabel
            ),
            dueAt: dueAt,
            importanceLevel: "medium",
            lifecycleState: "active",
            currentState: .open,
            source: source,
            gmailThreadID: gmailThreadID,
            gmailThreadAction: gmailThreadID == nil ? nil : .archive,
            traceID: id,
            createdAt: now
        )
    }

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
                    to: "demo@example.test",
                    cc: nil,
                    bcc: nil,
                    subject: update.subject,
                    body: update.summary ?? row.summary ?? "Demo message body.",
                    htmlBody: nil,
                    htmlRenderDocument: nil,
                    snippet: update.summary,
                    labelIDs: row.labelIDs,
                    receivedAt: update.receivedAt
                )
            }
        )
    }
}
