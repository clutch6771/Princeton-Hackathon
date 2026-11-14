import Foundation
import UserNotifications

final class CheckpointNotifier {
    func requestPermission() {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }
    }

    func deliver(_ report: CheckpointReport) {
        let c = UNMutableNotificationContent()
        c.title = "Checkpoint: \(report.reelCount) Reels"
        c.body = report.headline
        c.sound = .default
        UNUserNotificationCenter.current().add(
            UNNotificationRequest(identifier: UUID().uuidString, content: c, trigger: nil))
    }
}
