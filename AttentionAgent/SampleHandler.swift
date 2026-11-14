import ReplayKit
import UserNotifications

/// Broadcast Upload Extension. Receives the screen while Instagram is open,
/// detects Reels on device, and writes observations to the App Group.
class SampleHandler: RPBroadcastSampleHandler {
    private let audio = ReelAudioTranscriber()
    private var uploading = false
    private var pendingUploads: [ReelIngestPayload] = []
    private let uploadLock = NSLock()
    private let tracker = ReelTracker()
    private var count = 0
    private var interval = 15
    private var timer: DispatchSourceTimer?

    override func broadcastStarted(withSetupInfo setupInfo: [String: NSObject]?) {
        SharedStore.sessionID = UUID().uuidString
        interval = SharedStore.checkpointInterval
        SharedStore.stopRequested = false
        SharedStore.heartbeat()

        tracker.onObservation = { [weak self] obs in self?.emit(obs) }

        let t = DispatchSource.makeTimerSource(queue: .global(qos: .utility))
        t.schedule(deadline: .now() + 1, repeating: 2)
        t.setEventHandler { [weak self] in
            SharedStore.heartbeat()
            if SharedStore.stopRequested {
                self?.finishBroadcastWithError(NSError(
                    domain: "AttentionAgent", code: 1,
                    userInfo: [NSLocalizedDescriptionKey: "Monitoring stopped."]))
            }
        }
        t.resume()
        timer = t
    }

    override func broadcastFinished() {
        timer?.cancel()
        tracker.flush()
        _ = audio.finish()
        SharedStore.clearHeartbeat()
    }

    override func processSampleBuffer(_ sampleBuffer: CMSampleBuffer, with type: RPSampleBufferType) {
        if type == .audioApp { audio.append(sampleBuffer); return }
        guard type == .video, let pb = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        let t = CMTimeGetSeconds(CMSampleBufferGetPresentationTimeStamp(sampleBuffer))
        autoreleasepool { tracker.process(pixelBuffer: pb, time: t) }
    }

    private func emit(_ obs: ReelObservation) {
        let transcript = audio.finish()
        if SharedStore.useLiveBackend && !obs.sensitiveSkipped {
            let payload = ReelIngestPayload(sessionId: SharedStore.sessionID, reelIndex: count + 1,
                dwellSeconds: obs.duration, ocrText: obs.ocrText, transcriptText: transcript,
                frameBase64: tracker.frameBase64, sensitivity: SharedStore.sensitivity, statedGoals: SharedStore.userGoal)
            enqueue(payload)
        }
        SharedStore.append(obs)
        DarwinNotifier.post()
        interval = SharedStore.checkpointInterval
        count += 1
        if count % interval == 0 { notifyCheckpoint() }
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
                let digest = try await backend.fetchCheckpointDigest(reelsWatched: min(count, interval), threshold: interval)
                try await AgentNotification.deliver(digest)
            } catch { NSLog("Checkpoint notification failed: %@", error.localizedDescription) }
        }
    }
}
