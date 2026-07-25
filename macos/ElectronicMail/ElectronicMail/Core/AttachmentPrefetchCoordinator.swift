import Foundation

/// Bounded attachment prefetch used only for selected and newest priority mail.
/// Foreground downloads share any matching prefetch operation so the same
/// authenticated attachment is never fetched twice concurrently.
actor AttachmentPrefetchCoordinator {
    private struct Request {
        let userID: String
        let messageID: String
        let attachment: ThreadAttachment

        var id: String {
            "\(userID)\u{0}\(messageID)\u{0}\(attachment.attachmentID)"
        }
    }

    private struct ActiveDownload {
        let request: Request
        let automatic: Bool
        let userID: String
        let userGeneration: UInt64
        let globalGeneration: UInt64
        let task: Task<DownloadedAttachment, Error>
    }

    private let backend: AppClient
    private let cache: EncryptedAttachmentCache
    private let maximumConcurrentDownloads: Int
    private var queued: [String: Request] = [:]
    private var queueOrder: [String] = []
    private var activeDownloads: [String: ActiveDownload] = [:]
    private var activeAutomaticIDs: Set<String> = []
    private var foregroundReservations = 0
    private var userGenerations: [String: UInt64] = [:]
    private var globalGeneration: UInt64 = 0
    private var blockedUsers: Set<String> = []
    private var allUsersBlocked = false
    private var accountEpochs: [String: UInt64] = [:]
    private var invalidatedThroughAccountEpoch: UInt64 = 0

    init(
        backend: AppClient,
        cache: EncryptedAttachmentCache,
        maximumConcurrentDownloads: Int = 2
    ) {
        self.backend = backend
        self.cache = cache
        self.maximumConcurrentDownloads = max(1, maximumConcurrentDownloads)
    }

    func activate(userID: String, accountEpoch: UInt64? = nil) {
        guard acceptAccountEpoch(userID: userID, accountEpoch: accountEpoch) else { return }
        allUsersBlocked = false
        blockedUsers.remove(userID)
    }

    func enqueue(
        thread: ThreadReaderResponse,
        userID: String,
        selected: Bool,
        accountEpoch: UInt64? = nil
    ) {
        guard acceptAccountEpoch(userID: userID, accountEpoch: accountEpoch) else { return }
        guard !allUsersBlocked, !blockedUsers.contains(userID) else { return }
        for message in thread.messages {
            for attachment in message.attachments {
                guard eligibleForAutomaticPrefetch(attachment) else { continue }
                let request = Request(userID: userID, messageID: message.id, attachment: attachment)
                if selected, queued[request.id] != nil {
                    // Selection promotes an already queued newest-mail
                    // download instead of leaving it behind the rest of the
                    // automatic prefetch backlog.
                    queueOrder.removeAll { $0 == request.id }
                    queueOrder.insert(request.id, at: 0)
                } else if queued[request.id] == nil && activeDownloads[request.id] == nil {
                    queued[request.id] = request
                    if selected {
                        queueOrder.insert(request.id, at: 0)
                    } else {
                        queueOrder.append(request.id)
                    }
                }
            }
        }
        pump()
    }

    /// Used by a click/open. It returns cached bytes immediately, joins an
    /// active prefetch when possible, or starts one foreground request.
    func download(
        messageID: String,
        attachment: ThreadAttachment,
        userID: String,
        accountEpoch: UInt64? = nil
    ) async throws -> DownloadedAttachment {
        guard acceptAccountEpoch(userID: userID, accountEpoch: accountEpoch) else {
            throw CancellationError()
        }
        let requestedUserGeneration = userGenerations[userID, default: 0]
        let requestedGlobalGeneration = globalGeneration
        guard !allUsersBlocked, !blockedUsers.contains(userID) else {
            throw CancellationError()
        }
        if let cached = await cache.read(
            userID: userID,
            messageID: messageID,
            attachmentID: attachment.attachmentID
        ) {
            try Task.checkCancellation()
            guard isCurrent(
                userID: userID,
                userGeneration: requestedUserGeneration,
                globalGeneration: requestedGlobalGeneration
            ) else {
                throw CancellationError()
            }
            return cached
        }
        guard isCurrent(
            userID: userID,
            userGeneration: requestedUserGeneration,
            globalGeneration: requestedGlobalGeneration
        ) else {
            throw CancellationError()
        }

        let request = Request(userID: userID, messageID: messageID, attachment: attachment)
        if let active = activeDownloads[request.id] {
            let downloaded = try await active.task.value
            try Task.checkCancellation()
            guard isCurrent(
                userID: userID,
                userGeneration: requestedUserGeneration,
                globalGeneration: requestedGlobalGeneration
            ) else {
                throw CancellationError()
            }
            return downloaded
        }

        queued[request.id] = nil
        queueOrder.removeAll { $0 == request.id }
        if activeDownloads.count >= maximumConcurrentDownloads {
            foregroundReservations += 1
            await makeCapacityForForegroundDownload()
            foregroundReservations -= 1
            guard isCurrent(
                userID: userID,
                userGeneration: requestedUserGeneration,
                globalGeneration: requestedGlobalGeneration
            ) else {
                throw CancellationError()
            }
        }
        let active = makeDownload(request: request, automatic: false)
        activeDownloads[request.id] = active
        do {
            let downloaded = try await active.task.value
            try Task.checkCancellation()
            guard isCurrent(
                userID: userID,
                userGeneration: active.userGeneration,
                globalGeneration: active.globalGeneration
            ) else {
                throw CancellationError()
            }
            complete(
                id: request.id,
                userGeneration: active.userGeneration,
                globalGeneration: active.globalGeneration
            )
            return downloaded
        } catch {
            complete(
                id: request.id,
                userGeneration: active.userGeneration,
                globalGeneration: active.globalGeneration
            )
            throw error
        }
    }

    /// Cancels and *awaits* matching operations before the caller removes the
    /// encrypted files and account key. This closes the logout race where a
    /// late network response could recreate both after purge.
    func reset(
        userID: String? = nil,
        invalidatingAccountEpoch: UInt64? = nil
    ) async {
        if let invalidatingAccountEpoch {
            guard invalidatingAccountEpoch >= invalidatedThroughAccountEpoch else { return }
            invalidatedThroughAccountEpoch = invalidatingAccountEpoch
            if let userID,
               let activeEpoch = accountEpochs[userID],
               activeEpoch > invalidatingAccountEpoch {
                return
            }
            if let userID {
                accountEpochs[userID] = nil
            } else {
                accountEpochs = [:]
            }
        }
        let matching: [(String, ActiveDownload)]
        if let userID {
            blockedUsers.insert(userID)
            userGenerations[userID, default: 0] &+= 1
            let prefix = "\(userID)\u{0}"
            queued = queued.filter { !$0.key.hasPrefix(prefix) }
            queueOrder.removeAll { $0.hasPrefix(prefix) }
            matching = activeDownloads.filter { $0.value.userID == userID }
        } else {
            allUsersBlocked = true
            globalGeneration &+= 1
            queued = [:]
            queueOrder = []
            matching = Array(activeDownloads)
        }

        for (_, active) in matching {
            active.task.cancel()
        }
        for (_, active) in matching {
            _ = try? await active.task.value
        }
        for (id, active) in matching {
            guard let current = activeDownloads[id],
                  current.userGeneration == active.userGeneration,
                  current.globalGeneration == active.globalGeneration else {
                continue
            }
            activeDownloads[id] = nil
            activeAutomaticIDs.remove(id)
        }
        pump()
    }

    private func acceptAccountEpoch(userID: String, accountEpoch: UInt64?) -> Bool {
        guard let accountEpoch else { return true }
        guard accountEpoch >= invalidatedThroughAccountEpoch else { return false }
        if let activeEpoch = accountEpochs[userID], accountEpoch < activeEpoch {
            return false
        }
        accountEpochs[userID] = accountEpoch
        return true
    }

    private func pump() {
        while activeDownloads.count + foregroundReservations < maximumConcurrentDownloads,
              !queueOrder.isEmpty {
            let id = queueOrder.removeFirst()
            guard let request = queued.removeValue(forKey: id),
                  !allUsersBlocked,
                  !blockedUsers.contains(request.userID) else {
                continue
            }
            let active = makeDownload(request: request, automatic: true)
            activeDownloads[id] = active
            activeAutomaticIDs.insert(id)
            Task { [weak self] in
                _ = try? await active.task.value
                await self?.complete(
                    id: id,
                    userGeneration: active.userGeneration,
                    globalGeneration: active.globalGeneration
                )
            }
        }
    }

    private func makeDownload(request: Request, automatic: Bool) -> ActiveDownload {
        let userGeneration = userGenerations[request.userID, default: 0]
        let capturedGlobalGeneration = globalGeneration
        let task = Task { [weak self, backend, cache] () throws -> DownloadedAttachment in
            if let cached = await cache.read(
                userID: request.userID,
                messageID: request.messageID,
                attachmentID: request.attachment.attachmentID
            ) {
                try Task.checkCancellation()
                guard let self,
                      await self.isCurrent(
                          userID: request.userID,
                          userGeneration: userGeneration,
                          globalGeneration: capturedGlobalGeneration
                      ) else {
                    throw CancellationError()
                }
                return cached
            }
            let downloaded = try await backend.downloadAttachment(
                messageID: request.messageID,
                attachment: request.attachment
            )
            try Task.checkCancellation()
            guard let self,
                  await self.isCurrent(
                      userID: request.userID,
                      userGeneration: userGeneration,
                      globalGeneration: capturedGlobalGeneration
                  ) else {
                throw CancellationError()
            }

            // Descriptor sizes are advisory. Enforce the limit against the
            // authenticated bytes before an automatic prefetch reaches disk.
            if !automatic || downloaded.data.count <= EncryptedAttachmentCache.automaticFileLimit {
                do {
                    try await cache.write(
                        downloaded,
                        userID: request.userID,
                        messageID: request.messageID,
                        attachmentID: request.attachment.attachmentID
                    )
                } catch {
                    await Self.publishStorageWarning(userID: request.userID)
                }
            }
            return downloaded
        }
        return ActiveDownload(
            request: request,
            automatic: automatic,
            userID: request.userID,
            userGeneration: userGeneration,
            globalGeneration: capturedGlobalGeneration,
            task: task
        )
    }

    /// A user click must take priority over speculative work without allowing
    /// a third network request. Cancel one automatic operation, await its
    /// termination, and put it back at the front of the prefetch queue.
    private func makeCapacityForForegroundDownload() async {
        while activeDownloads.count >= maximumConcurrentDownloads {
            let selectedID = activeAutomaticIDs.first ?? activeDownloads.keys.first
            guard let selectedID, let active = activeDownloads[selectedID] else { return }

            if active.automatic {
                active.task.cancel()
            }
            _ = try? await active.task.value
            complete(
                id: selectedID,
                userGeneration: active.userGeneration,
                globalGeneration: active.globalGeneration
            )

            if active.automatic,
               isCurrent(
                   userID: active.userID,
                   userGeneration: active.userGeneration,
                   globalGeneration: active.globalGeneration
               ),
               queued[selectedID] == nil,
               activeDownloads[selectedID] == nil {
                queued[selectedID] = active.request
                queueOrder.removeAll { $0 == selectedID }
                queueOrder.insert(selectedID, at: 0)
            }
        }
    }

    private func isCurrent(
        userID: String,
        userGeneration: UInt64,
        globalGeneration: UInt64
    ) -> Bool {
        !allUsersBlocked
            && !blockedUsers.contains(userID)
            && self.globalGeneration == globalGeneration
            && userGenerations[userID, default: 0] == userGeneration
    }

    private func complete(id: String, userGeneration: UInt64, globalGeneration: UInt64) {
        guard let active = activeDownloads[id],
              active.userGeneration == userGeneration,
              active.globalGeneration == globalGeneration else {
            return
        }
        activeDownloads[id] = nil
        activeAutomaticIDs.remove(id)
        pump()
    }

    private func eligibleForAutomaticPrefetch(_ attachment: ThreadAttachment) -> Bool {
        guard let size = attachment.size else { return false }
        return size >= 0 && size <= EncryptedAttachmentCache.automaticFileLimit
    }

    @MainActor
    private static func publishStorageWarning(userID: String) {
        NotificationCenter.default.post(
            name: .offlineContentSyncStorageWarning,
            object: nil,
            userInfo: [
                "user_id": userID,
                "message": "Attachments could not be cached. Downloads still work online.",
            ]
        )
    }
}
