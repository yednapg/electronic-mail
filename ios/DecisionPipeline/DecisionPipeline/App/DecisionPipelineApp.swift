import DecisionPipelineCore
import SwiftUI

@main
struct DecisionPipelineApp: App {
    @StateObject private var store = DashboardStore(
        apiClient: DashboardAPIClient(baseURL: AppConfiguration.defaultBackendURL),
        oauthService: GoogleOAuthService()
    )

    var body: some Scene {
        WindowGroup {
            RootView(store: store)
        }
    }
}
