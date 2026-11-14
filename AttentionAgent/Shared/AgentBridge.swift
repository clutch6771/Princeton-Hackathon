import Foundation
import UserNotifications

struct FlaggedReelEvent: Identifiable, Codable, Hashable {
    var id: UUID
    var timestamp: Date
    var topic: String
    var emotionTag: String
    var transcriptText: String?
    var dwellSeconds: Int
}
struct CheckpointDigest: Identifiable, Codable, Hashable {
    var id = UUID()
    var totalReelsWatched: Int
    var flaggedReels: [FlaggedReelEvent]
    var userGoalConflict: String?
    var coachHookMessage: String
    var suggestedAction: String
}
struct ChatMessage: Identifiable, Codable, Hashable {
    enum Sender: String, Codable { case user, coachAgent }
    var id = UUID()
    var sender: Sender
    var text: String
    var timestamp = Date()
    var attachedDigest: CheckpointDigest?
    var clientAction: GuardianClientAction?
    var isUser: Bool { sender == .user }
    init(isUser: Bool, text: String, attachedDigest: CheckpointDigest? = nil) {
        sender = isUser ? .user : .coachAgent
        self.text = text
        self.attachedDigest = attachedDigest
    }
}
protocol AgentBackendServing {
    func fetchCheckpointDigest(reelsWatched: Int, threshold: Int) async throws -> CheckpointDigest
    func sendMessageToCoach(_ text: String, context: CheckpointDigest?) async throws -> ChatMessage
    func updateUserPreferences(_ preferences: String) async throws
}
struct MockAgentBackend: AgentBackendServing {
    // TODO: Teammate Backend Hook: Replace with APIClient checkpoint endpoint and Watcher memory.
    func fetchCheckpointDigest(reelsWatched: Int, threshold: Int) async throws -> CheckpointDigest {
        let observations = Array(SharedStore.read(from: 0).items.suffix(max(1, threshold)))
        return Self.digest(observations: observations, total: reelsWatched)
    }
    static func digest(observations: [ReelObservation], total: Int) -> CheckpointDigest {
        let flags = observations.filter {
            let text = $0.ocrText.joined(separator: " ").lowercased()
            return !$0.sensitiveSkipped && ["outrage", "lying", "worst takes", "hate", "believe what"].contains(where: text.contains)
        }.map { FlaggedReelEvent(id: $0.id, timestamp: $0.endedAt, topic: $0.ocrText.last ?? "Debate", emotionTag: "anger", dwellSeconds: max(0, Int($0.duration))) }
        return CheckpointDigest(totalReelsWatched: max(total, flags.count), flaggedReels: flags,
            userGoalConflict: flags.isEmpty ? nil : "Possible conflict with your ‘\(SharedStore.userGoal)’ goal",
            coachHookMessage: flags.isEmpty ? "Your recent feed looks calm. Tap to reflect on what you want to watch next." : "I noticed \(flags.count) potentially divisive reels. Does that fit the feed you want? Tap to talk with your coach.",
            suggestedAction: "Take a breath, mark an unwanted reel ‘Not interested’, then search for one topic that supports your goal.")
    }
    // TODO: Teammate Backend Hook: Send text and attached digest to APIClient coach endpoint.
    func sendMessageToCoach(_ text: String, context: CheckpointDigest?) async throws -> ChatMessage {
        let reply: String
        if text.lowercased().contains("reset") { reply = context?.suggestedAction ?? "Pause for 30 seconds and choose one topic you want to learn about next." }
        else if text.lowercased().contains("goal") { reply = "What would you like more of, and what would you like less of? Use Update my feed goals to save your intention." }
        else { reply = "You said: ‘\(text)’. With your goal of ‘\(SharedStore.userGoal)’ in mind, how did the last few reels leave you feeling?" }
        return ChatMessage(isUser: false, text: reply, attachedDigest: context)
    }
    // TODO: Teammate Backend Hook: Persist preferences through APIClient; local App Group remains the offline cache.
    func updateUserPreferences(_ preferences: String) async throws { SharedStore.userGoal = preferences }
}

enum AgentNotification {
    static let category = "agent.checkpoint"
    static let reset = "agent.reset"
    static let keep = "agent.keep"
    static func register() {
        UNUserNotificationCenter.current().setNotificationCategories([UNNotificationCategory(identifier: category, actions: [
            UNNotificationAction(identifier: "agent.talk", title: "Talk to Coach", options: .foreground),
            UNNotificationAction(identifier: reset, title: "Quick Feed Reset", options: .foreground),
            UNNotificationAction(identifier: keep, title: "Keep Scrolling", options: [])
        ], intentIdentifiers: [], options: [])])
    }
    static func deliver(_ digest: CheckpointDigest) async throws {
        let c = UNMutableNotificationContent()
        c.title = "Agent Check-In • \(digest.flaggedReels.count)/\(digest.totalReelsWatched) Flagged"
        c.subtitle = digest.userGoalConflict ?? "Checking in with your feed goals"
        c.body = digest.coachHookMessage
        c.categoryIdentifier = category
        c.sound = .default
        c.userInfo = ["digest": try JSONEncoder().encode(digest)]
        try await UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: digest.id.uuidString, content: c, trigger: nil))
    }
}
