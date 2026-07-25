import Foundation
import CryptoKit
import Security
import SQLite3

/// One non-synchronizable, device-only 256-bit key per signed-in account. The
/// same key is intentionally reusable by the thread, attachment, and image
/// caches while each payload supplies its own authenticated context.
final class AccountContentKeyStore: @unchecked Sendable {
    static let shared = AccountContentKeyStore()

    private static let defaultService = "app.electronicmail.content-key.v1"
    private let service: String
    private let lock = NSLock()

    init(service: String = AccountContentKeyStore.defaultService) {
        self.service = service
    }

    func loadOrCreateKey(userID: String) throws -> SymmetricKey {
        try lock.withLock {
            if let key = loadKeyWithoutLock(userID: userID) {
                return key
            }

            let key = SymmetricKey(size: .bits256)
            let keyData = key.withUnsafeBytes { Data($0) }
            do {
                try writeKeyData(keyData, userID: userID)
                return key
            } catch KeychainError.status(errSecDuplicateItem) {
                if let racedKey = loadKeyWithoutLock(userID: userID) {
                    return racedKey
                }
                throw KeychainError.status(errSecDuplicateItem)
            }
        }
    }

    func loadKey(userID: String) -> SymmetricKey? {
        lock.withLock {
            loadKeyWithoutLock(userID: userID)
        }
    }

    func removeKey(userID: String) {
        lock.withLock {
            _ = SecItemDelete(secureQuery(userID: userID) as CFDictionary)
            #if os(macOS) && (DEBUG || ELECTRONIC_MAIL_LOCAL_BETA)
            _ = SecItemDelete(classicMacQuery(userID: userID) as CFDictionary)
            #endif
        }
    }

    func removeAllKeys() {
        lock.withLock {
            var query = baseQuery(userID: nil)
            query[kSecUseDataProtectionKeychain as String] = true
            query[kSecAttrSynchronizable as String] = false
            _ = SecItemDelete(query as CFDictionary)
            #if os(macOS) && (DEBUG || ELECTRONIC_MAIL_LOCAL_BETA)
            _ = SecItemDelete(baseQuery(userID: nil) as CFDictionary)
            #endif
        }
    }

    private func loadKeyWithoutLock(userID: String) -> SymmetricKey? {
        if let data = readKeyData(query: secureQuery(userID: userID)), data.count == 32 {
            return SymmetricKey(data: data)
        }

        #if os(macOS) && (DEBUG || ELECTRONIC_MAIL_LOCAL_BETA)
        if let data = readKeyData(query: classicMacQuery(userID: userID)), data.count == 32 {
            // Ad-hoc local builds may not have the Data Protection Keychain
            // entitlement. Try to migrate, but keep the classic item intact if
            // the secure write is unavailable.
            try? writeKeyData(data, userID: userID)
            return SymmetricKey(data: data)
        }
        #endif

        return nil
    }

    private func writeKeyData(_ data: Data, userID: String) throws {
        let query = secureQuery(userID: userID)
        var item = query
        item[kSecValueData as String] = data
        item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        let status = SecItemAdd(item as CFDictionary, nil)
        if status == errSecSuccess || status == errSecDuplicateItem {
            if status == errSecDuplicateItem {
                throw KeychainError.status(status)
            }
            #if os(macOS) && (DEBUG || ELECTRONIC_MAIL_LOCAL_BETA)
            _ = SecItemDelete(classicMacQuery(userID: userID) as CFDictionary)
            #endif
            return
        }

        #if os(macOS) && (DEBUG || ELECTRONIC_MAIL_LOCAL_BETA)
        if KeychainSessionTokenStore.shouldUseClassicMacFallback(
            for: status,
            debugBuild: KeychainSessionTokenStore.classicMacFallbackEnabled
        ) {
            var classicItem = classicMacQuery(userID: userID)
            classicItem[kSecValueData as String] = data
            let classicStatus = SecItemAdd(classicItem as CFDictionary, nil)
            guard classicStatus == errSecSuccess || classicStatus == errSecDuplicateItem else {
                throw KeychainError.status(classicStatus)
            }
            if classicStatus == errSecDuplicateItem {
                throw KeychainError.status(classicStatus)
            }
            return
        }
        #endif

        throw KeychainError.status(status)
    }

    private func readKeyData(query base: [String: Any]) -> Data? {
        var query = base
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess else {
            return nil
        }
        return item as? Data
    }

    private func secureQuery(userID: String) -> [String: Any] {
        var query = baseQuery(userID: userID)
        query[kSecUseDataProtectionKeychain as String] = true
        query[kSecAttrSynchronizable as String] = false
        return query
    }

    #if os(macOS) && (DEBUG || ELECTRONIC_MAIL_LOCAL_BETA)
    private func classicMacQuery(userID: String) -> [String: Any] {
        var query = baseQuery(userID: userID)
        query[kSecAttrSynchronizable as String] = false
        return query
    }
    #endif

    private func baseQuery(userID: String?) -> [String: Any] {
        var query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
        ]
        if let userID {
            let digest = SHA256.hash(data: Data(userID.utf8))
            query[kSecAttrAccount as String] = digest.map { String(format: "%02x", $0) }.joined()
        }
        return query
    }
}

private enum EncryptedThreadPayload {
    private static let magic = Data([0x45, 0x4D, 0x54, 0x43, 0x01]) // EMTC + format version

    static func seal(
        _ plaintext: Data,
        userID: String,
        threadID: String,
        key: SymmetricKey
    ) throws -> Data {
        let compressed = try (plaintext as NSData).compressed(using: .lzfse) as Data
        let box = try AES.GCM.seal(
            compressed,
            using: key,
            authenticating: authenticatedContext(userID: userID, threadID: threadID)
        )
        guard let combined = box.combined else {
            throw PayloadError.missingCombinedRepresentation
        }
        return magic + combined
    }

    static func open(
        _ payload: Data,
        userID: String,
        threadID: String,
        key: SymmetricKey
    ) throws -> Data {
        guard isEncrypted(payload) else {
            throw PayloadError.notEncrypted
        }
        let combined = payload.dropFirst(magic.count)
        let box = try AES.GCM.SealedBox(combined: combined)
        let compressed = try AES.GCM.open(
            box,
            using: key,
            authenticating: authenticatedContext(userID: userID, threadID: threadID)
        )
        return try (compressed as NSData).decompressed(using: .lzfse) as Data
    }

    static func isEncrypted(_ payload: Data) -> Bool {
        payload.count > magic.count && payload.starts(with: magic)
    }

    private static func authenticatedContext(userID: String, threadID: String) -> Data {
        Data("ElectronicMail.thread-cache.v1\u{0}\(userID)\u{0}\(threadID)".utf8)
    }

    private enum PayloadError: Error {
        case missingCombinedRepresentation
        case notEncrypted
    }
}

public final class SQLiteLocalMailStore: LocalMailStore {
    private let databaseURL: URL
    private let lock = NSLock()
    private let contentKeyStore: AccountContentKeyStore
    private var database: OpaquePointer?

