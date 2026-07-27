import AppKit
import Foundation
import SwiftUI
import WebKit
import libxml2

struct EmailReaderView: View {
    let threadID: String
    let focusedMessageID: String?
    let thread: ThreadReaderResponse?
    let row: InboxRowViewModel?
    let errorMessage: String?
    let currentUserDisplayName: String?
    let currentUserEmail: String?
    let colorScheme: ColorScheme
    let mailboxLabel: MailboxLabel
    let onRetry: () -> Void
    let onRespond: (MailComposerMode, String) -> Void
    let onThreadAction: (GmailThreadAction, String?) -> Void
    let onOpenAttachment: (ThreadAttachment, String) -> Void
    let isAttachmentDownloading: (ThreadAttachment, String) -> Bool

    @State private var expandedMessageKeys: Set<EmailThreadPresentationItem.ID> = []
    @State private var summaryExpanded = false

    var body: some View {
        GeometryReader { proxy in
            let contentWidth = min(
                EmailReaderMetrics.maxContentWidth,
                max(
                    EmailReaderMetrics.minContentWidth,
                    proxy.size.width
                        - ElectronicMailShellMetrics.navLeading * 2
                        - EmailReaderMetrics.sideRunway
                )
            )

            ScrollViewReader { scrollProxy in
                ScrollView(.vertical, showsIndicators: true) {
                    EmailReaderChrome(contentWidth: contentWidth) {
                        if resolvedMessageCount <= 1 {
                            SingleEmailContent(
                                threadID: threadID,
                                title: readerTitle,
                                summary: readerSummary,
                                summaryExpanded: $summaryExpanded,
                                message: thread?.messages.first,
                                row: row,
                                errorMessage: errorMessage,
                                currentUserDisplayName: currentUserDisplayName,
                                currentUserEmail: currentUserEmail,
                                colorScheme: colorScheme,
                                mailboxLabel: mailboxLabel,
                                onRetry: onRetry,
                                onRespond: onRespond,
                                onThreadAction: onThreadAction,
                                onOpenAttachment: onOpenAttachment,
                                isAttachmentDownloading: isAttachmentDownloading
                            )
                        } else {
                            GroupedEmailContent(
                                threadID: threadID,
                                title: readerTitle,
                                summary: readerSummary,
                                summaryExpanded: $summaryExpanded,
                                messages: thread?.messages ?? [],
                                expectedMessageCount: resolvedMessageCount,
                                focusedMessageID: focusedMessageID,
                                errorMessage: errorMessage,
                                currentUserDisplayName: currentUserDisplayName,
                                currentUserEmail: currentUserEmail,
                                colorScheme: colorScheme,
                                mailboxLabel: mailboxLabel,
                                expandedMessageKeys: $expandedMessageKeys,
                                onRetry: onRetry,
                                onRespond: onRespond,
                                onThreadAction: onThreadAction,
                                onOpenAttachment: onOpenAttachment,
                                isAttachmentDownloading: isAttachmentDownloading,
                                onFocusedMessageKey: { messageKey in
                                    scrollProxy.scrollTo(messageKey, anchor: .center)
                                }
                            )
                        }
                    }
                    .padding(.top, EmailReaderMetrics.contentTop)
                    .padding(.bottom, 88)
                    .frame(maxWidth: .infinity)
                }
            }
        }
        .onChange(of: threadID) { _, _ in
            summaryExpanded = false
        }
        .onChange(of: readerSummary) { _, summary in
            if summary == nil {
                summaryExpanded = false
            }
        }
    }

    private var resolvedMessageCount: Int {
        max(1, thread?.messages.count ?? row?.messageCount ?? 1)
    }

    private var readerTitle: String {
        nonEmpty(thread?.title)
            ?? nonEmpty(thread?.subject)
            ?? nonEmpty(row?.title)
            ?? "Email"
    }

    private var readerSummary: String? {
        nil
    }

    private func nonEmpty(_ value: String?) -> String? {
        let trimmed = value?.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed?.isEmpty == false ? trimmed : nil
    }

}

private struct EmailReaderChrome<Content: View>: View {
    let contentWidth: CGFloat
    let content: Content

    init(contentWidth: CGFloat, @ViewBuilder content: () -> Content) {
        self.contentWidth = contentWidth
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            content
        }
        .frame(width: contentWidth, alignment: .leading)
    }
}

private struct SingleEmailContent: View {
    let threadID: String
    let title: String
    let summary: String?
    @Binding var summaryExpanded: Bool
    let message: ThreadMessage?
    let row: InboxRowViewModel?
    let errorMessage: String?
    let currentUserDisplayName: String?
    let currentUserEmail: String?
    let colorScheme: ColorScheme
    let mailboxLabel: MailboxLabel
    let onRetry: () -> Void
    let onRespond: (MailComposerMode, String) -> Void
    let onThreadAction: (GmailThreadAction, String?) -> Void
    let onOpenAttachment: (ThreadAttachment, String) -> Void
    let isAttachmentDownloading: (ThreadAttachment, String) -> Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            EmailReaderTitleHeader(
                title: title,
                summary: summary,
                summaryExpanded: $summaryExpanded,
                colorScheme: colorScheme
            )

            if let errorMessage {
                EmailReaderErrorView(
                    message: errorMessage,
                    colorScheme: colorScheme,
                    onRetry: onRetry
                )
                .padding(.top, 28)
            } else if let message {
                EmailMessageCard(
                    threadID: threadID,
                    message: message,
                    expanded: true,
                    currentUserDisplayName: currentUserDisplayName,
                    currentUserEmail: currentUserEmail,
                    colorScheme: colorScheme,
                    mailboxLabel: mailboxLabel,
                    onRespond: onRespond,
                    onThreadAction: onThreadAction,
                    onOpenAttachment: onOpenAttachment,
                    isAttachmentDownloading: isAttachmentDownloading,
                    onToggle: {}
                )
                .padding(.top, 44)
            } else {
                EmailReaderLoadingCard(colorScheme: colorScheme)
                    .padding(.top, 44)
            }
        }
    }
}

private struct GroupedEmailContent: View {
    let threadID: String
    let title: String
    let summary: String?
    @Binding var summaryExpanded: Bool
    let messages: [ThreadMessage]
    let expectedMessageCount: Int
    let focusedMessageID: String?
    let errorMessage: String?
    let currentUserDisplayName: String?
    let currentUserEmail: String?
    let colorScheme: ColorScheme
    let mailboxLabel: MailboxLabel
    @Binding var expandedMessageKeys: Set<EmailThreadPresentationItem.ID>
    let onRetry: () -> Void
    let onRespond: (MailComposerMode, String) -> Void
    let onThreadAction: (GmailThreadAction, String?) -> Void
    let onOpenAttachment: (ThreadAttachment, String) -> Void
    let isAttachmentDownloading: (ThreadAttachment, String) -> Bool
    let onFocusedMessageKey: (EmailThreadPresentationItem.ID) -> Void

    var body: some View {
        let presentation = EmailThreadPresentation.snapshot(from: messages)
        let renderIdentities = messages.map {
            EmailMessageRenderIdentity(id: $0.id, renderRevision: $0.renderRevision)
        }

        VStack(alignment: .leading, spacing: 0) {
            EmailReaderTitleHeader(
                title: title,
                summary: summary,
                summaryExpanded: $summaryExpanded,
                colorScheme: colorScheme
            )

            if let errorMessage {
                EmailReaderErrorView(
                    message: errorMessage,
                    colorScheme: colorScheme,
                    onRetry: onRetry
                )
                .padding(.top, 52)
            } else if messages.isEmpty {
                loadingCards
                    .padding(.top, 52)
            } else {
                VStack(alignment: .leading, spacing: 20) {
                    ForEach(presentation.items) { item in
                        EmailMessageCard(
                            threadID: threadID,
                            message: item.message,
                            expanded: EmailThreadPresentation.isExpanded(
                                messageKey: item.id,
                                latestMessageKey: presentation.latestMessageKey,
                                userExpandedMessageKeys: expandedMessageKeys
                            ),
                            currentUserDisplayName: currentUserDisplayName,
                            currentUserEmail: currentUserEmail,
                            colorScheme: colorScheme,
                            mailboxLabel: mailboxLabel,
                            onRespond: onRespond,
                            onThreadAction: onThreadAction,
                            onOpenAttachment: onOpenAttachment,
                            isAttachmentDownloading: isAttachmentDownloading
                        ) {
                            toggle(item.id, latestMessageKey: presentation.latestMessageKey)
                        }
                        .id(item.id)
                    }
                }
                .padding(.top, 52)
            }
        }
        .onAppear {
            focusRequestedMessage(in: presentation)
        }
        .onChange(of: focusedMessageID) { _, _ in
            focusRequestedMessage(in: presentation)
        }
        .onChange(of: renderIdentities) { _, _ in
            focusRequestedMessage(in: presentation)
        }
    }

    private var loadingCards: some View {
        VStack(alignment: .leading, spacing: 20) {
            ForEach(0..<min(max(expectedMessageCount, 1), 2), id: \.self) { _ in
                RoundedRectangle(cornerRadius: EmailReaderMetrics.cardRadius, style: .continuous)
                    .fill(ElectronicMailDesign.panelFill(for: colorScheme))
                    .overlay {
                        RoundedRectangle(cornerRadius: EmailReaderMetrics.cardRadius, style: .continuous)
                            .stroke(ElectronicMailDesign.panelBorder(for: colorScheme), lineWidth: 1)
                    }
                    .frame(height: 152)
                    .overlay(alignment: .topLeading) {
                        Text(EmailReaderText.loadingFullEmail)
                            .font(EmailReaderTypography.body())
                            .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                            .padding(.top, 30)
                            .padding(.leading, 32)
                    }
            }
        }
    }

    private func toggle(
        _ messageKey: EmailThreadPresentationItem.ID,
        latestMessageKey: EmailThreadPresentationItem.ID?
    ) {
        if messageKey == latestMessageKey {
            return
        }
        if expandedMessageKeys.contains(messageKey) {
            expandedMessageKeys.remove(messageKey)
        } else {
            expandedMessageKeys.insert(messageKey)
        }
    }

    private func focusRequestedMessage(in presentation: EmailThreadPresentationSnapshot) {
        guard let focusedMessageID,
              let item = presentation.items.first(where: { $0.message.id == focusedMessageID }) else {
            return
        }
        if item.id != presentation.latestMessageKey {
            expandedMessageKeys.insert(item.id)
        }
        DispatchQueue.main.async {
            onFocusedMessageKey(item.id)
        }
    }
}

private struct EmailMessageRenderIdentity: Hashable {
    let id: String
    let renderRevision: UInt64
}

private struct EmailReaderTitleHeader: View {
    let title: String
    let summary: String?
    @Binding var summaryExpanded: Bool
    let colorScheme: ColorScheme

    var body: some View {
        let summaryModel = EmailReaderSummaryDisclosureModel(summary: summary, isExpanded: summaryExpanded)

        VStack(alignment: .leading, spacing: 9) {
            Text(title)
                .font(EmailReaderTypography.title())
                .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                .lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)

            if summaryModel.hasSummary {
                Button {
                    withAnimation(.easeInOut(duration: 0.16)) {
                        summaryExpanded.toggle()
                    }
                } label: {
                    HStack(spacing: 4) {
                        Text(summaryModel.controlTitle)
                        Image(systemName: summaryExpanded ? "chevron.up" : "chevron.down")
                            .font(.system(size: 9, weight: .semibold))
                    }
                    .font(EmailReaderTypography.metadata(weight: .medium))
                    .foregroundStyle(ElectronicMailDesign.appleBlue)
                }
                .buttonStyle(.plain)
                .help(summaryModel.controlAccessibilityLabel)
                .accessibilityLabel(summaryModel.controlAccessibilityLabel)

                if let visibleSummary = summaryModel.visibleSummary {
                    Text(visibleSummary)
                        .font(EmailReaderTypography.subtitle())
                        .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                        .fixedSize(horizontal: false, vertical: true)
                        .textSelection(.enabled)
                        .transition(.opacity.combined(with: .move(edge: .top)))
                }
            }
        }
    }
}

