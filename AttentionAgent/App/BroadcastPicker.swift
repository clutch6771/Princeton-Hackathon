import SwiftUI
import ReplayKit

/// Invisible system broadcast picker. Bump `trigger` to open it, which shows
/// Apple's "Start Broadcast" sheet for the AttentionBroadcast extension.
struct BroadcastPicker: UIViewRepresentable {
    let trigger: Int

    /// Resolve the installed provider rather than assuming the signing bundle ID.
    /// Never open an unfiltered picker when the extension is missing.
    static var installedExtensionID: String? {
        guard let directory = Bundle.main.builtInPlugInsURL,
              let urls = try? FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil) else { return nil }
        return urls.compactMap { Bundle(url: $0) }.first {
            let info = $0.infoDictionary?["NSExtension"] as? [String: Any]
            return info?["NSExtensionPointIdentifier"] as? String == "com.apple.broadcast-services-upload"
                && $0.bundleURL.deletingPathExtension().lastPathComponent == AppConfig.extensionName
        }?.bundleIdentifier
    }

    func makeUIView(context: Context) -> RPSystemBroadcastPickerView {
        let v = RPSystemBroadcastPickerView(frame: CGRect(x: 0, y: 0, width: 44, height: 44))
        v.preferredExtension = Self.installedExtensionID ?? AppConfig.extensionBundleID
        v.showsMicrophoneButton = false
        return v
    }

    func updateUIView(_ view: RPSystemBroadcastPickerView, context: Context) {
        guard context.coordinator.last != trigger else { return }
        context.coordinator.last = trigger
        guard trigger > 0, let identifier = Self.installedExtensionID else { return }
        view.preferredExtension = identifier
        view.showsMicrophoneButton = false
        for sub in view.subviews {
            if let button = sub as? UIButton { button.sendActions(for: .touchUpInside) }
        }
    }

    func makeCoordinator() -> Coordinator { Coordinator() }
    final class Coordinator { var last = 0 }
}
