import Foundation
import Security

public protocol SessionTokenStoring: AnyObject {
    func load() -> String?
    func save(_ token: String) throws
    func clear()

    /// Generation-aware stores persist this nonsecret intent before any
    /// potentially blocking credential operation begins. Legacy stores return
    /// `nil` and retain their original behavior.
    func prepareMutation(_ kind: SessionTokenMutationKind) throws -> SessionTokenMutation?
    func save(_ token: String, for mutation: SessionTokenMutation?) throws
    func clear(for mutation: SessionTokenMutation?)
}

public extension SessionTokenStoring {
    func prepareMutation(_: SessionTokenMutationKind) throws -> SessionTokenMutation? {
        nil
    }

    func save(_ token: String, for _: SessionTokenMutation?) throws {
        try save(token)
    }

    func clear(for _: SessionTokenMutation?) {
        clear()
    }
}

public enum SessionTokenMutationKind: String, Codable, Equatable, Sendable {
    case token
    case tombstone
}

public struct SessionTokenMutation: Codable, Equatable, Sendable {
    public let generationID: String
    public let ordinal: UInt64
    public let kind: SessionTokenMutationKind

    init(generationID: String, ordinal: UInt64, kind: SessionTokenMutationKind) {
        self.generationID = generationID
        self.ordinal = ordinal
        self.kind = kind
    }
}

public enum SessionTokenLoadResult: Equatable, Sendable {
    case loaded(String?)
    case timedOut
}

public enum SessionTokenSaveResult: Equatable, Sendable {
    case saved
    case failed(String)
    case timedOut
}

public enum SessionTokenClearResult: Equatable, Sendable {
    case cleared
    case failed(String)
    case timedOut
}

/// A durably recorded sign-out intent whose credential-store work has not
/// necessarily started yet. Creating this value only updates the nonsecret
/// generation ledger; callers may therefore prepare it synchronously before
/// exposing signed-out UI without invoking Security.framework on that actor.
public struct PreparedSessionTokenClearIntent: Sendable {
    fileprivate let mutation: SessionTokenMutation?

    fileprivate init(mutation: SessionTokenMutation?) {
        self.mutation = mutation
    }
}

/// Runs synchronous credential-store operations away from UI actors and races
/// them against an independent timeout queue. Security.framework calls cannot
/// be cancelled, so this intentionally does not use a task group: a blocked
/// operation is allowed to finish later while its one-shot result is discarded.
public struct AsyncSessionTokenStore: Sendable {
    private static let timeoutQueue = DispatchQueue(
        label: "app.electronicmail.session-token.timeout",
        qos: .userInitiated
    )

    /// Credential operations run concurrently so a Security.framework call
    /// which never returns cannot poison this client-side execution lane.
    /// Generation-aware stores write only immutable per-request records, so a
    /// late operation cannot overwrite or delete a newer credential.
    private let operationQueue: DispatchQueue
    private let requestEpoch: SessionTokenRequestEpoch
    private let prepareMutationOperation: @Sendable (SessionTokenMutationKind) throws -> SessionTokenMutation?
    private let loadOperation: @Sendable () -> String?
    private let saveOperation: @Sendable (String, SessionTokenMutation?) -> SessionTokenSaveResult
    private let clearOperation: @Sendable (SessionTokenMutation?) -> Void

    public init<Store: SessionTokenStoring & Sendable>(store: Store) {
        operationQueue = DispatchQueue(
            label: "app.electronicmail.session-token.\(UUID().uuidString)",
            qos: .userInitiated,
            attributes: .concurrent
        )
        requestEpoch = SessionTokenRequestEpoch()
        prepareMutationOperation = { kind in try store.prepareMutation(kind) }
        loadOperation = { store.load() }
        saveOperation = { token, mutation in
            do {
                try store.save(token, for: mutation)
                return .saved
            } catch {
                return .failed(error.localizedDescription)
            }
        }
        clearOperation = { mutation in store.clear(for: mutation) }
    }

    public func load(timeout: TimeInterval) async -> SessionTokenLoadResult {
        let epoch = requestEpoch.current()
        return await execute(timeout: timeout, timeoutResult: .timedOut) {
            let token = loadOperation()
            return requestEpoch.isCurrent(epoch) ? .loaded(token) : .timedOut
        }
    }

    /// Gives a transiently busy credential service one fresh concurrent read
    /// before the caller interrupts launch with recovery UI. A timed-out
    /// Security.framework call cannot be cancelled, so the retry deliberately
    /// uses the store's concurrent operation lane instead of waiting behind it.
    public func loadWithRetry(
        initialTimeout: TimeInterval,
        retryTimeout: TimeInterval
    ) async -> SessionTokenLoadResult {
        let initialResult = await load(timeout: initialTimeout)
        guard initialResult == .timedOut, !Task.isCancelled else {
            return initialResult
        }
        return await load(timeout: retryTimeout)
    }