struct EmailReaderSummaryDisclosureModel: Equatable {
    let summary: String?
    let isExpanded: Bool

    init(summary: String?, isExpanded: Bool) {
        let trimmed = summary?.trimmingCharacters(in: .whitespacesAndNewlines)
        self.summary = trimmed?.isEmpty == false ? trimmed : nil
        self.isExpanded = isExpanded
    }

    var hasSummary: Bool {
        summary != nil
    }

    var controlTitle: String {
        isExpanded ? "Hide details" : "Message details"
    }

    var controlAccessibilityLabel: String {
        controlTitle
    }

    var visibleSummary: String? {
        isExpanded ? summary : nil
    }
}

private struct EmailBodyContent: View, Equatable {
    let threadID: String
    let message: ThreadMessage?
    let fallbackText: String
    let colorScheme: ColorScheme
    private let resolutionID: EmailBodyResolutionID
    @State private var resolvedBody: EmailPreparedBody?

    init(
        threadID: String,
        message: ThreadMessage?,
        fallbackText: String,
        colorScheme: ColorScheme
    ) {
        self.threadID = threadID
        self.message = message
        self.fallbackText = fallbackText
        self.colorScheme = colorScheme
        self.resolutionID = EmailBodyResolutionID(
            messageKey: message?.id ?? threadID,
            renderRevision: message?.renderRevision ?? 0,
            usesDarkMode: colorScheme == .dark
        )
    }

    static func == (lhs: EmailBodyContent, rhs: EmailBodyContent) -> Bool {
        lhs.threadID == rhs.threadID
            && lhs.resolutionID == rhs.resolutionID
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            switch resolvedBody {
            case .some(.html(let preparedDocument, let fallbackText)):
                EmailOriginalBodyView(
                    preparedDocument: preparedDocument,
                    fallbackText: fallbackText,
                    threadID: threadID,
                    messageID: message?.id,
                    renderRevision: resolutionID.renderRevision
                )
            case .some(.text(let attributedText)):
                if !markers.isEmpty {
                    EmailReaderMarkerRow(markers: markers, colorScheme: colorScheme)
                }

                EmailPreparedTextBodyView(
                    attributedText: attributedText,
                    colorScheme: colorScheme
                )
            case .none:
                HStack(spacing: 10) {
                    ProgressView().controlSize(.small)
                    Text("Preparing message…")
                        .font(EmailReaderTypography.metadata())
                        .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                }
                .frame(maxWidth: .infinity, minHeight: 120, alignment: .center)
            }
        }
        .task(id: resolutionID) {
            resolvedBody = nil
            let input = EmailBodyResolutionInput(
                message: message,
                fallbackText: fallbackText,
                threadID: threadID,
                usesDarkMode: colorScheme == .dark
            )
            let work = Task.detached(priority: .userInitiated) { () -> EmailPreparedBody? in
                guard !Task.isCancelled else { return nil }
                let bodyKind = EmailReaderBodyResolver.bodyKind(
                    message: input.message,
                    fallbackText: input.fallbackText,
                    threadID: input.threadID
                )
                guard !Task.isCancelled else { return nil }

                switch bodyKind {
                case .html(let sourceHTML, let fallbackText):
                    let renderable = EmailHTMLDocument.renderableDocument(from: sourceHTML, colorScheme: .light)
                    guard !Task.isCancelled else { return nil }
                    return .html(
                        EmailHTMLPreparedDocument(
                            remoteImagesDocument: EmailRemoteImagePolicy.renderDocument(from: renderable)
                        ),
                        fallbackText: fallbackText
                    )
                case .text(let bodyText):
                    guard !Task.isCancelled else { return nil }
                    return .text(
                        EmailReaderText.attributedPlainText(
                            bodyText,
                            colorScheme: input.usesDarkMode ? .dark : .light
                        )
                    )
                }
            }
            let nextBody = await withTaskCancellationHandler {
                await work.value
            } onCancel: {
                work.cancel()
            }
            guard let nextBody, !Task.isCancelled else { return }
            resolvedBody = nextBody
        }
    }

    private var markers: [ThreadMessageReaderMarker] {
        message?.reader?.markers.uniquedPreservingOrder() ?? []
    }

}

private struct EmailBodyResolutionID: Hashable {
    let messageKey: String
    let renderRevision: UInt64
    let usesDarkMode: Bool
}

private struct EmailBodyResolutionInput: @unchecked Sendable {
    let message: ThreadMessage?
    let fallbackText: String
    let threadID: String
    let usesDarkMode: Bool
}

private enum EmailPreparedBody: @unchecked Sendable {
    case html(EmailHTMLPreparedDocument, fallbackText: String)
    case text(AttributedString)
}

enum EmailReaderBodyKind: Equatable, Sendable {
    case html(String, fallbackText: String)
    case text(String)
}

enum EmailReaderBodyResolver {
    static func bodyKind(message: ThreadMessage?, fallbackText: String, threadID: String? = nil) -> EmailReaderBodyKind {
        if Task.isCancelled {
            return .text(EmailReaderText.loadingFullEmail)
        }
        if let html = nonEmpty(message?.htmlRenderDocument) ?? nonEmpty(message?.htmlBody) {
            let analysis = analyzeHTML(html)
            if Task.isCancelled {
                return .text(EmailReaderText.loadingFullEmail)
            }
            let fallback = readableFallbackText(message: message, fallbackText: fallbackText, analysis: analysis)
            let shouldRenderHTML = shouldRenderHTML(message: message, analysis: analysis)

            if shouldRenderHTML {
                logClassification(
                    threadID: threadID,
                    messageID: message?.id,
                    mode: "html",
                    visibleTextLength: analysis.visibleTextLength,
                    imageCount: analysis.imageCount,
                    tableCount: analysis.tableCount,
                    fallbackReason: nil
                )

                return .html(html, fallbackText: fallback)
            }

            if let primaryText = nonEmpty(message?.reader?.primaryText) {
                logClassification(
                    threadID: threadID,
                    messageID: message?.id,
                    mode: "reader",
                    visibleTextLength: primaryText.components(separatedBy: .whitespacesAndNewlines).joined().count,
                    imageCount: analysis.imageCount,
                    tableCount: analysis.tableCount,
                    fallbackReason: "html-classified-as-conversation"
                )
                return .text(primaryText)
            }

            logClassification(
                threadID: threadID,
                messageID: message?.id,
                mode: "text",
                visibleTextLength: fallback.components(separatedBy: .whitespacesAndNewlines).joined().count,
                imageCount: analysis.imageCount,
                tableCount: analysis.tableCount,
                fallbackReason: "html-without-reader-primary"
            )
            return .text(fallback)
        }

        if let primaryText = nonEmpty(message?.reader?.primaryText) {
            logClassification(
                threadID: threadID,
                messageID: message?.id,
                mode: "reader",
                visibleTextLength: primaryText.components(separatedBy: .whitespacesAndNewlines).joined().count,
                imageCount: 0,
                tableCount: 0,
                fallbackReason: nil
            )
            return .text(primaryText)
        }

        let bodyText = nonEmpty(message?.body).map { readableBodyText($0) }
            ?? nonEmpty(fallbackText)
            ?? EmailReaderText.loadingFullEmail
        let visibleTextLength = bodyText.components(separatedBy: .whitespacesAndNewlines).joined().count
        logClassification(
            threadID: threadID,
            messageID: message?.id,
            mode: "text",
            visibleTextLength: visibleTextLength,
            imageCount: 0,
            tableCount: 0,
            fallbackReason: nil
        )
        return .text(bodyText)
    }

    static func renderableHTML(from message: ThreadMessage?) -> String? {
        guard let html = nonEmpty(message?.htmlRenderDocument) ?? nonEmpty(message?.htmlBody) else {
            return nil
        }
        let analysis = analyzeHTML(html)
        return shouldRenderHTML(message: message, analysis: analysis) ? html : nil
    }

    static func originalHTML(from message: ThreadMessage?) -> String? {
        nonEmpty(message?.htmlRenderDocument) ?? nonEmpty(message?.htmlBody)
    }

    static func isRichEmailHTML(_ value: String) -> Bool {
        guard let html = nonEmpty(value) else {
            return false
        }
        return analyzeHTML(html).isRich
    }

    private static func shouldRenderHTML(message: ThreadMessage?, analysis: HTMLBodyAnalysis) -> Bool {
        switch message?.reader?.renderMode {
        case "rich_html":
            return true
        case "plain_conversation", "mixed":
            return false
        default:
            return message?.reader?.htmlIsRich ?? analysis.isRich
        }
    }

