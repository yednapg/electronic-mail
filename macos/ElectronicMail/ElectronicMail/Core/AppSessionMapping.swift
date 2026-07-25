import Foundation

extension AppSessionResponse {
    func replacingDashboardFeed(_ transform: (FeedResponse) -> FeedResponse) -> AppSessionResponse {
        AppSessionResponse(
            user: user,
            readiness: readiness,
            dashboard: dashboard.replacingFeed(transform(dashboard.feed)),
            mailbox: mailbox,
            sync: sync
        )
    }

    func replacingMailboxRows(_ transform: (GmailThreadRow) -> GmailThreadRow) -> AppSessionResponse {
        AppSessionResponse(
            user: user,
            readiness: readiness,
            dashboard: dashboard,
            mailbox: mailbox.replacingRows(transform),
            sync: sync
        )
    }
}

extension DashboardResponse {
    func replacingFeed(_ feed: FeedResponse) -> DashboardResponse {
        DashboardResponse(
            auth: auth,
            profile: profile,
            briefing: briefing,
            feed: feed
        )
    }
}

extension FeedResponse {
    func removingEntity(_ entityID: String) -> FeedResponse {
        FeedResponse(
            now: now.filter { $0.entityID != entityID },
            today: today.filter { $0.entityID != entityID },
            worthKnowing: worthKnowing.filter { $0.entityID != entityID }
        )
    }

    func appending(_ item: AttentionItem, section: String) -> FeedResponse {
        if section == "now" {
            return FeedResponse(now: now + [item], today: today, worthKnowing: worthKnowing)
        }
        if section == "later" {
            return FeedResponse(now: now, today: today, worthKnowing: worthKnowing + [item])
        }
        return FeedResponse(now: now, today: today + [item], worthKnowing: worthKnowing)
    }
}

extension MailboxResponse {
    func replacingRows(_ transform: (GmailThreadRow) -> GmailThreadRow) -> MailboxResponse {
        let nextSections = sections.map { section in
            GmailThreadSection(
                id: section.id,
                title: section.title,
                rows: section.rows.map(transform)
            )
        }
        return MailboxResponse(
            label: label,
            totalThreads: totalThreads,
            unreadThreads: unreadThreads,
            nextCursor: nextCursor,
            loadedThreads: loadedThreads,
            windowDays: windowDays,
            sections: nextSections,
            readyCount: readyCount,
            pendingCount: pendingCount,
            mailboxRevision: mailboxRevision,
            generatedAt: generatedAt,
            oldestImportedAt: oldestImportedAt,
            fullImportRunning: fullImportRunning,
            fullImportCompleted: fullImportCompleted,
            syncGeneration: syncGeneration,
            phase: phase,
            initialTargetCount: initialTargetCount,
            initialMetadataCount: initialMetadataCount,
            initialBodyTargetCount: initialBodyTargetCount,
            initialBodyReadyCount: initialBodyReadyCount,
            historyMetadataCount: historyMetadataCount,
            historyBodyReadyCount: historyBodyReadyCount,
            estimatedTotalCount: estimatedTotalCount,
            initialWindowComplete: initialWindowComplete,
            historyMetadataComplete: historyMetadataComplete,
            historyBodyComplete: historyBodyComplete,
            lastProgressAt: lastProgressAt
        )
    }

