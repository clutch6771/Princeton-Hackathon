import SwiftUI

/// In-app vertical Reels feed. Swipe up for the next Reel.
struct ReelFeedView: View {
    @Environment(SessionManager.self) private var session
    @State private var position: Int? = 0
    @State private var shownAt = Date()

    private let count = 120

    var body: some View {
        ScrollView(.vertical) {
            LazyVStack(spacing: 0) {
                ForEach(0..<count, id: \.self) { i in
                    ReelCardView(reel: SampleReel.make(i))
                        .containerRelativeFrame([.horizontal, .vertical])
                        .id(i)
                }
            }
            .scrollTargetLayout()
        }
        .scrollTargetBehavior(.paging)
        .scrollPosition(id: $position)
        .scrollIndicators(.hidden)
        .ignoresSafeArea()
        .overlay(alignment: .top) { statusPill }
        .onAppear { shownAt = Date() }
        .onChange(of: position) { old, _ in
            if let old, session.isMonitoring, session.mode == .feedDirect {
                session.feedReelFinished(SampleReel.make(old), startedAt: shownAt)
            }
            shownAt = Date()
        }
    }

    private var statusPill: some View {
        HStack(spacing: 6) {
            Circle().fill(session.isMonitoring ? Color.red : Color.gray).frame(width: 8, height: 8)
            Text(session.isMonitoring ? "Monitoring  \(session.reelCount)" : "Not monitoring")
                .font(.caption.bold())
        }
        .padding(.horizontal, 10).padding(.vertical, 6)
        .background(.ultraThinMaterial, in: Capsule())
        .padding(.top, 54)
    }
}

struct ReelCardView: View {
    let reel: SampleReel

    var body: some View {
        TimelineView(.animation) { ctx in
            let t = ctx.date.timeIntervalSinceReferenceDate
            // Background colors flip every 3.5s to mimic a scene cut inside a video.
            let flip = Int(t / 3.5) % 2 == 1
            let h = (reel.hue + (flip ? 0.45 : 0)).truncatingRemainder(dividingBy: 1)
            let h2 = (h + 0.12).truncatingRemainder(dividingBy: 1)

            ZStack {
                LinearGradient(
                    colors: [Color(hue: h, saturation: 0.8, brightness: 0.85),
                             Color(hue: h2, saturation: 0.9, brightness: 0.35)],
                    startPoint: .top, endPoint: .bottom)

                Circle()
                    .fill(.white.opacity(0.18))
                    .frame(width: 180, height: 180)
                    .offset(x: CGFloat(sin(t * 1.5)) * 90, y: CGFloat(cos(t * 1.1)) * 60)

                VStack(spacing: 12) {
                    Text(reel.caption)
                        .font(.system(size: 34, weight: .heavy))
                        .multilineTextAlignment(.center)
                        .foregroundStyle(.white)
                        .shadow(radius: 4)
                    Text(reel.tag)
                        .font(.system(size: 24, weight: .semibold))
                        .foregroundStyle(.white.opacity(0.9))
                }
                .padding(.horizontal, 48)

                VStack(spacing: 22) {
                    Image(systemName: "heart")
                    Image(systemName: "bubble.right")
                    Image(systemName: "paperplane")
                    Image(systemName: "ellipsis")
                }
                .font(.title)
                .foregroundStyle(.white)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomTrailing)
                .padding(.trailing, 16)
                .padding(.bottom, 130)
            }
        }
        .clipped()
    }
}