    private static func analyzeHTML(_ value: String) -> HTMLBodyAnalysis {
        if Task.isCancelled {
            return HTMLBodyAnalysis(
                sourceLength: 0,
                plainText: "",
                visibleTextLength: 0,
                imageCount: 0,
                substantiveImageCount: 0,
                trackingImageCount: 0,
                tableCount: 0,
                tableTagCount: 0,
                layoutTagCount: 0,
                styleCount: 0,
                classCount: 0,
                hasPictureElement: false
            )
        }
        let imageTags = matches(pattern: #"<\s*img\b[^>]*>"#, in: value)
        var substantiveImageCount = 0
        var trackingImageCount = 0

        for tag in imageTags {
            if Task.isCancelled {
                break
            }
            let attrs = htmlAttributes(in: tag)
            let style = attrs["style"] ?? ""
            if isHiddenImageStyle(style) || isTrackingImage(attrs: attrs, style: style) {
                trackingImageCount += 1
                continue
            }

            let sizes = imageSizes(attrs: attrs, style: style)
            if sizes.contains(where: { $0 <= 2 }) {
                trackingImageCount += 1
            } else if isMeaningfulImageSize(sizes) {
                substantiveImageCount += 1
            }
        }

        let plainText = Task.isCancelled ? "" : plainText(fromHTML: value)
        return HTMLBodyAnalysis(
            sourceLength: value.count,
            plainText: plainText,
            visibleTextLength: plainText.components(separatedBy: .whitespacesAndNewlines).joined().count,
            imageCount: imageTags.count,
            substantiveImageCount: substantiveImageCount,
            trackingImageCount: trackingImageCount,
            tableCount: count(pattern: #"<\s*table\b"#, in: value),
            tableTagCount: count(pattern: #"<\s*(table|tbody|thead|tfoot|tr|td|th)\b"#, in: value),
            layoutTagCount: count(pattern: #"<\s*(center|font|hr)\b"#, in: value),
            styleCount: count(pattern: #"\sstyle\s*="#, in: value),
            classCount: count(pattern: #"\sclass\s*="#, in: value),
            hasPictureElement: contains(pattern: #"<\s*(picture|source)\b"#, in: value)
        )
    }

    private static func readableFallbackText(
        message: ThreadMessage?,
        fallbackText: String,
        analysis: HTMLBodyAnalysis
    ) -> String {
        nonEmpty(analysis.plainText)
            ?? nonEmpty(message?.body).map { readableBodyText($0) }
            ?? nonEmpty(message?.snippet).map { EmailReaderText.decodingHTML($0) }
            ?? nonEmpty(fallbackText).map { readableBodyText($0) }
            ?? EmailReaderText.loadingFullEmail
    }

    private static func readableBodyText(_ value: String) -> String {
        if looksLikeHTML(value) {
            return plainText(fromHTML: value)
        }
        let decoded = EmailReaderText.decodingHTML(value.trimmingCharacters(in: .whitespacesAndNewlines))
        return restoringPlainTextParagraphs(decoded)
    }

    private static func restoringPlainTextParagraphs(_ value: String) -> String {
        let normalized = value
            .replacingOccurrences(of: "\r\n", with: "\n")
            .replacingOccurrences(of: "\r", with: "\n")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard normalized.count > 180, normalized.contains("\n") == false else {
            return normalized
        }

        return normalized
            .replacingOccurrences(
                of: #"(?i)^((?:hi|hello|hey|dear)\b[^,]{0,80},)\s+"#,
                with: "$1\n\n",
                options: .regularExpression
            )
            .replacingOccurrences(
                of: #"(?i)\s+(regards,|best,|thanks,|thank you,)\s+"#,
                with: "\n\n$1\n",
                options: .regularExpression
            )
            .replacingOccurrences(
                of: #"(?i)\s+(P\.S\.)\s+"#,
                with: "\n\n$1 ",
                options: .regularExpression
            )
    }

    private static func looksLikeHTML(_ value: String) -> Bool {
        contains(pattern: #"<\s*(html|body|div|p|br|table|tr|td|span|a|img|ul|ol|li|h[1-6])\b"#, in: value)
    }

    private static func isTrackingImage(attrs: [String: String], style: String) -> Bool {
        let sizes = imageSizes(attrs: attrs, style: style)
        if !sizes.isEmpty, sizes.allSatisfy({ $0 <= 2 }) {
            return true
        }
        guard let src = attrs["src"], !src.isEmpty else {
            return false
        }
        return contains(pattern: #"(/wf/open|[?&]open=|/open[?/]|/track|tracking|pixel|beacon|analytics)"#, in: src)
    }

    private static func imageSizes(attrs: [String: String], style: String) -> [Double] {
        var sizes = [
            numericCSSSize(attrs["width"]),
            numericCSSSize(attrs["height"]),
        ].compactMap { $0 }
        sizes.append(contentsOf: matches(pattern: #"\b(?:width|height)\s*:\s*([0-9.]+)\s*px"#, in: style).compactMap(numericCSSSize))
        return sizes
    }

    private static func isMeaningfulImageSize(_ sizes: [Double]) -> Bool {
        sizes.contains(where: { $0 >= 80 }) || sizes.filter { $0 >= 24 }.count >= 2
    }

    private static func htmlAttributes(in tag: String) -> [String: String] {
        guard let regex = try? NSRegularExpression(
            pattern: #"(?i)\b([a-z0-9_-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+))"#
        ) else {
            return [:]
        }

        var attrs: [String: String] = [:]
        let nsRange = NSRange(tag.startIndex..<tag.endIndex, in: tag)
        regex.enumerateMatches(in: tag, range: nsRange) { match, _, _ in
            guard let match else {
                return
            }
            let key = string(in: tag, range: match.range(at: 1)).lowercased()
            let value = (2..<match.numberOfRanges)
                .compactMap { index -> String? in
                    let range = match.range(at: index)
                    return range.location != NSNotFound ? string(in: tag, range: range) : nil
                }
                .first ?? ""
            attrs[key] = value
        }
        return attrs
    }

    private static func isHiddenImageStyle(_ style: String) -> Bool {
        let normalized = style.replacingOccurrences(of: " ", with: "").lowercased()
        return normalized.contains("display:none")
            || normalized.contains("visibility:hidden")
            || normalized.contains("opacity:0")
    }

    private static func numericCSSSize(_ value: String?) -> Double? {
        guard let value,
              let range = value.range(of: #"[0-9.]+"#, options: .regularExpression),
              let number = Double(value[range]) else {
            return nil
        }
        return number
    }

    private static func plainText(fromHTML value: String) -> String {
        let readable = value
            .replacingOccurrences(of: #"(?is)<!--.*?-->"#, with: " ", options: .regularExpression)
            .replacingOccurrences(of: #"(?is)<\s*(script|style)\b[^>]*>.*?</\s*\1\s*>"#, with: " ", options: .regularExpression)
            .replacingOccurrences(
                of: #"(?is)<([a-z0-9]+)\b(?=[^>]*\bstyle\s*=\s*['"][^'"]*(?:display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0|color\s*:\s*transparent|font-size\s*:\s*0))[^>]*>.*?</\s*\1\s*>"#,
                with: " ",
                options: .regularExpression
            )
        let withLineBreaks = readable
            .replacingOccurrences(of: #"(?i)<\s*br\s*/?\s*>"#, with: "\n", options: .regularExpression)
            .replacingOccurrences(of: #"(?i)</\s*(div|p|tr|table|li|h[1-6])\s*>"#, with: "\n\n", options: .regularExpression)
        let withoutTags = withLineBreaks.replacingOccurrences(
            of: #"<[^>]+>"#,
            with: " ",
            options: .regularExpression
        )
        return readableText(EmailReaderText.decodingHTML(withoutTags))
    }

    private static func readableText(_ value: String) -> String {
        let normalized = value
            .replacingOccurrences(of: "\r\n", with: "\n")
            .replacingOccurrences(of: "\r", with: "\n")
        let lines = normalized.components(separatedBy: "\n").map { line in
            line
                .components(separatedBy: .whitespaces)
                .filter { !$0.isEmpty }
                .joined(separator: " ")
        }

        var outputLines: [String] = []
        var pendingBlankLine = false

        for line in lines {
            if line.isEmpty {
                pendingBlankLine = !outputLines.isEmpty
                continue
            }

            if pendingBlankLine, outputLines.last?.isEmpty == false {
                outputLines.append("")
            }
            outputLines.append(line)
            pendingBlankLine = false
        }

        return outputLines
            .joined(separator: "\n")
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private static func contains(pattern: String, in value: String) -> Bool {
        value.range(of: pattern, options: [.regularExpression, .caseInsensitive]) != nil
    }

    private static func count(pattern: String, in value: String) -> Int {
        guard let regex = try? NSRegularExpression(pattern: pattern, options: [.caseInsensitive, .dotMatchesLineSeparators]) else {
            return 0
        }
        return regex.numberOfMatches(in: value, range: NSRange(value.startIndex..<value.endIndex, in: value))
    }

    private static func matches(pattern: String, in value: String) -> [String] {
        guard let regex = try? NSRegularExpression(pattern: pattern, options: [.caseInsensitive, .dotMatchesLineSeparators]) else {
            return []
        }
        return regex.matches(in: value, range: NSRange(value.startIndex..<value.endIndex, in: value)).compactMap { match in
            string(in: value, range: match.numberOfRanges > 1 && match.range(at: 1).location != NSNotFound ? match.range(at: 1) : match.range)
        }
    }

    private static func string(in value: String, range: NSRange) -> String {
        guard let swiftRange = Range(range, in: value) else {
            return ""
        }
        return String(value[swiftRange])
    }

    private static func nonEmpty(_ value: String?) -> String? {
        let trimmed = value?.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed?.isEmpty == false ? trimmed : nil
    }

    private static func logClassification(
        threadID: String?,
        messageID: String?,
        mode: String,
        visibleTextLength: Int,
        imageCount: Int,
        tableCount: Int,
        fallbackReason: String?
    ) {
        #if DEBUG
        print(
            "[EmailBodyRender] threadID=\(threadID ?? "unknown") messageID=\(messageID ?? "unknown") mode=\(mode) visibleTextLength=\(visibleTextLength) imageCount=\(imageCount) tableCount=\(tableCount) fallbackReason=\(fallbackReason ?? "none")"
        )
        #endif
    }

    private struct HTMLBodyAnalysis {
        let sourceLength: Int
        let plainText: String
        let visibleTextLength: Int
        let imageCount: Int
        let substantiveImageCount: Int
        let trackingImageCount: Int
        let tableCount: Int
        let tableTagCount: Int
        let layoutTagCount: Int
        let styleCount: Int
        let classCount: Int
        let hasPictureElement: Bool

        var isRich: Bool {
            if hasPictureElement || substantiveImageCount > 0 {
                return true
            }

            let sourceIsDocumentSized = sourceLength > max(700, visibleTextLength * 2)
            if tableCount >= 2 && tableTagCount >= 4 && sourceIsDocumentSized {
                return true
            }
            if tableCount >= 2 && tableTagCount >= 2 && (styleCount >= 1 || classCount >= 1) && sourceLength > max(500, visibleTextLength * 2) {
                return true
            }
            if layoutTagCount >= 2 && (styleCount >= 1 || classCount >= 1) && sourceIsDocumentSized {
                return true
            }
            return false
        }
    }

}

private struct EmailTextBodyView: View {
    let bodyText: String
    let colorScheme: ColorScheme
    var minHeight: CGFloat = 0
    let renderRevision: UInt64
    @State private var attributedBodyText: AttributedString?

    init(
        bodyText: String,
        colorScheme: ColorScheme,
        minHeight: CGFloat = 0,
        renderRevision: UInt64
    ) {
        self.bodyText = bodyText
        self.colorScheme = colorScheme
        self.minHeight = minHeight
        self.renderRevision = renderRevision
    }

    var body: some View {
        Group {
            if let attributedBodyText {
                EmailPreparedTextBodyView(
                    attributedText: attributedBodyText,
                    colorScheme: colorScheme,
                    minHeight: minHeight
                )
            } else {
                HStack(spacing: 10) {
                    ProgressView().controlSize(.small)
                    Text("Preparing message…")
                        .font(EmailReaderTypography.metadata())
                        .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                }
                .frame(maxWidth: .infinity, minHeight: max(80, minHeight), alignment: .center)
            }
        }
        .task(id: EmailTextPreparationID(renderRevision: renderRevision, usesDarkMode: colorScheme == .dark)) {
            attributedBodyText = nil
            let source = bodyText
            let usesDarkMode = colorScheme == .dark
            let work = Task.detached(priority: .userInitiated) { () -> AttributedString? in
                guard !Task.isCancelled else { return nil }
                let decoded = EmailReaderText.decodingHTML(
                    source.trimmingCharacters(in: .whitespacesAndNewlines)
                )
                guard !Task.isCancelled else { return nil }
                return EmailReaderText.attributedPlainText(
                    decoded,
                    colorScheme: usesDarkMode ? .dark : .light
                )
            }
            let prepared = await withTaskCancellationHandler {
                await work.value
            } onCancel: {
                work.cancel()
            }
            guard let prepared, !Task.isCancelled else { return }
            attributedBodyText = prepared
        }
    }
}

private struct EmailPreparedTextBodyView: View {
    let attributedText: AttributedString
    let colorScheme: ColorScheme
    var minHeight: CGFloat = 0

    var body: some View {
        Text(attributedText)
            .lineSpacing(5)
            .fixedSize(horizontal: false, vertical: true)
            .textSelection(.enabled)
            .frame(maxWidth: .infinity, minHeight: minHeight, alignment: .topLeading)
    }
}

private struct EmailTextPreparationID: Hashable {
    let renderRevision: UInt64
    let usesDarkMode: Bool
}

private struct EmailReaderMarkerRow: View {
    let markers: [ThreadMessageReaderMarker]
    let colorScheme: ColorScheme

    var body: some View {
        HStack(spacing: 8) {
            ForEach(markers) { marker in
                Text(marker.label)
                    .font(EmailReaderTypography.marker())
                    .foregroundStyle(marker.kind == "external_warning" ? ElectronicMailDesign.appleBlue : ElectronicMailDesign.secondaryText(for: colorScheme))
                    .padding(.horizontal, 9)
                    .padding(.vertical, 4)
                    .background(
                        Capsule()
                            .fill(ElectronicMailDesign.controlFill(for: colorScheme, selected: marker.kind == "external_warning"))
                    )
                    .overlay {
                        Capsule()
                            .stroke(ElectronicMailDesign.panelBorder(for: colorScheme), lineWidth: 1)
                    }
                    .help(marker.text)
            }
        }
    }
}

private struct EmailReaderDetailStack: View {
    let message: ThreadMessage?
    let colorScheme: ColorScheme

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let signatureText = nonEmpty(message?.reader?.signatureText) {
                EmailReaderDetailDisclosure(
                    title: "Signature",
                    bodyText: signatureText,
                    colorScheme: colorScheme,
                    renderRevision: message?.renderRevision ?? 0
                )
            }
            if let quotedText = nonEmpty(message?.reader?.quotedText) {
                EmailReaderDetailDisclosure(
                    title: "Quoted text",
                    bodyText: quotedText,
                    colorScheme: colorScheme,
                    renderRevision: message?.renderRevision ?? 0
                )
            }
            if let footerText = nonEmpty(message?.reader?.footerText) {
                EmailReaderDetailDisclosure(
                    title: "Footer",
                    bodyText: footerText,
                    colorScheme: colorScheme,
                    renderRevision: message?.renderRevision ?? 0
                )
            }
        }
    }

    private func nonEmpty(_ value: String?) -> String? {
        let trimmed = value?.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed?.isEmpty == false ? trimmed : nil
    }
}

private struct EmailReaderDetailDisclosure: View {
    let title: String
    let bodyText: String
    let colorScheme: ColorScheme
    let renderRevision: UInt64

    @State private var expanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button {
                withAnimation(.easeInOut(duration: 0.16)) {
                    expanded.toggle()
                }
            } label: {
                HStack(spacing: 7) {
                    Image(systemName: expanded ? "chevron.down" : "chevron.right")
                        .font(.system(size: 11, weight: .semibold))
                    Text(title)
                        .font(EmailReaderTypography.metadata(weight: .medium))
                }
                .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
            }
            .buttonStyle(.plain)

            if expanded {
                EmailTextBodyView(
                    bodyText: bodyText,
                    colorScheme: colorScheme,
                    renderRevision: renderRevision
                )
                    .padding(12)
                    .background(
                        RoundedRectangle(cornerRadius: EmailReaderMetrics.cardRadius, style: .continuous)
                            .fill(ElectronicMailDesign.panelFill(for: colorScheme))
                    )
                    .transition(.opacity.combined(with: .move(edge: .top)))
            }
        }
    }
}

private struct EmailOriginalBodyView: View {
    let preparedDocument: EmailHTMLPreparedDocument
    let fallbackText: String
    let threadID: String
    let messageID: String?
    let renderRevision: UInt64

    var body: some View {
        EmailHTMLBodyView(
            preparedDocument: preparedDocument,
            fallbackText: fallbackText,
            threadID: threadID,
            messageID: messageID,
            colorScheme: .light,
            renderRevision: renderRevision
        )
        .id(renderRevision)
        .background(Color.white)
        .padding(16)
    }
}

private struct EmailHTMLBodyView: View {
    let preparedDocument: EmailHTMLPreparedDocument
    let fallbackText: String
    let threadID: String
    let messageID: String?
    let colorScheme: ColorScheme
    let renderRevision: UInt64

    @State private var contentHeight: CGFloat = EmailReaderMetrics.htmlBodyMinHeight
    @State private var runtimeFallbackReason: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Group {
                if runtimeFallbackReason != nil {
                    EmailTextBodyView(
                        bodyText: fallbackText,
                        colorScheme: colorScheme,
                        minHeight: 120,
                        renderRevision: renderRevision
                    )
                } else {
                    EmailHTMLWebView(
                        html: preparedDocument.remoteImagesDocument,
                        contentHeight: $contentHeight
                    ) { result in
                        if result.fallbackReason != nil {
                            logRuntimeFallback(result)
                            runtimeFallbackReason = result.fallbackReason
                        }
                    }
                    .frame(maxWidth: .infinity)
                    .frame(height: max(EmailReaderMetrics.htmlBodyMinHeight, min(contentHeight, EmailReaderMetrics.htmlBodyMaxHeight)))
                }
            }
        }
    }

    private func logRuntimeFallback(_ result: EmailHTMLRenderResult) {
        #if DEBUG
        print(
            "[EmailBodyRender] threadID=\(threadID) messageID=\(messageID ?? "unknown") mode=text visibleTextLength=\(result.visibleTextLength) imageCount=\(result.imageCount) tableCount=\(result.tableCount) fallbackReason=\(result.fallbackReason ?? "runtime-html-fallback")"
        )
        #endif
    }
}

private struct EmailHTMLPreparedDocument: @unchecked Sendable {
    let remoteImagesDocument: String
}

enum EmailRemoteImagePolicy {
    static func renderDocument(from html: String) -> String {
        let policy = "default-src 'none'; img-src electronicmail-image: data: cid:; style-src 'unsafe-inline'; font-src data:; media-src data:; frame-src 'none'; script-src 'none'"
        let meta = #"<meta http-equiv="Content-Security-Policy" content="\#(policy)">"#
        if let headRange = html.range(of: #"(?i)<head(?:\s[^>]*)?>"#, options: .regularExpression) {
            var result = html
            result.insert(contentsOf: meta, at: headRange.upperBound)
            return result
        }
        return "<html><head>\(meta)</head><body>\(html)</body></html>"
    }
}

private struct EmailHTMLWebView: NSViewRepresentable {
    let html: String
    @Binding var contentHeight: CGFloat
    let onRenderResult: (EmailHTMLRenderResult) -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(contentHeight: $contentHeight, onRenderResult: onRenderResult)
    }

    func makeNSView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        EmailHTMLWebViewPolicy.configure(configuration)
        configuration.userContentController.add(
            context.coordinator,
            name: Coordinator.contentSizeMessageName
        )
        configuration.userContentController.addUserScript(Coordinator.contentSizeUserScript)

        let webView = EmailScrollPassthroughWebView(frame: .zero, configuration: configuration)
        webView.appearance = NSAppearance(named: .aqua)
        webView.navigationDelegate = context.coordinator
        webView.setValue(false, forKey: "drawsBackground")
        return webView
    }

    func updateNSView(_ webView: WKWebView, context: Context) {
        context.coordinator.onRenderResult = onRenderResult
        guard context.coordinator.currentHTML != html else {
            return
        }
        context.coordinator.currentHTML = html
        webView.loadHTMLString(html, baseURL: nil)
    }

    final class Coordinator: NSObject, WKNavigationDelegate, WKScriptMessageHandler {
        static let contentSizeMessageName = "emailContentSizeDidChange"
        static var contentSizeUserScript: WKUserScript {
            WKUserScript(
                source: """
                (() => {
                  var notificationScheduled = false;
                  const notify = () => {
                    if (notificationScheduled) return;
                    notificationScheduled = true;
                    requestAnimationFrame(() => {
                      notificationScheduled = false;
                      window.webkit.messageHandlers.\(contentSizeMessageName).postMessage(null);
                    });
                  };
                  if (window.ResizeObserver) {
                    new ResizeObserver(notify).observe(document.documentElement);
                  }
                  Array.from(document.images || []).forEach((image) => {
                    image.addEventListener('load', notify, { once: true });
                    image.addEventListener('error', notify, { once: true });
                  });
                  notify();
                })();
                """,
                injectionTime: .atDocumentEnd,
                forMainFrameOnly: true
            )
        }

        var currentHTML: String?
        var onRenderResult: (EmailHTMLRenderResult) -> Void
        private var contentHeight: Binding<CGFloat>

        init(contentHeight: Binding<CGFloat>, onRenderResult: @escaping (EmailHTMLRenderResult) -> Void) {
            self.contentHeight = contentHeight
            self.onRenderResult = onRenderResult
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            updateRenderResult(from: webView)
        }

        func userContentController(
            _ userContentController: WKUserContentController,
            didReceive message: WKScriptMessage
        ) {
            guard message.name == Self.contentSizeMessageName,
                  let webView = message.webView else {
                return
            }
            updateContentHeight(from: webView)
        }

        func webView(
            _ webView: WKWebView,
            decidePolicyFor navigationAction: WKNavigationAction,
            decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
        ) {
            if navigationAction.navigationType == .linkActivated, let url = navigationAction.request.url {
                if EmailExternalLinkPolicy.canOpen(url) {
                    NSWorkspace.shared.open(url)
                }
                decisionHandler(.cancel)
                return
            }
            decisionHandler(.allow)
        }

        private func updateRenderResult(from webView: WKWebView) {
            let script = """
            (() => {
              const body = document.body;
              const doc = document.documentElement;
              const text = (body ? body.innerText : '').replace(/\\s+/g, ' ').trim();
              const images = Array.from(document.images || []);
              const brokenImages = images.filter((image) => {
                const naturalWidth = image.naturalWidth || 0;
                const naturalHeight = image.naturalHeight || 0;
                return image.complete && (naturalWidth <= 1 || naturalHeight <= 1);
              });
              const emptyImages = images.filter((image) => {
                const rect = image.getBoundingClientRect();
                return rect.width <= 2 || rect.height <= 2;
              });
              [...new Set([...brokenImages, ...emptyImages])].forEach((image) => {
                image.style.display = 'none';
              });
              const height = Math.max(
                body ? body.scrollHeight : 0,
                body ? body.offsetHeight : 0,
                doc ? doc.scrollHeight : 0,
                doc ? doc.offsetHeight : 0
              );
              return {
                visibleTextLength: text.length,
                imageCount: images.length,
                brokenImageCount: brokenImages.length,
                emptyImageCount: emptyImages.length,
                tableCount: document.getElementsByTagName('table').length,
                height: height
              };
            })()
            """
            webView.evaluateJavaScript(script) { [weak self] result, _ in
                guard let self else {
                    return
                }

                guard let values = result as? [String: Any] else {
                    return
                }
                let renderResult = EmailHTMLRenderResult(values: values)
                if let nextHeight = renderResult.height, nextHeight.isFinite, nextHeight > 0 {
                    self.applyContentHeight(nextHeight)
                }
                self.onRenderResult(renderResult)
            }
        }

        private func updateContentHeight(from webView: WKWebView) {
            let script = """
            (() => {
              const body = document.body;
              const doc = document.documentElement;
              return Math.max(
                body ? body.scrollHeight : 0,
                body ? body.offsetHeight : 0,
                doc ? doc.scrollHeight : 0,
                doc ? doc.offsetHeight : 0
              );
            })()
            """
            webView.evaluateJavaScript(script) { [weak self] result, _ in
                guard let self,
                      let number = result as? NSNumber else {
                    return
                }
                let nextHeight = CGFloat(truncating: number)
                guard nextHeight.isFinite, nextHeight > 0 else {
                    return
                }
                self.applyContentHeight(nextHeight)
            }
        }

        private func applyContentHeight(_ nextHeight: CGFloat) {
            let currentHeight = contentHeight.wrappedValue
            guard abs(nextHeight - currentHeight) > 1 else {
                return
            }
            var transaction = Transaction(animation: nil)
            transaction.disablesAnimations = true
            withTransaction(transaction) {
                contentHeight.wrappedValue = nextHeight
            }
        }
    }
}

enum EmailHTMLWebViewPolicy {
    static func configure(_ configuration: WKWebViewConfiguration) {
        configuration.websiteDataStore = .nonPersistent()
        configuration.defaultWebpagePreferences.allowsContentJavaScript = false
        configuration.setURLSchemeHandler(
            EmailRemoteImageSchemeHandler(),
            forURLScheme: EmailRemoteImageSchemeHandler.scheme
        )
    }
}

enum EmailExternalLinkPolicy {
    private static let allowedSchemes: Set<String> = ["https", "http", "mailto", "tel"]

    static func canOpen(_ url: URL) -> Bool {
        guard let scheme = url.scheme?.lowercased(), allowedSchemes.contains(scheme) else {
            return false
        }
        if scheme == "http" || scheme == "https" {
            return url.host?.isEmpty == false
        }
        return true
    }
}

private struct EmailHTMLRenderResult {
    let visibleTextLength: Int
    let imageCount: Int
    let brokenImageCount: Int
    let emptyImageCount: Int
    let tableCount: Int
    let height: CGFloat?

    init(values: [String: Any]) {
        visibleTextLength = Self.intValue(values["visibleTextLength"])
        imageCount = Self.intValue(values["imageCount"])
        brokenImageCount = Self.intValue(values["brokenImageCount"])
        emptyImageCount = Self.intValue(values["emptyImageCount"])
        tableCount = Self.intValue(values["tableCount"])
        height = Self.cgFloatValue(values["height"])
    }

    var fallbackReason: String? {
        if visibleTextLength < 12, imageCount == 0 {
            return "runtime-blank-html"
        }
        if visibleTextLength < 40, imageCount > 0, brokenImageCount + emptyImageCount >= imageCount {
            return "runtime-broken-images"
        }
        return nil
    }

    private static func intValue(_ value: Any?) -> Int {
        if let number = value as? NSNumber {
            return number.intValue
        }
        if let int = value as? Int {
            return int
        }
        if let double = value as? Double {
            return Int(double)
        }
        return 0
    }

    private static func cgFloatValue(_ value: Any?) -> CGFloat? {
        if let number = value as? NSNumber {
            return CGFloat(truncating: number)
        }
        if let double = value as? Double {
            return CGFloat(double)
        }
        return nil
    }
}

private final class EmailScrollPassthroughWebView: WKWebView {
    private weak var parentScrollView: NSScrollView?

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        parentScrollView = enclosingScrollView()
    }

    override func scrollWheel(with event: NSEvent) {
        guard let scrollView = parentScrollView ?? enclosingScrollView() else {
            super.scrollWheel(with: event)
            return
        }
        parentScrollView = scrollView
        scrollView.scrollWheel(with: event)
    }

    private func enclosingScrollView() -> NSScrollView? {
        var candidate = superview
        while let current = candidate {
            if let scrollView = current as? NSScrollView {
                return scrollView
            }
            candidate = current.superview
        }
        return nil
    }
}

struct EmailThreadPresentationItem: Identifiable, Equatable {
    let id: EmailThreadPresentationItemID
    let message: ThreadMessage
}

struct EmailThreadPresentationItemID: Hashable {
    let messageID: String
    let duplicateDiscriminator: EmailThreadPresentationDuplicateDiscriminator?

    static func unique(messageID: String) -> EmailThreadPresentationItemID {
        EmailThreadPresentationItemID(messageID: messageID, duplicateDiscriminator: nil)
    }
}

struct EmailThreadPresentationDuplicateDiscriminator: Hashable {
    let renderRevision: UInt64
    let receivedAt: String
    let occurrence: Int
}

struct EmailThreadPresentationSnapshot: Equatable {
    let items: [EmailThreadPresentationItem]
    let latestMessageKey: EmailThreadPresentationItem.ID?
}

enum EmailThreadPresentation {
    static func snapshot(from messages: [ThreadMessage]) -> EmailThreadPresentationSnapshot {
        let items = items(from: orderedMessages(messages))
        return EmailThreadPresentationSnapshot(
            items: items,
            latestMessageKey: latestMessageKey(in: items)
        )
    }

    static func orderedMessages(_ messages: [ThreadMessage]) -> [ThreadMessage] {
        orderedMessages(messages, dateParser: EmailReaderText.date(from:))
    }

    static func orderedMessages(
        _ messages: [ThreadMessage],
        dateParser: (String) -> Date?
    ) -> [ThreadMessage] {
        messages.enumerated().map { offset, message in
            DatedMessage(
                offset: offset,
                message: message,
                receivedDate: dateParser(message.receivedAt)
            )
        }.sorted { lhs, rhs in
            switch (lhs.receivedDate, rhs.receivedDate) {
            case let (lhsDate?, rhsDate?) where lhsDate != rhsDate:
                return lhsDate < rhsDate
            case (nil, nil) where lhs.message.receivedAt != rhs.message.receivedAt:
                return lhs.message.receivedAt < rhs.message.receivedAt
            default:
                return lhs.offset < rhs.offset
            }
        }.map(\.message)
    }

    static func latestMessageID(in orderedMessages: [ThreadMessage]) -> String? {
        orderedMessages.last?.id
    }

    static func items(from orderedMessages: [ThreadMessage]) -> [EmailThreadPresentationItem] {
        let countsByMessageID = orderedMessages.reduce(into: [String: Int]()) { counts, message in
            counts[message.id, default: 0] += 1
        }
        var occurrencesByDuplicate = [EmailThreadPresentationDuplicateKey: Int]()

        return orderedMessages.map { message in
            let id: EmailThreadPresentationItemID
            if countsByMessageID[message.id] == 1 {
                id = .unique(messageID: message.id)
            } else {
                let duplicateKey = EmailThreadPresentationDuplicateKey(
                    messageID: message.id,
                    renderRevision: message.renderRevision,
                    receivedAt: message.receivedAt
                )
                let occurrence = occurrencesByDuplicate[duplicateKey, default: 0]
                occurrencesByDuplicate[duplicateKey] = occurrence + 1
                id = EmailThreadPresentationItemID(
                    messageID: message.id,
                    duplicateDiscriminator: EmailThreadPresentationDuplicateDiscriminator(
                        renderRevision: message.renderRevision,
                        receivedAt: message.receivedAt,
                        occurrence: occurrence
                    )
                )
            }
            return EmailThreadPresentationItem(id: id, message: message)
        }
    }

    static func latestMessageKey(in items: [EmailThreadPresentationItem]) -> EmailThreadPresentationItem.ID? {
        items.last?.id
    }

    static func isExpanded(
        messageKey: EmailThreadPresentationItem.ID,
        latestMessageKey: EmailThreadPresentationItem.ID?,
        userExpandedMessageKeys: Set<EmailThreadPresentationItem.ID>
    ) -> Bool {
        messageKey == latestMessageKey || userExpandedMessageKeys.contains(messageKey)
    }

    static func displaySubject(for message: ThreadMessage) -> String {
        let value = message.subject?.trimmingCharacters(in: .whitespacesAndNewlines)
        return EmailReaderText.decodingHTML(value?.isEmpty == false ? value! : "No subject")
    }
}

private struct DatedMessage {
    let offset: Int
    let message: ThreadMessage
    let receivedDate: Date?
}

private struct EmailThreadPresentationDuplicateKey: Hashable {
    let messageID: String
    let renderRevision: UInt64
    let receivedAt: String
}

private enum EmailHTMLDocument {
    static func renderableDocument(from html: String, colorScheme: ColorScheme) -> String {
        guard !Task.isCancelled else { return "" }
        let trimmed = html.trimmingCharacters(in: .whitespacesAndNewlines)
        let document = removingOuterMailCanvas(from: trimmed)
        guard !Task.isCancelled else { return "" }
        if document.range(of: #"<\s*(?:!doctype\s+html|html)\b"#, options: [.regularExpression, .caseInsensitive]) != nil {
            return injectingMailClientDefaults(
                into: document,
                colorScheme: colorScheme
            )
        }
        return """
        <!doctype html>
        <html>
        <head>
          <meta charset="utf-8">
          <meta name="viewport" content="width=device-width, initial-scale=1.0">
          <style>
            \(mailClientDefaults(for: colorScheme))
          </style>
        </head>
        <body>
          \(document)
        </body>
        </html>
        """
    }

    private static func mailClientDefaults(for colorScheme: ColorScheme) -> String {
        let textColor = colorScheme == .dark ? "#f5f5f7" : "#000000"
        let linkColor = colorScheme == .dark ? "#0a84ff" : "#0066cc"

        return """
    :root {
      color-scheme: \(colorScheme == .dark ? "dark" : "light");
      supported-color-schemes: light dark;
    }
    html, body {
      -webkit-text-size-adjust: 100%;
      background: transparent !important;
    }
    body {
      margin: 0;
      font-family: sans-serif;
      color: \(textColor);
    }
    a {
      color: \(linkColor);
    }
    """
    }

    private static func injectingMailClientDefaults(
        into document: String,
        colorScheme: ColorScheme
    ) -> String {
        let defaults = """
        <style>
        \(mailClientDefaults(for: colorScheme))
        </style>
        """

        if let headRange = document.range(of: #"<head\b[^>]*>"#, options: [.regularExpression, .caseInsensitive]) {
            var nextDocument = document
            nextDocument.insert(contentsOf: "\n\(defaults)\n", at: headRange.upperBound)
            return nextDocument
        }

        if let htmlRange = document.range(of: #"<html\b[^>]*>"#, options: [.regularExpression, .caseInsensitive]) {
            var nextDocument = document
            nextDocument.insert(contentsOf: "\n<head>\n\(defaults)\n</head>\n", at: htmlRange.upperBound)
            return nextDocument
        }

        return """
        <!doctype html>
        <html>
        <head>
          <meta charset="utf-8">
          \(defaults)
        </head>
        <body>
          \(document)
        </body>
        </html>
        """
    }

    private static func removingOuterMailCanvas(from document: String) -> String {
        guard let bodyOpenRange = document.range(of: #"<body\b[^>]*>"#, options: [.regularExpression, .caseInsensitive]),
              let bodyCloseRange = document.range(of: #"</body\s*>"#, options: [.regularExpression, .caseInsensitive, .backwards])
        else {
            return document
        }

        let bodyContent = String(document[bodyOpenRange.upperBound..<bodyCloseRange.lowerBound])
        guard bodyContent.range(
            of: #"<table\b[^>]*\bclass\s*=\s*['\"][^'\"]*\bbody\b"#,
            options: [.regularExpression, .caseInsensitive]
        ) != nil, let mainTableRange = firstBalancedTableRange(withClass: "main", in: bodyContent) else {
            return document
        }

        let maxWidth = containerMaxWidth(in: document) ?? "769px"
        let mainTable = String(bodyContent[mainTableRange])
        let unwrappedBody = """
        <div class="electronic-mail-unwrapped-main" style="max-width: \(maxWidth); width: 100%; margin: 0 auto;">
        \(mainTable)
        </div>
        """
        let unwrappedDocument = document[..<bodyOpenRange.upperBound] + "\n" + unwrappedBody + "\n" + document[bodyCloseRange.lowerBound...]
        return removingCanvasBackgroundDeclarations(from: String(unwrappedDocument))
    }

    private static func firstBalancedTableRange(withClass className: String, in html: String) -> Range<String.Index>? {
        guard let tableRegex = try? NSRegularExpression(pattern: #"(?is)</?\s*table\b[^>]*>"#) else {
            return nil
        }
        let matches = tableRegex.matches(in: html, range: NSRange(html.startIndex..<html.endIndex, in: html))
        var targetStart: String.Index?
        var depth = 0

        for match in matches {
            if Task.isCancelled {
                return nil
            }
            guard let tagRange = Range(match.range, in: html) else {
                continue
            }
            let tag = String(html[tagRange])
            let isClosingTag = tag.range(of: #"(?is)^</\s*table\b"#, options: .regularExpression) != nil

            if targetStart == nil {
                guard !isClosingTag, classAttribute(in: tag, contains: className) else {
                    continue
                }
                targetStart = tagRange.lowerBound
                depth = 1
                continue
            }

            depth += isClosingTag ? -1 : 1
            if depth == 0, let targetStart {
                return targetStart..<tagRange.upperBound
            }
        }

        return nil
    }

    private static func classAttribute(in tag: String, contains className: String) -> Bool {
        let patterns = [
            #"(?is)\bclass\s*=\s*"([^"]*)""#,
            #"(?is)\bclass\s*=\s*'([^']*)'"#,
        ]
        for pattern in patterns {
            guard let regex = try? NSRegularExpression(pattern: pattern),
                  let match = regex.firstMatch(in: tag, range: NSRange(tag.startIndex..<tag.endIndex, in: tag)),
                  match.numberOfRanges > 1,
                  let classRange = Range(match.range(at: 1), in: tag)
            else {
                continue
            }
            let classes = tag[classRange].split(whereSeparator: { $0.isWhitespace })
            if classes.contains(where: { $0.caseInsensitiveCompare(className) == .orderedSame }) {
                return true
            }
        }
        return false
    }

    private static func containerMaxWidth(in document: String) -> String? {
        let patterns = [
            #"(?is)\.container\s*\{[^}]*\bmax-width\s*:\s*([^;]+)"#,
            #"(?is)\.container\s*\{[^}]*\bwidth\s*:\s*([^;]+)"#,
        ]
        for pattern in patterns {
            guard let regex = try? NSRegularExpression(pattern: pattern),
                  let match = regex.firstMatch(in: document, range: NSRange(document.startIndex..<document.endIndex, in: document)),
                  match.numberOfRanges > 1,
                  let valueRange = Range(match.range(at: 1), in: document)
            else {
                continue
            }
            let value = document[valueRange].trimmingCharacters(in: .whitespacesAndNewlines)
            if !value.isEmpty {
                return value
            }
        }
        return nil
    }

    private static func removingCanvasBackgroundDeclarations(from document: String) -> String {
        document.replacingOccurrences(
            of: #"(?i)\s*background(?:-color)?\s*:\s*#f6f6f6\s*;?"#,
            with: "",
            options: .regularExpression
        )
    }
}

private struct EmailMessageCard: View {
    let threadID: String
    let message: ThreadMessage
    let expanded: Bool
    let currentUserDisplayName: String?
    let currentUserEmail: String?
    let colorScheme: ColorScheme
    let mailboxLabel: MailboxLabel
    let onRespond: (MailComposerMode, String) -> Void
    let onThreadAction: (GmailThreadAction, String?) -> Void
    let onOpenAttachment: (ThreadAttachment, String) -> Void
    let isAttachmentDownloading: (ThreadAttachment, String) -> Bool
    let onToggle: () -> Void

    @State private var detailsExpanded = false

    @ViewBuilder
    var body: some View {
        if expanded {
            expandedMessage
        } else {
            collapsedMessageCard
        }
    }

    private var messageHeader: some View {
        EmailMessageHeader(
            message: message,
            currentUserDisplayName: currentUserDisplayName,
            currentUserEmail: currentUserEmail,
            colorScheme: colorScheme,
            mailboxLabel: mailboxLabel,
            detailsExpanded: $detailsExpanded,
            onRespond: onRespond,
            onThreadAction: onThreadAction
        )
    }

    private var collapsedMessageCard: some View {
        Button(action: onToggle) {
            HStack(alignment: .firstTextBaseline, spacing: 24) {
                Text(sender)
                    .font(EmailReaderTypography.messageTitle())
                    .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                    .lineLimit(1)
                    .frame(maxWidth: 220, alignment: .leading)

                Text(subject)
                    .font(EmailReaderTypography.body())
                    .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                    .lineLimit(1)

                Spacer(minLength: 24)

                Text(EmailReaderText.shortDate(message.receivedAt))
                    .font(EmailReaderTypography.metadata())
                    .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                    .lineLimit(1)
            }
            .padding(.horizontal, 22)
            .frame(maxWidth: .infinity, minHeight: 60, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: EmailReaderMetrics.cardRadius, style: .continuous)
                    .fill(ElectronicMailDesign.panelFill(for: colorScheme))
            )
            .overlay {
                RoundedRectangle(cornerRadius: EmailReaderMetrics.cardRadius, style: .continuous)
                    .stroke(ElectronicMailDesign.panelBorder(for: colorScheme), lineWidth: 1)
            }
            .contentShape(RoundedRectangle(cornerRadius: EmailReaderMetrics.cardRadius, style: .continuous))
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Email from \(sender), \(subject), \(EmailReaderText.shortDate(message.receivedAt))")
        .accessibilityHint("Expand email")
    }

    private var expandedMessage: some View {
        VStack(alignment: .leading, spacing: 18) {
            messageHeader

            if detailsExpanded {
                EmailMessageDetailsStrip(message: message, colorScheme: colorScheme)
                    .transition(.opacity.combined(with: .move(edge: .top)))
            }

            Rectangle()
                .fill(ElectronicMailDesign.divider(for: colorScheme))
                .frame(height: 1)
                .padding(.top, 2)

            EmailBodyContent(
                threadID: threadID,
                message: message,
                fallbackText: message.body.isEmpty ? EmailReaderText.loadingFullEmail : message.body,
                colorScheme: colorScheme
            )
            .equatable()

            if !message.attachments.isEmpty {
                EmailAttachmentsView(
                    attachments: message.attachments,
                    colorScheme: colorScheme,
                    isDownloading: { attachment in
                        isAttachmentDownloading(attachment, message.id)
                    },
                    onOpen: { attachment in
                        onOpenAttachment(attachment, message.id)
                    }
                )
            }
        }
        .padding(.horizontal, 22)
        .padding(.vertical, 20)
        .frame(maxWidth: .infinity, alignment: .topLeading)
        .background(
            RoundedRectangle(cornerRadius: EmailReaderMetrics.cardRadius, style: .continuous)
                .fill(ElectronicMailDesign.panelFill(for: colorScheme))
        )
        .overlay {
            RoundedRectangle(cornerRadius: EmailReaderMetrics.cardRadius, style: .continuous)
                .stroke(ElectronicMailDesign.panelBorder(for: colorScheme), lineWidth: 1)
        }
    }

    private var sender: String {
        EmailReaderText.senderName(message.fromAddress) ?? "Unknown sender"
    }

    private var subject: String {
        EmailThreadPresentation.displaySubject(for: message)
    }

}

private struct EmailAttachmentsView: View {
    let attachments: [ThreadAttachment]
    let colorScheme: ColorScheme
    let isDownloading: (ThreadAttachment) -> Bool
    let onOpen: (ThreadAttachment) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(attachments) { attachment in
                let downloading = isDownloading(attachment)
                Button {
                    onOpen(attachment)
                } label: {
                    HStack(spacing: 10) {
                        Image(systemName: symbol(for: attachment))
                            .font(.system(size: 15, weight: .semibold))
                            .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                            .frame(width: 20, height: 20)

                        VStack(alignment: .leading, spacing: 2) {
                            Text(attachment.filename)
                                .font(EmailReaderTypography.metadata(weight: .semibold))
                                .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                                .lineLimit(1)

                            if let sizeText = sizeText(for: attachment.size) {
                                Text(sizeText)
                                    .font(EmailReaderTypography.metadata())
                                    .foregroundStyle(ElectronicMailDesign.tertiaryText(for: colorScheme))
                                    .lineLimit(1)
                            }
                        }

                        Spacer(minLength: 12)

                        if downloading {
                            ProgressView()
                                .controlSize(.small)
                                .accessibilityLabel("Downloading \(attachment.filename)")
                        } else {
                            Image(systemName: "arrow.down.circle")
                                .font(.system(size: 14, weight: .medium))
                                .foregroundStyle(ElectronicMailDesign.tertiaryText(for: colorScheme))
                        }
                    }
                    .padding(.horizontal, 12)
                    .padding(.vertical, 10)
                    .frame(maxWidth: 360, alignment: .leading)
                    .background(
                        RoundedRectangle(cornerRadius: 8, style: .continuous)
                            .fill(ElectronicMailDesign.controlFill(for: colorScheme))
                    )
                    .overlay {
                        RoundedRectangle(cornerRadius: 8, style: .continuous)
                            .stroke(ElectronicMailDesign.panelBorder(for: colorScheme), lineWidth: 1)
                    }
                }
                .buttonStyle(.plain)
                .disabled(downloading)
                .help("Open \(attachment.filename)")
            }
        }
        .padding(.top, 2)
    }

    private func symbol(for attachment: ThreadAttachment) -> String {
        let filename = attachment.filename.lowercased()
        let mimeType = attachment.mimeType?.lowercased() ?? ""
        if mimeType.contains("pdf") || filename.hasSuffix(".pdf") {
            return "doc.richtext"
        }
        if mimeType.hasPrefix("image/") {
            return "photo"
        }
        return "paperclip"
    }

    private func sizeText(for size: Int?) -> String? {
        guard let size, size > 0 else {
            return nil
        }
        return ByteCountFormatter.string(fromByteCount: Int64(size), countStyle: .file)
    }
}

private struct EmailMessageHeader: View {
    let message: ThreadMessage
    let currentUserDisplayName: String?
    let currentUserEmail: String?
    let colorScheme: ColorScheme
    let mailboxLabel: MailboxLabel
    @Binding var detailsExpanded: Bool
    let onRespond: (MailComposerMode, String) -> Void
    let onThreadAction: (GmailThreadAction, String?) -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            EmailSenderAvatar(name: senderName, colorScheme: colorScheme)

            VStack(alignment: .leading, spacing: 5) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(senderName)
                        .font(EmailReaderTypography.messageTitle())
                        .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                        .lineLimit(1)
                        .layoutPriority(2)

                    if let senderEmail {
                        Text(senderEmail)
                            .font(EmailReaderTypography.metadata())
                            .foregroundStyle(ElectronicMailDesign.tertiaryText(for: colorScheme))
                            .lineLimit(1)
                            .truncationMode(.middle)
                    }
                }

                HStack(spacing: 5) {
                    Text(recipientSummary)
                        .font(EmailReaderTypography.metadata())
                        .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                        .lineLimit(1)

                    Button {
                        withAnimation(.easeInOut(duration: 0.16)) {
                            detailsExpanded.toggle()
                        }
                    } label: {
                        HStack(spacing: 3) {
                            Text("Details")
                            Image(systemName: detailsExpanded ? "chevron.up" : "chevron.down")
                                .font(.system(size: 9, weight: .semibold))
                        }
                        .font(EmailReaderTypography.metadata(weight: .medium))
                        .foregroundStyle(ElectronicMailDesign.appleBlue)
                    }
                    .buttonStyle(.plain)
                    .help(detailsExpanded ? "Hide message details" : "Show message details")
                    .accessibilityLabel(detailsExpanded ? "Hide message details" : "Show message details")
                }
            }

            Spacer(minLength: 16)

            HStack(alignment: .center, spacing: 16) {
                EmailActionRow(
                    colorScheme: colorScheme,
                    mailboxLabel: mailboxLabel,
                    isUnread: message.labelIDs.contains(where: { $0.uppercased() == "UNREAD" }),
                    isStarred: message.labelIDs.contains(where: { $0.uppercased() == "STARRED" }),
                    onRespond: { mode in onRespond(mode, message.id) },
                    onThreadAction: { action in onThreadAction(action, message.id) }
                )

                Text(EmailReaderText.readerDate(message.receivedAt))
                    .font(EmailReaderTypography.metadata())
                    .foregroundStyle(ElectronicMailDesign.tertiaryText(for: colorScheme))
                    .lineLimit(1)
                    .fixedSize(horizontal: true, vertical: false)
            }
        }
    }

    private var senderName: String {
        EmailReaderText.senderName(message.fromAddress) ?? "Unknown sender"
    }

    private var senderEmail: String? {
        EmailReaderText.emailAddress(message.fromAddress)
    }

    private var recipientSummary: String {
        let rawTo = message.to?.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let rawTo, !rawTo.isEmpty else {
            return "to me"
        }
        let display = EmailReaderText.recipientSummary(
            rawTo,
            currentUserDisplayName: currentUserDisplayName,
            currentUserEmail: currentUserEmail
        )
        return display.isEmpty ? "to me" : "to \(display)"
    }
}

private struct EmailSenderAvatar: View {
    let name: String
    let colorScheme: ColorScheme

    var body: some View {
        Circle()
            .fill(ElectronicMailDesign.appleBlue.opacity(colorScheme == .dark ? 0.35 : 0.72))
            .frame(width: 30, height: 30)
            .overlay {
                Text(initial)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(Color.white)
            }
            .accessibilityHidden(true)
    }

    private var initial: String {
        name.trimmingCharacters(in: .whitespacesAndNewlines).first.map { String($0).uppercased() } ?? "?"
    }
}

private struct EmailMessageDetailsStrip: View {
    private static let minimumLabelColumnWidth: CGFloat = 76

    let message: ThreadMessage
    let colorScheme: ColorScheme

    var body: some View {
        VStack(alignment: .leading, spacing: 13) {
            detailRow("From", value: message.fromAddress)
            detailRow("To", value: message.to)
            detailRow("Cc", value: message.cc)
            detailRow("Bcc", value: message.bcc)
            detailRow("Date", value: EmailReaderText.readerDate(message.receivedAt))
            detailRow("Subject", value: message.subject)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 7, style: .continuous)
                .fill(ElectronicMailDesign.panelFill(for: colorScheme).opacity(colorScheme == .dark ? 0.42 : 0.36))
        )
        .overlay {
            RoundedRectangle(cornerRadius: 7, style: .continuous)
                .stroke(ElectronicMailDesign.panelBorder(for: colorScheme).opacity(0.72), lineWidth: 0.75)
        }
    }

    @ViewBuilder
    private func detailRow(_ label: String, value: String?) -> some View {
        if let cleanValue = clean(value) {
            HStack(alignment: .firstTextBaseline, spacing: 14) {
                Text(label)
                    .font(EmailReaderTypography.metadata(weight: .semibold))
                    .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                    .lineLimit(1)
                    .fixedSize(horizontal: true, vertical: false)
                    .frame(minWidth: Self.minimumLabelColumnWidth, alignment: .leading)

                Text(cleanValue)
                    .font(EmailReaderTypography.metadata())
                    .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                    .textSelection(.enabled)
                    .lineLimit(2)
            }
        }
    }

    private func clean(_ value: String?) -> String? {
        let trimmed = value?.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed?.isEmpty == false ? trimmed : nil
    }
}

private struct EmailActionRow: View {
    let colorScheme: ColorScheme
    let mailboxLabel: MailboxLabel
    let isUnread: Bool
    let isStarred: Bool
    let onRespond: (MailComposerMode) -> Void
    let onThreadAction: (GmailThreadAction) -> Void

    @State private var confirmPermanentDelete = false

    private let actions: [EmailReaderAction] = [.reply, .replyAll, .forward, .archive, .trash]

    var body: some View {
        HStack(spacing: 11) {
            ForEach(actions) { action in
                Button {
                    perform(action)
                } label: {
                    Image(systemName: action.symbol)
                        .font(EmailReaderTypography.actionIcon())
                        .foregroundStyle(action.enabled ? ElectronicMailDesign.secondaryText(for: colorScheme) : ElectronicMailDesign.tertiaryText(for: colorScheme))
                        .frame(width: 23, height: 23)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .help(action.help)
                .accessibilityLabel(action.help)
                .disabled(!action.enabled)
                .opacity(action.enabled ? 1 : 0.45)
            }

            Menu {
                Button(isStarred ? "Unstar" : "Star") {
                    onThreadAction(isStarred ? .unstar : .star)
                }

                Button(isUnread ? "Mark as Read" : "Mark as Unread") {
                    onThreadAction(isUnread ? .markRead : .markUnread)
                }

                if mailboxLabel == .archive {
                    Button("Move Conversation to Inbox") { onThreadAction(.unarchive) }
                } else if mailboxLabel != .trash && mailboxLabel != .spam {
                    Button("Archive Conversation") { onThreadAction(.archive) }
                }

                if mailboxLabel == .trash {
                    Button("Restore from Trash") { onThreadAction(.restoreTrash) }
                    Divider()
                    Button("Delete Permanently", role: .destructive) {
                        confirmPermanentDelete = true
                    }
                } else {
                    Button("Move to Trash", role: .destructive) { onThreadAction(.moveTrash) }
                }

                Divider()
                if mailboxLabel == .spam {
                    Button("Not Spam") { onThreadAction(.notSpam) }
                } else if mailboxLabel != .trash {
                    Button("Mark as Spam") { onThreadAction(.markSpam) }
                }
            } label: {
                Image(systemName: "ellipsis")
                    .font(EmailReaderTypography.actionIcon())
                    .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                    .frame(width: 23, height: 23)
                    .contentShape(Rectangle())
            }
            .menuStyle(.borderlessButton)
            .menuIndicator(.hidden)
            .fixedSize()
            .help("More actions")
            .accessibilityLabel("More email actions")
        }
        .confirmationDialog(
            "Delete this email permanently?",
            isPresented: $confirmPermanentDelete,
            titleVisibility: .visible
        ) {
            Button("Delete Permanently", role: .destructive) {
                onThreadAction(.deleteForever)
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("This cannot be undone.")
        }
    }

    private func perform(_ action: EmailReaderAction) {
        switch action {
        case .reply:
            onRespond(.reply)
        case .replyAll:
            onRespond(.replyAll)
        case .forward:
            onRespond(.forward)
        case .archive:
            switch mailboxLabel {
            case .archive:
                onThreadAction(.unarchive)
            case .spam:
                onThreadAction(.notSpam)
            case .trash:
                onThreadAction(.restoreTrash)
            default:
                onThreadAction(.archive)
            }
        case .trash:
            if mailboxLabel == .trash {
                confirmPermanentDelete = true
            } else {
                onThreadAction(.moveTrash)
            }
        case .more:
            break
        }
    }
}

private enum EmailReaderAction: Identifiable {
    case reply
    case replyAll
    case forward
    case archive
    case trash
    case more

    var id: String {
        help
    }

    var symbol: String {
        switch self {
        case .reply:
            return "arrowshape.turn.up.left"
        case .replyAll:
            return "arrowshape.turn.up.left.2"
        case .forward:
            return "arrowshape.turn.up.right"
        case .archive:
            return "archivebox"
        case .trash:
            return "trash"
        case .more:
            return "ellipsis"
        }
    }

    var help: String {
        switch self {
        case .reply:
            return "Reply"
        case .replyAll:
            return "Reply all"
        case .forward:
            return "Forward"
        case .archive:
            return "Archive conversation or move conversation to Inbox"
        case .trash:
            return "Trash or Delete"
        case .more:
            return "More actions (coming soon)"
        }
    }

    var enabled: Bool {
        switch self {
        case .reply, .archive, .trash:
            return true
        case .replyAll, .forward:
            return true
        case .more:
            return false
        }
    }
}

private struct EmailReaderLoadingCard: View {
    let colorScheme: ColorScheme

    var body: some View {
        RoundedRectangle(cornerRadius: EmailReaderMetrics.cardRadius, style: .continuous)
            .fill(ElectronicMailDesign.panelFill(for: colorScheme))
            .overlay {
                RoundedRectangle(cornerRadius: EmailReaderMetrics.cardRadius, style: .continuous)
                    .stroke(ElectronicMailDesign.panelBorder(for: colorScheme), lineWidth: 1)
            }
            .frame(height: 152)
            .overlay(alignment: .topLeading) {
                Text(EmailReaderText.loadingFullEmail)
                    .font(EmailReaderTypography.body())
                    .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                    .padding(.top, 30)
                    .padding(.leading, 32)
            }
    }
}

private struct EmailReaderErrorView: View {
    let message: String
    let colorScheme: ColorScheme
    let onRetry: () -> Void

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 18) {
            Text(message)
                .font(EmailReaderTypography.body())
                .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))

            Button("Retry", action: onRetry)
                .buttonStyle(.plain)
                .font(EmailReaderTypography.body(weight: .semibold))
                .foregroundStyle(ElectronicMailDesign.appleBlue)

            Spacer(minLength: 0)
        }
        .padding(.horizontal, 28)
        .frame(height: 86)
        .background(
            RoundedRectangle(cornerRadius: EmailReaderMetrics.cardRadius, style: .continuous)
                .fill(ElectronicMailDesign.panelFill(for: colorScheme))
        )
        .overlay {
            RoundedRectangle(cornerRadius: EmailReaderMetrics.cardRadius, style: .continuous)
                .stroke(ElectronicMailDesign.panelBorder(for: colorScheme), lineWidth: 1)
        }
    }
}

private enum EmailReaderMetrics {
    static let minContentWidth: CGFloat = 620
    static let maxContentWidth: CGFloat = 840
    static let sideRunway: CGFloat = 160
    static let contentTop: CGFloat = ElectronicMailShellMetrics.navTop + 8
    static let cardRadius: CGFloat = 7
    static let htmlBodyMinHeight: CGFloat = 360
    static let htmlBodyMaxHeight: CGFloat = 6000
}

private enum EmailReaderTypography {
    static func title(weight: Font.Weight = .bold) -> Font {
        ElectronicMailType.headerTitle(weight: weight)
    }

    static func subtitle(weight: Font.Weight = .regular) -> Font {
        ElectronicMailType.detail(weight: weight)
    }

    static func section(weight: Font.Weight = .semibold) -> Font {
        ElectronicMailType.small(weight: weight)
    }

    static func body(weight: Font.Weight = .regular) -> Font {
        ElectronicMailType.body(weight: weight)
    }

    static func metadata(weight: Font.Weight = .regular) -> Font {
        ElectronicMailType.small(weight: weight)
    }

    static func messageTitle(weight: Font.Weight = .semibold) -> Font {
        ElectronicMailType.detail(weight: weight)
    }

    static func marker(weight: Font.Weight = .medium) -> Font {
        ElectronicMailType.status(weight: weight)
    }

    static func actionIcon(weight: Font.Weight = .regular) -> Font {
        .system(size: ElectronicMailType.smallSize, weight: weight, design: .rounded)
    }

}

private enum EmailReaderText {
    static let loadingFullEmail = "Loading full email..."

    static func senderName(_ rawValue: String?) -> String? {
        guard let rawValue else {
            return nil
        }
        let candidate = rawValue
            .split(separator: "<", maxSplits: 1)
            .first
            .map(String.init) ?? rawValue
        let cleaned = candidate
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: "\""))
        return cleaned.isEmpty ? nil : cleaned
    }

