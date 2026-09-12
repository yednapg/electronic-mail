import ElectronicMailShared
import Observation
import PhotosUI
import SwiftUI
import UniformTypeIdentifiers
import UIKit

struct IOSComposerView: View {
    @ObservedObject var store: InboxStore
    @ObservedObject var accountStore: GmailAccountSettingsStore
    let context: IOSComposerContext

    @Environment(\.dismiss) private var dismiss
    @Environment(\.colorScheme) private var colorScheme
    @State private var toText: String
    @State private var ccText: String
    @State private var bccText = ""
    @State private var subject: String
    @State private var messageBody: NSAttributedString
    @State private var attachments: [IOSComposerAttachment]
    @State private var showingAddressDetails = false
    @State private var showingFileImporter = false
    @State private var photoItems: [PhotosPickerItem] = []
    @State private var sending = false
    @State private var sendError: String?
    @State private var confirmEmptySubject = false
    @State private var confirmDeleteDraft = false
    @State private var clientSendID = UUID().uuidString
    @State private var editorController = IOSRichTextController()

    init(
        store: InboxStore,
        accountStore: GmailAccountSettingsStore,
        context: IOSComposerContext
    ) {
        self.store = store
        self.accountStore = accountStore
        self.context = context
        let recovered = IOSComposerRecovery.load(id: context.id)
        _toText = State(initialValue: recovered?.to ?? context.to.joined(separator: ", "))
        _ccText = State(initialValue: recovered?.cc ?? context.cc.joined(separator: ", "))
        _bccText = State(initialValue: recovered?.bcc ?? context.bcc.joined(separator: ", "))
        _subject = State(initialValue: recovered?.subject ?? context.subject)
        let initialBody = recovered?.body
            ?? (context.mode == .draft ? context.quotedBody : context.quotedBody.map { "\n\n\($0)" })
            ?? ""
        _messageBody = State(initialValue: NSAttributedString(string: initialBody))
        _attachments = State(initialValue: context.retainedAttachments.map(IOSComposerAttachment.init(retained:)))
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                addressFields
                Divider()
                formatBar
                IOSRichTextEditor(text: $messageBody, controller: editorController)
                    .padding(.horizontal, 12)
                    .accessibilityLabel("Message body")

                if !attachments.isEmpty {
                    attachmentStrip
                }
                if let sendError {
                    Label(sendError, systemImage: "exclamationmark.triangle.fill")
                        .font(.footnote)
                        .foregroundStyle(.red)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 8)
                }
            }
            .background(IOSMailDesign.canvas(colorScheme))
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") {
                        saveRecovery()
                        dismiss()
                    }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button {
                        requestSend()
                    } label: {
                        if sending { ProgressView() }
                        else { Text("Send").fontWeight(.semibold) }
                    }
                    .disabled(sending || recipients.isEmpty || messageBody.string.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
                if context.mode == .draft, context.gmailDraftID != nil {
                    ToolbarItem(placement: .bottomBar) {
                        Button(role: .destructive) {
                            confirmDeleteDraft = true
                        } label: {
                            Label("Delete Draft", systemImage: "trash")
                        }
                    }
                }
            }
        }
        .interactiveDismissDisabled(hasRecoverableContent)
        .fileImporter(
            isPresented: $showingFileImporter,
            allowedContentTypes: [.item],
            allowsMultipleSelection: true
        ) { result in
            switch result {
            case .success(let urls): addFiles(urls)
            case .failure(let error): sendError = error.localizedDescription
            }
        }
        .onChange(of: photoItems) { _, items in
            Task { await addPhotos(items) }
        }
        .onChange(of: toText) { _, _ in saveRecovery() }
        .onChange(of: ccText) { _, _ in saveRecovery() }
        .onChange(of: bccText) { _, _ in saveRecovery() }
        .onChange(of: subject) { _, _ in saveRecovery() }
        .onChange(of: messageBody.string) { _, _ in saveRecovery() }
        .alert("Send without a subject?", isPresented: $confirmEmptySubject) {
            Button("Cancel", role: .cancel) {}
            Button("Send") { Task { await send() } }
        } message: {
            Text("This message has no subject.")
        }
        .alert("Delete this draft?", isPresented: $confirmDeleteDraft) {
            Button("Cancel", role: .cancel) {}
            Button("Delete Draft", role: .destructive) { Task { await deleteDraft() } }
        } message: {
            Text("This removes the draft from Gmail and cannot be undone.")
        }
        .alert("Discard message?", isPresented: Binding(
            get: { false },
            set: { _ in }
        )) { }
    }

    private var title: String {
        switch context.mode {
        case .compose: "New Message"
        case .reply: "Reply"
        case .replyAll: "Reply All"
        case .forward: "Forward"
        case .draft: "Draft"
        }
    }

    private var addressFields: some View {
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                Text("From")
                    .foregroundStyle(.secondary)
                    .frame(width: 42, alignment: .leading)
                Picker("Sender", selection: Binding(
                    get: { selectedSenderID },
                    set: { accountStore.defaultSenderAccountID = $0 }
                )) {
                    ForEach(senderAccounts) { account in
                        Text(account.email).tag(String?.some(account.id))
                    }
                    if senderAccounts.isEmpty {
                        Text("Current account").tag(String?.none)
                    }
                }
                .pickerStyle(.menu)
                Spacer()
                Button(showingAddressDetails ? "Hide" : "Cc/Bcc") {
                    showingAddressDetails.toggle()
                }
                .font(.subheadline)
            }
            .padding(.horizontal, 16)
            .frame(minHeight: 44)

            Divider().padding(.leading, 16)
            composerField(label: "To", text: $toText, contentType: .emailAddress)

            if showingAddressDetails || !ccText.isEmpty || !bccText.isEmpty {
                Divider().padding(.leading, 16)
                composerField(label: "Cc", text: $ccText, contentType: .emailAddress)
                Divider().padding(.leading, 16)
                composerField(label: "Bcc", text: $bccText, contentType: .emailAddress)
            }

            Divider().padding(.leading, 16)
            composerField(label: "Subject", text: $subject, contentType: nil)
        }
        .background(IOSMailDesign.surface(colorScheme))
    }

    private func composerField(label: String, text: Binding<String>, contentType: UITextContentType?) -> some View {
        HStack(spacing: 8) {
            Text(label)
                .foregroundStyle(.secondary)
                .frame(width: 58, alignment: .leading)
            TextField(label, text: text)
                .textInputAutocapitalization(contentType == .emailAddress ? .never : .sentences)
                .autocorrectionDisabled(contentType == .emailAddress)
                .keyboardType(contentType == .emailAddress ? .emailAddress : .default)
                .textContentType(contentType)
        }
        .padding(.horizontal, 16)
        .frame(minHeight: 44)
    }

    private var formatBar: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 5) {
                formatButton("bold", label: "Bold") { editorController.toggleBold() }
                formatButton("italic", label: "Italic") { editorController.toggleItalic() }
                formatButton("underline", label: "Underline") { editorController.toggleUnderline() }
                formatButton("list.bullet", label: "Bulleted list") { editorController.insertListPrefix("• ") }
                formatButton("list.number", label: "Numbered list") { editorController.insertListPrefix("1. ") }
                Divider().frame(height: 24)
                Button {
                    showingFileImporter = true
                } label: {
                    Image(systemName: "paperclip")
                }
                .minimumTouchTarget()
                .accessibilityLabel("Attach file")

                PhotosPicker(selection: $photoItems, maxSelectionCount: 8, matching: .images) {
                    Image(systemName: "photo")
                        .minimumTouchTarget()
                }
                .accessibilityLabel("Attach photos")
            }
            .padding(.horizontal, 10)
        }
        .frame(height: 48)
        .background(IOSMailDesign.surface(colorScheme))
    }

    private func formatButton(_ symbol: String, label: String, action: @escaping () -> Void) -> some View {
        Button(action: action) { Image(systemName: symbol) }
            .minimumTouchTarget()
            .accessibilityLabel(label)
    }

    private var attachmentStrip: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(attachments) { attachment in
                    HStack(spacing: 8) {
                        Image(systemName: "doc")
                        VStack(alignment: .leading, spacing: 1) {
                            Text(attachment.filename).lineLimit(1)
                            Text(attachment.data.map {
                                ByteCountFormatter.string(fromByteCount: Int64($0.count), countStyle: .file)
                            } ?? "Saved attachment")
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                        Button {
                            attachments.removeAll { $0.id == attachment.id }
                        } label: {
                            Image(systemName: "xmark.circle.fill")
                        }
                        .accessibilityLabel("Remove \(attachment.filename)")
                    }
                    .padding(10)
                    .background(IOSMailDesign.elevated(colorScheme), in: RoundedRectangle(cornerRadius: 10))
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
        }
        .background(IOSMailDesign.surface(colorScheme))
    }

    private var senderAccounts: [GmailAccount] {
        accountStore.response?.accounts.filter { $0.state == .ready } ?? []
    }

    private var selectedSenderID: String? {
        context.gmailAccountID ?? accountStore.defaultSenderAccountID ?? senderAccounts.first?.id
    }

    private var recipients: [String] { parsedAddresses(toText) }
    private var hasRecoverableContent: Bool {
        !recipients.isEmpty || !subject.isEmpty || !messageBody.string.isEmpty || !attachments.isEmpty
    }

    private func requestSend() {
        if subject.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            confirmEmptySubject = true
        } else {
            Task { await send() }
        }
    }

    @MainActor
    private func send() async {
        guard !sending else { return }
        sending = true
        sendError = nil
        let uploads = attachments.compactMap { attachment -> MailAttachmentUpload? in
            guard let data = attachment.data else { return nil }
            return MailAttachmentUpload(filename: attachment.filename, mimeType: attachment.mimeType, dataBase64: data.base64EncodedString())
        }
        do {
            let html = bodyHTML
            if context.mode == .draft, let mailboxThreadID = context.mailboxThreadID {
                let saved = try await store.saveMobileDraft(
                    clientDraftID: context.clientDraftID ?? context.id,
                    gmailDraftID: context.gmailDraftID,
                    gmailThreadID: context.gmailThreadID,
                    mailboxThreadID: mailboxThreadID,
                    gmailAccountID: selectedSenderID,
                    to: recipients,
                    cc: parsedAddresses(ccText),
                    bcc: parsedAddresses(bccText),
                    subject: subject,
                    bodyText: messageBody.string,
                    bodyHTML: html,
                    attachments: uploads,
                    retainedAttachmentIDs: attachments.compactMap(\.retainedAttachmentID)
                )
                guard let gmailDraftID = saved.gmailDraftID else {
                    throw IOSComposerError.missingDraftIdentifier
                }
                try await store.sendMobileDraft(
                    gmailDraftID: gmailDraftID,
                    clientDraftID: saved.clientDraftID,
                    clientSendID: clientSendID
                )
            } else if context.mode == .compose {
                _ = try await store.sendCompose(
                    clientSendID: clientSendID,
                    gmailAccountID: selectedSenderID,
                    to: recipients,
                    cc: parsedAddresses(ccText),
                    bcc: parsedAddresses(bccText),
                    subject: subject,
                    bodyText: messageBody.string,
                    bodyHTML: html,
                    attachments: uploads
                )
            } else if let threadID = context.threadID {
                _ = try await store.sendReply(
                    clientSendID: clientSendID,
                    threadID: threadID,
                    sourceMessageID: context.sourceMessageID,
                    mode: replyMode,
                    to: recipients,
                    cc: parsedAddresses(ccText),
                    bcc: parsedAddresses(bccText),
                    subject: subject,
                    bodyText: messageBody.string,
                    bodyHTML: html,
                    attachments: uploads
                )
            }
            IOSComposerRecovery.clear(id: context.id)
            AppHaptics.success()
            dismiss()
        } catch {
            sendError = error.localizedDescription
            sending = false
            AppHaptics.error()
        }
    }

    private var replyMode: MailReplyMode {
        switch context.mode {
        case .replyAll: .replyAll
        case .forward: .forward
        default: .reply
        }
    }

    private var bodyHTML: String? {
        let range = NSRange(location: 0, length: messageBody.length)
        return try? String(
            data: messageBody.data(from: range, documentAttributes: [.documentType: NSAttributedString.DocumentType.html]),
            encoding: .utf8
        )
    }

    private func parsedAddresses(_ value: String) -> [String] {
        value.split(whereSeparator: { $0 == "," || $0 == ";" || $0.isNewline })
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
    }

    private func addFiles(_ urls: [URL]) {
        for url in urls {
            let accessed = url.startAccessingSecurityScopedResource()
            defer { if accessed { url.stopAccessingSecurityScopedResource() } }
            do {
                let values = try url.resourceValues(forKeys: [.fileSizeKey, .contentTypeKey])
                let data = try Data(contentsOf: url, options: [.mappedIfSafe])
                try appendAttachment(
                    IOSComposerAttachment(
                        filename: url.lastPathComponent,
                        mimeType: values.contentType?.preferredMIMEType ?? "application/octet-stream",
                        data: data
                    )
                )
            } catch { sendError = error.localizedDescription }
        }
    }

    @MainActor
    private func addPhotos(_ items: [PhotosPickerItem]) async {
        for (index, item) in items.enumerated() {
            do {
                guard let data = try await item.loadTransferable(type: Data.self) else { continue }
                try appendAttachment(
                    IOSComposerAttachment(filename: "Photo-\(index + 1).jpg", mimeType: "image/jpeg", data: data)
                )
            } catch { sendError = error.localizedDescription }
        }
        photoItems = []
    }

    private func appendAttachment(_ attachment: IOSComposerAttachment) throws {
        let maximumBytes = 25 * 1_024 * 1_024
        let nextSize = attachments.reduce(attachment.data?.count ?? 0) { $0 + ($1.data?.count ?? 0) }
        guard nextSize <= maximumBytes else { throw IOSComposerError.attachmentLimit }
        attachments.append(attachment)
    }

    @MainActor
    private func deleteDraft() async {
        guard let gmailDraftID = context.gmailDraftID else { return }
        sending = true
        do {
            try await store.deleteMobileDraft(gmailDraftID: gmailDraftID)
            IOSComposerRecovery.clear(id: context.id)
            dismiss()
        } catch {
            sendError = error.localizedDescription
            sending = false
            AppHaptics.error()
        }
    }

    private func saveRecovery() {
        IOSComposerRecovery.save(
            .init(to: toText, cc: ccText, bcc: bccText, subject: subject, body: messageBody.string),
            id: context.id
        )
    }
}