    func appendingPage(_ page: MailboxResponse) -> MailboxResponse {
        var sectionOrder = sections.map(\.id)
        var rowsBySection = Dictionary(uniqueKeysWithValues: sections.map { ($0.id, $0.rows) })
        var titlesBySection = Dictionary(uniqueKeysWithValues: sections.map { ($0.id, $0.title) })
        var seenRowKeys = Set<String>()
        sections.flatMap(\.rows).forEach { Self.insertMergeKeys(for: $0, into: &seenRowKeys) }

        for section in page.sections {
            if rowsBySection[section.id] == nil {
                sectionOrder.append(section.id)
                rowsBySection[section.id] = []
                titlesBySection[section.id] = section.title
            }
            let newRows = section.rows.filter { Self.insertMergeKeysIfUnique(for: $0, into: &seenRowKeys) }
            rowsBySection[section.id, default: []].append(contentsOf: newRows)
        }

        let nextSections = sectionOrder.map { id in
            GmailThreadSection(id: id, title: titlesBySection[id] ?? "", rows: rowsBySection[id] ?? [])
        }
        let visibleRows = nextSections.reduce(0) { $0 + $1.rows.count }
        let currentVisibleRows = sections.reduce(0) { $0 + $1.rows.count }
        let pageVisibleRows = page.sections.reduce(0) { $0 + $1.rows.count }
        let total = max(visibleRows, max(totalThreads, page.totalThreads))
        let loaded = min(
            total,
            max(
                visibleRows,
                max(loadedThreads ?? currentVisibleRows, page.loadedThreads ?? pageVisibleRows)
            )
        )
        let useOnlyPageProgress = syncGeneration != nil
            && page.syncGeneration != nil
            && syncGeneration != page.syncGeneration
        return MailboxResponse(
            label: label,
            totalThreads: total,
            unreadThreads: page.unreadThreads ?? unreadThreads,
            nextCursor: page.nextCursor,
            loadedThreads: loaded,
            windowDays: page.windowDays ?? windowDays,
            sections: nextSections,
            readyCount: page.readyCount ?? readyCount,
            pendingCount: page.pendingCount ?? pendingCount,
            mailboxRevision: page.mailboxRevision ?? mailboxRevision,
            generatedAt: page.generatedAt ?? generatedAt,
            oldestImportedAt: page.oldestImportedAt ?? oldestImportedAt,
            fullImportRunning: page.fullImportRunning ?? fullImportRunning,
            fullImportCompleted: page.fullImportCompleted ?? fullImportCompleted,
            syncGeneration: page.syncGeneration ?? syncGeneration,
            phase: page.phase ?? phase,
            initialTargetCount: useOnlyPageProgress ? page.initialTargetCount : Self.monotonicMax(initialTargetCount, page.initialTargetCount),
            initialMetadataCount: useOnlyPageProgress ? page.initialMetadataCount : Self.monotonicMax(initialMetadataCount, page.initialMetadataCount),
            initialBodyTargetCount: useOnlyPageProgress ? page.initialBodyTargetCount : Self.monotonicMax(initialBodyTargetCount, page.initialBodyTargetCount),
            initialBodyReadyCount: useOnlyPageProgress ? page.initialBodyReadyCount : Self.monotonicMax(initialBodyReadyCount, page.initialBodyReadyCount),
            historyMetadataCount: useOnlyPageProgress ? page.historyMetadataCount : Self.monotonicMax(historyMetadataCount, page.historyMetadataCount),
            historyBodyReadyCount: useOnlyPageProgress ? page.historyBodyReadyCount : Self.monotonicMax(historyBodyReadyCount, page.historyBodyReadyCount),
            estimatedTotalCount: useOnlyPageProgress ? page.estimatedTotalCount : Self.monotonicMax(estimatedTotalCount, page.estimatedTotalCount),
            initialWindowComplete: useOnlyPageProgress ? page.initialWindowComplete : Self.monotonicCompletion(initialWindowComplete, page.initialWindowComplete),
            historyMetadataComplete: useOnlyPageProgress ? page.historyMetadataComplete : Self.monotonicCompletion(historyMetadataComplete, page.historyMetadataComplete),
            historyBodyComplete: useOnlyPageProgress ? page.historyBodyComplete : Self.monotonicCompletion(historyBodyComplete, page.historyBodyComplete),
            lastProgressAt: page.lastProgressAt ?? lastProgressAt
        )
    }

