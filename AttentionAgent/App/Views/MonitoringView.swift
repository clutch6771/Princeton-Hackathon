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
            }.padding(20)
        }.navigationTitle("Statistics")
    }
}
