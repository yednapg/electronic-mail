import Foundation

public enum SourceType: String, Codable, Equatable {
    case gmail
    case calendar
}

public enum TimingBand: String, Codable, Equatable {
    case now
    case today
    case later
    case hidden
}

public enum EntityCurrentState: String, Codable, Equatable {
    case open
    case waiting
    case done
}

public enum GmailThreadAction: String, Codable, Equatable {
    case archive
    case unarchive
}

public enum NeedType: String, Codable, Equatable {
    case decision
    case awareness
}

public struct GoogleAuthState: Codable, Equatable {
    let available: Bool
    let connected: Bool
    let connectURL: String?

    enum CodingKeys: String, CodingKey {
        case available
        case connected
        case connectURL = "connect_url"
    }
}

public struct DashboardProfile: Codable, Equatable {
    let email: String?
    let displayName: String?

    enum CodingKeys: String, CodingKey {
        case email
        case displayName = "display_name"
    }
}

public struct DashboardBriefing: Codable, Equatable {
    let headline: String
    let brief: String
}

public struct DashboardResponse: Codable, Equatable {
    let auth: GoogleAuthState
    let profile: DashboardProfile?
    let briefing: DashboardBriefing?
    let feed: FeedResponse
}

public struct FeedResponse: Codable, Equatable {
    let now: [AttentionItem]
    let today: [AttentionItem]
    let worthKnowing: [AttentionItem]

    var isEmpty: Bool {
        now.isEmpty && today.isEmpty && worthKnowing.isEmpty
    }

    enum CodingKeys: String, CodingKey {
        case now
        case today
        case worthKnowing = "worth_knowing"
    }
}

public struct AttentionItem: Codable, Equatable, Identifiable, Hashable {
    public let id: String
    let entityID: String
    let userID: String
    let needType: NeedType
    let actionType: String
    let effortLevel: String
    let timingBand: TimingBand
    let actionConfidence: String
    let primaryAction: String
    let fallbackAction: String
    let title: String
    let whyThisIsHere: String
    let dueAt: String?
    let importanceLevel: String?
    let lifecycleState: String?
    let currentState: EntityCurrentState?
    let source: SourceType?
    let gmailThreadID: String?
    let gmailThreadAction: GmailThreadAction?
    let traceID: String
    let createdAt: String

    var canMutateGmailThread: Bool {
        gmailThreadID != nil && gmailThreadAction != nil
    }

    enum CodingKeys: String, CodingKey {
        case id
        case entityID = "entity_id"
        case userID = "user_id"
        case needType = "need_type"
        case actionType = "action_type"
        case effortLevel = "effort_level"
        case timingBand = "timing_band"
        case actionConfidence = "action_confidence"
        case primaryAction = "primary_action"
        case fallbackAction = "fallback_action"
        case title
        case whyThisIsHere = "why_this_is_here"
        case dueAt = "due_at"
        case importanceLevel = "importance_level"
        case lifecycleState = "lifecycle_state"
        case currentState = "current_state"
        case source
        case gmailThreadID = "gmail_thread_id"
        case gmailThreadAction = "gmail_thread_action"
        case traceID = "trace_id"
        case createdAt = "created_at"
    }
}

public struct GmailThreadMutationResponse: Codable, Equatable {
    let threadID: String
    let action: GmailThreadAction

    enum CodingKeys: String, CodingKey {
        case threadID = "thread_id"
        case action
    }
}

public struct TraceReplayResponse: Codable, Equatable {
    let entityID: String
    let sourceRecordIDs: [String]
    let items: [TraceRecord]

    enum CodingKeys: String, CodingKey {
        case entityID = "entity_id"
        case sourceRecordIDs = "source_record_ids"
        case items
    }
}

public struct TraceRecord: Codable, Equatable, Identifiable {
    public let id: String
    let traceID: String
    let entityID: String?
    let sourceRecordID: String?
    let userID: String
    let stage: String
    let input: [String: JSONValue]
    let output: [String: JSONValue]
    let createdAt: String

    enum CodingKeys: String, CodingKey {
        case id
        case traceID = "trace_id"
        case entityID = "entity_id"
        case sourceRecordID = "source_record_id"
        case userID = "user_id"
        case stage
        case input
        case output
        case createdAt = "created_at"
    }
}

public enum JSONValue: Codable, Equatable {
    case string(String)
    case number(Double)
    case bool(Bool)
    case object([String: JSONValue])
    case array([JSONValue])
    case null

    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()

        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode(Bool.self) {
            self = .bool(value)
        } else if let value = try? container.decode(Double.self) {
            self = .number(value)
        } else if let value = try? container.decode(String.self) {
            self = .string(value)
        } else if let value = try? container.decode([String: JSONValue].self) {
            self = .object(value)
        } else if let value = try? container.decode([JSONValue].self) {
            self = .array(value)
        } else {
            throw DecodingError.dataCorruptedError(in: container, debugDescription: "Unsupported JSON value")
        }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()

        switch self {
        case .string(let value):
            try container.encode(value)
        case .number(let value):
            try container.encode(value)
        case .bool(let value):
            try container.encode(value)
        case .object(let value):
            try container.encode(value)
        case .array(let value):
            try container.encode(value)
        case .null:
            try container.encodeNil()
        }
    }

    var displayString: String {
        switch self {
        case .string(let value):
            return value
        case .number(let value):
            return String(value)
        case .bool(let value):
            return value ? "true" : "false"
        case .object(let value):
            return "\(value.count) fields"
        case .array(let value):
            return "\(value.count) items"
        case .null:
            return "null"
        }
    }
}

public extension JSONDecoder {
    static let backend: JSONDecoder = {
        JSONDecoder()
    }()
}

public extension JSONEncoder {
    static let backend: JSONEncoder = {
        JSONEncoder()
    }()
}