    public func save(_ token: String, timeout: TimeInterval) async -> SessionTokenSaveResult {
        requestEpoch.advance()
        let mutation: SessionTokenMutation?
        do {
            mutation = try prepareMutationOperation(.token)
        } catch {
            return .failed(error.localizedDescription)
        }
        return await execute(timeout: timeout, timeoutResult: .timedOut) {
            saveOperation(token, mutation)
        }
    }

    public func clear(timeout: TimeInterval) async -> SessionTokenClearResult {
        let intent: PreparedSessionTokenClearIntent
        do {
            intent = try prepareClearIntent()
        } catch {
            return .failed(error.localizedDescription)
        }
        return await clear(preparedIntent: intent, timeout: timeout)
    }

    /// Publishes the next desired generation before any asynchronous
    /// credential operation is launched. Generation-aware stores use this as
    /// the crash-safe boundary for sign-out, disconnect, and account deletion.
    public func prepareClearIntent() throws -> PreparedSessionTokenClearIntent {
        requestEpoch.advance()
        let mutation = try prepareMutationOperation(.tombstone)
        return PreparedSessionTokenClearIntent(mutation: mutation)
    }

    /// Completes a previously published sign-out intent away from UI actors.
    /// A newer token generation remains authoritative even if this immutable
    /// tombstone record finishes later.
    public func clear(
        preparedIntent: PreparedSessionTokenClearIntent,
        timeout: TimeInterval
    ) async -> SessionTokenClearResult {
        return await execute(timeout: timeout, timeoutResult: .timedOut) {
            clearOperation(preparedIntent.mutation)
            return .cleared
        }
    }

    private func execute<Result: Sendable>(
        timeout: TimeInterval,
        timeoutResult: Result,
        operation: @escaping @Sendable () -> Result
    ) async -> Result {
        let oneShot = SessionTokenOperationOneShot<Result>()
        operationQueue.async {
            oneShot.resolve(operation())
        }
        Self.timeoutQueue.asyncAfter(deadline: .now() + max(0, timeout)) {
            oneShot.resolve(timeoutResult)
        }
        return await oneShot.value()
    }
}

private final class SessionTokenRequestEpoch: @unchecked Sendable {
    private let lock = NSLock()
    private var value: UInt64 = 0

    func current() -> UInt64 {
        lock.lock()
        defer { lock.unlock() }
        return value
    }

    func advance() {
        lock.lock()
        value &+= 1
        lock.unlock()
    }

    func isCurrent(_ candidate: UInt64) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        return value == candidate
    }
}

private final class SessionTokenOperationOneShot<Value: Sendable>: @unchecked Sendable {
    private enum State {
        case pending
        case waiting(CheckedContinuation<Value, Never>)
        case resolved(Value)
    }

    private let lock = NSLock()
    private var state: State = .pending

    func value() async -> Value {
        await withCheckedContinuation { continuation in
            lock.lock()
            switch state {
            case .pending:
                state = .waiting(continuation)
                lock.unlock()
            case .resolved(let value):
                lock.unlock()
                continuation.resume(returning: value)
            case .waiting:
                lock.unlock()
                preconditionFailure("A session-token operation may only be awaited once")
            }
        }
    }

    func resolve(_ value: Value) {
        let continuation: CheckedContinuation<Value, Never>?
        lock.lock()
        switch state {
        case .pending:
            state = .resolved(value)
            continuation = nil
        case .waiting(let waiting):
            state = .resolved(value)
            continuation = waiting
        case .resolved:
            continuation = nil
        }
        lock.unlock()
        continuation?.resume(returning: value)
    }
}

public final class UserDefaultsSessionTokenStore: SessionTokenStoring, @unchecked Sendable {
    private let defaults: UserDefaults
    private let key: String

    public init(
        defaults: UserDefaults = .standard,
        key: String = "ElectronicMail.debug.email_session"
    ) {
        self.defaults = defaults
        self.key = key
    }

    public func load() -> String? {
        guard let token = defaults.string(forKey: key)?.trimmingCharacters(in: .whitespacesAndNewlines),
              !token.isEmpty else {
            return nil
        }
        return token
    }

    public func save(_ token: String) throws {
        defaults.set(token, forKey: key)
    }

    public func clear() {
        defaults.removeObject(forKey: key)
    }
}

private struct SessionTokenLedgerEntry: Codable, Equatable, Sendable {
    let mutation: SessionTokenMutation
    var recordPersisted: Bool
}

private struct SessionTokenLedgerState: Codable, Equatable, Sendable {
    var nextOrdinal: UInt64 = 0
    var desired: SessionTokenMutation?
    var committedGenerationID: String?
    var entries: [SessionTokenLedgerEntry] = []
}

private enum SessionTokenLedgerSnapshot: Sendable {
    case absent
    case corrupt
    case available(SessionTokenLedgerState)
}

struct SessionTokenGenerationRecord: Equatable, Sendable {
    let mutation: SessionTokenMutation
    let data: Data
}

private enum LocalGenerationRecoveryResult {
    case token(String)
    case tombstone
    case retry
}

private final class SessionTokenGenerationLedger: @unchecked Sendable {
    private static let lock = NSLock()
    private static let storageKey = "ElectronicMail.session-token.v2.generation-ledger"

