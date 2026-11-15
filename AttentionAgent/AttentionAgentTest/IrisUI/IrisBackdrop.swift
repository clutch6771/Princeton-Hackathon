import SwiftUI
import UIKit

// Live backdrop: one base gradient, three drifting glows, a thin field of dust.
// Everything is a Core Animation layer animated by the system render server, so the app
// does no per-frame work and keeps no bitmaps beyond one 24 px particle dot.
struct IrisBackdrop: UIViewRepresentable {
    var theme = IrisThemeStore.shared.theme
    var dust = true
    func makeUIView(context: Context) -> IrisBackdropView { IrisBackdropView(theme: theme, dust: dust) }
    func updateUIView(_ view: IrisBackdropView, context: Context) { view.theme = theme }
}

final class IrisBackdropView: UIView {
    // Shared clock: every instance animates from the same origin, so recreating the view
    // (each tab switch rebuilds it) continues the motion instead of restarting it.
    private static let epoch = CACurrentMediaTime() - 30

    private let base = CAGradientLayer()
    private let glows: [CAGradientLayer]
    private let dust = CAEmitterLayer()
    private var laidOutSize = CGSize.zero

    var theme: IrisTheme {
        didSet { if theme != oldValue { recolor(); if laidOutSize != .zero { animate() } } }
    }

    private struct Glow {
        let stop: Int // index into the theme's gradient colours
        let alpha: CGFloat
        let route: [CGPoint] // unit coordinates, closed loop
        let period: CFTimeInterval
    }

    private static let plan = [
        Glow(stop: 2, alpha: 0.64,
             route: [CGPoint(x: 0.85, y: 0.15), CGPoint(x: 0.35, y: 0.30), CGPoint(x: 0.60, y: 0.65), CGPoint(x: 1.00, y: 0.45)], period: 25),
        Glow(stop: 1, alpha: 0.42,
             route: [CGPoint(x: 0.10, y: 0.85), CGPoint(x: 0.55, y: 0.95), CGPoint(x: 0.30, y: 0.55), CGPoint(x: -0.05, y: 0.60)], period: 32),
        Glow(stop: 0, alpha: 0.27,
             route: [CGPoint(x: 0.95, y: 0.90), CGPoint(x: 0.65, y: 0.75), CGPoint(x: 0.80, y: 1.05), CGPoint(x: 1.10, y: 0.80)], period: 40),
    ]

    init(theme: IrisTheme, dust showsDust: Bool) {
        self.theme = theme
        glows = Self.plan.map { _ in
            let layer = CAGradientLayer()
            layer.type = .radial
            layer.startPoint = CGPoint(x: 0.5, y: 0.5)
            layer.endPoint = CGPoint(x: 1, y: 1)
            layer.locations = [0, 0.45, 1]
            return layer
        }
        super.init(frame: .zero)
        isUserInteractionEnabled = false
        isOpaque = true
        clipsToBounds = true

        base.startPoint = CGPoint(x: 0, y: 0)
        base.endPoint = CGPoint(x: 1, y: 1)
        layer.addSublayer(base)
        glows.forEach(layer.addSublayer)

        let mote = CAEmitterCell()
        mote.contents = Self.dot
        mote.birthRate = 2.2
        mote.lifetime = 16
        mote.lifetimeRange = 4
        mote.velocity = 7
        mote.velocityRange = 5
        mote.emissionLongitude = -.pi / 2
        mote.emissionRange = .pi / 3
        mote.scale = 0.05
        mote.scaleRange = 0.03
        mote.scaleSpeed = 0.012
        mote.color = UIColor(red: 0.85, green: 1, blue: 0.95, alpha: 0.5).cgColor
        mote.alphaRange = 0.2
        mote.alphaSpeed = -0.035
        dust.emitterShape = .rectangle
        dust.emitterCells = [mote]
        dust.seed = 7
        dust.beginTime = Self.epoch
        if showsDust { layer.addSublayer(dust) }
        recolor()
    }

    private func recolor() {
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        backgroundColor = theme.base
        base.colors = restColors
        for (layer, glow) in zip(glows, Self.plan) {
            let alpha = glow.alpha * theme.glow
            layer.colors = [alpha, alpha * 0.4, 0].map { theme.stops[glow.stop].withAlphaComponent($0).cgColor }
        }
        CATransaction.commit()
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) is not used") }