    static func emailAddress(_ rawValue: String?) -> String? {
        guard let rawValue else {
            return nil
        }
        if let match = rawValue.range(of: #"<([^>]+)>"#, options: .regularExpression) {
            let value = String(rawValue[match])
                .trimmingCharacters(in: CharacterSet(charactersIn: "<>"))
                .trimmingCharacters(in: .whitespacesAndNewlines)
            return value.isEmpty ? nil : value
        }
        let trimmed = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.contains("@") else {
            return nil
        }
        return trimmed.trimmingCharacters(in: CharacterSet(charactersIn: "\""))
    }

    static func recipientSummary(
        _ rawValue: String,
        currentUserDisplayName: String? = nil,
        currentUserEmail: String? = nil
    ) -> String {
        let addresses = splitAddressList(rawValue)
        guard let first = addresses.first else {
            return ""
        }
        let display = recipientDisplayName(
            first,
            currentUserDisplayName: currentUserDisplayName,
            currentUserEmail: currentUserEmail
        ) ?? emailAddress(first) ?? first
        if addresses.count > 1 {
            return "\(display) +\(addresses.count - 1)"
        }
        return display
    }

    private static func recipientDisplayName(
        _ rawValue: String,
        currentUserDisplayName: String?,
        currentUserEmail: String?
    ) -> String? {
        let rawEmail = emailAddress(rawValue)?.lowercased()
        let userEmail = currentUserEmail?
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased()
        if let rawEmail,
           let userEmail,
           rawEmail == userEmail,
           let name = cleanDisplayName(currentUserDisplayName) {
            return name
        }
        return parsedDisplayName(rawValue)
    }