private struct IOSComposerAttachment: Identifiable {
    let id: String
    let filename: String
    let mimeType: String
    let data: Data?
    let retainedAttachmentID: String?

    init(filename: String, mimeType: String, data: Data) {
        id = UUID().uuidString
        self.filename = filename
        self.mimeType = mimeType
        self.data = data
        retainedAttachmentID = nil
    }

    init(retained: MobileDraftAttachmentPresentation) {
        id = retained.id
        filename = retained.filename
        mimeType = retained.mimeType
        data = nil
        retainedAttachmentID = retained.attachmentID
    }
}

private enum IOSComposerError: LocalizedError {
    case attachmentLimit
    case missingDraftIdentifier

    var errorDescription: String? {
        switch self {
        case .attachmentLimit: "Attachments must total 25 MB or less."
        case .missingDraftIdentifier: "Gmail did not return a saved draft identifier. Try again."
        }
    }
}

private struct IOSComposerRecovery: Codable {
    let to: String
    let cc: String
    let bcc: String
    let subject: String
    let body: String

    static func load(id: String) -> Self? {
        guard let data = UserDefaults.standard.data(forKey: key(id)) else { return nil }
        return try? JSONDecoder().decode(Self.self, from: data)
    }

    static func save(_ value: Self, id: String) {
        guard let data = try? JSONEncoder().encode(value) else { return }
        UserDefaults.standard.set(data, forKey: key(id))
    }

