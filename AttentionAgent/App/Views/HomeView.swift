import SwiftUI
import Speech

struct HomeView: View {
    @Environment(SessionManager.self) private var session
    @State private var showConnection = false
    @State private var pickerTrigger = 0

    var body: some View {
        VStack(spacing: 20) {
            Spacer()
            Image(systemName: session.isMonitoring ? "eye.fill" : "eye.slash")
                .font(.system(size: 64))
                .foregroundStyle(session.isMonitoring ? .green : .secondary)

            Text(session.isMonitoring ? "Monitoring is on" : "Monitoring is off")
                .font(.title2.bold())

            Picker("Source", selection: Binding(
                get: { session.mode }, set: { session.setMode($0) })) {
                ForEach(SourceMode.allCases) { Text($0.rawValue).tag($0) }
            }
            .pickerStyle(.menu)
            .disabled(session.isMonitoring)

            VStack {
                Picker("Checkpoint", selection: Binding(get: { session.checkpointInterval }, set: { session.setInterval($0) })) {
                    ForEach([10, 15, 30, 50], id: \.self) { Text("\($0) reels").tag($0) }
                }.pickerStyle(.segmented)
                Stepper("Every \(session.checkpointInterval) reels", value: Binding(get: { session.checkpointInterval }, set: { session.setInterval($0) }), in: 1...100)
                Button("Simulate Checkpoint") { session.simulateCheckpoint() }
                    .buttonStyle(.bordered)
                Text("Demo coach • simulated checkpoint data").font(.caption).foregroundStyle(.secondary)
            }.padding(.horizontal)

            Button("Backend Connection & Permissions") { showConnection = true }
            Text(hint)
                .font(.footnote)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal)

            Button {
                if session.isMonitoring {
                    session.stop()
                } else {
                    session.start()
                    if session.mode == .instagramBroadcast, SharedStore.isAvailable {
                        pickerTrigger += 1
                    }
                }
            } label: {
                Text(session.isMonitoring ? "Stop Monitoring" : "Start Monitoring")
                    .font(.headline)
                    .frame(maxWidth: .infinity)
                    .padding()
                    .background(session.isMonitoring ? Color.red : Color.accentColor)
                    .foregroundStyle(.white)
                    .clipShape(RoundedRectangle(cornerRadius: 14))
            }
            .padding(.horizontal)

            if let msg = session.statusMessage {
                Text(msg).font(.footnote).foregroundStyle(.orange)
                    .multilineTextAlignment(.center).padding(.horizontal)
            }

            if session.isMonitoring {
                Text("\(session.reelCount) Reels, checkpoint every \(session.checkpointInterval)")
                    .font(.footnote.monospacedDigit())
            }
            Spacer()
        }
        .background(
            BroadcastPicker(trigger: pickerTrigger)
                .frame(width: 44, height: 44)
                .opacity(0.01)
                .allowsHitTesting(false)
        )
        .sheet(isPresented: $showConnection) { NavigationStack { BackendConnectionView() } }
        .navigationTitle("AttentionAgent")
    }

    private var hint: String {
        switch session.mode {
        case .feedDirect:
            return "Opens the Reels tab. Each swipe is counted directly. Use this to test the interface."
        case .inAppCapture:
            return "Captures this app's own screen and runs the real detector on the Reels tab. Stay on that tab."
        case .instagramBroadcast:
            return "Tap Start, choose AttentionBroadcast, tap Start Broadcast, then open Instagram. Stop here or from the red status bar."
        }
    }
}


struct BackendConnectionView: View {
    @State private var url = SharedStore.backendBaseURL
    @State private var live = SharedStore.useLiveBackend
    @State private var connected: Bool?
    @State private var testing = false
    @State private var speech = SFSpeechRecognizer.authorizationStatus()
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
            Section("Speech Recognition Permission") {
                Text(speech == .authorized ? "Authorized • on-device only" : speech == .notDetermined ? "Not requested" : "Unavailable — enable Speech Recognition in Settings")
                Button("Request Speech Permission") {
                    SFSpeechRecognizer.requestAuthorization { status in Task { @MainActor in speech = status } }
                }
            }
        }.navigationTitle("Connection & Permissions")
    }
}
