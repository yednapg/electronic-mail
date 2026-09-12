# Electronic Mail — corrected iPhone design system

## Correction

The first mockups lost the original app's compact information density, repeated the current-view title, enlarged controls, and varied geometry between screens. This revision returns to the desktop references and defines one shared system before generating the remaining screens.

The product identity is a pure black canvas, quiet text-first lists, restrained charcoal controls, blue actions, and small semantic AI accents. Navigation supports the mail content.

## Shared design decisions

| Element | Reference specification |
| --- | --- |
| Phone viewport | 390 × 844 logical points |
| Outer horizontal inset | 20 points |
| List text rail | 36 points, with a reserved unread-dot gutter |
| Trailing text/date rail | 370 points |
| Spacing scale | 4, 8, 12, 16, 24 points |
| Navigation title where required | 17-point semibold |
| Sender | 17-point semibold |
| Subject and list secondary text | 15-point regular; 20-point line height |
| Date and section heading | 13-point regular |
| Reader subject | 22-point semibold; 27-point line height |
| Reader body | 17-point regular; 23-point line height |
| Interactive region | At least 44 × 44 points |
| Visible round toolbar control | 36-point diameter, centered within its interactive region |
| Toolbar glyph | 20 points, consistent weight |
| Background | #000000 |
| Primary text | #F2F2F7 |
| Secondary text | #B0B0B8 |
| Metadata | #8E8E93 |
| Control background | #1C1C1E |
| Separator | #242426 |
| Primary action | #0A84FF |

These dimensions are design choices for this app. They are not all prescribed by Apple, and generated images do not prove pixel accuracy or accessibility conformance.

## Hierarchy and consistency

- Inbox and AI Inbox share the same compact navigation, equal-width text segments, date rail, row structure, and bottom actions.
- Sender is the primary row label. Subject is secondary. Time/date is tertiary. Unread state strengthens weight and adds a blue dot while preserving all alignment rails.
- Search expands on request; it does not consume a permanent additional row.
- Mailbox navigation uses standard-sized titles and repeated row geometry.
- The reader's subject is the primary heading. AI summary, sender metadata, and message body follow in distinct levels.
- Compose fields share label and value rails. The keyboard changes available height, not control dimensions.
- Account, settings, compose, reader actions, and formatting controls use the same circle and glyph geometry.
- The reader retains full original email content. Long messages scroll rather than shrink.

## Apple guidance consulted

Apple recommends using text styles to establish hierarchy and preserve it as text size changes. [Typography](https://developer.apple.com/design/human-interface-guidelines/typography?changes=_5)

Apple advises respecting safe areas and adapting consistently to changes in device size and context. [Layout](https://developer.apple.com/design/human-interface-guidelines/layout?changes=_____7&language=objc)

Toolbar actions should be prioritized and grouped consistently. [Toolbars](https://developer.apple.com/design/human-interface-guidelines/toolbars?changes=_2)

Related subviews can use a segmented control; equal segment widths help visual balance. [Segmented controls](https://developer.apple.com/design/human-interface-guidelines/segmented-controls?changes=_1_4)

Apple's interface-design tips recommend 44-point touch targets and aligned content. [UI Design Dos and Don'ts](https://developer.apple.com/design/tips/)

## Validation boundary

These deliverables are raster design mockups. Runtime safe areas, Dynamic Type, keyboard avoidance, VoiceOver, hit areas, and responsive behavior require verification in the implemented app. Actual export dimensions are recorded with the deliverables.

