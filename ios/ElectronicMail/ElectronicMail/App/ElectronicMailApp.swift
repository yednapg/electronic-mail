import ElectronicMailCore
import SwiftUI

@main
struct ElectronicMailApp: App {
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
