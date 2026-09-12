import Foundation

public struct MobileInboxSnapshot: Equatable {
    public let totalThreads: Int
    public let sections: [MobileInboxSectionViewModel]
    public let fullImportRunning: Bool

    public var isEmpty: Bool {
        totalThreads == 0 || sections.allSatisfy { $0.rows.isEmpty }
    }
}

public struct MobileInboxSectionViewModel: Equatable, Identifiable {
    public let id: String
    public let title: String
    public let rows: [MobileInboxRowViewModel]
}

public struct MobileInboxRowViewModel: Equatable, Identifiable {
    public let id: String
    public let entityID: String?
    public let sender: String
    public let subject: String
    public let timeLabel: String
    public let unread: Bool
    public let grouped: Bool
    public let dimmed: Bool
}

public enum MobileInboxViewModelBuilder {
    public static func snapshot(from mailbox: MailboxResponse) -> MobileInboxSnapshot {
        MobileInboxSnapshot(
            totalThreads: mailbox.totalThreads,
            sections: mailbox.sections.map { section in
                MobileInboxSectionViewModel(
                    id: section.id,
                    title: section.title,
                    rows: section.rows.map { row in
                        MobileInboxRowViewModel(
                            id: row.threadID,
                            entityID: row.entityID,
                            sender: row.displaySender,
                            subject: row.displayTitle,
                            timeLabel: timeLabel(for: row.latestReceivedAt, sectionTitle: section.title),
                            unread: row.isUnread,
                            grouped: row.isGrouped,
                            dimmed: row.currentState == .done || row.outcomeType == "done" || row.outcomeType == "complete"
                        )
                    }
                )
            },
            fullImportRunning: mailbox.fullImportRunning == true
        )
    }

    private static func timeLabel(for value: String, sectionTitle: String) -> String {
        guard let date = ISO8601DateFormatter.inboxBackend.date(from: value) else {
            return ""
        }

        if sectionTitle.localizedCaseInsensitiveContains("today") {
            return DateFormatter.inboxTime.string(from: date)
        }

        if sectionTitle.localizedCaseInsensitiveContains("yesterday") {
            return "Yesterday"
        }

        return DateFormatter.inboxDate.string(from: date)
    }
}

private extension DateFormatter {
    static let inboxTime: DateFormatter = {
        let formatter = DateFormatter()
        formatter.timeStyle = .short
        formatter.dateStyle = .none
        return formatter
    }()

    static let inboxDate: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "MMM d"
        return formatter
    }()
}

private extension ISO8601DateFormatter {
    static let inboxBackend: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withColonSeparatorInTimeZone]
        return formatter
    }()
}

public struct MobileMailboxPresentation: Equatable {
    public let label: MailboxLabel
    public let title: String
    public let sections: [MobileMailboxSectionPresentation]
    public let total: Int
    public let unread: Int
    public let loading: Bool
    public let loadingMore: Bool
    public let refreshFailed: Bool
    public let searchQuery: String
    public let searchInProgress: Bool
    public let searchError: String?
    public let footerText: String?
    public let syncStatus: String
    public let accountWarnings: [String]

    public var isEmpty: Bool { sections.allSatisfy(\.rows.isEmpty) }
}

public struct MobileMailboxSectionPresentation: Equatable, Identifiable {
    public let id: String
    public let title: String
    public let rows: [MobileMailboxRowPresentation]
}

public struct MobileMailboxRowPresentation: Equatable, Identifiable {
    public let id: String
    public let threadID: String
    public let focusedMessageID: String?
    public let sender: String
    public let title: String
    public let summary: String?
    public let timeLabel: String
    public let unread: Bool
    public let messageCount: Int
    public let hasAttachments: Bool
    public let status: String?
    public let isChild: Bool
    public let isExpandable: Bool
    public let isExpanded: Bool
}

public struct MobileReaderPresentation: Equatable, Identifiable {
    public let id: String
    public let subject: String
    public let summary: String?
    public let focusedMessageID: String?
    public let messages: [MobileReaderMessagePresentation]
}

public struct MobileReaderMessagePresentation: Equatable, Identifiable {
    public let id: String
    public let threadID: String?
    public let from: String
    public let replyTo: String?
    public let to: String?
    public let cc: String?
    public let bcc: String?
    public let subject: String
    public let body: String
    public let html: String?
    public let snippet: String?
    public let receivedAt: String
    public let unread: Bool
    public let starred: Bool
    public let bodyComplete: Bool
    public let attachments: [MobileAttachmentPresentation]
}

