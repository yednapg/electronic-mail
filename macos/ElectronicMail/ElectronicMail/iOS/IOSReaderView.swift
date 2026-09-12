import ElectronicMailShared
import QuickLook
import SwiftUI
import WebKit

struct IOSReaderView: View {
    @ObservedObject var store: InboxStore
    @Bindable var router: IOSRouter
    let threadID: String
    let focusedMessageID: String?

    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var expandedMessageIDs: Set<String> = []
    @State private var preview: IOSQuickLookItem?
    @State private var attachmentError: String?

    private var reader: MobileReaderPresentation? { store.mobileReader }

    var body: some View {
        Group {
            if let reader {
                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 14) {
                            readerHeader(reader)
                            ForEach(reader.messages) { message in
                                IOSReaderMessageCard(
                                    message: message,
                                    expanded: expandedMessageIDs.contains(message.id),
                                    onToggle: { toggle(message.id) },
                                    onAttachment: { attachment in
                                        Task { await openAttachment(attachment) }
                                    }
                                )
                                .id(message.id)
                            }
                        }
                        .padding(16)
                    }
                    .background(IOSMailDesign.canvas(colorScheme))
                    .onAppear {
                        configureExpansion(reader)
                        let target = focusedMessageID ?? reader.messages.last?.id
                        if let target {
                            DispatchQueue.main.async {
                                withAnimation(reduceMotion ? nil : .easeOut(duration: 0.25)) {
                                    proxy.scrollTo(target, anchor: .top)
                                }
                            }
                        }
                    }
                }
            } else if let error = store.mobileMailbox.searchError {
                ContentUnavailableView("Email unavailable", systemImage: "exclamationmark.triangle", description: Text(error))
            } else {
                VStack(spacing: 12) {
                    ProgressView()
                    Text("Loading conversation…")
                        .foregroundStyle(.secondary)
                }
            }
        }
        .navigationTitle(reader?.subject ?? "Email")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar { readerToolbar }
        .task(id: threadID) {
            _ = store.openReader(threadID: threadID, focusedMessageID: focusedMessageID)
        }
        .onDisappear { store.closeReader() }
        .sheet(item: $preview) { item in
            IOSQuickLookPreview(item: item)
                .ignoresSafeArea()
        }
        .alert("Attachment unavailable", isPresented: Binding(
            get: { attachmentError != nil },
            set: { if !$0 { attachmentError = nil } }
        )) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(attachmentError ?? "The attachment could not be opened.")
        }
    }

    private func readerHeader(_ reader: MobileReaderPresentation) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(reader.subject)
                .font(.system(.title2, design: .rounded, weight: .bold))
                .textSelection(.enabled)
            if let summary = reader.summary, !summary.isEmpty {
                Label(summary, systemImage: "sparkles")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
            Text("\(reader.messages.count) \(reader.messages.count == 1 ? "message" : "messages")")
                .font(.caption)
                .foregroundStyle(.tertiary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(18)
        .mailCard()
    }

    @ToolbarContentBuilder
    private var readerToolbar: some ToolbarContent {
        ToolbarItemGroup(placement: .bottomBar) {
            Button {
                Task { await store.performReaderThreadAction(.archive, threadID: threadID) }
            } label: {
                Label("Archive", systemImage: "archivebox")
            }

            Spacer()

            Menu {
                Button("Mark unread", systemImage: "envelope.badge") {
                    Task { await store.performReaderThreadAction(.markUnread, threadID: threadID) }
                }
                Button("Star", systemImage: "star") {
                    Task { await store.performReaderThreadAction(.star, threadID: threadID) }
                }
                Button("Spam", systemImage: "exclamationmark.shield") {
                    Task { await store.performReaderThreadAction(.markSpam, threadID: threadID) }
                }
                Button("Move to Trash", systemImage: "trash", role: .destructive) {
                    Task { await store.performReaderThreadAction(.moveTrash, threadID: threadID) }
                }
            } label: {
                Label("More", systemImage: "ellipsis.circle")
            }

            Spacer()

            Menu {
                Button("Reply", systemImage: "arrowshape.turn.up.left") { presentComposer(.reply) }
                Button("Reply All", systemImage: "arrowshape.turn.up.left.2") { presentComposer(.replyAll) }
                Button("Forward", systemImage: "arrowshape.turn.up.right") { presentComposer(.forward) }
            } label: {
                Label("Reply", systemImage: "arrowshape.turn.up.left")
            }
        }
    }

    private func configureExpansion(_ reader: MobileReaderPresentation) {
        if let focusedMessageID {
            expandedMessageIDs.insert(focusedMessageID)
        } else if let newest = reader.messages.last?.id {
            expandedMessageIDs.insert(newest)
        }
    }

    private func toggle(_ id: String) {
        if expandedMessageIDs.contains(id) {
            expandedMessageIDs.remove(id)
        } else {
            expandedMessageIDs.insert(id)
        }
        AppHaptics.selection()
    }

    private func presentComposer(_ mode: MailComposerMode) {
        guard let message = reader?.messages.last else { return }
        router.composer = .reply(
            mode: mode,
            threadID: threadID,
            message: message,
            gmailAccountID: store.mailboxViewScope.gmailAccountID
        )
    }

    @MainActor
    private func openAttachment(_ attachment: MobileAttachmentPresentation) async {
        do {
            let payload = try await store.downloadMobileAttachment(
                messageID: attachment.messageID,
                attachmentID: attachment.id
            )
            let directory = FileManager.default.temporaryDirectory
                .appendingPathComponent("ElectronicMail-Preview-\(UUID().uuidString)", isDirectory: true)
            try FileManager.default.createDirectory(
                at: directory,
                withIntermediateDirectories: true,
                attributes: [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication]
            )
            let filename = payload.filename
                .replacingOccurrences(of: "/", with: "-")
                .replacingOccurrences(of: ":", with: "-")
            let fileURL = directory.appendingPathComponent(filename.isEmpty ? "Attachment" : filename)
            try payload.data.write(to: fileURL, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
            preview = IOSQuickLookItem(url: fileURL, title: payload.filename)
        } catch is CancellationError {
            return
        } catch {
            attachmentError = error.localizedDescription
            AppHaptics.error()
        }
    }
}

private struct IOSReaderMessageCard: View {
    @Environment(\.colorScheme) private var colorScheme
    let message: MobileReaderMessagePresentation
    let expanded: Bool
    let onToggle: () -> Void
    let onAttachment: (MobileAttachmentPresentation) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button(action: onToggle) {
                HStack(alignment: .top, spacing: 12) {
                    Circle()
                        .fill(IOSMailDesign.accent.opacity(0.16))
                        .frame(width: 42, height: 42)
                        .overlay {
                            Text(initials)
                                .font(.system(.subheadline, design: .rounded, weight: .bold))
                                .foregroundStyle(IOSMailDesign.accent)
                        }

                    VStack(alignment: .leading, spacing: 3) {
                        Text(message.from)
                            .font(.system(.body, design: .rounded, weight: .semibold))
                            .lineLimit(1)
                        Text(message.receivedAt)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        if !expanded {
                            Text(message.snippet ?? message.body)
                                .font(.subheadline)
                                .foregroundStyle(.secondary)
                                .lineLimit(1)
                        }
                    }
                    Spacer(minLength: 8)
                    if message.starred {
                        Image(systemName: "star.fill").foregroundStyle(.yellow)
                    }
                    Image(systemName: expanded ? "chevron.up" : "chevron.down")
                        .foregroundStyle(.secondary)
                }
                .padding(16)
            }
            .buttonStyle(.plain)

            if expanded {
                Divider().padding(.horizontal, 16)
                VStack(alignment: .leading, spacing: 14) {
                    senderDetails

                    if !message.bodyComplete {
                        Label("The full body is still syncing.", systemImage: "arrow.triangle.2.circlepath")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }

                    if let html = message.html, !html.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                        IOSMailWebView(html: html)
                    } else {
                        Text(message.body)
                            .font(.body)
                            .textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }

                    ForEach(message.attachments) { attachment in
                        Button {
                            onAttachment(attachment)
                        } label: {
                            HStack(spacing: 12) {
                                Image(systemName: "doc")
                                    .font(.title3)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(attachment.filename)
                                        .lineLimit(1)
                                    Text(attachmentSubtitle(attachment))
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                                Spacer()
                                if attachment.isDownloading {
                                    ProgressView()
                                } else {
                                    Image(systemName: "eye")
                                }
                            }
                            .padding(12)
                            .background(IOSMailDesign.elevated(colorScheme), in: RoundedRectangle(cornerRadius: 12))
                        }
                        .buttonStyle(.plain)
                        .minimumTouchTarget()
                    }
                }
                .padding(16)
            }
        }
        .mailCard()
        .accessibilityElement(children: .contain)
    }

    private var senderDetails: some View {
        Grid(alignment: .leading, horizontalSpacing: 10, verticalSpacing: 4) {
            detailRow("From", message.from)
            if let to = message.to { detailRow("To", to) }
            if let cc = message.cc, !cc.isEmpty { detailRow("Cc", cc) }
            if let bcc = message.bcc, !bcc.isEmpty { detailRow("Bcc", bcc) }
        }
        .font(.caption)
    }

    private func detailRow(_ label: String, _ value: String) -> some View {
        GridRow {
            Text(label).foregroundStyle(.secondary)
            Text(value).textSelection(.enabled)
        }
    }

    private var initials: String {
        let name = message.from.split(separator: "<").first.map(String.init) ?? message.from
        let parts = name.split(whereSeparator: \.isWhitespace).prefix(2)
        let value = parts.compactMap(\.first).map(String.init).joined()
        return value.isEmpty ? "@" : value.uppercased()
    }

    private func attachmentSubtitle(_ attachment: MobileAttachmentPresentation) -> String {
        let type = attachment.mimeType ?? "Attachment"
        guard let size = attachment.size else { return type }
        return "\(type) · \(ByteCountFormatter.string(fromByteCount: Int64(size), countStyle: .file))"
    }
}