    public convenience init?() {
        guard let supportURL = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first else {
            return nil
        }
        let directory = supportURL.appendingPathComponent("ElectronicMail", isDirectory: true)
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        self.init(databaseURL: directory.appendingPathComponent("LocalMail.sqlite3"))
    }

    public convenience init?(databaseURL: URL) {
        self.init(databaseURL: databaseURL, contentKeyStore: .shared)
    }

    init?(databaseURL: URL, contentKeyStore: AccountContentKeyStore) {
        self.databaseURL = databaseURL
        self.contentKeyStore = contentKeyStore
        guard sqlite3_open(databaseURL.path, &database) == SQLITE_OK else {
            return nil
        }
        guard migrate() else {
            sqlite3_close(database)
            database = nil
            return nil
        }
    }

    deinit {
        sqlite3_close(database)
    }

    public func readSession() -> AppSessionResponse? {
        lock.withLock {
            guard let userID = currentUserID() else {
                return nil
            }
            return readPayload(
                sql: "SELECT payload FROM app_sessions WHERE user_id = ?",
                bindings: [userID],
                as: AppSessionResponse.self
            )
        }
    }

    public func writeSession(_ session: AppSessionResponse) {
        lock.withLock {
            guard execute(sql: "BEGIN IMMEDIATE") else {
                return
            }
            var transactionFinished = false
            defer {
                if !transactionFinished {
                    _ = execute(sql: "ROLLBACK")
                }
            }
            let cachedSession = readPayload(
                sql: "SELECT payload FROM app_sessions WHERE user_id = ?",
                bindings: [session.user.id],
                as: AppSessionResponse.self
            )
            let separatelyCachedMailbox = readPayload(
                sql: "SELECT payload FROM mailboxes WHERE user_id = ? AND label = ?",
                bindings: [session.user.id, session.mailbox.label.rawValue],
                as: MailboxResponse.self
            )
            let incomingMailboxIsRetired = isRetiredMailboxGeneration(
                session.mailbox.syncGeneration,
                userID: session.user.id,
                label: session.mailbox.label
            )
            if !incomingMailboxIsRetired {
                guard clearMailboxTombstonesForNewerAuthoritativeInclusion(
                    mailbox: session.mailbox,
                    comparedTo: separatelyCachedMailbox ?? cachedSession?.mailbox,
                    userID: session.user.id,
                    label: session.mailbox.label
                ) else {
                    return
                }
            }
            let mailboxToPersist: MailboxResponse
            if incomingMailboxIsRetired {
                mailboxToPersist = separatelyCachedMailbox
                    ?? cachedSession?.mailbox
                    ?? session.mailbox
            } else {
                var mergedMailbox = session.mailbox
                if let cachedSession, cachedSession.user.id == session.user.id {
                    mergedMailbox = cacheSafeMailboxSnapshot(
                        cached: cachedSession.mailbox,
                        incoming: mergedMailbox,
                        userID: session.user.id,
                        label: session.mailbox.label
                    )
                }
                if let separatelyCachedMailbox {
                    mergedMailbox = cacheSafeMailboxSnapshot(
                        cached: separatelyCachedMailbox,
                        incoming: mergedMailbox,
                        userID: session.user.id,
                        label: session.mailbox.label
                    )
                }
                mailboxToPersist = applyingMailboxTombstones(
                    to: mergedMailbox,
                    userID: session.user.id,
                    label: session.mailbox.label
                )
            }
            let sessionToPersist = session.replacingMailboxForCache(mailboxToPersist)
            guard let payload = try? JSONEncoder.backend.encode(sessionToPersist),
                  let mailboxPayload = try? JSONEncoder.backend.encode(mailboxToPersist) else {
                return
            }
            guard execute(
                sql: """
                INSERT INTO app_sessions (user_id, payload, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET payload = excluded.payload, updated_at = excluded.updated_at
                """,
                bindings: [session.user.id, payload, Date().timeIntervalSince1970]
            ), execute(
                sql: """
                INSERT INTO current_session (id, user_id)
                VALUES (1, ?)
                ON CONFLICT(id) DO UPDATE SET user_id = excluded.user_id
                """,
                bindings: [session.user.id]
            ), execute(
                sql: """
                INSERT INTO mailboxes (user_id, label, payload, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, label) DO UPDATE SET payload = excluded.payload, updated_at = excluded.updated_at
                """,
                bindings: [
                    session.user.id,
                    session.mailbox.label.rawValue,
                    mailboxPayload,
                    Date().timeIntervalSince1970,
                ]
            ), execute(sql: "COMMIT") else {
                return
            }
            transactionFinished = true
        }
    }

    public func readMailbox(userID: String, label: MailboxLabel) -> MailboxResponse? {
        lock.withLock {
            guard let mailbox = readPayload(
                sql: "SELECT payload FROM mailboxes WHERE user_id = ? AND label = ?",
                bindings: [userID, label.rawValue],
                as: MailboxResponse.self
            ), mailbox.label == label else {
                return nil
            }
            return mailbox
        }
    }

    public func writeMailbox(_ mailbox: MailboxResponse, userID: String, label: MailboxLabel) {
        writeMailbox(
            mailbox,
            userID: userID,
            label: label,
            removingThreadIDs: [],
            reenteringThreadIDs: [],
            reentryLabels: []
        )
    }

    public func writeMailbox(
        _ mailbox: MailboxResponse,
        userID: String,
        label: MailboxLabel,
        removingThreadIDs: [String],
        reenteringThreadIDs: [String],
        reentryLabels: [MailboxLabel]
    ) {
        guard mailbox.label == label else {
            return
        }
        lock.withLock {
            guard execute(sql: "BEGIN IMMEDIATE") else {
                return
            }
            var transactionFinished = false
            defer {
                if !transactionFinished {
                    _ = execute(sql: "ROLLBACK")
                }
            }
            guard !isRetiredMailboxGeneration(
                mailbox.syncGeneration,
                userID: userID,
                label: label
            ) else {
                return
            }
            let cached = readPayload(
                sql: "SELECT payload FROM mailboxes WHERE user_id = ? AND label = ?",
                bindings: [userID, label.rawValue],
                as: MailboxResponse.self
            )
            let incoming = removingThreadIDs.isEmpty
                ? mailbox
                : mailbox.inheritingCacheIdentityIfNeeded(from: cached)
            if !removingThreadIDs.isEmpty {
                guard recordMailboxTombstones(
                    Set(removingThreadIDs),
                    mailbox: incoming,
                    userID: userID,
                    label: label
                ) else {
                    return
                }
            }
            if !reentryLabels.isEmpty {
                guard clearMailboxTombstonesForExplicitReentry(
                    Set(reenteringThreadIDs),
                    labels: Set(reentryLabels),
                    mailbox: incoming,
                    userID: userID
                ) else {
                    return
                }
            }
            guard clearMailboxTombstonesForNewerAuthoritativeInclusion(
                mailbox: incoming,
                comparedTo: cached,
                userID: userID,
                label: label
            ) else {
                return
            }
            var mailboxToPersist = cached.map {
                cacheSafeMailboxSnapshot(
                    cached: $0,
                    incoming: incoming,
                    userID: userID,
                    label: label
                )
            } ?? applyingMailboxTombstones(to: incoming, userID: userID, label: label)
            if !removingThreadIDs.isEmpty {
                // The optimistic projection already contains the authoritative
                // post-action counts. A progressive merge may retain older
                // pages, but it must not restore pre-action totals or the row.
                mailboxToPersist = mailboxToPersist.withMailboxCounts(
                    totalThreads: max(
                        mailboxToPersist.sections.reduce(0) { $0 + $1.rows.count },
                        incoming.totalThreads
                    ),
                    unreadThreads: incoming.unreadThreads ?? mailboxToPersist.unreadThreads,
                    loadedThreads: max(
                        mailboxToPersist.sections.reduce(0) { $0 + $1.rows.count },
                        incoming.loadedThreads ?? 0
                    )
                )
            }
            guard let payload = try? JSONEncoder.backend.encode(mailboxToPersist) else {
                return
            }
            let didWrite = execute(
                sql: """
                INSERT INTO mailboxes (user_id, label, payload, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, label) DO UPDATE SET payload = excluded.payload, updated_at = excluded.updated_at
                """,
                bindings: [userID, label.rawValue, payload, Date().timeIntervalSince1970]
            )
            guard didWrite, execute(sql: "COMMIT") else {
                return
            }
            transactionFinished = true
        }
    }

