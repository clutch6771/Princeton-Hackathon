import SwiftUI

struct SettingsView: View {
    @Environment(SessionManager.self) private var session
    @AppStorage("offline") private var offline = true
    @AppStorage("backendURL") private var backendURL = ""
    @State private var confirmDelete = false

    var body: some View {
        Form {
            Section("Checkpoint") {
                Stepper("Every \(session.checkpointInterval) Reels",
                        value: Binding(get: { session.checkpointInterval },
                                       set: { session.setInterval($0) }),
                        in: 3...50)
                    .disabled(session.isMonitoring)
            }
            Section("Backend") {
                NavigationLink("Backend Connection & Speech Permissions") { BackendConnectionView() }
            }
            Section("What is sent to the backend") {
                Text("Per Reel: an ID, start and end time, duration, the text read on screen, and a confidence score. In live broadcast mode, one downscaled snapshot and the on-device audio transcript are also sent per reel. Screens that look like they contain passwords or card numbers are skipped.")
                    .font(.footnote)
            }
            Section("Your data") {
                Button("Delete session history", role: .destructive) { confirmDelete = true }
            }
        }
        .navigationTitle("Settings")
        .confirmationDialog("Delete all session history?", isPresented: $confirmDelete) {
            Button("Delete", role: .destructive) { session.deleteHistory() }
        }
    }
}
