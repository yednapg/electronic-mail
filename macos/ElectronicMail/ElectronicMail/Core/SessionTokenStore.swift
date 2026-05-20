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
    private let service = "ElectronicMail"
    private let account = "email_session"

    public init() {}

    public func load() -> String? {
        var query = baseQuery()
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne

        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        guard status == errSecSuccess, let data = item as? Data else {
            return nil
        }
        return String(data: data, encoding: .utf8)
    }

    public func save(_ token: String) throws {
        let data = Data(token.utf8)
        var query = baseQuery()
        let attributes = [kSecValueData as String: data]
        let status = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if status == errSecSuccess {
            return
        }
        guard status == errSecItemNotFound else {
            throw KeychainError.status(status)
        }

        query[kSecValueData as String] = data
        let addStatus = SecItemAdd(query as CFDictionary, nil)
        guard addStatus == errSecSuccess else {
            throw KeychainError.status(addStatus)
        }
    }

    public func clear() {
        SecItemDelete(baseQuery() as CFDictionary)
    }

    private func baseQuery() -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account
        ]
    }
}

public enum KeychainError: Error, Equatable {
    case status(OSStatus)
}
