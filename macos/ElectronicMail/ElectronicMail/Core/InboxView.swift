import AppKit
import SwiftUI

public struct InboxView: View {
    @Environment(\.colorScheme) private var colorScheme
    @ObservedObject private var store: InboxStore

    public init(store: InboxStore) {
        self.store = store
    }

    public var body: some View {
        GeometryReader { proxy in
            let metrics = InboxLayoutMetrics(windowSize: proxy.size)

            ZStack {
                ElectronicMailColors.background(for: colorScheme)
                    .ignoresSafeArea()

                VStack(alignment: .leading, spacing: 0) {
                    InboxHeader(
                        metrics: metrics,
                        navigationPlaceholderVisible: store.navigationPlaceholderVisible,
                        onMenu: { store.toggleNavigationPlaceholder() }
                    )
                    .frame(height: ElectronicMailTypography.bodyLineHeight)
                    .padding(.top, metrics.headerTop)
                    .padding(.bottom, metrics.headerBottom)

                    ScrollViewReader { scrollProxy in
                        ZStack(alignment: .topLeading) {
                            ScrollView(.vertical, showsIndicators: false) {
                                LazyVStack(alignment: .leading, spacing: 0) {
                                    ForEach(store.sections) { section in
                                        InboxSectionView(
                                            section: section,
                                            metrics: metrics,
                                            colorScheme: colorScheme,
                                            onSelect: { threadID in
                                                store.select(threadID: threadID, prefetch: false)
                                            }
                                        )
                                    }
                                }
                                .padding(.bottom, ElectronicMailTypography.bodyLineHeight)
                            }

                            InboxKeyboardEventCapture(
                                onMove: { delta in moveSelection(delta: delta, proxy: scrollProxy) },
                                onOpen: { store.openActiveSelection() }
                            )
                            .frame(width: 1, height: 1)
                            .opacity(0.01)
                        }
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)

                if store.refreshFailed {
                    VStack {
                        Spacer()
                        Text("Inbox could not refresh. Showing last saved state.")
                            .font(.rounded(size: 13, weight: .medium))
                            .foregroundStyle(.secondary)
                            .padding(.horizontal, 14)
                            .padding(.vertical, 8)
                            .background(.regularMaterial, in: Capsule())
                            .padding(.bottom, 22)
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
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

    private func moveSelection(delta: Int, proxy: ScrollViewProxy) {
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
        store.select(threadID: nextThreadID, prefetch: false)

        withAnimation(.easeOut(duration: 0.08)) {
            proxy.scrollTo(nextThreadID)
        }
    }
}

private struct InboxHeader: View {
    let metrics: InboxLayoutMetrics
    let navigationPlaceholderVisible: Bool
    let onMenu: () -> Void

    var body: some View {
        ZStack(alignment: .topLeading) {
            Button(action: onMenu) {
                Image(systemName: "line.3.horizontal")
                    .font(.rounded(size: ElectronicMailTypography.iconSize, weight: .semibold))
                    .foregroundStyle(.primary)
                    .frame(width: 16, height: 20)
            }
            .buttonStyle(.plain)
            .help(navigationPlaceholderVisible ? "Hide navigation" : "Show navigation")
            .frame(width: 30, height: 30)
            .position(x: metrics.groupedIconX, y: ElectronicMailTypography.bodyLineHeight / 2)

            Text("Inbox")
                .font(.rounded(size: ElectronicMailTypography.titleSize, weight: .bold))
                .tracking(ElectronicMailTypography.titleTracking)
                .foregroundStyle(.primary)
                .frame(height: ElectronicMailTypography.bodyLineHeight, alignment: .center)
                .offset(x: metrics.contentLeading)
        }
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
                .font(.rounded(size: ElectronicMailTypography.bodySize, weight: .regular))
                .tracking(ElectronicMailTypography.bodyTracking)
                .foregroundStyle(ElectronicMailColors.sectionTitle(for: colorScheme))
                .lineLimit(1)
                .frame(height: ElectronicMailTypography.bodyLineHeight)
                .offset(x: metrics.contentLeading, y: metrics.sectionTopSpacing(for: section.id))

            Rectangle()
                .fill(ElectronicMailColors.divider(for: colorScheme))
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
                ElectronicMailColors.appleBlue
                    .frame(width: metrics.windowSize.width, height: ElectronicMailTypography.bodyLineHeight)
            }

            if row.isGrouped {
                Image(systemName: "chevron.right.circle")
                    .font(.rounded(size: ElectronicMailTypography.iconSize, weight: .regular))
                    .symbolRenderingMode(.monochrome)
                    .foregroundStyle(row.isSelected ? .white : ElectronicMailColors.appleBlue)
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
                .offset(x: metrics.senderLeading)

            rowText(row.title, alignment: .leading)
                .frame(
                    width: metrics.subjectWidth,
                    height: ElectronicMailTypography.bodyLineHeight,
                    alignment: .leading
                )
                .offset(x: metrics.subjectLeading)

            rowText(row.timeLabel, alignment: .trailing)
                .frame(
                    width: metrics.timeWidth,
                    height: ElectronicMailTypography.bodyLineHeight,
                    alignment: .trailing
                )
                .offset(x: metrics.timeLeading)
        }
        .frame(width: metrics.windowSize.width, height: ElectronicMailTypography.bodyLineHeight, alignment: .topLeading)
        .contentShape(Rectangle())
        .onTapGesture(perform: onSelect)
    }

    private func rowText(_ value: String, alignment: Alignment) -> some View {
        Text(value)
            .font(.rounded(size: ElectronicMailTypography.bodySize, weight: .regular))
            .tracking(ElectronicMailTypography.bodyTracking)
            .foregroundStyle(textColor)
            .lineLimit(1)
            .truncationMode(.tail)
            .multilineTextAlignment(alignment == .trailing ? .trailing : .leading)
    }

    private var textColor: Color {
        if row.isSelected {
            return .white
        }
        if row.isUnread {
            return ElectronicMailColors.primaryText(for: colorScheme)
        }
        return ElectronicMailColors.readText(for: colorScheme)
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
        nsView.onMove = onMove
        nsView.onOpen = onOpen
        DispatchQueue.main.async {
            nsView.window?.makeFirstResponder(nsView)
        }
    }

    final class KeyView: NSView {
        var onMove: ((Int) -> Void)?
        var onOpen: (() -> Void)?

        override var acceptsFirstResponder: Bool {
            true
        }

        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            DispatchQueue.main.async { [weak self] in
                guard let self else {
                    return
                }
                self.window?.makeFirstResponder(self)
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

private enum ElectronicMailColors {
    static let appleBlue = Color(red: 0.0, green: 90.0 / 255.0, blue: 205.0 / 255.0)

    static func background(for colorScheme: ColorScheme) -> Color {
        colorScheme == .dark ? .black : .white
    }

    static func primaryText(for colorScheme: ColorScheme) -> Color {
        colorScheme == .dark ? .white : .black
    }

    static func divider(for colorScheme: ColorScheme) -> Color {
        colorScheme == .dark ? .white.opacity(0.10) : .black.opacity(0.10)
    }

    static func sectionTitle(for colorScheme: ColorScheme) -> Color {
        colorScheme == .dark ? .white.opacity(0.25) : .black.opacity(0.25)
    }

    static func readText(for colorScheme: ColorScheme) -> Color {
        colorScheme == .dark ? .white.opacity(0.34) : .black.opacity(0.42)
    }
}

private enum ElectronicMailTypography {
    static let titleSize: CGFloat = 26
    static let titleTracking: CGFloat = titleSize * 0.02
    static let bodySize: CGFloat = 22
    static let bodyTracking: CGFloat = bodySize * 0.015
    static let bodyLineHeight: CGFloat = 40
    static let iconSize: CGFloat = 22
}

private struct InboxLayoutMetrics {
    let windowSize: CGSize

    let timeWidth: CGFloat = 190

    var headerTop: CGFloat {
        max(16, contentLeading - 80)
    }

    var headerBottom: CGFloat {
        30
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
        senderLeading + senderWidth
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
}

private extension Font {
    static func rounded(size: CGFloat, weight: Font.Weight) -> Font {
        .system(size: size, weight: weight, design: .rounded)
    }
}

#Preview {
    InboxView(store: InboxStore(client: DemoAppClient()))
        .frame(width: 1440, height: 900)
}
