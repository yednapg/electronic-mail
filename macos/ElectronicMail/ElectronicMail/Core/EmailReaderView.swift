import AppKit
import Foundation
import SwiftUI
import WebKit

struct EmailReaderView: View {
    let threadID: String
    let thread: ThreadReaderResponse?
    let row: InboxRowViewModel?
    let errorMessage: String?
    let colorScheme: ColorScheme
    let onClose: () -> Void
    let onRetry: () -> Void

    @State private var expandedMessageKeys: Set<String> = []

    var body: some View {
        GeometryReader { proxy in
            let contentWidth = min(
                EmailReaderMetrics.maxContentWidth,
                max(
                    EmailReaderMetrics.minContentWidth,
                    proxy.size.width * 0.5 - ElectronicMailShellMetrics.navLeading * 2
                )
            )

            ScrollView(.vertical, showsIndicators: true) {
                EmailReaderChrome(contentWidth: contentWidth) {
                    if resolvedMessageCount <= 1 {
                        SingleEmailContent(
                            title: readerTitle,
                            message: thread?.messages.first,
                            row: row,
                            errorMessage: errorMessage,
                            colorScheme: colorScheme,
                            onClose: onClose,
                            onRetry: onRetry
                        )
                    } else {
                        GroupedEmailContent(
                            title: readerTitle,
                            messages: thread?.messages ?? [],
                            expectedMessageCount: resolvedMessageCount,
                            errorMessage: errorMessage,
                            colorScheme: colorScheme,
                            expandedMessageKeys: $expandedMessageKeys,
                            onClose: onClose,
                            onRetry: onRetry
                        )
                    }
                }
                .padding(.top, EmailReaderMetrics.contentTop)
                .padding(.bottom, 88)
                .frame(maxWidth: .infinity)
            }
        }
        .environment(\.font, .system(.body, design: .rounded))
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
    let title: String
    let message: ThreadMessage?
    let row: InboxRowViewModel?
    let errorMessage: String?
    let colorScheme: ColorScheme
    let onClose: () -> Void
    let onRetry: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            EmailReaderTitleHeader(
                title: title,
                colorScheme: colorScheme,
                onClose: onClose
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

                EmailActionRow(colorScheme: colorScheme)
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
        return value?.isEmpty == false ? value! : "Loading email..."
    }
}

private struct GroupedEmailContent: View {
    let title: String
    let messages: [ThreadMessage]
    let expectedMessageCount: Int
    let errorMessage: String?
    let colorScheme: ColorScheme
    @Binding var expandedMessageKeys: Set<String>
    let onClose: () -> Void
    let onRetry: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            EmailReaderTitleHeader(
                title: title,
                colorScheme: colorScheme,
                onClose: onClose
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
                    ForEach(presentationItems) { item in
                        EmailMessageCard(
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
                    }
                }
                .padding(.top, 52)
                .animation(.easeInOut(duration: 0.16), value: expandedMessageKeys)
            }
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
                        Text("Loading email...")
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
}

private struct EmailReaderTitleHeader: View {
    let title: String
    let colorScheme: ColorScheme
    let onClose: () -> Void

