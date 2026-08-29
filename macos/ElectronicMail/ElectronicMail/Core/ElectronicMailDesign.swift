import AppKit
import SwiftUI

/// Semantic SF Symbols shared by the signed-in app and its onboarding screen.
/// Keeping these names in one place prevents the welcome artwork from drifting
/// to visually similar, but inconsistent, glyph variants.
public enum ElectronicMailSymbols {
    public static let search = "magnifyingglass"
    public static let contacts = "person.crop.circle.badge.plus"
    public static let inbox = "tray.full"
    public static let moveToInbox = "tray.and.arrow.down.fill"
    public static let starred = "star"
    public static let starredFilled = "star.fill"
    public static let reply = "arrowshape.turn.up.left.fill"
}

public enum ElectronicMailDesign {
    /// Electronic Mail's original brand accent. Keep this independent from the
    /// user's system accent so the product identity remains black and blue.
    public static let appleBlue = Color(red: 0, green: 90.0 / 255.0, blue: 205.0 / 255.0)
    public static let green = Color(red: 0, green: 190.0 / 255.0, blue: 36.0 / 255.0)
    public static let featureSearch = Color(red: 0.03, green: 0.48, blue: 0.98)
    public static let featureContacts = Color(red: 0.12, green: 0.76, blue: 0.25)
    public static let featureInbox = Color(red: 0.96, green: 0.50, blue: 0.08)
    public static let featureStarred = Color(red: 0.97, green: 0.69, blue: 0.02)
    public static let featureReply = Color(red: 0.48, green: 0.27, blue: 0.88)
    /// Apple's semantic system red adapts to appearance and increased-contrast
    /// settings, so failures stay legible without freezing a custom RGB value.
    public static let errorAccent = Color(nsColor: .systemRed)
    public static let sidebarBackground = Color.black
    public static let sidebarText = Color.white.opacity(0.90)
    public static let sidebarSecondaryText = Color.white.opacity(0.58)

    public static func background(for colorScheme: ColorScheme) -> Color {
        colorScheme == .dark ? .black : .white
    }

    public static func successAccent(for colorScheme: ColorScheme) -> Color {
        colorScheme == .dark
            ? green
            : Color(red: 0, green: 0.46, blue: 0.12)
    }

    public static func selectedText(for _: ColorScheme) -> Color {
        .white
    }

    public static func panelFill(for _: ColorScheme) -> Color {
        Color(nsColor: .controlBackgroundColor)
    }

    public static func panelBorder(for _: ColorScheme) -> Color {
        Color(nsColor: .separatorColor)
    }

    public static func primaryText(for _: ColorScheme) -> Color {
        Color(nsColor: .labelColor)
    }

    public static func secondaryText(for _: ColorScheme) -> Color {
        Color(nsColor: .secondaryLabelColor)
    }

    public static func unreadText(for colorScheme: ColorScheme) -> Color {
        colorScheme == .dark ? .white : .black
    }

    public static func readText(for _: ColorScheme) -> Color {
        Color(nsColor: .labelColor).opacity(0.78)
    }

    public static func tertiaryText(for _: ColorScheme) -> Color {
        Color(nsColor: .tertiaryLabelColor)
    }

    public static func sectionText(for _: ColorScheme) -> Color {
        Color(nsColor: .secondaryLabelColor)
    }

    public static func divider(for _: ColorScheme) -> Color {
        Color(nsColor: .separatorColor)
    }

    public static func readerHairline(for colorScheme: ColorScheme) -> Color {
        colorScheme == .dark ? Color.white.opacity(0.12) : Color.black.opacity(0.12)
    }

    public static func readerControlFill(for colorScheme: ColorScheme) -> Color {
        colorScheme == .dark ? Color.white.opacity(0.055) : Color.black.opacity(0.045)
    }

    public static func readerControlBorder(for colorScheme: ColorScheme) -> Color {
        colorScheme == .dark ? Color.white.opacity(0.14) : Color.black.opacity(0.12)
    }

    public static func readerActionFill(for colorScheme: ColorScheme) -> Color {
        colorScheme == .dark
            ? Color(red: 25.0 / 255.0, green: 25.0 / 255.0, blue: 27.0 / 255.0)
            : Color(red: 238.0 / 255.0, green: 238.0 / 255.0, blue: 240.0 / 255.0)
    }

    public static let readerAskBorderGradient = LinearGradient(
        colors: [
            Color(red: 221.0 / 255.0, green: 161.0 / 255.0, blue: 143.0 / 255.0),
            Color(red: 246.0 / 255.0, green: 217.0 / 255.0, blue: 145.0 / 255.0),
            Color(red: 240.0 / 255.0, green: 166.0 / 255.0, blue: 190.0 / 255.0),
            Color(red: 131.0 / 255.0, green: 201.0 / 255.0, blue: 244.0 / 255.0),
        ],
        startPoint: .topLeading,
        endPoint: .bottomTrailing
    )

    public static func readerAvatarFill(for colorScheme: ColorScheme, highlighted: Bool) -> Color {
        if highlighted {
            return appleBlue
        }
        return colorScheme == .dark ? Color.white.opacity(0.19) : Color.black.opacity(0.16)
    }

    public static func controlFill(for colorScheme: ColorScheme, selected: Bool = false) -> Color {
        if selected {
            return appleBlue.opacity(colorScheme == .dark ? 0.24 : 0.13)
        }
        return Color(nsColor: .controlBackgroundColor)
    }

    public static func composerSurface(for colorScheme: ColorScheme) -> Color {
        colorScheme == .dark
            ? Color(red: 18.0 / 255.0, green: 19.0 / 255.0, blue: 21.0 / 255.0)
            : Color(nsColor: .windowBackgroundColor)
    }

    public static func composerEditorSurface(for colorScheme: ColorScheme) -> Color {
        colorScheme == .dark
            ? Color(red: 13.0 / 255.0, green: 14.0 / 255.0, blue: 16.0 / 255.0)
            : Color(nsColor: .textBackgroundColor)
    }

