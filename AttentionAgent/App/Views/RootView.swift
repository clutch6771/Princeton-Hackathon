import SwiftUI

// UI-only styling and archive; capture and backend ownership stay in SessionManager.
enum Vigil {
    static let canvas = Color(red: 9/255, green: 13/255, blue: 38/255)
    static let indigo = Color(red: 20/255, green: 25/255, blue: 74/255)
    static let card = Color(red: 17/255, green: 22/255, blue: 59/255)
    static let cyan = Color(red: 0, green: 229/255, blue: 1)
    static let coral = Color(red: 0.65, green: 0.61, blue: 0.86)
    static let background = LinearGradient(colors: [canvas, Color(red: 0.10, green: 0.11, blue: 0.30), Color(red: 0.16, green: 0.10, blue: 0.32), Color(red: 0.03, green: 0.23, blue: 0.26)], startPoint: .topLeading, endPoint: .bottomTrailing)
    static let accent = LinearGradient(colors: [.blue, Color(red: 0.5, green: 0.35, blue: 0.95), cyan, Color(red: 0.3, green: 0.9, blue: 0.65)], startPoint: .topLeading, endPoint: .bottomTrailing)
    static let spring = Animation.spring(response: 0.45, dampingFraction: 0.8)
    static func tap() { UIImpactFeedbackGenerator(style: .light).impactOccurred() }
}
extension View {
    func vigilCard() -> some View {
        padding(20).background(Vigil.card.opacity(0.95), in: RoundedRectangle(cornerRadius: 24))
            .overlay(RoundedRectangle(cornerRadius: 24).stroke(.white.opacity(0.10), lineWidth: 1))
    }
}
struct RootView: View {
    @Environment(SessionManager.self) private var session
    @AppStorage("vigilDigestArchive", store: UserDefaults(suiteName: AppConfig.appGroupID)) private var archiveData = Data()
    @Environment(\.scenePhase) private var phase
    @State private var wasBackground = false
    var body: some View {
        VStack(spacing: 0) {
            NavigationStack {
                Group {
                    switch session.selectedTab {
                    case .coach: ChatView()
                    case .session: MonitoringView()
                    case .settings: SettingsView()
                    default: HomeView()
                    }
                }.frame(maxWidth: .infinity, maxHeight: .infinity).background(Vigil.background)
            }.id(session.selectedTab)
            HStack {
                tab(.home, "house", "Home")
                tab(.coach, "bubble.left.and.bubble.right", "Chat")
                tab(.session, "chart.bar", "Statistics")
                tab(.settings, "gearshape", "Settings")
            }.padding(.vertical, 18).background(Vigil.background)
        }.background(Vigil.background).tint(Vigil.cyan).preferredColorScheme(.dark)
        .scrollDismissesKeyboard(.interactively)
        .onChange(of: phase) { _, value in
            if value == .active { session.refreshBroadcastState() }
            if value == .background { wasBackground = true; session.saveConversationMemory() }
            if value == .active && wasBackground { session.beginFreshConversation(); wasBackground = false }
        }
        .onChange(of: session.activeDigest) { _, digest in
            guard let digest else { return }
            let existing = (try? JSONDecoder().decode([CheckpointDigest].self, from: archiveData)) ?? []
            guard !existing.contains(where: { $0.id == digest.id }) else { return }
            archiveData = (try? JSONEncoder().encode(Array(([digest] + existing).prefix(100)))) ?? archiveData
        }
    }
    private func tab(_ tab: AppTab, _ icon: String, _ label: String) -> some View {
        Button { Vigil.tap(); session.selectedTab = tab } label: {
            Image(systemName: icon).font(.system(size: 24, weight: .medium)).frame(maxWidth: .infinity).frame(height: 44)
                .foregroundStyle(session.selectedTab == tab ? Vigil.cyan : .white.opacity(0.4))
        }.accessibilityLabel(label).accessibilityAddTraits(session.selectedTab == tab ? .isSelected : [])
    }
}