    public func readThread(userID: String, threadID: String) -> ThreadReaderResponse? {
        lock.withLock {
            guard let payload = readData(
                sql: "SELECT payload FROM thread_details WHERE user_id = ? AND thread_id = ?",
                bindings: [userID, threadID]
            ) else {
                return nil
            }

            if EncryptedThreadPayload.isEncrypted(payload) {
                guard let key = contentKeyStore.loadKey(userID: userID),
                      let plaintext = try? EncryptedThreadPayload.open(
                          payload,
                          userID: userID,
                          threadID: threadID,
                          key: key
                      ) else {
                    return nil
                }
                return try? JSONDecoder.backend.decode(ThreadReaderResponse.self, from: plaintext)
            }

            // Existing installations stored JSON directly in this column.
            // Decode first so a Keychain failure never makes valid offline mail
            // unavailable, then opportunistically replace it with ciphertext.
            guard let thread = try? JSONDecoder.backend.decode(ThreadReaderResponse.self, from: payload) else {
                return nil
            }
            if let migrated = encryptedThreadPayload(
                payload,
                userID: userID,
                threadID: threadID
            ) {
                let didMigrate = execute(
                    sql: "UPDATE thread_details SET payload = ?, updated_at = ? WHERE user_id = ? AND thread_id = ?",
                    bindings: [migrated, Date().timeIntervalSince1970, userID, threadID]
                )
                if didMigrate {
                    // secure_delete overwrites the replaced cell in the main
                    // database. Truncating the WAL also removes frames that may
                    // still contain the legacy plaintext representation.
                    _ = execute(sql: "PRAGMA wal_checkpoint(TRUNCATE)")
                }
            }
            return thread
        }
    }

    public func writeThread(_ thread: ThreadReaderResponse, userID: String, threadID: String) {
        guard let plaintext = try? JSONEncoder.backend.encode(thread),
              let payload = encryptedThreadPayload(
                  plaintext,
                  userID: userID,
                  threadID: threadID
              ) else {
            return
        }
        lock.withLock {
            _ = execute(
                sql: """
                INSERT INTO thread_details (user_id, thread_id, payload, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, thread_id) DO UPDATE SET payload = excluded.payload, updated_at = excluded.updated_at
                """,
                bindings: [userID, threadID, payload, Date().timeIntervalSince1970]
            )
        }
    }

    public func removeThread(userID: String, threadID: String) {
        lock.withLock {
            _ = execute(
                sql: "DELETE FROM thread_details WHERE user_id = ? AND thread_id = ?",
                bindings: [userID, threadID]
            )
        }
    }

    public func purgeAccount(userID: String) {
        let didPurge = lock.withLock {
            _ = execute(sql: "PRAGMA secure_delete = ON")
            guard execute(sql: "BEGIN IMMEDIATE") else {
                return false
            }
            let statements: [(String, [Any])] = [
                ("DELETE FROM current_session WHERE user_id = ?", [userID]),
                ("DELETE FROM app_sessions WHERE user_id = ?", [userID]),
                ("DELETE FROM mailboxes WHERE user_id = ?", [userID]),
                ("DELETE FROM retired_mailbox_generations WHERE user_id = ?", [userID]),
                ("DELETE FROM mailbox_thread_tombstones WHERE user_id = ?", [userID]),
                ("DELETE FROM thread_details WHERE user_id = ?", [userID]),
                ("DELETE FROM pending_thread_actions WHERE user_id = ?", [userID]),
            ]
            let deleted = statements.allSatisfy { execute(sql: $0.0, bindings: $0.1) }
            guard deleted, execute(sql: "COMMIT") else {
                _ = execute(sql: "ROLLBACK")
                return false
            }
            purgeDeletedPages()
            return true
        }
        // Sign-out/disconnect is a hard cryptographic boundary. Even if a
        // damaged or locked SQLite file prevents row deletion, removing the
        // account key immediately makes every encrypted body unreadable.
        contentKeyStore.removeKey(userID: userID)
        _ = didPurge
    }

    public func writePendingThreadAction(_ action: LocalPendingThreadAction) {
        lock.withLock {
            _ = execute(
                sql: """
                INSERT INTO pending_thread_actions (
                  client_action_id, user_id, mailbox_thread_id, target_message_id, action, state, created_at, updated_at, error
                ) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?)
                ON CONFLICT(client_action_id) DO UPDATE SET
                  user_id = excluded.user_id,
                  mailbox_thread_id = excluded.mailbox_thread_id,
                  target_message_id = excluded.target_message_id,
                  action = excluded.action,
                  state = 'queued',
                  created_at = excluded.created_at,
                  updated_at = excluded.updated_at,
                  error = excluded.error
                """,
                bindings: [
                    action.clientActionID,
                    action.userID,
                    action.mailboxThreadID,
                    action.targetMessageID as Any,
                    action.action.rawValue,
                    action.createdAt,
                    Date().timeIntervalSince1970,
                    action.error as Any,
                ]
            )
        }
    }