    public static func composerTokenFill(for colorScheme: ColorScheme) -> Color {
        colorScheme == .dark
            ? Color.white.opacity(0.08)
            : Color.black.opacity(0.06)
    }
}

/// Shared geometry for the macOS control layer. Content surfaces keep their own
/// readable widths, but every navigation and action control resolves through
/// these semantic measurements so switching destinations never changes scale.
public enum ElectronicMailControlMetrics {
    /// Launch, sign-in, and mailbox preparation stay in a deliberately compact
    /// centered window before the ready inbox unfolds into the main workspace.
    public static let onboardingWindowWidth: CGFloat = 680
    public static let onboardingWindowHeight: CGFloat = 520
    public static let mainWindowWidth: CGFloat = 1512
    public static let mainWindowHeight: CGFloat = 918
    public static let mainWindowBackdropInset: CGFloat = 24
    public static let headerHeight: CGFloat = 64
    public static let headerCenterY: CGFloat = headerHeight / 2
    public static let headerControlSize: CGFloat = 36
    /// Native capsule glass draws slightly inside a view's bounds, unlike the
    /// button glass used by circular controls. Give Search a 42-point frame so
    /// its visible bubble is optically the same height as a 36-point icon button.
    public static let headerSearchHeight: CGFloat = 42
    public static let headerSymbolSize: CGFloat = 18
    /// SF Symbols use typographic bounds rather than optical bounds. Keep the
    /// uneven compose and search glyphs visually centered in their circular
    /// mailbox controls without moving the control or its hit target.
    public static let composeSymbolOpticalOffset = CGSize(width: -0.5, height: -1)
    public static let searchSymbolOpticalOffset = CGSize(width: 0, height: -0.5)
    /// Mailbox navigation, compose, and search stay available without
    /// competing with the current mailbox title and message content.
    public static let mailboxHeaderIconOpacity: CGFloat = 0.50
    /// Native glass blooms beyond the nominal control frame. A 32-point frame
    /// gap produces the same visible air as the 32-point outer/title interval.
    public static let headerControlGap: CGFloat = 32
    /// Mailbox compose and search controls form a tighter pair than the
    /// independent controls used on the other shell surfaces.
    public static let mailboxHeaderControlGap: CGFloat = headerControlGap / 2
    /// Mailbox trailing controls align with the inbox date column.
    public static let mailboxHeaderTrailingInset: CGFloat = headerTitleLeading
    /// The first mailbox section begins at the scroll viewport's clipping
    /// boundary. Later sections retain separation from the rows above them.
    public static let mailboxFirstSectionTopSpacing: CGFloat = 0
    public static let mailboxSectionTopSpacing: CGFloat = 24
    /// macOS 26 uses the native soft scroll-edge effect. These values drive the
    /// opacity-gradient fallback on earlier systems so mailbox rows still fade
    /// naturally beneath the fixed navigation header.
    public static let mailboxScrollFadeHeight: CGFloat = 64
    public static let mailboxScrollFadeActivationDistance: CGFloat = 16
    public static let mailboxScrollFadeTopContentOpacity: CGFloat = 0
    /// Reader actions form a denser, content-scoped tool group. This is
    /// intentionally reader-only; shell and search spacing remain unchanged.
    public static let readerActionGap: CGFloat = headerControlGap / 2
    /// Shared vertical gap for the Reader's two-line information groups:
    /// subject/metadata and sender/date.
    public static let readerTwoLineGap: CGFloat = 6
    /// Reader subjects may wrap when the action rail leaves insufficient room,
    /// but remain compact enough to preserve the conversation hierarchy.
    public static let readerSubjectLineLimit = 2
    /// SwiftUI's text line box contains a small amount of top leading. Lift the
    /// line box so the first visible subject glyph starts on the controls'
    /// optical center line. Wrapped lines still grow only downward. Inbox and
    /// AI Inbox readers share this anchor through `ElectronicMailShellHeader`.
    public static let readerHeaderTitleOpticalCorrection: CGFloat = 10
    /// Approved fixed first-line anchor. Keep this independent of the reader's
    /// computed header height so later wrapping or chrome changes cannot move
    /// the subject in either Inbox or AI Inbox.
    public static let readerHeaderTitleTop: CGFloat = 22
    public static let readerHeaderAdditionalLineHeight: CGFloat = 20
    public static let readerHeaderBottomInset: CGFloat = 9
    public static let readerResponseTopSpacing: CGFloat = 28
    public static let readerResponseBottomSpacing: CGFloat = 48
    /// Reader response controls are viewport chrome, not message content.
    /// Keep the group a stable distance from the lower window edge.
    public static let readerFloatingActionsBottomInset: CGFloat = 40
    public static let readerFixedActionsReservedHeight: CGFloat =
        actionHeight + readerResponseTopSpacing + readerResponseBottomSpacing
    /// The Details disclosure belongs to sender metadata, so it is quieter
    /// than the adjacent date instead of reading as an accent-colored action.
    public static let readerDetailsLabelOpacity: CGFloat = 5.0 / 7.0
    /// Readers use the exact same fade profile as both mailbox home screens.
    /// Keeping these aliases shared also gives WebKit-backed email bodies the
    /// same fully transparent clipped edge as native mailbox rows.
    public static let readerScrollFadeHeight = mailboxScrollFadeHeight
    public static let readerScrollFadeActivationDistance = mailboxScrollFadeActivationDistance
    public static let readerScrollFadeTopContentOpacity = mailboxScrollFadeTopContentOpacity
    /// Reader scroll views already begin below all fixed chrome. The WebKit
    /// fade overlay therefore belongs at the viewport's own top edge.
    public static let readerScrollFadeTopInset: CGFloat = 0
    public static let composerHeaderContentOffsetY: CGFloat = 14
    /// Reader content begins below the fixed toolbar, with enough breathing
    /// room that the subject reads as page content instead of another control.
    public static let readerContentTop: CGFloat = 20
    public static let readerHeaderToConversation: CGFloat = 12
    public static let readerPreviousMessagePeek: CGFloat = 72
    public static let readerPreviousMessagePeekFadeProgress: CGFloat = 0.78
    public static let readerPinnedSummaryBottomSpacing: CGFloat = 12
    public static let headerOuterInset: CGFloat = 32
    public static let headerLeadingControlCenter: CGFloat = headerOuterInset + headerControlSize / 2
    /// The title is one outer-inset away from the leading control. This makes
    /// the window edge -> control and control -> title intervals read as one
    /// deliberate rhythm instead of placing the title against the glass.
    public static let headerTitleGap: CGFloat = headerOuterInset
    public static let headerTitleLeading: CGFloat = headerOuterInset + headerControlSize + headerTitleGap
    public static let actionHeight: CGFloat = 40
    public static let actionGap: CGFloat = 32
    public static let glassMergeSpacing: CGFloat = 0
    public static let trailingInset: CGFloat = headerOuterInset
    public static let composerFieldHeight: CGFloat = 44
    public static let composerMaxWidth: CGFloat = 960
    public static let readerMaxWidth: CGFloat = 860
    public static let paletteMaxWidth: CGFloat = 640
    public static let paletteRowHeight: CGFloat = 40