    private static func parsedDisplayName(_ rawValue: String) -> String? {
        guard rawValue.contains("<") else {
            return nil
        }
        let candidate = rawValue
            .split(separator: "<", maxSplits: 1)
            .first
            .map(String.init)
        return cleanDisplayName(candidate)
    }

    private static func cleanDisplayName(_ rawValue: String?) -> String? {
        let cleaned = rawValue?
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: "\""))
        guard let cleaned, !cleaned.isEmpty, !cleaned.contains("@") else {
            return nil
        }
        return cleaned
    }

    private static func splitAddressList(_ rawValue: String) -> [String] {
        var addresses: [String] = []
        var current = ""
        var isQuoted = false
        var angleDepth = 0

        for character in rawValue {
            switch character {
            case "\"":
                isQuoted.toggle()
                current.append(character)
            case "<":
                angleDepth += 1
                current.append(character)
            case ">":
                angleDepth = max(0, angleDepth - 1)
                current.append(character)
            case "," where !isQuoted && angleDepth == 0:
                let value = current.trimmingCharacters(in: .whitespacesAndNewlines)
                if !value.isEmpty {
                    addresses.append(value)
                }
                current = ""
            default:
                current.append(character)
            }
        }

        let value = current.trimmingCharacters(in: .whitespacesAndNewlines)
        if !value.isEmpty {
            addresses.append(value)
        }
        return addresses
    }

    static func readerDate(_ value: String) -> String {
        guard let date = isoDateFormatter.date(from: value) else {
            return value
        }
        if Calendar.current.isDateInToday(date) {
            return "Today \(timeFormatter.string(from: date))"
        }
        return fullDateFormatter.string(from: date)
    }

    static func shortDate(_ value: String) -> String {
        guard let date = date(from: value) else {
            return value
        }
        if Calendar.current.isDateInToday(date) {
            return "Today"
        }
        return shortDateFormatter.string(from: date)
    }

    static func date(from value: String) -> Date? {
        isoDateFormatter.date(from: value)
    }

    static func compact(_ value: String, limit: Int) -> String {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.count > limit else {
            return trimmed
        }
        let end = trimmed.index(trimmed.startIndex, offsetBy: max(0, limit - 1))
        return String(trimmed[..<end]) + "..."
    }

    static func decodingHTML(_ value: String) -> String {
        HTMLCharacterEntityDecoder.decode(value)
    }

    static func attributedPlainText(_ value: String, colorScheme: ColorScheme) -> AttributedString {
        var attributed = attributedTextCollapsingLabeledLinks(value, colorScheme: colorScheme)
        applyDetectedRawLinks(to: &attributed)

        return attributed
    }

    private static func attributedTextCollapsingLabeledLinks(_ value: String, colorScheme: ColorScheme) -> AttributedString {
        guard let regex = try? NSRegularExpression(
            pattern: #"([^\n()]{1,160}?)\s*\((https?://[^)\s]+)\)"#,
            options: [.caseInsensitive]
        ) else {
            return AttributedString(value)
        }

        let nsRange = NSRange(value.startIndex..<value.endIndex, in: value)
        var cursor = value.startIndex
        var output = AttributedString()

        for match in regex.matches(in: value, range: nsRange) {
            guard match.numberOfRanges >= 3,
                  let fullRange = Range(match.range(at: 0), in: value),
                  let labelRange = Range(match.range(at: 1), in: value),
                  let urlRange = Range(match.range(at: 2), in: value),
                  let url = URL(string: String(value[urlRange]))
            else {
                continue
            }

            if fullRange.lowerBound > cursor {
                output.append(plainAttributedString(String(value[cursor..<fullRange.lowerBound]), colorScheme: colorScheme))
            }

            let rawLabel = String(value[labelRange])
            let leadingWhitespace = rawLabel.prefix { $0.isWhitespace }
            if !leadingWhitespace.isEmpty {
                output.append(plainAttributedString(String(leadingWhitespace), colorScheme: colorScheme))
            }

            let label = rawLabel.trimmingCharacters(in: .whitespacesAndNewlines)
            if label.isEmpty {
                output.append(plainAttributedString(String(value[fullRange]), colorScheme: colorScheme))
            } else {
                var linkedLabel = AttributedString(label)
                linkedLabel.font = EmailReaderTypography.body()
                linkedLabel.link = url
                linkedLabel.foregroundColor = ElectronicMailDesign.appleBlue
                output.append(linkedLabel)
            }

            cursor = fullRange.upperBound
        }

        if cursor < value.endIndex {
            output.append(plainAttributedString(String(value[cursor...]), colorScheme: colorScheme))
        }

        return output.characters.isEmpty ? plainAttributedString(value, colorScheme: colorScheme) : output
    }

    private static func plainAttributedString(_ value: String, colorScheme: ColorScheme) -> AttributedString {
        var attributed = AttributedString(value)
        attributed.font = EmailReaderTypography.body()
        attributed.foregroundColor = ElectronicMailDesign.primaryText(for: colorScheme)
        return attributed
    }

    private static func applyDetectedRawLinks(to attributed: inout AttributedString) {
        let value = String(attributed.characters)

        guard let detector = try? NSDataDetector(types: NSTextCheckingResult.CheckingType.link.rawValue) else {
            return
        }

        let range = NSRange(value.startIndex..<value.endIndex, in: value)
        for match in detector.matches(in: value, options: [], range: range) {
            guard let url = match.url,
                  let stringRange = Range(match.range, in: value),
                  let lowerBound = AttributedString.Index(stringRange.lowerBound, within: attributed),
                  let upperBound = AttributedString.Index(stringRange.upperBound, within: attributed)
            else {
                continue
            }

            attributed[lowerBound..<upperBound].link = url
            attributed[lowerBound..<upperBound].foregroundColor = ElectronicMailDesign.appleBlue
        }
    }

    private static let isoDateFormatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withColonSeparatorInTimeZone]
        return formatter
    }()

    private static let timeFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "h:mm a"
        return formatter
    }()

    private static let fullDateFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "MMM d h:mm a"
        return formatter
    }()

    private static let shortDateFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "MMM d"
        return formatter
    }()
}