    func preservingLoadedPages(
        afterRefreshingFirstPage firstPage: MailboxResponse,
        discardStalePages: Bool = false
    ) -> MailboxResponse {
        let generationChanged = firstPage.syncGeneration != nil
            && syncGeneration != firstPage.syncGeneration
        if discardStalePages || generationChanged {
            return firstPage
        }
        let currentRevision = mailboxRevision?.trimmingCharacters(in: .whitespacesAndNewlines)
        let firstPageRevision = firstPage.mailboxRevision?.trimmingCharacters(in: .whitespacesAndNewlines)
        let revisionChanged = currentRevision?.isEmpty == false
            && firstPageRevision?.isEmpty == false
            && currentRevision != firstPageRevision
        let firstPageVisibleRows = firstPage.sections.reduce(0) { $0 + $1.rows.count }
        let firstPageLoadedRows = max(firstPage.loadedThreads ?? firstPageVisibleRows, firstPageVisibleRows)
        let firstPageIsAuthoritative = firstPage.nextCursor?.isEmpty != false
            && firstPage.fullImportRunning != true
            && firstPage.fullImportCompleted != false
            && firstPageVisibleRows >= firstPage.totalThreads
            && firstPageLoadedRows >= firstPage.totalThreads
            && (firstPage.syncGeneration == nil || firstPage.historyMetadataComplete == true)
        if revisionChanged, firstPageIsAuthoritative {
            return firstPage
        }
        var sectionOrder = firstPage.sections.map(\.id)
        var rowsBySection = Dictionary(uniqueKeysWithValues: firstPage.sections.map { ($0.id, $0.rows) })
        var titlesBySection = Dictionary(uniqueKeysWithValues: firstPage.sections.map { ($0.id, $0.title) })
        var seenRowKeys = Set<String>()
        firstPage.sections.flatMap(\.rows).forEach { Self.insertMergeKeys(for: $0, into: &seenRowKeys) }

        for section in sections {
            if rowsBySection[section.id] == nil {
                sectionOrder.append(section.id)
                rowsBySection[section.id] = []
                titlesBySection[section.id] = section.title
            }
            let preservedRows = section.rows.filter { Self.insertMergeKeysIfUnique(for: $0, into: &seenRowKeys) }
            rowsBySection[section.id, default: []].append(contentsOf: preservedRows)
        }

        let nextSections = sectionOrder.map { id in
            GmailThreadSection(id: id, title: titlesBySection[id] ?? "", rows: rowsBySection[id] ?? [])
        }
        let loaded = nextSections.reduce(0) { $0 + $1.rows.count }
        let firstPageLoaded = firstPage.loadedThreads ?? firstPage.sections.reduce(0) { $0 + $1.rows.count }
        let nextCursor = loaded > firstPageLoaded ? self.nextCursor : firstPage.nextCursor
        let isStillComplete = firstPage.fullImportRunning != true
            && firstPage.fullImportCompleted != false
            && self.fullImportCompleted == true
            && loaded >= firstPage.totalThreads
        let mergedImportCompleted: Bool?
        if let firstPageImportCompleted = firstPage.fullImportCompleted {
            mergedImportCompleted = firstPageImportCompleted
        } else if firstPage.fullImportRunning == true {
            mergedImportCompleted = false
        } else {
            mergedImportCompleted = isStillComplete ? true : fullImportCompleted
        }

        return MailboxResponse(
            label: firstPage.label,
            totalThreads: firstPage.totalThreads,
            unreadThreads: firstPage.unreadThreads ?? unreadThreads,
            nextCursor: nextCursor,
            loadedThreads: max(firstPage.loadedThreads ?? 0, loadedThreads ?? 0, loaded),
            windowDays: firstPage.windowDays ?? windowDays,
            sections: nextSections,
            readyCount: firstPage.readyCount ?? readyCount,
            pendingCount: firstPage.pendingCount ?? pendingCount,
            mailboxRevision: firstPage.mailboxRevision ?? mailboxRevision,
            generatedAt: firstPage.generatedAt ?? generatedAt,
            oldestImportedAt: firstPage.oldestImportedAt ?? oldestImportedAt,
            fullImportRunning: firstPage.fullImportRunning ?? fullImportRunning,
            fullImportCompleted: mergedImportCompleted,
            syncGeneration: firstPage.syncGeneration ?? syncGeneration,
            phase: firstPage.phase ?? phase,
            initialTargetCount: Self.monotonicMax(initialTargetCount, firstPage.initialTargetCount),
            initialMetadataCount: Self.monotonicMax(initialMetadataCount, firstPage.initialMetadataCount),
            initialBodyTargetCount: Self.monotonicMax(initialBodyTargetCount, firstPage.initialBodyTargetCount),
            initialBodyReadyCount: Self.monotonicMax(initialBodyReadyCount, firstPage.initialBodyReadyCount),
            historyMetadataCount: Self.monotonicMax(historyMetadataCount, firstPage.historyMetadataCount),
            historyBodyReadyCount: Self.monotonicMax(historyBodyReadyCount, firstPage.historyBodyReadyCount),
            estimatedTotalCount: Self.monotonicMax(estimatedTotalCount, firstPage.estimatedTotalCount),
            initialWindowComplete: Self.monotonicCompletion(initialWindowComplete, firstPage.initialWindowComplete),
            historyMetadataComplete: Self.monotonicCompletion(historyMetadataComplete, firstPage.historyMetadataComplete),
            historyBodyComplete: Self.monotonicCompletion(historyBodyComplete, firstPage.historyBodyComplete),
            lastProgressAt: firstPage.lastProgressAt ?? lastProgressAt
        )
    }

