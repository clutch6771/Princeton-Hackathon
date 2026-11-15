import SwiftUI

struct ChatView: View {
    @Environment(SessionManager.self) private var session
    @State private var draft = ""
    @State private var breathe = false
    var body: some View {
        VStack(spacing: 0) {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 18) {
                        if session.showGuardianGreeting {
                            VStack(alignment: .leading, spacing: 14) {
                                Text("WELCOME").font(IrisUI.font(.caption)).tracking(2).foregroundStyle(Vigil.cyan)
                                Text("Hi, I'm your Guardian").font(IrisUI.font(.title2))
                                Text("Say hello to get started. Tell me how you're feeling or what you'd like from your feed today. I'm here to help, never to judge.")
                                    .font(IrisUI.font(.subheadline)).foregroundStyle(.secondary)
                            }.frame(maxWidth: .infinity, alignment: .leading).vigilCard()
                        } else if let digest = session.guardianSessionContext {
                            VStack(alignment: .leading, spacing: 14) {
                                Text("GOAL VS. REALITY").font(.caption.weight(.semibold)).tracking(2).foregroundStyle(Vigil.coral)
                                Text("\(digest.flaggedReels.count) / \(digest.totalReelsWatched) Divisive").font(.title2.weight(.semibold)).fontDesign(.rounded).monospacedDigit()
                                Text("Your goal: \(session.userGoal)").font(IrisUI.font(.subheadline))
                                Text(digest.userGoalConflict ?? "No preference conflict has been reported yet.")
                                    .font(IrisUI.font(.subheadline)).foregroundStyle(.secondary)
                                Text("Latest session").font(IrisUI.font(.caption)).foregroundStyle(.secondary)

                            }.vigilCard()
                        }
                        ForEach(session.messages) { message in
                            HStack {
                                if message.isUser { Spacer(minLength: 40) }
                                Text(message.text).font(IrisUI.font(.body)).padding(16)
                                    .background(message.isUser ? Vigil.indigo : Color.clear, in: UnevenRoundedRectangle(topLeadingRadius: 20, bottomLeadingRadius: message.isUser ? 20 : 5, bottomTrailingRadius: message.isUser ? 5 : 20, topTrailingRadius: 20))
                                    .overlay(RoundedRectangle(cornerRadius: 20).stroke(message.isUser ? Vigil.cyan.opacity(0.25) : Color.clear))
                                if !message.isUser { Spacer(minLength: 40) }
                            }.id(message.id)
                        }
                        if session.isGuardianThinking { GuardianThinkingView() }
                        Color.clear.frame(height: 1).id("end")
                    }.padding(20)
                }
                .scrollDismissesKeyboard(.interactively)
                .scrollBounceBehavior(.always)
                .onChange(of: session.isGuardianThinking) { _, thinking in
                    if thinking { withAnimation(Vigil.spring) { proxy.scrollTo("end", anchor: .bottom) } }
                }.onChange(of: session.messages.count) { _, _ in withAnimation(Vigil.spring) { proxy.scrollTo("end", anchor: .bottom) } }
            }
            HStack(alignment: .bottom, spacing: 12) {
                TextField("Message", text: $draft, axis: .vertical).lineLimit(1...4).padding(14).background(Vigil.card, in: RoundedRectangle(cornerRadius: 22))
                Button {
                    let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
                    guard !text.isEmpty, !session.isGuardianThinking else { return }; session.send(text); draft = ""
                } label: { Image(systemName: "arrow.up.circle.fill").font(.system(size: 38)).foregroundStyle(Vigil.cyan) }.accessibilityLabel("Send message").disabled(session.isGuardianThinking || draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }.padding(.horizontal, 20).padding(.bottom, 12)
        }.navigationTitle("Talk with Guardian")
        .sheet(isPresented: $breathe) { BreathingResetView() }
        .onChange(of: session.showReset) { _, show in if show { breathe = true; session.showReset = false } }
        .onAppear { if session.showReset { breathe = true; session.showReset = false } }
    }

}
struct BreathingResetView: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var deadline = Date().addingTimeInterval(60)
    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            let remaining = max(0, Int(ceil(deadline.timeIntervalSince(context.date))))
            let inhale = remaining % 8 >= 4
            VStack(spacing: 32) {
                Text(remaining == 0 ? "A fresh start" : "Make a little space").font(IrisUI.font(.title2).weight(.semibold))
                ZStack {
                    Circle().fill(Vigil.indigo).frame(width: 220, height: 220).scaleEffect(inhale && !reduceMotion ? 1.1 : 0.9).animation(.easeInOut(duration: 4), value: inhale)
                    Circle().stroke(Vigil.cyan.opacity(0.6), lineWidth: 2).frame(width: 220, height: 220)
                    VStack { Text(remaining == 0 ? "Ready" : inhale ? "Breathe in" : "Breathe out").font(IrisUI.font(.title3)); Text("\(remaining)s").monospacedDigit().foregroundStyle(.secondary) }
                }
                Text("Choose what deserves your attention next.").font(IrisUI.font(.subheadline)).foregroundStyle(.secondary)
                Button(remaining == 0 ? "Done" : "End reset") { dismiss() }.buttonStyle(.bordered)
            }.frame(maxWidth: .infinity, maxHeight: .infinity).background(Vigil.canvas).tint(Vigil.cyan)
        }.preferredColorScheme(.dark)
    }
}

struct GuardianThinkingView: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var pulse = false
    var body: some View {
        HStack(spacing: 10) {
            HStack(spacing: 5) {
                ForEach(0..<3) { index in
                    Circle().fill(Vigil.cyan.opacity(0.75)).frame(width: 6, height: 6)
                        .opacity(reduceMotion || pulse ? 1 : 0.3)
                        .scaleEffect(reduceMotion || pulse ? 1 : 0.7)
                        .animation(reduceMotion ? nil : .easeInOut(duration: 0.65).repeatForever(autoreverses: true).delay(Double(index) * 0.15), value: pulse)
                }
            }
            Text("Thinking…").font(.subheadline).foregroundStyle(.secondary)
        }.padding(.horizontal, 16).padding(.vertical, 10)
            .accessibilityElement(children: .ignore).accessibilityLabel("Guardian is preparing a response")
            .onAppear { pulse = true }
    }
}
