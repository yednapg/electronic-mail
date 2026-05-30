import AppKit
import SwiftUI

public struct InboxView: View {
    @Environment(\.colorScheme) private var colorScheme
    @ObservedObject private var store: InboxStore
    private let onCompose: () -> Void
    private let onReply: (String) -> Void
    @State private var scrollView: NSScrollView?
    @State private var armedRowID: String?

    public init(
        store: InboxStore,
        onCompose: @escaping () -> Void = {},
        onReply: @escaping (String) -> Void = { _ in }
    ) {
        self.store = store
        self.onCompose = onCompose
        self.onReply = onReply
    }

    public var body: some View {
        GeometryReader { proxy in
            let metrics = InboxLayoutMetrics(windowSize: proxy.size)

            ZStack {
                ElectronicMailDesign.background(for: colorScheme)
                    .ignoresSafeArea()

                if let readerThreadID = store.readerThreadID {
                    EmailReaderView(
                        threadID: readerThreadID,
                        focusedMessageID: store.readerFocusedMessageID,
                        thread: store.readerThread,
                        row: store.readerRow,
                        errorMessage: store.readerError,
                        colorScheme: colorScheme,
                        onRetry: {
                            Task {
                                await store.prefetchThread(threadID: readerThreadID, force: true, silent: false)
                            }
                        },
                        onReply: { onReply(readerThreadID) }
                    )
                    .transition(.opacity)
                } else {
                    inboxList(metrics: metrics)
                        .transition(.opacity)
                }

                if !store.navigationPlaceholderVisible, store.readerThreadID == nil {
                    InboxScreenTitle(title: store.mailboxTitle, colorScheme: colorScheme)
                        .zIndex(3)
                }

                if !store.navigationPlaceholderVisible, store.readerThreadID == nil {
                    InboxHeaderActions(
                        isSyncing: store.manualSyncInProgress,
                        colorScheme: colorScheme,
                        onCompose: onCompose,
                        onSync: {
                            Task {
                                await store.syncNow()
                            }
                        }
                    )
                    .zIndex(3)
                }

                InboxKeyboardEventCapture(
                    onMove: { delta in
                        guard store.readerThreadID == nil else {
                            return
                        }
                        moveSelection(delta: delta, metrics: metrics)
                    },
                    onOpen: {
                        guard store.readerThreadID == nil else {
                            return
                        }
                        withAnimation(.easeInOut(duration: 0.16)) {
                            store.openActiveSelection()
                        }
                    },
                    onEscape: {
                        guard store.readerThreadID != nil else {
                            return
                        }
                        closeReader()
                    },
                    onDelete: { permanently in
                        Task {
                            await store.performSelectedThreadAction(permanently ? .deleteForever : .moveTrash)
                        }
                    }
                )
                .frame(width: 1, height: 1)
                .opacity(0.01)

                if store.refreshFailed {
                    ElectronicMailRefreshFailureToast(message: "Inbox could not refresh. Showing last saved state.")
                }
            }
        }
        .task {
            if store.session == nil {
                await store.load()
            }
        }
        .environment(\.font, .system(.body, design: .rounded))
    }

    private func inboxList(metrics: InboxLayoutMetrics) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Spacer(minLength: 0)
                .frame(height: ElectronicMailShellMetrics.contentTop)

            ZStack(alignment: .topLeading) {
                ScrollView(.vertical, showsIndicators: false) {
                    VStack(alignment: .leading, spacing: 0) {
                        ForEach(Array(store.sections.enumerated()), id: \.element.id) { indexedSection in
                            InboxSectionView(
                                section: indexedSection.element,
                                isFirstSection: indexedSection.offset == 0,
                                metrics: metrics,
                                colorScheme: colorScheme,
                                onActivate: { row in
                                    activate(row: row)
                                },
                                onToggleExpansion: { threadID in
                                    store.toggleExpansion(threadID: threadID)
                                }
                            )
                        }

                        if let footer = store.mailboxFooter {
                            InboxFooterView(
                                text: footer.text,
                                canLoadMore: footer.canLoadMore,
                                isLoading: store.mailboxPageLoading,
                                metrics: metrics,
                                colorScheme: colorScheme,
                                onLoadMore: {
                                    Task {
                                        await store.loadMoreMailbox()
                                    }
                                }
                            )
                            .onAppear {
                                guard footer.canLoadMore else {
                                    return
                                }
                                Task {
                                    await store.loadMoreMailbox(automatic: true)
                                }
                            }
                        }
                    }
                    .padding(.bottom, ElectronicMailTypography.bodyLineHeight)
                    .transaction { transaction in
                        transaction.disablesAnimations = true
                    }
                }
                .background(
                    InboxScrollViewAccessor { scrollView in
                        self.scrollView = scrollView
                    }
                )
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }

