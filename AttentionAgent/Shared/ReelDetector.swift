import Foundation

/// Decides when the user swiped to a different Reel.
///
/// A swipe shows up as a large frame change in which the new frame matches the
/// previous frame shifted vertically. A scene cut inside a video changes the
/// frame just as much but has no vertical match, so it is not counted.
/// A new Reel is committed once vertical motion has stopped for `settleQuiet`
/// seconds, the content moved at least `minNetShift` rows (of 64), and the
/// settled frame differs from the one before the swipe.
final class ReelDetector {
    struct Event {
        let swipeStart: TimeInterval
        let settledAt: TimeInterval
        let confidence: Double
    }

    var onNewReel: ((Event) -> Void)?

    // Tuning knobs
    var startMotion = 0.03
    var startMatchRatio = 0.65
    var minNetShift = 14
    var settleQuiet = 0.12
    var minChangeAfterSwipe = 0.04
    var revisitThreshold = 0.02
    var minReelGap = 0.25

    private(set) var sceneCuts = 0
    private(set) var lastDiff = 0.0
    private(set) var stateName = "stable"

    private enum State { case stable, swiping }
    private var state = State.stable
    private var prev: LumaFrame?
    private var prevT: TimeInterval = 0
    private var preSwipe: LumaFrame?
    private var swipeStart: TimeInterval = 0
    private var lastVertical: TimeInterval = 0
    private var net = 0
    private var vertFrames = 0
    private var fingerprints: [LumaFrame] = []
    private var lastCommit: TimeInterval = -100

    private static let maxShift = 44

    func process(_ f: LumaFrame, at t: TimeInterval) {
        defer { prev = f; prevT = t }
        guard let p = prev else {
            fingerprints = [f]
            return
        }
        let d0 = Self.mad(p, f)
        lastDiff = d0

        switch state {
        case .stable:
            guard d0 > startMotion else { return }
            let best = Self.bestShift(prev: p, cur: f)
            if best.score < startMatchRatio * d0 {
                state = .swiping
                stateName = "swiping"
                preSwipe = p
                swipeStart = prevT
                lastVertical = t
                net = best.shift
                vertFrames = 1
            } else if d0 > 0.2 {
                sceneCuts += 1
            }

        case .swiping:
            if d0 > 0.012 {
                let best = Self.bestShift(prev: p, cur: f)
                if best.score < 0.8 * d0 {
                    net += best.shift
                    vertFrames += 1
                    lastVertical = t
                }
            }
            if t - lastVertical >= settleQuiet {
                settle(f, at: t)
            } else if t - swipeStart > 3 {
                endSwipe()
            }
        }
    }

    func reset() {
        prev = nil
        fingerprints = []
        lastCommit = -100
        sceneCuts = 0
        endSwipe()
    }

    private func endSwipe() {
        state = .stable
        stateName = "stable"
        preSwipe = nil
    }

    private func settle(_ f: LumaFrame, at t: TimeInterval) {
        defer { endSwipe() }
        guard abs(net) >= minNetShift, let pre = preSwipe else { return }
        let change = Self.mad(pre, f)
        guard change > minChangeAfterSwipe else { return }          // snapped back
        guard t - lastCommit >= minReelGap else { return }
        if fingerprints.contains(where: { Self.mad($0, f) < revisitThreshold }) { return }  // revisit

        fingerprints.append(f)
        if fingerprints.count > 5 { fingerprints.removeFirst() }
        lastCommit = t

        let motion = min(1, Double(vertFrames) / 3)
        let novelty = min(1, change / 0.15)
        let confidence = 0.5 + 0.25 * motion + 0.25 * novelty
        onNewReel?(Event(swipeStart: swipeStart, settledAt: t - settleQuiet, confidence: confidence))
    }

    // MARK: Frame math

    static func mad(_ a: LumaFrame, _ b: LumaFrame) -> Double {
        var s = 0
        for i in 0..<a.px.count { s += abs(Int(a.px[i]) - Int(b.px[i])) }
        return Double(s) / Double(a.px.count) / 255.0
    }

    /// Mean difference between cur[y] and prev[y + s] over the overlapping rows.
    private static func shiftedMad(prev: LumaFrame, cur: LumaFrame, shift s: Int) -> Double? {
        let w = LumaFrame.width, h = LumaFrame.height
        let y0 = max(0, -s), y1 = min(h, h - s)
        guard y1 - y0 >= 16 else { return nil }
        var sum = 0
        for y in y0..<y1 {
            let rp = (y + s) * w, rc = y * w
            for x in 0..<w { sum += abs(Int(cur.px[rc + x]) - Int(prev.px[rp + x])) }
        }
        return Double(sum) / Double((y1 - y0) * w) / 255.0
    }

    private static func bestShift(prev: LumaFrame, cur: LumaFrame) -> (score: Double, shift: Int) {
        var best = (score: Double.greatestFiniteMagnitude, shift: 0)
        for s in -maxShift...maxShift where abs(s) >= 3 {
            if let d = shiftedMad(prev: prev, cur: cur, shift: s), d < best.score {
                best = (d, s)
            }
        }
        return best
    }
}
