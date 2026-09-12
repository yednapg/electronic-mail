import SwiftUI

enum IOSMailDesign {
    static let accent = Color(red: 0.20, green: 0.45, blue: 0.98)
    static let success = Color(red: 0.16, green: 0.66, blue: 0.38)
    static let warning = Color(red: 0.96, green: 0.58, blue: 0.12)

    static func canvas(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color(red: 0.035, green: 0.04, blue: 0.055) : Color(red: 0.965, green: 0.97, blue: 0.98)
    }

    static func surface(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color(red: 0.075, green: 0.085, blue: 0.11) : .white
    }

    static func elevated(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color.white.opacity(0.08) : Color.black.opacity(0.045)
    }

    static func border(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color.white.opacity(0.12) : Color.black.opacity(0.10)
    }

    static func primaryText(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? .white : Color(red: 0.08, green: 0.09, blue: 0.12)
    }

    static func secondaryText(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color.white.opacity(0.62) : Color.black.opacity(0.57)
    }
}

extension View {
    func mailCard(cornerRadius: CGFloat = 18) -> some View {
        modifier(IOSMailCardModifier(cornerRadius: cornerRadius))
    }

    func minimumTouchTarget() -> some View {
        frame(minWidth: 44, minHeight: 44)
        .contentShape(Rectangle())
    }
}

private struct IOSMailCardModifier: ViewModifier {
    @Environment(\.colorScheme) private var colorScheme
    let cornerRadius: CGFloat

    func body(content: Content) -> some View {
        content
            .background(IOSMailDesign.surface(colorScheme), in: RoundedRectangle(cornerRadius: cornerRadius, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                    .stroke(IOSMailDesign.border(colorScheme), lineWidth: 0.7)
            }
    }
}

enum IOSAppearance: String, CaseIterable, Identifiable {
    case system
    case light
    case dark

    var id: Self { self }
    var title: String { rawValue.capitalized }
    var colorScheme: ColorScheme? {
        switch self {
        case .system: nil
        case .light: .light
        case .dark: .dark
        }
    }
}