    public static func centeredContentLeading(
        containerWidth: CGFloat,
        maxContentWidth: CGFloat,
        horizontalPadding: CGFloat = 0,
        contentInset: CGFloat = 0
    ) -> CGFloat {
        let availableWidth = max(1, containerWidth - horizontalPadding * 2)
        let contentWidth = min(maxContentWidth, availableWidth)
        return max(headerTitleLeading, (containerWidth - contentWidth) / 2 + contentInset)
    }

    public static func readerHeaderHeight(subject: String, availableWidth: CGFloat) -> CGFloat {
        guard availableWidth > 1 else { return headerHeight }
        let font = NSFont.systemFont(ofSize: ElectronicMailReaderType.titleSize, weight: .semibold)
        let lineHeight = max(1, ceil(font.ascender - font.descender + font.leading))
        let measured = (subject as NSString).boundingRect(
            with: NSSize(width: availableWidth, height: .greatestFiniteMagnitude),
            options: [.usesLineFragmentOrigin, .usesFontLeading],
            attributes: [.font: font]
        )
        let lineCount = min(
            readerSubjectLineLimit,
            max(1, Int(ceil(measured.height / lineHeight)))
        )
        let metadataFont = NSFont.systemFont(ofSize: ElectronicMailReaderType.metadataSize)
        let metadataLineHeight = ceil(
            metadataFont.ascender - metadataFont.descender + metadataFont.leading
        )
        let contentHeight = readerHeaderTitleTop
            + CGFloat(lineCount) * readerHeaderAdditionalLineHeight
            + readerTwoLineGap
            + metadataLineHeight
            + readerHeaderBottomInset
        return max(headerHeight, ceil(contentHeight))
    }

    public static func readerActionRailWidth(controlCount: Int) -> CGFloat {
        guard controlCount > 0 else { return 0 }
        return CGFloat(controlCount) * headerControlSize
            + CGFloat(controlCount - 1) * readerActionGap
    }
}

/// Responsive mailbox anchors derived from the approved Figma grid. The
/// disclosure, sender, and date share the mailbox's fixed edge anchors; the
/// subject alone expands responsively with the available width.
struct ElectronicMailLayoutMetrics: Equatable {
    let width: CGFloat

    private static let figmaWidth: CGFloat = 2_399
    private static let subjectLeadingX: CGFloat = 643

    var utilityCenter: CGFloat {
        ElectronicMailControlMetrics.headerLeadingControlCenter
    }

    var textLeading: CGFloat {
        ElectronicMailControlMetrics.headerTitleLeading
    }

    var subjectLeading: CGFloat {
        max(textLeading + 196, width * Self.subjectLeadingX / Self.figmaWidth)
    }

    var dateTrailing: CGFloat {
        textLeading
    }
}

/// The single signed-in header rail. Leading, title, and trailing content use
/// the same anchors on every destination, while each screen supplies context.
struct ElectronicMailShellHeader<Leading: View, Title: View, Trailing: View>: View {
    let width: CGFloat
    let height: CGFloat
    let titleLeading: CGFloat
    let titleTrailingReservation: CGFloat
    let titleAlignment: Alignment
    let trailingSpacing: CGFloat
    let trailingInset: CGFloat
    private let leading: Leading
    private let title: Title
    private let trailing: Trailing

    init(
        width: CGFloat,
        height: CGFloat = ElectronicMailControlMetrics.headerHeight,
        titleLeading: CGFloat = ElectronicMailControlMetrics.headerTitleLeading,
        titleTrailingReservation: CGFloat = 0,
        titleAlignment: Alignment = .leading,
        trailingSpacing: CGFloat = ElectronicMailControlMetrics.headerControlGap,
        trailingInset: CGFloat = ElectronicMailControlMetrics.trailingInset,
        @ViewBuilder leading: () -> Leading,
        @ViewBuilder title: () -> Title,
        @ViewBuilder trailing: () -> Trailing
    ) {
        self.width = width
        self.height = height
        self.titleLeading = titleLeading
        self.titleTrailingReservation = titleTrailingReservation
        self.titleAlignment = titleAlignment
        self.trailingSpacing = trailingSpacing
        self.trailingInset = trailingInset
        self.leading = leading()
        self.title = title()
        self.trailing = trailing()
    }

