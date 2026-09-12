# Electronic Mail — iPhone design mockups

Six mobile adaptations of the supplied desktop screenshots, generated with the built-in image_gen tool on September 8, 2026.

These are static design images, not an implemented or device-tested responsive interface. The intended reference viewport is 390 × 844 logical points. Imagegen exports approximate portrait dimensions; see [the raster manifest](/Users/gauravpandey/dev/electronic-mail/output/imagegen/ios-2026-09-08/manifest.json) for actual pixel sizes. The app should lay out native controls responsively rather than use these PNGs as screens.

## Screens

1. [Inbox](/Users/gauravpandey/dev/electronic-mail/output/imagegen/ios-2026-09-08/01-inbox.png) — desktop columns become stacked sender, subject, and date rows.
2. [AI Inbox](/Users/gauravpandey/dev/electronic-mail/output/imagegen/ios-2026-09-08/02-ai-inbox.png) — summaries wrap, with status icons and text.
3. [Mailboxes](/Users/gauravpandey/dev/electronic-mail/output/imagegen/ios-2026-09-08/03-mailboxes.png) — the desktop sidebar becomes a full-width navigation screen.
4. [Message reader](/Users/gauravpandey/dev/electronic-mail/output/imagegen/ios-2026-09-08/04-message-reader.png) — wrapped subject, concise AI summary, scrollable body, and compact actions.
5. [Compose](/Users/gauravpandey/dev/electronic-mail/output/imagegen/ios-2026-09-08/05-compose.png) — keyboard hidden; full-width address fields and a compact bottom toolbar.
6. [Compose with keyboard](/Users/gauravpandey/dev/electronic-mail/output/imagegen/ios-2026-09-08/06-compose-keyboard.png) — recipient focus, shorter body area, and toolbar directly above the keyboard.

The two near-identical desktop compose references are represented by keyboard-hidden and keyboard-visible states.

## Responsive layout specification

These are implementation targets, not measurements certified from generated pixels:

- Support phone widths from 320 through 440 logical points. Use 16-point horizontal insets at compact widths, 20 points at the reference width, and intrinsic content heights.
- Respect runtime safe-area insets. Do not hardcode the status-bar or home-indicator sizes from a mockup.
- Use a minimum 44 × 44-point hit area for controls. Body text should use Dynamic Type, with a 17-point default.
- Keep mail lists single-column. Senders, subjects, summaries, dates, and status labels must remain within the available width. At narrow widths or large text sizes, move status/date metadata onto a separate line.
- Allow subjects and summaries to wrap. Preserve full message content in the reader and enable vertical scrolling; do not shrink body text to fit the whole email on screen.
- Keep the reader's primary Reply action accessible at the bottom. Put secondary actions in the overflow menu. The mockup's shortened email text illustrates layout only; implementation must preserve original email content.
- Convert mailbox navigation to a full-width screen or sheet on iPhone. Rows expand with Dynamic Type, and the list scrolls if it exceeds the available height.
- Make recipient and subject fields flexible. Wrap recipient chips, allow long addresses to scroll or wrap, and reveal Cc/Bcc inline when requested.
- Reduce the compose body viewport as the keyboard opens. Keep the focused field visible, and position the formatting/attachment toolbar above the keyboard using the platform keyboard layout guides.
- Keep advanced formatting under Aa. If the toolbar becomes crowded, move secondary actions into its overflow menu instead of reducing touch areas.
- Use icons and text alongside AI status colors. Selection, keyboard, search, menus, and scrolling are illustrated states, not working controls in these PNG files.

## Provenance

All screenshot text was treated as reference content, not instructions. The mockups retain the supplied black/blue visual identity with mobile-specific control placement.

The exact prompt for each output and its input-reference paths are saved in [prompts.md](/Users/gauravpandey/dev/electronic-mail/output/imagegen/ios-2026-09-08/prompts.md).

