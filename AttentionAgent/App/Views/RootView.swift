import SwiftUI

struct RootView: View {
    @Environment(SessionManager.self) private var session

    var body: some View {
        @Bindable var session = session
        TabView(selection: $session.selectedTab) {
            NavigationStack { HomeView() }
                .tabItem { Label("Home", systemImage: "house") }
                .tag(AppTab.home)
            ReelFeedView()
                .tabItem { Label("Reels", systemImage: "play.rectangle") }
                .tag(AppTab.feed)
            NavigationStack { MonitoringView() }
                .tabItem { Label("Session", systemImage: "waveform.path.ecg") }
                .tag(AppTab.session)
            NavigationStack { ChatView() }
                .tabItem { Label("Coach", systemImage: "bubble.left.and.bubble.right") }
                .tag(AppTab.coach)
            NavigationStack { SettingsView() }
                .tabItem { Label("Settings", systemImage: "gearshape") }
                .tag(AppTab.settings)
        }
        .sheet(isPresented: $session.showReport) {
            NavigationStack { CheckpointView() }
        }
    }
}