    var body: some View {
        ZStack(alignment: .leading) {
            title
                .frame(height: height, alignment: titleAlignment)
                .padding(.leading, titleLeading)
                .padding(.trailing, titleTrailingReservation)
                .frame(maxWidth: .infinity, alignment: .leading)

            leading
                .frame(
                    width: ElectronicMailControlMetrics.headerControlSize,
                    height: ElectronicMailControlMetrics.headerControlSize
                )
                .position(
                    x: ElectronicMailControlMetrics.headerLeadingControlCenter,
                    y: ElectronicMailControlMetrics.headerCenterY
                )

            HStack(spacing: trailingSpacing) {
                Spacer(minLength: 0)
                trailing
            }
            .padding(.trailing, trailingInset)
            .frame(
                width: width,
                height: ElectronicMailControlMetrics.headerControlSize,
                alignment: .trailing
            )
            .position(
                x: width / 2,
                y: ElectronicMailControlMetrics.headerCenterY
            )
        }
        .frame(width: width, height: height, alignment: .leading)
        .accessibilityElement(children: .contain)
    }
}

struct ElectronicMailIconLabel: View {
    let symbol: String
    var role: ElectronicMailGlassRole = .standard
    var controlSize: CGFloat = ElectronicMailControlMetrics.headerControlSize
    var symbolSize: CGFloat = ElectronicMailControlMetrics.headerSymbolSize
    var symbolOffset: CGSize = .zero
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        Image(systemName: symbol)
            .font(.system(size: symbolSize, weight: .medium))
            .foregroundStyle(foregroundColor)
            .offset(x: symbolOffset.width, y: symbolOffset.height)
            .frame(
                width: controlSize,
                height: controlSize
            )
            .contentShape(Circle())
    }

    private var foregroundColor: Color {
        switch role {
        case .prominent:
            return .white
        case .destructive:
            return .red
        case .standard, .brandedAsk:
            return ElectronicMailDesign.primaryText(for: colorScheme)
        }
    }
}

struct ElectronicMailIconControl: View {
    let symbol: String
    let accessibilityLabel: String
    var role: ElectronicMailGlassRole = .standard
    var controlSize: CGFloat = ElectronicMailControlMetrics.headerControlSize
    var symbolSize: CGFloat = ElectronicMailControlMetrics.headerSymbolSize
    var symbolOpacity: CGFloat = 1
    var symbolOffset: CGSize = .zero
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            ElectronicMailIconLabel(
                symbol: symbol,
                role: role,
                controlSize: controlSize,
                symbolSize: symbolSize,
                symbolOffset: symbolOffset
            )
            .opacity(symbolOpacity)
        }
        .electronicMailGlassButton(role: role, shape: .circle)
        .frame(
            width: controlSize,
            height: controlSize
        )
        .contentShape(Circle())
        .help(accessibilityLabel)
        .accessibilityLabel(accessibilityLabel)
    }
}

public enum ElectronicMailGlassRole: String, CaseIterable, Equatable, Sendable {
    case standard
    case prominent
    case destructive
    case brandedAsk
}

/// Glass is reserved for navigation and action layers. Sidebar rows, forms,
/// message content, and mailbox rows retain their native content appearance.
typealias ElectronicMailFloatingActionRole = ElectronicMailGlassRole
typealias ElectronicMailFloatingActionGroup = ElectronicMailGlassGroup

public enum ElectronicMailGlassShape: Equatable, Sendable {
    case circle
    case capsule
    case panel(radius: CGFloat)

    var buttonBorderShape: ButtonBorderShape {
        switch self {
        case .circle:
            return .circle
        case .capsule:
            return .capsule
        case .panel(let radius):
            return .roundedRectangle(radius: radius)
        }
    }
}

enum ElectronicMailGlassRenderingMode: String, CaseIterable, Equatable {
    case automatic
    case native
    case fallback

    func usesNativeGlass(osMajorVersion: Int, reduceTransparency: Bool) -> Bool {
        guard !reduceTransparency, osMajorVersion >= 26 else {
            return false
        }
        return self != .fallback
    }
}

private struct ElectronicMailGlassRenderingModeKey: EnvironmentKey {
    static let defaultValue = ElectronicMailGlassRenderingMode.automatic
}

extension EnvironmentValues {
    var electronicMailGlassRenderingMode: ElectronicMailGlassRenderingMode {
        get { self[ElectronicMailGlassRenderingModeKey.self] }
        set { self[ElectronicMailGlassRenderingModeKey.self] = newValue }
    }
}

extension View {
    func electronicMailGlassRenderingMode(_ mode: ElectronicMailGlassRenderingMode) -> some View {
        environment(\.electronicMailGlassRenderingMode, mode)
    }

    public func electronicMailGlassButton(
        role: ElectronicMailGlassRole = .standard,
        shape: ElectronicMailGlassShape
    ) -> some View {
        modifier(ElectronicMailGlassButtonModifier(role: role, shape: shape))
    }

    /// Applies one explicit interactive glass surface to mixed control types.
    /// Unlike `GlassButtonStyle`, this keeps `Menu` triggers and `Button`s
    /// visually identical because the effect is resolved from the laid-out
    /// control geometry instead of the control's platform context.
    func electronicMailGlassControlSurface(
        shape: ElectronicMailGlassShape
    ) -> some View {
        modifier(ElectronicMailGlassControlSurfaceModifier(shape: shape))
    }

    func electronicMailGlassPanel(
        shape: ElectronicMailGlassShape = .panel(radius: 16)
    ) -> some View {
        modifier(ElectronicMailGlassPanelModifier(shape: shape))
    }

    func electronicMailFloatingAction(role: ElectronicMailFloatingActionRole) -> some View {
        electronicMailGlassButton(role: role, shape: .capsule)
    }
}

