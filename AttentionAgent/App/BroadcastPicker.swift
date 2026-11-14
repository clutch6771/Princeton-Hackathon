import SwiftUI
import ReplayKit

/// Invisible system broadcast picker. Bump `trigger` to open it, which shows
/// Apple's "Start Broadcast" sheet for the AttentionBroadcast extension.
struct BroadcastPicker: UIViewRepresentable {
    let trigger: Int

    func makeUIView(context: Context) -> RPSystemBroadcastPickerView {
        let v = RPSystemBroadcastPickerView(frame: CGRect(x: 0, y: 0, width: 44, height: 44))
        v.preferredExtension = AppConfig.extensionBundleID
        v.showsMicrophoneButton = false
        return v
    }

    func updateUIView(_ view: RPSystemBroadcastPickerView, context: Context) {
        guard context.coordinator.last != trigger else { return }
        context.coordinator.last = trigger
        guard trigger > 0 else { return }
        for sub in view.subviews {
            if let button = sub as? UIButton { button.sendActions(for: .touchUpInside) }
        }
    }

    func makeCoordinator() -> Coordinator { Coordinator() }
    final class Coordinator { var last = 0 }
}
