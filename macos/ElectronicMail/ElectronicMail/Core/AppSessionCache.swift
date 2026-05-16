import Foundation

public final class AppSessionCache {
    private let defaults: UserDefaults
    private let storagePrefix: String
    private let currentUserKey: String

    private var memorySession: AppSessionResponse?

    public init(defaults: UserDefaults = .standard, namespace: String = "live") {
        self.defaults = defaults
        self.storagePrefix = "electronic-mail-app-session:\(namespace):v1:"
        self.currentUserKey = "electronic-mail-current-user:\(namespace):v1"
    }

    func read() -> AppSessionResponse? {
        if let memorySession {
            return memorySession
        }
        guard let userID = defaults.string(forKey: currentUserKey) else {
            return nil
        }
        return read(userID: userID)
    }

    func read(userID: String) -> AppSessionResponse? {
        guard let data = defaults.data(forKey: storagePrefix + userID) else {
            return nil
        }
        do {
            let session = try JSONDecoder.backend.decode(AppSessionResponse.self, from: data)
            memorySession = session
            return session
        } catch {
            return nil
        }
    }

    func write(_ session: AppSessionResponse) {
        memorySession = session
        defaults.set(session.user.id, forKey: currentUserKey)
        if let data = try? JSONEncoder.backend.encode(session) {
            defaults.set(data, forKey: storagePrefix + session.user.id)
        }
    }

    func merge(current: AppSessionResponse?, next: AppSessionResponse) -> AppSessionResponse {
        guard let current, current.user.id == next.user.id else {
            return next
        }

        let currentDashboardCount = current.dashboard.feed.now.count
            + current.dashboard.feed.today.count
            + current.dashboard.feed.worthKnowing.count
        let nextDashboardCount = next.dashboard.feed.now.count
            + next.dashboard.feed.today.count
            + next.dashboard.feed.worthKnowing.count
        let dashboard = currentDashboardCount > 0 && nextDashboardCount == 0 ? current.dashboard : next.dashboard
        let mailbox = !current.mailbox.isEmpty && next.mailbox.isEmpty ? current.mailbox : next.mailbox

        return AppSessionResponse(
            user: next.user,
            readiness: next.readiness,
            dashboard: dashboard,
            mailbox: mailbox,
            sync: next.sync
        )
    }

    func clear() {
        memorySession = nil
        if let userID = defaults.string(forKey: currentUserKey) {
            defaults.removeObject(forKey: storagePrefix + userID)
        }
        defaults.removeObject(forKey: currentUserKey)
    }
}
