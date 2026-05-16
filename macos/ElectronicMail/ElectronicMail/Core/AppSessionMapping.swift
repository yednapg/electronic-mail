import Foundation

extension AppSessionResponse {
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