    public func pendingThreadActions() -> [LocalPendingThreadAction] {
        lock.withLock {
            var statement: OpaquePointer?
            guard sqlite3_prepare_v2(
                database,
                "SELECT client_action_id, user_id, mailbox_thread_id, target_message_id, action, created_at, error FROM pending_thread_actions WHERE state IN ('queued', 'failed') ORDER BY updated_at ASC",
                -1,
                &statement,
                nil
            ) == SQLITE_OK else {
                return []
            }
            defer { sqlite3_finalize(statement) }
            var actions: [LocalPendingThreadAction] = []
            while sqlite3_step(statement) == SQLITE_ROW {
                guard
                    let clientID = sqliteText(statement, 0),
                    let userID = sqliteText(statement, 1),
                    let threadID = sqliteText(statement, 2),
                    let actionValue = sqliteText(statement, 4),
                    let action = GmailThreadAction(rawValue: actionValue),
                    let createdAt = sqliteText(statement, 5)
                else {
                    continue
                }
                actions.append(
                    LocalPendingThreadAction(
                        clientActionID: clientID,
                        userID: userID,
                        mailboxThreadID: threadID,
                        targetMessageID: sqliteText(statement, 3),
                        action: action,
                        createdAt: createdAt,
                        error: sqliteText(statement, 6)
                    )
                )
            }
            return actions
        }
    }

    public func removePendingThreadAction(clientActionID: String) {
        lock.withLock {
            _ = execute(sql: "DELETE FROM pending_thread_actions WHERE client_action_id = ?", bindings: [clientActionID])
        }
    }

    public func markPendingThreadActionFailed(clientActionID: String, error: String) {
        lock.withLock {
            _ = execute(
                sql: "UPDATE pending_thread_actions SET state = 'failed', error = ?, updated_at = ? WHERE client_action_id = ?",
                bindings: [error, Date().timeIntervalSince1970, clientActionID]
            )
        }
    }

    public func clearSession() {
        lock.withLock {
            guard execute(sql: "BEGIN IMMEDIATE") else {
                return
            }
            let cleared = execute(sql: "DELETE FROM current_session")
                && execute(sql: "DELETE FROM app_sessions")
            if cleared {
                _ = execute(sql: "COMMIT")
            } else {
                _ = execute(sql: "ROLLBACK")
            }
        }
    }

    public func clearAll() {
        let didClear = lock.withLock {
            _ = execute(sql: "PRAGMA secure_delete = ON")

            guard execute(sql: "BEGIN IMMEDIATE") else {
                return false
            }

            let didDeleteEverything = [
                "DELETE FROM current_session",
                "DELETE FROM app_sessions",
                "DELETE FROM mailboxes",
                "DELETE FROM retired_mailbox_generations",
                "DELETE FROM mailbox_thread_tombstones",
                "DELETE FROM thread_details",
                "DELETE FROM pending_thread_actions",
            ].allSatisfy { execute(sql: $0) }

            guard didDeleteEverything, execute(sql: "COMMIT") else {
                _ = execute(sql: "ROLLBACK")
                return false
            }

            purgeDeletedPages()
            return true
        }
        contentKeyStore.removeAllKeys()
        _ = didClear
    }

    private func cacheSafeMailboxSnapshot(
        cached: MailboxResponse,
        incoming: MailboxResponse,
        userID: String,
        label: MailboxLabel
    ) -> MailboxResponse {
        let persisted = MailboxResponse.cacheSafeSnapshot(cached: cached, incoming: incoming)
        let cachedGeneration = MailboxResponse.normalizedGeneration(cached.syncGeneration)
        let incomingGeneration = MailboxResponse.normalizedGeneration(incoming.syncGeneration)
        let persistedGeneration = MailboxResponse.normalizedGeneration(persisted.syncGeneration)
        if let cachedGeneration,
           let incomingGeneration,
           cachedGeneration != incomingGeneration,
           persistedGeneration == incomingGeneration {
            _ = execute(
                sql: """
                INSERT OR IGNORE INTO retired_mailbox_generations (user_id, label, generation)
                VALUES (?, ?, ?)
                """,
                bindings: [userID, label.rawValue, cachedGeneration]
            )
        }
        let persistedScope = mailboxGenerationScope(persisted)
        _ = execute(
            sql: "DELETE FROM mailbox_thread_tombstones WHERE user_id = ? AND label = ? AND generation_scope != ?",
            bindings: [userID, label.rawValue, persistedScope]
        )

        return applyingMailboxTombstones(to: persisted, userID: userID, label: label)
    }

    private func recordMailboxTombstones(
        _ threadIDs: Set<String>,
        mailbox: MailboxResponse,
        userID: String,
        label: MailboxLabel
    ) -> Bool {
        guard !threadIDs.isEmpty else {
            return true
        }
        let generationScope = mailboxGenerationScope(mailbox)
        guard execute(
            sql: "DELETE FROM mailbox_thread_tombstones WHERE user_id = ? AND label = ? AND generation_scope != ?",
            bindings: [userID, label.rawValue, generationScope]
        ) else {
            return false
        }
        let progressAt: Any = MailboxResponse.progressDate(mailbox)
            .map { $0.timeIntervalSince1970 as Any }
            ?? NSNull()
        let mailboxRevision: Any = normalizedMailboxRevision(mailbox.mailboxRevision) ?? NSNull()
        for threadID in threadIDs {
            guard execute(
                sql: """
                INSERT INTO mailbox_thread_tombstones (
                  user_id, label, generation_scope, thread_id, mailbox_revision, progress_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, label, generation_scope, thread_id)
                DO UPDATE SET
                  mailbox_revision = excluded.mailbox_revision,
                  progress_at = excluded.progress_at,
                  created_at = excluded.created_at
                """,
                bindings: [
                    userID,
                    label.rawValue,
                    generationScope,
                    threadID,
                    mailboxRevision,
                    progressAt,
                    Date().timeIntervalSince1970,
                ]
            ) else {
                return false
            }
        }
        return true
    }

    private func clearMailboxTombstonesForNewerAuthoritativeInclusion(
        mailbox: MailboxResponse,
        comparedTo cached: MailboxResponse?,
        userID: String,
        label: MailboxLabel
    ) -> Bool {
        guard mailbox.isAuthoritativeCacheSnapshot,
              let incomingProgressDate = MailboxResponse.progressDate(mailbox),
              let incomingRevision = normalizedMailboxRevision(mailbox.mailboxRevision) else {
            return true
        }
        if let cached, cached.label == mailbox.label {
            let cachedGeneration = MailboxResponse.normalizedGeneration(cached.syncGeneration)
            let incomingGeneration = MailboxResponse.normalizedGeneration(mailbox.syncGeneration)
            if cachedGeneration != incomingGeneration {
                // A legacy response cannot supersede a generation-fenced cache.
                guard incomingGeneration != nil else {
                    return true
                }
                if cachedGeneration != nil,
                   MailboxResponse.isDemonstrablyOlder(mailbox, than: cached) {
                    return true
                }
            } else if MailboxResponse.isDemonstrablyOlder(mailbox, than: cached) {
                return true
            }
        }

        let includedThreadIDs = Array(Set(mailbox.sections.flatMap(\.rows).map(\.threadID)))
        guard !includedThreadIDs.isEmpty else {
            return true
        }
        let generationScope = mailboxGenerationScope(mailbox)
        // Keep each statement comfortably below SQLite's variable limit for
        // very large completed mailboxes.
        for offset in stride(from: 0, to: includedThreadIDs.count, by: 200) {
            let end = min(offset + 200, includedThreadIDs.count)
            let threadIDChunk = Array(includedThreadIDs[offset..<end])
            let placeholders = Array(repeating: "?", count: threadIDChunk.count).joined(separator: ", ")
            var bindings: [Any] = [
                userID,
                label.rawValue,
                generationScope,
                incomingProgressDate.timeIntervalSince1970,
                incomingRevision,
            ]
            bindings.append(contentsOf: threadIDChunk)
            guard execute(
                sql: """
                DELETE FROM mailbox_thread_tombstones
                WHERE user_id = ?
                  AND label = ?
                  AND generation_scope = ?
                  AND progress_at IS NOT NULL
                  AND progress_at < ?
                  AND mailbox_revision IS NOT NULL
                  AND mailbox_revision != ?
                  AND thread_id IN (\(placeholders))
                """,
                bindings: bindings
            ) else {
                return false
            }
        }
        return true
    }