    private let defaults: UserDefaults

    init(defaults: UserDefaults) {
        self.defaults = defaults
    }

    func allocate(_ kind: SessionTokenMutationKind) throws -> SessionTokenMutation {
        try Self.withLock {
            var state: SessionTokenLedgerState
            switch snapshotLocked() {
            case .available(let existing):
                state = existing
            case .absent, .corrupt:
                // An explicit user mutation may recover corrupt nonsecret
                // metadata. The UUID account keeps it disjoint from any orphan.
                state = SessionTokenLedgerState()
            }
            let highestOrdinal = max(
                state.nextOrdinal,
                state.entries.map(\.mutation.ordinal).max() ?? 0
            )
            guard highestOrdinal < UInt64.max else {
                throw SessionTokenLedgerError.generationExhausted
            }
            let mutation = SessionTokenMutation(
                generationID: UUID().uuidString.lowercased(),
                ordinal: highestOrdinal + 1,
                kind: kind
            )
            state.nextOrdinal = mutation.ordinal
            state.desired = mutation
            state.entries.append(SessionTokenLedgerEntry(mutation: mutation, recordPersisted: false))
            try persistLocked(state)
            return mutation
        }
    }

    func allocateLegacyMigrationIfUnclaimed() throws -> SessionTokenMutation? {
        try Self.withLock {
            switch snapshotLocked() {
            case .corrupt:
                return nil
            case .available(let existing) where existing.desired != nil:
                return nil
            case .absent, .available:
                var state: SessionTokenLedgerState
                if case .available(let existing) = snapshotLocked() {
                    state = existing
                } else {
                    state = SessionTokenLedgerState()
                }
                let highestOrdinal = max(
                    state.nextOrdinal,
                    state.entries.map(\.mutation.ordinal).max() ?? 0
                )
                guard highestOrdinal < UInt64.max else {
                    throw SessionTokenLedgerError.generationExhausted
                }
                let mutation = SessionTokenMutation(
                    generationID: UUID().uuidString.lowercased(),
                    ordinal: highestOrdinal + 1,
                    kind: .token
                )
                state.nextOrdinal = mutation.ordinal
                state.desired = mutation
                state.entries.append(SessionTokenLedgerEntry(mutation: mutation, recordPersisted: false))
                try persistLocked(state)
                return mutation
            }
        }
    }

    func recoverCommittedIfUnclaimed(_ mutation: SessionTokenMutation) throws -> Bool {
        try Self.withLock {
            guard case .absent = snapshotLocked() else {
                return false
            }
            let state = SessionTokenLedgerState(
                nextOrdinal: mutation.ordinal,
                desired: mutation,
                committedGenerationID: mutation.generationID,
                entries: [SessionTokenLedgerEntry(mutation: mutation, recordPersisted: true)]
            )
            try persistLocked(state)
            return true
        }
    }

    func replaceWithCommittedForExplicitLocalRepair(_ mutation: SessionTokenMutation) throws {
        try Self.withLock {
            let state = SessionTokenLedgerState(
                nextOrdinal: mutation.ordinal,
                desired: mutation,
                committedGenerationID: mutation.generationID,
                entries: [SessionTokenLedgerEntry(mutation: mutation, recordPersisted: true)]
            )
            try persistLocked(state)
        }
    }

    func snapshot() -> SessionTokenLedgerSnapshot {
        Self.withLock { snapshotLocked() }
    }

    func isDesired(_ mutation: SessionTokenMutation) -> Bool {
        Self.withLock {
            guard case .available(let state) = snapshotLocked() else {
                return false
            }
            return state.desired == mutation
        }
    }

    func containsAllocated(_ mutation: SessionTokenMutation) -> Bool {
        Self.withLock {
            guard case .available(let state) = snapshotLocked() else {
                return false
            }
            return state.entries.contains { $0.mutation == mutation }
        }
    }

    @discardableResult
    func markRecordPersisted(_ mutation: SessionTokenMutation) throws -> Bool {
        try Self.withLock {
            guard case .available(var state) = snapshotLocked(),
                  let index = state.entries.firstIndex(where: {
                      $0.mutation == mutation
                  }) else {
                return false
            }
            state.entries[index].recordPersisted = true
            let isDesired = state.desired == mutation
            if isDesired {
                state.committedGenerationID = mutation.generationID
            }
            try persistLocked(state)
            return isDesired
        }
    }

    /// Returns a cleanup plan from one ledger snapshot only after the newest
    /// desired record is durable. A later allocation cannot add that former
    /// desired record to this already-captured older-only plan.
    func cleanupPlan() -> [SessionTokenMutation]? {
        Self.withLock {
            guard case .available(let state) = snapshotLocked(), let desired = state.desired else {
                return nil
            }
            guard state.entries.contains(where: {
                $0.mutation == desired && $0.recordPersisted
            }) else {
                return nil
            }
            return state.entries.compactMap { entry in
                guard entry.recordPersisted, entry.mutation.ordinal < desired.ordinal else {
                    return nil
                }
                return entry.mutation
            }
        }
    }

