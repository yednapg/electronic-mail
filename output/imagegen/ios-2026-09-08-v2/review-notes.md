# Review notes

## Why the first pass failed

The desktop references prioritize mail content. The first generated set consumed too much vertical space with duplicate headings and an always-visible search field. Controls, title sizes, list densities and screen proportions drifted between images.

## Revised visual checks

- One compact Inbox/AI Inbox switch identifies the current list.
- Navigation controls share a baseline and common circle/glyph geometry.
- Sender, subject, and time have distinct levels of emphasis.
- Read and unread rows share text and timestamp alignment.
- Dates remain trailing-aligned and do not displace the sender column.
- AI status marks remain compact secondary indicators, differentiated by shape and color.
- Mailboxes use repeated rows and standard-sized navigation.
- Reader content scrolls; the summary can expand rather than forcing a large block into every reading state.
- Compose uses one primary Send action in the header, while formatting and attachment actions stay near editing content.
- Keyboard-visible compose preserves form columns and control sizes from keyboard-hidden compose.

The generated raster may approximate the specified spacing and typography. The design system is the source for implementation measurements; native behavior and accessibility must be checked in the app.

