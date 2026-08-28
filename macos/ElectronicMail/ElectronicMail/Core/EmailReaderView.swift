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
    var onAsk: ((ThreadMessage) -> Void)? = nil

    @State private var expandedMessageKeys: Set<EmailThreadPresentationItem.ID> = []
    @State private var activeMessageKey: EmailThreadPresentationItem.ID?
    @State private var expansionInitializedThreadID: String?

    var body: some View {
        GeometryReader { proxy in
            let contentWidth = min(EmailReaderMetrics.maxContentWidth, max(1, proxy.size.width - EmailReaderMetrics.horizontalPadding * 2))
            let messages = thread?.messages ?? []
            let presentation = EmailThreadPresentation.snapshot(from: messages)
            let selectedKey = resolvedActiveMessageKey(in: presentation)
            let activeMessage = presentation.items.first(where: { $0.id == selectedKey })?.message
                ?? messages.first

            ZStack(alignment: .bottom) {
                ScrollViewReader { scrollProxy in
                    ElectronicMailReaderFadingScrollView(
                        colorScheme: colorScheme,
                        showsIndicators: true,
                        scrollIdentity: threadID
                    ) {
                        VStack(alignment: .center, spacing: 0) {
                            EmailReaderChrome(contentWidth: contentWidth) {
                                if resolvedMessageCount <= 1 {
                                    SingleEmailContent(
                                        threadID: threadID,
                                        message: messages.first,
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
                                        messages: messages,
                                        expectedMessageCount: resolvedMessageCount,
                                        focusedMessageID: focusedMessageID,
                                        errorMessage: errorMessage,
                                        currentUserDisplayName: currentUserDisplayName,
                                        currentUserEmail: currentUserEmail,
                                        colorScheme: colorScheme,
                                        mailboxLabel: mailboxLabel,
                                        expandedMessageKeys: $expandedMessageKeys,
                                        activeMessageKey: $activeMessageKey,
                                        expansionInitializedThreadID: $expansionInitializedThreadID,
                                        onRetry: onRetry,
                                        onRespond: onRespond,
                                        onThreadAction: onThreadAction,
                                        onOpenAttachment: onOpenAttachment,
                                        isAttachmentDownloading: isAttachmentDownloading,
                                        onInitialMessageKey: { messageKey, explicitlyFocused in
                                            DispatchQueue.main.async {
                                                scrollProxy.scrollTo(
                                                    messageKey,
                                                    anchor: explicitlyFocused ? .center : .top
                                                )
                                            }
                                        }
                                    )
                                }
                            }
                            .padding(.top, EmailReaderMetrics.contentTop)
                            .frame(maxWidth: .infinity)

                            if activeMessage != nil, errorMessage == nil {
                                Color.clear
                                    .frame(height: ElectronicMailControlMetrics.readerFixedActionsReservedHeight)
                                    .accessibilityHidden(true)
                            }

                            if resolvedMessageCount > 1 {
                                Color.clear
                                    .frame(height: max(0, proxy.size.height - 120))
                                    .accessibilityHidden(true)
                            }
                        }
                        .frame(maxWidth: .infinity)
                    }
                }

                if let activeMessage, errorMessage == nil {
                    ReaderActionBubbles(
                        onReply: { onRespond(.reply, activeMessage.id) },
                        onReplyAll: { onRespond(.replyAll, activeMessage.id) },
                        onForward: { onRespond(.forward, activeMessage.id) }
                    )
                    .frame(width: contentWidth)
                    .padding(.bottom, ElectronicMailControlMetrics.readerFloatingActionsBottomInset)
                    .zIndex(2)
                }
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

    private func nonEmpty(_ value: String?) -> String? {
        let trimmed = value?.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed?.isEmpty == false ? trimmed : nil
    }

    private func resolvedActiveMessageKey(
        in presentation: EmailThreadPresentationSnapshot
    ) -> EmailThreadPresentationItem.ID? {
        if let activeMessageKey,
           presentation.items.contains(where: { $0.id == activeMessageKey }) {
            return activeMessageKey
        }
        if let focusedMessageID,
           let focused = presentation.items.first(where: { $0.message.id == focusedMessageID }) {
            return focused.id
        }
        return presentation.latestMessageKey
    }

}

/// Shared reader scroll surface. A measured top marker drives one lightweight
/// gradient, so every message shape fades consistently beneath the fixed
/// subject/header area without adding per-row state or animations.
struct ElectronicMailReaderFadingScrollView<Content: View>: View {
    let colorScheme: ColorScheme
    let showsIndicators: Bool
    let scrollIdentity: AnyHashable?
    let offsetReadRequest: AnyHashable?
    let fadeTopInset: CGFloat
    let onScrollOffsetChange: (CGFloat) -> Void
    let content: Content

    @State private var scrollOffset: CGFloat = 0
    @State private var initialScrollOffset: CGFloat?

    init(
        colorScheme: ColorScheme,
        showsIndicators: Bool,
        scrollIdentity: AnyHashable? = nil,
        offsetReadRequest: AnyHashable? = nil,
        fadeTopInset: CGFloat = ElectronicMailControlMetrics.readerScrollFadeTopInset,
        onScrollOffsetChange: @escaping (CGFloat) -> Void = { _ in },
        @ViewBuilder content: () -> Content
    ) {
        self.colorScheme = colorScheme
        self.showsIndicators = showsIndicators
        self.scrollIdentity = scrollIdentity
        self.offsetReadRequest = offsetReadRequest
        self.fadeTopInset = fadeTopInset
        self.onScrollOffsetChange = onScrollOffsetChange
        self.content = content()
    }

    var body: some View {
        ScrollView(.vertical, showsIndicators: showsIndicators) {
            content
                .background(alignment: .top) {
                    ElectronicMailReaderScrollObserver(
                        identity: scrollIdentity,
                        offsetReadRequest: offsetReadRequest,
                        onOffsetChange: recordScrollOffset
                    )
                }
        }
        .onChange(of: scrollIdentity) { _, _ in
            initialScrollOffset = nil
            scrollOffset = 0
        }
        .environment(\.electronicMailReaderFadeTopInset, fadeTopInset)
        .mask {
            ElectronicMailReaderFadeMask(
                progress: ElectronicMailReaderScrollFade.progress(
                    forContentTop: scrollOffset,
                    initialContentTop: initialScrollOffset ?? scrollOffset
                )
            )
        }
    }

    private func recordScrollOffset(_ nextOffset: CGFloat) {
        if initialScrollOffset == nil {
            initialScrollOffset = nextOffset
            scrollOffset = nextOffset
            onScrollOffsetChange(nextOffset)
            return
        }
        guard abs(nextOffset - scrollOffset) > 0.25 else {
            return
        }
        scrollOffset = nextOffset
        onScrollOffsetChange(nextOffset)
    }
}

/// Mask the complete scroll surface rather than painting above it. That makes
/// rich WKWebView bodies and native SwiftUI message rows fade through the same
/// top edge even when AppKit composites the web view in a separate layer.
private struct ElectronicMailReaderFadeMask: View {
    let progress: CGFloat

    var body: some View {
        let clampedProgress = min(1, max(0, progress))
        VStack(spacing: 0) {
            LinearGradient(
                colors: [
                    Color.black.opacity(1 - clampedProgress),
                    Color.black,
                ],
                startPoint: .top,
                endPoint: .bottom
            )
            .frame(height: ElectronicMailControlMetrics.readerScrollFadeHeight)

            Color.black
        }
        .accessibilityHidden(true)
    }
}

enum ElectronicMailReaderScrollFade {
    static func progress(
        forContentTop contentTop: CGFloat,
        initialContentTop: CGFloat = 0
    ) -> CGFloat {
        let travel = abs(initialContentTop - contentTop)
        return min(
            1,
            max(
                0,
                travel / ElectronicMailControlMetrics.readerScrollFadeActivationDistance
            )
        )
    }
}

private struct ElectronicMailReaderScrollObserver: NSViewRepresentable {
    let identity: AnyHashable?
    let offsetReadRequest: AnyHashable?
    let onOffsetChange: (CGFloat) -> Void

    func makeNSView(context _: Context) -> ObserverView {
        let view = ObserverView()
        view.identity = identity
        view.offsetReadRequest = offsetReadRequest
        view.onOffsetChange = onOffsetChange
        return view
    }

    func updateNSView(_ nsView: ObserverView, context _: Context) {
        nsView.onOffsetChange = onOffsetChange
        nsView.identity = identity
        nsView.offsetReadRequest = offsetReadRequest
        nsView.installObservationIfPossible()
    }

    final class ObserverView: NSView {
        var identity: AnyHashable? {
            didSet {
                guard identity != oldValue else { return }
                DispatchQueue.main.async { [weak self] in
                    self?.publishCurrentOffset()
                }
            }
        }
        var offsetReadRequest: AnyHashable? {
            didSet {
                guard offsetReadRequest != oldValue else { return }
                DispatchQueue.main.async { [weak self] in
                    self?.installObservationIfPossible()
                    self?.publishCurrentOffset()
                }
            }
        }
        var onOffsetChange: (CGFloat) -> Void = { _ in }
        private weak var observedScrollView: NSScrollView?
        private var boundsObserver: NSObjectProtocol?

        override func viewDidMoveToSuperview() {
            super.viewDidMoveToSuperview()
            installObservationIfPossible()
        }

        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            installObservationIfPossible()
        }

        deinit {
            removeObservation()
        }

        func installObservationIfPossible() {
            guard window != nil, let scrollView = enclosingScrollView() else { return }
            if observedScrollView === scrollView {
                publishCurrentOffset()
                return
            }

            removeObservation()
            observedScrollView = scrollView
            let clipView = scrollView.contentView
            clipView.postsBoundsChangedNotifications = true
            boundsObserver = NotificationCenter.default.addObserver(
                forName: NSView.boundsDidChangeNotification,
                object: clipView,
                queue: .main
            ) { [weak self] _ in
                self?.publishCurrentOffset()
            }
            publishCurrentOffset()
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

        private func publishCurrentOffset() {
            guard let observedScrollView else { return }
            onOffsetChange(observedScrollView.contentView.bounds.origin.y)
        }

        private func removeObservation() {
            if let boundsObserver {
                NotificationCenter.default.removeObserver(boundsObserver)
            }
            boundsObserver = nil
            observedScrollView = nil
        }
    }
}

private struct ElectronicMailReaderFadeTopInsetEnvironmentKey: EnvironmentKey {
    static let defaultValue = ElectronicMailControlMetrics.readerScrollFadeTopInset
}

private extension EnvironmentValues {
    var electronicMailReaderFadeTopInset: CGFloat {
        get { self[ElectronicMailReaderFadeTopInsetEnvironmentKey.self] }
        set { self[ElectronicMailReaderFadeTopInsetEnvironmentKey.self] = newValue }
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
            if let errorMessage {
                EmailReaderErrorView(
                    message: errorMessage,
                    colorScheme: colorScheme,
                    onRetry: onRetry
                )
                .padding(.top, EmailReaderMetrics.headerToConversation)
            } else if let message {
                EmailMessageCard(
                    threadID: threadID,
                    message: message,
                    expanded: true,
                    allowsCollapse: false,
                    currentUserDisplayName: currentUserDisplayName,
                    currentUserEmail: currentUserEmail,
                    colorScheme: colorScheme,
                    mailboxLabel: mailboxLabel,
                    onRespond: onRespond,
                    onThreadAction: onThreadAction,
                    onOpenAttachment: onOpenAttachment,
                    isAttachmentDownloading: isAttachmentDownloading,
                    groupingDetails: nil,
                    onOpenDetails: nil,
                    onToggle: {}
                )
                .padding(.top, EmailReaderMetrics.headerToConversation)
            } else {
                EmailReaderLoadingCard(colorScheme: colorScheme)
                    .padding(.top, EmailReaderMetrics.headerToConversation)
            }
        }
    }
}

private struct GroupedEmailContent: View {
    let threadID: String
    let messages: [ThreadMessage]
    let expectedMessageCount: Int
    let focusedMessageID: String?
    let errorMessage: String?
    let currentUserDisplayName: String?
    let currentUserEmail: String?
    let colorScheme: ColorScheme
    let mailboxLabel: MailboxLabel
    @Binding var expandedMessageKeys: Set<EmailThreadPresentationItem.ID>
    @Binding var activeMessageKey: EmailThreadPresentationItem.ID?
    @Binding var expansionInitializedThreadID: String?
    let onRetry: () -> Void
    let onRespond: (MailComposerMode, String) -> Void
    let onThreadAction: (GmailThreadAction, String?) -> Void
    let onOpenAttachment: (ThreadAttachment, String) -> Void
    let isAttachmentDownloading: (ThreadAttachment, String) -> Bool
    let onInitialMessageKey: (EmailThreadPresentationItem.ID, Bool) -> Void

    var body: some View {
        let presentation = EmailThreadPresentation.snapshot(from: messages)
        let renderIdentities = messages.map {
            EmailMessageRenderIdentity(id: $0.id, renderRevision: $0.renderRevision)
        }

        VStack(alignment: .leading, spacing: 0) {
            if let errorMessage {
                EmailReaderErrorView(
                    message: errorMessage,
                    colorScheme: colorScheme,
                    onRetry: onRetry
                )
                .padding(.top, EmailReaderMetrics.headerToConversation)
            } else if messages.isEmpty {
                loadingCards
                    .padding(.top, EmailReaderMetrics.headerToConversation)
            } else {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(Array(presentation.items.enumerated()), id: \.element.id) { index, item in
                        if index > 0 {
                            Rectangle()
                                .fill(ElectronicMailDesign.readerHairline(for: colorScheme))
                                .frame(height: 1)
                        }

                        EmailMessageCard(
                            threadID: threadID,
                            message: item.message,
                            expanded: expandedMessageKeys.contains(item.id),
                            allowsCollapse: true,
                            currentUserDisplayName: currentUserDisplayName,
                            currentUserEmail: currentUserEmail,
                            colorScheme: colorScheme,
                            mailboxLabel: mailboxLabel,
                            onRespond: onRespond,
                            onThreadAction: onThreadAction,
                            onOpenAttachment: onOpenAttachment,
                            isAttachmentDownloading: isAttachmentDownloading,
                            groupingDetails: nil,
                            onOpenDetails: nil
                        ) {
                            toggle(item.id, in: presentation)
                        }
                        .id(item.id)
                    }
                }
                .padding(.top, EmailReaderMetrics.headerToConversation)
            }
        }
        .onAppear {
            synchronizeExpansion(in: presentation, scrollToFocus: true)
        }
        .onChange(of: focusedMessageID) { _, _ in
            synchronizeExpansion(in: presentation, scrollToFocus: true)
        }
        .onChange(of: renderIdentities) { _, _ in
            synchronizeExpansion(in: presentation, scrollToFocus: false)
        }
    }

    private var loadingCards: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(0..<min(max(expectedMessageCount, 1), 2), id: \.self) { _ in
                EmailReaderLoadingCard(colorScheme: colorScheme)
                Rectangle()
                    .fill(ElectronicMailDesign.readerHairline(for: colorScheme))
                    .frame(height: 1)
            }
        }
    }

    private func toggle(
        _ messageKey: EmailThreadPresentationItem.ID,
        in presentation: EmailThreadPresentationSnapshot
    ) {
        // Email bodies can contain asynchronously measured WKWebViews. Animating
        // their layout height makes following messages move before WebKit has
        // reported its final size, which causes overlap and a second layout jump.
        if expandedMessageKeys.contains(messageKey) {
            expandedMessageKeys.remove(messageKey)
            if activeMessageKey == messageKey {
                activeMessageKey = presentation.items.reversed().first(where: {
                    expandedMessageKeys.contains($0.id)
                })?.id
            }
        } else {
            expandedMessageKeys.insert(messageKey)
            activeMessageKey = messageKey
        }
    }

    private func synchronizeExpansion(
        in presentation: EmailThreadPresentationSnapshot,
        scrollToFocus: Bool
    ) {
        let validKeys = Set(presentation.items.map(\.id))
        expandedMessageKeys.formIntersection(validKeys)

        let initialTarget = EmailReaderInitialMessagePolicy.target(
            focusedMessageID: focusedMessageID,
            in: presentation
        )

        if expansionInitializedThreadID != threadID,
           let initialTarget {
            expandedMessageKeys = [initialTarget.messageKey]
            activeMessageKey = initialTarget.messageKey
            expansionInitializedThreadID = threadID
            DispatchQueue.main.async {
                onInitialMessageKey(initialTarget.messageKey, initialTarget.explicitlyFocused)
            }
            return
        }

        if let activeMessageKey, !validKeys.contains(activeMessageKey) {
            self.activeMessageKey = presentation.items.reversed().first(where: {
                expandedMessageKeys.contains($0.id)
            })?.id
        }

        guard scrollToFocus,
              let focused = initialTarget,
              focused.explicitlyFocused else {
            return
        }
        expandedMessageKeys.insert(focused.messageKey)
        activeMessageKey = focused.messageKey
        DispatchQueue.main.async {
            onInitialMessageKey(focused.messageKey, true)
        }
    }
}

