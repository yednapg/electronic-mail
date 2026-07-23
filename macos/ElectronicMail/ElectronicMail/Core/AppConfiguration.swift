import Foundation

public enum AppConfiguration {
    public static let defaultBackendURL: URL = {
        if let value = Bundle.main.object(forInfoDictionaryKey: "BackendBaseURL") as? String,
           let url = URL(string: value.trimmingCharacters(in: .whitespacesAndNewlines)),
           ["https", "http"].contains(url.scheme?.lowercased()),
           url.host?.isEmpty == false {
            return url
        }

        #if DEBUG
        return URL(string: "http://localhost:3001")!
        #else
        return URL(string: "https://electronic-mail-backend.invalid")!
        #endif
    }()
    public static let authRedirectURI = "electronicmail://auth/callback"
}
