import AppKit
import Foundation
import SwiftUI
import WebKit

struct EmailReaderView: View {
    let threadID: String
    let focusedMessageID: String?
    let thread: ThreadReaderResponse?
    let row: InboxRowViewModel?
    let errorMessage: String?
    let colorScheme: ColorScheme
    let onRetry: () -> Void
    let onReply: () -> Void

    @State private var expandedMessageKeys: Set<String> = []

    var body: some View {
        GeometryReader { proxy in
            let contentWidth = min(
                EmailReaderMetrics.maxContentWidth,
                max(
                    EmailReaderMetrics.minContentWidth,
                    proxy.size.width - ElectronicMailShellMetrics.navLeading * 2 - 160
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
                                message: thread?.messages.first,
                                row: row,
                                errorMessage: errorMessage,
                                colorScheme: colorScheme,
                                onRetry: onRetry,
                                onReply: onReply
                            )
                        } else {
                            GroupedEmailContent(
                                threadID: threadID,
                                title: readerTitle,
                                summary: readerSummary,
                                messages: thread?.messages ?? [],
                                expectedMessageCount: resolvedMessageCount,
                                focusedMessageID: focusedMessageID,
                                errorMessage: errorMessage,
                                colorScheme: colorScheme,
                                expandedMessageKeys: $expandedMessageKeys,
                                onRetry: onRetry,
                                onReply: onReply,
                                onFocusedMessageKey: { messageKey in
                                    withAnimation(.easeInOut(duration: 0.16)) {
                                        scrollProxy.scrollTo(messageKey, anchor: .center)
                                    }
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
        .environment(\.font, .system(.body))
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
        let value = nonEmpty(thread?.summary) ?? nonEmpty(row?.summary)
        guard value != readerTitle else {
            return nil
        }
        return value
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
    let message: ThreadMessage?
    let row: InboxRowViewModel?
    let errorMessage: String?
    let colorScheme: ColorScheme
    let onRetry: () -> Void
    let onReply: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            EmailReaderTitleHeader(
                title: title,
                summary: summary,
                colorScheme: colorScheme
            )

            HStack(alignment: .bottom, spacing: 28) {
                VStack(alignment: .leading, spacing: 6) {
                    Text(sender)
                        .font(EmailReaderTypography.metadata(weight: .semibold))
                        .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                        .lineLimit(1)

                    Text(receivedAt)
                        .font(EmailReaderTypography.metadata())
                        .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                        .lineLimit(1)
                }

                Spacer(minLength: 24)

                EmailActionRow(colorScheme: colorScheme, onReply: onReply)
            }
            .padding(.top, 44)

            Rectangle()
                .fill(ElectronicMailDesign.divider(for: colorScheme))
                .frame(height: 1)
                .padding(.top, 40)

            if let errorMessage {
                EmailReaderErrorView(
                    message: errorMessage,
                    colorScheme: colorScheme,
                    onRetry: onRetry
                )
                .padding(.top, 28)
            } else {
                EmailBodyContent(
                    threadID: threadID,
                    message: message,
                    fallbackText: bodyText,
                    colorScheme: colorScheme
                )
                .padding(.top, 34)
            }
        }
    }

    private var sender: String {
        EmailReaderText.senderName(message?.fromAddress)
            ?? row?.sender
            ?? "Unknown sender"
    }

    private var receivedAt: String {
        if let receivedAt = message?.receivedAt {
            return EmailReaderText.readerDate(receivedAt)
        }
        return row?.timeLabel ?? ""
    }

    private var bodyText: String {
        let value = message?.body.trimmingCharacters(in: .whitespacesAndNewlines)
        return value?.isEmpty == false ? value! : EmailReaderText.loadingFullEmail
    }
}

private struct GroupedEmailContent: View {
    let threadID: String
    let title: String
    let summary: String?
    let messages: [ThreadMessage]
    let expectedMessageCount: Int
    let focusedMessageID: String?
    let errorMessage: String?
    let colorScheme: ColorScheme
    @Binding var expandedMessageKeys: Set<String>
    let onRetry: () -> Void
    let onReply: () -> Void
    let onFocusedMessageKey: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            EmailReaderTitleHeader(
                title: title,
                summary: summary,
                colorScheme: colorScheme
            )

            HStack {
                Spacer()
                EmailActionRow(colorScheme: colorScheme, onReply: onReply)
            }
            .padding(.top, 30)

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
                    ForEach(presentationItems) { item in
                        EmailMessageCard(
                            threadID: threadID,
                            message: item.message,
                            expanded: EmailThreadPresentation.isExpanded(
                                messageKey: item.id,
                                latestMessageKey: latestMessageKey,
                                userExpandedMessageKeys: expandedMessageKeys
                            ),
                            colorScheme: colorScheme
                        ) {
                            toggle(item.id)
                        }
                        .id(item.id)
                    }
                }
                .padding(.top, 52)
                .animation(.easeInOut(duration: 0.16), value: expandedMessageKeys)
            }
        }
        .onAppear(perform: focusRequestedMessage)
        .onChange(of: focusedMessageID) { _ in
            focusRequestedMessage()
        }
        .onChange(of: messages) { _ in
            focusRequestedMessage()
        }
    }

    private var orderedMessages: [ThreadMessage] {
        EmailThreadPresentation.orderedMessages(messages)
    }

    private var presentationItems: [EmailThreadPresentationItem] {
        EmailThreadPresentation.items(from: orderedMessages)
    }

    private var latestMessageKey: String? {
        EmailThreadPresentation.latestMessageKey(in: presentationItems)
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

    private func toggle(_ messageKey: String) {
        if messageKey == latestMessageKey {
            return
        }
        if expandedMessageKeys.contains(messageKey) {
            expandedMessageKeys.remove(messageKey)
        } else {
            expandedMessageKeys.insert(messageKey)
        }
    }

    private func focusRequestedMessage() {
        guard let focusedMessageID,
              let item = presentationItems.first(where: { $0.message.id == focusedMessageID }) else {
            return
        }
        if item.id != latestMessageKey {
            expandedMessageKeys.insert(item.id)
        }
        DispatchQueue.main.async {
            onFocusedMessageKey(item.id)
        }
    }
}