    var body: some View {
        ZStack(alignment: .leading) {
            Button(action: onClose) {
                Image(systemName: "chevron.backward")
                    .font(EmailReaderTypography.backIcon())
                    .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                    .frame(width: EmailReaderMetrics.backHitSize, height: EmailReaderMetrics.backHitSize)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .offset(x: -EmailReaderMetrics.backOffset)
            .accessibilityLabel("Back")

            Text(title)
                .font(EmailReaderTypography.title())
                .tracking(ElectronicMailType.titleTracking)
                .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                .lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

private struct EmailBodyContent: View {
    let message: ThreadMessage?
    let fallbackText: String
    let colorScheme: ColorScheme

    var body: some View {
        switch EmailReaderBodyResolver.bodyKind(message: message, fallbackText: fallbackText) {
        case .html(let htmlDocument):
            EmailHTMLBodyView(htmlDocument: htmlDocument, colorScheme: colorScheme)
        case .text(let bodyText):
            EmailTextBodyCard(
                bodyText: bodyText,
                colorScheme: colorScheme
            )
        }
    }
}

enum EmailReaderBodyKind: Equatable {
    case html(String)
    case text(String)
}

enum EmailReaderBodyResolver {
    static func bodyKind(message: ThreadMessage?, fallbackText: String) -> EmailReaderBodyKind {
        if let html = renderableHTML(from: message) {
            return .html(html)
        }

        let bodyText = textFromSimpleHTML(message)
            ?? nonEmpty(message?.body)
            ?? nonEmpty(fallbackText)
            ?? "Loading email..."
        return .text(EmailReaderText.decodingHTML(bodyText))
    }

    static func renderableHTML(from message: ThreadMessage?) -> String? {
        let value = nonEmpty(message?.htmlRenderDocument) ?? nonEmpty(message?.htmlBody)
        guard let value, isRichEmailHTML(value) else {
            return nil
        }
        return value
    }

    static func isRichEmailHTML(_ value: String) -> Bool {
        if contains(pattern: #"<\s*(picture|source)\b"#, in: value) || hasSubstantiveImage(in: value) {
            return true
        }

        let tableElementCount = count(pattern: #"<\s*table\b"#, in: value)
        let tableTagCount = count(pattern: #"<\s*(table|tbody|thead|tfoot|tr|td|th)\b"#, in: value)
        let layoutTagCount = count(pattern: #"<\s*(center|font|hr)\b"#, in: value)
        let styleCount = count(pattern: #"\sstyle\s*="#, in: value)
        let classCount = count(pattern: #"\sclass\s*="#, in: value)
        let textLength = plainText(fromHTML: value).count
        let sourceIsDocumentSized = value.count > max(700, textLength * 2)

        if tableElementCount >= 2 && tableTagCount >= 4 && sourceIsDocumentSized {
            return true
        }
        if tableElementCount >= 2 && tableTagCount >= 2 && (styleCount >= 1 || classCount >= 1) && value.count > max(500, textLength * 2) {
            return true
        }
        if layoutTagCount >= 2 && (styleCount >= 1 || classCount >= 1) && sourceIsDocumentSized {
            return true
        }
        return false
    }

    private static func hasSubstantiveImage(in value: String) -> Bool {
        for tag in matches(pattern: #"<\s*img\b[^>]*>"#, in: value) {
            let attrs = htmlAttributes(in: tag)
            let style = attrs["style"] ?? ""
            if isHiddenImageStyle(style) {
                continue
            }

            var sizes = [
                numericCSSSize(attrs["width"]),
                numericCSSSize(attrs["height"]),
            ]
            sizes.append(contentsOf: matches(pattern: #"\b(?:width|height)\s*:\s*([0-9.]+)\s*px"#, in: style).map(numericCSSSize))

            if sizes.compactMap({ $0 }).contains(where: { $0 <= 2 }) {
                continue
            }
            if sizes.compactMap({ $0 }).contains(where: { $0 >= 24 }) {
                return true
            }

            guard let src = attrs["src"], !src.isEmpty else {
                continue
            }
            if contains(pattern: #"(/wf/open|[?&]open=|/open[?/]|/track|tracking|pixel|beacon)"#, in: src) {
                continue
            }
            return true
        }
        return false
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

    private static func textFromSimpleHTML(_ message: ThreadMessage?) -> String? {
        guard let html = nonEmpty(message?.htmlRenderDocument) ?? nonEmpty(message?.htmlBody) else {
            return nil
        }
        return nonEmpty(plainText(fromHTML: html))
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
            .replacingOccurrences(of: #"(?i)</\s*(div|p|tr|table|li|h[1-6])\s*>"#, with: "\n", options: .regularExpression)
        let withoutTags = withLineBreaks.replacingOccurrences(
            of: #"<[^>]+>"#,
            with: " ",
            options: .regularExpression
        )
        return compactText(EmailReaderText.decodingHTML(withoutTags))
    }

    private static func compactText(_ value: String) -> String {
        value
            .components(separatedBy: .whitespacesAndNewlines)
            .filter { !$0.isEmpty }
            .joined(separator: " ")
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
}

private struct EmailTextBodyCard: View {
    let bodyText: String
    let colorScheme: ColorScheme

    var bodyViewText: String {
        EmailReaderText.decodingHTML(bodyText.trimmingCharacters(in: .whitespacesAndNewlines))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 28) {
            Text(bodyViewText)
                .font(EmailReaderTypography.body())
                .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                .lineSpacing(8)
                .fixedSize(horizontal: false, vertical: true)

            if let url = EmailReaderText.firstURL(in: bodyViewText) {
                Link(url.absoluteString, destination: url)
                    .font(EmailReaderTypography.body(weight: .semibold))
                    .foregroundStyle(ElectronicMailDesign.appleBlue)
            }
        }
        .padding(.horizontal, 36)
        .padding(.vertical, 34)
        .frame(maxWidth: .infinity, minHeight: EmailReaderMetrics.singleBodyMinHeight, alignment: .topLeading)
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

private struct EmailHTMLBodyView: View {
    let htmlDocument: String
    let colorScheme: ColorScheme

    @State private var contentHeight: CGFloat = EmailReaderMetrics.htmlBodyMinHeight

    var body: some View {
        EmailHTMLWebView(
            html: EmailHTMLDocument.renderableDocument(from: htmlDocument),
            contentHeight: $contentHeight
        )
        .frame(maxWidth: .infinity)
        .frame(height: max(EmailReaderMetrics.htmlBodyMinHeight, min(contentHeight, EmailReaderMetrics.htmlBodyMaxHeight)))
    }
}

private struct EmailHTMLWebView: NSViewRepresentable {
    let html: String
    @Binding var contentHeight: CGFloat

    func makeCoordinator() -> Coordinator {
        Coordinator(contentHeight: $contentHeight)
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
        guard context.coordinator.currentHTML != html else {
            return
        }
        context.coordinator.currentHTML = html
        webView.loadHTMLString(html, baseURL: nil)
    }

    final class Coordinator: NSObject, WKNavigationDelegate {
        var currentHTML: String?
        private var contentHeight: Binding<CGFloat>

        init(contentHeight: Binding<CGFloat>) {
            self.contentHeight = contentHeight
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            updateHeight(from: webView)
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { [weak self, weak webView] in
                guard let webView else {
                    return
                }
                self?.updateHeight(from: webView)
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

        private func updateHeight(from webView: WKWebView) {
            let script = "Math.max(document.body.scrollHeight, document.body.offsetHeight, document.documentElement.scrollHeight, document.documentElement.offsetHeight)"
            webView.evaluateJavaScript(script) { [weak self] result, _ in
                guard let self else {
                    return
                }
                let nextHeight: CGFloat?
                if let number = result as? NSNumber {
                    nextHeight = CGFloat(truncating: number)
                } else if let value = result as? Double {
                    nextHeight = CGFloat(value)
                } else {
                    nextHeight = nil
                }
                guard let nextHeight, nextHeight.isFinite, nextHeight > 0 else {
                    return
                }
                self.contentHeight.wrappedValue = nextHeight
            }
        }
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
                return lhsDate < rhsDate
            case (nil, nil) where lhs.element.receivedAt != rhs.element.receivedAt:
                return lhs.element.receivedAt < rhs.element.receivedAt
            default:
                return lhs.offset < rhs.offset
            }
        }.map(\.element)
    }

    static func latestMessageID(in orderedMessages: [ThreadMessage]) -> String? {
        orderedMessages.last?.id
    }

    static func items(from orderedMessages: [ThreadMessage]) -> [EmailThreadPresentationItem] {
        orderedMessages.enumerated().map { index, message in
            EmailThreadPresentationItem(id: "\(index)::\(message.id)", message: message)
        }
    }

    static func latestMessageKey(in items: [EmailThreadPresentationItem]) -> String? {
        items.last?.id
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
    static func renderableDocument(from html: String) -> String {
        let trimmed = html.trimmingCharacters(in: .whitespacesAndNewlines)
        let document = removingOuterMailCanvas(from: trimmed)
        if document.range(of: #"<\s*(?:!doctype\s+html|html)\b"#, options: [.regularExpression, .caseInsensitive]) != nil {
            return injectingMailClientDefaults(into: document)
        }
        return """
        <!doctype html>
        <html>
        <head>
          <meta charset="utf-8">
          <meta name="viewport" content="width=device-width, initial-scale=1.0">
          <style>
            \(mailClientDefaults)
          </style>
        </head>
        <body>
          \(document)
        </body>
        </html>
        """
    }

    private static let mailClientDefaults = """
    :root {
      color-scheme: light;
      supported-color-schemes: light;
    }
    html, body {
      -webkit-text-size-adjust: 100%;
    }
    body {
      color: #000000;
      font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", Helvetica, Arial, sans-serif;
    }
    """

    private static func injectingMailClientDefaults(into document: String) -> String {
        let defaults = """
        <style>
        \(mailClientDefaults)
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
        .padding(.horizontal, 32)
        .frame(maxWidth: .infinity, minHeight: 80, alignment: .leading)
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
                .padding(.horizontal, 32)

            EmailBodyContent(
                message: message,
                fallbackText: displayBody,
                colorScheme: colorScheme
            )
        }
        .frame(maxWidth: .infinity, alignment: .topLeading)
    }

    private var sender: String {
        EmailReaderText.senderName(message.fromAddress) ?? "Unknown sender"
    }

    private var subject: String {
        EmailThreadPresentation.displaySubject(for: message)
    }

    private var displayBody: String {
        EmailReaderText.decodingHTML(message.body)
    }

    private var htmlDocument: String? {
        let value = (message.htmlRenderDocument ?? message.htmlBody)?.trimmingCharacters(in: .whitespacesAndNewlines)
        return value?.isEmpty == false ? value : nil
    }
}

private struct EmailActionRow: View {
    let colorScheme: ColorScheme

    private let symbols = [
        "arrowshape.turn.up.left",
        "arrowshape.turn.up.right",
        "star",
        "ellipsis",
    ]

    var body: some View {
        HStack(spacing: 28) {
            ForEach(symbols, id: \.self) { symbol in
                Button(action: {}) {
                    Image(systemName: symbol)
                        .font(EmailReaderTypography.actionIcon())
                        .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                        .frame(width: 28, height: 28)
                }
                .buttonStyle(.plain)
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
    static let maxContentWidth: CGFloat = 1000
    static let contentTop: CGFloat = ElectronicMailShellMetrics.navTop
    static let cardRadius: CGFloat = 7
    static let singleBodyMinHeight: CGFloat = 360
    static let htmlBodyMinHeight: CGFloat = 360
    static let htmlBodyMaxHeight: CGFloat = 6000
    static let backHitSize: CGFloat = 44
    static let backOffset: CGFloat = 58
}

private enum EmailReaderTypography {
    static func title(weight: Font.Weight = .bold) -> Font {
        ElectronicMailType.title(weight: weight)
    }

    static func subtitle(weight: Font.Weight = .regular) -> Font {
        ElectronicMailType.detail(weight: weight)
    }

    static func section(weight: Font.Weight = .semibold) -> Font {
        ElectronicMailType.sectionTitle(weight: weight)
    }

    static func body(weight: Font.Weight = .regular) -> Font {
        ElectronicMailType.body(weight: weight)
    }

    static func metadata(weight: Font.Weight = .regular) -> Font {
        ElectronicMailType.detail(weight: weight)
    }

    static func messageTitle(weight: Font.Weight = .semibold) -> Font {
        ElectronicMailType.sectionTitle(weight: weight)
    }

    static func actionIcon(weight: Font.Weight = .regular) -> Font {
        ElectronicMailType.icon(weight: weight)
    }

    static func backIcon() -> Font {
        ElectronicMailType.icon(weight: .semibold)
    }
}

private enum EmailReaderText {
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

    static func firstURL(in value: String) -> URL? {
        guard let detector = try? NSDataDetector(types: NSTextCheckingResult.CheckingType.link.rawValue) else {
            return nil
        }
        let range = NSRange(value.startIndex..<value.endIndex, in: value)
        return detector.firstMatch(in: value, options: [], range: range)?.url
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
        thread: DemoAppFixtures.threads["demo-google-today"],
        row: nil,
        errorMessage: nil,
        colorScheme: .dark,
        onClose: {},
        onRetry: {}
    )
    .frame(width: 1440, height: 900)
}
