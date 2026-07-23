import Foundation
import Security

public protocol SessionTokenStoring: AnyObject {
    func load() -> String?
    func save(_ token: String) throws
    func clear()
}

public final class UserDefaultsSessionTokenStore: SessionTokenStoring {
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

public final class KeychainSessionTokenStore: SessionTokenStoring {
    private static let service = "ElectronicMail"
    private static let account = "email_session"

    static var securityPolicyAttributes: [String: Any] {
        [
            kSecUseDataProtectionKeychain as String: true,
            kSecAttrSynchronizable as String: false,
            kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly,
        ]
    }

    #if os(macOS)
    static var classicMacDebugPolicyAttributes: [String: Any] {
        [
            kSecAttrSynchronizable as String: false,
        ]
    }
    #endif

    static var classicMacFallbackEnabled: Bool {
        #if os(macOS) && DEBUG
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

    public init() {}

    public func load() -> String? {
        if let data = readData(query: secureQuery(synchronizable: false)) {
            return String(data: data, encoding: .utf8)
        }

        if let synchronizedData = readData(query: secureQuery(synchronizable: true)) {
            migrateToDeviceOnlyStorage(synchronizedData) {
                SecItemDelete(self.secureQuery(synchronizable: true) as CFDictionary)
            }
            return String(data: synchronizedData, encoding: .utf8)
        }

        #if os(macOS) && DEBUG
        if let classicData = readData(query: classicMacQuery()) {
            migrateClassicMacDataToDataProtectionKeychain(classicData)
            return String(data: classicData, encoding: .utf8)
        }
        #endif

        return nil
    }

    public func save(_ token: String) throws {
        let destination = try writeSecureData(Data(token.utf8))
        _ = SecItemDelete(secureQuery(synchronizable: true) as CFDictionary)
        #if os(macOS)
        if destination == .dataProtection {
            _ = SecItemDelete(classicMacQuery() as CFDictionary)
        }
        #endif
    }

    public func clear() {
        _ = SecItemDelete(secureQuery(synchronizable: false) as CFDictionary)
        _ = SecItemDelete(secureQuery(synchronizable: true) as CFDictionary)
        #if os(macOS)
        _ = SecItemDelete(classicMacQuery() as CFDictionary)
        _ = SecItemDelete(baseQuery() as CFDictionary)
        #endif
    }

    private enum StorageDestination: Equatable {
        case dataProtection
        #if os(macOS) && DEBUG
        case classicMac
        #endif
    }

    @discardableResult
    private func writeSecureData(_ data: Data) throws -> StorageDestination {
        do {
            try writeData(data, query: secureQuery(synchronizable: false), enforcesDeviceOnlyAccessibility: true)
            return .dataProtection
        } catch KeychainError.status(let status) {
            #if os(macOS) && DEBUG
            if Self.shouldUseClassicMacFallback(for: status, debugBuild: Self.classicMacFallbackEnabled) {
                try writeData(data, query: classicMacQuery(), enforcesDeviceOnlyAccessibility: false)
                return .classicMac
            }
            #endif
            throw KeychainError.status(status)
        }
    }

    private func writeData(
        _ data: Data,
        query: [String: Any],
        enforcesDeviceOnlyAccessibility: Bool
    ) throws {
        var attributes: [String: Any] = [kSecValueData as String: data]
        if enforcesDeviceOnlyAccessibility {
            attributes[kSecAttrAccessible as String] = Self.securityPolicyAttributes[kSecAttrAccessible as String]
        }
        let status = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if status == errSecSuccess {
            return
        }
        guard status == errSecItemNotFound else {
            throw KeychainError.status(status)
        }

        var newItem = query
        newItem[kSecValueData as String] = data
        if enforcesDeviceOnlyAccessibility {
            newItem[kSecAttrAccessible as String] = Self.securityPolicyAttributes[kSecAttrAccessible as String]
        }
        let addStatus = SecItemAdd(newItem as CFDictionary, nil)
        guard addStatus == errSecSuccess else {
            throw KeychainError.status(addStatus)
        }
    }

    private func readData(query base: [String: Any]) -> Data? {
        var query = base
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        guard status == errSecSuccess, let data = item as? Data else {
            return nil
        }
        return data
    }

    private func migrateToDeviceOnlyStorage(_ data: Data, removeLegacyItem: () -> OSStatus) {
        do {
            try writeSecureData(data)
            _ = removeLegacyItem()
        } catch {
            // Keep the source item intact and retry migration on the next access.
        }
    }

    private func secureQuery(synchronizable: Bool) -> [String: Any] {
        var query = baseQuery()
        query[kSecUseDataProtectionKeychain as String] = Self.securityPolicyAttributes[kSecUseDataProtectionKeychain as String]
        query[kSecAttrSynchronizable as String] = synchronizable
        return query
    }

    #if os(macOS)
    private func classicMacQuery() -> [String: Any] {
        var query = baseQuery()
        query[kSecAttrSynchronizable as String] = Self.classicMacDebugPolicyAttributes[kSecAttrSynchronizable as String]
        return query
    }

    #if DEBUG

    private func migrateClassicMacDataToDataProtectionKeychain(_ data: Data) {
        do {
            try writeData(
                data,
                query: secureQuery(synchronizable: false),
                enforcesDeviceOnlyAccessibility: true
            )
            _ = SecItemDelete(classicMacQuery() as CFDictionary)
        } catch {
            // Local ad-hoc Xcode builds can lack the entitlement required by the
            // Data Protection Keychain. Keep the DEBUG-only classic Keychain item
            // intact; Release builds never read from or write to this fallback.
        }
    }
    #endif
    #endif

    private func baseQuery() -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: Self.service,
            kSecAttrAccount as String: Self.account,
        ]
    }
}

public enum KeychainError: Error, Equatable, LocalizedError {
    case status(OSStatus)

    public var errorDescription: String? {
        switch self {
        case .status(let status):
            let systemMessage = SecCopyErrorMessageString(status, nil) as String?
            let detail = systemMessage.map { "\($0) (\(status))" } ?? "Keychain status \(status)"
            if status == errSecMissingEntitlement {
                return "Electronic Mail could not securely save your sign-in because this build is missing a required Keychain signing entitlement. \(detail)"
            }
            return "Electronic Mail could not securely save your sign-in in the system Keychain. \(detail)"
        }
    }
}
