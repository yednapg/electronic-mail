import ElectronicMailShared
import SwiftUI
import UIKit

@main
struct ElectronicMailiOSApp: App {
    @UIApplicationDelegateAdaptor(ElectronicMailIOSApplicationDelegate.self) private var appDelegate
    @StateObject private var inboxStore: InboxStore
    @StateObject private var aiStore: AIInboxStore
    @StateObject private var accountStore: GmailAccountSettingsStore

    private let tokenStore = KeychainSessionTokenStore()
    private let authService = IOSGoogleOAuthService()

    init() {
        let localStore: LocalMailStore = SQLiteLocalMailStore() ?? NoopLocalMailStore()
        let backend: AppClient
#if DEBUG
        if ElectronicMailiOSConfiguration.isDemoMode {
            backend = DemoAppClient(baseURL: ElectronicMailiOSConfiguration.backendURL)
        } else {
            backend = LiveBackendAppClient(baseURL: ElectronicMailiOSConfiguration.backendURL)
        }
#else
        backend = LiveBackendAppClient(baseURL: ElectronicMailiOSConfiguration.backendURL)
#endif
        let client = OfflineFirstAppClient(backend: backend, localMailStore: localStore)
        _inboxStore = StateObject(wrappedValue: InboxStore(
            client: client,
            localMailStore: localStore,
            automaticallyPrefetchThreads: true
        ))
        _aiStore = StateObject(wrappedValue: AIInboxStore(client: client))
        _accountStore = StateObject(wrappedValue: GmailAccountSettingsStore(client: client))
    }

    var body: some Scene {
        WindowGroup {
            ElectronicMailiOSRootView(
                inboxStore: inboxStore,
                aiStore: aiStore,
                accountStore: accountStore,
                tokenStore: tokenStore,
                authService: authService,
                demoMode: ElectronicMailiOSConfiguration.isDemoMode
            )
            .task {
                if !ElectronicMailiOSConfiguration.isDemoMode { inboxStore.connectNotifications() }
            }
        }
    }
}

private final class ElectronicMailIOSApplicationDelegate: NSObject, UIApplicationDelegate {
    func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        MailNotificationController.shared.install()
        return true
    }

    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        MailNotificationController.shared.didRegister(deviceToken: deviceToken)
    }

    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: Error) {
        MailNotificationController.shared.registrationFailed()
    }
}

enum ElectronicMailiOSConfiguration {
    static var isDemoMode: Bool {
        ProcessInfo.processInfo.arguments.contains("-ElectronicMailDemo")
    }

    static var backendURL: URL {
        if let launchValue = ProcessInfo.processInfo.environment["ELECTRONIC_MAIL_IOS_BACKEND_URL"],
           let launchURL = validatedURL(launchValue) {
            return launchURL
        }
        if let value = Bundle.main.object(forInfoDictionaryKey: "BackendBaseURL") as? String,
           let url = validatedURL(value) {
            return url
        }
#if targetEnvironment(simulator) && DEBUG
        return URL(string: "http://localhost:3001")!
#else
        return URL(string: "https://electronic-mail-backend.invalid")!
#endif
    }

    static var backendWarning: String? {
        guard backendURL.host == "electronic-mail-backend.invalid" else { return nil }
        return "Supply ELECTRONIC_MAIL_IOS_BACKEND_URL with a LAN or HTTPS backend for a physical-device build."
    }

    private static func validatedURL(_ value: String) -> URL? {
        guard let url = URL(string: value.trimmingCharacters(in: .whitespacesAndNewlines)),
              ["https", "http"].contains(url.scheme?.lowercased()),
              url.host?.isEmpty == false else { return nil }
        return url
    }
}
