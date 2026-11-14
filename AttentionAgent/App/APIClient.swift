import Foundation

/// Talks to the FastAPI backend. While the "Offline demo backend" switch in
/// Settings is on, it returns canned answers so the whole app works without a server.
final class APIClient {
    private var offline: Bool { true }
    private var baseURL: URL? { URL(string: UserDefaults.standard.string(forKey: "backendURL") ?? "") }

    private let encoder: JSONEncoder = {
        let e = JSONEncoder(); e.dateEncodingStrategy = .iso8601; return e
    }()
    private let decoder: JSONDecoder = {
        let d = JSONDecoder(); d.dateDecodingStrategy = .iso8601; return d
    }()

    func analyze(_ obs: ReelObservation) async throws -> AnalysisResult {
        if offline {
            try await Task.sleep(for: .milliseconds(Int.random(in: 300...1200)))
            let text = obs.ocrText.joined(separator: " ").lowercased()
            let hot = ["outrage", "lying", "worst takes", "believe what"].contains { text.contains($0) }
            return AnalysisResult(
                reelID: obs.id,
                topics: obs.ocrText.filter { $0.hasPrefix("#") },
                flaggedPatterns: hot ? ["outrage framing"] : [],
                summary: obs.ocrText.first ?? "No text read")
        }
        let data = try await post("reels/analyze", body: try encoder.encode(obs))
        return try decoder.decode(AnalysisResult.self, from: data)
    }

    func checkpoint(records: [ReelRecord]) async throws -> CheckpointReport {
        let flagged = records.filter { !($0.analysis?.flaggedPatterns.isEmpty ?? true) }.count
        let analyzed = records.filter { $0.status == .analyzed }.count
        if offline {
            return CheckpointReport(
                createdAt: Date(), reelCount: records.count, analyzedCount: analyzed,
                flaggedCount: flagged,
                headline: "\(flagged) of \(records.count) Reels were flagged",
                mismatches: flagged > 0
                    ? ["Outrage-style content showed up while your saved goals ask for educational content."]
                    : [])
        }
        let data = try await post("reels/checkpoint", body: try encoder.encode(records))
        return try decoder.decode(CheckpointReport.self, from: data)
    }

    func chat(_ message: String) async throws -> ChatReply {
        if offline {
            try await Task.sleep(for: .milliseconds(600))
            return ChatReply(text: "Noted. Should I save that as a preference?", proposal: message)
        }
        let payload = try encoder.encode(["message": message])
        let data = try await post("chat", body: payload)
        return try decoder.decode(ChatReply.self, from: data)
    }

    private func post(_ path: String, body: Data) async throws -> Data {
        guard let base = baseURL else { throw URLError(.badURL) }
        var req = URLRequest(url: base.appending(path: path))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = body
        req.timeoutInterval = 20
        let (data, resp) = try await URLSession.shared.data(for: req)
        guard (resp as? HTTPURLResponse)?.statusCode == 200 else { throw URLError(.badServerResponse) }
        return data
    }
}