    private func normalizedMailboxRevision(_ revision: String?) -> String? {
        guard let revision = revision?.trimmingCharacters(in: .whitespacesAndNewlines),
              !revision.isEmpty else {
            return nil
        }
        return revision
    }

    private func clearMailboxTombstonesForExplicitReentry(
        _ threadIDs: Set<String>,
        labels: Set<MailboxLabel>,
        mailbox: MailboxResponse,
        userID: String
    ) -> Bool {
        guard !threadIDs.isEmpty, !labels.isEmpty else {
            return true
        }
        let generationScope = mailboxGenerationScope(mailbox)
        for label in labels {
            for threadID in threadIDs {
                guard execute(
                    sql: "DELETE FROM mailbox_thread_tombstones WHERE user_id = ? AND label = ? AND generation_scope = ? AND thread_id = ?",
                    bindings: [userID, label.rawValue, generationScope, threadID]
                ) else {
                    return false
                }
            }
        }
        return true
    }

    private func applyingMailboxTombstones(
        to mailbox: MailboxResponse,
        userID: String,
        label: MailboxLabel
    ) -> MailboxResponse {
        mailbox.removingCachedThreadIDs(
            mailboxTombstoneThreadIDs(
                userID: userID,
                label: label,
                generationScope: mailboxGenerationScope(mailbox)
            )
        )
    }

    private func mailboxTombstoneThreadIDs(
        userID: String,
        label: MailboxLabel,
        generationScope: String
    ) -> Set<String> {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(
            database,
            "SELECT thread_id FROM mailbox_thread_tombstones WHERE user_id = ? AND label = ? AND generation_scope = ?",
            -1,
            &statement,
            nil
        ) == SQLITE_OK else {
            return []
        }
        defer { sqlite3_finalize(statement) }
        bind([userID, label.rawValue, generationScope], to: statement)
        var threadIDs: Set<String> = []
        while sqlite3_step(statement) == SQLITE_ROW {
            if let threadID = sqliteText(statement, 0) {
                threadIDs.insert(threadID)
            }
        }
        return threadIDs
    }

    private func mailboxGenerationScope(_ mailbox: MailboxResponse) -> String {
        if let generation = MailboxResponse.normalizedGeneration(mailbox.syncGeneration) {
            return "generation:\(generation)"
        }
        if let revision = mailbox.mailboxRevision?.trimmingCharacters(in: .whitespacesAndNewlines),
           !revision.isEmpty {
            return "legacy-revision:\(revision)"
        }
        return "legacy-ungenerated"
    }

    private func isRetiredMailboxGeneration(
        _ generation: String?,
        userID: String,
        label: MailboxLabel
    ) -> Bool {
        guard let generation = MailboxResponse.normalizedGeneration(generation) else {
            return false
        }
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(
            database,
            "SELECT 1 FROM retired_mailbox_generations WHERE user_id = ? AND label = ? AND generation = ? LIMIT 1",
            -1,
            &statement,
            nil
        ) == SQLITE_OK else {
            return false
        }
        defer { sqlite3_finalize(statement) }
        bind([userID, label.rawValue, generation], to: statement)
        return sqlite3_step(statement) == SQLITE_ROW
    }

    private func migrate() -> Bool {
        lock.withLock {
            execute(sql: "PRAGMA journal_mode = WAL")
            execute(sql: "PRAGMA secure_delete = ON")
            execute(sql: "PRAGMA foreign_keys = ON")
            execute(
                sql: """
                CREATE TABLE IF NOT EXISTS app_sessions (
                  user_id TEXT PRIMARY KEY,
                  payload BLOB NOT NULL,
                  updated_at REAL NOT NULL
                )
                """
            )
            execute(
                sql: """
                CREATE TABLE IF NOT EXISTS current_session (
                  id INTEGER PRIMARY KEY CHECK (id = 1),
                  user_id TEXT NOT NULL
                )
                """
            )
            execute(
                sql: """
                CREATE TABLE IF NOT EXISTS thread_details (
                  user_id TEXT NOT NULL,
                  thread_id TEXT NOT NULL,
                  payload BLOB NOT NULL,
                  updated_at REAL NOT NULL,
                  PRIMARY KEY (user_id, thread_id)
                )
                """
            )
            execute(
                sql: """
                CREATE TABLE IF NOT EXISTS mailboxes (
                  user_id TEXT NOT NULL,
                  label TEXT NOT NULL,
                  payload BLOB NOT NULL,
                  updated_at REAL NOT NULL,
                  PRIMARY KEY (user_id, label)
                )
                """
            )
            execute(
                sql: """
                CREATE TABLE IF NOT EXISTS retired_mailbox_generations (
                  user_id TEXT NOT NULL,
                  label TEXT NOT NULL,
                  generation TEXT NOT NULL,
                  PRIMARY KEY (user_id, label, generation)
                )
                """
            )
            execute(
                sql: """
                CREATE TABLE IF NOT EXISTS mailbox_thread_tombstones (
                  user_id TEXT NOT NULL,
                  label TEXT NOT NULL,
                  generation_scope TEXT NOT NULL,
                  thread_id TEXT NOT NULL,
                  mailbox_revision TEXT,
                  progress_at REAL,
                  created_at REAL NOT NULL,
                  PRIMARY KEY (user_id, label, generation_scope, thread_id)
                )
                """
            )
            execute(
                sql: """
                CREATE TABLE IF NOT EXISTS pending_thread_actions (
                  client_action_id TEXT PRIMARY KEY,
                  user_id TEXT NOT NULL,
                  mailbox_thread_id TEXT NOT NULL,
                  target_message_id TEXT,
                  action TEXT NOT NULL,
                  state TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at REAL NOT NULL,
                  error TEXT
                )
                """
            )
            if !table("pending_thread_actions", hasColumn: "target_message_id") {
                execute(sql: "ALTER TABLE pending_thread_actions ADD COLUMN target_message_id TEXT")
            }
            if !table("mailbox_thread_tombstones", hasColumn: "progress_at") {
                execute(sql: "ALTER TABLE mailbox_thread_tombstones ADD COLUMN progress_at REAL")
            }
            if !table("mailbox_thread_tombstones", hasColumn: "mailbox_revision") {
                execute(sql: "ALTER TABLE mailbox_thread_tombstones ADD COLUMN mailbox_revision TEXT")
            }
            return true
        }
    }

