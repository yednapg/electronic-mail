import Foundation
import SwiftUI
import UserNotifications
#if os(macOS)
import AppKit
#else
import UIKit
#endif

public struct MailNotificationTarget: Equatable, Sendable {
    public let userID: String
    public let accountID: String
    public let threadID: String
    public let messageID: String

    public init?(userInfo: [AnyHashable: Any]) {
        guard let userID = userInfo["user_id"] as? String,
              let accountID = userInfo["gmail_account_id"] as? String,
              let threadID = userInfo["thread_id"] as? String,
              let messageID = userInfo["message_id"] as? String,
              [userID, accountID, threadID, messageID].allSatisfy({ !$0.isEmpty && $0.count <= 256 }) else { return nil }
        self.userID = userID
        self.accountID = accountID
        self.threadID = threadID
        self.messageID = messageID
    }
}

@MainActor
public final class MailNotificationController: NSObject, ObservableObject, UNUserNotificationCenterDelegate {
    public static let shared = MailNotificationController(defaults: .standard)
    @Published public private(set) var enabled: Bool
    @Published public private(set) var soundEnabled: Bool
    @Published public private(set) var previewEnabled: Bool
    @Published public private(set) var authorization: UNAuthorizationStatus = .notDetermined
    @Published public private(set) var status = "Enable notifications for new email."
    @Published public private(set) var busy = false
    @Published public private(set) var pendingOpen: MailNotificationTarget?
    private let defaults: UserDefaults
    private var center: UNUserNotificationCenter { .current() }
    private let authorizationStatus: @MainActor () async -> UNAuthorizationStatus
    private let transport: @MainActor (URLRequest) async throws -> (Data, URLResponse)
    private let installationID: String
    private var baseURL: URL?
    private var sessionToken: String?
    private var deviceToken: String?
    private var operation: Task<Void, Never>?
    private var retryTask: Task<Void, Never>?

    init(
        defaults: UserDefaults,
        authorizationStatus: @escaping @MainActor () async -> UNAuthorizationStatus = {
            await UNUserNotificationCenter.current().notificationSettings().authorizationStatus
        },
        transport: @escaping @MainActor (URLRequest) async throws -> (Data, URLResponse) = {
            try await URLSession.shared.data(for: $0)
        }
    ) {
        self.defaults = defaults
        self.authorizationStatus = authorizationStatus
        self.transport = transport
        enabled = defaults.bool(forKey: "ElectronicMail.Notifications.Enabled")
        soundEnabled = defaults.object(forKey: "ElectronicMail.Notifications.Sound") as? Bool ?? true
        previewEnabled = defaults.bool(forKey: "ElectronicMail.Notifications.Previews")
        installationID = defaults.string(forKey: "ElectronicMail.Notifications.Installation") ?? UUID().uuidString
        super.init()
        defaults.set(installationID, forKey: "ElectronicMail.Notifications.Installation")
    }

    public func install() {
        center.delegate = self
    }

    public func configure(baseURL: URL, sessionToken: String?) {
        let previousToken = self.sessionToken
        let previousURL = self.baseURL
        self.baseURL = baseURL
        self.sessionToken = sessionToken
        if previousToken != nil && previousToken != sessionToken {
            center.removeAllDeliveredNotifications()
            center.removeAllPendingNotificationRequests()
            pendingOpen = nil
            if let previousURL, let previousToken {
                schedule { [self] in
                    _ = try? await request(baseURL: previousURL, token: previousToken, method: "DELETE", path: devicePath)
                }
            }
        }
        schedule { [self] in await refreshNow() }
    }

    public func refresh() async {
        schedule { [self] in await refreshNow() }
        await operation?.value
    }

    private func refreshNow() async {
        authorization = await authorizationStatus()
        guard sessionToken != nil else {
            status = "Sign in to receive notifications."
            return
        }
        guard enabled else {
            // A previous disable request may have failed while offline. Reconcile
            // the persisted preference with the server on every launch/refresh.
            await unregister()
            return
        }
        guard authorization == .authorized || authorization == .provisional else {
            status = "Allow notifications in System Settings."
            await unregister()
            return
        }
        if deviceToken == nil {
            status = "Connecting notifications…"
#if os(macOS)
            NSApplication.shared.registerForRemoteNotifications()
#else
            UIApplication.shared.registerForRemoteNotifications()
#endif
        } else {
            await synchronize()
        }
    }

    public func setEnabled(_ value: Bool) {
        enabled = value
        defaults.set(value, forKey: "ElectronicMail.Notifications.Enabled")
        schedule { [self] in
            busy = true
            defer { busy = false }
            guard enabled else {
                center.removeAllDeliveredNotifications()
                await unregister()
                return
            }
            do {
                guard let baseURL, let sessionToken else { status = "Sign in to receive notifications."; return }
                let data = try await request(baseURL: baseURL, token: sessionToken, method: "GET", path: "/v1/push/status")
                let available = (try JSONSerialization.jsonObject(with: data) as? [String: Bool])?["available"] == true
                guard available else {
                    status = "Push notifications need to be enabled on the mail server."
                    return
                }
                if await authorizationStatus() == .notDetermined {
                    _ = try await center.requestAuthorization(options: [.alert, .sound])
                }
                await refreshNow()
            } catch {
                status = "Could not enable notifications. Check your connection and try again."
            }
        }
    }

    public func setSoundEnabled(_ value: Bool) {
        soundEnabled = value
        defaults.set(value, forKey: "ElectronicMail.Notifications.Sound")
        schedule { [self] in await synchronize() }
    }

    public func setPreviewEnabled(_ value: Bool) {
        previewEnabled = value
        defaults.set(value, forKey: "ElectronicMail.Notifications.Previews")
        schedule { [self] in await synchronize() }
    }

