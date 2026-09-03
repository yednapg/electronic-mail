import SwiftUI

enum AIOrganizationSettingsPhase: Equatable {
    case idle
    case loading
    case loaded
    case failed(String)
}

@MainActor
final class AIOrganizationSettingsStore: ObservableObject {
    @Published private(set) var phase: AIOrganizationSettingsPhase = .idle
    @Published private(set) var profile: AIOrganizationProfile?
    @Published var selectedGroupingStyle: AIGroupingStyle = .focused
    @Published private(set) var mutationInProgress = false
    @Published private(set) var mutationError: String?

    private let client: AppClient

    init(client: AppClient) {
        self.client = client
    }

    var hasPendingGroupingStyle: Bool {
        guard let profile else { return false }
        return selectedGroupingStyle != profile.groupingStyle
    }

    func load() async {
        if profile == nil {
            phase = .loading
        }
        mutationError = nil
        do {
            accept(try await client.aiOrganizationProfile())
        } catch {
            if profile == nil {
                phase = .failed(error.localizedDescription)
            } else {
                mutationError = error.localizedDescription
            }
        }
    }

    func setEnabled(_ enabled: Bool) async {
        guard let profile, profile.available, profile.enabled != enabled else { return }
        await update(
            AIOrganizationProfilePatch(
                consent: enabled && !profile.consented ? true : nil,
                enabled: enabled
            )
        )
    }

    func applyGroupingStyle(rebuildExisting: Bool) async {
        guard hasPendingGroupingStyle else { return }
        await update(
            AIOrganizationProfilePatch(
                groupingStyle: selectedGroupingStyle,
                rebuildExisting: rebuildExisting
            )
        )
    }

    func deleteAIData() async {
        guard !mutationInProgress else { return }
        mutationInProgress = true
        mutationError = nil
        defer { mutationInProgress = false }

        do {
            try await client.deleteAIOrganizationData()
            NotificationCenter.default.post(name: .electronicMailAIInboxChanged, object: nil)
            accept(try await client.aiOrganizationProfile())
        } catch {
            mutationError = error.localizedDescription
        }
    }

    private func update(_ patch: AIOrganizationProfilePatch) async {
        guard !mutationInProgress else { return }
        mutationInProgress = true
        mutationError = nil
        defer { mutationInProgress = false }

        do {
            accept(try await client.updateAIOrganizationProfile(patch))
            NotificationCenter.default.post(name: .electronicMailAIInboxChanged, object: nil)
        } catch {
            mutationError = error.localizedDescription
        }
    }

    private func accept(_ profile: AIOrganizationProfile) {
        self.profile = profile
        selectedGroupingStyle = profile.groupingStyle
        phase = .loaded
    }
}

struct AIOrganizationSettingsView: View {
    @ObservedObject var store: AIOrganizationSettingsStore
    @AppStorage(AIReaderSummaryVisibility.storageKey)
    private var summaryVisibilityRawValue = AIReaderSummaryVisibility.always.rawValue
    @State private var confirmsRebuild = false
    @State private var confirmsDataDeletion = false

    var body: some View {
        Form {
            settingsContent
        }
        .formStyle(.grouped)
        .task {
            guard store.phase == .idle else { return }
            await store.load()
        }
        .alert("Rebuild automatic organization?", isPresented: $confirmsRebuild) {
            Button("Cancel", role: .cancel) {}
            Button("Rebuild") {
                Task { await store.applyGroupingStyle(rebuildExisting: true) }
            }
        } message: {
            Text("Electronic Mail will create a new AI organization generation while preserving confirmed memberships and explicit separations.")
        }
        .alert("Delete all AI Inbox data?", isPresented: $confirmsDataDeletion) {
            Button("Cancel", role: .cancel) {}
            Button("Delete AI Data", role: .destructive) {
                Task { await store.deleteAIData() }
            }
        } message: {
            Text("This permanently removes summaries, embeddings, matters, attachment text, and corrections. It does not delete Gmail messages.")
        }
    }

    @ViewBuilder
    private var settingsContent: some View {
        switch store.phase {
        case .idle, .loading:
            Section {
                ProgressView("Loading AI Inbox settings")
            }
        case .failed(let message):
            Section {
                Label("AI Inbox settings are unavailable", systemImage: "exclamationmark.triangle")
                Text(message)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Button("Try Again") {
                    Task { await store.load() }
                }
            }
        case .loaded:
            if let profile = store.profile {
                aiInboxSection(profile)
                readerSection
                groupingSection(profile)
                dataSection
            }
        }

        if let mutationError = store.mutationError {
            Section {
                Label(mutationError, systemImage: "exclamationmark.triangle")
                    .foregroundStyle(.red)
            }
        }
    }

    private func aiInboxSection(_ profile: AIOrganizationProfile) -> some View {
        Section("AI Inbox") {
            Toggle(
                "Enable AI Inbox",
                isOn: Binding(
                    get: { profile.enabled },
                    set: { enabled in
                        Task { await store.setEnabled(enabled) }
                    }
                )
            )
            .disabled(!profile.available || store.mutationInProgress)

            LabeledContent("Status") {
                Text(statusText(for: profile))
                    .foregroundStyle(.secondary)
            }

            if !profile.available {
                Text("AI Inbox is not available for this account yet.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else if !profile.consented {
                Text("Enabling AI Inbox allows Electronic Mail to process cleaned email text and supported attachment text for organization. Your normal Inbox remains unchanged.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                Text("Turning AI Inbox off stops new AI processing. Your normal Inbox and Gmail data remain available.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var readerSection: some View {
        Section("Reader") {
            Picker("AI summaries", selection: $summaryVisibilityRawValue) {
                ForEach(AIReaderSummaryVisibility.allCases) { visibility in
                    Text(visibility.title).tag(visibility.rawValue)
                }
            }

            Text(summaryVisibility.explanation)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    private var summaryVisibility: AIReaderSummaryVisibility {
        AIReaderSummaryVisibility(rawValue: summaryVisibilityRawValue) ?? .always
    }

    private func groupingSection(_ profile: AIOrganizationProfile) -> some View {
        Section("Organization") {
            Picker("Grouping style", selection: $store.selectedGroupingStyle) {
                ForEach(AIGroupingStyle.allCases) { style in
                    Text(style.title).tag(style)
                }
            }
            .disabled(!profile.enabled || store.mutationInProgress)

            HStack {
                Button("Apply to Future Mail") {
                    Task { await store.applyGroupingStyle(rebuildExisting: false) }
                }
                .disabled(
                    !profile.enabled
                        || !store.hasPendingGroupingStyle
                        || store.mutationInProgress
                )

                Button("Rebuild Existing Organization…") {
                    confirmsRebuild = true
                }
                .disabled(
                    !profile.enabled
                        || !store.hasPendingGroupingStyle
                        || store.mutationInProgress
                )
            }

            Text("Applying affects future mail only. Rebuilding creates a new organization generation while preserving your confirmed decisions.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    private var dataSection: some View {
        Section("AI Data") {
            Button("Delete AI Data…", role: .destructive) {
                confirmsDataDeletion = true
            }
            .disabled(store.mutationInProgress)

            Text("Deletes AI-generated data and corrections without deleting Gmail messages.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    private func statusText(for profile: AIOrganizationProfile) -> String {
        if !profile.available {
            return "Unavailable"
        }
        return profile.enabled ? "On" : "Off"
    }
}