private struct EmailMessageRenderIdentity: Hashable {
    let id: String
    let renderRevision: UInt64
}

private struct ReaderThreadActions: View {
    let colorScheme: ColorScheme
    let mailboxLabel: MailboxLabel
    let isUnread: Bool
    let isStarred: Bool
    let onThreadAction: (GmailThreadAction) -> Void

    @State private var confirmPermanentDelete = false

    var body: some View {
        HStack(spacing: 10) {
            ReaderIconButton(
                symbol: primaryActionSymbol,
                help: primaryActionHelp,
                colorScheme: colorScheme,
                action: performPrimaryAction
            )

            ReaderIconButton(
                symbol: mailboxLabel == .trash ? "trash.slash" : "trash",
                help: mailboxLabel == .trash ? "Delete permanently" : "Move to Trash",
                colorScheme: colorScheme,
                destructive: mailboxLabel == .trash,
                action: performTrashAction
            )

            ReaderMoreActionsMenu(
                colorScheme: colorScheme,
                mailboxLabel: mailboxLabel,
                isUnread: isUnread,
                isStarred: isStarred,
                includesResponses: false,
                onRespond: { _ in },
                onThreadAction: onThreadAction,
                bordered: true
            )
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

    private var primaryActionSymbol: String {
        switch mailboxLabel {
        case .archive: return "tray.and.arrow.down"
        case .spam: return "checkmark.shield"
        case .trash: return "arrow.uturn.backward"
        default: return "archivebox"
        }
    }

    private var primaryActionHelp: String {
        switch mailboxLabel {
        case .archive: return "Move to Inbox"
        case .spam: return "Not Spam"
        case .trash: return "Restore from Trash"
        default: return "Archive conversation"
        }
    }

    private func performPrimaryAction() {
        switch mailboxLabel {
        case .archive: onThreadAction(.unarchive)
        case .spam: onThreadAction(.notSpam)
        case .trash: onThreadAction(.restoreTrash)
        default: onThreadAction(.archive)
        }
    }

    private func performTrashAction() {
        if mailboxLabel == .trash {
            confirmPermanentDelete = true
        } else {
            onThreadAction(.moveTrash)
        }
    }
}

private struct ReaderIconButton: View {
    let symbol: String
    let help: String
    let colorScheme: ColorScheme
    var destructive = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.system(size: 16, weight: .medium))
                .foregroundStyle(destructive ? Color.red : ElectronicMailDesign.primaryText(for: colorScheme))
                .frame(width: EmailReaderMetrics.headerActionHeight, height: EmailReaderMetrics.headerActionHeight)
                .contentShape(Circle())
        }
        .buttonStyle(.bordered)
        .buttonBorderShape(.circle)
        .tint(destructive ? Color.red : ElectronicMailDesign.appleBlue)
        .help(help)
        .accessibilityLabel(help)
    }
}

