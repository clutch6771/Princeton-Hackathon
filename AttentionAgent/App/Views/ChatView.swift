import SwiftUI

struct ChatView: View {
    @Environment(SessionManager.self) private var session
    @State private var draft = ""
    @State private var showPrefs = false
    @State private var goalDraft = ""

    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                LazyVStack(spacing: 8) {
                    DisclosureGroup("Agent 1 → Agent 2 Memory Sync") {
                        Text("Your goal: \(session.userGoal)")
                        if let digest = session.activeDigest {
                            ScrollView(.horizontal) {
                                HStack {
                                    ForEach(digest.flaggedReels) { event in
                                        VStack {
                                        Text("\(event.topic) • \(event.emotionTag) • \(event.dwellSeconds)s")
                                            .font(.caption).padding(8).background(.orange.opacity(0.12), in: Capsule())
                                        if let quote = event.transcriptText { Text("🎙️ \(quote)").font(.caption).lineLimit(3) }
                                        Button("Not divisive") { session.correct(event, correction: "not_divisive") }
                                        Button("Intentional watch") { session.correct(event, correction: "intentional_watch") }
                                        }
                                    }
                                }
                            }
                        } else { Text("Waiting for a checkpoint").font(.caption) }
                    }.padding().background(.thinMaterial, in: RoundedRectangle(cornerRadius: 14))
                    if let digest = session.activeDigest {
                        VStack(alignment: .leading, spacing: 10) {
                            Text("\(digest.flaggedReels.count) / \(digest.totalReelsWatched) Divisive").font(.title2.bold())
                            Text(digest.userGoalConflict ?? "No conflict detected")
                            Text("Recommended Action").font(.headline)
                            Text(digest.suggestedAction).padding().background(.mint.opacity(0.15), in: RoundedRectangle(cornerRadius: 10))
                        }.frame(maxWidth: .infinity, alignment: .leading).padding()
                            .background(.orange.opacity(0.08), in: RoundedRectangle(cornerRadius: 14))
                    }
                    ForEach(session.messages) { m in
                        HStack {
                            if m.isUser { Spacer(minLength: 40) }
                            Text(m.text)
                                .padding(10)
                                .background(m.isUser ? Color.accentColor : Color(.secondarySystemBackground))
                                .foregroundStyle(m.isUser ? .white : .primary)
                                .clipShape(RoundedRectangle(cornerRadius: 14))
                            if !m.isUser { Spacer(minLength: 40) }
                        }
                    }
                }
                .padding()
            }

            if let p = session.proposal {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Save this preference?").font(.caption).foregroundStyle(.secondary)
                    Text(p.text)
                    HStack {
                        Button("Reject", role: .destructive) { session.resolveProposal(accept: false) }
                        Spacer()
                        Button("Confirm") { session.resolveProposal(accept: true) }
                            .buttonStyle(.borderedProminent)
                    }
                }
                .padding()
                .background(.thinMaterial)
            }

            ScrollView(.horizontal, showsIndicators: false) {
                HStack {
                    Button("Give me a 30-sec feed reset") { session.send("Give me a 30-sec feed reset") }
                    Button("Update my feed goals") { showPrefs = true }
                    Button("Switch alerts to every 30 reels") {
                        session.setInterval(30)
                        session.messages.append(ChatMessage(isUser: false, text: "Alerts now arrive every 30 reels."))
                    }
                }.buttonStyle(.bordered).padding(.horizontal)
            }
            HStack {
                TextField("Message", text: $draft).textFieldStyle(.roundedBorder)
                Button {
                    let t = draft.trimmingCharacters(in: .whitespaces)
                    guard !t.isEmpty else { return }
                    session.send(t)
                    draft = ""
                } label: {
                    Image(systemName: "arrow.up.circle.fill").font(.title2)
                }
            }
            .padding()
        }
        .alert("Quick Feed Reset", isPresented: Binding(get: { session.showReset }, set: { session.showReset = $0 })) {
            Button("Done", role: .cancel) { }
        } message: { Text(session.activeDigest?.suggestedAction ?? "Pause and choose your next topic.") }
        .navigationTitle("Coach")
        .toolbar { Button("Preferences") { showPrefs = true } }
        .sheet(isPresented: $showPrefs) {
            NavigationStack {
                Form {
                    TextField("Your feed goal", text: $goalDraft, axis: .vertical)
                    Button("Save goal") { session.saveGoal(goalDraft); showPrefs = false }
                    Text("Current goal: \(session.userGoal)")
                    if session.savedPreferences.isEmpty {
                        Text("Nothing saved yet.").foregroundStyle(.secondary)
                    }
                    ForEach(session.savedPreferences, id: \.self) { Text($0) }
                }
                .navigationTitle("Feed goals")
                .onAppear { goalDraft = session.userGoal }
            }
        }
    }
}
