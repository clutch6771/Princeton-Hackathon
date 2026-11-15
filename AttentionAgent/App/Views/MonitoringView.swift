import SwiftUI
import Charts

struct MonitoringView: View {
    @Environment(SessionManager.self) private var session
    private var recent: [SessionInsight] { Array(session.sessionInsights.prefix(7).reversed()) }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                if recent.isEmpty { ContentUnavailableView("Your sessions will appear here", systemImage: "chart.bar", description: Text("Start monitoring to record your first session.")) }
                VStack(alignment: .leading, spacing: 20) {
                    Text("Flagged per session").font(.headline)
                    Chart(Array(recent.enumerated()), id: \.element.id) { index, item in
                        BarMark(x: .value("Session", "\(index + 1)"), y: .value("Flagged", Double(item.flagged) / Double(max(1, item.total))))
                            .foregroundStyle(Vigil.coral).cornerRadius(5)
                    }.chartYScale(domain: 0...1).chartYAxis { AxisMarks { value in AxisGridLine(); AxisValueLabel { if let number = value.as(Double.self) { Text(number, format: .percent.precision(.fractionLength(0))) } } } }
                        .chartXAxisLabel("Last seven sessions · oldest to newest").frame(height: 220)
                }.vigilCard()
                VStack(alignment: .leading, spacing: 20) {
                    Text("Reels per session").font(.headline)
                    Chart(Array(recent.enumerated()), id: \.element.id) { index, item in
                        BarMark(x: .value("Session", "\(index + 1)"), y: .value("Reels", item.flagged)).foregroundStyle(by: .value("Type", "Flagged"))
                        BarMark(x: .value("Session", "\(index + 1)"), y: .value("Reels", max(0, item.total - item.flagged))).foregroundStyle(by: .value("Type", "Neutral"))
                    }.chartForegroundStyleScale(["Flagged": Vigil.coral, "Neutral": Color.gray.opacity(0.5)])
                        .chartXAxisLabel("Last seven sessions · oldest to newest").frame(height: 220)
                }.vigilCard()
                VStack(alignment: .leading, spacing: 16) {
                    Text("Flagged reels · Latest session").font(.headline)
                    Text("Visual descriptions of reels flagged by the Judge.")
                        .font(.subheadline).foregroundStyle(.secondary)
                    if session.sessionFlaggedReels.isEmpty {
                        Text("No flagged reels in this session yet.")
                            .font(.subheadline).foregroundStyle(.secondary)
                    }
                    ForEach(session.sessionFlaggedReels) { event in
                        VStack(alignment: .leading, spacing: 8) {
                            HStack {
                                Text(event.timestamp.formatted(date: .abbreviated, time: .shortened))
                                Spacer()
                                Text("\(event.dwellSeconds)s").monospacedDigit()
                                Menu {
                                    Button("Not Divisive") { session.correct(event, correction: "not_divisive") }
                                    Button("Intentional Watch") { session.correct(event, correction: "intentional_watch") }
                                } label: { Image(systemName: "ellipsis").padding(8) }
                                    .accessibilityLabel("Reel feedback")
                            }.font(.caption).foregroundStyle(.secondary)
                            Text(event.topic.isEmpty ? "Visual description unavailable." : event.topic)
                                .font(.subheadline).fixedSize(horizontal: false, vertical: true)
                        }.padding(14).background(Vigil.canvas.opacity(0.5), in: RoundedRectangle(cornerRadius: 14))
                    }
                }.vigilCard()
            }.padding(20)
        }.navigationTitle("Statistics")
    }
}