    private static func monotonicMax(_ lhs: Int?, _ rhs: Int?) -> Int? {
        switch (lhs, rhs) {
        case let (lhs?, rhs?): max(lhs, rhs)
        case let (lhs?, nil): lhs
        case let (nil, rhs?): rhs
        case (nil, nil): nil
        }
    }

    private static func monotonicCompletion(_ lhs: Bool?, _ rhs: Bool?) -> Bool? {
        if lhs == true || rhs == true { return true }
        return rhs ?? lhs
    }

    private static func insertMergeKeysIfUnique(for row: GmailThreadRow, into seen: inout Set<String>) -> Bool {
        let keys = mergeKeys(for: row)
        if keys.contains(where: { seen.contains($0) }) {
            return false
        }
        seen.formUnion(keys)
        return true
    }

    private static func insertMergeKeys(for row: GmailThreadRow, into seen: inout Set<String>) {
        seen.formUnion(mergeKeys(for: row))
    }

    private static func mergeKeys(for row: GmailThreadRow) -> Set<String> {
        var keys: Set<String> = ["thread:\(row.threadID)", "source:\(row.latestSourceRecordID)"]
        if let entityID = row.entityID, !entityID.isEmpty {
            keys.insert("entity:\(entityID)")
        }
        if let aiGroupID = row.aiGroupID, !aiGroupID.isEmpty {
            keys.insert("group:\(aiGroupID)")
        }
        for update in row.lifecycleUpdates {
            keys.insert("source:\(update.sourceRecordID)")
        }
        return keys
    }

}

