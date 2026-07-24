import AppKit
import SwiftUI

public enum ElectronicMailDesign {
    /// Electronic Mail's original brand accent. Keep this independent from the
    /// user's system accent so the product identity remains black and blue.
    public static let appleBlue = Color(red: 0, green: 90.0 / 255.0, blue: 205.0 / 255.0)
    public static let green = Color(red: 0, green: 190.0 / 255.0, blue: 36.0 / 255.0)
    public static let sidebarBackground = Color.black
    public static let sidebarText = Color.white.opacity(0.90)
    public static let sidebarSecondaryText = Color.white.opacity(0.58)

    public static func background(for colorScheme: ColorScheme) -> Color {
        colorScheme == .dark ? .black : .white
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
        Color(nsColor: .secondaryLabelColor)
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

    public static func controlFill(for colorScheme: ColorScheme, selected: Bool = false) -> Color {
        if selected {
            return appleBlue.opacity(colorScheme == .dark ? 0.24 : 0.13)
        }
        return Color(nsColor: .controlBackgroundColor)
    }
}

public enum ElectronicMailType {
    public static let titleSize: CGFloat = 22
    public static let sectionTitleSize: CGFloat = 22
    public static let bodySize: CGFloat = 20
    public static let bodyLineHeight: CGFloat = 40
    public static let iconSize: CGFloat = 22
    public static let smallSize: CGFloat = 16
    public static let detailSize: CGFloat = 18
    public static let statusSize: CGFloat = 13
    public static let titleTracking: CGFloat = titleSize * 0.02
    public static let bodyTracking: CGFloat = bodySize * 0.015

    public static func title(weight: Font.Weight = .bold) -> Font {
        .system(size: titleSize, weight: weight, design: .rounded)
    }

    public static func headerTitle(weight: Font.Weight = .bold) -> Font {
        title(weight: weight)
    }

    public static func sectionTitle(weight: Font.Weight = .semibold) -> Font {
        .system(size: sectionTitleSize, weight: weight, design: .rounded)
    }

    public static func body(weight: Font.Weight = .regular) -> Font {
        .system(size: bodySize, weight: weight, design: .rounded)
    }

    public static func detail(weight: Font.Weight = .regular) -> Font {
        .system(size: detailSize, weight: weight, design: .rounded)
    }

    public static func small(weight: Font.Weight = .regular) -> Font {
        .system(size: smallSize, weight: weight, design: .rounded)
    }

    public static func status(weight: Font.Weight = .medium) -> Font {
        .system(size: statusSize, weight: weight, design: .rounded)
    }

    public static func icon(weight: Font.Weight = .regular) -> Font {
        .system(size: iconSize, weight: weight, design: .rounded)
    }
}

/// Mailbox-specific type roles. The reader, composer, and mailbox share the
/// product's larger rounded scale while preserving distinct semantic weights.
public enum ElectronicMailMailboxType {
    public static let rowHeight: CGFloat = 40
    public static let sidebarHeaderSize: CGFloat = 12
    public static let sidebarItemSize: CGFloat = 14
    public static let sidebarAccountSize: CGFloat = 13
    public static let sectionSize: CGFloat = 18
    public static let senderSize: CGFloat = 18
    public static let subjectSize: CGFloat = 18
    public static let metadataSize: CGFloat = 17

    public static func sidebarHeader() -> Font {
        .system(size: sidebarHeaderSize, weight: .semibold, design: .rounded)
    }

    public static func sidebarItem(selected: Bool = false) -> Font {
        .system(size: sidebarItemSize, weight: selected ? .semibold : .regular, design: .rounded)
    }

    public static func sidebarAccount() -> Font {
        .system(size: sidebarAccountSize, weight: .medium, design: .rounded)
    }

    public static func section() -> Font {
        .system(size: sectionSize, weight: .regular, design: .rounded)
    }

    public static func sender(unread: Bool) -> Font {
        .system(size: senderSize, weight: unread ? .semibold : .regular, design: .rounded)
    }

    public static func subject(unread _: Bool) -> Font {
        .system(size: subjectSize, weight: .regular, design: .rounded)
    }

    public static func metadata(unread _: Bool = false) -> Font {
        .system(size: metadataSize, weight: .regular, design: .rounded)
    }
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

    public init(message: String) {
        self.message = message
    }

    public var body: some View {
        VStack {
            Spacer()

            Text(message)
                .font(ElectronicMailType.status())
                .foregroundStyle(.secondary)
                .padding(.horizontal, 14)
                .padding(.vertical, 8)
                .background(.regularMaterial, in: Capsule())
                .padding(.bottom, 22)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .allowsHitTesting(false)
    }
}