    public func didRegister(deviceToken: Data) {
        self.deviceToken = deviceToken.map { String(format: "%02x", $0) }.joined()
        schedule { [self] in await synchronize() }
    }

    public func registrationFailed() {
        status = "Notifications are unavailable in this build. Install a version with notification support."
    }

    public func consume(_ target: MailNotificationTarget) {
        if pendingOpen == target { pendingOpen = nil }
    }

    private var devicePath: String { "/v1/push/devices/\(installationID)" }

    private func schedule(_ action: @escaping @MainActor () async -> Void) {
        let previous = operation
        operation = Task {
            await previous?.value
            await action()
        }
    }

    private func unregister() async {
        guard let baseURL, let sessionToken else { return }
        do {
            _ = try await request(baseURL: baseURL, token: sessionToken, method: "DELETE", path: devicePath)
            retryTask?.cancel()
            retryTask = nil
            status = enabled ? "Allow notifications in System Settings." : "Notifications are off."
        } catch {
            status = "Could not update notifications on the server. Will retry when connected."
            retry()
        }
    }

    private func synchronize() async {
        guard enabled, authorization == .authorized || authorization == .provisional else {
            await unregister()
            return
        }
        guard let baseURL, let sessionToken, let deviceToken else { return }
#if os(macOS)
        let platform = "macos"
#else
        let platform = "ios"
#endif
        // The plist and signing entitlement use the same build setting.
        guard let environment = Bundle.main.object(forInfoDictionaryKey: "APNSEnvironment") as? String,
              ["development", "production"].contains(environment) else {
            registrationFailed()
            return
        }
        let body: [String: Any] = [
            "token": deviceToken, "platform": platform,
            "environment": environment == "development" ? "sandbox" : "production",
            "enabled": true, "sound_enabled": soundEnabled, "preview_enabled": previewEnabled,
        ]
        do {
            _ = try await request(baseURL: baseURL, token: sessionToken, method: "PUT", path: devicePath, body: body)
            retryTask?.cancel()
            retryTask = nil
            status = "Notifications are on for new email in your connected accounts."
        } catch {
            status = "Could not connect notifications. Check the mail server and try again."
            retry()
        }
    }

    private func retry() {
        retryTask?.cancel()
        retryTask = Task { [weak self] in
            do { try await Task.sleep(for: .seconds(30)) } catch { return }
            guard let self, sessionToken != nil else { return }
            schedule { [self] in
                if self.enabled { await self.refreshNow() } else { await self.unregister() }
            }
        }
    }

    private func request(baseURL: URL, token: String, method: String, path: String, body: [String: Any]? = nil) async throws -> Data {
        var request = URLRequest(url: baseURL.appendingPathComponent(path))
        request.httpMethod = method
        request.timeoutInterval = 15
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        if let body {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let (data, response) = try await transport(request)
        guard let response = response as? HTTPURLResponse, (200..<300).contains(response.statusCode) else {
            throw URLError(.badServerResponse)
        }
        return data
    }

    nonisolated public func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse, withCompletionHandler completionHandler: @escaping () -> Void) {
        guard response.actionIdentifier == UNNotificationDefaultActionIdentifier,
              let target = MailNotificationTarget(userInfo: response.notification.request.content.userInfo) else {
            completionHandler()
            return
        }
        Task { @MainActor in
            pendingOpen = target
#if os(macOS)
            NSApplication.shared.activate(ignoringOtherApps: true)
            NSApplication.shared.windows.first(where: { $0.canBecomeMain })?.makeKeyAndOrderFront(nil)
#endif
            completionHandler()
        }
    }

    nonisolated public func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification, withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        Task { @MainActor in
#if os(macOS)
            let active = NSApplication.shared.isActive
#else
            let active = UIApplication.shared.applicationState == .active
#endif
            // The active app already shows the mailbox change. A running Mac
            // app in the background should still present new-mail alerts.
            guard enabled, !active else { completionHandler([]); return }
            var options: UNNotificationPresentationOptions = [.banner, .list]
            if soundEnabled { options.insert(.sound) }
            completionHandler(options)
        }
    }
}

public struct MailNotificationSettingsView: View {
    @ObservedObject private var notifications = MailNotificationController.shared

    public init() {}

    public var body: some View {
        Form {
            Section("New email") {
                Toggle("Notifications", isOn: Binding(get: { notifications.enabled }, set: notifications.setEnabled))
                    .disabled(notifications.busy)
                    .accessibilityIdentifier("notifications.enabled")
                Toggle("Play sound", isOn: Binding(get: { notifications.soundEnabled }, set: notifications.setSoundEnabled))
                    .disabled(!notifications.enabled)
                Toggle("Show sender and message preview", isOn: Binding(get: { notifications.previewEnabled }, set: notifications.setPreviewEnabled))
                    .disabled(!notifications.enabled)
                Text(notifications.status)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                Text("Alerts appear for new, unread inbox mail. Imports and changes to existing messages do not send alerts.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                if notifications.enabled {
                    Button("Retry connection") { notifications.setEnabled(true) }
                        .disabled(notifications.busy)
                }
                Button("Open notification settings") {
#if os(macOS)
                    if let url = URL(string: "x-apple.systempreferences:com.apple.Notifications-Settings.extension") { NSWorkspace.shared.open(url) }
#else
                    if let url = URL(string: UIApplication.openNotificationSettingsURLString) { UIApplication.shared.open(url) }
#endif
                }
            }
        }
        .formStyle(.grouped)
        .navigationTitle("Notifications")
        .task { await notifications.refresh() }
    }
}
