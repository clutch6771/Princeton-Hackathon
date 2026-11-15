import ReplayKit
import UserNotifications

/// Broadcast Upload Extension. Receives the screen while Instagram is open,
/// detects Reels on device, and writes observations to the App Group.
class SampleHandler: RPBroadcastSampleHandler {
    private let lifecycleLock = NSRecursiveLock()
    private var finished = false
    private let audio = ReelAudioTranscriber()
    private var uploading = false
    private var pendingUploads: [ReelIngestPayload] = []
    private let uploadLock = NSLock()
    private let tracker = ReelTracker()
    private var count = 0
    private var interval = 15
    private var sinceCheckpoint = 0
    private var timer: DispatchSourceTimer?

    override func broadcastStarted(withSetupInfo setupInfo: [String: NSObject]?) {
        SharedStore.sessionID = UUID().uuidString
        SharedStore.broadcastStartedAt = Date()
        count = 0
        SharedStore.broadcastReelCount = 0
        SharedStore.broadcastFlaggedEvents = []
        sinceCheckpoint = 0
        AgentNotification.register()
        interval = SharedStore.checkpointInterval
        SharedStore.stopRequested = false
        SharedStore.heartbeat()

        tracker.onObservation = { [weak self] obs in self?.emit(obs) }

        let t = DispatchSource.makeTimerSource(queue: .global(qos: .utility))
        t.schedule(deadline: .now() + 0.25, repeating: 0.25)
        t.setEventHandler { [weak self] in
            guard let self else { return }
            self.lifecycleLock.lock()
            defer { self.lifecycleLock.unlock() }
            guard !self.finished else { return }
            if SharedStore.stopRequested {
                // ReplayKit may not call broadcastFinished after an error-driven finish.
                self.broadcastFinished()
                self.finishBroadcastWithError(NSError(
                    domain: "AttentionAgent", code: 1,
                    userInfo: [NSLocalizedDescriptionKey: "Monitoring stopped."]))
            } else { SharedStore.heartbeat() }
        }
        t.resume()
        timer = t
    }

    override func broadcastFinished() {
        lifecycleLock.lock(); defer { lifecycleLock.unlock() }
        guard !finished else { return }
        finished = true
        timer?.cancel()
        tracker.flush()
        _ = audio.finish()
        if SharedStore.useLiveBackend {
            let sessionID = SharedStore.sessionID
            Task {
                while hasPendingUploads() {
                    try? await Task.sleep(for: .milliseconds(100))
                }
                do { try await LiveAgentBackend().endSession(sessionID) }
                catch { NSLog("Session end failed: %@", error.localizedDescription) }
            }
        }
        SharedStore.clearHeartbeat()
    }

    override func processSampleBuffer(_ sampleBuffer: CMSampleBuffer, with type: RPSampleBufferType) {
        lifecycleLock.lock(); defer { lifecycleLock.unlock() }
        guard !finished, !SharedStore.stopRequested else { return }
        if type == .audioApp { audio.append(sampleBuffer); return }
        guard type == .video, let pb = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        let t = CMTimeGetSeconds(CMSampleBufferGetPresentationTimeStamp(sampleBuffer))
        autoreleasepool { tracker.process(pixelBuffer: pb, time: t) }
    }

    private func emit(_ obs: ReelObservation) {
        let transcript = audio.finish()
        if SharedStore.useLiveBackend && !obs.sensitiveSkipped {
            let payload = ReelIngestPayload(sessionId: SharedStore.sessionID, reelIndex: count + 1,
                dwellSeconds: min(obs.duration, 90), ocrText: obs.ocrText, transcriptText: transcript,
                frameBase64: tracker.frameBase64, sensitivity: SharedStore.sensitivity, statedGoals: SharedStore.userGoal,
                startedAt: obs.startedAt)
            enqueue(payload)
        }
        SharedStore.append(obs)
        DarwinNotifier.post()
        interval = SharedStore.checkpointInterval
        count += 1
        SharedStore.broadcastReelCount = count
        if !SharedStore.useLiveBackend {
            SharedStore.broadcastFlaggedEvents += MockAgentBackend.digest(observations: [obs], total: 1).flaggedReels
        }
        sinceCheckpoint += 1
        if sinceCheckpoint >= interval { sinceCheckpoint = 0; notifyCheckpoint() }
    }

    private func enqueue(_ payload: ReelIngestPayload) {
        uploadLock.lock()
        // Bound retained JPEG data during server outages.
        if pendingUploads.count >= 8 {
            uploadLock.unlock()
            NSLog("Reel upload queue full; packet dropped")
            return
        }
        pendingUploads.append(payload)
        let shouldStart = !uploading
        uploading = true
        uploadLock.unlock()
        if shouldStart { Task { await drainUploads() } }
    }
    private func nextUpload() -> ReelIngestPayload? {
        uploadLock.lock(); defer { uploadLock.unlock() }
        guard !pendingUploads.isEmpty else { uploading = false; return nil }
        return pendingUploads.removeFirst()
    }
    private func hasPendingUploads() -> Bool {
        uploadLock.lock(); defer { uploadLock.unlock() }
        return uploading || !pendingUploads.isEmpty
    }
    private func drainUploads() async {
        while let payload = nextUpload() {
            do { try await LiveAgentBackend().ingest(payload) }
            catch { NSLog("Reel upload failed: %@", error.localizedDescription) }
        }
    }
    private func notifyCheckpoint() {
        Task {
            do {
                let backend: any AgentBackendServing = SharedStore.useLiveBackend ? LiveAgentBackend() : MockAgentBackend()
                let digest: CheckpointDigest
                do { digest = try await backend.fetchCheckpointDigest(reelsWatched: min(count, interval), threshold: interval) }
                catch {
                    digest = CheckpointDigest(totalReelsWatched: min(count, interval), flaggedReels: [], userGoalConflict: nil,
                        coachHookMessage: "Checkpoint reached. Analysis is unavailable; open the app to reconnect.", suggestedAction: "Pause and check your connection.")
                }
                if SharedStore.useLiveBackend {
                    let existing = SharedStore.broadcastFlaggedEvents
                    let ids = Set(existing.map(\.id))
                    SharedStore.broadcastFlaggedEvents = existing + digest.flaggedReels.filter { !ids.contains($0.id) }
                }
                SharedStore.latestDigest = digest
                try await AgentNotification.deliver(digest)
                SharedStore.notificationError = nil
            } catch {
                SharedStore.notificationError = error.localizedDescription
                NSLog("Checkpoint notification failed: %@", error.localizedDescription)
            }
        }
    }
}
