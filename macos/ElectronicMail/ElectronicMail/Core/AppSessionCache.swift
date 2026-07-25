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
        let mailbox = Self.mergedMailbox(current: current, next: next)

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

    private static func mergedMailbox(
        current: AppSessionResponse,
        next: AppSessionResponse
    ) -> MailboxResponse {
        guard !current.mailbox.isEmpty, next.mailbox.isEmpty else {
            return next.mailbox
        }

        let currentProgress = MailboxSyncProgress.newestMerged([
            current.readiness.mailboxSyncProgress,
            current.sync.mailboxSyncProgress,
            current.mailbox.mailboxSyncProgress,
        ])
        let nextProgress = MailboxSyncProgress.newestMerged([
            next.readiness.mailboxSyncProgress,
            next.sync.mailboxSyncProgress,
            next.mailbox.mailboxSyncProgress,
        ])
        let currentGeneration = normalizedGeneration(
            currentProgress.syncGeneration ?? current.mailbox.syncGeneration
        )
        let nextGeneration = normalizedGeneration(
            nextProgress.syncGeneration ?? next.mailbox.syncGeneration
        )

        // A newly reported progressive generation owns its empty first
        // snapshot. Keeping a previous generation here makes an interrupted
        // session/mailbox commit restore stale mail forever.
        if let nextGeneration, nextGeneration != currentGeneration {
            return next.mailbox
        }
        // Conversely, a generation-less legacy failure must not erase a
        // generation-fenced cache.
        if currentGeneration != nil, nextGeneration == nil {
            return current.mailbox
        }
        if isConfirmedEmpty(next.mailbox, progress: nextProgress) {
            return next.mailbox
        }
        return current.mailbox
    }

    private static func isConfirmedEmpty(
        _ mailbox: MailboxResponse,
        progress: MailboxSyncProgress
    ) -> Bool {
        guard mailbox.totalThreads == 0,
              mailbox.sections.allSatisfy({ $0.rows.isEmpty }),
              mailbox.nextCursor?.isEmpty != false,
              mailbox.loadedThreads == nil || mailbox.loadedThreads == 0 else {
            return false
        }
        let completeLegacySnapshot = mailbox.fullImportRunning != true
            && mailbox.fullImportCompleted != false
        let explicitlyConfirmedInitialWindow = progress.initialWindowComplete == true
            && max(0, progress.initialTargetCount ?? 0) == 0
            && max(0, progress.initialMetadataCount ?? 0) == 0
            && max(0, progress.estimatedTotalCount ?? 0) == 0
        return completeLegacySnapshot || explicitlyConfirmedInitialWindow
    }

    private static func normalizedGeneration(_ generation: String?) -> String? {
        guard let generation = generation?.trimmingCharacters(in: .whitespacesAndNewlines),
              !generation.isEmpty else {
            return nil
        }
        return generation
    }
}

private extension NSLock {
    func withAppSessionCacheLock<T>(_ body: () -> T) -> T {
        lock()
        defer { unlock() }
        return body()
    }
}
