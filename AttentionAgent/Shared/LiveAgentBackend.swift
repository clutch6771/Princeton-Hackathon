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
}
struct GuardianClientAction: Codable, Hashable {
    enum Kind: String, Codable { case updateThreshold = "update_threshold", updateSensitivity = "update_sensitivity", pauseMonitoring = "pause_monitoring" }
    var type: Kind
    var threshold: Int?
    var sensitivity: String?
    var minutes: Int?
}
struct LiveAgentBackend: AgentBackendServing {
    struct CheckpointRequest: Encodable { var reelsWatched: Int; var threshold: Int; var profile: UserProfileContext }
    struct ChatRequest: Encodable { var message: String; var profile: UserProfileContext; var context: CheckpointDigest? }
    struct Feedback: Encodable { var eventId: UUID; var correction: String; var profile: UserProfileContext }
    struct Pending: Decodable { var digest: CheckpointDigest? }
    func request<T: Encodable>(_ path: String, body: T?, method: String = "POST") async throws -> Data {
        guard let base = URL(string: SharedStore.backendBaseURL), ["http", "https"].contains(base.scheme ?? ""), base.host != nil else { throw URLError(.badURL) }
        var request = URLRequest(url: URL(string: path, relativeTo: base.appendingPathComponent("/"))!)
        request.httpMethod = method
        request.timeoutInterval = 12
        if let body {
            let encoder = JSONEncoder(); encoder.keyEncodingStrategy = .convertToSnakeCase; encoder.dateEncodingStrategy = .iso8601
            request.httpBody = try encoder.encode(body)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let response = response as? HTTPURLResponse, (200..<300).contains(response.statusCode) else { throw URLError(.badServerResponse) }
        return data
    }
    func decode<T: Decodable>(_ type: T.Type, _ data: Data) throws -> T {
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase; decoder.dateDecodingStrategy = .iso8601
        return try decoder.decode(type, from: data)
    }
    func health() async throws { _ = try await request("health", body: Optional<String>.none, method: "GET") }
    func ingest(_ payload: ReelIngestPayload) async throws { _ = try await request("agent1/ingest", body: payload) }
    func fetchCheckpointDigest(reelsWatched: Int, threshold: Int) async throws -> CheckpointDigest {
        try decode(CheckpointDigest.self, await request("agent2/checkpoint", body: CheckpointRequest(reelsWatched: reelsWatched, threshold: threshold, profile: UserProfileContext())))
    }
    func sendMessageToCoach(_ text: String, context: CheckpointDigest?) async throws -> ChatMessage {
        try decode(ChatMessage.self, await request("agent2/chat", body: ChatRequest(message: text, profile: UserProfileContext(), context: context)))
    }
    // Preferences travel with every request; no separate server route is required.
    func updateUserPreferences(_ preferences: String) async throws { SharedStore.userGoal = preferences }
    func feedback(event: UUID, correction: String) async throws {
        _ = try await request("agent2/feedback", body: Feedback(eventId: event, correction: correction, profile: UserProfileContext()))
    }
    func pending() async throws -> CheckpointDigest? {
        try decode(Pending.self, await request("agent2/pending-intervention?session_id=\(SharedStore.sessionID)", body: Optional<String>.none, method: "GET")).digest
    }
}