    func removeCleanedEntries(generationIDs: Set<String>) throws {
        guard !generationIDs.isEmpty else {
            return
        }
        try Self.withLock {
            guard case .available(var state) = snapshotLocked() else {
                return
            }
            state.entries.removeAll { generationIDs.contains($0.mutation.generationID) }
            try persistLocked(state)
        }
    }

    private func snapshotLocked() -> SessionTokenLedgerSnapshot {
        guard let data = defaults.data(forKey: Self.storageKey) else {
            return .absent
        }
        guard let state = try? JSONDecoder().decode(SessionTokenLedgerState.self, from: data) else {
            return .corrupt
        }
        let uniqueGenerationIDs = Set(state.entries.map(\.mutation.generationID))
        let uniqueOrdinals = Set(state.entries.map(\.mutation.ordinal))
        guard uniqueGenerationIDs.count == state.entries.count,
              uniqueOrdinals.count == state.entries.count,
              state.nextOrdinal >= (state.entries.map(\.mutation.ordinal).max() ?? 0),
              state.desired.map({ desired in state.entries.contains { $0.mutation == desired } }) ?? true,
              state.committedGenerationID.map({ committed in
                  state.entries.contains {
                      $0.mutation.generationID == committed && $0.recordPersisted
                  }
              }) ?? true else {
            return .corrupt
        }
        return .available(state)
    }

    private func persistLocked(_ state: SessionTokenLedgerState) throws {
        let data = try JSONEncoder().encode(state)
        defaults.set(data, forKey: Self.storageKey)
        guard defaults.synchronize() else {
            throw SessionTokenLedgerError.persistenceFailed
        }
    }

    private static func withLock<Value>(_ operation: () throws -> Value) rethrows -> Value {
        lock.lock()
        defer { lock.unlock() }
        return try operation()
    }
}

private enum SessionTokenLedgerError: LocalizedError {
    case generationExhausted
    case persistenceFailed

    var errorDescription: String? {
        switch self {
        case .generationExhausted:
            return "Electronic Mail could not allocate a new secure sign-in generation."
        case .persistenceFailed:
            return "Electronic Mail could not durably record the secure sign-in change."
        }
    }
}

protocol SessionTokenSecureRecordStoring: AnyObject, Sendable {
    func readGenerationRecord(account: String) -> Data?
    func addGenerationRecord(_ data: Data, account: String) throws
    func deleteGenerationRecord(account: String) -> Bool
    func readMigratableGenerationRecord(account: String) throws -> Data?
    func deleteMigratableGenerationRecord(account: String) -> Bool
    func latestLocalRecoveryRecord() -> SessionTokenGenerationRecord?
    func latestLocalTokenRecoveryRecord() -> SessionTokenGenerationRecord?
    var lastLocalRecoveryStatus: OSStatus? { get }
    func readLegacyRecord() -> Data?
    func deleteLegacyRecords()
}

extension SessionTokenSecureRecordStoring {
    var lastLocalRecoveryStatus: OSStatus? { nil }

    func latestLocalRecoveryRecord() -> SessionTokenGenerationRecord? {
        nil
    }

    func latestLocalTokenRecoveryRecord() -> SessionTokenGenerationRecord? {
        guard let record = latestLocalRecoveryRecord(), record.mutation.kind == .token else {
            return nil
        }
        return record
    }
}

extension SessionTokenSecureRecordStoring {
    func readMigratableGenerationRecord(account _: String) throws -> Data? {
        nil
    }

    func deleteMigratableGenerationRecord(account _: String) -> Bool {
        false
    }
}

public final class KeychainSessionTokenStore: SessionTokenStoring, @unchecked Sendable {
    static let generationService = "ElectronicMail.session.v2"
    private static let cleanupQueue = DispatchQueue(
        label: "app.electronicmail.session-token.cleanup",
        qos: .utility,
        attributes: .concurrent
    )

    static var securityPolicyAttributes: [String: Any] {
        [
            kSecUseDataProtectionKeychain as String: true,
            kSecAttrSynchronizable as String: false,
            kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly,
        ]
    }

    #if os(macOS)
    static var classicMacLocalTestingPolicyAttributes: [String: Any] {
        [:]
    }
    #endif

    static var classicMacFallbackEnabled: Bool {
        #if os(macOS) && (DEBUG || ELECTRONIC_MAIL_LOCAL_BETA)
        return true
        #else
        return false
        #endif
    }

    static func shouldUseClassicMacFallback(for status: OSStatus, debugBuild: Bool) -> Bool {
        #if os(macOS)
        return debugBuild && status == errSecMissingEntitlement
        #else
        return false
        #endif
    }

    static func shouldRetryClassicMacReadInteractively(
        for status: OSStatus,
        localBuild: Bool
    ) -> Bool {
        #if os(macOS)
        return localBuild && (
            status == errSecInteractionNotAllowed ||
            status == errSecAuthFailed ||
            status == errSecItemNotFound
        )
        #else
        return false
        #endif
    }