    private func activate(row: InboxRowViewModel) {
        guard armedRowID == row.id || row.isSelected else {
            armedRowID = row.id
            select(row: row)
            return
        }

        armedRowID = nil
        withAnimation(.easeInOut(duration: 0.16)) {
            _ = store.openReader(threadID: row.threadID, focusedMessageID: row.focusedMessageID)
        }
    }

    private func closeReader() {
        armedRowID = nil
        withAnimation(.easeInOut(duration: 0.16)) {
            store.closeReader()
        }
    }

    private func moveSelection(delta: Int, metrics: InboxLayoutMetrics) {
        let rows = store.flatRows
        guard !rows.isEmpty else {
            return
        }

        let currentID = store.flatRows.first(where: { $0.isSelected })?.id
        let currentIndex = currentID.flatMap { id in
            rows.firstIndex { $0.id == id }
        } ?? -1

        let nextIndex: Int
        if currentIndex < 0 {
            nextIndex = delta >= 0 ? 0 : rows.count - 1
        } else {
            nextIndex = min(max(currentIndex + delta, 0), rows.count - 1)
        }

        let nextRow = rows[nextIndex]
        armedRowID = nextRow.id
        select(row: nextRow)
        scrollThreadIntoKeyboardRange(
            rowID: nextRow.id,
            direction: delta,
            metrics: metrics
        )
    }

    private func select(row: InboxRowViewModel) {
        var transaction = Transaction()
        transaction.disablesAnimations = true
        withTransaction(transaction) {
            store.select(threadID: row.threadID, focusedMessageID: row.focusedMessageID, prefetch: false)
        }
    }

    private func scrollThreadIntoKeyboardRange(
        rowID: String,
        direction: Int,
        metrics: InboxLayoutMetrics
    ) {
        guard
            let scrollView,
            let rowRange = contentYRange(for: rowID, metrics: metrics)
        else {
            return
        }

        let viewportMinY = scrollView.contentView.bounds.minY
        let viewportHeight = metrics.visibleScrollHeight
        guard viewportHeight > 0 else {
            return
        }

        let topLimit = viewportMinY + InboxKeyboardScroll.runwayAbove
        let bottomLimit = viewportMinY + viewportHeight - InboxKeyboardScroll.runwayBelow
        if direction > 0, rowRange.upperBound > bottomLimit {
            scroll(
                scrollView,
                to: rowRange.upperBound - viewportHeight + InboxKeyboardScroll.runwayBelow,
                metrics: metrics
            )
        } else if direction < 0, rowRange.lowerBound < topLimit {
            scroll(
                scrollView,
                to: rowRange.lowerBound - InboxKeyboardScroll.runwayAbove,
                metrics: metrics
            )
        }
    }

    private func scroll(_ scrollView: NSScrollView, to requestedY: CGFloat, metrics: InboxLayoutMetrics) {
        let viewportHeight = metrics.visibleScrollHeight
        let contentHeight = totalContentHeight(metrics: metrics) + ElectronicMailTypography.bodyLineHeight
        let maxY = max(0, contentHeight - viewportHeight)
        let targetY = min(max(0, requestedY), maxY)
        let currentOrigin = scrollView.contentView.bounds.origin

        guard abs(currentOrigin.y - targetY) > 0.5 else {
            return
        }

        scrollView.contentView.scroll(to: CGPoint(x: currentOrigin.x, y: targetY))
        scrollView.reflectScrolledClipView(scrollView.contentView)
    }

