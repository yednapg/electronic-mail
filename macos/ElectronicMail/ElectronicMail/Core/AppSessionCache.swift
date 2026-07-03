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

    func merge(current: AppSessionResponse?, next: AppSessionResponse, allowEmptyDashboard: Bool = false) -> AppSessionResponse {
        guard let current, current.user.id == next.user.id else {
            return next
        }

        let mailbox = !current.mailbox.isEmpty && next.mailbox.isEmpty ? current.mailbox : next.mailbox
        let keepCurrentSmartProjection = current.smartInbox?.isEmpty == false && next.smartInbox?.isEmpty != false
        let smartInbox = keepCurrentSmartProjection ? current.smartInbox : next.smartInbox
        let smartWorkQueue = keepCurrentSmartProjection && current.smartWorkQueue?.hasOpenItems == true && next.smartWorkQueue?.hasOpenItems != true ? current.smartWorkQueue : next.smartWorkQueue
        let smartReadiness = keepCurrentSmartProjection ? current.smartReadiness : next.smartReadiness

        return AppSessionResponse(
            user: next.user,
            readiness: next.readiness,
            dashboard: next.dashboard,
            mailbox: mailbox,
            sync: next.sync,
            smartInbox: smartInbox,
            smartWorkQueue: smartWorkQueue,
            smartReadiness: smartReadiness
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