/// Incremental cursor-chain merge state. The set/dictionary index is built
/// once and each page only inspects its own rows. Keeping this state behind a
/// dedicated actor moves the large-history preparation off the main actor.
actor MailboxPageAccumulator {
    struct Diagnostics: Equatable, Sendable {
        let processedRowCount: Int
        let snapshotBuildCount: Int
    }

    private var sectionOrder: [String] = []
    private var rowsBySection: [String: [GmailThreadRow]] = [:]
    private var titlesBySection: [String: String] = [:]
    private var seenRowKeys: Set<String> = []
    private var metadata: MailboxResponse
    private var visibleRowCount = 0
    private var processedRowCount = 0
    private var snapshotBuildCount = 0

    init(_ initial: MailboxResponse) {
        metadata = Self.copy(initial, sections: [])
        sectionOrder = initial.sections.map(\.id)
        rowsBySection = Dictionary(uniqueKeysWithValues: initial.sections.map { ($0.id, $0.rows) })
        titlesBySection = Dictionary(uniqueKeysWithValues: initial.sections.map { ($0.id, $0.title) })
        for row in initial.sections.flatMap(\.rows) {
            Self.insertMergeKeys(for: row, into: &seenRowKeys)
        }
        visibleRowCount = initial.sections.reduce(0) { $0 + $1.rows.count }
        processedRowCount = visibleRowCount
    }

    func reset(to response: MailboxResponse) {
        metadata = Self.copy(response, sections: [])
        resetStorage(to: response)
    }

    func identitySnapshot() -> MailboxResponse {
        metadata
    }

    func nextCursor() -> String? {
        metadata.nextCursor
    }

    @discardableResult
    func append(_ page: MailboxResponse) -> Int {
        let currentVisibleRows = visibleRowCount
        let pageVisibleRows = page.sections.reduce(0) { $0 + $1.rows.count }
        processedRowCount += pageVisibleRows
        var appendedRows = 0

        for section in page.sections {
            if rowsBySection[section.id] == nil {
                sectionOrder.append(section.id)
                rowsBySection[section.id] = []
                titlesBySection[section.id] = section.title
            }
            for row in section.rows where Self.insertMergeKeysIfUnique(for: row, into: &seenRowKeys) {
                rowsBySection[section.id, default: []].append(row)
                appendedRows += 1
            }
        }
        visibleRowCount += appendedRows

        let total = max(visibleRowCount, max(metadata.totalThreads, page.totalThreads))
        let loaded = min(
            total,
            max(
                visibleRowCount,
                max(metadata.loadedThreads ?? currentVisibleRows, page.loadedThreads ?? pageVisibleRows)
            )
        )
        let useOnlyPageProgress = metadata.syncGeneration != nil
            && page.syncGeneration != nil
            && metadata.syncGeneration != page.syncGeneration
        metadata = MailboxResponse(
            label: metadata.label,
            totalThreads: total,
            unreadThreads: page.unreadThreads ?? metadata.unreadThreads,
            nextCursor: page.nextCursor,
            loadedThreads: loaded,
            windowDays: page.windowDays ?? metadata.windowDays,
            sections: [],
            readyCount: page.readyCount ?? metadata.readyCount,
            pendingCount: page.pendingCount ?? metadata.pendingCount,
            mailboxRevision: page.mailboxRevision ?? metadata.mailboxRevision,
            generatedAt: page.generatedAt ?? metadata.generatedAt,
            oldestImportedAt: page.oldestImportedAt ?? metadata.oldestImportedAt,
            fullImportRunning: page.fullImportRunning ?? metadata.fullImportRunning,
            fullImportCompleted: page.fullImportCompleted ?? metadata.fullImportCompleted,
            syncGeneration: page.syncGeneration ?? metadata.syncGeneration,
            phase: page.phase ?? metadata.phase,
            initialTargetCount: useOnlyPageProgress ? page.initialTargetCount : Self.monotonicMax(metadata.initialTargetCount, page.initialTargetCount),
            initialMetadataCount: useOnlyPageProgress ? page.initialMetadataCount : Self.monotonicMax(metadata.initialMetadataCount, page.initialMetadataCount),
            initialBodyTargetCount: useOnlyPageProgress ? page.initialBodyTargetCount : Self.monotonicMax(metadata.initialBodyTargetCount, page.initialBodyTargetCount),
            initialBodyReadyCount: useOnlyPageProgress ? page.initialBodyReadyCount : Self.monotonicMax(metadata.initialBodyReadyCount, page.initialBodyReadyCount),
            historyMetadataCount: useOnlyPageProgress ? page.historyMetadataCount : Self.monotonicMax(metadata.historyMetadataCount, page.historyMetadataCount),
            historyBodyReadyCount: useOnlyPageProgress ? page.historyBodyReadyCount : Self.monotonicMax(metadata.historyBodyReadyCount, page.historyBodyReadyCount),
            estimatedTotalCount: useOnlyPageProgress ? page.estimatedTotalCount : Self.monotonicMax(metadata.estimatedTotalCount, page.estimatedTotalCount),
            initialWindowComplete: useOnlyPageProgress ? page.initialWindowComplete : Self.monotonicCompletion(metadata.initialWindowComplete, page.initialWindowComplete),
            historyMetadataComplete: useOnlyPageProgress ? page.historyMetadataComplete : Self.monotonicCompletion(metadata.historyMetadataComplete, page.historyMetadataComplete),
            historyBodyComplete: useOnlyPageProgress ? page.historyBodyComplete : Self.monotonicCompletion(metadata.historyBodyComplete, page.historyBodyComplete),
            lastProgressAt: page.lastProgressAt ?? metadata.lastProgressAt
        )
        return appendedRows
    }

    func snapshot() -> MailboxResponse {
        snapshotBuildCount += 1
        let sections = sectionOrder.map { sectionID in
            GmailThreadSection(
                id: sectionID,
                title: titlesBySection[sectionID] ?? "",
                rows: rowsBySection[sectionID] ?? []
            )
        }
        return Self.copy(metadata, sections: sections)
    }

    func diagnostics() -> Diagnostics {
        Diagnostics(
            processedRowCount: processedRowCount,
            snapshotBuildCount: snapshotBuildCount
        )
    }

    private func resetStorage(to response: MailboxResponse) {
        sectionOrder = response.sections.map(\.id)
        rowsBySection = Dictionary(uniqueKeysWithValues: response.sections.map { ($0.id, $0.rows) })
        titlesBySection = Dictionary(uniqueKeysWithValues: response.sections.map { ($0.id, $0.title) })
        seenRowKeys = []
        for row in response.sections.flatMap(\.rows) {
            Self.insertMergeKeys(for: row, into: &seenRowKeys)
        }
        visibleRowCount = response.sections.reduce(0) { $0 + $1.rows.count }
        processedRowCount = visibleRowCount
    }

    private static func copy(
        _ response: MailboxResponse,
        sections: [GmailThreadSection]
    ) -> MailboxResponse {
        MailboxResponse(
            label: response.label,
            totalThreads: response.totalThreads,
            unreadThreads: response.unreadThreads,
            nextCursor: response.nextCursor,
            loadedThreads: response.loadedThreads,
            windowDays: response.windowDays,
            sections: sections,
            readyCount: response.readyCount,
            pendingCount: response.pendingCount,
            mailboxRevision: response.mailboxRevision,
            generatedAt: response.generatedAt,
            oldestImportedAt: response.oldestImportedAt,
            fullImportRunning: response.fullImportRunning,
            fullImportCompleted: response.fullImportCompleted,
            syncGeneration: response.syncGeneration,
            phase: response.phase,
            initialTargetCount: response.initialTargetCount,
            initialMetadataCount: response.initialMetadataCount,
            initialBodyTargetCount: response.initialBodyTargetCount,
            initialBodyReadyCount: response.initialBodyReadyCount,
            historyMetadataCount: response.historyMetadataCount,
            historyBodyReadyCount: response.historyBodyReadyCount,
            estimatedTotalCount: response.estimatedTotalCount,
            initialWindowComplete: response.initialWindowComplete,
            historyMetadataComplete: response.historyMetadataComplete,
            historyBodyComplete: response.historyBodyComplete,
            lastProgressAt: response.lastProgressAt
        )
    }

    private static func monotonicMax(_ lhs: Int?, _ rhs: Int?) -> Int? {
        switch (lhs, rhs) {
        case let (lhs?, rhs?): max(lhs, rhs)
        case let (lhs?, nil): lhs
        case let (nil, rhs?): rhs
        case (nil, nil): nil
        }
    }

    private static func monotonicCompletion(_ lhs: Bool?, _ rhs: Bool?) -> Bool? {
        if lhs == true || rhs == true { return true }
        return rhs ?? lhs
    }

    private static func insertMergeKeysIfUnique(
        for row: GmailThreadRow,
        into seen: inout Set<String>
    ) -> Bool {
        let keys = mergeKeys(for: row)
        if keys.contains(where: seen.contains) {
            return false
        }
        seen.formUnion(keys)
        return true
    }

    private static func insertMergeKeys(for row: GmailThreadRow, into seen: inout Set<String>) {
        seen.formUnion(mergeKeys(for: row))
    }

    private static func mergeKeys(for row: GmailThreadRow) -> Set<String> {
        var keys: Set<String> = ["thread:\(row.threadID)", "source:\(row.latestSourceRecordID)"]
        if let entityID = row.entityID, !entityID.isEmpty {
            keys.insert("entity:\(entityID)")
        }
        if let aiGroupID = row.aiGroupID, !aiGroupID.isEmpty {
            keys.insert("group:\(aiGroupID)")
        }
        for update in row.lifecycleUpdates {
            keys.insert("source:\(update.sourceRecordID)")
        }
        return keys
    }
}

