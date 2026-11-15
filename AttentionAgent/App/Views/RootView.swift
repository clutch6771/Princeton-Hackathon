import SwiftUI

// UI-only styling and archive; capture and backend ownership stay in SessionManager.
enum Vigil {
    // Colours come from the theme in use, so switching themes recolours every screen.
    private static var theme: IrisTheme { IrisThemeStore.shared.theme }
    static var canvas: Color { theme.canvas }
    static var indigo: Color { theme.surface }
    static var card: Color { theme.card }
    static var cyan: Color { theme.accent }
    static var coral: Color { theme.flag }
    static var background: LinearGradient { theme.background }
    static var accent: LinearGradient { theme.startButton }
    static var monitoringButton: LinearGradient { theme.stopButton }
    static let spring = Animation.spring(response: 0.45, dampingFraction: 0.8)
    @ViewBuilder static var backdrop: some View {
        if IrisUI.liveBackdrop { IrisBackdrop(theme: theme).ignoresSafeArea() } else { Rectangle().fill(background).ignoresSafeArea() }
    }
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
    @State private var launching = true
    init() { IrisUI.install() }
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
                }.frame(maxWidth: .infinity, maxHeight: .infinity).background { Vigil.backdrop }
            }.id(session.selectedTab)
            HStack {
                tab(.home, "house", "Home")
                tab(.coach, "bubble.left.and.bubble.right", "Guardian")
                tab(.session, "chart.bar", "Statistics")
                tab(.settings, "gearshape", "Settings")
            }.padding(.vertical, 18).background(Vigil.background)
        }.background(Vigil.background).font(IrisUI.font(.body)).tint(Vigil.cyan).preferredColorScheme(.dark)
        .scrollDismissesKeyboard(.interactively)
        .overlay { if launching { IrisSplash().transition(.opacity) } }
        .task {
            try? await Task.sleep(for: .seconds(2.4))
            withAnimation(.easeOut(duration: 0.5)) { launching = false }
        }
        .onChange(of: phase) { _, value in
            if value == .active { session.refreshBroadcastState() }
            if value == .background { wasBackground = true; session.saveConversationMemory() }
            if value == .active && wasBackground { session.beginFreshConversation(); wasBackground = false }
        }
        .onChange(of: session.activeDigest) { _, digest in
            guard let digest, digest.totalReelsWatched > 0 else { return }
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
