import Foundation

public enum AppConfiguration {
    public static let defaultBackendURL = URL(string: "http://localhost:3001")!
    public static let authRedirectURI = "electronicmail://auth/callback"
}