    private func contentYRange(for rowID: String, metrics: InboxLayoutMetrics) -> Range<CGFloat>? {
        var y: CGFloat = 0

        for (index, section) in store.sections.enumerated() {
            y += metrics.sectionHeaderHeight(isFirst: index == 0)

            for row in section.rows {
                let rowStart = y
                let rowEnd = rowStart + ElectronicMailTypography.bodyLineHeight

                if row.id == rowID {
                    return rowStart..<rowEnd
                }

                y = rowEnd
            }
        }

        return nil
    }

    private func totalContentHeight(metrics: InboxLayoutMetrics) -> CGFloat {
        store.sections.enumerated().reduce(CGFloat(0)) { height, indexedSection in
            height
                + metrics.sectionHeaderHeight(isFirst: indexedSection.offset == 0)
                + CGFloat(indexedSection.element.rows.count) * ElectronicMailTypography.bodyLineHeight
        }
    }
}

private enum InboxKeyboardScroll {
    static let runwayAbove = ElectronicMailTypography.bodyLineHeight
    static let runwayBelow = ElectronicMailTypography.bodyLineHeight
}

private struct InboxScreenTitle: View {
    let title: String
    let colorScheme: ColorScheme

    var body: some View {
        Text(title)
            .font(ElectronicMailType.headerTitle())
            .tracking(ElectronicMailTypography.titleTracking)
            .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
            .frame(height: ElectronicMailTypography.bodyLineHeight, alignment: .center)
            .padding(.top, ElectronicMailShellMetrics.navTop)
            .padding(.leading, ElectronicMailShellMetrics.navTextLeading)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .allowsHitTesting(false)
    }
}

private struct InboxHeaderActions: View {
    let isSyncing: Bool
    let colorScheme: ColorScheme
    let onCompose: () -> Void
    let onSync: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            InboxHeaderIconButton(
                symbolName: "square.and.pencil",
                accessibilityLabel: "Compose email",
                colorScheme: colorScheme,
                action: onCompose
            )

            InboxHeaderIconButton(
                symbolName: "arrow.clockwise",
                accessibilityLabel: isSyncing ? "Syncing mailbox" : "Sync mailbox now",
                colorScheme: colorScheme,
                action: onSync
            )
            .disabled(isSyncing)
            .opacity(isSyncing ? 0.45 : 1)
        }
        .padding(.top, ElectronicMailShellMetrics.navTop)
        .padding(.trailing, ElectronicMailShellMetrics.navLeading)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topTrailing)
    }
}

private struct InboxHeaderIconButton: View {
    let symbolName: String
    let accessibilityLabel: String
    let colorScheme: ColorScheme
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: symbolName)
                .font(.system(size: 17, weight: .semibold))
                .foregroundStyle(ElectronicMailDesign.secondaryText(for: colorScheme))
                .frame(width: ElectronicMailShellMetrics.navHitFrame, height: ElectronicMailShellMetrics.navHitFrame)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help(accessibilityLabel)
        .accessibilityLabel(accessibilityLabel)
    }
}

private struct InboxFooterView: View {
    let text: String
    let canLoadMore: Bool
    let isLoading: Bool
    let metrics: InboxLayoutMetrics
    let colorScheme: ColorScheme
    let onLoadMore: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            if isLoading {
                ProgressView()
                    .controlSize(.small)
                    .frame(width: 18, height: 18)
            }

            Button(action: onLoadMore) {
                Text(text)
                    .font(ElectronicMailType.body())
                    .tracking(ElectronicMailTypography.bodyTracking)
                    .foregroundStyle(canLoadMore ? ElectronicMailDesign.appleBlue : ElectronicMailDesign.secondaryText(for: colorScheme))
                    .lineLimit(1)
                    .truncationMode(.tail)
            }
            .buttonStyle(.plain)
            .disabled(!canLoadMore || isLoading)
            .help(canLoadMore ? "Load more emails" : text)
        }
        .frame(
            width: max(0, metrics.windowSize.width - metrics.contentLeading - metrics.dividerTrailing),
            height: ElectronicMailTypography.bodyLineHeight * 2,
            alignment: .leading
        )
        .padding(.leading, metrics.contentLeading)
    }
}

