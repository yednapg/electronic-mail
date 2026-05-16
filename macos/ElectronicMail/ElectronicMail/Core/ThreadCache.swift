import Foundation

public final class ThreadCache {
    private let defaults: UserDefaults
    private let storagePrefix: String
    private var memoryThreads: [String: [String: ThreadReaderResponse]] = [:]

    public init(defaults: UserDefaults = .standard, namespace: String = "live") {
        self.defaults = defaults
        self.storagePrefix = "electronic-mail-thread:\(namespace):v1:"
    }

    func read(userID: String, threadID: String) -> ThreadReaderResponse? {
        if let thread = memoryThreads[userID]?[threadID] {
            return thread
        }
        guard let data = defaults.data(forKey: key(userID: userID, threadID: threadID)) else {
            return nil
        }
        do {
            let thread = try JSONDecoder.backend.decode(ThreadReaderResponse.self, from: data)
            var userThreads = memoryThreads[userID] ?? [:]
            userThreads[threadID] = thread
            memoryThreads[userID] = userThreads
            return thread
        } catch {
            return nil
        }
    }

    func write(_ thread: ThreadReaderResponse, userID: String, threadID: String) {
        var userThreads = memoryThreads[userID] ?? [:]
        userThreads[threadID] = thread
        memoryThreads[userID] = userThreads
        if let data = try? JSONEncoder.backend.encode(thread) {
            defaults.set(data, forKey: key(userID: userID, threadID: threadID))
        }
    }

    func clearMemory() {
        memoryThreads = [:]
    }

    private func key(userID: String, threadID: String) -> String {
        "\(storagePrefix)\(userID):\(threadID)"
    }
}
