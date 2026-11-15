import SwiftUI

// Launch screen: the Iris word mark over a gently moving gradient in the current theme,
// with the eye blinking at the bottom as the loading indicator.
struct IrisSplash: View {
    var body: some View {
        let theme = IrisThemeStore.shared.theme
        ZStack {
            IrisBackdrop(theme: theme, dust: false).ignoresSafeArea()
            IrisWordMark().fill(theme.wordmark)
                .frame(width: 190, height: 112)
                .shadow(color: theme.canvas.opacity(0.55), radius: 14)
                .accessibilityLabel("Iris")
            VStack {
                Spacer()
                IrisEyeLoader(color: theme.wordmark).frame(width: 46, height: 38).padding(.bottom, 36)
            }
        }
    }
}

// The open/close eye on a loop, for use as a small progress indicator.
struct IrisEyeLoader: View {
    var color: Color = .white
    @State private var open = false

    var body: some View {
        IrisEye(open: open, color: color)
            .accessibilityLabel("Loading")
            .task {
                while !Task.isCancelled {
                    try? await Task.sleep(for: .milliseconds(750))
                    open.toggle()
                }
            }
    }
}

// userInterface/IrisBranding/IrisWordMark.svg as a shape.
struct IrisWordMark: Shape {
    func path(in rect: CGRect) -> Path {
        let fit = min(rect.width / 212, rect.height / 124)
        let place = CGAffineTransform(translationX: rect.midX - 106 * fit, y: rect.midY - 62 * fit).scaledBy(x: fit, y: fit)
        return Self.artwork.applying(place)
    }

    private static let artwork: Path = {
        var path = Path()
        for outline in outlines { path.addPath(Path(svg: outline)) }
        return path
    }()

    private static let outlines = [
        "M11.0352 0C17.0898 0 21.9727 4.98047 21.9727 11.0352C21.9727 17.0898 17.0898 22.0703 11.0352 22.0703C4.88281 22.0703 0 17.0898 0 11.0352C0 4.98047 4.88281 0 11.0352 0ZM0 28.3203H21.9727V123.926H0V28.3203Z",
        "M77.832 28.0273C86.6211 27.9297 95.8008 31.0547 102.051 36.9141L87.5977 53.9062C85.7422 32.2266 55.7617 34.5703 55.7617 53.8086V123.926H33.7891V28.3203H55.7617V38.5742C60.6445 31.543 69.043 28.125 77.832 28.0273Z",
        "M108.984 28.3203H130.957V123.926H108.984V28.3203Z",
        "M176.758 28.3203C192.969 28.3203 202.637 38.8672 209.863 54.1992L191.504 62.0117C193.066 45.2148 185.449 32.6172 176.758 32.6172C168.164 32.6172 160.645 42.1875 161.914 57.4219C163.281 73.1445 204.883 64.9414 210.645 90.625C214.258 111.23 194.238 123.926 173.145 123.926C157.031 123.926 141.504 113.77 140.137 98.0469L158.398 90.2344C156.348 106.445 164.453 119.727 173.145 119.727C183.789 119.727 189.746 107.129 188.574 95.3125C185.938 80.3711 143.652 89.7461 139.941 62.1094C138.574 41.7969 155.664 28.3203 176.758 28.3203Z",
        "M142.941 5.17578C141.012 8.51751 138.392 11.3962 135.269 13.6309L138.223 18.748L133.893 21.248L130.95 16.1514C128.224 17.4201 125.306 18.2251 122.315 18.5322V23.6758H117.315V18.5801C114.318 18.3302 111.384 17.5813 108.634 16.3643L105.815 21.248L101.485 18.748L104.268 13.9277C101.117 11.7629 98.4531 8.94819 96.4629 5.66211L100.74 3.07226C102.721 6.34199 105.518 9.03934 108.858 10.8984C112.199 12.7576 115.966 13.7149 119.789 13.6748C123.611 13.6348 127.357 12.5996 130.658 10.6709C133.959 8.74226 136.699 5.98659 138.611 2.67578L142.941 5.17578Z",
    ]
}

extension Path {
    // Minimal SVG path reader: absolute M, L, H, V, C and Z only, which is all the brand artwork uses.
    init(svg: String) {
        self.init()
        var numbers: [CGFloat] = []
        var command: Character = "M"
        var token = ""
        var current = CGPoint.zero
        func flushToken() {
            if let value = Double(token) { numbers.append(CGFloat(value)) }
            token = ""
        }
        func apply(_ path: inout Path) {
            switch command {
            case "M" where numbers.count >= 2:
                current = CGPoint(x: numbers[0], y: numbers[1]); path.move(to: current)
            case "L" where numbers.count >= 2:
                current = CGPoint(x: numbers[0], y: numbers[1]); path.addLine(to: current)
            case "H" where numbers.count >= 1:
                current.x = numbers[0]; path.addLine(to: current)
            case "V" where numbers.count >= 1:
                current.y = numbers[0]; path.addLine(to: current)
            case "C" where numbers.count >= 6:
                current = CGPoint(x: numbers[4], y: numbers[5])
                path.addCurve(to: current, control1: CGPoint(x: numbers[0], y: numbers[1]), control2: CGPoint(x: numbers[2], y: numbers[3]))
            case "Z":
                path.closeSubpath()
            default:
                break
            }
            numbers = []
        }
        for character in svg {
            if character.isLetter {
                flushToken(); apply(&self); command = character
            } else if character == " " || character == "," {
                flushToken()
            } else {
                token.append(character)
            }
        }
        flushToken(); apply(&self)
    }
}