private struct ElectronicMailGlassControlSurfaceModifier: ViewModifier {
    let shape: ElectronicMailGlassShape
    @Environment(\.electronicMailGlassRenderingMode) private var renderingMode
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.colorSchemeContrast) private var contrast
    @Environment(\.isEnabled) private var isEnabled

    @ViewBuilder
    func body(content: Content) -> some View {
        if #available(macOS 26.0, *), usesNativeGlass {
            nativeBody(content: content)
        } else {
            content
                .buttonStyle(.plain)
                .foregroundStyle(ElectronicMailDesign.primaryText(for: colorScheme))
                .background {
                    ElectronicMailFallbackGlassFill(
                        role: .standard,
                        shape: shape,
                        colorScheme: colorScheme,
                        reduceTransparency: reduceTransparency
                    )
                }
                .overlay {
                    ElectronicMailGlassShapeStroke(
                        shape: shape,
                        style: ElectronicMailDesign.panelBorder(for: colorScheme),
                        lineWidth: contrast == .increased ? 1.5 : 1
                    )
                }
                .contentShape(ElectronicMailGlassContentShape(shape: shape))
                .opacity(isEnabled ? 1 : 0.46)
        }
    }

    private var usesNativeGlass: Bool {
        renderingMode.usesNativeGlass(
            osMajorVersion: ProcessInfo.processInfo.operatingSystemVersion.majorVersion,
            reduceTransparency: reduceTransparency
        )
    }

    @available(macOS 26.0, *)
    @ViewBuilder
    private func nativeBody(content: Content) -> some View {
        switch shape {
        case .circle:
            nativeContent(content: content)
                .glassEffect(.regular.interactive(isEnabled), in: Circle())
        case .capsule:
            nativeContent(content: content)
                .glassEffect(.regular.interactive(isEnabled), in: Capsule())
        case .panel(let radius):
            nativeContent(content: content)
                .glassEffect(
                    .regular.interactive(isEnabled),
                    in: RoundedRectangle(cornerRadius: radius, style: .continuous)
                )
        }
    }

    @available(macOS 26.0, *)
    private func nativeContent(content: Content) -> some View {
        content
            .buttonStyle(.plain)
            .foregroundStyle(.primary)
            .opacity(isEnabled ? 1 : 0.46)
    }
}

public struct ElectronicMailGlassGroup<Content: View>: View {
    private let mergeSpacing: CGFloat
    private let content: Content
    @Environment(\.electronicMailGlassRenderingMode) private var renderingMode
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency

    public init(
        spacing _: CGFloat,
        mergeSpacing: CGFloat = ElectronicMailControlMetrics.glassMergeSpacing,
        @ViewBuilder content: () -> Content
    ) {
        self.mergeSpacing = mergeSpacing
        self.content = content()
    }

    @ViewBuilder
    public var body: some View {
        if #available(macOS 26.0, *), usesNativeGlass {
            // The HStack owns visible spacing. A zero merge threshold keeps
            // discrete icon controls from forming the scalloped, fused strip
            // seen in the visual review while retaining native glass behavior.
            GlassEffectContainer(spacing: mergeSpacing) {
                content
            }
        } else {
            content
        }
    }

    private var usesNativeGlass: Bool {
        renderingMode.usesNativeGlass(
            osMajorVersion: ProcessInfo.processInfo.operatingSystemVersion.majorVersion,
            reduceTransparency: reduceTransparency
        )
    }
}

private struct ElectronicMailGlassButtonModifier: ViewModifier {
    let role: ElectronicMailGlassRole
    let shape: ElectronicMailGlassShape
    @Environment(\.electronicMailGlassRenderingMode) private var renderingMode
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @Environment(\.isEnabled) private var isEnabled
    @Environment(\.appearsActive) private var appearsActive

    @ViewBuilder
    func body(content: Content) -> some View {
        if #available(macOS 26.0, *), usesNativeGlass {
            nativeBody(content: content)
        } else {
            content
                .buttonStyle(ElectronicMailFallbackGlassButtonStyle(role: role, shape: shape))
        }
    }

    private var usesNativeGlass: Bool {
        renderingMode.usesNativeGlass(
            osMajorVersion: ProcessInfo.processInfo.operatingSystemVersion.majorVersion,
            reduceTransparency: reduceTransparency
        )
    }

    @available(macOS 26.0, *)
    @ViewBuilder
    private func nativeBody(content: Content) -> some View {
        switch role {
        case .prominent:
            nativeProminentBody(content: content)
        case .destructive:
            content
                .buttonBorderShape(shape.buttonBorderShape)
                .buttonStyle(.glass)
                .tint(.clear)
                .foregroundStyle(.red)
        case .brandedAsk:
            content
                .buttonBorderShape(shape.buttonBorderShape)
                .buttonStyle(.glass)
                .tint(.clear)
                .foregroundStyle(.primary)
                .overlay {
                    ElectronicMailGlassShapeStroke(
                        shape: shape,
                        style: ElectronicMailDesign.readerAskBorderGradient,
                        lineWidth: 1.25
                    )
                    .allowsHitTesting(false)
                }
        case .standard:
            content
                .buttonBorderShape(shape.buttonBorderShape)
                .buttonStyle(.glass)
                .tint(.clear)
                .foregroundStyle(.primary)
        }
    }

    @available(macOS 26.0, *)
    @ViewBuilder
    private func nativeProminentBody(content: Content) -> some View {
        switch shape {
        case .circle:
            nativeProminentContent(content: content)
                .glassEffect(prominentGlass, in: Circle())
        case .capsule:
            nativeProminentContent(content: content)
                .glassEffect(prominentGlass, in: Capsule())
        case .panel(let radius):
            nativeProminentContent(content: content)
                .glassEffect(
                    prominentGlass,
                    in: RoundedRectangle(cornerRadius: radius, style: .continuous)
                )
        }
    }

    @available(macOS 26.0, *)
    private var prominentGlass: Glass {
        .regular
            .tint(ElectronicMailDesign.appleBlue)
            .interactive(isEnabled)
    }

    @available(macOS 26.0, *)
    private func nativeProminentContent(content: Content) -> some View {
        content
            .buttonStyle(.plain)
            .foregroundStyle(Color.white.opacity(isEnabled ? 1 : 0.72))
            .opacity(isEnabled ? 1 : 0.62)
            .background {
                if isEnabled, !appearsActive {
                    ElectronicMailGlassShapeFill(
                        shape: shape,
                        style: ElectronicMailDesign.appleBlue.opacity(0.72)
                    )
                }
            }
    }
}