private struct InboxSectionView: View {
    let section: InboxSectionViewModel
    let isFirstSection: Bool
    let metrics: InboxLayoutMetrics
    let colorScheme: ColorScheme
    let onActivate: (InboxRowViewModel) -> Void
    let onToggleExpansion: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            InboxSectionHeader(
                section: section,
                isFirstSection: isFirstSection,
                metrics: metrics,
                colorScheme: colorScheme
            )

            ForEach(section.rows) { row in
                InboxRowView(
                    row: row,
                    metrics: metrics,
                    colorScheme: colorScheme,
                    onSelect: { onActivate(row) },
                    onToggleExpansion: row.isExpandable ? { onToggleExpansion(row.threadID) } : nil
                )
            }
        }
    }
}

private struct InboxSectionHeader: View {
    let section: InboxSectionViewModel
    let isFirstSection: Bool
    let metrics: InboxLayoutMetrics
    let colorScheme: ColorScheme

    var body: some View {
        let topSpacing = metrics.sectionTopSpacing(isFirst: isFirstSection)

        ZStack(alignment: .topLeading) {
            Text(section.title)
                .font(ElectronicMailType.sectionTitle())
                .tracking(ElectronicMailTypography.bodyTracking)
                .foregroundStyle(ElectronicMailDesign.sectionText(for: colorScheme))
                .lineLimit(1)
                .frame(height: ElectronicMailTypography.bodyLineHeight)
                .offset(x: metrics.contentLeading, y: topSpacing)

            Rectangle()
                .fill(ElectronicMailDesign.divider(for: colorScheme))
                .frame(
                    width: max(0, metrics.windowSize.width - metrics.contentLeading - metrics.dividerTrailing),
                    height: 1
                )
                .offset(
                    x: metrics.contentLeading,
                    y: topSpacing + ElectronicMailTypography.bodyLineHeight
                )
        }
        .frame(
            width: metrics.windowSize.width,
            height: topSpacing + ElectronicMailTypography.bodyLineHeight + 3,
            alignment: .topLeading
        )
    }
}

private struct InboxRowView: View {
    let row: InboxRowViewModel
    let metrics: InboxLayoutMetrics
    let colorScheme: ColorScheme
    let onSelect: () -> Void
    let onToggleExpansion: (() -> Void)?

    var body: some View {
        ZStack(alignment: .topLeading) {
            if row.isSelected {
                ElectronicMailDesign.appleBlue
                    .frame(width: metrics.windowSize.width, height: ElectronicMailTypography.bodyLineHeight)
            }

            if row.isExpandable, let onToggleExpansion {
                Button(action: onToggleExpansion) {
                    Image(systemName: row.isExpanded ? "chevron.down.circle" : "chevron.right.circle")
                        .font(ElectronicMailType.icon())
                        .symbolRenderingMode(.monochrome)
                        .foregroundStyle(row.isSelected ? ElectronicMailDesign.selectedText(for: colorScheme) : ElectronicMailDesign.appleBlue)
                        .frame(width: 22, height: 22)
                }
                .buttonStyle(.plain)
                .frame(width: 26, height: ElectronicMailTypography.bodyLineHeight)
                .position(
                    x: metrics.groupedIconX,
                    y: ElectronicMailTypography.bodyLineHeight / 2
                )
            }

            rowText(row.sender, alignment: .leading)
                .frame(
                    width: metrics.senderWidth(isChild: row.isChild),
                    height: ElectronicMailTypography.bodyLineHeight,
                    alignment: .leading
                )
                .clipped()
                .offset(x: metrics.senderLeading(isChild: row.isChild))

            rowText(row.title, alignment: .leading)
                .frame(
                    width: metrics.subjectWidth(isChild: row.isChild),
                    height: ElectronicMailTypography.bodyLineHeight,
                    alignment: .leading
                )
                .clipped()
                .offset(x: metrics.subjectLeading(isChild: row.isChild))

            rowText(row.timeLabel, alignment: .trailing)
                .frame(
                    width: metrics.timeWidth,
                    height: ElectronicMailTypography.bodyLineHeight,
                    alignment: .trailing
                )
                .clipped()
                .offset(x: metrics.timeLeading)
        }
        .frame(width: metrics.windowSize.width, height: ElectronicMailTypography.bodyLineHeight, alignment: .topLeading)
        .contentShape(Rectangle())
        .onTapGesture(perform: onSelect)
    }