struct ReaderActionBubbles: View {
    let onReply: () -> Void
    let onReplyAll: () -> Void
    let onForward: () -> Void
    let onCompletion: (() -> Void)?
    let completionHelp: String?

    init(
        onReply: @escaping () -> Void,
        onReplyAll: @escaping () -> Void,
        onForward: @escaping () -> Void,
        onCompletion: (() -> Void)? = nil,
        completionHelp: String? = nil
    ) {
        self.onReply = onReply
        self.onReplyAll = onReplyAll
        self.onForward = onForward
        self.onCompletion = onCompletion
        self.completionHelp = completionHelp
    }

    var body: some View {
        ElectronicMailFloatingActionGroup(spacing: ElectronicMailControlMetrics.readerActionGap) {
            HStack(spacing: ElectronicMailControlMetrics.readerActionGap) {
                actionButton(
                    title: "Reply",
                    symbol: ElectronicMailSymbols.reply,
                    role: .prominent,
                    action: onReply
                )

                actionButton(
                    title: "Reply All",
                    symbol: "arrowshape.turn.up.left.2.fill",
                    role: .standard,
                    action: onReplyAll
                )

                actionButton(
                    title: "Forward",
                    symbol: "arrowshape.turn.up.right.fill",
                    role: .standard,
                    action: onForward
                )

                if let onCompletion, let completionHelp {
                    actionButton(
                        title: completionHelp,
                        symbol: "checkmark",
                        role: .standard,
                        action: onCompletion
                    )
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .center)
    }

    private func actionButton(
        title: String,
        symbol: String,
        role: ElectronicMailFloatingActionRole,
        action: @escaping () -> Void
    ) -> some View {
        ElectronicMailIconControl(
            symbol: symbol,
            accessibilityLabel: title,
            role: role,
            controlSize: ElectronicMailControlMetrics.actionHeight,
            symbolSize: ElectronicMailControlMetrics.headerSymbolSize,
            action: action
        )
        .help(title)
        .accessibilityLabel(title)
    }
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
                    .padding(.vertical, 3)
                    .contentShape(Rectangle())
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
    @State private var resolvedBodyID: EmailBodyResolutionID?
    @State private var showsQuotedContent = false

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
        let resolutionID = EmailBodyResolutionID(
            messageKey: message?.id ?? threadID,
            renderRevision: message?.renderRevision ?? 0,
            usesDarkMode: colorScheme == .dark,
            presentationVersion: EmailHTMLConversationPolicy.presentationVersion
        )
        self.resolutionID = resolutionID

        let initialBody = EmailReaderBodyResolver.immediatePlainText(
            message: message,
            fallbackText: fallbackText
        ).flatMap { bodyText -> EmailPreparedBody? in
            guard bodyText.utf16.count <= EmailReaderMetrics.maximumImmediateBodyCharacters else {
                return nil
            }
            return EmailPreparedBody.text(
                EmailReaderText.attributedPlainText(bodyText, colorScheme: colorScheme)
            )
        }
        _resolvedBody = State(initialValue: initialBody)
        _resolvedBodyID = State(initialValue: initialBody == nil ? nil : resolutionID)
    }

    static func == (lhs: EmailBodyContent, rhs: EmailBodyContent) -> Bool {
        lhs.threadID == rhs.threadID
            && lhs.resolutionID == rhs.resolutionID
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            switch resolvedBody {
            case .some(.html(let presentation, let fallbackText)):
                EmailOriginalBodyView(
                    preparedDocument: showsQuotedContent
                        ? presentation.originalDocument
                        : presentation.primaryDocument,
                    fallbackText: fallbackText,
                    threadID: threadID,
                    messageID: message?.id,
                    colorScheme: colorScheme,
                    renderRevision: resolutionID.renderRevision,
                    presentationMode: showsQuotedContent ? "original" : "conversation"
                )

                if presentation.hidesQuotedContent {
                    EmailQuotedContentButton(
                        expanded: showsQuotedContent,
                        colorScheme: colorScheme
                    ) {
                        showsQuotedContent.toggle()
                    }
                }
            case .some(.text(let attributedText)):
                if !markers.isEmpty {
                    EmailReaderMarkerRow(markers: markers, colorScheme: colorScheme)
                }

                EmailPreparedTextBodyView(
                    attributedText: attributedText,
                    colorScheme: colorScheme
                )

                if let quotedText = EmailReaderBodyResolver.quotedTextForDisclosure(from: message) {
                    EmailReaderDetailDisclosure(
                        title: "quoted content",
                        bodyText: quotedText,
                        colorScheme: colorScheme,
                        renderRevision: resolutionID.renderRevision
                    )
                    .id("\(resolutionID.messageKey)-quoted-content")
                }
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
            guard resolvedBodyID != resolutionID else { return }
            showsQuotedContent = false
            resolvedBody = nil
            resolvedBodyID = nil
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
                    let sourcePresentation = EmailHTMLConversationPolicy.presentation(
                        from: sourceHTML,
                        quoteDetected: input.message?.reader?.quoteDetected
                    )
                    let primaryRenderable = EmailHTMLDocument.renderableDocument(
                        from: sourcePresentation.primaryHTML,
                        colorScheme: input.usesDarkMode ? .dark : .light
                    )
                    guard !Task.isCancelled else { return nil }
                    let originalRenderable = sourcePresentation.hasHiddenQuotedContent
                        ? EmailHTMLDocument.renderableDocument(
                            from: sourcePresentation.originalHTML,
                            colorScheme: input.usesDarkMode ? .dark : .light
                        )
                        : primaryRenderable
                    guard !Task.isCancelled else { return nil }
                    return .html(
                        EmailHTMLPreparedConversation(
                            primaryDocument: EmailHTMLPreparedDocument(
                                remoteImagesDocument: EmailRemoteImagePolicy.renderDocument(from: primaryRenderable)
                            ),
                            originalDocument: EmailHTMLPreparedDocument(
                                remoteImagesDocument: EmailRemoteImagePolicy.renderDocument(from: originalRenderable)
                            ),
                            hidesQuotedContent: sourcePresentation.hasHiddenQuotedContent
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
            resolvedBodyID = resolutionID
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
    let presentationVersion: Int
}

private struct EmailBodyResolutionInput: @unchecked Sendable {
    let message: ThreadMessage?
    let fallbackText: String
    let threadID: String
    let usesDarkMode: Bool
}

private enum EmailPreparedBody: @unchecked Sendable {
    case html(EmailHTMLPreparedConversation, fallbackText: String)
    case text(AttributedString)
}

private struct EmailHTMLPreparedConversation: @unchecked Sendable {
    let primaryDocument: EmailHTMLPreparedDocument
    let originalDocument: EmailHTMLPreparedDocument
    let hidesQuotedContent: Bool
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

            if let primaryText = preferredReaderText(from: message) {
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

        if let primaryText = preferredReaderText(from: message) {
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

    /// Returns only bodies whose presentation is already authoritative without
    /// parsing sender HTML. This lets compact conversation messages expand at
    /// their final intrinsic height instead of showing a provisional loader.
    static func immediatePlainText(message: ThreadMessage?, fallbackText: String) -> String? {
        let renderMode = message?.reader?.renderMode
        if renderMode == "plain_conversation" || renderMode == "mixed" {
            guard !shouldReconsiderPlainConversationHTML(message) else {
                return nil
            }
            return preferredReaderText(from: message)
                ?? nonEmpty(message?.body).map(readableBodyText)
                ?? nonEmpty(fallbackText).map(readableBodyText)
        }

        guard nonEmpty(message?.htmlRenderDocument) == nil,
              nonEmpty(message?.htmlBody) == nil else {
            return nil
        }

        return preferredReaderText(from: message)
            ?? nonEmpty(message?.body).map(readableBodyText)
            ?? nonEmpty(fallbackText).map(readableBodyText)
    }

    static func quotedTextForDisclosure(from message: ThreadMessage?) -> String? {
        guard let quotedText = nonEmpty(message?.reader?.quotedText) else {
            return nil
        }
        guard let primaryText = nonEmpty(message?.reader?.primaryText) else {
            return nil
        }
        return normalizedForComparison(primaryText) == normalizedForComparison(quotedText)
            ? nil
            : quotedText
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
        case "plain_conversation":
            // Older cached reader payloads can classify designed transactional
            // mail as plain text. Recover only when there is no quoted-history
            // signal and the HTML itself is clearly layout-bearing.
            return message?.reader?.quoteDetected != true && analysis.isRich
        case "mixed":
            return false
        default:
            return message?.reader?.htmlIsRich == true || analysis.isRich
        }
    }

    private static func shouldReconsiderPlainConversationHTML(_ message: ThreadMessage?) -> Bool {
        guard message?.reader?.quoteDetected != true,
              let html = nonEmpty(message?.htmlRenderDocument) ?? nonEmpty(message?.htmlBody)
        else {
            return false
        }

        let primaryLength = preferredReaderText(from: message)?.utf16.count ?? 0
        return html.utf16.count > max(900, max(1, primaryLength) * 3)
    }

    private static func preferredReaderText(from message: ThreadMessage?) -> String? {
        let primaryText = nonEmpty(message?.reader?.primaryText).map(readableBodyText)
        guard message?.reader?.quoteDetected == true,
              let quotedText = nonEmpty(message?.reader?.quotedText).map(readableBodyText) else {
            return primaryText
        }
        guard let primaryText else {
            return quotedText
        }
        return normalizedForComparison(primaryText) == normalizedForComparison(quotedText)
            ? quotedText
            : primaryText
    }

    private static func normalizedForComparison(_ value: String) -> String {
        EmailReaderText.decodingHTML(value)
            .lowercased()
            .filter { !$0.isWhitespace }
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
        let structured = restoringCompactedHeaderLines(in: normalized)
        guard structured.count > 180, normalized.contains("\n") == false else {
            return structured
        }

        return structured
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

    /// Older local rows were stored after signal normalization collapsed every
    /// line. Recover obvious RFC-style header runs without changing ordinary
    /// one-line messages that happen to contain a single "Subject:" token.
    private static func restoringCompactedHeaderLines(in value: String) -> String {
        guard value.filter({ $0 == "\n" }).count < 2,
              let regex = try? NSRegularExpression(
                pattern: #"(?i)(?:^|\s)(From|Sent|Date|To|Cc|Bcc|Reply-To|Subject|Message-ID|Content-Type):\s"#
              ) else {
            return value
        }

        let range = NSRange(value.startIndex..<value.endIndex, in: value)
        let labels = Set(regex.matches(in: value, range: range).compactMap { match -> String? in
            guard match.numberOfRanges > 1,
                  let labelRange = Range(match.range(at: 1), in: value) else {
                return nil
            }
            return value[labelRange].lowercased()
        })
        guard labels.count >= 4,
              labels.contains("from"),
              labels.contains("subject"),
              labels.contains("to"),
              labels.contains("message-id") || labels.contains("content-type") else {
            return value
        }

        var formatted = value.replacingOccurrences(
            of: #"(?i)\h+(?=(?:From|Sent|Date|To|Cc|Bcc|Reply-To|Subject|Message-ID|Content-Type):\h)"#,
            with: "\n",
            options: .regularExpression
        )
        formatted = formatted.replacingOccurrences(
            of: #"\n(?=From:\h)"#,
            with: "\n\n",
            options: [.regularExpression, .caseInsensitive]
        )
        formatted = formatted.replacingOccurrences(
            of: #"(?i)(Content-Type:\h*[^\s;]+(?:\h*;\h*charset\h*=\h*(?:\"[^\"]*\"|'[^']*'|[^\s]+))?)\h+"#,
            with: "$1\n\n",
            options: .regularExpression
        )
        return formatted
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
            if tableCount == 1,
               tableTagCount >= 4,
               styleCount + classCount >= 4,
               sourceLength > max(900, visibleTextLength * 2) {
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
                    Text("\(expanded ? "Hide" : "Show") \(title)")
                        .font(EmailReaderTypography.metadata(weight: .medium))
                }
                .foregroundStyle(ElectronicMailDesign.appleBlue)
                .padding(.vertical, 3)
                .contentShape(Rectangle())
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

private struct EmailQuotedContentButton: View {
    let expanded: Bool
    let colorScheme: ColorScheme
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 7) {
                Image(systemName: expanded ? "chevron.up" : "ellipsis")
                    .font(.system(size: 11, weight: .semibold))
                    .frame(width: 14)

                Text(expanded ? "Hide quoted content" : "Show quoted content")
                    .font(EmailReaderTypography.metadata(weight: .medium))
            }
            .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .background(
                Capsule()
                    .fill(ElectronicMailDesign.controlFill(for: colorScheme))
            )
            .overlay {
                Capsule()
                    .stroke(ElectronicMailDesign.panelBorder(for: colorScheme), lineWidth: 0.75)
            }
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .help(expanded ? "Hide the earlier messages quoted inside this email" : "Show the earlier messages quoted inside this email")
        .accessibilityLabel(expanded ? "Hide quoted content" : "Show quoted content")
    }
}

private struct EmailOriginalBodyView: View {
    let preparedDocument: EmailHTMLPreparedDocument
    let fallbackText: String
    let threadID: String
    let messageID: String?
    let colorScheme: ColorScheme
    let renderRevision: UInt64
    let presentationMode: String

    var body: some View {
        EmailHTMLBodyView(
            preparedDocument: preparedDocument,
            fallbackText: fallbackText,
            threadID: threadID,
            messageID: messageID,
            colorScheme: colorScheme,
            renderRevision: renderRevision
        )
        .id("\(renderRevision)-\(colorScheme == .dark ? "dark" : "light")-\(presentationMode)")
        .background(ElectronicMailDesign.background(for: colorScheme))
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
                        colorScheme: colorScheme,
                        contentHeight: $contentHeight
                    ) { result in
                        if result.fallbackReason != nil {
                            logRuntimeFallback(result)
                            runtimeFallbackReason = result.fallbackReason
                        }
                    }
                    .frame(maxWidth: .infinity)
                    .frame(height: max(EmailReaderMetrics.htmlBodyMinHeight, contentHeight))
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

enum EmailHTMLDarkModePolicy {
    static var userScript: WKUserScript {
        WKUserScript(
            source: source,
            injectionTime: .atDocumentEnd,
            forMainFrameOnly: true
        )
    }

    // Sender-authored JavaScript remains disabled. This trusted app script only
    // adapts bright HTML surfaces and dark text after the message has rendered.
    static let source = """
    (() => {
      const rootStyle = getComputedStyle(document.documentElement);
      if (rootStyle.getPropertyValue('--electronic-mail-dark-mode').trim() !== '1') return;

      const parseColor = (value) => {
        const parts = String(value || '').match(/[\\d.]+/g);
        if (!parts || parts.length < 3) return null;
        return {
          red: Number(parts[0]),
          green: Number(parts[1]),
          blue: Number(parts[2]),
          alpha: parts.length > 3 ? Number(parts[3]) : 1
        };
      };
      const luminance = (color) => (
        (0.2126 * color.red) + (0.7152 * color.green) + (0.0722 * color.blue)
      ) / 255;
      const hasOwnText = (element) => Array.from(element.childNodes || []).some((node) => (
        node.nodeType === Node.TEXT_NODE && String(node.textContent || '').trim().length > 0
      ));
      const isArtwork = (element) => {
        const tag = String(element.tagName || '').toLowerCase();
        return ['img', 'picture', 'video', 'canvas', 'svg'].includes(tag) || Boolean(element.closest('svg'));
      };
      const setImportant = (element, property, value) => {
        element.style.setProperty(property, value, 'important');
      };

      const elements = [document.documentElement, document.body]
        .concat(Array.from(document.body ? document.body.querySelectorAll('*') : []))
        .filter(Boolean)
        .slice(0, 10000);

      elements.forEach((element) => {
        if (isArtwork(element)) return;
        const style = getComputedStyle(element);
        const background = parseColor(style.backgroundColor);
        if (background && background.alpha > 0.02) {
          const backgroundLuminance = luminance(background);
          if (backgroundLuminance > 0.92) {
            setImportant(element, 'background-color', '#1c1c1e');
          } else if (backgroundLuminance > 0.78) {
            setImportant(element, 'background-color', '#242426');
          }
        }

        if (hasOwnText(element)) {
          const foreground = parseColor(style.color);
          if (foreground && foreground.alpha > 0.02) {
            const foregroundLuminance = luminance(foreground);
            if (foregroundLuminance < 0.38) {
              const nextColor = element.closest('a') ? '#64a8ff' : '#f5f5f7';
              setImportant(element, 'color', nextColor);
              setImportant(element, '-webkit-text-fill-color', nextColor);
            } else if (foregroundLuminance < 0.62) {
              setImportant(element, 'color', '#d1d1d6');
              setImportant(element, '-webkit-text-fill-color', '#d1d1d6');
            }
          }
        }

        ['border-top-color', 'border-right-color', 'border-bottom-color', 'border-left-color']
          .forEach((property) => {
            const border = parseColor(style.getPropertyValue(property));
            if (!border || border.alpha <= 0.02) return;
            const borderLuminance = luminance(border);
            if (borderLuminance < 0.25 || borderLuminance > 0.78) {
              setImportant(element, property, '#48484a');
            }
          });
      });

      setImportant(document.documentElement, 'background-color', 'transparent');
      if (document.body) setImportant(document.body, 'background-color', 'transparent');
    })();
    """
}

private struct EmailHTMLWebView: NSViewRepresentable {
    @Environment(\.electronicMailReaderFadeTopInset) private var readerFadeTopInset
    let html: String
    let colorScheme: ColorScheme
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
        configuration.userContentController.addUserScript(EmailHTMLDarkModePolicy.userScript)
        configuration.userContentController.addUserScript(Coordinator.contentSizeUserScript)

        let webView = EmailScrollPassthroughWebView(frame: .zero, configuration: configuration)
        webView.readerFadeTopInset = readerFadeTopInset
        updateAppearance(of: webView)
        webView.navigationDelegate = context.coordinator
        webView.setValue(false, forKey: "drawsBackground")
        return webView
    }

    func updateNSView(_ webView: WKWebView, context: Context) {
        context.coordinator.onRenderResult = onRenderResult
        (webView as? EmailScrollPassthroughWebView)?.readerFadeTopInset = readerFadeTopInset
        updateAppearance(of: webView)
        guard context.coordinator.currentHTML != html else {
            return
        }
        context.coordinator.currentHTML = html
        webView.loadHTMLString(html, baseURL: nil)
    }

    private func updateAppearance(of webView: WKWebView) {
        let name: NSAppearance.Name = colorScheme == .dark ? .darkAqua : .aqua
        if webView.appearance?.name != name {
            webView.appearance = NSAppearance(named: name)
        }
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
    var readerFadeTopInset = ElectronicMailControlMetrics.readerScrollFadeTopInset {
        didSet {
            guard abs(readerFadeTopInset - oldValue) > 0.25 else { return }
            updateReaderFadeMask()
        }
    }
    private weak var parentScrollView: NSScrollView?
    private var parentBoundsObserver: NSObjectProtocol?
    private var initialParentBoundsY: CGFloat?
    private weak var readerFadeOverlay: EmailReaderFadeOverlayView?

    override func viewDidMoveToSuperview() {
        super.viewDidMoveToSuperview()
        if window != nil {
            installParentScrollObservation()
        }
    }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        installParentScrollObservation()
    }

    override func layout() {
        super.layout()
        if parentScrollView == nil, window != nil {
            installParentScrollObservation()
        }
        updateReaderFadeMask()
    }

    deinit {
        removeParentScrollObservation()
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

    private func installParentScrollObservation() {
        removeParentScrollObservation()
        guard window != nil, let scrollView = enclosingScrollView() else {
            parentScrollView = nil
            initialParentBoundsY = nil
            layer?.mask = nil
            readerFadeOverlay?.isHidden = true
            return
        }

        parentScrollView = scrollView
        let clipView = scrollView.contentView
        readerFadeOverlay = EmailReaderFadeOverlayView.install(in: clipView)
        initialParentBoundsY = clipView.bounds.origin.y
        clipView.postsBoundsChangedNotifications = true
        parentBoundsObserver = NotificationCenter.default.addObserver(
            forName: NSView.boundsDidChangeNotification,
            object: clipView,
            queue: .main
        ) { [weak self] _ in
            self?.updateReaderFadeMask()
        }
        updateReaderFadeMask()
    }

    private func removeParentScrollObservation() {
        if let parentBoundsObserver {
            NotificationCenter.default.removeObserver(parentBoundsObserver)
        }
        parentBoundsObserver = nil
    }

    private func updateReaderFadeMask() {
        guard let scrollView = parentScrollView, window != nil else {
            layer?.mask = nil
            readerFadeOverlay?.isHidden = true
            return
        }

        let clipView = scrollView.contentView
        let initialBoundsY = initialParentBoundsY ?? clipView.bounds.origin.y
        let upwardTravel = abs(clipView.bounds.origin.y - initialBoundsY)
        let progress = ElectronicMailReaderScrollFade.progress(
            forContentTop: -upwardTravel
        )
        let overlay = readerFadeOverlay ?? EmailReaderFadeOverlayView.install(in: clipView)
        readerFadeOverlay = overlay
        overlay.update(progress: progress, in: clipView, topInset: readerFadeTopInset)

        // WKWebView's remote content can bypass a SwiftUI or ancestor layer
        // mask. The native overlay above the clip view is the authoritative
        // fade; clear any stale mask left by an earlier render.
        layer?.mask = nil
    }
}

private final class EmailReaderFadeOverlayView: NSView {
    private static let sharedIdentifier = NSUserInterfaceItemIdentifier(
        "electronic-mail-reader-fade-overlay"
    )
    private let gradientLayer = CAGradientLayer()

    static func install(in clipView: NSClipView) -> EmailReaderFadeOverlayView {
        let container = clipView.enclosingScrollView?.superview
            ?? clipView.superview
            ?? clipView
        if let existing = container.subviews.first(where: {
            $0.identifier == sharedIdentifier
        }) as? EmailReaderFadeOverlayView {
            container.addSubview(existing, positioned: .above, relativeTo: nil)
            return existing
        }

        let overlay = EmailReaderFadeOverlayView(frame: .zero)
        overlay.identifier = sharedIdentifier
        container.addSubview(overlay, positioned: .above, relativeTo: nil)
        return overlay
    }

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        wantsLayer = true
        layer = gradientLayer
        gradientLayer.isGeometryFlipped = false
        gradientLayer.startPoint = CGPoint(x: 0.5, y: 0)
        gradientLayer.endPoint = CGPoint(x: 0.5, y: 1)
        gradientLayer.locations = [0, 1]
        isHidden = true
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func hitTest(_ point: NSPoint) -> NSView? {
        nil
    }

    func update(progress: CGFloat, in clipView: NSClipView, topInset: CGFloat) {
        let clampedProgress = min(1, max(0, progress))
        guard clampedProgress > 0 else {
            isHidden = true
            return
        }

        guard let container = superview else {
            isHidden = true
            return
        }
        let viewport = clipView.convert(clipView.bounds, to: container)
        let height = min(
            ElectronicMailControlMetrics.readerScrollFadeHeight,
            viewport.height
        )
        // AppKit views normally measure upward from the bottom, while flipped
        // containers measure downward from the top. Anchor the overlay to the
        // visual top in either coordinate system, then place it immediately
        // beneath the fixed reader header.
        let y = container.isFlipped
            ? viewport.minY + topInset
            : viewport.maxY
                - height
                - topInset
        frame = NSRect(
            x: viewport.minX,
            y: y,
            width: viewport.width,
            height: height
        )

        let background = effectiveAppearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
            ? NSColor.black
            : NSColor.white
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        gradientLayer.frame = bounds
        gradientLayer.colors = [
            background.withAlphaComponent(clampedProgress).cgColor,
            background.withAlphaComponent(0).cgColor,
        ]
        CATransaction.commit()
        isHidden = false
    }
}

enum ElectronicMailReaderWebFade {
    static func alpha(
        atWindowY y: CGFloat,
        fadeTop: CGFloat,
        fadeBottom: CGFloat,
        progress: CGFloat
    ) -> CGFloat {
        let clampedProgress = min(1, max(0, progress))
        let baseAlpha: CGFloat
        if y >= fadeTop {
            baseAlpha = 0
        } else if y <= fadeBottom {
            baseAlpha = 1
        } else {
            baseAlpha = (fadeTop - y) / max(1, fadeTop - fadeBottom)
        }
        return 1 - clampedProgress * (1 - baseAlpha)
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

struct EmailReaderInitialMessageTarget: Equatable {
    let messageKey: EmailThreadPresentationItem.ID
    let explicitlyFocused: Bool
}

enum EmailReaderInitialMessagePolicy {
    static func target(
        focusedMessageID: String?,
        in presentation: EmailThreadPresentationSnapshot
    ) -> EmailReaderInitialMessageTarget? {
        if let focusedMessageID,
           let focused = presentation.items.first(where: {
               $0.message.id == focusedMessageID
           }) {
            return EmailReaderInitialMessageTarget(
                messageKey: focused.id,
                explicitlyFocused: true
            )
        }

        guard let latestMessageKey = presentation.latestMessageKey else {
            return nil
        }
        return EmailReaderInitialMessageTarget(
            messageKey: latestMessageKey,
            explicitlyFocused: false
        )
    }
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

struct EmailHTMLConversationPresentation: Equatable, Sendable {
    let primaryHTML: String
    let originalHTML: String
    let hasHiddenQuotedContent: Bool
}

enum EmailHTMLConversationPolicy {
    static let presentationVersion = 2

    private static let namedQuoteContainerPattern = #"(?is)<(?:div|blockquote|section|table)\b[^>]*(?:id|class)\s*=\s*["'][^"']*(?:gmail_quote|yahoo_quoted|protonmail_quote|moz-cite-prefix|moz-forward-container|replyForwardMsg|divRplyFwdMsg)[^"']*["'][^>]*>"#
    private static let citeBlockquotePattern = #"(?is)<blockquote\b[^>]*\btype\s*=\s*(?:["']\s*)?cite\b[^>]*>"#
    private static let genericBlockquotePattern = #"(?is)<blockquote\b[^>]*>"#
    private static let outlookBoundaryPattern = #"(?is)<div\b[^>]*\bstyle\s*=\s*["'][^"']*border-top\s*:\s*solid[^"']*["'][^>]*>"#
    private static let originalMessageMarkerPattern = #"(?is)(?:-{2,}\s*)?(?:begin\s+)?(?:original|forwarded)\s+message\s*:?(?:\s*-{2,})?"#

    static func presentation(
        from html: String,
        quoteDetected: Bool?
    ) -> EmailHTMLConversationPresentation {
        let original = html.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !original.isEmpty, quoteDetected != false else {
            return unmodified(original)
        }

        var candidates: [String.Index] = []
        appendFirstMatch(namedQuoteContainerPattern, in: original, to: &candidates)
        appendFirstMatch(citeBlockquotePattern, in: original, to: &candidates)
        appendFirstMatch(outlookBoundaryPattern, in: original, to: &candidates, requiringOutlookHeaders: true)
        appendFirstMatch(originalMessageMarkerPattern, in: original, to: &candidates)

        if quoteDetected == true {
            appendFirstMatch(genericBlockquotePattern, in: original, to: &candidates)
        }

        guard let quoteStart = candidates
            .sorted()
            .first(where: { hasVisiblePrimaryContent(in: original, before: $0) }) else {
            return unmodified(original)
        }

        let bodyEnd = original.range(
            of: #"(?is)</body\s*>"#,
            options: [.regularExpression, .backwards]
        )?.lowerBound
        let suffixStart = bodyEnd.map { max($0, quoteStart) } ?? original.endIndex
        let collapsed = String(original[..<quoteStart])
            + "\n<!-- Electronic Mail collapsed quoted conversation history. -->\n"
            + String(original[suffixStart...])

        guard collapsed != original else {
            return unmodified(original)
        }
        return EmailHTMLConversationPresentation(
            primaryHTML: collapsed,
            originalHTML: original,
            hasHiddenQuotedContent: true
        )
    }

    private static func unmodified(_ html: String) -> EmailHTMLConversationPresentation {
        EmailHTMLConversationPresentation(
            primaryHTML: html,
            originalHTML: html,
            hasHiddenQuotedContent: false
        )
    }

    private static func appendFirstMatch(
        _ pattern: String,
        in html: String,
        to candidates: inout [String.Index],
        requiringOutlookHeaders: Bool = false
    ) {
        guard let range = html.range(of: pattern, options: .regularExpression) else {
            return
        }
        if requiringOutlookHeaders,
           !containsOutlookHeaderSequence(in: html, after: range.lowerBound) {
            return
        }
        candidates.append(range.lowerBound)
    }

    private static func containsOutlookHeaderSequence(
        in html: String,
        after start: String.Index
    ) -> Bool {
        let end = html.index(start, offsetBy: 6_000, limitedBy: html.endIndex) ?? html.endIndex
        let text = visibleText(in: String(html[start..<end])).lowercased()
        let fields = ["from:", "sent:", "to:", "subject:"]
        var cursor = text.startIndex
        for field in fields {
            guard let range = text.range(of: field, range: cursor..<text.endIndex) else {
                return false
            }
            cursor = range.upperBound
        }
        return true
    }

    private static func hasVisiblePrimaryContent(
        in html: String,
        before quoteStart: String.Index
    ) -> Bool {
        let bodyStart = html.range(of: #"(?is)<body\b[^>]*>"#, options: .regularExpression)?.upperBound
            ?? html.startIndex
        guard bodyStart < quoteStart else {
            return false
        }
        let prefix = String(html[bodyStart..<quoteStart])
        let visibleCharacters = visibleText(in: prefix).filter { !$0.isWhitespace }
        return visibleCharacters.count >= 2
            || prefix.range(of: #"(?is)<img\b[^>]*>"#, options: .regularExpression) != nil
    }

    private static func visibleText(in html: String) -> String {
        let withoutHiddenBlocks = html
            .replacingOccurrences(
                of: #"(?is)<!--.*?-->"#,
                with: " ",
                options: .regularExpression
            )
            .replacingOccurrences(
                of: #"(?is)<\s*(script|style)\b[^>]*>.*?</\s*\1\s*>"#,
                with: " ",
                options: .regularExpression
            )
        let withBreaks = withoutHiddenBlocks
            .replacingOccurrences(
                of: #"(?is)<\s*br\s*/?\s*>"#,
                with: "\n",
                options: .regularExpression
            )
            .replacingOccurrences(
                of: #"(?is)</\s*(div|p|tr|table|li|h[1-6])\s*>"#,
                with: "\n",
                options: .regularExpression
            )
        return EmailReaderText.decodingHTML(
            withBreaks.replacingOccurrences(
                of: #"(?is)<[^>]+>"#,
                with: " ",
                options: .regularExpression
            )
        )
    }
}

enum EmailHTMLDocument {
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
      --electronic-mail-dark-mode: \(colorScheme == .dark ? "1" : "0");
    }
    html, body {
      -webkit-text-size-adjust: 100%;
      background: transparent !important;
      box-sizing: border-box;
      max-width: 100%;
    }
    body {
      margin: 0;
      min-width: 0;
      overflow-wrap: anywhere;
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif;
      font-size: 15px;
      line-height: 1.45;
      color: \(textColor);
    }
    body > table,
    body > center > table,
    body > div[width],
    body > div[style*="width" i],
    body > div[style*="max-width" i] {
      margin-left: auto !important;
      margin-right: auto !important;
    }
    table {
      max-width: 100% !important;
    }
    img {
      max-width: 100% !important;
      height: auto !important;
    }
    pre {
      max-width: 100%;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
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

struct EmailMessageGroupingDetails: Equatable {
    let explanation: String?
    let supportingFactors: [String]
    let conflictingFactors: [String]
    let isLoading: Bool
}

struct EmailMessageCard: View {
    let threadID: String
    let message: ThreadMessage
    let expanded: Bool
    let allowsCollapse: Bool
    let currentUserDisplayName: String?
    let currentUserEmail: String?
    let colorScheme: ColorScheme
    let mailboxLabel: MailboxLabel
    let onRespond: (MailComposerMode, String) -> Void
    let onThreadAction: (GmailThreadAction, String?) -> Void
    let onOpenAttachment: (ThreadAttachment, String) -> Void
    let isAttachmentDownloading: (ThreadAttachment, String) -> Bool
    let groupingDetails: EmailMessageGroupingDetails?
    let onOpenDetails: (() -> Void)?
    let onToggle: () -> Void

    @State private var detailsExpanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            messageHeader

            EmailMessageBodyDisclosure(
                expanded: expanded,
                initialMeasurementDelay: initialDisclosureMeasurementDelay
            ) {
                messageDisclosureContent
            }
        }
        .frame(maxWidth: .infinity, alignment: .topLeading)
        .onChange(of: expanded) { _, isExpanded in
            if !isExpanded {
                detailsExpanded = false
            }
        }
        .onChange(of: detailsExpanded) { _, isExpanded in
            if isExpanded {
                onOpenDetails?()
            }
        }
    }

    private var messageHeader: some View {
        EmailMessageHeader(
            message: message,
            expanded: expanded,
            allowsCollapse: allowsCollapse,
            currentUserDisplayName: currentUserDisplayName,
            currentUserEmail: currentUserEmail,
            colorScheme: colorScheme,
            mailboxLabel: mailboxLabel,
            detailsExpanded: $detailsExpanded,
            onRespond: onRespond,
            onThreadAction: onThreadAction,
            onToggle: onToggle
        )
    }

    private var messageDisclosureContent: some View {
        VStack(alignment: .leading, spacing: 0) {
            if detailsExpanded {
                EmailMessageDetailsStrip(
                    message: message,
                    currentUserDisplayName: currentUserDisplayName,
                    currentUserEmail: currentUserEmail,
                    colorScheme: colorScheme,
                    groupingDetails: groupingDetails
                )
                    .padding(.top, EmailReaderMetrics.messageHeaderToDetails)
                    .padding(.bottom, 16)
            }

            Rectangle()
                .fill(ElectronicMailDesign.readerHairline(for: colorScheme))
                .frame(height: 1)

            EmailBodyContent(
                threadID: bodyThreadID,
                message: message,
                fallbackText: message.body.isEmpty ? EmailReaderText.loadingFullEmail : message.body,
                colorScheme: colorScheme
            )
            .equatable()
            .padding(.top, 18)

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
                .padding(.top, 18)
            }
        }
        .frame(maxWidth: .infinity, alignment: .topLeading)
        .padding(.bottom, EmailReaderMetrics.expandedMessageBottomSpacing)
    }

    private var initialDisclosureMeasurementDelay: TimeInterval {
        let hasHTML = message.htmlRenderDocument?.isEmpty == false || message.htmlBody?.isEmpty == false
        return hasHTML
            ? EmailReaderMetrics.htmlDisclosureMeasurementDelay
            : EmailReaderMetrics.textDisclosureMeasurementDelay
    }

    private var bodyThreadID: String {
        let messageThreadID = message.threadID?.trimmingCharacters(in: .whitespacesAndNewlines)
        return messageThreadID?.isEmpty == false ? messageThreadID! : threadID
    }
}

private struct EmailMessageBodyDisclosure<Content: View>: View {
    let expanded: Bool
    let initialMeasurementDelay: TimeInterval
    let content: Content

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var mounted: Bool
    @State private var measuredHeight: CGFloat = 0
    @State private var revealProgress: CGFloat
    @State private var revealGeneration = 0

    init(
        expanded: Bool,
        initialMeasurementDelay: TimeInterval,
        @ViewBuilder content: () -> Content
    ) {
        self.expanded = expanded
        self.initialMeasurementDelay = initialMeasurementDelay
        self.content = content()
        _mounted = State(initialValue: expanded)
        _revealProgress = State(initialValue: expanded ? 1 : 0)
    }

    var body: some View {
        Group {
            if mounted {
                content
                    .fixedSize(horizontal: false, vertical: true)
                    .background(EmailDisclosureHeightReader())
                    .frame(height: max(0, measuredHeight * revealProgress), alignment: .top)
                    .clipped()
                    .opacity(reduceMotion ? (expanded ? 1 : 0) : min(1, revealProgress * 1.4))
                    .allowsHitTesting(expanded)
                    .accessibilityHidden(!expanded)
            }
        }
        .frame(maxWidth: .infinity, alignment: .topLeading)
        .onPreferenceChange(EmailDisclosureHeightPreferenceKey.self) { nextHeight in
            guard nextHeight.isFinite, nextHeight > 0 else { return }
            measuredHeight = nextHeight
            scheduleRevealIfNeeded()
        }
        .onChange(of: expanded) { _, isExpanded in
            revealGeneration += 1
            if isExpanded {
                mounted = true
                scheduleRevealIfNeeded(
                    after: measuredHeight > 0 ? 0.01 : initialMeasurementDelay
                )
            } else if reduceMotion {
                revealProgress = 0
            } else {
                withAnimation(.smooth(duration: EmailReaderMetrics.disclosureDuration)) {
                    revealProgress = 0
                }
            }
        }
    }

    private func scheduleRevealIfNeeded(after delay: TimeInterval? = nil) {
        guard expanded, mounted, measuredHeight > 0, revealProgress < 1 else { return }
        revealGeneration += 1
        let generation = revealGeneration
        let resolvedDelay = reduceMotion ? 0 : (delay ?? initialMeasurementDelay)
        DispatchQueue.main.asyncAfter(deadline: .now() + resolvedDelay) {
            guard generation == revealGeneration, expanded else { return }
            if reduceMotion {
                revealProgress = 1
            } else {
                withAnimation(.smooth(duration: EmailReaderMetrics.disclosureDuration)) {
                    revealProgress = 1
                }
            }
        }
    }
}

private struct EmailDisclosureHeightReader: View {
    var body: some View {
        GeometryReader { proxy in
            Color.clear.preference(
                key: EmailDisclosureHeightPreferenceKey.self,
                value: proxy.size.height
            )
        }
    }
}

private struct EmailDisclosureHeightPreferenceKey: PreferenceKey {
    static var defaultValue: CGFloat = 0

    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = max(value, nextValue())
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
    let expanded: Bool
    let allowsCollapse: Bool
    let currentUserDisplayName: String?
    let currentUserEmail: String?
    let colorScheme: ColorScheme
    let mailboxLabel: MailboxLabel
    @Binding var detailsExpanded: Bool
    let onRespond: (MailComposerMode, String) -> Void
    let onThreadAction: (GmailThreadAction, String?) -> Void
    let onToggle: () -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            EmailSenderAvatar(
                name: senderName,
                assetID: message.senderAvatarAssetID,
                colorScheme: colorScheme,
                highlighted: isUnread
            )

            VStack(alignment: .leading, spacing: ElectronicMailControlMetrics.readerTwoLineGap) {
                if allowsCollapse {
                    Button(action: onToggle) {
                        senderLine
                    }
                    .buttonStyle(.plain)
                    .help(expanded ? "Collapse email" : "Expand email")
                    .accessibilityLabel("Email from \(senderName), \(EmailReaderText.shortDate(message.receivedAt))")
                    .accessibilityHint(expanded ? "Collapse email" : "Expand email")
                } else {
                    senderLine
                }

                HStack(spacing: 5) {
                    Text(EmailReaderText.fullHeaderDate(message.receivedAt))
                        .font(EmailReaderTypography.metadata())
                        .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                        .lineLimit(1)

                    Button {
                        if !expanded, allowsCollapse {
                            onToggle()
                            detailsExpanded = true
                        } else if reduceMotion {
                            detailsExpanded.toggle()
                        } else {
                            withAnimation(.smooth(duration: EmailReaderMetrics.detailDisclosureDuration)) {
                                detailsExpanded.toggle()
                            }
                        }
                    } label: {
                        HStack(spacing: 3) {
                            Text("Details")
                            Image(systemName: detailsExpanded ? "chevron.up" : "chevron.down")
                                .font(.system(size: 9, weight: .semibold))
                        }
                        .font(EmailReaderTypography.metadata(weight: .medium))
                        .foregroundStyle(
                            ElectronicMailDesign.secondaryText(for: colorScheme)
                                .opacity(ElectronicMailControlMetrics.readerDetailsLabelOpacity)
                        )
                        .padding(.vertical, 3)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .help(detailsExpanded ? "Hide message details" : "Show message details")
                    .accessibilityLabel(detailsExpanded ? "Hide message details" : "Show message details")

                    if !message.attachments.isEmpty {
                        Image(systemName: "paperclip")
                            .font(.system(size: 11, weight: .medium))
                            .foregroundStyle(ElectronicMailDesign.tertiaryText(for: colorScheme))
                            .accessibilityLabel("Has attachments")
                    }
                }
            }

            Spacer(minLength: 16)

            if allowsCollapse {
                Button(action: onToggle) {
                    Image(systemName: "chevron.down")
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                        .frame(width: 28, height: 28)
                        .rotationEffect(.degrees(expanded ? 180 : 0))
                        .contentShape(Circle())
                }
                .buttonStyle(.plain)
                .animation(
                    reduceMotion ? nil : .smooth(duration: EmailReaderMetrics.chevronDuration),
                    value: expanded
                )
                .help(expanded ? "Collapse email" : "Expand email")
                .accessibilityLabel(expanded ? "Collapse email" : "Expand email")
            }
        }
        .frame(
            maxWidth: .infinity,
            minHeight: EmailReaderMetrics.messageHeaderHeight,
            alignment: .leading
        )
        .padding(.vertical, EmailReaderMetrics.messageHeaderVerticalPadding)
    }

    private var senderLine: some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            Text(senderName)
                .font(EmailReaderTypography.messageTitle())
                .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                .lineLimit(1)
                .truncationMode(.tail)

            Text(recipientSummary)
                .font(EmailReaderTypography.metadata())
                .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                .lineLimit(1)
                .layoutPriority(1)
        }
        .contentShape(Rectangle())
    }

    private var isUnread: Bool {
        message.labelIDs.contains(where: { $0.uppercased() == "UNREAD" })
    }

    private var senderName: String {
        EmailReaderText.senderName(message.fromAddress) ?? "Unknown sender"
    }

    private var recipientSummary: String {
        EmailReaderText.recipientLabel(
            message.to,
            currentUserDisplayName: currentUserDisplayName,
            currentUserEmail: currentUserEmail
        )
    }
}

private struct EmailSenderAvatar: View {
    let name: String
    let assetID: String?
    let colorScheme: ColorScheme
    let highlighted: Bool

    @State private var remoteImage: NSImage?

    var body: some View {
        Circle()
            .fill(ElectronicMailDesign.readerAvatarFill(for: colorScheme, highlighted: highlighted))
            .frame(width: EmailReaderMetrics.avatarSize, height: EmailReaderMetrics.avatarSize)
            .overlay {
                if let remoteImage {
                    Image(nsImage: remoteImage)
                        .resizable()
                        .scaledToFill()
                        .frame(width: EmailReaderMetrics.avatarSize, height: EmailReaderMetrics.avatarSize)
                        .clipShape(Circle())
                } else {
                    Text(initial)
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundStyle(Color.white)
                }
            }
            .task(id: assetID) {
                remoteImage = nil
                guard let assetID, !assetID.isEmpty else { return }
                guard let loaded = try? await EmailRemoteImageLoader.shared.load(assetID: assetID),
                      !Task.isCancelled else { return }
                remoteImage = NSImage(data: loaded.data)
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
    let currentUserDisplayName: String?
    let currentUserEmail: String?
    let colorScheme: ColorScheme
    let groupingDetails: EmailMessageGroupingDetails?

    var body: some View {
        VStack(alignment: .leading, spacing: EmailReaderMetrics.detailRowSpacing) {
            addressRow("From", value: message.fromAddress)
            addressRow(
                "To",
                value: message.to,
                preferredDisplayName: recipientDisplayName(for: message.to)
            )
            detailRow("Cc", value: message.cc)
            detailRow("Bcc", value: message.bcc)
            detailRow("Reply-To", value: message.replyTo)
            detailRow("Date", value: EmailReaderText.fullHeaderDate(message.receivedAt))
            ForEach(message.reader?.markers.uniquedPreservingOrder() ?? []) { marker in
                detailRow(marker.label, value: marker.text)
            }
            if let groupingDetails {
                Divider()
                    .padding(.vertical, 4)
                detailRow("AI matter") {
                    groupingExplanation(groupingDetails)
                }
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, EmailReaderMetrics.detailPanelVerticalPadding)
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
    private func groupingExplanation(_ details: EmailMessageGroupingDetails) -> some View {
        if let explanation = clean(details.explanation) {
            VStack(alignment: .leading, spacing: 6) {
                Text(explanation)
                    .font(EmailReaderTypography.metadata())
                    .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                    .fixedSize(horizontal: false, vertical: true)

                ForEach(details.supportingFactors, id: \.self) { factor in
                    Label(factor, systemImage: "checkmark.circle")
                        .font(EmailReaderTypography.metadata())
                        .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                }

                ForEach(details.conflictingFactors, id: \.self) { factor in
                    Label(factor, systemImage: "exclamationmark.circle")
                        .font(EmailReaderTypography.metadata())
                        .foregroundStyle(Color.orange)
                }
            }
        } else {
            HStack(spacing: 7) {
                ProgressView()
                    .controlSize(.small)
                Text(details.isLoading ? "Loading grouping details…" : "Grouping details are unavailable.")
                    .font(EmailReaderTypography.metadata())
                    .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
            }
        }
    }

    @ViewBuilder
    private func detailRow(_ label: String, value: String?) -> some View {
        if let cleanValue = clean(value) {
            detailRow(label) {
                Text(cleanValue)
                    .font(EmailReaderTypography.metadata())
                    .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                    .textSelection(.enabled)
                    .lineLimit(3)
            }
        }
    }

    @ViewBuilder
    private func addressRow(
        _ label: String,
        value: String?,
        preferredDisplayName: String? = nil
    ) -> some View {
        if let cleanValue = clean(value) {
            detailRow(label) {
                addressText(cleanValue, preferredDisplayName: preferredDisplayName)
                    .font(EmailReaderTypography.metadata())
                    .textSelection(.enabled)
                    .lineLimit(3)
            }
        }
    }

    private func detailRow<Content: View>(
        _ label: String,
        @ViewBuilder content: () -> Content
    ) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 14) {
            Text(label)
                .font(EmailReaderTypography.metadata(weight: .semibold))
                .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                .lineLimit(1)
                .fixedSize(horizontal: true, vertical: false)
                .frame(minWidth: Self.minimumLabelColumnWidth, alignment: .leading)

            content()
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func addressText(
        _ rawValue: String,
        preferredDisplayName: String?
    ) -> Text {
        let email = EmailReaderText.emailAddress(rawValue)
        let parsedName = EmailReaderText.senderName(rawValue)
        let displayName = clean(preferredDisplayName)
            ?? ((parsedName?.caseInsensitiveCompare(email ?? "") == .orderedSame) ? nil : parsedName)

        if let displayName, let email {
            return Text(displayName)
                .foregroundColor(ElectronicMailDesign.primaryText(for: colorScheme))
                + Text(" <\(email)>")
                .foregroundColor(ElectronicMailDesign.secondaryText(for: colorScheme))
        }
        if let displayName {
            return Text(displayName)
                .foregroundColor(ElectronicMailDesign.primaryText(for: colorScheme))
        }
        return Text(email ?? rawValue)
            .foregroundColor(ElectronicMailDesign.secondaryText(for: colorScheme))
    }

    private func recipientDisplayName(for rawValue: String?) -> String? {
        guard let recipientEmail = EmailReaderText.emailAddress(rawValue)?.lowercased(),
              let userEmail = clean(currentUserEmail)?.lowercased(),
              recipientEmail == userEmail
        else {
            return nil
        }
        return clean(currentUserDisplayName)
    }

    private func clean(_ value: String?) -> String? {
        let trimmed = value?.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed?.isEmpty == false ? trimmed : nil
    }
}

private struct ReaderMoreActionsMenu: View {
    let colorScheme: ColorScheme
    let mailboxLabel: MailboxLabel
    let isUnread: Bool
    let isStarred: Bool
    let includesResponses: Bool
    let onRespond: (MailComposerMode) -> Void
    let onThreadAction: (GmailThreadAction) -> Void
    var bordered: Bool = false
    @State private var confirmPermanentDelete = false

    var body: some View {
        Menu {
            if includesResponses {
                Button("Reply") { onRespond(.reply) }
                Button("Reply All") { onRespond(.replyAll) }
                Button("Forward") { onRespond(.forward) }
                Divider()
            }

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
                .font(.system(size: 16, weight: .semibold))
                .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                .frame(
                    width: bordered ? EmailReaderMetrics.headerActionHeight : 28,
                    height: bordered ? EmailReaderMetrics.headerActionHeight : 28
                )
                .contentShape(Circle())
        }
        .menuIndicator(.hidden)
        .modifier(ReaderMoreActionsButtonStyle(bordered: bordered))
        .fixedSize()
        .help("More actions")
        .accessibilityLabel("More email actions")
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
}

private struct ReaderMoreActionsButtonStyle: ViewModifier {
    let bordered: Bool

    @ViewBuilder
    func body(content: Content) -> some View {
        if bordered {
            content
                .buttonStyle(.bordered)
                .buttonBorderShape(.circle)
        } else {
            content.menuStyle(.borderlessButton)
        }
    }
}

private struct EmailReaderLoadingCard: View {
    let colorScheme: ColorScheme

    var body: some View {
        HStack(spacing: 12) {
            ProgressView()
                .controlSize(.small)
            Text(EmailReaderText.loadingFullEmail)
                .font(EmailReaderTypography.body())
                .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, minHeight: EmailReaderMetrics.collapsedRowHeight, alignment: .leading)
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
                .padding(.horizontal, 14)
                .frame(minHeight: ElectronicMailControlMetrics.actionHeight)
                .buttonStyle(.bordered)
                .buttonBorderShape(.capsule)
                .font(EmailReaderTypography.body(weight: .semibold))

            Spacer(minLength: 0)
        }
        .padding(.horizontal, 18)
        .frame(minHeight: 62)
        .background(
            RoundedRectangle(cornerRadius: EmailReaderMetrics.cardRadius, style: .continuous)
                .fill(ElectronicMailDesign.readerControlFill(for: colorScheme))
        )
        .overlay {
            RoundedRectangle(cornerRadius: EmailReaderMetrics.cardRadius, style: .continuous)
                .stroke(ElectronicMailDesign.readerControlBorder(for: colorScheme), lineWidth: 1)
        }
    }
}

private enum EmailReaderMetrics {
    static let maxContentWidth = ElectronicMailControlMetrics.readerMaxWidth
    static let horizontalPadding: CGFloat = 36
    static let contentTop = ElectronicMailControlMetrics.readerContentTop
    static let headerActionHeight = ElectronicMailControlMetrics.headerControlSize
    static let threadTitleActionGap: CGFloat = 20
    static let headerToConversation = ElectronicMailControlMetrics.readerHeaderToConversation
    static let collapsedRowHeight: CGFloat = 82
    static let messageHeaderHeight: CGFloat = 66
    static let messageHeaderVerticalPadding: CGFloat = 8
    static let messageHeaderToDetails: CGFloat = 12
    static let detailRowSpacing: CGFloat = 17
    static let detailPanelVerticalPadding: CGFloat = 15
    static let expandedMessageBottomSpacing: CGFloat = 28
    static let avatarSize: CGFloat = 36
    static let actionBubbleHeight = ElectronicMailControlMetrics.actionHeight
    static let actionSectionTopSpacing = ElectronicMailControlMetrics.readerResponseTopSpacing
    static let actionSectionBottomSpacing = ElectronicMailControlMetrics.readerResponseBottomSpacing
    static let cardRadius: CGFloat = 7
    static let htmlBodyMinHeight: CGFloat = 360
    static let maximumImmediateBodyCharacters = 20_000
    static let disclosureDuration: TimeInterval = 0.22
    static let detailDisclosureDuration: TimeInterval = 0.18
    static let chevronDuration: TimeInterval = 0.18
    static let textDisclosureMeasurementDelay: TimeInterval = 0.04
    static let htmlDisclosureMeasurementDelay: TimeInterval = 0.14
}

private enum EmailReaderTypography {
    static func threadTitle() -> Font {
        ElectronicMailReaderType.title()
    }

    static func title(weight: Font.Weight = .bold) -> Font {
        ElectronicMailReaderType.title()
    }

    static func subtitle(weight: Font.Weight = .regular) -> Font {
        ElectronicMailReaderType.body(weight: weight)
    }

    static func section(weight: Font.Weight = .semibold) -> Font {
        ElectronicMailReaderType.metadata(weight: weight)
    }

    static func body(weight: Font.Weight = .regular) -> Font {
        ElectronicMailReaderType.body(weight: weight)
    }

    static func metadata(weight: Font.Weight = .regular) -> Font {
        ElectronicMailReaderType.metadata(weight: weight)
    }

    static func messageTitle(weight: Font.Weight = .semibold) -> Font {
        ElectronicMailReaderType.sender(weight: weight)
    }

    static func marker(weight: Font.Weight = .medium) -> Font {
        ElectronicMailType.status(weight: weight)
    }

    static func actionIcon(weight: Font.Weight = .regular) -> Font {
        .system(size: 16, weight: weight)
    }

}

enum EmailReaderText {
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
            currentUserEmail: currentUserEmail,
            allowsCurrentUserAlias: addresses.count == 1
        ) ?? emailAddress(first) ?? first
        if addresses.count > 1 {
            return "\(display) +\(addresses.count - 1)"
        }
        return display
    }

    static func recipientLabel(
        _ rawValue: String?,
        currentUserDisplayName: String? = nil,
        currentUserEmail: String? = nil
    ) -> String {
        let value = rawValue?.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let value, !value.isEmpty else {
            return "to me"
        }
        let display = recipientSummary(
            value,
            currentUserDisplayName: currentUserDisplayName,
            currentUserEmail: currentUserEmail
        )
        return display.isEmpty ? "to me" : "to \(display)"
    }

    private static func recipientDisplayName(
        _ rawValue: String,
        currentUserDisplayName: String?,
        currentUserEmail: String?,
        allowsCurrentUserAlias: Bool
    ) -> String? {
        let rawEmail = emailAddress(rawValue)?.lowercased()
        let userEmail = currentUserEmail?
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased()
        let parsedName = parsedDisplayName(rawValue)
        if let rawEmail,
           let userEmail,
           rawEmail == userEmail,
           let name = cleanDisplayName(currentUserDisplayName) {
            return name
        }
        if allowsCurrentUserAlias,
           rawEmail != nil,
           parsedName == nil,
           let name = cleanDisplayName(currentUserDisplayName) {
            return name
        }
        return parsedName
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

    static func fullHeaderDate(_ value: String) -> String {
        guard let date = date(from: value) else {
            return value
        }
        if Calendar.current.isDateInToday(date) {
            return "Today, \(timeFormatter.string(from: date))"
        }
        return fullDateFormatter.string(from: date)
    }

    static func timeOnly(_ value: String) -> String {
        guard let date = date(from: value) else {
            return value
        }
        return timeFormatter.string(from: date)
    }

    static func dayGrouping(_ value: String) -> String {
        guard let date = date(from: value) else {
            return value
        }
        if Calendar.current.isDateInToday(date) {
            return "Today"
        }
        if Calendar.current.isDateInYesterday(date) {
            return "Yesterday"
        }
        return dayGroupingFormatter.string(from: date)
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
        formatter.dateFormat = "MMM d, h:mm a"
        return formatter
    }()

    private static let shortDateFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "MMM d"
        return formatter
    }()

    private static let dayGroupingFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "MMM d, yyyy"
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
        currentUserDisplayName: "Gaurav Pandey",
        currentUserEmail: "yednapg@gmail.com",
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
