import Foundation

enum AnalysisStatus: String, Codable { case pending, analyzed, failed }

struct AnalysisResult: Codable, Hashable {
    let reelID: UUID
    var topics: [String]
    var flaggedPatterns: [String]
    var summary: String
}

struct ReelRecord: Identifiable, Codable, Hashable {
    var id: UUID { observation.id }
    let observation: ReelObservation
    var status: AnalysisStatus = .pending
    var analysis: AnalysisResult?
}

struct CheckpointReport: Codable {
    let createdAt: Date
    let reelCount: Int
    let analyzedCount: Int
    let flaggedCount: Int
    let headline: String
    let mismatches: [String]
}

struct ProposedPreference: Identifiable, Hashable {
    let id = UUID()
    let text: String
}

struct ChatReply: Codable {
    let text: String
    let proposal: String?
}

enum SourceMode: String, CaseIterable, Identifiable {
    case feedDirect = "Demo feed, simulated events"
    case inAppCapture = "Demo feed, real capture + detector"
    case instagramBroadcast = "Instagram, screen broadcast"
    var id: String { rawValue }
}

enum AppTab: Hashable { case home, feed, session, coach, settings }

struct SampleReel: Identifiable, Hashable {
    let id: Int
    let caption: String
    let tag: String
    let hue: Double

    private static let pool: [(String, String)] = [
        ("Why this stock dropped today", "#investing"),
        ("You will not believe what they said", "#politics"),
        ("5 minute garlic pasta", "#recipe"),
        ("Linear algebra in 60 seconds", "#math"),
        ("Outrage of the day", "#news"),
        ("Gym form check: squats", "#fitness"),
        ("How transformers read text", "#ai"),
        ("They are lying to you", "#politics"),
        ("Cheap meal prep for the week", "#food"),
        ("Why entropy always grows", "#science"),
        ("Rank the worst takes ever", "#drama"),
        ("Python tip you need to know", "#coding")
    ]

    static func make(_ i: Int) -> SampleReel {
        let c = pool[i % pool.count]
        return SampleReel(id: i, caption: c.0, tag: c.1, hue: Double((i * 37) % 100) / 100)
    }
}

struct SessionInsight: Identifiable, Codable {
    var id = UUID()
    var startedAt: Date
    var endedAt: Date?
    var total = 0
    var flagged = 0
}