extension GmailThreadRow {
    func markedRead(targetMessageID: String? = nil) -> GmailThreadRow {
        guard let targetMessageID else {
            return copy(
                unread: false,
                labelIDs: labelIDs.removingUnreadLabel(),
                labels: labels.removingUnreadLabel(),
                children: children?.map { $0.markedRead() }
            )
        }

        let nextChildren = children?.map { child in
            child.messageID == targetMessageID ? child.markedRead() : child
        }
        guard latestSourceRecordID == targetMessageID else {
            return copy(children: nextChildren)
        }
        return copy(
            unread: false,
            labelIDs: labelIDs.removingUnreadLabel(),
            labels: labels.removingUnreadLabel(),
            children: nextChildren
        )
    }

    func markedUnread(targetMessageID: String? = nil) -> GmailThreadRow {
        guard let targetMessageID else {
            return copy(
                unread: true,
                labelIDs: labelIDs.addingUnreadLabel(),
                labels: labels.addingUnreadLabel(),
                children: children?.map { $0.markedUnread() }
            )
        }

        let nextChildren = children?.map { child in
            child.messageID == targetMessageID ? child.markedUnread() : child
        }
        return copy(
            unread: true,
            labelIDs: labelIDs.addingUnreadLabel(),
            labels: labels.addingUnreadLabel(),
            children: nextChildren
        )
    }