    private let ledger: SessionTokenGenerationLedger
    private let records: SessionTokenSecureRecordStoring
    private let cleanupQueue: DispatchQueue
    private let loadFailureLock = NSLock()
    private var storedLoadFailureMessage: String?

    public var lastLoadFailureMessage: String? {
        loadFailureLock.lock()
        defer { loadFailureLock.unlock() }
        return storedLoadFailureMessage
    }

    public convenience init(defaults: UserDefaults = .standard) {
        self.init(
            defaults: defaults,
            records: KeychainSessionTokenSecureRecordStore(),
            cleanupQueue: Self.cleanupQueue
        )
    }

    init(
        defaults: UserDefaults,
        records: SessionTokenSecureRecordStoring,
        cleanupQueue: DispatchQueue
    ) {
        ledger = SessionTokenGenerationLedger(defaults: defaults)
        self.records = records
        self.cleanupQueue = cleanupQueue
    }

    public func prepareMutation(_ kind: SessionTokenMutationKind) throws -> SessionTokenMutation? {
        try ledger.allocate(kind)
    }

    public func load() -> String? {
        setLoadFailureMessage(nil)
        for _ in 0..<4 {
            switch ledger.snapshot() {
            case .corrupt:
                return nil
            case .absent:
                if let result = loadAndMigrateLegacyIfUnclaimed() {
                    return result
                }
                if case .available = ledger.snapshot() {
                    continue
                }
                switch recoverLatestLocalGenerationIfUnclaimed() {
                case .token(let token):
                    return token
                case .tombstone:
                    return nil
                case .retry:
                    continue
                case nil:
                    return nil
                }
            case .available(let state):
                guard let desired = state.desired else {
                    guard let result = loadAndMigrateLegacyIfUnclaimed() else {
                        if ledger.snapshotDesiredGenerationID() != nil {
                            continue
                        }
                        return nil
                    }
                    return result
                }
                guard ledger.isDesired(desired) else {
                    continue
                }
                guard desired.kind == .token else {
                    scheduleCleanup()
                    return nil
                }
                let account = Self.account(for: desired)
                guard let data = records.readGenerationRecord(account: account) else {
                    // A desired token with no exact record is intentionally
                    // fail-closed. The only permitted recovery is copying this
                    // exact generation from the classic macOS Keychain into the
                    // Team-bound Data Protection Keychain.
                    guard ledger.isDesired(desired) else {
                        continue
                    }
                    if let migrated = migrateExactClassicGeneration(
                        desired,
                        account: account
                    ) {
                        return migrated
                    }
                    return nil
                }
                guard ledger.isDesired(desired), let token = String(data: data, encoding: .utf8) else {
                    continue
                }
                _ = try? ledger.markRecordPersisted(desired)
                scheduleCleanup()
                return token
            }
        }
        return nil
    }

    private func migrateExactClassicGeneration(
        _ mutation: SessionTokenMutation,
        account: String
    ) -> String? {
        do {
            guard let classicData = try records.readMigratableGenerationRecord(account: account) else {
                return nil
            }
            guard ledger.isDesired(mutation),
                  let token = String(data: classicData, encoding: .utf8),
                  !token.isEmpty else {
                return nil
            }

            try records.addGenerationRecord(classicData, account: account)
            guard records.readGenerationRecord(account: account) == classicData else {
                throw KeychainError.migrationVerificationFailed
            }
            guard ledger.isDesired(mutation) else {
                scheduleCleanup()
                return nil
            }

            _ = try ledger.markRecordPersisted(mutation)
            _ = records.deleteMigratableGenerationRecord(account: account)
            scheduleCleanup()
            return token
        } catch {
            setLoadFailureMessage(
                "Electronic Mail found your saved sign-in but could not move it into the stable app Keychain. Unlock your Mac and try again, or sign in again. Your mailbox data and existing saved sign-in were not removed. \(error.localizedDescription)"
            )
            return nil
        }
    }

    private func setLoadFailureMessage(_ message: String?) {
        loadFailureLock.lock()
        storedLoadFailureMessage = message
        loadFailureLock.unlock()
    }

    public func save(_ token: String) throws {
        let mutation = try ledger.allocate(.token)
        try save(token, for: mutation)
    }

    public func save(_ token: String, for mutation: SessionTokenMutation?) throws {
        let mutation = try mutation ?? ledger.allocate(.token)
        guard mutation.kind == .token, ledger.containsAllocated(mutation) else {
            throw SessionTokenLedgerError.persistenceFailed
        }
        try records.addGenerationRecord(Data(token.utf8), account: Self.account(for: mutation))
        _ = try ledger.markRecordPersisted(mutation)
        scheduleCleanup()
    }

    public func clear() {
        guard let mutation = try? ledger.allocate(.tombstone) else {
            return
        }
        clear(for: mutation)
    }

    public func clear(for mutation: SessionTokenMutation?) {
        guard let mutation = mutation ?? (try? ledger.allocate(.tombstone)),
              mutation.kind == .tombstone,
              ledger.containsAllocated(mutation) else {
            return
        }
        do {
            try records.addGenerationRecord(Data("tombstone-v2".utf8), account: Self.account(for: mutation))
            _ = try ledger.markRecordPersisted(mutation)
            scheduleCleanup()
        } catch {
            // The durable desired tombstone was published before this Keychain
            // call. A relaunch therefore remains signed out and never falls
            // back to an older token; cleanup can retry after a later mutation.
        }
    }

