import SwiftUI
import UserNotifications

final class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        AgentNotification.register()
        return true
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                willPresent notification: UNNotification,
                                withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .list, .sound])
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                didReceive response: UNNotificationResponse,
                                withCompletionHandler completionHandler: @escaping () -> Void) {
        if response.actionIdentifier != AgentNotification.keep,
           response.actionIdentifier != UNNotificationDismissActionIdentifier,
           let data = response.notification.request.content.userInfo["digest"] as? Data,
           let digest = try? JSONDecoder().decode(CheckpointDigest.self, from: data) {
            Task { @MainActor in
                SessionManager.shared.handleNotificationTap(digest, reset: response.actionIdentifier == AgentNotification.reset)
            }
        }
        completionHandler()
    }
}

@main
struct AttentionAgentApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var delegate

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(SessionManager.shared)
                .task { SessionManager.shared.bootstrap() }
        }
    }
}
