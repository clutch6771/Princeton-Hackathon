import Foundation
import Speech
import CoreMedia

/// Serializes recognition callbacks and capture access; never falls back to server speech.
final class ReelAudioTranscriber {
    private let recognizer = SFSpeechRecognizer()
    private let lock = NSRecursiveLock()
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var generation = UUID()
    private var transcript = ""
    private var began = Date()
    func append(_ buffer: CMSampleBuffer) {
        lock.lock(); defer { lock.unlock() }
        guard SFSpeechRecognizer.authorizationStatus() == .authorized,
              let recognizer, recognizer.supportsOnDeviceRecognition, recognizer.isAvailable else { return }
        if request == nil {
            began = Date()
            let token = generation
            let next = SFSpeechAudioBufferRecognitionRequest()
            next.requiresOnDeviceRecognition = true
            next.shouldReportPartialResults = true
            request = next
            task = recognizer.recognitionTask(with: next) { [weak self] result, _ in
                guard let self else { return }
                self.lock.lock(); defer { self.lock.unlock() }
                guard self.generation == token else { return }
                if let result { self.transcript = String(result.bestTranscription.formattedString.prefix(500)) }
            }
        }
        // Bound each recognition segment, even on unusually long reels.
        if Date().timeIntervalSince(began) < 50 { request?.appendAudioSampleBuffer(buffer) }
    }
    func finish() -> String {
        lock.lock(); defer { lock.unlock() }
        let value = transcript
        generation = UUID()
        request?.endAudio(); task?.cancel()
        request = nil; task = nil; transcript = ""
        return value
    }
    deinit { _ = finish() }
}