private enum HTMLCharacterEntityDecoder {
    private static let namedEntities: [Substring: String] = [
        "amp": "&", "apos": "'", "gt": ">", "lt": "<", "quot": "\"",
        "nbsp": "\u{00A0}", "ensp": "\u{2002}", "emsp": "\u{2003}", "thinsp": "\u{2009}",
        "zwnj": "\u{200C}", "zwj": "\u{200D}", "lrm": "\u{200E}", "rlm": "\u{200F}",
        "ndash": "–", "mdash": "—", "hellip": "…", "bull": "•", "middot": "·",
        "lsquo": "‘", "rsquo": "’", "sbquo": "‚", "ldquo": "“", "rdquo": "”", "bdquo": "„",
        "lsaquo": "‹", "rsaquo": "›", "laquo": "«", "raquo": "»",
        "copy": "©", "reg": "®", "trade": "™", "sect": "§", "para": "¶",
        "cent": "¢", "pound": "£", "yen": "¥", "euro": "€", "curren": "¤",
        "deg": "°", "plusmn": "±", "times": "×", "divide": "÷", "micro": "µ",
        "frac14": "¼", "frac12": "½", "frac34": "¾", "shy": "\u{00AD}",
    ]

    static func decode(_ value: String) -> String {
        guard value.contains("&") else {
            return value
        }

        var output = String()
        output.reserveCapacity(value.utf8.count)
        var cursor = value.startIndex

        while cursor < value.endIndex,
              let ampersand = value[cursor...].firstIndex(of: "&") {
            if Task.isCancelled {
                return value
            }
            output.append(contentsOf: value[cursor..<ampersand])
            let tokenStart = value.index(after: ampersand)
            let tokenLimit = value.index(tokenStart, offsetBy: 32, limitedBy: value.endIndex) ?? value.endIndex
            guard let semicolon = value[tokenStart..<tokenLimit].firstIndex(of: ";") else {
                output.append("&")
                cursor = tokenStart
                continue
            }

            let token = value[tokenStart..<semicolon]
            if let replacement = replacement(for: token) {
                output.append(replacement)
            } else {
                output.append(contentsOf: value[ampersand...semicolon])
            }
            cursor = value.index(after: semicolon)
        }

        output.append(contentsOf: value[cursor...])
        return output
    }

