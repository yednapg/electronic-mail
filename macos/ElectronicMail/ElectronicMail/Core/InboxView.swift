import AppKit
import SwiftUI

public struct InboxView: View {
    @Environment(\.colorScheme) private var colorScheme
    @ObservedObject private var store: InboxStore
    @State private var scrollView: NSScrollView?

    public init(store: InboxStore) {
        self.store = store
    }

    public var body: some View {
        GeometryReader { proxy in
            let metrics = InboxLayoutMetrics(windowSize: proxy.size)

            ZStack {
                ElectronicMailDesign.background(for: colorScheme)
                    .ignoresSafeArea()

                VStack(alignment: .leading, spacing: 0) {
                    Spacer(minLength: 0)
                        .frame(height: ElectronicMailShellMetrics.contentTop)

                    ZStack(alignment: .topLeading) {
                        ScrollView(.vertical, showsIndicators: false) {
                            LazyVStack(alignment: .leading, spacing: 0) {
                                ForEach(store.sections) { section in
                                    InboxSectionView(
                                        section: section,
                                        metrics: metrics,
                                        colorScheme: colorScheme,
                                        onSelect: { threadID in
                                            select(threadID: threadID)
                                        }
                                    )
                                }
                            }
                            .padding(.bottom, ElectronicMailTypography.bodyLineHeight)
                        }
                        .background(
                            InboxScrollViewAccessor { scrollView in
                                self.scrollView = scrollView
                            }
                        )

                        InboxKeyboardEventCapture(
                            onMove: { delta in
                                moveSelection(delta: delta, metrics: metrics)
                            },
                            onOpen: { store.openActiveSelection() }
                        )
                        .frame(width: 1, height: 1)
                        .opacity(0.01)
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)

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
        .onAppear {
            store.startLiveRefreshLoop()
        }
        .onDisappear {
            store.stopLiveRefreshLoop()
        }
        .environment(\.font, .system(.body, design: .rounded))
    }

    private func moveSelection(delta: Int, metrics: InboxLayoutMetrics) {
        let rows = store.flatRows
        guard !rows.isEmpty else {
            return
        }

        let currentID = store.selectedThreadID
        let currentIndex = currentID.flatMap { id in
            rows.firstIndex { $0.threadID == id }
        } ?? -1

        let nextIndex: Int
        if currentIndex < 0 {
            nextIndex = delta >= 0 ? 0 : rows.count - 1
        } else {
            nextIndex = min(max(currentIndex + delta, 0), rows.count - 1)
        }

        let nextThreadID = rows[nextIndex].threadID
        select(threadID: nextThreadID)
        scrollThreadIntoKeyboardRange(
            threadID: nextThreadID,
            direction: delta,
            metrics: metrics
        )
    }

    private func select(threadID: String) {
        var transaction = Transaction()
        transaction.disablesAnimations = true
        withTransaction(transaction) {
            store.select(threadID: threadID, prefetch: false)
        }
    }

    private func scrollThreadIntoKeyboardRange(
        threadID: String,
        direction: Int,
        metrics: InboxLayoutMetrics
    ) {
        guard
            let scrollView,
            let rowRange = contentYRange(for: threadID, metrics: metrics)
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

    private func contentYRange(for threadID: String, metrics: InboxLayoutMetrics) -> Range<CGFloat>? {
        var y: CGFloat = 0

        for section in store.sections {
            y += metrics.sectionHeaderHeight(for: section.id)

            for row in section.rows {
                let rowStart = y
                let rowEnd = rowStart + ElectronicMailTypography.bodyLineHeight

                if row.threadID == threadID {
                    return rowStart..<rowEnd
                }

                y = rowEnd
            }
        }

        return nil
    }

    private func totalContentHeight(metrics: InboxLayoutMetrics) -> CGFloat {
        store.sections.reduce(CGFloat(0)) { height, section in
            height
                + metrics.sectionHeaderHeight(for: section.id)
                + CGFloat(section.rows.count) * ElectronicMailTypography.bodyLineHeight
        }
    }
}

private enum InboxKeyboardScroll {
    static let runwayAbove = ElectronicMailTypography.bodyLineHeight
    static let runwayBelow = ElectronicMailTypography.bodyLineHeight
}

private struct InboxHeader: View {
    let metrics: InboxLayoutMetrics
    let navigationPlaceholderVisible: Bool
    let onMenu: () -> Void

    var body: some View {
        HStack(spacing: ElectronicMailShellMetrics.navTitleGap) {
            Button(action: onMenu) {
                ElectronicMailHamburgerIcon()
                    .foregroundStyle(.primary)
                    .frame(width: ElectronicMailShellMetrics.navIconFrame, height: ElectronicMailShellMetrics.navIconFrame)
            }
            .buttonStyle(.plain)
            .help(navigationPlaceholderVisible ? "Hide navigation" : "Show navigation")
            .frame(width: ElectronicMailShellMetrics.navIconFrame, height: ElectronicMailShellMetrics.navIconFrame)

            Text("Inbox")
                .font(ElectronicMailType.headerTitle())
                .tracking(ElectronicMailTypography.titleTracking)
                .foregroundStyle(.primary)
                .frame(height: ElectronicMailTypography.bodyLineHeight, alignment: .center)
        }
        .opacity(navigationPlaceholderVisible ? 0 : 1)
        .accessibilityHidden(navigationPlaceholderVisible)
        .padding(.leading, ElectronicMailShellMetrics.navLeading)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct InboxSectionView: View {
    let section: InboxSectionViewModel
    let metrics: InboxLayoutMetrics
    let colorScheme: ColorScheme
    let onSelect: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            InboxSectionHeader(
                section: section,
                metrics: metrics,
                colorScheme: colorScheme
            )

            ForEach(section.rows) { row in
                InboxRowView(
                    row: row,
                    metrics: metrics,
                    colorScheme: colorScheme,
                    onSelect: { onSelect(row.threadID) }
                )
                .id(row.threadID)
            }
        }
    }
}

private struct InboxSectionHeader: View {
    let section: InboxSectionViewModel
    let metrics: InboxLayoutMetrics
    let colorScheme: ColorScheme

    var body: some View {
        ZStack(alignment: .topLeading) {
            Text(section.title)
                .font(ElectronicMailType.sectionTitle())
                .tracking(ElectronicMailTypography.bodyTracking)
                .foregroundStyle(ElectronicMailDesign.sectionText(for: colorScheme))
                .lineLimit(1)
                .frame(height: ElectronicMailTypography.bodyLineHeight)
                .offset(x: metrics.contentLeading, y: metrics.sectionTopSpacing(for: section.id))

            Rectangle()
                .fill(ElectronicMailDesign.divider(for: colorScheme))
                .frame(
                    width: max(0, metrics.windowSize.width - metrics.contentLeading - metrics.dividerTrailing),
                    height: 1
                )
                .offset(
                    x: metrics.contentLeading,
                    y: metrics.sectionTopSpacing(for: section.id) + ElectronicMailTypography.bodyLineHeight
                )
        }
        .frame(
            width: metrics.windowSize.width,
            height: metrics.sectionTopSpacing(for: section.id) + ElectronicMailTypography.bodyLineHeight + 3,
            alignment: .topLeading
        )
    }
}

private struct InboxRowView: View {
    let row: InboxRowViewModel
    let metrics: InboxLayoutMetrics
    let colorScheme: ColorScheme
    let onSelect: () -> Void

    var body: some View {
        ZStack(alignment: .topLeading) {
            if row.isSelected {
                ElectronicMailDesign.appleBlue
                    .frame(width: metrics.windowSize.width, height: ElectronicMailTypography.bodyLineHeight)
            }

            if row.isGrouped {
                Image(systemName: "chevron.right.circle")
                    .font(ElectronicMailType.icon())
                    .symbolRenderingMode(.monochrome)
                    .foregroundStyle(row.isSelected ? ElectronicMailDesign.selectedText(for: colorScheme) : ElectronicMailDesign.appleBlue)
                    .frame(width: 22, height: 22)
                    .position(
                        x: metrics.groupedIconX,
                        y: ElectronicMailTypography.bodyLineHeight / 2
                    )
            }

            rowText(row.sender, alignment: .leading)
                .frame(
                    width: metrics.senderWidth,
                    height: ElectronicMailTypography.bodyLineHeight,
                    alignment: .leading
                )
                .clipped()
                .offset(x: metrics.senderLeading)

            rowText(row.title, alignment: .leading)
                .frame(
                    width: metrics.subjectWidth,
                    height: ElectronicMailTypography.bodyLineHeight,
                    alignment: .leading
                )
                .clipped()
                .offset(x: metrics.subjectLeading)

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

    func makeNSView(context: Context) -> KeyView {
        let view = KeyView()
        view.onMove = onMove
        view.onOpen = onOpen
        DispatchQueue.main.async {
            view.window?.makeFirstResponder(view)
        }
        return view
    }

    func updateNSView(_ nsView: KeyView, context: Context) {
        nsView.updateHandlers(onMove: onMove, onOpen: onOpen)
    }

    final class KeyView: NSView {
        var onMove: ((Int) -> Void)?
        var onOpen: (() -> Void)?

        override var acceptsFirstResponder: Bool {
            true
        }

        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            KeyMonitor.install(
                window: window,
                onMove: onMove,
                onOpen: onOpen
            )
            DispatchQueue.main.async { [weak self] in
                guard let self else {
                    return
                }
                self.window?.makeFirstResponder(self)
            }
        }

        func updateHandlers(onMove: @escaping (Int) -> Void, onOpen: @escaping () -> Void) {
            self.onMove = onMove
            self.onOpen = onOpen
            KeyMonitor.install(window: window, onMove: onMove, onOpen: onOpen)
        }

        private enum KeyMonitor {
            static weak var window: NSWindow?
            static var onMove: ((Int) -> Void)?
            static var onOpen: (() -> Void)?
            static var monitor: Any?

            static func install(
                window: NSWindow?,
                onMove: ((Int) -> Void)?,
                onOpen: (() -> Void)?
            ) {
                self.window = window
                self.onMove = onMove
                self.onOpen = onOpen

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

    var senderWidth: CGFloat {
        min(max(windowSize.width * 0.24, 330), 410)
    }

    var subjectLeading: CGFloat {
        senderLeading + senderWidth + senderSubjectGap
    }

    var timeLeading: CGFloat {
        windowSize.width - trailingInset - timeWidth
    }

    var subjectWidth: CGFloat {
        max(0, timeLeading - subjectLeading - 20)
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

    func sectionTopSpacing(for sectionID: String) -> CGFloat {
        sectionID == "today" ? 0 : ElectronicMailTypography.bodyLineHeight
    }

    func sectionHeaderHeight(for sectionID: String) -> CGFloat {
        sectionTopSpacing(for: sectionID) + ElectronicMailTypography.bodyLineHeight + 3
    }
}

#Preview {
    InboxView(store: InboxStore(client: DemoAppClient()))
        .frame(width: 1440, height: 900)
}
