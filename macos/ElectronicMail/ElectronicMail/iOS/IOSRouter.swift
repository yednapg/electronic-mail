import ElectronicMailShared
import Foundation
import Observation

enum IOSRoute: Hashable {
    case reader(threadID: String, focusedMessageID: String?)
    case aiMatter(String)
    case todoSource(String)
    case settings(IOSSettingsDestination)
}

enum IOSSettingsDestination: String, Hashable, CaseIterable, Identifiable {
    case notifications
    case general
    case composing
    case accounts
    case aiInbox
    case accountAndData
    case about

    var id: Self { self }

    var title: String {
        switch self {
        case .notifications: "Notifications"
        case .general: "General"
        case .composing: "Composing"
        case .accounts: "Accounts"
        case .aiInbox: "AI Inbox"
        case .accountAndData: "Account & Data"
        case .about: "About & Legal"
        }
    }

    var symbol: String {
        switch self {
        case .notifications: "bell"
        case .general: "gearshape"
        case .composing: "square.and.pencil"
        case .accounts: "person.crop.circle.badge.checkmark"
        case .aiInbox: "sparkles"
        case .accountAndData: "externaldrive.badge.person.crop"
        case .about: "info.circle"
        }
    }
}

enum IOSPresentedSheet: Identifiable, Equatable {
    case settings
    case newTask
    case aiOrganization

    var id: String {
        switch self {
        case .settings: "settings"
        case .newTask: "new-task"
        case .aiOrganization: "ai-organization"
        }
    }
}

struct IOSComposerContext: Identifiable, Equatable {
    let id: String
    let mode: MailComposerMode
    let threadID: String?
    let sourceMessageID: String?
    let to: [String]
    let cc: [String]
    let bcc: [String]
    let subject: String
    let quotedBody: String?
    let gmailAccountID: String?
    let gmailDraftID: String?
    let clientDraftID: String?
    let gmailThreadID: String?
    let mailboxThreadID: String?
    let retainedAttachments: [MobileDraftAttachmentPresentation]

    static func compose(gmailAccountID: String? = nil) -> Self {
        Self(
            // A stable ID lets an interrupted new message recover after relaunch.
            // Threaded modes remain scoped to their source message below.
            id: "compose-new",
            mode: .compose,
            threadID: nil,
            sourceMessageID: nil,
            to: [],
            cc: [],
            bcc: [],
            subject: "",
            quotedBody: nil,
            gmailAccountID: gmailAccountID,
            gmailDraftID: nil,
            clientDraftID: nil,
            gmailThreadID: nil,
            mailboxThreadID: nil,
            retainedAttachments: []
        )
    }

    static func reply(
        mode: MailComposerMode,
        threadID: String,
        message: MobileReaderMessagePresentation,
        gmailAccountID: String? = nil
    ) -> Self {
        let subjectPrefix = mode == .forward ? "Fwd:" : "Re:"
        let cleanSubject = message.subject
            .replacingOccurrences(of: "Re: ", with: "", options: .caseInsensitive)
            .replacingOccurrences(of: "Fwd: ", with: "", options: .caseInsensitive)
        return Self(
            id: "\(mode.rawValue)-\(threadID)-\(message.id)",
            mode: mode,
            threadID: threadID,
            sourceMessageID: message.id,
            to: mode == .forward ? [] : [message.replyTo ?? message.from],
            cc: mode == .replyAll ? Self.addresses(message.cc) : [],
            bcc: [],
            subject: "\(subjectPrefix) \(cleanSubject)",
            quotedBody: message.body,
            gmailAccountID: gmailAccountID,
            gmailDraftID: nil,
            clientDraftID: nil,
            gmailThreadID: nil,
            mailboxThreadID: nil,
            retainedAttachments: []
        )
    }

    static func draft(_ draft: MobileDraftPresentation) -> Self {
        Self(
            id: "draft-\(draft.id)",
            mode: .draft,
            threadID: nil,
            sourceMessageID: nil,
            to: draft.to,
            cc: draft.cc,
            bcc: draft.bcc,
            subject: draft.subject,
            quotedBody: draft.bodyText,
            gmailAccountID: draft.gmailAccountID,
            gmailDraftID: draft.gmailDraftID,
            clientDraftID: draft.clientDraftID,
            gmailThreadID: draft.gmailThreadID,
            mailboxThreadID: draft.mailboxThreadID,
            retainedAttachments: draft.attachments
        )
    }

    private static func addresses(_ value: String?) -> [String] {
        value?.split(separator: ",").map {
            $0.trimmingCharacters(in: .whitespacesAndNewlines)
        }.filter { !$0.isEmpty } ?? []
    }
}

@MainActor
@Observable
final class IOSRouter {
    var path: [IOSRoute] = []
    var drawerPresented = false
    var sheet: IOSPresentedSheet?
    var composer: IOSComposerContext?

    func resetForAccountChange() {
        path.removeAll()
        sheet = nil
        composer = nil
        drawerPresented = false
    }

    func resetForSignOut() {
        resetForAccountChange()
    }
}