    override func layoutSubviews() {
        super.layoutSubviews()
        guard bounds.size != laidOutSize, bounds.width > 0, bounds.height > 0 else { return }
        laidOutSize = bounds.size
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        base.frame = bounds
        let side = max(bounds.width, bounds.height) * 0.95
        for (layer, glow) in zip(glows, Self.plan) {
            layer.bounds = CGRect(x: 0, y: 0, width: side, height: side)
            layer.position = point(glow.route[0])
        }
        dust.frame = bounds
        dust.emitterPosition = CGPoint(x: bounds.midX, y: bounds.midY)
        dust.emitterSize = bounds.size
        CATransaction.commit()
        animate()
    }

    override func didMoveToWindow() {
        super.didMoveToWindow()
        // Core Animation drops animations when a view leaves its window.
        if window != nil, laidOutSize != .zero { animate() }
    }

    private func animate() {
        base.removeAllAnimations()
        glows.forEach { $0.removeAllAnimations() }
        let still = UIAccessibility.isReduceMotionEnabled
        dust.isHidden = still
        guard !still else { return }

        base.add(sweep("colors", from: restColors, to: driftColors, period: 11), forKey: "colors")
        base.add(sweep("startPoint", from: CGPoint(x: 0, y: 0), to: CGPoint(x: 0.4, y: 0), period: 14), forKey: "start")
        base.add(sweep("endPoint", from: CGPoint(x: 1, y: 1), to: CGPoint(x: 0.6, y: 1), period: 14), forKey: "end")

        for (layer, glow) in zip(glows, Self.plan) {
            let drift = CAKeyframeAnimation(keyPath: "position")
            drift.values = (glow.route + [glow.route[0]]).map { NSValue(cgPoint: point($0)) }
            drift.calculationMode = .cubicPaced
            drift.duration = glow.period
            layer.add(looping(drift), forKey: "drift")
            layer.add(sweep("transform.scale", from: 0.85, to: 1.2, period: glow.period / 3), forKey: "breathe")
        }
    }

    private func sweep(_ keyPath: String, from: Any, to: Any, period: CFTimeInterval) -> CAAnimation {
        let animation = CABasicAnimation(keyPath: keyPath)
        animation.fromValue = from
        animation.toValue = to
        animation.duration = period
        animation.autoreverses = true
        animation.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
        return looping(animation)
    }

    private func looping(_ animation: CAAnimation) -> CAAnimation {
        animation.beginTime = Self.epoch
        animation.repeatCount = .infinity
        animation.isRemovedOnCompletion = false // survive app backgrounding
        return animation
    }

    private func point(_ unit: CGPoint) -> CGPoint {
        CGPoint(x: bounds.width * unit.x, y: bounds.height * unit.y)
    }

    private var restColors: [CGColor] {
        [theme.base.mixed(with: .black, 0.65), theme.base, theme.base.mixed(with: theme.stops[2], 0.6 * theme.glow)].map(\.cgColor)
    }
    private var driftColors: [CGColor] {
        [theme.base.mixed(with: theme.stops[2], 0.25), theme.base.mixed(with: theme.stops[1], 0.3 * theme.glow),
         theme.base.mixed(with: theme.stops[0], 0.4 * theme.glow)].map(\.cgColor)
    }

    // Soft 12 pt dot, about 2 KB.
    private static let dot: CGImage? = {
        let format = UIGraphicsImageRendererFormat()
        format.scale = 2
        let size = CGSize(width: 12, height: 12)
        let image = UIGraphicsImageRenderer(size: size, format: format).image { context in
            let colors = [UIColor.white.cgColor, UIColor.white.withAlphaComponent(0).cgColor] as CFArray
            guard let gradient = CGGradient(colorsSpace: CGColorSpaceCreateDeviceRGB(), colors: colors, locations: [0.25, 1]) else { return }
            let centre = CGPoint(x: 6, y: 6)
            context.cgContext.drawRadialGradient(gradient, startCenter: centre, startRadius: 0, endCenter: centre, endRadius: 6, options: [])
        }
        return image.cgImage
    }()
}
