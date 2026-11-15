import SwiftUI

struct CheckpointView: View {
    @Environment(SessionManager.self) private var session
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        List {
            if let r = session.latestReport {
                Section {
                    Text(r.headline).font(IrisUI.font(.headline))
                    Text(r.createdAt.formatted(date: .abbreviated, time: .shortened))
                        .font(IrisUI.font(.caption)).foregroundStyle(.secondary)
                }
                Section("Numbers") {
                    LabeledContent("Reels", value: "\(r.reelCount)")
                    LabeledContent("Analyzed", value: "\(r.analyzedCount)")
                    LabeledContent("Flagged", value: "\(r.flaggedCount)")
                }
                Section("Possible mismatches with your goals") {
                    if r.mismatches.isEmpty {
                        Text("None found in this batch.").foregroundStyle(.secondary)
                    } else {
                        ForEach(r.mismatches, id: \.self) { Text($0) }
                    }
                }
                Section {
                    Text("This report describes what was observed. It does not change or block anything on Instagram.")
                        .font(IrisUI.font(.footnote)).foregroundStyle(.secondary)
                }
            } else {
                Text("No checkpoint yet.").foregroundStyle(.secondary)
            }
        }
        .navigationTitle("Checkpoint")
        .toolbar {
            ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
        }
    }
}
