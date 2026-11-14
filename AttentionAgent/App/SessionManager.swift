import Foundation
import Observation

@Observable
@MainActor
final class SessionManager {
    static let shared = SessionManager()

    // UI state
    var selectedTab: AppTab = .home
    var mode: SourceMode = .feedDirect
    var isMonitoring = false
    var startedAt: Date?
    var statusMessage: String?
    var debugLine = ""
    var records: [ReelRecord] = []
    var userGoal = SharedStore.userGoal
    var activeDigest: CheckpointDigest?
    var showReset = false
    // TODO: Teammate Backend Hook: Inject APIClient conforming to AgentBackendServing here.
    private var agentBackend: any AgentBackendServing { SharedStore.useLiveBackend ? LiveAgentBackend() : MockAgentBackend() }
    private var lastPoll = Date.distantPast
    private var polling = false
    private var seenInterventions = Set<UUID>()
    var checkpointInterval = 15
    var latestReport: CheckpointReport?
    var showReport = false

    // Chat state
    var messages = [ChatMessage(isUser: false, text: "Tell me what you want from your feed.")]
    var savedPreferences: [String] = []
    var proposal: ProposedPreference?

    private let api = APIClient()
    private let notifier = CheckpointNotifier()
    private let capture = InAppCapture()
    private var tracker = ReelTracker()
    private var consumed = 0
    private var nextCheckpointAt = 15
    private var timer: Timer?
    private var booted = false

    var reelCount: Int { records.count }
    var analyzedCount: Int { records.filter { $0.status == .analyzed }.count }
    var failedCount: Int { records.filter { $0.status == .failed }.count }
    var flaggedCount: Int { records.filter { !($0.analysis?.flaggedPatterns.isEmpty ?? true) }.count }

    // MARK: Setup

