# Electronic Mail — requested layout, revision 3

## Screen set

1. [Default AI Inbox](/Users/gauravpandey/dev/electronic-mail/output/imagegen/ios-2026-09-08-v3/01-ai-inbox.png)
2. [Mailbox menu](/Users/gauravpandey/dev/electronic-mail/output/imagegen/ios-2026-09-08-v3/02-mailboxes.png)
3. [Email reader](/Users/gauravpandey/dev/electronic-mail/output/imagegen/ios-2026-09-08-v3/03-message-reader.png)
4. [New mail](/Users/gauravpandey/dev/electronic-mail/output/imagegen/ios-2026-09-08-v3/04-new-mail.png)
5. [Reply](/Users/gauravpandey/dev/electronic-mail/output/imagegen/ios-2026-09-08-v3/05-reply.png)
6. [Composer with keyboard](/Users/gauravpandey/dev/electronic-mail/output/imagegen/ios-2026-09-08-v3/06-compose-keyboard.png)

## Applied layout

- AI Inbox is the default. Its header has only the hamburger menu and search icon. There is no title, Inbox/AI Inbox switch, expanded search field, or date-group heading.
- Every email row has a sender line, a subject line, and a summary area that reserves exactly two lines. Sender, subject and summary share their left alignment. Rows use equal spacing and no separator rules.
- The Inbox footer contains only the new-email button at the right.
- The mailbox menu has no row separators, header title, top account block or trailing row chevrons. Account and Settings are at the bottom.
- The reader shows the desktop's six actions at the top right: grid, reply, archive, mark unread, star and trash. Back remains at the left.
- The reader footer contains only Reply, Reply All and Forward in a centered group.
- New mail and reply share the same form columns. Their formatting controls are grouped at the bottom left, with Send at the bottom right. The visible compact set contains System, Aa, Bold, Italic and Attachment; underline, alignment and lists remain available through Aa.
- The keyboard moves the compose toolbar upward while preserving the control sizes and horizontal alignment.

## Responsive implementation notes

Reference layout: 390 × 844 logical points. Use real safe-area and keyboard insets when implementing it.

A mail summary should reserve two lines at the active text size, even when its text is short. Longer summaries truncate after the second line; the full message remains accessible in the reader. Sender and subject each use one line in the standard text-size layout. Accessibility text sizes may require a more spacious layout while preserving hierarchy.

At smaller widths, the six reader actions can wrap into a second compact top row to preserve touch targets. The composer may horizontally scroll its left formatting group while keeping Send fixed at the trailing edge.

These PNGs are visual mockups generated with built-in image_gen. They do not implement app behavior. Summary copy illustrates the layout using the supplied screenshot facts; it is not a live AI analysis of the inbox.

[Exact prompts](/Users/gauravpandey/dev/electronic-mail/output/imagegen/ios-2026-09-08-v3/prompts.md) · [Export details](/Users/gauravpandey/dev/electronic-mail/output/imagegen/ios-2026-09-08-v3/manifest.json)


All six final PNGs use the same 853 × 1844-pixel canvas.
