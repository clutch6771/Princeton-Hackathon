import Foundation
import UserNotifications

final class CheckpointNotifier {
    func requestPermission() {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }
    }

    func deliver(_ report: CheckpointReport) {
        let digest = CheckpointDigest(totalReelsWatched: report.reelCount, flaggedReels: [], userGoalConflict: report.mismatches.first, coachHookMessage: report.headline, suggestedAction: "Check in with your Guardian.")
        Task {
            do { try await AgentNotification.deliver(digest) }
            catch { SharedStore.notificationError = error.localizedDescription }
        }
    }
}
