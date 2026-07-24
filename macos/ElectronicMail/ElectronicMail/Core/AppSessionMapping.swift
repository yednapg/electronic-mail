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
            GmailThreadSection(id: id, title: titlesBySection[id] ?? "", rows: rowsBySection[id] ?? [])
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
            fullImportCompleted: page.fullImportCompleted ?? fullImportCompleted
        )
    }

    func preservingLoadedPages(
        afterRefreshingFirstPage firstPage: MailboxResponse,
        discardStalePages: Bool = false
    ) -> MailboxResponse {
        let revisionChanged = mailboxRevision?.isEmpty == false
            && firstPage.mailboxRevision?.isEmpty == false
            && mailboxRevision != firstPage.mailboxRevision
        if discardStalePages || revisionChanged {
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
            fullImportCompleted: mergedImportCompleted
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
            messages: appendedMessages
        )
    }
}
