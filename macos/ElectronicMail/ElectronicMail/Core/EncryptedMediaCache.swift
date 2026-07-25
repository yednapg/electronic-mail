import CryptoKit
import Foundation

struct CachedMediaPayload: Equatable, Sendable {
    let data: Data
    let mimeType: String?
    let filename: String?
    let etag: String?
}

private struct MediaCacheManifest: Codable {
    var entries: [String: MediaCacheEntry] = [:]
}

private struct MediaCacheEntry: Codable {
    let identity: String
    let etag: String
    let mimeType: String?
    let filename: String?
    let fileName: String
    let encryptedByteCount: Int
    var lastAccessedAt: TimeInterval
}

/// Account-scoped encrypted file cache with a strict LRU byte budget.
actor EncryptedMediaCache {
    enum Namespace: String, Sendable {
        case attachments
        case remoteImages = "remote-images"
    }

    private let namespace: Namespace
    private let byteLimit: Int
    private let rootURL: URL?
    private let keyStore: AccountContentKeyStore
    private let beforeReturningRead: (@Sendable () async -> Void)?
    private var manifests: [String: MediaCacheManifest] = [:]

    init(
        namespace: Namespace,
        byteLimit: Int,
        rootURL: URL? = nil,
        keyStore: AccountContentKeyStore = .shared,
        beforeReturningRead: (@Sendable () async -> Void)? = nil
    ) {
        self.namespace = namespace
        self.byteLimit = max(0, byteLimit)
        self.keyStore = keyStore
        self.beforeReturningRead = beforeReturningRead
        if let rootURL {
            self.rootURL = rootURL
        } else {
            self.rootURL = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first?
                .appendingPathComponent("ElectronicMail", isDirectory: true)
                .appendingPathComponent("MediaCache", isDirectory: true)
        }
    }

    func read(userID: String, identity: String) async -> CachedMediaPayload? {
        guard var manifest = loadManifest(userID: userID),
              var entry = manifest.entries[identity],
              let directory = accountDirectory(userID: userID) else {
            return nil
        }
        let fileURL = directory.appendingPathComponent(entry.fileName, isDirectory: false)
        do {
            let encrypted = try Data(contentsOf: fileURL, options: [.mappedIfSafe])
            guard let key = keyStore.loadKey(userID: userID) else {
                throw CacheError.missingKey
            }
            let box = try AES.GCM.SealedBox(combined: encrypted)
            let plaintext = try AES.GCM.open(
                box,
                using: key,
                authenticating: authenticatedContext(userID: userID, identity: identity, etag: entry.etag)
            )
            entry.lastAccessedAt = Date().timeIntervalSince1970
            manifest.entries[identity] = entry
            manifests[userID] = manifest
            try? writeManifest(manifest, userID: userID)
            let payload = CachedMediaPayload(
                data: plaintext,
                mimeType: entry.mimeType,
                filename: entry.filename,
                etag: entry.etag
            )
            // This suspension point is also useful in deterministic race
            // tests. Callers must revalidate their account/generation after
            // awaiting a cache read because sign-out may purge the account
            // while decrypted bytes are in flight.
            await beforeReturningRead?()
            return payload
        } catch {
            try? FileManager.default.removeItem(at: fileURL)
            manifest.entries[identity] = nil
            manifests[userID] = manifest
            try? writeManifest(manifest, userID: userID)
            return nil
        }
    }

    func write(
        _ payload: CachedMediaPayload,
        userID: String,
        identity: String
    ) throws {
        guard byteLimit > 0, let directory = accountDirectory(userID: userID) else {
            throw CacheError.unavailable
        }
        let etag = payload.etag ?? Self.digest(payload.data)
        let key = try keyStore.loadOrCreateKey(userID: userID)
        let box = try AES.GCM.seal(
            payload.data,
            using: key,
            authenticating: authenticatedContext(userID: userID, identity: identity, etag: etag)
        )
        guard let encrypted = box.combined else {
            throw CacheError.encryptionFailed
        }
        // Files larger than the entire budget can be previewed or saved by the
        // caller but are deliberately not retained.
        guard encrypted.count <= byteLimit else { return }

        var manifest = loadManifest(userID: userID) ?? MediaCacheManifest()
        try FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700]
        )
        let fileName = "\(Self.digest(Data("\(identity)\u{0}\(etag)".utf8))).sealed"
        let fileURL = directory.appendingPathComponent(fileName, isDirectory: false)
        // Complete-file-protection write options are an iOS data-protection
        // facility and can fail with EPERM for otherwise writable macOS
        // locations (including XCTest containers). The payload is already
        // AES-GCM encrypted with an account Keychain key; use a same-directory
        // atomic replacement and lock the resulting file to the account.
        try encrypted.write(to: fileURL, options: .atomic)
        try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: fileURL.path)

        if let previous = manifest.entries[identity], previous.fileName != fileName {
            try? FileManager.default.removeItem(at: directory.appendingPathComponent(previous.fileName))
        }
        manifest.entries[identity] = MediaCacheEntry(
            identity: identity,
            etag: etag,
            mimeType: payload.mimeType,
            filename: payload.filename,
            fileName: fileName,
            encryptedByteCount: encrypted.count,
            lastAccessedAt: Date().timeIntervalSince1970
        )
        evictIfNeeded(manifest: &manifest, directory: directory)
        do {
            try writeManifest(manifest, userID: userID)
            manifests[userID] = manifest
        } catch {
            // Do not leave newly written bytes outside the durable LRU index.
            try? FileManager.default.removeItem(at: fileURL)
            manifests[userID] = nil
            throw error
        }
    }

    func purge(userID: String) {
        manifests[userID] = nil
        if let directory = accountDirectory(userID: userID) {
            try? FileManager.default.removeItem(at: directory)
        }
    }

    func purgeAll() {
        manifests = [:]
        guard let rootURL else { return }
        try? FileManager.default.removeItem(at: rootURL.appendingPathComponent(namespace.rawValue))
    }

    private func loadManifest(userID: String) -> MediaCacheManifest? {
        if let cached = manifests[userID] {
            return cached
        }
        guard let manifestURL = manifestURL(userID: userID) else { return nil }
        if FileManager.default.fileExists(atPath: manifestURL.path) {
            guard let key = keyStore.loadKey(userID: userID) else { return nil }
            do {
                let encrypted = try Data(contentsOf: manifestURL)
                let box = try AES.GCM.SealedBox(combined: encrypted)
                let plaintext = try AES.GCM.open(
                    box,
                    using: key,
                    authenticating: manifestAuthenticatedContext(userID: userID)
                )
                let decoded = try JSONDecoder().decode(MediaCacheManifest.self, from: plaintext)
                manifests[userID] = decoded
                return decoded
            } catch {
                // A corrupt index cannot be trusted to account for the byte
                // budget. Quarantine it by removing this encrypted cache only;
                // online reading remains available and the cache can rebuild.
                if let directory = accountDirectory(userID: userID) {
                    try? FileManager.default.removeItem(at: directory)
                }
            }
        } else if let legacyURL = legacyManifestURL(userID: userID),
                  let data = try? Data(contentsOf: legacyURL),
                  let decoded = try? JSONDecoder().decode(MediaCacheManifest.self, from: data) {
            manifests[userID] = decoded
            if (try? writeManifest(decoded, userID: userID)) != nil {
                try? FileManager.default.removeItem(at: legacyURL)
            }
            return decoded
        }
        let empty = MediaCacheManifest()
        manifests[userID] = empty
        return empty
    }

    private func writeManifest(_ manifest: MediaCacheManifest, userID: String) throws {
        guard let manifestURL = manifestURL(userID: userID) else {
            throw CacheError.unavailable
        }
        try FileManager.default.createDirectory(
            at: manifestURL.deletingLastPathComponent(),
            withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700]
        )
        let plaintext = try JSONEncoder().encode(manifest)
        let key = try keyStore.loadOrCreateKey(userID: userID)
        let box = try AES.GCM.seal(
            plaintext,
            using: key,
            authenticating: manifestAuthenticatedContext(userID: userID)
        )
        guard let encrypted = box.combined else { throw CacheError.encryptionFailed }
        try encrypted.write(to: manifestURL, options: .atomic)
        try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: manifestURL.path)
    }

    private func evictIfNeeded(manifest: inout MediaCacheManifest, directory: URL) {
        var total = manifest.entries.values.reduce(0) { $0 + $1.encryptedByteCount }
        guard total > byteLimit else { return }
        for entry in manifest.entries.values.sorted(by: { $0.lastAccessedAt < $1.lastAccessedAt }) {
            guard total > byteLimit else { break }
            manifest.entries[entry.identity] = nil
            total -= entry.encryptedByteCount
            try? FileManager.default.removeItem(at: directory.appendingPathComponent(entry.fileName))
        }
    }

    private func accountDirectory(userID: String) -> URL? {
        rootURL?
            .appendingPathComponent(namespace.rawValue, isDirectory: true)
            .appendingPathComponent(Self.digest(Data(userID.utf8)), isDirectory: true)
    }

    private func manifestURL(userID: String) -> URL? {
        accountDirectory(userID: userID)?.appendingPathComponent("manifest.sealed", isDirectory: false)
    }

    private func legacyManifestURL(userID: String) -> URL? {
        accountDirectory(userID: userID)?.appendingPathComponent("manifest.json", isDirectory: false)
    }

    private func authenticatedContext(userID: String, identity: String, etag: String) -> Data {
        Data("ElectronicMail.media-cache.v1\u{0}\(namespace.rawValue)\u{0}\(userID)\u{0}\(identity)\u{0}\(etag)".utf8)
    }

    private func manifestAuthenticatedContext(userID: String) -> Data {
        Data("ElectronicMail.media-cache-manifest.v1\u{0}\(namespace.rawValue)\u{0}\(userID)".utf8)
    }

    private static func digest(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    private enum CacheError: Error {
        case unavailable
        case missingKey
        case encryptionFailed
    }
}

