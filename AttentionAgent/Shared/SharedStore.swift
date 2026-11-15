import Foundation

/// File and flag exchange between the app and the broadcast extension through the App Group.
enum SharedStore {
    private static var defaults: UserDefaults? { UserDefaults(suiteName: AppConfig.appGroupID) }

    private static var fileURL: URL? {
        FileManager.default
            .containerURL(forSecurityApplicationGroupIdentifier: AppConfig.appGroupID)?
            .appendingPathComponent("observations.jsonl")
    }

    private static let encoder: JSONEncoder = {
        let e = JSONEncoder(); e.dateEncodingStrategy = .iso8601; return e
    }()
    private static let decoder: JSONDecoder = {
        let d = JSONDecoder(); d.dateDecodingStrategy = .iso8601; return d
    }()

    static var isAvailable: Bool { fileURL != nil }

    // MARK: Observations (one JSON object per line)

    static func append(_ obs: ReelObservation) {
        guard let url = fileURL, var data = try? encoder.encode(obs) else { return }
        data.append(0x0A)
        if !FileManager.default.fileExists(atPath: url.path) {
            FileManager.default.createFile(atPath: url.path, contents: nil)
        }
        guard let handle = try? FileHandle(forWritingTo: url) else { return }
        defer { try? handle.close() }
        _ = try? handle.seekToEnd()
        try? handle.write(contentsOf: data)
    }

    static func read(from index: Int) -> (items: [ReelObservation], total: Int) {
        guard let url = fileURL,
              let text = try? String(contentsOf: url, encoding: .utf8) else { return ([], 0) }
        // A reader may race the extension's append. Consume only complete lines.
        let lines = text.split(separator: "\n", omittingEmptySubsequences: false).dropLast()
        let items = lines.dropFirst(index).compactMap {
            try? decoder.decode(ReelObservation.self, from: Data($0.utf8))
        }
        return (items, lines.count)
    }

    static func clear() {
        if let url = fileURL { try? FileManager.default.removeItem(at: url) }
    }

    // MARK: Flags

    static var checkpointInterval: Int {
        get { let v = defaults?.integer(forKey: "interval") ?? 0; return v > 0 ? v : 15 }
        set { defaults?.set(newValue, forKey: "interval") }
    }

    static var backendBaseURL: String {
        get { defaults?.string(forKey: "backendBaseURL") ?? "http://127.0.0.1:8000" }
        set { defaults?.set(newValue, forKey: "backendBaseURL") }
    }
    static var useLiveBackend: Bool {
        get { defaults?.bool(forKey: "useLiveBackend") ?? false }
        set { defaults?.set(newValue, forKey: "useLiveBackend") }
    }
    static var demoUserID: String {
        get { defaults?.string(forKey: "demoUserID") ?? "demo-user" }
        set { defaults?.set(newValue, forKey: "demoUserID") }
    }
    static var sensitivity: String {
        get { defaults?.string(forKey: "sensitivity") ?? "medium" }
        set { defaults?.set(newValue, forKey: "sensitivity") }
    }
    static var broadcastReelCount: Int {
        get { defaults?.integer(forKey: "broadcastReelCount") ?? 0 }
        set { defaults?.set(newValue, forKey: "broadcastReelCount") }
    }
    static var broadcastFlaggedEvents: [FlaggedReelEvent] {
        get { defaults?.data(forKey: "broadcastFlaggedEvents").flatMap { try? JSONDecoder().decode([FlaggedReelEvent].self, from: $0) } ?? [] }
        set { defaults?.set(try? JSONEncoder().encode(newValue), forKey: "broadcastFlaggedEvents") }
    }
    static var lastNotificationScheduled: Date? {
        get { defaults?.object(forKey: "lastNotificationScheduled") as? Date }
        set { defaults?.set(newValue, forKey: "lastNotificationScheduled") }
    }
    static var broadcastStartedAt: Date? {
        get { defaults?.object(forKey: "broadcastStartedAt") as? Date }
        set { defaults?.set(newValue, forKey: "broadcastStartedAt") }
    }
    static var latestDigest: CheckpointDigest? {
        get { defaults?.data(forKey: "latestDigest").flatMap { try? JSONDecoder().decode(CheckpointDigest.self, from: $0) } }
        set { defaults?.set(try? JSONEncoder().encode(newValue), forKey: "latestDigest") }
    }
    static var notificationError: String? {
        get { defaults?.string(forKey: "notificationError") }
        set { defaults?.set(newValue, forKey: "notificationError") }
    }
    static var sessionID: String {
        get { defaults?.string(forKey: "sessionID") ?? "demo" }
        set { defaults?.set(newValue, forKey: "sessionID") }
    }
    static var pausedUntil: Double {
        get { defaults?.double(forKey: "pausedUntil") ?? 0 }
        set { defaults?.set(newValue, forKey: "pausedUntil") }
    }
    static var userGoal: String {
        get { defaults?.string(forKey: "userGoal") ?? "Low-Stress Feed" }
        set { defaults?.set(newValue, forKey: "userGoal") }
    }

    static var stopRequested: Bool {
        get { defaults?.bool(forKey: "stopRequested") ?? false }
        set { defaults?.set(newValue, forKey: "stopRequested") }
    }

    static func heartbeat() { defaults?.set(Date().timeIntervalSince1970, forKey: "beat") }
    static func clearHeartbeat() { defaults?.set(0.0, forKey: "beat") }

    static var broadcastActive: Bool {
        guard let t = defaults?.double(forKey: "beat"), t > 0 else { return false }
        return Date().timeIntervalSince1970 - t < 6
    }
}

/// Wakes the app when the extension writes a new observation.
enum DarwinNotifier {
    private static let name = "com.attentionagent.newobservation" as CFString
    private static var handler: (() -> Void)?

    static func post() {
        CFNotificationCenterPostNotification(
            CFNotificationCenterGetDarwinNotifyCenter(),
            CFNotificationName(name), nil, nil, true)
    }

    static func observe(_ block: @escaping () -> Void) {
        handler = block
        CFNotificationCenterAddObserver(
            CFNotificationCenterGetDarwinNotifyCenter(), nil,
            { _, _, _, _, _ in DarwinNotifier.handler?() },
            name, nil, .deliverImmediately)
    }
}