    /// Repairs a known accidental local tombstone after an ad-hoc-to-stable
    /// signing transition. This is deliberately opt-in and is only invoked by
    /// the local repair command; ordinary launch and explicit sign-out remain
    /// fail-closed.
    public func repairLatestLocalSession() -> Bool {
        #if os(macOS) && (DEBUG || ELECTRONIC_MAIL_LOCAL_BETA)
        guard let record = records.latestLocalTokenRecoveryRecord() else {
            if let status = records.lastLocalRecoveryStatus {
                setLoadFailureMessage(KeychainError.status(status).localizedDescription)
            } else {
                setLoadFailureMessage("Electronic Mail did not find a readable saved local session.")
            }
            return false
        }
        guard let token = String(data: record.data, encoding: .utf8),
              !token.isEmpty else {
            setLoadFailureMessage("Electronic Mail found an invalid saved local session.")
            return false
        }
        do {
            try ledger.replaceWithCommittedForExplicitLocalRepair(record.mutation)
            return load() == token
        } catch {
            setLoadFailureMessage(error.localizedDescription)
            return false
        }
        #else
        return false
        #endif
    }

    static func account(for mutation: SessionTokenMutation) -> String {
        let paddedOrdinal = String(format: "%020llu", mutation.ordinal)
        return "session.v2.\(paddedOrdinal).\(mutation.generationID).\(mutation.kind.rawValue)"
    }

    static func mutation(fromAccount account: String) -> SessionTokenMutation? {
        let prefix = "session.v2."
        guard account.hasPrefix(prefix) else {
            return nil
        }
        let components = account.dropFirst(prefix.count).split(separator: ".", omittingEmptySubsequences: false)
        guard components.count == 3,
              let ordinal = UInt64(components[0]),
              !components[1].isEmpty,
              let kind = SessionTokenMutationKind(rawValue: String(components[2])) else {
            return nil
        }
        let mutation = SessionTokenMutation(
            generationID: String(components[1]),
            ordinal: ordinal,
            kind: kind
        )
        guard account == Self.account(for: mutation) else {
            return nil
        }
        return mutation
    }

    private func recoverLatestLocalGenerationIfUnclaimed() -> LocalGenerationRecoveryResult? {
        guard let record = records.latestLocalRecoveryRecord() else {
            return nil
        }
        guard (try? ledger.recoverCommittedIfUnclaimed(record.mutation)) == true else {
            return .retry
        }
        scheduleCleanup()
        guard record.mutation.kind == .token,
              let token = String(data: record.data, encoding: .utf8),
              !token.isEmpty else {
            return .tombstone
        }
        return .token(token)
    }

    private func loadAndMigrateLegacyIfUnclaimed() -> String? {
        guard let data = records.readLegacyRecord(),
              let token = String(data: data, encoding: .utf8),
              !token.isEmpty else {
            return nil
        }
        guard let mutation = try? ledger.allocateLegacyMigrationIfUnclaimed() else {
            return nil
        }
        do {
            try records.addGenerationRecord(data, account: Self.account(for: mutation))
            _ = try ledger.markRecordPersisted(mutation)
        } catch {
            return nil
        }
        guard ledger.isDesired(mutation) else {
            scheduleCleanup()
            return nil
        }
        scheduleCleanup()
        return token
    }

    private func scheduleCleanup() {
        cleanupQueue.async { [ledger, records] in
            guard let candidates = ledger.cleanupPlan() else {
                return
            }
            var cleanedGenerationIDs = Set<String>()
            for mutation in candidates {
                if records.deleteGenerationRecord(account: Self.account(for: mutation)) {
                    cleanedGenerationIDs.insert(mutation.generationID)
                }
            }
            try? ledger.removeCleanedEntries(generationIDs: cleanedGenerationIDs)
            records.deleteLegacyRecords()
        }
    }
}

private extension SessionTokenGenerationLedger {
    func snapshotDesiredGenerationID() -> String? {
        guard case .available(let state) = snapshot() else {
            return nil
        }
        return state.desired?.generationID
    }
}

private final class KeychainSessionTokenSecureRecordStore: SessionTokenSecureRecordStoring, @unchecked Sendable {
    private static let legacyService = "ElectronicMail"
    private static let legacyAccount = "email_session"
    private let localRecoveryStatusLock = NSLock()
    private var storedLocalRecoveryStatus: OSStatus?

    var lastLocalRecoveryStatus: OSStatus? {
        localRecoveryStatusLock.lock()
        defer { localRecoveryStatusLock.unlock() }
        return storedLocalRecoveryStatus
    }