private struct ElectronicMailFallbackGlassButtonStyle: ButtonStyle {
    let role: ElectronicMailGlassRole
    let shape: ElectronicMailGlassShape
    @Environment(\.isEnabled) private var isEnabled
    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.colorSchemeContrast) private var contrast
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .foregroundStyle(foregroundStyle)
            .background {
                ElectronicMailFallbackGlassFill(
                    role: role,
                    shape: shape,
                    colorScheme: colorScheme,
                    reduceTransparency: reduceTransparency
                )
            }
            .overlay {
                if role == .brandedAsk {
                    ElectronicMailGlassShapeStroke(
                        shape: shape,
                        style: ElectronicMailDesign.readerAskBorderGradient,
                        lineWidth: contrast == .increased ? 2 : 1.25
                    )
                } else {
                    ElectronicMailGlassShapeStroke(
                        shape: shape,
                        style: fallbackBorder,
                        lineWidth: contrast == .increased ? 1.5 : 1
                    )
                }
            }
            .contentShape(ElectronicMailGlassContentShape(shape: shape))
            .scaleEffect(configuration.isPressed && !reduceMotion ? 0.97 : 1)
            .opacity(isEnabled ? 1 : 0.46)
            .animation(reduceMotion ? nil : .easeOut(duration: 0.12), value: configuration.isPressed)
    }

    private var foregroundStyle: Color {
        switch role {
        case .prominent:
            return .white
        case .destructive:
            return .red
        case .standard, .brandedAsk:
            return ElectronicMailDesign.primaryText(for: colorScheme)
        }
    }

    private var fallbackBorder: Color {
        if role == .prominent {
            return Color.white.opacity(contrast == .increased ? 0.72 : 0.28)
        }
        return colorScheme == .dark
            ? Color.white.opacity(contrast == .increased ? 0.34 : 0.16)
            : Color.black.opacity(contrast == .increased ? 0.30 : 0.13)
    }
}

private struct ElectronicMailFallbackGlassFill: View {
    let role: ElectronicMailGlassRole
    let shape: ElectronicMailGlassShape
    let colorScheme: ColorScheme
    let reduceTransparency: Bool

    @ViewBuilder
    var body: some View {
        if role == .prominent {
            ElectronicMailGlassShapeFill(shape: shape, style: ElectronicMailDesign.appleBlue)
        } else if reduceTransparency {
            ElectronicMailGlassShapeFill(
                shape: shape,
                style: ElectronicMailDesign.panelFill(for: colorScheme)
            )
        } else {
            ElectronicMailGlassShapeFill(shape: shape, style: .regularMaterial)
        }
    }
}

private struct ElectronicMailGlassPanelModifier: ViewModifier {
    let shape: ElectronicMailGlassShape
    @Environment(\.electronicMailGlassRenderingMode) private var renderingMode
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.colorSchemeContrast) private var contrast

    @ViewBuilder
    func body(content: Content) -> some View {
        if #available(macOS 26.0, *), usesNativeGlass {
            nativeBody(content: content)
        } else {
            content
                .background {
                    if reduceTransparency {
                        ElectronicMailGlassShapeFill(
                            shape: shape,
                            style: ElectronicMailDesign.panelFill(for: colorScheme)
                        )
                    } else {
                        ElectronicMailGlassShapeFill(shape: shape, style: .regularMaterial)
                    }
                }
                .overlay {
                    ElectronicMailGlassShapeStroke(
                        shape: shape,
                        style: ElectronicMailDesign.panelBorder(for: colorScheme),
                        lineWidth: contrast == .increased ? 1.5 : 1
                    )
                }
        }
    }

    private var usesNativeGlass: Bool {
        renderingMode.usesNativeGlass(
            osMajorVersion: ProcessInfo.processInfo.operatingSystemVersion.majorVersion,
            reduceTransparency: reduceTransparency
        )
    }

    @available(macOS 26.0, *)
    @ViewBuilder
    private func nativeBody(content: Content) -> some View {
        switch shape {
        case .circle:
            content.glassEffect(.regular, in: Circle())
        case .capsule:
            content.glassEffect(.regular, in: Capsule())
        case .panel(let radius):
            content.glassEffect(.regular, in: RoundedRectangle(cornerRadius: radius, style: .continuous))
        }
    }
}

private struct ElectronicMailGlassShapeFill<Style: ShapeStyle>: View {
    let shape: ElectronicMailGlassShape
    let style: Style

    @ViewBuilder
    var body: some View {
        switch shape {
        case .circle:
            Circle().fill(style)
        case .capsule:
            Capsule().fill(style)
        case .panel(let radius):
            RoundedRectangle(cornerRadius: radius, style: .continuous).fill(style)
        }
    }
}

private struct ElectronicMailGlassShapeStroke<Style: ShapeStyle>: View {
    let shape: ElectronicMailGlassShape
    let style: Style
    let lineWidth: CGFloat

    @ViewBuilder
    var body: some View {
        switch shape {
        case .circle:
            Circle().strokeBorder(style, lineWidth: lineWidth)
        case .capsule:
            Capsule().strokeBorder(style, lineWidth: lineWidth)
        case .panel(let radius):
            RoundedRectangle(cornerRadius: radius, style: .continuous)
                .strokeBorder(style, lineWidth: lineWidth)
        }
    }
}

private struct ElectronicMailGlassContentShape: Shape {
    let shape: ElectronicMailGlassShape

    func path(in rect: CGRect) -> Path {
        switch shape {
        case .circle:
            return Circle().path(in: rect)
        case .capsule:
            return Capsule().path(in: rect)
        case .panel(let radius):
            return RoundedRectangle(cornerRadius: radius, style: .continuous).path(in: rect)
        }
    }
}

public enum ElectronicMailType {
    public static let heroTitleSize: CGFloat = 28
    public static let welcomeBodySize: CGFloat = 15
    public static let titleSize: CGFloat = 17
    public static let mailboxHeaderSize: CGFloat = 20
    public static let sectionTitleSize: CGFloat = 17
    public static let bodySize: CGFloat = 15
    public static let bodyLineHeight: CGFloat = 20
    public static let iconSize: CGFloat = 18
    public static let smallSize: CGFloat = 13
    public static let detailSize: CGFloat = 13
    public static let statusSize: CGFloat = 13
    public static func heroTitle(weight: Font.Weight = .semibold) -> Font {
        .system(size: heroTitleSize, weight: weight)
    }