    func copy(
        unread: Bool? = nil,
        labelIDs: [String]? = nil,
        labels: [String]? = nil,
        children: [GmailThreadChildRow]? = nil
    ) -> GmailThreadRow {
        GmailThreadRow(
            threadID: threadID,
            entityID: entityID,
            title: title,
            href: href,
            latestSourceRecordID: latestSourceRecordID,
            latestReceivedAt: latestReceivedAt,
            latestMessageAt: latestMessageAt,
            latestSubject: latestSubject,
            latestSender: latestSender,
            sender: sender,
            participants: participants,
            messageCount: messageCount,
            bodyReady: bodyReady,
            contentRevision: contentRevision,
            summary: summary,
            aiGroupID: aiGroupID,
            aiTitle: aiTitle,
            aiSummary: aiSummary,
            snippet: snippet,
            hasAttachments: hasAttachments,
            attachmentCount: attachmentCount,
            labelIDs: labelIDs ?? self.labelIDs,
            labels: labels ?? self.labels,
            unread: unread ?? self.unread,
            actionNeeded: actionNeeded,
            actionType: actionType,
            actionTypeKey: actionTypeKey,
            priority: priority,
            dashboardVisible: dashboardVisible,
            currentState: currentState,
            lifecycleState: lifecycleState,
            outcomeType: outcomeType,
            lifecycleUpdates: lifecycleUpdates,
            children: children ?? self.children,
            enrichmentStatus: enrichmentStatus,
            presentationStatus: presentationStatus,
            pendingAction: pendingAction
        )
    }
}

extension GmailThreadChildRow {
    func markedRead() -> GmailThreadChildRow {
        copy(
            unread: false,
            labelIDs: labelIDs.removingUnreadLabel(),
            labels: labels.removingUnreadLabel()
        )
    }

    func markedUnread() -> GmailThreadChildRow {
        copy(
            unread: true,
            labelIDs: labelIDs.addingUnreadLabel(),
            labels: labels.addingUnreadLabel()
        )
    }

    func copy(unread: Bool? = nil, labelIDs: [String]? = nil, labels: [String]? = nil) -> GmailThreadChildRow {
        GmailThreadChildRow(
            messageID: messageID,
            gmailThreadID: gmailThreadID,
            sender: sender,
            subject: subject,
            aiTitle: aiTitle,
            snippet: snippet,
            receivedAt: receivedAt,
            labelIDs: labelIDs ?? self.labelIDs,
            labels: labels ?? self.labels,
            unread: unread ?? self.unread
        )
    }
}

private extension Array where Element == String {
    func removingUnreadLabel() -> [String] {
        filter { $0.uppercased() != "UNREAD" }
    }

    func addingUnreadLabel() -> [String] {
        contains(where: { $0.uppercased() == "UNREAD" }) ? self : self + ["UNREAD"]
    }
}

extension ThreadReaderResponse {
    func appendingPage(_ page: ThreadReaderResponse) -> ThreadReaderResponse {
        var seen = Set(messages.map(\.id))
        let appendedMessages = messages + page.messages.filter { seen.insert($0.id).inserted }
        return ThreadReaderResponse(
            entityID: entityID,
            userID: userID,
            source: source ?? page.source,
            gmailThreadID: gmailThreadID ?? page.gmailThreadID,
            subject: subject ?? page.subject,
            title: title ?? page.title,
            summary: nil,
            totalMessages: max(totalMessages, page.totalMessages, appendedMessages.count),
            limit: max(limit, page.limit),
            offset: 0,
            hasMore: page.hasMore,
            messages: appendedMessages,
            contentRevision: page.contentRevision ?? contentRevision
        )
    }
}
