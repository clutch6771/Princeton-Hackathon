import CoreText
import SwiftUI
import UIKit

// Iris UI layer: live backdrop, animated eye, Silver Country type.
// Each switch below turns one piece off and restores the previous look.
enum IrisUI {
    static let liveBackdrop = true
    static let irisEye = true
    static let silverCountry = true

    // Gradient stops from userInterface/IrisBranding/Default.svg.
    static let mint = UIColor(red: 36/255, green: 222/255, blue: 151/255, alpha: 1)
    static let teal = UIColor(red: 39/255, green: 188/255, blue: 163/255, alpha: 1)
    static let purple = UIColor(red: 121/255, green: 50/255, blue: 153/255, alpha: 1)
    static let deep = UIColor(red: 29/255, green: 16/255, blue: 70/255, alpha: 1)

    private static let fontName = "SilverCountryRegular"

    // Registered once, from the bundled file, so no Info.plist entry is needed.
    private static let fontReady: Bool = {
        guard silverCountry else { return false }
        if UIFont(name: fontName, size: 12) != nil { return true }
        let bundled = Bundle.main.url(forResource: "Silver Country", withExtension: "otf")
            ?? Bundle.main.url(forResource: "Silver Country", withExtension: "otf", subdirectory: "IrisUI")
        guard let url = bundled else { return false }
        CTFontManagerRegisterFontsForURL(url as CFURL, .process, nil)
        return UIFont(name: fontName, size: 12) != nil
    }()

    static func font(_ style: Font.TextStyle) -> Font {
        guard fontReady else { return .system(style) }
        return .custom(fontName, size: pointSize(style), relativeTo: style)
    }

    static func font(size: CGFloat) -> Font {
        fontReady ? .custom(fontName, fixedSize: size) : .system(size: size, weight: .bold, design: .rounded)
    }

    // Navigation titles are drawn by UIKit, so they need the font separately.
    static func install() {
        guard fontReady, let large = UIFont(name: fontName, size: 34), let inline = UIFont(name: fontName, size: 18) else { return }
        UINavigationBar.appearance().largeTitleTextAttributes = [.font: large]
        UINavigationBar.appearance().titleTextAttributes = [.font: inline]
    }

    private static func pointSize(_ style: Font.TextStyle) -> CGFloat {
        switch style {
        case .largeTitle: return 34
        case .title: return 28
        case .title2: return 22
        case .title3: return 20
        case .callout: return 16
        case .subheadline: return 15
        case .footnote: return 13
        case .caption: return 12
        case .caption2: return 11
        default: return 17
        }
    }
}