    private func rowText(_ value: String, alignment: Alignment) -> some View {
        Text(value)
            .font(ElectronicMailType.body())
            .tracking(ElectronicMailTypography.bodyTracking)
            .foregroundStyle(textColor)
            .lineLimit(1)
            .truncationMode(.tail)
            .multilineTextAlignment(alignment == .trailing ? .trailing : .leading)
    }

    private var textColor: Color {
        if row.isSelected {
            return ElectronicMailDesign.selectedText(for: colorScheme)
        }
        if row.isUnread {
            return ElectronicMailDesign.unreadText(for: colorScheme)
        }
        return ElectronicMailDesign.readText(for: colorScheme)
    }
}

private struct InboxKeyboardEventCapture: NSViewRepresentable {
    let onMove: (Int) -> Void
    let onOpen: () -> Void
    let onEscape: () -> Void
    let onDelete: (Bool) -> Void

    func makeNSView(context: Context) -> KeyView {
        let view = KeyView()
        view.onMove = onMove
        view.onOpen = onOpen
        view.onEscape = onEscape
        view.onDelete = onDelete
        DispatchQueue.main.async {
            view.window?.makeFirstResponder(view)
        }
        return view
    }

    func updateNSView(_ nsView: KeyView, context: Context) {
        nsView.updateHandlers(onMove: onMove, onOpen: onOpen, onEscape: onEscape, onDelete: onDelete)
    }

    final class KeyView: NSView {
        var onMove: ((Int) -> Void)?
        var onOpen: (() -> Void)?
        var onEscape: (() -> Void)?
        var onDelete: ((Bool) -> Void)?

        override var acceptsFirstResponder: Bool {
            true
        }

        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            KeyMonitor.install(
                window: window,
                onMove: onMove,
                onOpen: onOpen,
                onEscape: onEscape,
                onDelete: onDelete
            )
            DispatchQueue.main.async { [weak self] in
                guard let self else {
                    return
                }
                self.window?.makeFirstResponder(self)
            }
        }

        func updateHandlers(onMove: @escaping (Int) -> Void, onOpen: @escaping () -> Void, onEscape: @escaping () -> Void, onDelete: @escaping (Bool) -> Void) {
            self.onMove = onMove
            self.onOpen = onOpen
            self.onEscape = onEscape
            self.onDelete = onDelete
            KeyMonitor.install(window: window, onMove: onMove, onOpen: onOpen, onEscape: onEscape, onDelete: onDelete)
        }

        private enum KeyMonitor {
            static weak var window: NSWindow?
            static var onMove: ((Int) -> Void)?
            static var onOpen: (() -> Void)?
            static var onEscape: (() -> Void)?
            static var onDelete: ((Bool) -> Void)?
            static var monitor: Any?

            static func install(
                window: NSWindow?,
                onMove: ((Int) -> Void)?,
                onOpen: (() -> Void)?,
                onEscape: (() -> Void)?,
                onDelete: ((Bool) -> Void)?
            ) {
                self.window = window
                self.onMove = onMove
                self.onOpen = onOpen
                self.onEscape = onEscape
                self.onDelete = onDelete

                guard monitor == nil else {
                    return
                }

                monitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { event in
                    handle(event)
                }
            }

            private static func handle(_ event: NSEvent) -> NSEvent? {
                guard
                    window?.isKeyWindow == true,
                    !(window?.firstResponder is NSTextView)
                else {
                    return event
                }

                switch event.keyCode {
                case 125:
                    onMove?(1)
                    return nil
                case 126:
                    onMove?(-1)
                    return nil
                case 36, 76:
                    onOpen?()
                    return nil
                case 53:
                    onEscape?()
                    return nil
                case 51:
                    onDelete?(event.modifierFlags.contains(.command))
                    return nil
                default:
                    return event
                }
            }

        }

        override func keyDown(with event: NSEvent) {
            switch event.keyCode {
            case 125:
                onMove?(1)
            case 126:
                onMove?(-1)
            case 36, 76:
                onOpen?()
            case 53:
                onEscape?()
            case 51:
                onDelete?(event.modifierFlags.contains(.command))
            default:
                super.keyDown(with: event)
            }
        }
    }
}

