import SwiftUI
import UserNotifications

struct SettingsView: View {
    @Environment(SessionManager.self) private var session
    @Environment(\.openURL) private var openURL
    var body: some View {
        Form {
            Section("Theme") {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 16) {
                        ForEach(IrisTheme.presets) { theme in
                            let selected = IrisThemeStore.shared.theme == theme
                            Button { Vigil.tap(); withAnimation(Vigil.spring) { IrisThemeStore.shared.select(theme) } } label: {
                                VStack(spacing: 8) {
                                    Circle().fill(theme.swatch).frame(width: 52, height: 52)
                                        .overlay(Circle().stroke(.white.opacity(selected ? 0.95 : 0.15), lineWidth: selected ? 3 : 1))
                                    Text(theme.name).font(IrisUI.font(.caption)).foregroundStyle(selected ? .primary : .secondary)
                                }
                            }.buttonStyle(.plain).accessibilityLabel("\(theme.name) theme").accessibilityAddTraits(selected ? .isSelected : [])
                        }
                    }.padding(.vertical, 6)
                }
            }
            Section {
                NavigationLink("Set Checkpoints") {
                    Form {
                        Stepper("Every \(session.checkpointInterval) reels", value: Binding(get: { session.checkpointInterval }, set: { session.setInterval($0) }), in: 1...100)
                        ForEach([10, 15, 30, 50], id: \.self) { n in
                            Button { session.setInterval(n) } label: { HStack { Text("Every \(n) reels"); Spacer(); if session.checkpointInterval == n { Image(systemName: "checkmark") } } }
                        }
                    }.navigationTitle("Set Checkpoints")
                }
                NavigationLink("Adjust Notification Preferences") { NotificationPreferencesView() }
                NavigationLink("Backend Connection") { BackendConnectionView() }
            }
            Section {
                permission("Allow Notifications", icon: "bell")
                permission("Allow Speech", icon: "waveform")
                permission("Allow Screen Recording", icon: "record.circle")
            } footer: { Text("Permissions open Apple Settings. Screen recording is approved each time you start a broadcast.") }
        }.scrollContentBackground(.hidden).background(IrisUI.liveBackdrop ? AnyShapeStyle(.clear) : AnyShapeStyle(Vigil.background)).navigationTitle("Settings")
    }
    private func permission(_ title: String, icon: String) -> some View {
        Button { if let url = URL(string: UIApplication.openSettingsURLString) { openURL(url) } } label: {
            HStack { Label(title, systemImage: icon); Spacer(); Image(systemName: "arrow.up.right").font(IrisUI.font(.caption)).foregroundStyle(.secondary) }
        }.foregroundStyle(.primary)
    }
}
struct NotificationPreferencesView: View {
    @AppStorage("notificationFields", store: UserDefaults(suiteName: AppConfig.appGroupID)) private var fields = "ratio,total,flagged,neutral,comment"
    @Environment(\.scenePhase) private var phase
    @State private var bannerStatus = "Checking…"
    @State private var deliveryStatus = ""
    @State private var testStatus: String?
    private let options = [("ratio", "Ratio"), ("total", "Total reels watched"), ("flagged", "Total flagged reels"), ("neutral", "Total neutral reels"), ("comment", "Agent comment")]
    var body: some View {
        Form {
            ForEach(options, id: \.0) { key, label in
                Button {
                    var selected = Set(fields.split(separator: ",").map(String.init))
                    if selected.contains(key) { selected.remove(key) } else { selected.insert(key) }
                    fields = selected.sorted().joined(separator: ",")
                } label: {
                    HStack { Text(label).foregroundStyle(.primary); Spacer(); Image(systemName: fields.split(separator: ",").contains(Substring(key)) ? "checkmark.square.fill" : "square").foregroundStyle(Vigil.cyan) }
                }.accessibilityValue(fields.split(separator: ",").contains(Substring(key)) ? "Selected" : "Not selected")
            }
            Section("Notification delivery") {
                Text(bannerStatus).font(IrisUI.font(.subheadline))
                Text("Reels detected this broadcast: \(SharedStore.broadcastReelCount)").font(IrisUI.font(.caption))
                if let scheduled = SharedStore.lastNotificationScheduled {
                    Text("Last scheduled: \(scheduled.formatted(date: .abbreviated, time: .standard))").font(IrisUI.font(.caption))
                } else { Text("No notification scheduled yet.").font(IrisUI.font(.caption)) }
                if !deliveryStatus.isEmpty { Text(deliveryStatus).font(IrisUI.font(.caption)) }
            }
            Section("Try your preferences") {
                let sample = CheckpointDigest(totalReelsWatched: 15, flaggedReels: (0..<3).map { _ in FlaggedReelEvent(id: UUID(), timestamp: Date(), topic: "Debate", emotionTag: "anger", dwellSeconds: 12) }, userGoalConflict: nil, coachHookMessage: "Some reels felt heated. Take a moment to reset your feed.", suggestedAction: "Pause for a breath.")
                if let preview = try? AgentNotification.content(for: sample) {
                    Text(preview.title).font(IrisUI.font(.headline))
                    if !preview.subtitle.isEmpty { Text(preview.subtitle).font(IrisUI.font(.caption)) }
                    Text(preview.body).font(IrisUI.font(.subheadline))
                }
                Button("Send Test Notification") {
                    Task {
                        do {
                            _ = try await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound])
                            try await AgentNotification.deliver(sample)
                            testStatus = "Test scheduled. A banner should appear shortly."
                            try await Task.sleep(for: .seconds(3))
                            let delivered = await UNUserNotificationCenter.current().deliveredNotifications()
                            deliveryStatus = delivered.contains { $0.request.identifier == sample.id.uuidString }
                                ? "iOS reports the test in Notification Center."
                                : "Test scheduled, but not found in Notification Center. Check the banner status above."
                            await refreshNotificationStatus()
                        } catch { testStatus = error.localizedDescription }
                    }
                }
                if let testStatus { Text(testStatus).font(IrisUI.font(.caption)) }
                if let error = SharedStore.notificationError { Text(error).font(IrisUI.font(.caption)).foregroundStyle(Vigil.coral) }
            }
        }.navigationTitle("Notification Preferences")
            .task { await refreshNotificationStatus() }
            .onChange(of: phase) { _, value in if value == .active { Task { await refreshNotificationStatus() } } }
    }
    private func refreshNotificationStatus() async {
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        if settings.authorizationStatus == .denied || settings.authorizationStatus == .notDetermined {
            bannerStatus = "Notifications are not authorized."
        } else if settings.authorizationStatus == .provisional {
            bannerStatus = "Quiet delivery only. Enable full alerts in Apple Settings."
        } else if settings.alertSetting != .enabled || settings.alertStyle == .none {
            bannerStatus = "Notifications allowed, but banners are disabled."
        } else {
            bannerStatus = "Banner alerts enabled."
        }
        if settings.scheduledDeliverySetting == .enabled { bannerStatus += " Scheduled Summary is enabled." }
    }
}
struct BackendConnectionView: View {
    @State private var url = SharedStore.backendBaseURL
    @State private var live = SharedStore.useLiveBackend
    @State private var connected: Bool?
    @State private var testing = false
    var body: some View {
        Form {
            Section("Backend Connection") {
                TextField("Server URL", text: $url).textInputAutocapitalization(.never).autocorrectionDisabled()
                    .onChange(of: url) { _, value in SharedStore.backendBaseURL = value; connected = nil }
                Toggle("Live Python Backend", isOn: $live).onChange(of: live) { _, value in SharedStore.useLiveBackend = value }
                Text(live ? "Live mode" : "Mock Mode")
                Button(testing ? "Testing…" : "Test Connection") {
                    testing = true
                    Task {
                        do { try await LiveAgentBackend().health(); connected = true }
                        catch { connected = false }
                        testing = false
                    }
                }.disabled(testing)
                if let connected { Text(connected ? "Connected" : "Offline").foregroundStyle(connected ? .green : .red) }
            }
        }
        .scrollDismissesKeyboard(.interactively)
        .scrollBounceBehavior(.always)
        .navigationTitle("Backend Connection")
    }
}
