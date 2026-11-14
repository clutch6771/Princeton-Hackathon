import ReplayKit
import UserNotifications

/// Broadcast Upload Extension. Receives the screen while Instagram is open,
/// detects Reels on device, and writes observations to the App Group.
class SampleHandler: RPBroadcastSampleHandler {
    private let tracker = ReelTracker()
    private var count = 0
    private var interval = 15
    private var timer: DispatchSourceTimer?

    override func broadcastStarted(withSetupInfo setupInfo: [String: NSObject]?) {
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
        SharedStore.clearHeartbeat()
    }

    override func processSampleBuffer(_ sampleBuffer: CMSampleBuffer, with type: RPSampleBufferType) {
        guard type == .video, let pb = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        let t = CMTimeGetSeconds(CMSampleBufferGetPresentationTimeStamp(sampleBuffer))
        tracker.process(pixelBuffer: pb, time: t)
    }

    private func emit(_ obs: ReelObservation) {
        SharedStore.append(obs)
        DarwinNotifier.post()
        count += 1
        if count % interval == 0 { notifyCheckpoint() }
    }

    private func notifyCheckpoint() {
        let c = UNMutableNotificationContent()
        c.title = "Checkpoint: \(count) Reels"
        c.body = "Tap to see how they compare with your goals."
        c.sound = .default
        UNUserNotificationCenter.current().add(
            UNNotificationRequest(identifier: UUID().uuidString, content: c, trigger: nil))
    }
}
