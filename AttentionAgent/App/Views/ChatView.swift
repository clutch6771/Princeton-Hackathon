import SwiftUI

struct ChatView: View {
    @Environment(SessionManager.self) private var session
    @State private var draft = ""
    @State private var showPrefs = false

    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                LazyVStack(spacing: 8) {
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
        .navigationTitle("Coach")
        .toolbar { Button("Preferences") { showPrefs = true } }
        .sheet(isPresented: $showPrefs) {
            NavigationStack {
                List {
                    if session.savedPreferences.isEmpty {
                        Text("Nothing saved yet.").foregroundStyle(.secondary)
                    }
                    ForEach(session.savedPreferences, id: \.self) { Text($0) }
                }
                .navigationTitle("Saved preferences")
            }
        }
    }
}
