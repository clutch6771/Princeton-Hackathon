import Foundation

struct UserProfileContext: Codable {
    var statedGoals = SharedStore.userGoal
    var sensitivity = SharedStore.sensitivity
    var checkpointThreshold = SharedStore.checkpointInterval
    var sessionId = SharedStore.sessionID
}

struct ReelIngestPayload: Codable {
    var sessionId: String
    var reelIndex: Int
    var dwellSeconds: Double
    var ocrText: [String]
    var transcriptText: String?
    var frameBase64: String?
    var sensitivity: String
    var statedGoals: String
    var startedAt: Date? = nil
}

struct GuardianClientAction: Codable, Hashable {
    enum Kind: String, Codable { case updateThreshold = "update_threshold", updateSensitivity = "update_sensitivity", pauseMonitoring = "pause_monitoring" }
    var type: Kind
    var threshold: Int?
    var sensitivity: String?
    var minutes: Int?
}

struct LiveAgentBackend: AgentBackendServing {
    struct TimelineEnvelope: Decodable { var data: TimelinePage }
    struct TimelinePage: Decodable { var items: [JudgedReel] }
    struct JudgedReel: Decodable {
        var id: UUID; var start: Date; var end: Date
        var transcript: String; var description: String
        var verdict: String?; var reason: String
    }

    struct ChatRequest: Encodable { var message: String; var profile: UserProfileContext; var context: CheckpointDigest?
        var conversationMemory: [ChatMessage] = {
            guard let data = UserDefaults(suiteName: AppConfig.appGroupID)?.data(forKey: "guardianMemory") else { return [] }
            return (try? JSONDecoder().decode([ChatMessage].self, from: data)) ?? []
        }()
    }
    func request(_ path: String, body: (some Encodable)?, method: String = "POST") async throws -> Data {
        guard let base = URL(string: SharedStore.backendBaseURL), ["http", "https"].contains(base.scheme ?? ""), base.host != nil else { throw URLError(.badURL) }
        var request = URLRequest(url: URL(string: path, relativeTo: base.appendingPathComponent("/"))!)
        request.httpMethod = method
        request.timeoutInterval = 30
        request.setValue(SharedStore.demoUserID, forHTTPHeaderField: "X-Demo-User-Id")
        if let body {
            let encoder = JSONEncoder(); encoder.keyEncodingStrategy = .convertToSnakeCase; encoder.dateEncodingStrategy = .iso8601
            request.httpBody = try encoder.encode(body)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let response = response as? HTTPURLResponse, (200 ..< 300).contains(response.statusCode) else { throw URLError(.badServerResponse) }
        return data
    }

    func decode<T: Decodable>(_ type: T.Type, _ data: Data) throws -> T {
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        decoder.dateDecodingStrategy = .custom { decoder in
            let value = try decoder.singleValueContainer().decode(String.self)
            let fractional = ISO8601DateFormatter()
            fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            if let date = fractional.date(from: value) ?? ISO8601DateFormatter().date(from: value) {
                return date
            }
            throw DecodingError.dataCorrupted(.init(codingPath: decoder.codingPath, debugDescription: "Invalid server date"))
        }
        return try decoder.decode(type, from: data)
    }

    func health() async throws {
        _ = try await request("health", body: String?.none, method: "GET")
    }

    func ingest(_ payload: ReelIngestPayload) async throws {
        _ = try await request("agent1/ingest", body: payload)
    }

    func endSession(_ sessionId: String) async throws {
        _ = try await request("session/end", body: ["session_id": sessionId])
    }

    func fetchCheckpointDigest(reelsWatched _: Int, threshold: Int) async throws -> CheckpointDigest {
        let path = "timeline?session_id=\(SharedStore.sessionID)&limit=\(min(100, max(1, threshold)))"
        let page = try await decode(TimelineEnvelope.self, request(path, body: String?.none, method: "GET"))
        let judged = page.data.items.filter { $0.verdict != nil }
        let conflicts = judged.filter { $0.verdict == "conflict" }
        let flags = conflicts.map { reel in
            FlaggedReelEvent(id: reel.id, timestamp: reel.end, topic: reel.description,
                             emotionTag: "value conflict", transcriptText: reel.transcript,
                             dwellSeconds: max(0, Int(reel.end.timeIntervalSince(reel.start))))
        }
        return CheckpointDigest(totalReelsWatched: judged.count, flaggedReels: flags,
                                userGoalConflict: conflicts.first?.reason,
                                coachHookMessage: "\(conflicts.count) of your last \(judged.count) judged reels conflicted with your approved values.",
                                suggestedAction: "Review the reasons and tell your coach whether you agree.")
    }

    func sendMessageToCoach(_ text: String, context: CheckpointDigest?) async throws -> ChatMessage {
        try await decode(ChatMessage.self, request("agent2/chat", body: ChatRequest(message: text, profile: UserProfileContext(), context: context)))
    }

    /// Preferences travel with every request; no separate server route is required.
    func updateUserPreferences(_ preferences: String) async throws {
        SharedStore.userGoal = preferences
    }

    func feedback(event: UUID, correction: String) async throws {
        _ = try await request("feedback", body: ["reel_id": event.uuidString, "label": "disagree", "comment": correction, "source": "app"])
    }

    func pending() async throws -> CheckpointDigest? {
        // Checkpoint delivery belongs to the app; the Judge has no notification outbox.
        nil
    }
}
