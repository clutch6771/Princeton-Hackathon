import SwiftUI

struct HomeView: View {
    @Environment(SessionManager.self) private var session
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