private struct InboxScrollViewAccessor: NSViewRepresentable {
    let onResolve: (NSScrollView) -> Void

    func makeNSView(context: Context) -> ResolverView {
        let view = ResolverView(frame: .zero)
        view.onResolve = onResolve
        view.resolveIfNeeded()
        return view
    }

    func updateNSView(_ nsView: ResolverView, context: Context) {
        nsView.onResolve = onResolve
        if !nsView.hasResolved {
            nsView.resolveIfNeeded()
        }
    }

    final class ResolverView: NSView {
        var onResolve: ((NSScrollView) -> Void)?
        private weak var lastResolvedScrollView: NSScrollView?

        var hasResolved: Bool {
            lastResolvedScrollView != nil
        }

        override func viewDidMoveToSuperview() {
            super.viewDidMoveToSuperview()
            resolveIfNeeded()
        }

        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            resolveIfNeeded()
        }

        func resolveIfNeeded() {
            DispatchQueue.main.async { [weak self] in
                guard let self, let scrollView = self.enclosingScrollView else {
                    return
                }

                guard scrollView !== self.lastResolvedScrollView else {
                    return
                }

                self.lastResolvedScrollView = scrollView
                self.onResolve?(scrollView)
            }
        }
    }
}

private enum ElectronicMailTypography {
    static let titleSize = ElectronicMailType.titleSize
    static let titleTracking = ElectronicMailType.titleTracking
    static let bodySize = ElectronicMailType.bodySize
    static let bodyTracking = ElectronicMailType.bodyTracking
    static let bodyLineHeight = ElectronicMailType.bodyLineHeight
    static let iconSize = ElectronicMailType.iconSize
}

private struct InboxLayoutMetrics {
    let windowSize: CGSize

    let timeWidth: CGFloat = 190
    let senderSubjectGap: CGFloat = 24

    var headerTop: CGFloat {
        ElectronicMailShellMetrics.navTop
    }

    var headerBottom: CGFloat {
        0
    }

    var visibleScrollHeight: CGFloat {
        max(
            0,
            windowSize.height
                - headerTop
                - ElectronicMailTypography.bodyLineHeight
                - headerBottom
        )
    }

    var contentLeading: CGFloat {
        min(max(windowSize.width * 0.075, 96), 132)
    }

    var senderLeading: CGFloat {
        contentLeading
    }

    func senderLeading(isChild: Bool) -> CGFloat {
        senderLeading + (isChild ? 42 : 0)
    }

    var senderWidth: CGFloat {
        min(max(windowSize.width * 0.24, 330), 410)
    }

    func senderWidth(isChild: Bool) -> CGFloat {
        max(0, senderWidth - (isChild ? 42 : 0))
    }

    var subjectLeading: CGFloat {
        senderLeading + senderWidth + senderSubjectGap
    }

    func subjectLeading(isChild: Bool) -> CGFloat {
        subjectLeading + (isChild ? 18 : 0)
    }

    var timeLeading: CGFloat {
        windowSize.width - trailingInset - timeWidth
    }

    var subjectWidth: CGFloat {
        max(0, timeLeading - subjectLeading - 20)
    }

    func subjectWidth(isChild: Bool) -> CGFloat {
        max(0, timeLeading - subjectLeading(isChild: isChild) - 20)
    }

    var trailingInset: CGFloat {
        dividerTrailing
    }

    var dividerTrailing: CGFloat {
        min(max(windowSize.width * 0.055, 76), 120)
    }

    var groupedIconX: CGFloat {
        contentLeading - 24
    }

    func sectionTopSpacing(isFirst: Bool) -> CGFloat {
        isFirst ? 0 : ElectronicMailTypography.bodyLineHeight
    }

    func sectionHeaderHeight(isFirst: Bool) -> CGFloat {
        sectionTopSpacing(isFirst: isFirst) + ElectronicMailTypography.bodyLineHeight + 3
    }
}

#Preview {
    InboxView(store: InboxStore(client: DemoAppClient()))
        .frame(width: 1440, height: 900)
}
