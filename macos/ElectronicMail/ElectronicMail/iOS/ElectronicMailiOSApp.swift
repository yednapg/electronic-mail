import ElectronicMailShared
import SwiftUI

@main
struct ElectronicMailiOSApp: App {
    @StateObject private var store = DashboardStore(
        client: LiveBackendAppClient(baseURL: ElectronicMailiOSConfiguration.backendURL)
    )

    private let tokenStore = KeychainSessionTokenStore()
    private let authService = IOSGoogleOAuthService()

    var body: some Scene {
        WindowGroup {
            ElectronicMailiOSRootView(
                store: store,
                tokenStore: tokenStore,
                authService: authService
            )
        }
    }
}

enum ElectronicMailiOSConfiguration {
    static var backendURL: URL {
        if let value = Bundle.main.object(forInfoDictionaryKey: "BackendBaseURL") as? String,
           let url = URL(string: value.trimmingCharacters(in: .whitespacesAndNewlines)),
           ["https", "http"].contains(url.scheme?.lowercased()),
           url.host?.isEmpty == false {
            return url
        }

        return URL(string: "https://electronic-mail-backend.invalid")!
    }

    static var backendWarning: String? {
        guard backendURL.host == "electronic-mail-backend.invalid" else {
            return nil
        }
        return "Set ELECTRONIC_MAIL_IOS_BACKEND_URL to your hosted backend before testing on iPhone."
    }
}
