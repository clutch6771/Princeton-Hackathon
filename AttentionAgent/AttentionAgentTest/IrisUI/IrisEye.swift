import SwiftUI

// The eye from userInterface/IrisBranding (IrisClosed.svg -> IrisOpen.svg), drawn as vectors.
// Closed is the lower lid alone; opening raises the upper lid from flat and grows the iris in.
// Paths are only recomputed while the transition is running.
struct IrisEye: View {
    let open: Bool

    var body: some View {
        let amount: CGFloat = open ? 1 : 0
        ZStack {
            IrisEyePart(part: .lowerLid, openness: amount)
            IrisEyePart(part: .upperLid, openness: amount)
            IrisEyePart(part: .iris, openness: amount)
        }
        .foregroundStyle(.white.opacity(0.95))
        .animation(open ? .spring(response: 0.6, dampingFraction: 0.72) : .easeInOut(duration: 0.4), value: open)
    }
}

struct IrisEyePart: Shape {
    enum Part { case lowerLid, upperLid, iris }

    let part: Part
    var openness: CGFloat

    var animatableData: CGFloat {
        get { openness }
        set { openness = newValue }
    }

    func path(in rect: CGRect) -> Path {
        var shape: Path
        switch part {
        case .lowerLid:
            shape = Self.lowerLid
        case .upperLid:
            // Flat (invisible) when closed; the spring may carry it slightly past fully open.
            let lift = max(openness, 0)
            guard lift > 0.001 else { return Path() }
            shape = Self.upperLid.applying(Self.scale(x: 1, y: lift))
        case .iris:
            let size = min(max(openness, 0), 1)
            guard size > 0.001 else { return Path() }
            shape = Self.iris.applying(Self.scale(x: size, y: size))
        }
        // Fit the 191 x 155 artwork, keeping the closed (lower-half) glyph vertically centred.
        let fit = min(rect.width / Self.artwork.width, rect.height / Self.artwork.height)
        let settled = min(max(openness, 0), 1)
        let centreY = Self.centre.y + (1 - settled) * 38.6
        let place = CGAffineTransform(translationX: rect.midX - Self.centre.x * fit, y: rect.midY - centreY * fit)
            .scaledBy(x: fit, y: fit)
        return shape.applying(place)
    }

    private static let artwork = CGSize(width: 191, height: 155)
    private static let centre = CGPoint(x: 95.5, y: 77.5)

    private static func scale(x: CGFloat, y: CGFloat) -> CGAffineTransform {
        CGAffineTransform(translationX: centre.x, y: centre.y).scaledBy(x: x, y: y).translatedBy(x: -centre.x, y: -centre.y)
    }

    private static func pt(_ x: CGFloat, _ y: CGFloat) -> CGPoint { CGPoint(x: x, y: y) }

    private static let lowerLid: Path = {
        var p = Path()
        p.move(to: pt(190.75, 77.5))
        p.addCurve(to: pt(157.32, 112.89), control1: pt(182.6, 92.2), control2: pt(171.41, 104.15))
        p.addLine(to: pt(170.26, 135.25))
        p.addLine(to: pt(155.81, 143.6))
        p.addLine(to: pt(142.85, 121.15))
        p.addCurve(to: pt(103.833, 131.55), control1: pt(130.75, 127.45), control2: pt(118.25, 130.45))
        p.addLine(to: pt(103.833, 154.75))
        p.addLine(to: pt(87.167, 154.75))
        p.addLine(to: pt(87.167, 131.55))
        p.addCurve(to: pt(48.15, 121.15), control1: pt(72.75, 130.45), control2: pt(60.25, 127.45))
        p.addLine(to: pt(35.19, 143.6))
        p.addLine(to: pt(20.74, 135.25))
        p.addLine(to: pt(33.68, 112.89))
        p.addCurve(to: pt(0.25, 77.5), control1: pt(19.59, 104.15), control2: pt(8.4, 92.2))
        p.addLine(to: pt(20.5, 77.5))
        p.addCurve(to: pt(49.38, 103.34), control1: pt(28.22, 88.49), control2: pt(38.07, 97.04))
        p.addCurve(to: pt(95.5, 115.17), control1: pt(63.61, 111.26), control2: pt(79.67, 115.34))
        p.addCurve(to: pt(141.62, 103.34), control1: pt(111.33, 115.34), control2: pt(127.39, 111.26))
        p.addCurve(to: pt(170.5, 77.5), control1: pt(152.93, 97.04), control2: pt(162.78, 88.49))
        p.closeSubpath()
        return p
    }()

    private static let upperLid: Path = {
        var p = Path()
        p.move(to: pt(0.25, 77.5))
        p.addCurve(to: pt(33.68, 42.3), control1: pt(8.4, 64), control2: pt(19.57, 51.92))
        p.addLine(to: pt(20.74, 19.92))
        p.addLine(to: pt(35.19, 11.58))
        p.addLine(to: pt(48.15, 34.03))
        p.addCurve(to: pt(87.167, 23.45), control1: pt(60.25, 27.95), control2: pt(72.75, 25.05))
        p.addLine(to: pt(87.167, 0.25))
        p.addLine(to: pt(103.833, 0.25))
        p.addLine(to: pt(103.833, 23.45))
        p.addCurve(to: pt(142.85, 34.03), control1: pt(118.25, 25.05), control2: pt(130.75, 27.95))
        p.addLine(to: pt(155.79, 11.58))
        p.addLine(to: pt(170.26, 19.92))
        p.addLine(to: pt(157.32, 42.3))
        p.addCurve(to: pt(190.75, 77.5), control1: pt(171.43, 51.92), control2: pt(182.6, 64))
        p.addLine(to: pt(170.5, 77.5))
        p.addCurve(to: pt(142.45, 52.9), control1: pt(163.05, 68.01), control2: pt(153.5, 59.35))
        p.addCurve(to: pt(95.5, 40), control1: pt(127.75, 44.65), control2: pt(111.7, 40.2))
        p.addCurve(to: pt(48.55, 52.9), control1: pt(79.3, 40.2), control2: pt(63.25, 44.65))
        p.addCurve(to: pt(20.5, 77.5), control1: pt(37.5, 59.35), control2: pt(27.95, 68.01))
        p.closeSubpath()
        return p
    }()

    private static let iris: Path = {
        var p = Path()
        p.move(to: pt(95.5654, 50))
        p.addCurve(to: pt(123, 77.5), control1: pt(110.723, 50.0353), control2: pt(123, 62.334))
        p.addCurve(to: pt(95.5, 105), control1: pt(123, 92.6878), control2: pt(110.688, 105))
        p.addCurve(to: pt(68, 77.5), control1: pt(80.3122, 105), control2: pt(68, 92.6878))
        p.addCurve(to: pt(70.2588, 66.5713), control1: pt(68, 73.6163), control2: pt(68.8064, 69.9212))
        p.addCurve(to: pt(80.5713, 70.4287), control1: pt(73.0186, 68.9737), control2: pt(76.625, 70.4287))
        p.addCurve(to: pt(96.2861, 54.7139), control1: pt(89.25, 70.4287), control2: pt(96.2861, 63.3926))
        p.addCurve(to: pt(95.5654, 50), control1: pt(96.2861, 53.0715), control2: pt(96.0329, 51.4882))
        p.closeSubpath()
        return p
    }()
}
