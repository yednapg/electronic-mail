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
            nextCursor: nextCursor,
            loadedThreads: loadedThreads,
            windowDays: windowDays,
            sections: nextSections,
            readyCount: readyCount,
            pendingCount: pendingCount,
            oldestImportedAt: oldestImportedAt,
            fullImportRunning: fullImportRunning,
            fullImportCompleted: fullImportCompleted
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
            GmailThreadSection(id: id, title: titlesBySection[id] ?? "", rows: Self.sortedRows(rowsBySection[id] ?? []))
        }
        let visibleRows = nextSections.reduce(0) { $0 + $1.rows.count }
        let currentVisibleRows = sections.reduce(0) { $0 + $1.rows.count }
        let pageVisibleRows = page.sections.reduce(0) { $0 + $1.rows.count }
        let loaded = min(
            page.totalThreads,
            max(
                visibleRows,
                (loadedThreads ?? currentVisibleRows) + (page.loadedThreads ?? pageVisibleRows)
            )
        )
        return MailboxResponse(
            label: label,
            totalThreads: page.totalThreads,
            nextCursor: page.nextCursor,
            loadedThreads: loaded,
            windowDays: page.windowDays ?? windowDays,
            sections: nextSections,
            readyCount: page.readyCount ?? readyCount,
            pendingCount: page.pendingCount ?? pendingCount,
            oldestImportedAt: page.oldestImportedAt ?? oldestImportedAt,
            fullImportRunning: page.fullImportRunning ?? fullImportRunning,
            fullImportCompleted: page.fullImportCompleted ?? fullImportCompleted
        )
    }

    func preservingLoadedPages(afterRefreshingFirstPage firstPage: MailboxResponse) -> MailboxResponse {
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
            GmailThreadSection(id: id, title: titlesBySection[id] ?? "", rows: Self.sortedRows(rowsBySection[id] ?? []))
        }
        let loaded = nextSections.reduce(0) { $0 + $1.rows.count }
        let firstPageLoaded = firstPage.loadedThreads ?? firstPage.sections.reduce(0) { $0 + $1.rows.count }
        let nextCursor = loaded > firstPageLoaded ? self.nextCursor : firstPage.nextCursor
        let isStillComplete = self.fullImportCompleted == true && loaded >= firstPage.totalThreads

        return MailboxResponse(
            label: firstPage.label,
            totalThreads: firstPage.totalThreads,
            nextCursor: nextCursor,
            loadedThreads: max(firstPage.loadedThreads ?? 0, loadedThreads ?? 0, loaded),
            windowDays: firstPage.windowDays ?? windowDays,
            sections: nextSections,
            readyCount: firstPage.readyCount ?? readyCount,
            pendingCount: firstPage.pendingCount ?? pendingCount,
            oldestImportedAt: firstPage.oldestImportedAt ?? oldestImportedAt,
            fullImportRunning: firstPage.fullImportRunning ?? fullImportRunning,
            fullImportCompleted: isStillComplete ? true : (firstPage.fullImportCompleted ?? fullImportCompleted)
        )
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

    private static func sortedRows(_ rows: [GmailThreadRow]) -> [GmailThreadRow] {
        rows.sorted { lhs, rhs in
            let leftDate = sortDate(for: lhs)
            let rightDate = sortDate(for: rhs)
            if leftDate != rightDate {
                return leftDate > rightDate
            }
            return lhs.threadID > rhs.threadID
        }
    }

    private static func sortDate(for row: GmailThreadRow) -> Date {
        let value = row.latestMessageAt ?? row.latestReceivedAt
        return ISO8601DateFormatter.mailboxMerge.date(from: value) ?? .distantPast
    }
}

private extension ISO8601DateFormatter {
    static let mailboxMerge: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withColonSeparatorInTimeZone]
        return formatter
    }()
}

extension GmailThreadRow {
    func copy(unread: Bool? = nil, labelIDs: [String]? = nil) -> GmailThreadRow {
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
            summary: summary,
            aiGroupID: aiGroupID,
            aiTitle: aiTitle,
            aiSummary: aiSummary,
            snippet: snippet,
            labelIDs: labelIDs ?? self.labelIDs,
            labels: labels,
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
            enrichmentStatus: enrichmentStatus
        )
    }
}