    private func table(_ tableName: String, hasColumn columnName: String) -> Bool {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(database, "PRAGMA table_info(\(tableName))", -1, &statement, nil) == SQLITE_OK else {
            return false
        }
        defer { sqlite3_finalize(statement) }
        while sqlite3_step(statement) == SQLITE_ROW {
            if sqliteText(statement, 1) == columnName {
                return true
            }
        }
        return false
    }

    private func currentUserID() -> String? {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(database, "SELECT user_id FROM current_session WHERE id = 1", -1, &statement, nil) == SQLITE_OK else {
            return nil
        }
        defer { sqlite3_finalize(statement) }
        guard sqlite3_step(statement) == SQLITE_ROW, let pointer = sqlite3_column_text(statement, 0) else {
            return nil
        }
        return String(cString: pointer)
    }

    private func encryptedThreadPayload(
        _ plaintext: Data,
        userID: String,
        threadID: String
    ) -> Data? {
        do {
            let key = try contentKeyStore.loadOrCreateKey(userID: userID)
            return try EncryptedThreadPayload.seal(
                plaintext,
                userID: userID,
                threadID: threadID,
                key: key
            )
        } catch {
            // Offline storage is an optimization. Keychain, compression, or
            // disk failures must never fail the network request or reader UI.
            return nil
        }
    }

    private func purgeDeletedPages() {
        // A normal DELETE leaves recoverable content in free pages and old WAL
        // frames. Secure deletion overwrites cells, checkpoint truncation
        // removes frames, and VACUUM rebuilds without free pages.
        _ = execute(sql: "PRAGMA wal_checkpoint(TRUNCATE)")
        _ = execute(sql: "VACUUM")
        _ = execute(sql: "PRAGMA wal_checkpoint(TRUNCATE)")
    }

    private func sqliteText(_ statement: OpaquePointer?, _ index: Int32) -> String? {
        guard let pointer = sqlite3_column_text(statement, index) else {
            return nil
        }
        return String(cString: pointer)
    }

    private func readPayload<T: Decodable>(sql: String, bindings: [Any], as type: T.Type) -> T? {
        guard let data = readData(sql: sql, bindings: bindings) else {
            return nil
        }
        return try? JSONDecoder.backend.decode(T.self, from: data)
    }

    private func readData(sql: String, bindings: [Any]) -> Data? {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(database, sql, -1, &statement, nil) == SQLITE_OK else {
            return nil
        }
        defer { sqlite3_finalize(statement) }
        bind(bindings, to: statement)
        guard sqlite3_step(statement) == SQLITE_ROW else {
            return nil
        }
        let bytes = sqlite3_column_blob(statement, 0)
        let count = Int(sqlite3_column_bytes(statement, 0))
        guard let bytes, count > 0 else {
            return nil
        }
        return Data(bytes: bytes, count: count)
    }

    @discardableResult
    private func execute(sql: String, bindings: [Any] = []) -> Bool {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(database, sql, -1, &statement, nil) == SQLITE_OK else {
            return false
        }
        defer { sqlite3_finalize(statement) }
        bind(bindings, to: statement)
        let result = sqlite3_step(statement)
        return result == SQLITE_DONE || result == SQLITE_ROW
    }

    private func bind(_ bindings: [Any], to statement: OpaquePointer?) {
        for (index, value) in bindings.enumerated() {
            let sqliteIndex = Int32(index + 1)
            let binding = SQLiteBinding(value)
            switch binding {
            case .text(let value):
                sqlite3_bind_text(statement, sqliteIndex, value, -1, SQLITE_TRANSIENT_VALUE)
            case .data(let data):
                _ = data.withUnsafeBytes { rawBuffer in
                    sqlite3_bind_blob(statement, sqliteIndex, rawBuffer.baseAddress, Int32(data.count), SQLITE_TRANSIENT_VALUE)
                }
            case .double(let value):
                sqlite3_bind_double(statement, sqliteIndex, value)
            case .null:
                sqlite3_bind_null(statement, sqliteIndex)
            }
        }
    }
}

private enum SQLiteBinding {
    case text(String)
    case data(Data)
    case double(Double)
    case null

    init(_ value: Any) {
        if let string = value as? String {
            self = .text(string)
        } else if let data = value as? Data {
            self = .data(data)
        } else if let double = value as? Double {
            self = .double(double)
        } else {
            self = .null
        }
    }
}

private let SQLITE_TRANSIENT_VALUE = unsafeBitCast(-1, to: sqlite3_destructor_type.self)

private extension AppSessionResponse {
    func replacingMailboxForCache(_ mailbox: MailboxResponse) -> AppSessionResponse {
        AppSessionResponse(
            user: user,
            readiness: readiness,
            dashboard: dashboard,
            mailbox: mailbox,
            sync: sync
        )
    }
}

private extension MailboxResponse {
    /// A mailbox row is an accumulated offline snapshot, even when its latest
    /// network source was only the first transport page. Within one sync
    /// generation, prefer the fresher copy of duplicate rows and retain every
    /// already-persisted page. A generation change or a completed authoritative
    /// reconciliation is the boundary at which replacing rows wholesale is safe.
    static func cacheSafeSnapshot(
        cached: MailboxResponse,
        incoming: MailboxResponse
    ) -> MailboxResponse {
        guard cached.label == incoming.label else {
            return incoming
        }

        let cachedGeneration = normalizedGeneration(cached.syncGeneration)
        let incomingGeneration = normalizedGeneration(incoming.syncGeneration)
        guard cachedGeneration == incomingGeneration else {
            // A response without a generation cannot supersede a cache that is
            // already fenced to one. This also prevents legacy/partial payloads
            // arriving late from erasing verified progressive-sync pages.
            guard incomingGeneration != nil else {
                return cached
            }

            // UUID generations are opaque and therefore not orderable. Server
            // progress timestamps provide a fence when both snapshots have one;
            // absent contrary evidence, the newly written generation is the
            // current one and may replace atomically.
            if cachedGeneration != nil, isDemonstrablyOlder(incoming, than: cached) {
                return cached
            }
            return incoming
        }

        if incoming.isAuthoritativeCacheSnapshot {
            guard !isDemonstrablyOlder(incoming, than: cached) else {
                return cached
            }
            return incoming.withMonotonicProgress(from: cached)
        }

        return mergingSameGeneration(cached: cached, incoming: incoming)
    }