public struct MobileAttachmentPresentation: Equatable, Identifiable {
    public let id: String
    public let messageID: String
    public let filename: String
    public let mimeType: String?
    public let size: Int?
    public let isDownloading: Bool
}

public struct MobileAttachmentPayload: Equatable {
    public let filename: String
    public let mimeType: String?
    public let data: Data
}

public struct MobileDraftPresentation: Equatable, Identifiable {
    public let id: String
    public let clientDraftID: String
    public let gmailDraftID: String?
    public let gmailThreadID: String?
    public let mailboxThreadID: String
    public let gmailAccountID: String?
    public let to: [String]
    public let cc: [String]
    public let bcc: [String]
    public let subject: String
    public let bodyText: String
    public let attachments: [MobileDraftAttachmentPresentation]
}

public struct MobileDraftAttachmentPresentation: Equatable, Identifiable {
    public let id: String
    public let filename: String
    public let mimeType: String
    public let attachmentID: String
}

@MainActor
public extension InboxStore {
    var mobileMailbox: MobileMailboxPresentation {
        let count = activeMailboxCount
        return MobileMailboxPresentation(
            label: activeMailboxLabel,
            title: mailboxTitle,
            sections: sections.map { section in
                MobileMailboxSectionPresentation(
                    id: section.id,
                    title: section.title,
                    rows: section.rows.map { row in
                        MobileMailboxRowPresentation(
                            id: row.id,
                            threadID: row.threadID,
                            focusedMessageID: row.focusedMessageID,
                            sender: row.sender,
                            title: row.title,
                            summary: row.summary,
                            timeLabel: row.timeLabel,
                            unread: row.isUnread,
                            messageCount: row.messageCount,
                            hasAttachments: row.hasAttachments,
                            status: row.presentationStatus,
                            isChild: row.isChild,
                            isExpandable: row.isExpandable,
                            isExpanded: row.isExpanded
                        )
                    }
                )
            },
            total: count?.total ?? mailboxVisibleRowCount,
            unread: count?.unread ?? 0,
            loading: !mailboxPresentationReady,
            loadingMore: mailboxPageLoading,
            refreshFailed: refreshFailed,
            searchQuery: searchQuery,
            searchInProgress: searchInProgress,
            searchError: searchError,
            footerText: mailboxFooterProgressText,
            syncStatus: syncStatusText,
            accountWarnings: accountWarnings
        )
    }

    var mobileReader: MobileReaderPresentation? {
        guard let readerThread else { return nil }
        return MobileReaderPresentation(
            id: readerThread.entityID,
            subject: readerThread.title ?? readerThread.subject ?? readerRow?.title ?? "Email",
            summary: readerThread.summary,
            focusedMessageID: readerFocusedMessageID,
            messages: readerThread.messages.sorted { $0.receivedAt < $1.receivedAt }.map { message in
                MobileReaderMessagePresentation(
                    id: message.id,
                    threadID: message.threadID,
                    from: message.fromAddress ?? "Unknown sender",
                    replyTo: message.replyTo,
                    to: message.to,
                    cc: message.cc,
                    bcc: message.bcc,
                    subject: message.subject ?? readerThread.subject ?? "Email",
                    body: message.reader?.primaryText ?? message.body,
                    html: message.htmlRenderDocument ?? message.htmlBody,
                    snippet: message.snippet,
                    receivedAt: message.receivedAt,
                    unread: message.labelIDs.contains("UNREAD"),
                    starred: message.labelIDs.contains("STARRED"),
                    bodyComplete: message.bodyComplete,
                    attachments: message.attachments.map { attachment in
                        MobileAttachmentPresentation(
                            id: attachment.id,
                            messageID: message.id,
                            filename: attachment.filename,
                            mimeType: attachment.mimeType,
                            size: attachment.size,
                            isDownloading: downloadingAttachmentIDs.contains(
                                AttachmentDownloadID(messageID: message.id, attachmentID: attachment.attachmentID)
                            )
                        )
                    }
                )
            }
        )
    }

    var mobileTodoSnapshot: MobileTodoSnapshot? {
        session.map { MobileTodoViewModelBuilder.snapshot(from: $0) }
    }

    var mobileUserDisplayName: String? {
        session?.user.displayName ?? session?.user.firstName
    }