actor EncryptedAttachmentCache {
    static let automaticFileLimit = 5 * 1_024 * 1_024
    private let storage: EncryptedMediaCache

    init(
        rootURL: URL? = nil,
        byteLimit: Int = 100 * 1_024 * 1_024,
        keyStore: AccountContentKeyStore = .shared,
        beforeReturningRead: (@Sendable () async -> Void)? = nil
    ) {
        storage = EncryptedMediaCache(
            namespace: .attachments,
            byteLimit: byteLimit,
            rootURL: rootURL,
            keyStore: keyStore,
            beforeReturningRead: beforeReturningRead
        )
    }

    func read(userID: String, messageID: String, attachmentID: String) async -> DownloadedAttachment? {
        guard let cached = await storage.read(
            userID: userID,
            identity: identity(messageID: messageID, attachmentID: attachmentID)
        ) else {
            return nil
        }
        return DownloadedAttachment(
            filename: cached.filename ?? "attachment",
            mimeType: cached.mimeType,
            data: cached.data,
            etag: cached.etag
        )
    }

    func write(
        _ attachment: DownloadedAttachment,
        userID: String,
        messageID: String,
        attachmentID: String
    ) async throws {
        try await storage.write(
            CachedMediaPayload(
                data: attachment.data,
                mimeType: attachment.mimeType,
                filename: attachment.filename,
                etag: attachment.etag
            ),
            userID: userID,
            identity: identity(messageID: messageID, attachmentID: attachmentID)
        )
    }

    func purge(userID: String) async {
        await storage.purge(userID: userID)
    }

    func purgeAll() async {
        await storage.purgeAll()
    }

    private func identity(messageID: String, attachmentID: String) -> String {
        "\(messageID)\u{0}\(attachmentID)"
    }
}

actor EncryptedRemoteImageCache {
    private let storage: EncryptedMediaCache

    init(
        rootURL: URL? = nil,
        byteLimit: Int = 50 * 1_024 * 1_024,
        keyStore: AccountContentKeyStore = .shared,
        beforeReturningRead: (@Sendable () async -> Void)? = nil
    ) {
        storage = EncryptedMediaCache(
            namespace: .remoteImages,
            byteLimit: byteLimit,
            rootURL: rootURL,
            keyStore: keyStore,
            beforeReturningRead: beforeReturningRead
        )
    }

    func read(userID: String, assetID: String) async -> CachedMediaPayload? {
        await storage.read(userID: userID, identity: assetID)
    }

    func write(_ payload: CachedMediaPayload, userID: String, assetID: String) async throws {
        try await storage.write(payload, userID: userID, identity: assetID)
    }

    func purge(userID: String) async {
        await storage.purge(userID: userID)
    }

    func purgeAll() async {
        await storage.purgeAll()
    }
}