    func bootstrap() {
        guard !booted else { return }
        booted = true
        let d = UserDefaults.standard
        if let raw = d.string(forKey: "mode"), let m = SourceMode(rawValue: raw) { mode = m }
        checkpointInterval = SharedStore.checkpointInterval
        consumed = d.integer(forKey: "consumed")
        loadHistory()
        resetNextCheckpoint()

        DarwinNotifier.observe {
            Task { @MainActor in SessionManager.shared.ingestBacklog() }
        }
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.tick() }
        }
        ingestBacklog()
    }

    func setMode(_ m: SourceMode) {
        guard !isMonitoring else { return }
        mode = m
        UserDefaults.standard.set(m.rawValue, forKey: "mode")
    }

    func setInterval(_ n: Int) {
        let n = max(1, min(100, n))
        checkpointInterval = n
        UserDefaults.standard.set(n, forKey: "interval")
        SharedStore.checkpointInterval = n
        resetNextCheckpoint()
    }

    private func resetNextCheckpoint() {
        nextCheckpointAt = (reelCount / checkpointInterval + 1) * checkpointInterval
    }

    // MARK: Start / stop

    func start() {
        guard Date().timeIntervalSince1970 >= SharedStore.pausedUntil else {
            statusMessage = "Monitoring is paused. Try again after the pause expires."
            return
        }
        SharedStore.sessionID = UUID().uuidString
        statusMessage = nil
        notifier.requestPermission()
        switch mode {
        case .feedDirect:
            isMonitoring = true
            startedAt = Date()
            selectedTab = .feed

        case .inAppCapture:
            selectedTab = .feed
            let tr = ReelTracker()
            tr.onObservation = { obs in Task { @MainActor in SessionManager.shared.ingest(obs) } }
            tracker = tr
            capture.onFrame = { pb, t in tr.process(pixelBuffer: pb, time: t) }
            capture.start { [weak self] error in
                Task { @MainActor in
                    if let error {
                        self?.statusMessage = "Capture failed: \(error.localizedDescription)"
                        self?.isMonitoring = false
                    } else {
                        self?.isMonitoring = true
                        self?.startedAt = Date()
                    }
                }
            }

        case .instagramBroadcast:
            if !SharedStore.isAvailable {
                statusMessage = "App Group missing. Add the App Groups capability to both targets."
                return
            }
            SharedStore.stopRequested = false
            statusMessage = "Choose AttentionBroadcast, tap Start Broadcast, then open Instagram."
        }
    }

    func stop() {
        switch mode {
        case .feedDirect:
            break
        case .inAppCapture:
            capture.stop()
            tracker.flush()
        case .instagramBroadcast:
            SharedStore.stopRequested = true
        }
        isMonitoring = false
        statusMessage = nil
    }

    func deleteHistory() {
        records.removeAll()
        latestReport = nil
        SharedStore.clear()
        consumed = 0
        UserDefaults.standard.set(0, forKey: "consumed")
        resetNextCheckpoint()
        save()
    }

    // MARK: Timer

    private func tick() {
        if isMonitoring && SharedStore.useLiveBackend && !polling && Date().timeIntervalSince(lastPoll) >= 8 {
            lastPoll = Date(); polling = true
            Task {
                defer { polling = false }
                do {
                    if let digest = try await LiveAgentBackend().pending(), seenInterventions.insert(digest.id).inserted {
                        activeDigest = digest
                        try await AgentNotification.deliver(digest)
                    }
                } catch { statusMessage = "Guardian connection unavailable: \(error.localizedDescription)" }
            }
        }
        if mode == .instagramBroadcast {
            let active = SharedStore.broadcastActive && !SharedStore.stopRequested
            if active != isMonitoring {
                isMonitoring = active
                if active { startedAt = Date(); statusMessage = nil } else { ingestBacklog() }
            }
        }
        if mode == .inAppCapture { debugLine = tracker.debug }
    }

    // MARK: Pipeline

    func ingestBacklog() {
        var result = SharedStore.read(from: consumed)
        if result.total < consumed {
            consumed = 0
            result = SharedStore.read(from: 0)
        }
        consumed = result.total
        UserDefaults.standard.set(consumed, forKey: "consumed")
        for o in result.items { ingest(o) }
    }

    func feedReelFinished(_ reel: SampleReel, startedAt: Date) {
        let obs = ReelObservation(
            id: UUID(), startedAt: startedAt, endedAt: Date(),
            ocrText: [reel.caption, reel.tag], confidence: 1, sensitiveSkipped: false)
        ingest(obs)
    }

    func ingest(_ obs: ReelObservation) {
        guard !records.contains(where: { $0.id == obs.id }) else { return }
        records.append(ReelRecord(observation: obs))
        save()
        Task { await analyze(obs) }
        if reelCount >= nextCheckpointAt {
            nextCheckpointAt += checkpointInterval
            Task { await refreshReport(notify: mode != .instagramBroadcast) }
        }
    }

    private func analyze(_ obs: ReelObservation) async {
        do {
            let result = try await api.analyze(obs)
            update(obs.id) { $0.status = .analyzed; $0.analysis = result }
        } catch {
            update(obs.id) { $0.status = .failed }
        }
    }

    private func update(_ id: UUID, _ change: (inout ReelRecord) -> Void) {
        guard let i = records.firstIndex(where: { $0.id == id }) else { return }
        change(&records[i])
        save()
    }

    private func refreshReport(notify: Bool) async {
        try? await Task.sleep(for: .seconds(2))
        let batch = Array(records.suffix(checkpointInterval))
        let report = (try? await api.checkpoint(records: batch)) ?? CheckpointReport(
            createdAt: Date(), reelCount: batch.count,
            analyzedCount: batch.filter { $0.status == .analyzed }.count,
            flaggedCount: batch.filter { !($0.analysis?.flaggedPatterns.isEmpty ?? true) }.count,
            headline: "Checkpoint reached", mismatches: [])
        latestReport = report
        if notify {
            guard let digest = SharedStore.useLiveBackend
                ? (try? await agentBackend.fetchCheckpointDigest(reelsWatched: batch.count, threshold: checkpointInterval))
                : MockAgentBackend.digest(observations: batch.map(\.observation), total: batch.count) else {
                statusMessage = "Checkpoint unavailable. Check your backend connection."
                return
            }
            activeDigest = digest
            do { try await AgentNotification.deliver(digest) }
            catch { statusMessage = error.localizedDescription }
        }
    }

    func openReport() { showReport = true }

    func handleNotificationTap(_ digest: CheckpointDigest, reset: Bool = false) {
        activeDigest = digest
        showReport = false
        selectedTab = .coach
        showReset = reset
        if !messages.contains(where: { $0.attachedDigest?.id == digest.id }) {
            messages.append(ChatMessage(isUser: false, text: digest.coachHookMessage, attachedDigest: digest))
        }
    }

    func simulateCheckpoint() {
        notifier.requestPermission()
        let now = Date()
        let samples = (0..<checkpointInterval).map { i in
            ReelObservation(id: UUID(), startedAt: now.addingTimeInterval(-Double(i * 12 + 10)), endedAt: now.addingTimeInterval(-Double(i * 12)), ocrText: i % 3 == 0 ? ["Outrage of the day", "#news"] : ["Learn something", "#science"], confidence: 1, sensitiveSkipped: false)
        }
        let digest = MockAgentBackend.digest(observations: samples, total: samples.count)
        handleNotificationTap(digest)
        Task {
            do { try await AgentNotification.deliver(digest) }
            catch { statusMessage = "Notification unavailable: \(error.localizedDescription)" }
        }
    }

    func saveGoal(_ goal: String) {
        let value = goal.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else { return }
        userGoal = value
        SharedStore.userGoal = value
        Task {
            do { try await agentBackend.updateUserPreferences(value) }
            catch { statusMessage = "Goal saved locally; sync failed." }
        }
    }

    // MARK: Persistence

    private var historyURL: URL {
        let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.appendingPathComponent("history.json")
    }

    private func save() {
        if let data = try? JSONEncoder().encode(records) { try? data.write(to: historyURL) }
    }

    private func loadHistory() {
        guard let data = try? Data(contentsOf: historyURL),
              var loaded = try? JSONDecoder().decode([ReelRecord].self, from: data) else { return }
        for i in loaded.indices where loaded[i].status == .pending { loaded[i].status = .failed }
        records = loaded
    }

    // MARK: Chat

    func send(_ text: String) {
        messages.append(ChatMessage(isUser: true, text: text))
        let context = activeDigest
        Task {
            do {
                let reply = try await agentBackend.sendMessageToCoach(text, context: context)
                messages.append(reply)
                if let action = reply.clientAction { execute(action) }
            }
            catch { messages.append(ChatMessage(isUser: false, text: "Coach unavailable. Please try again.")) }
        }
    }

    func execute(_ action: GuardianClientAction) {
        let confirmation: String
        switch action.type {
        case .updateThreshold:
            guard let value = action.threshold, (1...100).contains(value) else { return }
            setInterval(value); confirmation = "Alerts updated to every \(value) reels."
        case .updateSensitivity:
            guard let value = action.sensitivity, ["low", "medium", "high"].contains(value) else { return }
            SharedStore.sensitivity = value; confirmation = "Sensitivity updated to \(value)."
        case .pauseMonitoring:
            guard let minutes = action.minutes, (1...1440).contains(minutes) else { return }
            SharedStore.pausedUntil = Date().addingTimeInterval(Double(minutes * 60)).timeIntervalSince1970
            stop(); confirmation = "Monitoring paused for \(minutes) minutes. Restart manually when ready."
        }
        messages.append(ChatMessage(isUser: false, text: confirmation))
    }
    func correct(_ event: FlaggedReelEvent, correction: String) {
        Task {
            do {
                if SharedStore.useLiveBackend { try await LiveAgentBackend().feedback(event: event.id, correction: correction) }
                messages.append(ChatMessage(isUser: false, text: "Correction saved: \(correction)."))
            } catch { messages.append(ChatMessage(isUser: false, text: "Correction could not be sent. Please retry.")) }
        }
    }

    func resolveProposal(accept: Bool) {
        if accept, let p = proposal { savedPreferences.append(p.text) }
        proposal = nil
    }
}
