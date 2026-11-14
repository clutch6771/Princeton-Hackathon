import Foundation

/// IDs are derived from the app's bundle ID, so nothing to edit as long as the
/// extension target is named exactly "AttentionBroadcast" and the App Group is
/// "group.<your app bundle id>".
enum AppConfig {
    static let extensionName = "AttentionBroadcast"

    static var appBundleID: String {
        let id = Bundle.main.bundleIdentifier ?? "com.example.AttentionAgent"
        let suffix = "." + extensionName
        return id.hasSuffix(suffix) ? String(id.dropLast(suffix.count)) : id
    }
    static var extensionBundleID: String { appBundleID + "." + extensionName }
    static var appGroupID: String { "group." + appBundleID }
}
