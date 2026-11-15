import SwiftUI
import UserNotifications
import Speech

struct HomeView: View {
    @Environment(SessionManager.self) private var session
    @State private var trigger = 0
    @State private var showError = false
    @State private var preparing = false
    @State private var showPermissionNotice = false
    @State private var permissionNotice = ""
    @Environment(\.openURL) private var openURL
    private var ratio: Double { Double(session.sessionFlagged) / Double(max(1, session.sessionTotal)) }
    var body: some View {
        GeometryReader { geometry in
            ScrollView {
                VStack(spacing: 0) {
            Spacer(minLength: 24)
            Group {
                // Opens once the system broadcast prompt is confirmed, closes when monitoring stops.
                if IrisUI.irisEye { IrisEye(open: session.isMonitoring).frame(width: 150, height: 122).padding(.bottom, 28) }
                else { FriendlyEye(open: session.isMonitoring).frame(width: 150, height: 90).padding(.bottom, 44) }
            }.accessibilityElement().accessibilityLabel(session.isMonitoring ? "Eye open, monitoring" : "Eye closed, resting")
            Button {
                Vigil.tap()
                if session.isMonitoring { session.stop() }
                else {
                    Task { await prepareMonitoring() }
                }
            } label: {
                Text(session.isMonitoring ? "STOP" : "START")
                    .font(IrisUI.font(size: 30)).tracking(2)
                    .foregroundStyle(session.isMonitoring ? Color.white : Vigil.canvas).frame(width: 190, height: 190)
                    .background(session.isMonitoring ? Vigil.monitoringButton : Vigil.accent, in: Circle())
                    .shadow(color: (session.isMonitoring ? Color(uiColor: IrisUI.teal) : Color(uiColor: IrisUI.mint)).opacity(0.18), radius: 28, y: 8)
            }.disabled(preparing).buttonStyle(.plain).animation(Vigil.spring, value: session.isMonitoring)
                .background(BroadcastPicker(trigger: trigger).frame(width: 1, height: 1).opacity(0.01))
            GeometryReader { geometry in
                Capsule().fill(.white.opacity(0.28)).overlay(alignment: .leading) {
                    Capsule().fill(Vigil.coral).frame(width: geometry.size.width * ratio)
                }
            }.frame(height: 13).frame(maxWidth: 300).padding(.top, 44)
                .animation(Vigil.spring, value: ratio)
                .accessibilityLabel("Flagged share of watched reels").accessibilityValue("\(Int(ratio * 100)) percent")
            HStack {
                VStack(spacing: 5) { Text("\(session.sessionFlagged)").foregroundStyle(Vigil.coral); Text("Flagged").font(IrisUI.font(.caption)).foregroundStyle(.secondary) }
                Spacer()
                VStack(spacing: 5) { Text("\(max(0, session.sessionTotal - session.sessionFlagged))"); Text("Unflagged").font(IrisUI.font(.caption)).foregroundStyle(.secondary) }
                Spacer()
                VStack(spacing: 5) { Text("\(session.sessionTotal)"); Text("Watched").font(IrisUI.font(.caption)).foregroundStyle(.secondary) }
            }.font(IrisUI.font(.headline).monospacedDigit()).frame(maxWidth: 300).padding(.top, 18)
            Text(session.sessionTotal == 0 ? "No completed reels detected yet" : "Most recent session")
                .font(IrisUI.font(.caption)).foregroundStyle(.secondary).padding(.top, 12)
            Spacer(minLength: 24)
                }.frame(maxWidth: .infinity).frame(minHeight: max(0, geometry.size.height - 24))
                    .padding(.horizontal, 20).padding(.vertical, 12)
            }.scrollBounceBehavior(.basedOnSize)
        }.toolbar(.hidden, for: .navigationBar)
            .alert("Some features are unavailable", isPresented: $showPermissionNotice) {
                Button("Continue Monitoring") { beginBroadcast() }
                Button("Open Settings") { if let url = URL(string: UIApplication.openSettingsURLString) { openURL(url) } }
                Button("Cancel", role: .cancel) { }
            } message: { Text(permissionNotice) }
            .onChange(of: session.broadcastControlTrigger) { _, _ in trigger += 1 }
            .alert("Monitoring unavailable", isPresented: $showError) { Button("OK", role: .cancel) {} } message: { Text(session.statusMessage ?? "Please try again.") }
    }

    @MainActor private func prepareMonitoring() async {
        guard !preparing else { return }
        preparing = true
        defer { preparing = false }
        var unavailable: [String] = []
        let center = UNUserNotificationCenter.current()
        let settings = await center.notificationSettings()
        if settings.authorizationStatus == .notDetermined {
            do { _ = try await center.requestAuthorization(options: [.alert, .sound]) }
            catch { unavailable.append("Notification permission could not be requested.") }
        }
        let notifications = await center.notificationSettings()
        if notifications.authorizationStatus != .authorized && notifications.authorizationStatus != .provisional {
            unavailable.append("Notifications are off, so checkpoint alerts won't appear.")
        }
        // Reel soundtrack is supplied by ReplayKit .audioApp; microphone access is not used.
        if SFSpeechRecognizer.authorizationStatus() == .notDetermined {
            let _: SFSpeechRecognizerAuthorizationStatus = await withCheckedContinuation { continuation in
                SFSpeechRecognizer.requestAuthorization { continuation.resume(returning: $0) }
            }
        }
        if SFSpeechRecognizer.authorizationStatus() != .authorized {
            unavailable.append("Speech Recognition is off, so reel audio won't be transcribed.")
        }
        if unavailable.isEmpty { beginBroadcast() }
        else { permissionNotice = unavailable.joined(separator: "\n\n"); showPermissionNotice = true }
    }

    @MainActor private func beginBroadcast() {
        guard BroadcastPicker.installedExtensionID != nil else {
            session.statusMessage = "AttentionAgent's screen broadcast extension is missing. Reinstall the complete app to enable monitoring."
            showError = true
            return
        }
        session.setMode(.instagramBroadcast)
        session.start()
        if SharedStore.isAvailable && Date().timeIntervalSince1970 >= SharedStore.pausedUntil { trigger += 1 }
        else { showError = true }
    }
}
struct FriendlyEye: View {
    let open: Bool
    var body: some View {
        ZStack {
            if open {
                Path { p in
                    p.move(to: CGPoint(x: 5, y: 45))
                    p.addQuadCurve(to: CGPoint(x: 145, y: 45), control: CGPoint(x: 75, y: -22))
                    p.addQuadCurve(to: CGPoint(x: 5, y: 45), control: CGPoint(x: 75, y: 108))
                }.fill(.white.opacity(0.95))
                Circle().fill(Vigil.accent).frame(width: 54, height: 54)
                Circle().fill(Vigil.canvas).frame(width: 28, height: 28)
                Circle().fill(.white).frame(width: 12, height: 12).offset(x: -8, y: -9)
                Circle().fill(.white.opacity(0.7)).frame(width: 5, height: 5).offset(x: 9, y: 9)
            } else {
                Path { p in p.move(to: CGPoint(x: 10, y: 30)); p.addQuadCurve(to: CGPoint(x: 140, y: 30), control: CGPoint(x: 75, y: 96)) }
                    .stroke(.white, style: StrokeStyle(lineWidth: 7, lineCap: .round))
                ForEach(0..<3) { i in Capsule().fill(.white).frame(width: 6, height: 17).rotationEffect(.degrees(Double(i - 1) * -25)).offset(x: CGFloat(i - 1) * 38, y: 25) }
            }
        }.animation(Vigil.spring, value: open)
    }
}
