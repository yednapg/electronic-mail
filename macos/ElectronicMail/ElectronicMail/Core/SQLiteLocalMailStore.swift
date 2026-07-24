import Foundation
import SQLite3

public final class SQLiteLocalMailStore: LocalMailStore {
    private let databaseURL: URL
    private let lock = NSLock()
    private var database: OpaquePointer?

    public convenience init?() {
        guard let supportURL = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first else {
            return nil
        }
        let directory = supportURL.appendingPathComponent("ElectronicMail", isDirectory: true)
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        self.init(databaseURL: directory.appendingPathComponent("LocalMail.sqlite3"))
    }

    public init?(databaseURL: URL) {
        self.databaseURL = databaseURL
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
        guard let payload = try? JSONEncoder.backend.encode(session) else {
            return
        }
        lock.withLock {
            _ = execute(
                sql: """
                INSERT INTO app_sessions (user_id, payload, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET payload = excluded.payload, updated_at = excluded.updated_at
                """,
                bindings: [session.user.id, payload, Date().timeIntervalSince1970]
            )
            _ = execute(
                sql: """
                INSERT INTO current_session (id, user_id)
                VALUES (1, ?)
                ON CONFLICT(id) DO UPDATE SET user_id = excluded.user_id
                """,
                bindings: [session.user.id]
            )
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
        guard mailbox.label == label else {
            return
        }
        guard let payload = try? JSONEncoder.backend.encode(mailbox) else {
            return
        }
        lock.withLock {
            _ = execute(
                sql: """
                INSERT INTO mailboxes (user_id, label, payload, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, label) DO UPDATE SET payload = excluded.payload, updated_at = excluded.updated_at
                """,
                bindings: [userID, label.rawValue, payload, Date().timeIntervalSince1970]
            )
        }
    }

    public func readThread(userID: String, threadID: String) -> ThreadReaderResponse? {
        lock.withLock {
            readPayload(
                sql: "SELECT payload FROM thread_details WHERE user_id = ? AND thread_id = ?",
                bindings: [userID, threadID],
                as: ThreadReaderResponse.self
            )
        }
    }

    public func writeThread(_ thread: ThreadReaderResponse, userID: String, threadID: String) {
        guard let payload = try? JSONEncoder.backend.encode(thread) else {
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

    public func clearAll() {
        lock.withLock {
            _ = execute(sql: "PRAGMA secure_delete = ON")

            guard execute(sql: "BEGIN IMMEDIATE") else {
                return
            }

            let didDeleteEverything = [
                "DELETE FROM current_session",
                "DELETE FROM app_sessions",
                "DELETE FROM mailboxes",
                "DELETE FROM thread_details",
                "DELETE FROM pending_thread_actions",
            ].allSatisfy { execute(sql: $0) }

            guard didDeleteEverything, execute(sql: "COMMIT") else {
                _ = execute(sql: "ROLLBACK")
                return
            }

            // A normal DELETE leaves recoverable content in free pages and old WAL
            // frames. Secure deletion overwrites the cells, checkpoint truncation
            // removes the frames, and VACUUM rebuilds the database without free pages.
            _ = execute(sql: "PRAGMA wal_checkpoint(TRUNCATE)")
            _ = execute(sql: "VACUUM")
            _ = execute(sql: "PRAGMA wal_checkpoint(TRUNCATE)")
        }
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

    private func sqliteText(_ statement: OpaquePointer?, _ index: Int32) -> String? {
        guard let pointer = sqlite3_column_text(statement, index) else {
            return nil
        }
        return String(cString: pointer)
    }

    private func readPayload<T: Decodable>(sql: String, bindings: [Any], as type: T.Type) -> T? {
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
        let data = Data(bytes: bytes, count: count)
        return try? JSONDecoder.backend.decode(T.self, from: data)
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

private extension NSLock {
    func withLock<T>(_ body: () -> T) -> T {
        lock()
        defer { unlock() }
        return body()
    }
}