    func readGenerationRecord(account: String) -> Data? {
        #if os(macOS) && (DEBUG || ELECTRONIC_MAIL_LOCAL_BETA)
        // Identity-free local builds do not carry the production Data
        // Protection Keychain entitlement. Querying that keychain first can
        // make securityd wait for authorization before returning
        // errSecMissingEntitlement, so local builds use their explicitly
        // non-synchronizing classic record directly.
        // production Release builds never compile this fallback.
        let query = generationClassicQuery(account: account)
        var noninteractiveQuery = query
        noninteractiveQuery[kSecUseAuthenticationUI as String] = kSecUseAuthenticationUIFail
        let noninteractiveResult = readDataResult(query: noninteractiveQuery)
        if let data = noninteractiveResult.data {
            return data
        }
        guard KeychainSessionTokenStore.shouldRetryClassicMacReadInteractively(
            for: noninteractiveResult.status,
            localBuild: true
        ) else {
            return nil
        }
        // A session written by an older ad-hoc build can still be present but
        // protected by that build's Keychain ACL. Let macOS offer its standard
        // one-time Allow / Always Allow prompt for this exact generation. Once
        // approved, the stable certificate can read it across future rebuilds.
        return readData(query: query)
        #else
        return readData(query: generationSecureQuery(account: account))
        #endif
    }

    func addGenerationRecord(_ data: Data, account: String) throws {
        #if os(macOS) && (DEBUG || ELECTRONIC_MAIL_LOCAL_BETA)
        try addImmutableData(
            data,
            query: generationClassicQuery(account: account),
            enforcesDeviceOnlyAccessibility: false
        )
        #else
        try addImmutableData(
            data,
            query: generationSecureQuery(account: account),
            enforcesDeviceOnlyAccessibility: true
        )
        #endif
    }

    func deleteGenerationRecord(account: String) -> Bool {
        #if os(macOS) && (DEBUG || ELECTRONIC_MAIL_LOCAL_BETA)
        let classicStatus = SecItemDelete(generationClassicQuery(account: account) as CFDictionary)
        return Self.isSuccessfulDeletion(classicStatus)
        #else
        let secureStatus = SecItemDelete(generationSecureQuery(account: account) as CFDictionary)
        return Self.isSuccessfulDeletion(secureStatus)
        #endif
    }

    func readMigratableGenerationRecord(account: String) throws -> Data? {
        #if os(macOS) && !DEBUG && !ELECTRONIC_MAIL_LOCAL_BETA
        try readDataReportingStatus(query: generationClassicQuery(account: account))
        #else
        nil
        #endif
    }

    func deleteMigratableGenerationRecord(account: String) -> Bool {
        #if os(macOS) && !DEBUG && !ELECTRONIC_MAIL_LOCAL_BETA
        let status = SecItemDelete(generationClassicQuery(account: account) as CFDictionary)
        return Self.isSuccessfulDeletion(status)
        #else
        false
        #endif
    }

    func latestLocalRecoveryRecord() -> SessionTokenGenerationRecord? {
        latestLocalRecoveryRecord(kind: nil, allowsInteraction: true)
    }

    func latestLocalTokenRecoveryRecord() -> SessionTokenGenerationRecord? {
        // The explicit repair command must never trigger another password or
        // Keychain authorization prompt. If the stable signing identity cannot
        // read the record silently, repair stops without changing the ledger.
        latestLocalRecoveryRecord(kind: .token, allowsInteraction: false)
    }

    private func latestLocalRecoveryRecord(
        kind requiredKind: SessionTokenMutationKind?,
        allowsInteraction: Bool
    ) -> SessionTokenGenerationRecord? {
        #if os(macOS) && (DEBUG || ELECTRONIC_MAIL_LOCAL_BETA)
        var query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: KeychainSessionTokenStore.generationService,
            kSecReturnAttributes as String: true,
            kSecMatchLimit as String: kSecMatchLimitAll,
        ]
        var item: CFTypeRef?
        let attributeStatus = SecItemCopyMatching(query as CFDictionary, &item)
        guard attributeStatus == errSecSuccess else {
            setLocalRecoveryStatus(attributeStatus)
            return nil
        }
        let dictionaries: [[String: Any]]
        if let values = item as? [[String: Any]] {
            dictionaries = values
        } else if let value = item as? [String: Any] {
            dictionaries = [value]
        } else {
            return nil
        }
        let matchingMutations = dictionaries.compactMap { value -> SessionTokenMutation? in
            guard let account = value[kSecAttrAccount as String] as? String,
                  let mutation = KeychainSessionTokenStore.mutation(fromAccount: account),
                  (requiredKind.map { $0 == mutation.kind } ?? true) else {
                return nil
            }
            return mutation
        }
        guard let selectedMutation = matchingMutations.max(by: {
            lhs, rhs in lhs.ordinal < rhs.ordinal
        }) else {
            setLocalRecoveryStatus(errSecItemNotFound)
            return nil
        }