    public static func welcomeBody(weight: Font.Weight = .regular) -> Font {
        .system(size: welcomeBodySize, weight: weight)
    }

    public static func title(weight: Font.Weight = .bold) -> Font {
        .system(size: titleSize, weight: weight)
    }

    public static func mailboxHeader(weight: Font.Weight = .semibold) -> Font {
        .system(size: mailboxHeaderSize, weight: weight)
    }

    public static func headerTitle(weight: Font.Weight = .bold) -> Font {
        .system(size: sectionTitleSize, weight: weight)
    }

    public static func sectionTitle(weight: Font.Weight = .semibold) -> Font {
        .system(size: sectionTitleSize, weight: weight)
    }

    public static func body(weight: Font.Weight = .regular) -> Font {
        .system(size: bodySize, weight: weight)
    }

    public static func detail(weight: Font.Weight = .regular) -> Font {
        .system(size: detailSize, weight: weight)
    }

    public static func small(weight: Font.Weight = .regular) -> Font {
        .system(size: smallSize, weight: weight)
    }

    public static func status(weight: Font.Weight = .medium) -> Font {
        .system(size: statusSize, weight: weight)
    }

    public static func icon(weight: Font.Weight = .regular) -> Font {
        .system(size: iconSize, weight: weight)
    }
}

/// Mailbox-specific type roles use SF Pro at a unified 15-point inbox scale.
/// Weight and color preserve hierarchy without shrinking secondary content.
public enum ElectronicMailMailboxType {
    public static let rowHeight: CGFloat = 35
    public static let sidebarHeaderSize: CGFloat = 10
    public static let sidebarItemSize: CGFloat = 13
    public static let sidebarAccountSize: CGFloat = 12
    /// Navigation expands directly from the shell title, so its labels use the
    /// exact same 20-point semibold type role with a comfortably sized hit area.
    public static let navigationRowHeight: CGFloat = 36
    public static let sectionSize: CGFloat = 17
    public static let sectionOpacity: CGFloat = 0.50
    public static let senderSize: CGFloat = 15
    public static let subjectSize: CGFloat = 15
    public static let metadataSize: CGFloat = 15

    public static func sidebarHeader() -> Font {
        .caption.weight(.semibold)
    }

    public static func sidebarItem(selected: Bool = false) -> Font {
        .body.weight(selected ? .semibold : .regular)
    }

    public static func navigationItem() -> Font {
        ElectronicMailType.mailboxHeader(weight: .semibold)
    }

    public static func sidebarAccount() -> Font {
        .callout.weight(.medium)
    }

    public static func section() -> Font {
        .system(size: sectionSize, weight: .regular)
    }

    public static func sender(unread: Bool) -> Font {
        .system(size: senderSize, weight: unread ? .semibold : .regular)
    }

    public static func subject(unread: Bool) -> Font {
        .system(size: subjectSize, weight: unread ? .semibold : .regular)
    }

    public static func metadata(unread _: Bool = false) -> Font {
        .system(size: metadataSize, weight: .regular)
    }
}

/// Pure window geometry keeps launch presentation deterministic and unit-testable
/// without requiring an attached AppKit window or a particular display.
public enum ElectronicMailWindowLayout {
    public static func mainFrame(in visibleFrame: CGRect) -> CGRect {
        let margin = ElectronicMailControlMetrics.mainWindowBackdropInset
        let availableWidth = max(1, visibleFrame.width - margin * 2)
        let availableHeight = max(1, visibleFrame.height - margin * 2)
        let width = min(ElectronicMailControlMetrics.mainWindowWidth, availableWidth)
        let height = min(ElectronicMailControlMetrics.mainWindowHeight, availableHeight)

        return CGRect(
            x: visibleFrame.midX - width / 2,
            y: visibleFrame.midY - height / 2,
            width: width,
            height: height
        )
    }
}

/// Composer roles share the app's 15-point primary reading scale. Status text
/// remains slightly quieter, but field labels, values, body, and controls no
/// longer fall back to macOS's smaller 13-point default body size.
public enum ElectronicMailComposerType {
    public static let modeSize: CGFloat = 17
    public static let labelSize: CGFloat = 15
    public static let valueSize: CGFloat = 15
    public static let bodySize: CGFloat = 15
    public static let controlSize: CGFloat = 15
    public static let statusSize: CGFloat = 13

    public static func mode(weight: Font.Weight = .medium) -> Font {
        .system(size: modeSize, weight: weight)
    }

    public static func label() -> Font {
        .system(size: labelSize, weight: .medium)
    }

    public static func value() -> Font {
        .system(size: valueSize, weight: .regular)
    }

    public static func body() -> Font {
        .system(size: bodySize, weight: .regular)
    }

    public static func control(weight: Font.Weight = .medium) -> Font {
        .system(size: controlSize, weight: weight)
    }

    public static func status() -> Font {
        .system(size: statusSize, weight: .regular)
    }
}

public enum ElectronicMailReaderType {
    public static let titleSize: CGFloat = 17
    public static let bodySize: CGFloat = 15
    public static let metadataSize: CGFloat = 13

    public static func title() -> Font {
        .system(size: titleSize, weight: .semibold)
    }

    public static func sender(weight: Font.Weight = .semibold) -> Font {
        .system(size: bodySize, weight: weight)
    }

    public static func body(weight: Font.Weight = .regular) -> Font {
        .system(size: bodySize, weight: weight)
    }

    public static func metadata(weight: Font.Weight = .regular) -> Font {
        .system(size: metadataSize, weight: weight)
    }

    public static func action(weight: Font.Weight = .regular) -> Font {
        .system(size: bodySize, weight: weight)
    }
}

