import ReplayKit

/// Captures this app's own screen with ReplayKit. Used to test the detector
/// against the demo feed. It does not see other apps.
final class InAppCapture {
    var onFrame: ((CVPixelBuffer, TimeInterval) -> Void)?

    func start(completion: @escaping (Error?) -> Void) {
        let rec = RPScreenRecorder.shared()
        guard rec.isAvailable else {
            completion(NSError(domain: "AttentionAgent", code: 2,
                               userInfo: [NSLocalizedDescriptionKey: "Screen capture is unavailable."]))
            return
        }
        rec.isMicrophoneEnabled = false
        rec.startCapture(handler: { [weak self] sample, type, error in
            guard error == nil, type == .video,
                  CMSampleBufferDataIsReady(sample),
                  let pb = CMSampleBufferGetImageBuffer(sample) else { return }
            let t = CMTimeGetSeconds(CMSampleBufferGetPresentationTimeStamp(sample))
            self?.onFrame?(pb, t)
        }, completionHandler: completion)
    }

    func stop() {
        onFrame = nil
        RPScreenRecorder.shared().stopCapture { _ in }
    }
}