    var isAuthoritativeCacheSnapshot: Bool {
        // While a progressive generation is still scanning Gmail, an empty or
        // shorter label page describes only the rows imported so far. Absence
        // becomes authoritative only after metadata reconciliation completes.
        if Self.normalizedGeneration(syncGeneration) != nil,
           historyMetadataComplete != true {
            return false
        }
        guard fullImportRunning != true,
              fullImportCompleted != false,
              nextCursor?.isEmpty != false else {
            return false
        }
        let visibleRows = sections.reduce(0) { $0 + $1.rows.count }
        let loadedRows = max(loadedThreads ?? visibleRows, visibleRows)
        return visibleRows >= totalThreads && loadedRows >= totalThreads
    }

    func withMonotonicProgress(from cached: MailboxResponse) -> MailboxResponse {
        MailboxResponse(
            label: label,
            totalThreads: totalThreads,
            unreadThreads: unreadThreads,
            nextCursor: nextCursor,
            loadedThreads: loadedThreads,
            windowDays: windowDays,
            sections: sections,
            readyCount: Self.monotonicMax(cached.readyCount, readyCount),
            pendingCount: pendingCount,
            mailboxRevision: mailboxRevision,
            generatedAt: Self.latestTimestampValue(cached.generatedAt, generatedAt),
            oldestImportedAt: Self.earliestTimestampValue(
                cached.oldestImportedAt,
                oldestImportedAt,
                fallback: oldestImportedAt
            ),
            fullImportRunning: Self.monotonicCompletion(cached.fullImportCompleted, fullImportCompleted) == true
                ? false
                : fullImportRunning,
            fullImportCompleted: Self.monotonicCompletion(cached.fullImportCompleted, fullImportCompleted),
            syncGeneration: syncGeneration,
            phase: phase,
            initialTargetCount: Self.monotonicMax(cached.initialTargetCount, initialTargetCount),
            initialMetadataCount: Self.monotonicMax(cached.initialMetadataCount, initialMetadataCount),
            initialBodyTargetCount: Self.monotonicMax(cached.initialBodyTargetCount, initialBodyTargetCount),
            initialBodyReadyCount: Self.monotonicMax(cached.initialBodyReadyCount, initialBodyReadyCount),
            historyMetadataCount: Self.monotonicMax(cached.historyMetadataCount, historyMetadataCount),
            historyBodyReadyCount: Self.monotonicMax(cached.historyBodyReadyCount, historyBodyReadyCount),
            estimatedTotalCount: Self.monotonicMax(cached.estimatedTotalCount, estimatedTotalCount),
            initialWindowComplete: Self.monotonicCompletion(cached.initialWindowComplete, initialWindowComplete),
            historyMetadataComplete: Self.monotonicCompletion(cached.historyMetadataComplete, historyMetadataComplete),
            historyBodyComplete: Self.monotonicCompletion(cached.historyBodyComplete, historyBodyComplete),
            lastProgressAt: Self.latestTimestampValue(cached.lastProgressAt, lastProgressAt)
        )
    }

    func inheritingCacheIdentityIfNeeded(from cached: MailboxResponse?) -> MailboxResponse {
        guard let cached, cached.label == label else {
            return self
        }
        return MailboxResponse(
            label: label,
            totalThreads: totalThreads,
            unreadThreads: unreadThreads,
            nextCursor: nextCursor,
            loadedThreads: loadedThreads,
            windowDays: windowDays,
            sections: sections,
            readyCount: readyCount ?? cached.readyCount,
            pendingCount: pendingCount ?? cached.pendingCount,
            mailboxRevision: mailboxRevision ?? cached.mailboxRevision,
            generatedAt: generatedAt ?? cached.generatedAt,
            oldestImportedAt: oldestImportedAt ?? cached.oldestImportedAt,
            fullImportRunning: fullImportRunning ?? cached.fullImportRunning,
            fullImportCompleted: fullImportCompleted ?? cached.fullImportCompleted,
            syncGeneration: syncGeneration ?? cached.syncGeneration,
            phase: phase ?? cached.phase,
            initialTargetCount: initialTargetCount ?? cached.initialTargetCount,
            initialMetadataCount: initialMetadataCount ?? cached.initialMetadataCount,
            initialBodyTargetCount: initialBodyTargetCount ?? cached.initialBodyTargetCount,
            initialBodyReadyCount: initialBodyReadyCount ?? cached.initialBodyReadyCount,
            historyMetadataCount: historyMetadataCount ?? cached.historyMetadataCount,
            historyBodyReadyCount: historyBodyReadyCount ?? cached.historyBodyReadyCount,
            estimatedTotalCount: estimatedTotalCount ?? cached.estimatedTotalCount,
            initialWindowComplete: initialWindowComplete ?? cached.initialWindowComplete,
            historyMetadataComplete: historyMetadataComplete ?? cached.historyMetadataComplete,
            historyBodyComplete: historyBodyComplete ?? cached.historyBodyComplete,
            lastProgressAt: lastProgressAt ?? cached.lastProgressAt
        )
    }

    func withMailboxCounts(
        totalThreads: Int,
        unreadThreads: Int?,
        loadedThreads: Int
    ) -> MailboxResponse {
        MailboxResponse(
            label: label,
            totalThreads: totalThreads,
            unreadThreads: unreadThreads,
            nextCursor: nextCursor,
            loadedThreads: loadedThreads,
            windowDays: windowDays,
            sections: sections,
            readyCount: readyCount,
            pendingCount: pendingCount,
            mailboxRevision: mailboxRevision,
            generatedAt: generatedAt,
            oldestImportedAt: oldestImportedAt,
            fullImportRunning: fullImportRunning,
            fullImportCompleted: fullImportCompleted,
            syncGeneration: syncGeneration,
            phase: phase,
            initialTargetCount: initialTargetCount,
            initialMetadataCount: initialMetadataCount,
            initialBodyTargetCount: initialBodyTargetCount,
            initialBodyReadyCount: initialBodyReadyCount,
            historyMetadataCount: historyMetadataCount,
            historyBodyReadyCount: historyBodyReadyCount,
            estimatedTotalCount: estimatedTotalCount,
            initialWindowComplete: initialWindowComplete,
            historyMetadataComplete: historyMetadataComplete,
            historyBodyComplete: historyBodyComplete,
            lastProgressAt: lastProgressAt
        )
    }

