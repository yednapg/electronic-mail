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
            sections: nextSections,
            readyCount: readyCount,
            pendingCount: pendingCount,
            oldestImportedAt: oldestImportedAt,
            fullImportRunning: fullImportRunning,
            fullImportCompleted: fullImportCompleted
        )
    }
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
