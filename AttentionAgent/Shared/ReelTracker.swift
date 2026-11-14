import Foundation
import CoreVideo
import UIKit

/// Frames in, finished ReelObservations out. Runs in the app (demo capture) and
/// inside the broadcast extension (Instagram). Call from one queue only.
final class ReelTracker {
    var frameBase64: String?
    var onObservation: ((ReelObservation) -> Void)?

    private let detector = ReelDetector()
    private let extractor = ContentExtractor()

    private var anchorT: TimeInterval?
    private var anchorDate = Date()
    private var lastSample: TimeInterval = -1

    private var startWall: Date?
    private var ocrAtT: TimeInterval = 0
    private var ocrDone = false
    private var text: [String] = []
    private var sensitive = false
    private var startConfidence = 0.6

    var debug: String {
        String(format: "diff %.3f  cuts %d  %@", detector.lastDiff, detector.sceneCuts, detector.stateName)
    }

    init() {
        detector.onNewReel = { [weak self] e in self?.handle(e) }
    }

    func process(pixelBuffer pb: CVPixelBuffer, time t: TimeInterval) {
        if anchorT == nil {
            anchorT = t
            anchorDate = Date()
            startWall = anchorDate
            ocrAtT = t + 0.35
        }
        guard t - lastSample >= 1.0 / 15.0 else { return }
        lastSample = t
        guard let luma = FrameSampler.luma(from: pb) else { return }

        detector.process(luma, at: t)

        if !ocrDone, t >= ocrAtT {
            ocrDone = true
            if let img = FrameSampler.cgImage(from: pb, maxWidth: 512) {
                let r = extractor.extract(from: img)
                text = r.lines
                sensitive = r.sensitive
                frameBase64 = r.sensitive ? nil : UIImage(cgImage: img).jpegData(compressionQuality: 0.5)?.base64EncodedString()
            }
        }
    }

    /// Closes the Reel in progress (monitoring stopped).
    func flush() {
        guard let s = startWall else { return }
        let end = Date()
        if end.timeIntervalSince(s) >= 0.4 { emit(from: s, to: end, confidence: startConfidence) }
        startWall = nil
        anchorT = nil
        detector.reset()
    }

    private func wall(_ t: TimeInterval) -> Date {
        anchorDate.addingTimeInterval(t - (anchorT ?? t))
    }

    private func handle(_ e: ReelDetector.Event) {
        if let s = startWall {
            emit(from: s, to: wall(e.swipeStart), confidence: min(startConfidence, e.confidence))
        }
        startWall = wall(e.swipeStart)
        startConfidence = e.confidence
        ocrAtT = e.settledAt + 0.35
        ocrDone = false
        frameBase64 = nil
        text = []
        sensitive = false
    }

    private func emit(from start: Date, to end: Date, confidence: Double) {
        let obs = ReelObservation(
            id: UUID(), startedAt: start, endedAt: max(end, start),
            ocrText: sensitive ? [] : text,
            confidence: confidence, sensitiveSkipped: sensitive)
        onObservation?(obs)
    }
}