/// Shared editor geometry keeps the empty-state copy on the exact insertion
/// origin used by SwiftUI's macOS TextEditor.
enum ElectronicMailComposerEditorLayout {
    static let textEditorHorizontalInset: CGFloat = -5
    static let textEditorVerticalInset: CGFloat = 6
    static let nativeLineFragmentPadding: CGFloat = 5
    static let placeholderHorizontalInset = textEditorHorizontalInset + nativeLineFragmentPadding
    static let placeholderVerticalInset = textEditorVerticalInset
}

public struct ElectronicMailHamburgerIcon: View {
    private let sourceWidth: CGFloat = 60
    private let sourceHeight: CGFloat = 54

    public init() {}

    public var body: some View {
        GeometryReader { proxy in
            let sourceRatio = sourceWidth / sourceHeight
            let width = min(proxy.size.width, proxy.size.height * sourceRatio)
            let height = width / sourceRatio
            let scale = width / sourceWidth
            let x = (proxy.size.width - width) / 2
            let y = (proxy.size.height - height) / 2
            let barWidth = 59.1309 * scale
            let barHeight = 7.0312 * scale
            let radius = 3.5156 * scale
            let barYPositions: [CGFloat] = [0, 23.291, 46.582]

            ForEach(barYPositions, id: \.self) { barY in
                RoundedRectangle(cornerRadius: radius, style: .continuous)
                    .fill(.foreground)
                    .frame(width: barWidth, height: barHeight)
                    .position(
                        x: x + barWidth / 2,
                        y: y + barY * scale + barHeight / 2
                    )
            }
        }
        .aspectRatio(sourceWidth / sourceHeight, contentMode: .fit)
    }
}

public struct ElectronicMailRefreshFailureToast: View {
    private let message: String
    @Environment(\.electronicMailGlassRenderingMode) private var renderingMode
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.colorSchemeContrast) private var contrast

    public init(message: String) {
        self.message = message
    }

    public var body: some View {
        VStack {
            Spacer()

            failureSurface
                .padding(.bottom, 22)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .allowsHitTesting(false)
    }

    private var failureLabel: some View {
        HStack(spacing: 8) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: ElectronicMailType.statusSize, weight: .semibold))
                .accessibilityHidden(true)

            Text(message)
                .font(ElectronicMailType.status(weight: .semibold))
                .lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)
        }
        .foregroundStyle(ElectronicMailDesign.errorAccent)
        .padding(.horizontal, 14)
        .padding(.vertical, 9)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Error: \(message)")
    }

    @ViewBuilder
    private var failureSurface: some View {
        if #available(macOS 26.0, *), usesNativeGlass {
            failureLabel
                .glassEffect(.regular, in: Capsule())
                .overlay(errorBorder)
        } else {
            failureLabel
                .background {
                    if reduceTransparency {
                        Capsule().fill(ElectronicMailDesign.panelFill(for: colorScheme))
                    } else {
                        Capsule().fill(.regularMaterial)
                    }
                }
                .overlay(errorBorder)
        }
    }

    private var errorBorder: some View {
        Capsule()
            .strokeBorder(
                ElectronicMailDesign.errorAccent.opacity(contrast == .increased ? 1 : 0.90),
                lineWidth: contrast == .increased ? 2 : 1.25
            )
    }

    private var usesNativeGlass: Bool {
        renderingMode.usesNativeGlass(
            osMajorVersion: ProcessInfo.processInfo.operatingSystemVersion.majorVersion,
            reduceTransparency: reduceTransparency
        )
    }
}

#if DEBUG
private struct ElectronicMailGlassPreviewGallery: View {
    let renderingMode: ElectronicMailGlassRenderingMode

    var body: some View {
        VStack(alignment: .leading, spacing: 24) {
            Text(renderingMode == .fallback ? "Material fallback" : "Native Liquid Glass")
                .font(.headline)

            ElectronicMailGlassGroup(spacing: ElectronicMailControlMetrics.headerControlGap) {
                HStack(spacing: ElectronicMailControlMetrics.headerControlGap) {
                    previewIcon("line.3.horizontal", role: .standard)
                    previewIcon("square.and.pencil", role: .prominent)
                    previewIcon("trash", role: .destructive)
                }
            }

            ElectronicMailGlassGroup(spacing: ElectronicMailControlMetrics.actionGap) {
                HStack(spacing: ElectronicMailControlMetrics.actionGap) {
                    previewLabel("Ask", symbol: "magnifyingglass", role: .brandedAsk)
                    previewLabel("Forward", symbol: "arrowshape.turn.up.right", role: .standard)
                    previewLabel("Reply", symbol: "arrowshape.turn.up.left", role: .prominent)
                }
            }

            previewLabel("Disabled", symbol: "nosign", role: .standard)
                .disabled(true)
        }
        .padding(32)
        .frame(width: 620)
        .background(Color(nsColor: .windowBackgroundColor))
        .electronicMailGlassRenderingMode(renderingMode)
    }

    private func previewIcon(_ symbol: String, role: ElectronicMailGlassRole) -> some View {
        Button {} label: {
            Image(systemName: symbol)
                .frame(
                    width: ElectronicMailControlMetrics.headerControlSize,
                    height: ElectronicMailControlMetrics.headerControlSize
                )
                .contentShape(Circle())
        }
        .electronicMailGlassButton(role: role, shape: .circle)
    }

    private func previewLabel(
        _ title: String,
        symbol: String,
        role: ElectronicMailGlassRole
    ) -> some View {
        Button {} label: {
            Label(title, systemImage: symbol)
                .padding(.horizontal, 16)
                .frame(minHeight: ElectronicMailControlMetrics.actionHeight)
                .contentShape(Capsule())
        }
        .electronicMailGlassButton(role: role, shape: .capsule)
    }
}

#Preview("Liquid Glass") {
    ElectronicMailGlassPreviewGallery(renderingMode: .automatic)
}

#Preview("Liquid Glass Fallback") {
    ElectronicMailGlassPreviewGallery(renderingMode: .fallback)
}
#endif