struct IOSMailWebView: UIViewRepresentable {
    let html: String
    @State private var measuredHeight: CGFloat = 80

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        configuration.defaultWebpagePreferences.allowsContentJavaScript = false
        configuration.setURLSchemeHandler(
            EmailRemoteImageSchemeHandler(),
            forURLScheme: EmailRemoteImageSchemeHandler.scheme
        )
        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = context.coordinator
        webView.scrollView.isScrollEnabled = false
        webView.isOpaque = true
        webView.backgroundColor = .white
        webView.scrollView.backgroundColor = .white
        context.coordinator.onHeight = { height in measuredHeight = max(80, height) }
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        guard context.coordinator.lastHTML != html else { return }
        context.coordinator.lastHTML = html
        webView.loadHTMLString(document, baseURL: nil)
    }

    private var document: String {
        if html.localizedCaseInsensitiveContains("<html") { return html }
        return """
        <!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
        <style>html,body{margin:0;padding:0;background:#fff;color:#17191f;font:-apple-system-body;line-height:1.45}img{max-width:100%;height:auto}pre{white-space:pre-wrap}a{color:#246bfd}</style>
        </head><body>\(html)</body></html>
        """
    }

    func sizeThatFits(_ proposal: ProposedViewSize, uiView: WKWebView, context: Context) -> CGSize? {
        CGSize(width: proposal.width ?? 320, height: measuredHeight)
    }

    final class Coordinator: NSObject, WKNavigationDelegate {
        var lastHTML: String?
        var onHeight: ((CGFloat) -> Void)?

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation?) {
            webView.evaluateJavaScript("Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)") { [weak self] value, _ in
                if let height = value as? CGFloat { self?.onHeight?(height) }
                else if let height = value as? Double { self?.onHeight?(CGFloat(height)) }
            }
        }

        func webView(
            _ webView: WKWebView,
            decidePolicyFor navigationAction: WKNavigationAction,
            decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
        ) {
            guard navigationAction.navigationType == .linkActivated,
                  let url = navigationAction.request.url else {
                decisionHandler(.allow)
                return
            }
            if ["https", "http", "mailto"].contains(url.scheme?.lowercased() ?? "") {
                UIApplication.shared.open(url)
            }
            decisionHandler(.cancel)
        }
    }
}

struct IOSQuickLookItem: Identifiable {
    let url: URL
    let title: String
    var id: URL { url }
}

struct IOSQuickLookPreview: UIViewControllerRepresentable {
    let item: IOSQuickLookItem

    func makeCoordinator() -> Coordinator { Coordinator(item: item) }

    func makeUIViewController(context: Context) -> QLPreviewController {
        let controller = QLPreviewController()
        controller.dataSource = context.coordinator
        return controller
    }

    func updateUIViewController(_ uiViewController: QLPreviewController, context: Context) {}

    final class Coordinator: NSObject, QLPreviewControllerDataSource {
        let item: IOSQuickLookItem
        init(item: IOSQuickLookItem) { self.item = item }
        func numberOfPreviewItems(in controller: QLPreviewController) -> Int { 1 }
        func previewController(_ controller: QLPreviewController, previewItemAt index: Int) -> QLPreviewItem {
            item.url as NSURL
        }
    }
}