        let account = KeychainSessionTokenStore.account(for: selectedMutation)
        let baseQuery = generationClassicQuery(account: account)
        var noninteractiveQuery = baseQuery
        noninteractiveQuery[kSecUseAuthenticationUI as String] = kSecUseAuthenticationUIFail
        var dataResult = readDataResult(query: noninteractiveQuery)
        if allowsInteraction, KeychainSessionTokenStore.shouldRetryClassicMacReadInteractively(
            for: dataResult.status,
            localBuild: true
        ) {
            dataResult = readDataResult(query: baseQuery)
        }
        setLocalRecoveryStatus(dataResult.status)
        guard let data = dataResult.data else {
            return nil
        }
        if selectedMutation.kind == .tombstone,
           String(data: data, encoding: .utf8) != "tombstone-v2" {
            return nil
        }
        return SessionTokenGenerationRecord(mutation: selectedMutation, data: data)
        #else
        return nil
        #endif
    }

    private func setLocalRecoveryStatus(_ status: OSStatus) {
        localRecoveryStatusLock.lock()
        storedLocalRecoveryStatus = status
        localRecoveryStatusLock.unlock()
    }

    func readLegacyRecord() -> Data? {
        #if os(macOS) && (DEBUG || ELECTRONIC_MAIL_LOCAL_BETA)
        var query = legacyClassicQuery()
        query[kSecUseAuthenticationUI as String] = kSecUseAuthenticationUIFail
        return readData(query: query)
        #else
        if let data = readData(query: legacySecureQuery(synchronizable: false)) {
            return data
        }
        return readData(query: legacySecureQuery(synchronizable: true))
        #endif
    }

    func deleteLegacyRecords() {
        #if os(macOS) && (DEBUG || ELECTRONIC_MAIL_LOCAL_BETA)
        _ = SecItemDelete(legacyClassicQuery() as CFDictionary)
        #else
        _ = SecItemDelete(legacySecureQuery(synchronizable: false) as CFDictionary)
        _ = SecItemDelete(legacySecureQuery(synchronizable: true) as CFDictionary)
        #endif
    }

    private func addImmutableData(
        _ data: Data,
        query: [String: Any],
        enforcesDeviceOnlyAccessibility: Bool
    ) throws {
        var item = query
        item[kSecValueData as String] = data
        if enforcesDeviceOnlyAccessibility {
            item[kSecAttrAccessible as String] = KeychainSessionTokenStore.securityPolicyAttributes[
                kSecAttrAccessible as String
            ]
        }
        let status = SecItemAdd(item as CFDictionary, nil)
        if status == errSecDuplicateItem {
            guard readData(query: query) == data else {
                throw KeychainError.status(status)
            }
            return
        }
        guard status == errSecSuccess else {
            throw KeychainError.status(status)
        }
    }

    private func readData(query base: [String: Any]) -> Data? {
        readDataResult(query: base).data
    }

    private func readDataResult(
        query base: [String: Any]
    ) -> (data: Data?, status: OSStatus) {
        var query = base
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        guard status == errSecSuccess, let data = item as? Data else {
            return (nil, status)
        }
        return (data, status)
    }

    private func readDataReportingStatus(query base: [String: Any]) throws -> Data? {
        var query = base
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        if status == errSecItemNotFound {
            return nil
        }
        guard status == errSecSuccess, let data = item as? Data else {
            throw KeychainError.status(status)
        }
        return data
    }

    private func generationSecureQuery(account: String) -> [String: Any] {
        var query = generationBaseQuery(account: account)
        query[kSecUseDataProtectionKeychain as String] = true
        query[kSecAttrSynchronizable as String] = false
        return query
    }

    #if os(macOS)
    private func generationClassicQuery(account: String) -> [String: Any] {
        generationBaseQuery(account: account)
    }
    #endif

    private func generationBaseQuery(account: String) -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: KeychainSessionTokenStore.generationService,
            kSecAttrAccount as String: account,
        ]
    }

    private func legacySecureQuery(synchronizable: Bool) -> [String: Any] {
        var query = legacyBaseQuery()
        query[kSecUseDataProtectionKeychain as String] = true
        query[kSecAttrSynchronizable as String] = synchronizable
        return query
    }

    #if os(macOS)
    private func legacyClassicQuery() -> [String: Any] {
        legacyBaseQuery()
    }
    #endif

    private func legacyBaseQuery() -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: Self.legacyService,
            kSecAttrAccount as String: Self.legacyAccount,
        ]
    }

    private static func isSuccessfulDeletion(_ status: OSStatus) -> Bool {
        status == errSecSuccess || status == errSecItemNotFound
    }
}

public enum KeychainError: Error, Equatable, LocalizedError {
    case status(OSStatus)
    case migrationVerificationFailed

    public var errorDescription: String? {
        switch self {
        case .status(let status):
            let systemMessage = SecCopyErrorMessageString(status, nil) as String?
            let detail = systemMessage.map { "\($0) (\(status))" } ?? "Keychain status \(status)"
            if status == errSecMissingEntitlement {
                return "Electronic Mail could not securely save your sign-in because this build is missing a required Keychain signing entitlement. \(detail)"
            }
            return "Electronic Mail could not securely save your sign-in in the system Keychain. \(detail)"
        case .migrationVerificationFailed:
            return "Electronic Mail could not verify the copied Keychain record."
        }
    }
}
