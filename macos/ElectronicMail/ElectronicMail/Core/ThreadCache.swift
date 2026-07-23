import Foundation

public final class ThreadCache {
    private static let legacyStoragePrefix = "electronic-mail-thread:"

    private let defaults: UserDefaults
    private let lock = NSLock()
    private var memoryThreads: [String: [String: ThreadReaderResponse]] = [:]

    public init(defaults: UserDefaults = .standard, namespace _: String = "live") {
        self.defaults = defaults
        purgeLegacyPreferences()
    }

    func read(userID: String, threadID: String) -> ThreadReaderResponse? {
        lock.withThreadCacheLock {
            memoryThreads[userID]?[threadID]
        }
    }

    func write(_ thread: ThreadReaderResponse, userID: String, threadID: String) {
        lock.withThreadCacheLock {
            var userThreads = memoryThreads[userID] ?? [:]
            userThreads[threadID] = thread
            memoryThreads[userID] = userThreads
        }
    }

    func clearMemory() {
        lock.withThreadCacheLock {
            memoryThreads = [:]
        }
        purgeLegacyPreferences()
    }

    private func purgeLegacyPreferences() {
        for key in defaults.dictionaryRepresentation().keys where key.hasPrefix(Self.legacyStoragePrefix) {
            defaults.removeObject(forKey: key)
        }
    }
}

private extension NSLock {
    func withThreadCacheLock<T>(_ body: () -> T) -> T {
        lock()
        defer { unlock() }
        return body()
    }
}