    func downloadMobileAttachment(messageID: String, attachmentID: String) async throws -> MobileAttachmentPayload {
        guard let message = readerThread?.messages.first(where: { $0.id == messageID }),
              let attachment = message.attachments.first(where: { $0.id == attachmentID }) else {
            throw APIError.httpStatus(404)
        }
        let downloaded = try await downloadAttachmentForPreview(attachment, messageID: messageID)
        return MobileAttachmentPayload(
            filename: downloaded.filename,
            mimeType: downloaded.mimeType,
            data: downloaded.data
        )
    }

    func loadMobileDraft(mailboxThreadID: String) async throws -> MobileDraftPresentation {
        try mobileDraftPresentation(
            from: await draft(mailboxThreadID: mailboxThreadID),
            mailboxThreadID: mailboxThreadID
        )
    }

    func saveMobileDraft(
        clientDraftID: String,
        gmailDraftID: String?,
        gmailThreadID: String?,
        mailboxThreadID: String,
        gmailAccountID: String?,
        to: [String],
        cc: [String],
        bcc: [String],
        subject: String,
        bodyText: String,
        bodyHTML: String?,
        attachments: [MailAttachmentUpload],
        retainedAttachmentIDs: [String]
    ) async throws -> MobileDraftPresentation {
        let response = try await saveDraft(MailDraftSaveRequest(
            clientDraftID: clientDraftID,
            gmailDraftID: gmailDraftID,
            gmailThreadID: gmailThreadID,
            to: to,
            cc: cc,
            bcc: bcc,
            subject: subject,
            bodyText: bodyText,
            bodyHTML: bodyHTML,
            attachments: attachments.isEmpty ? nil : attachments,
            retainedAttachmentIDs: retainedAttachmentIDs,
            mailboxThreadID: mailboxThreadID,
            createdAt: ISO8601DateFormatter().string(from: Date()),
            gmailAccountID: gmailAccountID
        ))
        return mobileDraftPresentation(from: response, mailboxThreadID: mailboxThreadID)
    }

    func sendMobileDraft(gmailDraftID: String, clientDraftID: String, clientSendID: String) async throws {
        _ = try await sendDraft(
            gmailDraftID: gmailDraftID,
            clientDraftID: clientDraftID,
            clientSendID: clientSendID
        )
    }

    func deleteMobileDraft(gmailDraftID: String) async throws {
        try await deleteDraft(gmailDraftID: gmailDraftID)
    }

    private func mobileDraftPresentation(
        from response: MailDraftResponse,
        mailboxThreadID: String
    ) -> MobileDraftPresentation {
        MobileDraftPresentation(
            id: response.gmailDraftID ?? response.clientDraftID,
            clientDraftID: response.clientDraftID,
            gmailDraftID: response.gmailDraftID,
            gmailThreadID: response.gmailThreadID,
            mailboxThreadID: mailboxThreadID,
            gmailAccountID: response.gmailAccountID,
            to: response.to,
            cc: response.cc,
            bcc: response.bcc,
            subject: response.subject,
            bodyText: response.bodyText,
            attachments: response.attachments.map { attachment in
                MobileDraftAttachmentPresentation(
                    id: attachment.id,
                    filename: attachment.filename,
                    mimeType: attachment.mimeType,
                    attachmentID: attachment.attachmentID
                )
            }
        )
    }
}

@MainActor
public extension AIInboxStore {
    var mobileMatterReader: MobileReaderPresentation? {
        guard let detail else { return nil }
        return MobileReaderPresentation(
            id: detail.id,
            subject: detail.title,
            summary: detail.summary,
            focusedMessageID: detail.latestReplyableMessageID,
            messages: detail.messages.sorted { $0.receivedAt < $1.receivedAt }.map { message in
                MobileReaderMessagePresentation(
                    id: message.id,
                    threadID: message.threadID,
                    from: message.fromAddress ?? "Unknown sender",
                    replyTo: message.replyTo,
                    to: message.to,
                    cc: message.cc,
                    bcc: message.bcc,
                    subject: message.subject ?? detail.title,
                    body: message.reader?.primaryText ?? message.body,
                    html: message.htmlRenderDocument ?? message.htmlBody,
                    snippet: message.snippet,
                    receivedAt: message.receivedAt,
                    unread: message.labelIDs.contains("UNREAD"),
                    starred: message.labelIDs.contains("STARRED"),
                    bodyComplete: message.bodyComplete,
                    attachments: message.attachments.map { attachment in
                        MobileAttachmentPresentation(
                            id: attachment.id,
                            messageID: message.id,
                            filename: attachment.filename,
                            mimeType: attachment.mimeType,
                            size: attachment.size,
                            isDownloading: false
                        )
                    }
                )
            }
        )
    }
}