private struct EmailReaderTitleHeader: View {
    let title: String
    let summary: String?
    let colorScheme: ColorScheme

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title)
                .font(EmailReaderTypography.title())
                .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                .lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)

            if let summary {
                Text(summary)
                    .font(EmailReaderTypography.subtitle())
                    .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                    .lineLimit(3)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

private struct EmailBodyContent: View {
    let threadID: String
    let message: ThreadMessage?
    let fallbackText: String
    let colorScheme: ColorScheme

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            if let htmlDocument {
                EmailOriginalBodyView(
                    htmlDocument: htmlDocument,
                    fallbackText: bodyText,
                    threadID: threadID,
                    messageID: message?.id
                )
            } else {
                if !markers.isEmpty {
                    EmailReaderMarkerRow(markers: markers, colorScheme: colorScheme)
                }

                EmailTextBodyView(
                    bodyText: bodyText,
                    colorScheme: colorScheme
                )
            }
        }
    }

    private var bodyText: String {
        switch EmailReaderBodyResolver.bodyKind(message: message, fallbackText: fallbackText, threadID: threadID) {
        case .html(_, let fallbackText):
            return fallbackText
        case .text(let bodyText):
            return bodyText
        }
    }

    private var markers: [ThreadMessageReaderMarker] {
        message?.reader?.markers.uniquedPreservingOrder() ?? []
    }

    private var htmlDocument: String? {
        EmailReaderBodyResolver.originalHTML(from: message)
    }
}

enum EmailReaderBodyKind: Equatable {
    case html(String, fallbackText: String)
    case text(String)
}

