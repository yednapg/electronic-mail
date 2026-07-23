import Foundation

public final class AppSessionCache {
    private static let legacySessionPrefix = "electronic-mail-app-session:"
    private static let legacyCurrentUserPrefix = "electronic-mail-current-user:"

    private let defaults: UserDefaults
    private let lock = NSLock()
    private var memorySession: AppSessionResponse?

    public init(defaults: UserDefaults = .standard, namespace _: String = "live") {
        self.defaults = defaults
        purgeLegacyPreferences()
    }

    func read() -> AppSessionResponse? {
        lock.withAppSessionCacheLock {
            memorySession
        }
    }

    func read(userID: String) -> AppSessionResponse? {
        lock.withAppSessionCacheLock {
            guard memorySession?.user.id == userID else {
                return nil
            }
            return memorySession
        }
    }

    func write(_ session: AppSessionResponse) {
        lock.withAppSessionCacheLock {
            memorySession = session
        }
    }

    func merge(current: AppSessionResponse?, next: AppSessionResponse, allowEmptyDashboard: Bool = false) -> AppSessionResponse {
        guard let current, current.user.id == next.user.id else {
            return next
        }

        let currentDashboardCount = current.dashboard.feed.now.count
            + current.dashboard.feed.today.count
            + current.dashboard.feed.worthKnowing.count
        let nextDashboardCount = next.dashboard.feed.now.count
            + next.dashboard.feed.today.count
            + next.dashboard.feed.worthKnowing.count
        let dashboard = !allowEmptyDashboard && currentDashboardCount > 0 && nextDashboardCount == 0 ? current.dashboard : next.dashboard
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
        lock.withAppSessionCacheLock {
            memorySession = nil
        }
        purgeLegacyPreferences()
    }

    private func purgeLegacyPreferences() {
        for key in defaults.dictionaryRepresentation().keys where
            key.hasPrefix(Self.legacySessionPrefix) || key.hasPrefix(Self.legacyCurrentUserPrefix)
        {
            defaults.removeObject(forKey: key)
        }
    }
}

private extension NSLock {
    func withAppSessionCacheLock<T>(_ body: () -> T) -> T {
        lock()
        defer { unlock() }
        return body()
    }
}