    static func clear(id: String) { UserDefaults.standard.removeObject(forKey: key(id)) }
    private static func key(_ id: String) -> String { "ElectronicMail.iOS.composer.\(id)" }
}

@MainActor
@Observable
private final class IOSRichTextController {
    @ObservationIgnored weak var textView: UITextView?

    func toggleBold() { toggleTrait(.traitBold) }
    func toggleItalic() { toggleTrait(.traitItalic) }

    func toggleUnderline() {
        guard let textView else { return }
        let range = textView.selectedRange
        let location = max(0, min(range.location, max(0, textView.attributedText.length - 1)))
        let current = textView.attributedText.length > 0
            ? (textView.attributedText.attribute(.underlineStyle, at: location, effectiveRange: nil) as? Int ?? 0)
            : 0
        if range.length == 0 {
            var attributes = textView.typingAttributes
            attributes[.underlineStyle] = current == 0 ? NSUnderlineStyle.single.rawValue : 0
            textView.typingAttributes = attributes
        } else {
            let mutable = NSMutableAttributedString(attributedString: textView.attributedText)
            mutable.addAttribute(.underlineStyle, value: current == 0 ? NSUnderlineStyle.single.rawValue : 0, range: range)
            textView.attributedText = mutable
            textView.delegate?.textViewDidChange?(textView)
        }
    }

