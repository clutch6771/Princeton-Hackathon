import SwiftUI

struct MonitoringView: View {
    @Environment(SessionManager.self) private var session

    var body: some View {
        List {
            Section("Session") {
                row("Status", session.isMonitoring ? "Active" : "Stopped")
                row("Source", session.mode.rawValue)
                row("Reels detected", "\(session.reelCount)")
                row("Analyzed", "\(session.analyzedCount)")
                row("Flagged", "\(session.flaggedCount)")
                row("Failed", "\(session.failedCount)")
                row("Checkpoint every", "\(session.checkpointInterval)")
                if !session.debugLine.isEmpty {
                    row("Detector", session.debugLine)
                }
            }
            Section {
                ProgressView(
                    value: Double(session.reelCount % max(session.checkpointInterval, 1)),
                    total: Double(session.checkpointInterval))
                if session.latestReport != nil {
                    Button("Open latest report") { session.openReport() }
                }
            }
            Section("Recent Reels") {
                if session.records.isEmpty {
                    Text("Nothing yet.").foregroundStyle(.secondary)
                }
                ForEach(session.records.reversed().prefix(40)) { rec in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Text(rec.observation.sensitiveSkipped
                                 ? "Text skipped (sensitive)"
                                 : (rec.observation.ocrText.first ?? "No text read"))
                                .lineLimit(1)
                            Spacer()
                            statusIcon(rec.status)
                        }
                        Text(String(format: "%.1fs  confidence %.2f",
                                    rec.observation.duration, rec.observation.confidence))
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
        }
        .navigationTitle("Session")
    }

    private func row(_ k: String, _ v: String) -> some View {
        HStack {
            Text(k)
            Spacer()
            Text(v).foregroundStyle(.secondary).monospacedDigit().multilineTextAlignment(.trailing)
        }
    }

    @ViewBuilder private func statusIcon(_ s: AnalysisStatus) -> some View {
        switch s {
        case .pending: ProgressView()
        case .analyzed: Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
        case .failed: Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
        }
    }
}
