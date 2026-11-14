import Foundation
import Vision
import CoreGraphics

/// On-device OCR. Frames never leave the phone; only the recognized text does.
final class ContentExtractor {
    struct Result {
        var lines: [String]
        var sensitive: Bool
    }

    private static let uiNoise: Set<String> = [
        "follow", "following", "like", "share", "reels", "audio", "original audio",
        "more", "send", "comment", "suggested for you", "sponsored"
    ]
    private static let sensitiveWords = [
        "password", "passcode", "cvv", "security code", "social security",
        "routing number", "one-time code", "verification code"
    ]

    func extract(from image: CGImage) -> Result {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .fast
        request.usesLanguageCorrection = false
        request.minimumTextHeight = 0.015

        let handler = VNImageRequestHandler(cgImage: image, options: [:])
        try? handler.perform([request])

        var items: [(String, CGFloat)] = []
        for obs in request.results ?? [] {
            guard let cand = obs.topCandidates(1).first, cand.confidence > 0.4 else { continue }
            let s = cand.string.trimmingCharacters(in: .whitespacesAndNewlines)
            guard s.count >= 3, s.count <= 200,
                  !Self.uiNoise.contains(s.lowercased()),
                  s.contains(where: { $0.isLetter }) else { continue }
            items.append((s, obs.boundingBox.height))
        }

        let joined = items.map { $0.0.lowercased() }.joined(separator: " ")
        if Self.sensitiveWords.contains(where: { joined.contains($0) })
            || joined.range(of: #"\b(?:\d[ -]?){13,19}\b"#, options: .regularExpression) != nil {
            return Result(lines: [], sensitive: true)
        }

        let top = items.sorted { $0.1 > $1.1 }.prefix(12).map { $0.0 }
        return Result(lines: Array(top), sensitive: false)
    }
}
