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
        case .failed(let message):
            ContentUnavailableView {
                Label("Trace unavailable", systemImage: "exclamationmark.triangle")
            } description: {
                Text(message)
            } actions: {
                Button("Retry") {
                    Task { await loadTrace() }
                }
            }
        case .loaded(let trace):
            List {
                Section("Entity") {
                    Text(trace.entityID)
                        .font(.footnote.monospaced())
                    Text("\(trace.sourceRecordIDs.count) source records")
                        .foregroundStyle(.secondary)
                }

                Section("Pipeline") {
                    ForEach(trace.items) { item in
                        VStack(alignment: .leading, spacing: 6) {
                            Text(item.stage.replacingOccurrences(of: "_", with: " ").capitalized)
                                .font(.headline)
                            Text(item.createdAt)
                                .font(.caption.monospaced())
                                .foregroundStyle(.secondary)

                            if !item.output.isEmpty {
                                Text(outputSummary(item.output))
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
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
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()

                    Button {
                        store.saveBackendURL(backendURL)
                        Task { await store.refresh() }
                    } label: {
                        Label("Save and Refresh", systemImage: "arrow.clockwise")
                    }
                }

                if let profile = store.dashboard?.profile, let email = profile.email {
                    Section("Account") {
                        Label(email, systemImage: "envelope")
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