enum EmailReaderBodyResolver {
    static func bodyKind(message: ThreadMessage?, fallbackText: String, threadID: String? = nil) -> EmailReaderBodyKind {
        if let html = nonEmpty(message?.htmlRenderDocument) ?? nonEmpty(message?.htmlBody) {
            let analysis = analyzeHTML(html)
            let fallback = readableFallbackText(message: message, fallbackText: fallbackText, analysis: analysis)

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
        nonEmpty(message?.htmlRenderDocument) ?? nonEmpty(message?.htmlBody)
    }

    static func originalHTML(from message: ThreadMessage?) -> String? {
        nonEmpty(message?.htmlRenderDocument) ?? nonEmpty(message?.htmlBody)
    }

    static func isRichEmailHTML(_ value: String) -> Bool {
        nonEmpty(value) != nil
    }

    private static func analyzeHTML(_ value: String) -> HTMLBodyAnalysis {
        let imageTags = matches(pattern: #"<\s*img\b[^>]*>"#, in: value)
        var substantiveImageCount = 0
        var trackingImageCount = 0

        for tag in imageTags {
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

        let plainText = plainText(fromHTML: value)
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
    }

}

private struct EmailTextBodyView: View {
    let bodyText: String
    let colorScheme: ColorScheme
    var minHeight: CGFloat = 0

    var bodyViewText: String {
        EmailReaderText.decodingHTML(bodyText.trimmingCharacters(in: .whitespacesAndNewlines))
    }

    var attributedBodyText: AttributedString {
        EmailReaderText.attributedPlainText(
            bodyViewText,
            colorScheme: colorScheme
        )
    }

    var body: some View {
        Text(attributedBodyText)
            .lineSpacing(5)
            .fixedSize(horizontal: false, vertical: true)
            .textSelection(.enabled)
            .frame(maxWidth: .infinity, minHeight: minHeight, alignment: .topLeading)
    }
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
                EmailReaderDetailDisclosure(title: "Signature", bodyText: signatureText, colorScheme: colorScheme)
            }
            if let quotedText = nonEmpty(message?.reader?.quotedText) {
                EmailReaderDetailDisclosure(title: "Quoted text", bodyText: quotedText, colorScheme: colorScheme)
            }
            if let footerText = nonEmpty(message?.reader?.footerText) {
                EmailReaderDetailDisclosure(title: "Footer", bodyText: footerText, colorScheme: colorScheme)
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
                EmailTextBodyView(bodyText: bodyText, colorScheme: colorScheme)
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
    let htmlDocument: String
    let fallbackText: String
    let threadID: String
    let messageID: String?

    var body: some View {
        EmailHTMLBodyView(
            htmlDocument: htmlDocument,
            fallbackText: fallbackText,
            threadID: threadID,
            messageID: messageID,
            colorScheme: .light
        )
        .background(Color.white)
        .padding(16)
    }
}

private struct EmailHTMLBodyView: View {
    let htmlDocument: String
    let fallbackText: String
    let threadID: String
    let messageID: String?
    let colorScheme: ColorScheme

    @State private var contentHeight: CGFloat = EmailReaderMetrics.htmlBodyMinHeight
    @State private var runtimeFallbackReason: String?

    var body: some View {
        Group {
            if runtimeFallbackReason != nil {
                EmailTextBodyView(
                    bodyText: fallbackText,
                    colorScheme: colorScheme,
                    minHeight: 120
                )
            } else {
                EmailHTMLWebView(
                    html: EmailHTMLDocument.renderableDocument(from: htmlDocument, colorScheme: colorScheme),
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
        .onChange(of: htmlDocument) { _, _ in
            runtimeFallbackReason = nil
            contentHeight = EmailReaderMetrics.htmlBodyMinHeight
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

private struct EmailHTMLWebView: NSViewRepresentable {
    let html: String
    @Binding var contentHeight: CGFloat
    let onRenderResult: (EmailHTMLRenderResult) -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(contentHeight: $contentHeight, onRenderResult: onRenderResult)
    }

    func makeNSView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.defaultWebpagePreferences.allowsContentJavaScript = false

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

    final class Coordinator: NSObject, WKNavigationDelegate {
        var currentHTML: String?
        var onRenderResult: (EmailHTMLRenderResult) -> Void
        private var contentHeight: Binding<CGFloat>

        init(contentHeight: Binding<CGFloat>, onRenderResult: @escaping (EmailHTMLRenderResult) -> Void) {
            self.contentHeight = contentHeight
            self.onRenderResult = onRenderResult
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            updateRenderResult(from: webView)
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { [weak self, weak webView] in
                guard let webView else {
                    return
                }
                self?.updateRenderResult(from: webView)
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { [weak self, weak webView] in
                guard let webView else {
                    return
                }
                self?.updateRenderResult(from: webView)
            }
        }

        func webView(
            _ webView: WKWebView,
            decidePolicyFor navigationAction: WKNavigationAction,
            decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
        ) {
            if navigationAction.navigationType == .linkActivated, let url = navigationAction.request.url {
                NSWorkspace.shared.open(url)
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
                    self.contentHeight.wrappedValue = nextHeight
                }
                self.onRenderResult(renderResult)
            }
        }
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
    override func scrollWheel(with event: NSEvent) {
        guard let scrollView = enclosingScrollView() else {
            super.scrollWheel(with: event)
            return
        }
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
    let id: String
    let message: ThreadMessage
}

enum EmailThreadPresentation {
    static func orderedMessages(_ messages: [ThreadMessage]) -> [ThreadMessage] {
        messages.enumerated().sorted { lhs, rhs in
            let lhsDate = EmailReaderText.date(from: lhs.element.receivedAt)
            let rhsDate = EmailReaderText.date(from: rhs.element.receivedAt)
            switch (lhsDate, rhsDate) {
            case let (lhsDate?, rhsDate?) where lhsDate != rhsDate:
                return lhsDate > rhsDate
            case (nil, nil) where lhs.element.receivedAt != rhs.element.receivedAt:
                return lhs.element.receivedAt > rhs.element.receivedAt
            default:
                return lhs.offset < rhs.offset
            }
        }.map(\.element)
    }

    static func latestMessageID(in orderedMessages: [ThreadMessage]) -> String? {
        orderedMessages.first?.id
    }

    static func items(from orderedMessages: [ThreadMessage]) -> [EmailThreadPresentationItem] {
        orderedMessages.enumerated().map { index, message in
            EmailThreadPresentationItem(id: "\(index)::\(message.id)", message: message)
        }
    }

    static func latestMessageKey(in items: [EmailThreadPresentationItem]) -> String? {
        items.first?.id
    }

    static func isExpanded(
        messageKey: String,
        latestMessageKey: String?,
        userExpandedMessageKeys: Set<String>
    ) -> Bool {
        messageKey == latestMessageKey || userExpandedMessageKeys.contains(messageKey)
    }

    static func displaySubject(for message: ThreadMessage) -> String {
        let value = message.subject?.trimmingCharacters(in: .whitespacesAndNewlines)
        return EmailReaderText.decodingHTML(value?.isEmpty == false ? value! : "No subject")
    }
}

private enum EmailHTMLDocument {
    static func renderableDocument(from html: String, colorScheme: ColorScheme) -> String {
        let trimmed = html.trimmingCharacters(in: .whitespacesAndNewlines)
        let document = removingOuterMailCanvas(from: trimmed)
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
    let colorScheme: ColorScheme
    let onToggle: () -> Void

    @ViewBuilder
    var body: some View {
        if expanded {
            expandedMessage
        } else {
            collapsedMessageCard
        }
    }

    private var messageHeader: some View {
        HStack(alignment: .firstTextBaseline, spacing: 20) {
            Text(sender)
                .font(EmailReaderTypography.messageTitle())
                .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                .lineLimit(1)

            Spacer(minLength: 24)

            Text(EmailReaderText.shortDate(message.receivedAt))
                .font(EmailReaderTypography.metadata())
                .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                .lineLimit(1)
        }
    }

    private var collapsedMessageCard: some View {
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
        .onTapGesture(perform: onToggle)
    }

    private var expandedMessage: some View {
        VStack(alignment: .leading, spacing: 22) {
            messageHeader

            EmailBodyContent(
                threadID: threadID,
                message: message,
                fallbackText: displayBody,
                colorScheme: colorScheme
            )
        }
        .padding(.horizontal, 22)
        .padding(.vertical, 18)
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

    private var displayBody: String {
        let body = EmailReaderText.decodingHTML(message.body).trimmingCharacters(in: .whitespacesAndNewlines)
        return body.isEmpty ? EmailReaderText.loadingFullEmail : body
    }

    private var htmlDocument: String? {
        let value = (message.htmlRenderDocument ?? message.htmlBody)?.trimmingCharacters(in: .whitespacesAndNewlines)
        return value?.isEmpty == false ? value : nil
    }
}

private struct EmailActionRow: View {
    let colorScheme: ColorScheme
    let onReply: () -> Void

    private let symbols = [
        "arrowshape.turn.up.left",
        "arrowshape.turn.up.right",
        "star",
        "ellipsis",
    ]

    var body: some View {
        HStack(spacing: 28) {
            ForEach(symbols, id: \.self) { symbol in
                Button {
                    if symbol == "arrowshape.turn.up.left" {
                        onReply()
                    }
                } label: {
                    Image(systemName: symbol)
                        .font(EmailReaderTypography.actionIcon())
                        .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                        .frame(width: 28, height: 28)
                }
                .buttonStyle(.plain)
                .help(symbol == "arrowshape.turn.up.left" ? "Reply" : "Coming soon")
                .disabled(symbol != "arrowshape.turn.up.left")
            }
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
    static let contentTop: CGFloat = ElectronicMailShellMetrics.navTop
    static let cardRadius: CGFloat = 7
    static let htmlBodyMinHeight: CGFloat = 360
    static let htmlBodyMaxHeight: CGFloat = 6000
}

private enum EmailReaderTypography {
    static func title(weight: Font.Weight = .bold) -> Font {
        .system(size: 21, weight: weight)
    }

    static func subtitle(weight: Font.Weight = .regular) -> Font {
        .system(size: 14, weight: weight)
    }

    static func section(weight: Font.Weight = .semibold) -> Font {
        .system(size: 15, weight: weight)
    }

    static func body(weight: Font.Weight = .regular) -> Font {
        .system(size: 16, weight: weight)
    }

    static func metadata(weight: Font.Weight = .regular) -> Font {
        .system(size: 13, weight: weight)
    }

    static func messageTitle(weight: Font.Weight = .semibold) -> Font {
        .system(size: 15, weight: weight)
    }

    static func marker(weight: Font.Weight = .medium) -> Font {
        .system(size: 12, weight: weight)
    }

    static func actionIcon(weight: Font.Weight = .regular) -> Font {
        .system(size: 18, weight: weight)
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
        guard value.range(of: #"&(?:#\d+|#x[0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]+);"#, options: .regularExpression) != nil,
              let data = value.data(using: .utf8),
              let decoded = try? NSAttributedString(
                data: data,
                options: [
                    .documentType: NSAttributedString.DocumentType.html,
                    .characterEncoding: String.Encoding.utf8.rawValue,
                ],
                documentAttributes: nil
              ).string else {
            return value
        }
        return decoded
    }

    static func attributedPlainText(_ value: String, colorScheme: ColorScheme) -> AttributedString {
        var attributed = AttributedString(value)
        attributed.font = EmailReaderTypography.body()
        attributed.foregroundColor = ElectronicMailDesign.primaryText(for: colorScheme)

        guard let detector = try? NSDataDetector(types: NSTextCheckingResult.CheckingType.link.rawValue) else {
            return attributed
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

        return attributed
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

private extension Array where Element: Hashable {
    func uniquedPreservingOrder() -> [Element] {
        var seen = Set<Element>()
        return filter { seen.insert($0).inserted }
    }
}

#Preview("Single Email") {
    EmailReaderView(
        threadID: "demo-google-today",
        focusedMessageID: nil,
        thread: DemoAppFixtures.threads["demo-google-today"],
        row: nil,
        errorMessage: nil,
        colorScheme: .dark,
        onRetry: {},
        onReply: {}
    )
    .frame(width: 1440, height: 900)
}