    func insertListPrefix(_ prefix: String) {
        guard let textView else { return }
        textView.insertText(prefix)
    }

    private func toggleTrait(_ trait: UIFontDescriptor.SymbolicTraits) {
        guard let textView else { return }
        let range = textView.selectedRange
        let baseFont = (range.location < textView.attributedText.length
            ? textView.attributedText.attribute(.font, at: range.location, effectiveRange: nil) as? UIFont
            : nil) ?? UIFont.preferredFont(forTextStyle: .body)
        var traits = baseFont.fontDescriptor.symbolicTraits
        if traits.contains(trait) { traits.remove(trait) } else { traits.insert(trait) }
        guard let descriptor = baseFont.fontDescriptor.withSymbolicTraits(traits) else { return }
        let font = UIFont(descriptor: descriptor, size: 0)
        if range.length == 0 {
            var attributes = textView.typingAttributes
            attributes[.font] = font
            textView.typingAttributes = attributes
        } else {
            let mutable = NSMutableAttributedString(attributedString: textView.attributedText)
            mutable.addAttribute(.font, value: font, range: range)
            textView.attributedText = mutable
            textView.delegate?.textViewDidChange?(textView)
        }
    }
}

private struct IOSRichTextEditor: UIViewRepresentable {
    @Binding var text: NSAttributedString
    let controller: IOSRichTextController

    func makeCoordinator() -> Coordinator { Coordinator(text: $text) }

    func makeUIView(context: Context) -> UITextView {
        let view = UITextView()
        view.delegate = context.coordinator
        view.backgroundColor = .clear
        view.font = .preferredFont(forTextStyle: .body)
        view.adjustsFontForContentSizeCategory = true
        view.textContainerInset = UIEdgeInsets(top: 14, left: 4, bottom: 14, right: 4)
        view.attributedText = text
        controller.textView = view
        return view
    }

    func updateUIView(_ view: UITextView, context: Context) {
        controller.textView = view
        guard !view.attributedText.isEqual(to: text) else { return }
        let selection = view.selectedRange
        view.attributedText = text
        view.selectedRange = selection
    }

    final class Coordinator: NSObject, UITextViewDelegate {
        @Binding var text: NSAttributedString
        init(text: Binding<NSAttributedString>) { _text = text }
        func textViewDidChange(_ textView: UITextView) { text = textView.attributedText }
    }
}
