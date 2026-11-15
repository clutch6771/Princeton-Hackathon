import Observation
import SwiftUI
import UIKit

// A colour preset. Every colour the app draws with is derived from these few values.
struct IrisTheme: Identifiable, Equatable {
    let id: String
    let name: String
    let stops: [UIColor] // three gradient colours, brightest first
    let base: UIColor // dark ground the gradient sits on
    let accentUI: UIColor // controls, selection, highlights
    let flagUI: UIColor // flagged-reel emphasis; distinct from the accent
    var glow: CGFloat = 1 // backdrop glow strength; lowered for very bright palettes

    static func == (a: IrisTheme, b: IrisTheme) -> Bool { a.id == b.id }

    var canvas: Color { Color(uiColor: base.mixed(with: .black, 0.5)) }
    var card: Color { Color(uiColor: base.mixed(with: .white, 0.07)) }
    var surface: Color { Color(uiColor: base.mixed(with: stops[2], 0.3)) }
    var accent: Color { Color(uiColor: accentUI) }
    var flag: Color { Color(uiColor: flagUI) }
    // A pale tint of the accent: reads clearly on the dark, moving splash gradient.
    var wordmark: Color { Color(uiColor: accentUI.mixed(with: .white, 0.7)) }

    var background: LinearGradient {
        let colors = [base.mixed(with: .black, 0.5), base, base.mixed(with: stops[2], 0.3), base.mixed(with: stops[1], 0.25)]
        return LinearGradient(colors: colors.map { Color(uiColor: $0) }, startPoint: .topLeading, endPoint: .bottomTrailing)
    }
    var startButton: LinearGradient {
        LinearGradient(colors: [stops[1], stops[0], accentUI].map { Color(uiColor: $0) }, startPoint: .topLeading, endPoint: .bottomTrailing)
    }
    var stopButton: LinearGradient {
        LinearGradient(colors: [stops[1], stops[2], base].map { Color(uiColor: $0) }, startPoint: .topLeading, endPoint: .bottomTrailing)
    }
    var swatch: LinearGradient {
        LinearGradient(colors: (stops + [base]).map { Color(uiColor: $0) }, startPoint: .topTrailing, endPoint: .bottomLeading)
    }

    // The first three take their colours from the Default, Life and Tranquility icons.
    static let presets: [IrisTheme] = [
        IrisTheme(id: "default", name: "Default", stops: [hex(0x24DE97), hex(0x27BCA3), hex(0x793299)], base: hex(0x1D1046),
                  accentUI: hex(0x24DE97), flagUI: hex(0xB58CE6)),
        IrisTheme(id: "life", name: "Life", stops: [hex(0x1FDD29), hex(0x11C7D4), hex(0x39BAA6)], base: hex(0x062B2A),
                  accentUI: hex(0x40E379), flagUI: hex(0x11C7D4), glow: 0.7),
        IrisTheme(id: "tranquility", name: "Tranquility", stops: [hex(0xF511E6), hex(0xA126CA), hex(0x2D4670)], base: hex(0x17234F),
                  accentUI: hex(0xF45DEB), flagUI: hex(0x7FA2E8)),
        IrisTheme(id: "ember", name: "Ember", stops: [hex(0xDE7124), hex(0xDF257F), hex(0x2328C1)], base: hex(0x1D1046),
                  accentUI: hex(0xFF8A3D), flagUI: hex(0xF0508F)),
        IrisTheme(id: "midnight", name: "Midnight", stops: [hex(0x5CC8FF), hex(0x3B6CF6), hex(0x2A2F8F)], base: hex(0x0A1030),
                  accentUI: hex(0x5CC8FF), flagUI: hex(0x8C9BFF)),
        IrisTheme(id: "rosewood", name: "Rosewood", stops: [hex(0xFFB48A), hex(0xF0508F), hex(0x7A3E9D)], base: hex(0x2A1136),
                  accentUI: hex(0xFF9E8A), flagUI: hex(0xC58BF2)),
    ]

    private static func hex(_ value: UInt32) -> UIColor {
        UIColor(red: CGFloat((value >> 16) & 0xFF) / 255, green: CGFloat((value >> 8) & 0xFF) / 255, blue: CGFloat(value & 0xFF) / 255, alpha: 1)
    }
}

// The theme in use. Views that read colours through it redraw when it changes.
// The choice is saved by preset id, so it survives a restart and the splash opens in it.
@Observable final class IrisThemeStore {
    static let shared = IrisThemeStore()
    static let storageKey = "irisThemeID"
    private(set) var theme: IrisTheme
    @ObservationIgnored private let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        // A missing id, or one from a preset that no longer exists, falls back to the default theme.
        let saved = defaults.string(forKey: Self.storageKey)
        theme = IrisTheme.presets.first { $0.id == saved } ?? IrisTheme.presets[0]
    }

    func select(_ theme: IrisTheme) {
        self.theme = theme
        defaults.set(theme.id, forKey: Self.storageKey)
    }
}

extension UIColor {
    func mixed(with other: UIColor, _ amount: CGFloat) -> UIColor {
        var (r1, g1, b1, a1): (CGFloat, CGFloat, CGFloat, CGFloat) = (0, 0, 0, 0)
        var (r2, g2, b2, a2): (CGFloat, CGFloat, CGFloat, CGFloat) = (0, 0, 0, 0)
        getRed(&r1, green: &g1, blue: &b1, alpha: &a1)
        other.getRed(&r2, green: &g2, blue: &b2, alpha: &a2)
        return UIColor(red: r1 + (r2 - r1) * amount, green: g1 + (g2 - g1) * amount, blue: b1 + (b2 - b1) * amount, alpha: 1)
    }
}