    static func mergingSameGeneration(
        cached: MailboxResponse,
        incoming: MailboxResponse
    ) -> MailboxResponse {
        let incomingIsOlder = isDemonstrablyOlder(incoming, than: cached)
        let primary = incomingIsOlder ? cached : incoming
        let secondary = incomingIsOlder ? incoming : cached
        let sections = mergedSections(primary: primary.sections, secondary: secondary.sections)
        let visibleRows = sections.reduce(0) { $0 + $1.rows.count }
        let cachedVisibleRows = cached.sections.reduce(0) { $0 + $1.rows.count }
        let incomingVisibleRows = incoming.sections.reduce(0) { $0 + $1.rows.count }
        let cachedLoaded = max(cached.loadedThreads ?? cachedVisibleRows, cachedVisibleRows)
        let incomingLoaded = max(incoming.loadedThreads ?? incomingVisibleRows, incomingVisibleRows)
        let loadedThreads = max(visibleRows, cachedLoaded, incomingLoaded)
        let totalThreads = max(cached.totalThreads, incoming.totalThreads, loadedThreads)
        let cachedIsDeeper = cachedLoaded > incomingLoaded
            || (cachedLoaded == incomingLoaded && cachedVisibleRows > incomingVisibleRows)
        let incomingIsDeeper = incomingLoaded > cachedLoaded
            || (incomingLoaded == cachedLoaded && incomingVisibleRows > cachedVisibleRows)
        let cursorSource = cachedIsDeeper ? cached : (incomingIsDeeper ? incoming : primary)
        let fullImportCompleted = monotonicCompletion(
            cached.fullImportCompleted,
            incoming.fullImportCompleted
        )
        let preferred = incomingIsOlder ? cached : incoming

        return MailboxResponse(
            label: preferred.label,
            totalThreads: totalThreads,
            unreadThreads: preferred.unreadThreads ?? secondary.unreadThreads,
            nextCursor: cursorSource.nextCursor,
            loadedThreads: loadedThreads,
            windowDays: preferred.windowDays ?? secondary.windowDays,
            sections: sections,
            readyCount: monotonicMax(cached.readyCount, incoming.readyCount),
            pendingCount: preferred.pendingCount ?? secondary.pendingCount,
            mailboxRevision: preferred.mailboxRevision ?? secondary.mailboxRevision,
            generatedAt: latestTimestampValue(cached.generatedAt, incoming.generatedAt),
            oldestImportedAt: earliestTimestampValue(
                cached.oldestImportedAt,
                incoming.oldestImportedAt,
                fallback: cachedIsDeeper
                    ? cached.oldestImportedAt
                    : (incomingIsDeeper ? incoming.oldestImportedAt : preferred.oldestImportedAt)
            ),
            fullImportRunning: fullImportCompleted == true
                ? false
                : (preferred.fullImportRunning ?? secondary.fullImportRunning),
            fullImportCompleted: fullImportCompleted,
            syncGeneration: preferred.syncGeneration ?? secondary.syncGeneration,
            phase: preferred.phase ?? secondary.phase,
            initialTargetCount: monotonicMax(cached.initialTargetCount, incoming.initialTargetCount),
            initialMetadataCount: monotonicMax(cached.initialMetadataCount, incoming.initialMetadataCount),
            initialBodyTargetCount: monotonicMax(cached.initialBodyTargetCount, incoming.initialBodyTargetCount),
            initialBodyReadyCount: monotonicMax(cached.initialBodyReadyCount, incoming.initialBodyReadyCount),
            historyMetadataCount: monotonicMax(cached.historyMetadataCount, incoming.historyMetadataCount),
            historyBodyReadyCount: monotonicMax(cached.historyBodyReadyCount, incoming.historyBodyReadyCount),
            estimatedTotalCount: monotonicMax(cached.estimatedTotalCount, incoming.estimatedTotalCount),
            initialWindowComplete: monotonicCompletion(cached.initialWindowComplete, incoming.initialWindowComplete),
            historyMetadataComplete: monotonicCompletion(cached.historyMetadataComplete, incoming.historyMetadataComplete),
            historyBodyComplete: monotonicCompletion(cached.historyBodyComplete, incoming.historyBodyComplete),
            lastProgressAt: latestTimestampValue(cached.lastProgressAt, incoming.lastProgressAt)
        )
    }

    static func mergedSections(
        primary: [GmailThreadSection],
        secondary: [GmailThreadSection]
    ) -> [GmailThreadSection] {
        var sectionOrder: [String] = []
        var sectionTitles: [String: String] = [:]
        var rowsBySection: [String: [GmailThreadRow]] = [:]
        var seenThreadIDs: Set<String> = []

        func append(_ source: [GmailThreadSection]) {
            for section in source {
                if rowsBySection[section.id] == nil {
                    sectionOrder.append(section.id)
                    sectionTitles[section.id] = section.title
                    rowsBySection[section.id] = []
                }
                for row in section.rows where seenThreadIDs.insert(row.threadID).inserted {
                    rowsBySection[section.id, default: []].append(row)
                }
            }
        }

        append(primary)
        append(secondary)
        return sectionOrder.map {
            GmailThreadSection(
                id: $0,
                title: sectionTitles[$0] ?? "",
                rows: rowsBySection[$0] ?? []
            )
        }
    }

    static func normalizedGeneration(_ generation: String?) -> String? {
        guard let value = generation?.trimmingCharacters(in: .whitespacesAndNewlines),
              !value.isEmpty else {
            return nil
        }
        return value
    }

    static func isDemonstrablyOlder(
        _ candidate: MailboxResponse,
        than reference: MailboxResponse
    ) -> Bool {
        guard let candidateDate = progressDate(candidate),
              let referenceDate = progressDate(reference) else {
            return false
        }
        return candidateDate < referenceDate
    }

    static func progressDate(_ mailbox: MailboxResponse) -> Date? {
        switch (timestampDate(mailbox.lastProgressAt), timestampDate(mailbox.generatedAt)) {
        case let (progressDate?, generatedDate?): max(progressDate, generatedDate)
        case let (progressDate?, nil): progressDate
        case let (nil, generatedDate?): generatedDate
        case (nil, nil): nil
        }
    }

    static func timestampDate(_ value: String?) -> Date? {
        guard let value, !value.isEmpty else {
            return nil
        }
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = fractional.date(from: value) {
            return date
        }
        return ISO8601DateFormatter().date(from: value)
    }

    static func latestTimestampValue(_ lhs: String?, _ rhs: String?) -> String? {
        switch (timestampDate(lhs), timestampDate(rhs)) {
        case let (lhsDate?, rhsDate?): rhsDate >= lhsDate ? rhs : lhs
        case (_?, nil): lhs
        case (nil, _?): rhs
        case (nil, nil): rhs ?? lhs
        }
    }

    static func earliestTimestampValue(
        _ lhs: String?,
        _ rhs: String?,
        fallback: String?
    ) -> String? {
        switch (timestampDate(lhs), timestampDate(rhs)) {
        case let (lhsDate?, rhsDate?): rhsDate < lhsDate ? rhs : lhs
        case (_?, nil): lhs
        case (nil, _?): rhs
        case (nil, nil): fallback ?? rhs ?? lhs
        }
    }

    static func monotonicMax(_ lhs: Int?, _ rhs: Int?) -> Int? {
        switch (lhs, rhs) {
        case let (lhs?, rhs?): max(lhs, rhs)
        case let (lhs?, nil): lhs
        case let (nil, rhs?): rhs
        case (nil, nil): nil
        }
    }

    static func monotonicCompletion(_ lhs: Bool?, _ rhs: Bool?) -> Bool? {
        if lhs == true || rhs == true {
            return true
        }
        return rhs ?? lhs
    }
}

private extension NSLock {
    func withLock<T>(_ body: () -> T) -> T {
        lock()
        defer { unlock() }
        return body()
    }
}
