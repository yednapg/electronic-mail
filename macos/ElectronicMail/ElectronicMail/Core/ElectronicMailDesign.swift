import SwiftUI

public enum ElectronicMailDesign {
    public static let appleBlue = Color(red: 0.0, green: 90.0 / 255.0, blue: 205.0 / 255.0)
    public static let green = Color(red: 0.0, green: 190.0 / 255.0, blue: 36.0 / 255.0)

    public static func background(for colorScheme: ColorScheme) -> Color {
        colorScheme == .dark ? .black : .white
    }

    public static func selectedText(for colorScheme: ColorScheme) -> Color {
        .white
    }

    public static func panelFill(for colorScheme: ColorScheme) -> Color {
        colorScheme == .dark ? Color.white.opacity(0.075) : Color.black.opacity(0.045)
    }

    public static func panelBorder(for colorScheme: ColorScheme) -> Color {
        colorScheme == .dark ? Color.white.opacity(0.12) : Color.black.opacity(0.08)
    }

    public static func primaryText(for colorScheme: ColorScheme) -> Color {
        colorScheme == .dark ? Color.white.opacity(0.90) : Color.black.opacity(0.90)
    }

    public static func secondaryText(for colorScheme: ColorScheme) -> Color {
        colorScheme == .dark ? Color.white.opacity(0.75) : Color.black.opacity(0.75)
    }

    public static func unreadText(for colorScheme: ColorScheme) -> Color {
        primaryText(for: colorScheme)
    }

    public static func readText(for colorScheme: ColorScheme) -> Color {
        colorScheme == .dark ? Color.white.opacity(0.50) : Color.black.opacity(0.50)
    }

    public static func tertiaryText(for colorScheme: ColorScheme) -> Color {
        readText(for: colorScheme)
    }

    public static func sectionText(for colorScheme: ColorScheme) -> Color {
        colorScheme == .dark ? Color.white.opacity(0.25) : Color.black.opacity(0.25)
    }

    public static func divider(for colorScheme: ColorScheme) -> Color {
        colorScheme == .dark ? Color.white.opacity(0.10) : Color.black.opacity(0.10)
    }

    public static func controlFill(for colorScheme: ColorScheme, selected: Bool = false) -> Color {
        if selected {
            return appleBlue.opacity(colorScheme == .dark ? 0.24 : 0.13)
        }
        return colorScheme == .dark ? Color.white.opacity(0.08) : Color.black.opacity(0.035)
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
