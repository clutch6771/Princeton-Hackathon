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
        let saved = d.integer(forKey: "interval")
        checkpointInterval = saved > 0 ? saved : SharedStore.checkpointInterval
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
        if notify { notifier.deliver(report) }
    }

    func openReport() { showReport = true }

    func handleNotificationTap() {
        ingestBacklog()
        Task {
            await refreshReport(notify: false)
            showReport = true
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
        Task {
            let reply = (try? await api.chat(text)) ?? ChatReply(text: "Backend not reachable.", proposal: nil)
            messages.append(ChatMessage(isUser: false, text: reply.text))
            if let p = reply.proposal { proposal = ProposedPreference(text: p) }
        }
    }

    func resolveProposal(accept: Bool) {
        if accept, let p = proposal { savedPreferences.append(p.text) }
        proposal = nil
    }
}
