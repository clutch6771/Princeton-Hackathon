import Foundation

struct ReelObservation: Identifiable, Codable, Hashable {
    let id: UUID
    let startedAt: Date
    var endedAt: Date
    var ocrText: [String]
    var confidence: Double
    var sensitiveSkipped: Bool

    var duration: TimeInterval { endedAt.timeIntervalSince(startedAt) }
}