    private static func replacement(for token: Substring) -> String? {
        if token.hasPrefix("#x") || token.hasPrefix("#X") {
            return scalar(from: token.dropFirst(2), radix: 16)
        }
        if token.hasPrefix("#") {
            return scalar(from: token.dropFirst(), radix: 10)
        }
        if let replacement = namedEntities[token] {
            return replacement
        }
        return html4NamedEntity(token)
    }

    private static func html4NamedEntity(_ token: Substring) -> String? {
        var utf8 = Array(token.utf8)
        utf8.append(0)
        return utf8.withUnsafeBufferPointer { buffer in
            guard let baseAddress = buffer.baseAddress,
                  let entity = htmlEntityLookup(baseAddress),
                  let scalar = UnicodeScalar(entity.pointee.value) else {
                return nil
            }
            return String(scalar)
        }
    }

    private static func scalar(from digits: Substring, radix: Int) -> String? {
        guard let value = UInt32(digits, radix: radix),
              let scalar = UnicodeScalar(value) else {
            return nil
        }
        return String(scalar)
    }
}

private extension Array where Element: Hashable {
    func uniquedPreservingOrder() -> [Element] {
        var seen = Set<Element>()
        return filter { seen.insert($0).inserted }
    }
}

#if DEBUG
#Preview("Single Email") {
    EmailReaderView(
        threadID: "demo-google-today",
        focusedMessageID: nil,
        thread: DemoAppFixtures.threads["demo-google-today"],
        row: nil,
        errorMessage: nil,
        currentUserDisplayName: "TestUser",
        currentUserEmail: "demo@example.test",
        colorScheme: .dark,
        mailboxLabel: .inbox,
        onRetry: {},
        onRespond: { _, _ in },
        onThreadAction: { _, _ in },
        onOpenAttachment: { _, _ in },
        isAttachmentDownloading: { _, _ in false }
    )
    .frame(width: 1440, height: 900)
}
#endif
