import SwiftUI

public struct RootView: View {
    @ObservedObject var store: DashboardStore
    @State private var showingSettings = false

    public init(store: DashboardStore) {
        self.store = store
    }

    public var body: some View {
        NavigationStack {
            content
                .toolbar(.hidden, for: .navigationBar)
                .onLongPressGesture(minimumDuration: 1.0) {
                    showingSettings = true
                }
                .sheet(isPresented: $showingSettings) {
                    SettingsView(store: store)
                }
                .navigationDestination(for: String.self) { entityID in
                    TraceDetailView(entityID: entityID, store: store)
                }
        }
        .task {
            if store.dashboard == nil {
                await store.refresh()
            }
        }
    }

    @ViewBuilder
    private var content: some View {
        switch store.loadState {
        case .idle where store.dashboard == nil,
             .loading where store.dashboard == nil:
            LoadingView()
        case .failed(let message) where store.dashboard == nil:
            ErrorStateView(message: message) {
                Task { await store.refresh() }
            } settings: {
                showingSettings = true
            }
        default:
            if let dashboard = store.dashboard, dashboard.auth.connected {
                DashboardView(dashboard: dashboard, store: store)
            } else {
                SignedOutView(store: store)
            }
        }
    }
}

private struct LoadingView: View {
    var body: some View {
        VStack(spacing: 14) {
            ProgressView()
            Text("Loading dashboard")
                .font(DigestPalette.rounded(size: 16))
                .foregroundStyle(DigestPalette.muted)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color.white)
    }
}

private struct ErrorStateView: View {
    let message: String
    let retry: () -> Void
    let settings: () -> Void

    var body: some View {
        ContentUnavailableView {
            Label("Could not load dashboard", systemImage: "exclamationmark.triangle")
                .font(DigestPalette.rounded(size: 20, weight: .bold))
                .foregroundStyle(DigestPalette.text)
        } description: {
            Text(message)
                .font(DigestPalette.rounded(size: 16))
                .foregroundStyle(DigestPalette.muted)
        } actions: {
            HStack(spacing: 12) {
                Button("Retry", action: retry)
                    .buttonStyle(.borderedProminent)

                Button("Backend Settings", action: settings)
                    .buttonStyle(.bordered)
            }
            .font(DigestPalette.rounded(size: 15, weight: .semibold))
        }
    }
}
