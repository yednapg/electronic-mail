import SwiftUI

struct TraceDetailView: View {
    let entityID: String
    @ObservedObject var store: DashboardStore
    @State private var state: TraceState = .loading

    var body: some View {
        content
            .navigationTitle("Trace")
            .navigationBarTitleDisplayMode(.inline)
            .task(id: entityID) {
                await loadTrace()
            }
    }

    @ViewBuilder
    private var content: some View {
        switch state {
        case .loading:
            ProgressView()
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(Color.white)
        case .failed(let message):
            ContentUnavailableView {
                Label("Trace unavailable", systemImage: "exclamationmark.triangle")
                    .font(DigestPalette.rounded(size: 20, weight: .bold))
                    .foregroundStyle(DigestPalette.text)
            } description: {
                Text(message)
                    .font(DigestPalette.rounded(size: 16))
                    .foregroundStyle(DigestPalette.muted)
            } actions: {
                Button("Retry") {
                    Task { await loadTrace() }
                }
                .font(DigestPalette.rounded(size: 15, weight: .semibold))
            }
        case .loaded(let trace):
            List {
                Section("Entity") {
                    Text(trace.entityID)
                        .font(DigestPalette.rounded(size: 13, weight: .semibold))
                        .foregroundStyle(DigestPalette.text)
                    Text("\(trace.sourceRecordIDs.count) source records")
                        .font(DigestPalette.rounded(size: 15))
                        .foregroundStyle(DigestPalette.muted)
                }

                Section("Pipeline") {
                    ForEach(trace.items) { item in
                        VStack(alignment: .leading, spacing: 6) {
                            Text(item.stage.replacingOccurrences(of: "_", with: " ").capitalized)
                                .font(DigestPalette.rounded(size: 16, weight: .bold))
                                .foregroundStyle(DigestPalette.text)
                            Text(item.createdAt)
                                .font(DigestPalette.rounded(size: 12))
                                .foregroundStyle(DigestPalette.subtle)

                            if !item.output.isEmpty {
                                Text(outputSummary(item.output))
                                    .font(DigestPalette.rounded(size: 13))
                                    .foregroundStyle(DigestPalette.muted)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                        .padding(.vertical, 4)
                    }
                }
            }
        }
    }

    private func loadTrace() async {
        state = .loading

        do {
            state = .loaded(try await store.loadTrace(entityID: entityID))
        } catch {
            state = .failed(error.localizedDescription)
        }
    }

    private func outputSummary(_ output: [String: JSONValue]) -> String {
        output
            .sorted { $0.key < $1.key }
            .prefix(4)
            .map { "\($0.key): \($0.value.displayString)" }
            .joined(separator: "\n")
    }
}

private enum TraceState: Equatable {
    case loading
    case loaded(TraceReplayResponse)
    case failed(String)
}

struct SettingsView: View {
    @ObservedObject var store: DashboardStore
    @Environment(\.dismiss) private var dismiss
    @State private var backendURL: String = ""

    var body: some View {
        NavigationStack {
            Form {
                Section("Backend") {
                    TextField("Backend URL", text: $backendURL)
                        .font(DigestPalette.rounded(size: 16))
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()

                    Button {
                        store.saveBackendURL(backendURL)
                        Task { await store.refresh() }
                    } label: {
                        Label("Save and Refresh", systemImage: "arrow.clockwise")
                    }
                    .font(DigestPalette.rounded(size: 16, weight: .semibold))
                }

                if let profile = store.dashboard?.profile, let email = profile.email {
                    Section("Account") {
                        Label(email, systemImage: "envelope")
                            .font(DigestPalette.rounded(size: 16))
                    }
                }
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Done") {
                        dismiss()
                    }
                }
            }
            .onAppear {
                backendURL = store.backendBaseURLString
            }
        }
    }
}
