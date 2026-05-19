# macOS UI Contract

This file is an implementation contract for AI agents working on the macOS client.
Follow these rules literally. Do not reinterpret the product layout unless the user explicitly updates this file.

## Product Intent

The macOS client is simple, compact, minimal, and designed for quick reaction.
The UI should help the user see what needs attention, act quickly, and move on.

The app is one product. Inbox, To-do's, Calendar, and future screens must feel connected through shared typography, colors, rhythm, animation, and header behavior.

## Non-Negotiable Layout Rules

- Inbox content is full-width.
- To-do content is centered and narrow.
- Inbox rows have multiple columns.
- To-do rows do not copy Inbox columns.
- Do not make To-do full-width.
- Do not make Inbox centered/narrow.
- Do not change Inbox layout while fixing To-do typography or To-do expansion.
- Calendar card layout is already correct unless a task explicitly says to change it.

## Shared Design System

### Font

- Font family: SF Pro Rounded.
- Use system rounded font APIs.
- Do not introduce custom fonts.

### Symbols

- Use SF Symbols for all icons.
- Do not use emoji as product UI icons.

### Color

- Use Apple system colors where possible.
- Text is white on dark backgrounds and black on light backgrounds.
- Use opacity to create hierarchy. Do not introduce many unrelated text colors.

### Text Opacity Tokens

Use these semantic tokens everywhere:

- `primaryText`: 90% opacity.
- `secondaryText`: 75% opacity.
- `mutedText`: 50% opacity.
- `sectionText`: 25% opacity.
- `dividerText`: 10% opacity.

Use focus through opacity first, then weight only when necessary.

### Typography Tokens

Use these sizes across the macOS client:

- `large`: 22pt.
- `medium`: 20pt.
- `small`: 18pt.
- `extraSmall`: 16pt.

Default line-height/rhythm:

- `large`: 40pt line height.
- `medium`: 40pt line height.
- `small`: 28-32pt line height.
- `extraSmall`: 24pt line height.

Default weights:

- Section titles: `large`, semibold.
- Main row/item text: `medium`, regular.
- Supporting summary text: `small`, regular.
- Metadata/source text: `extraSmall`, regular or semibold only when needed.

Do not create page-specific font sizes without updating this contract.

## Shared Main Page Header

Every main signed-in page has the same header system:

- Hamburger menu at top-left.
- Same hamburger size on every page.
- Same top and leading metrics on every page.
- Same title font when a title is shown.
- Each screen may choose whether the title is visible.

Sidebar behavior:

- Clicking the hamburger opens a sidebar.
- Sidebar contains navigation items such as Inbox, Drafts, Sent, Spam, Calendar, and To-do's.
- Sidebar animation should be subtle: short opacity/move transition, no dramatic motion.

## Login Page

Header:

- No header.

Body:

- The login page is the first screen.
- It uses a simple visual idea: mail becomes to-do's.
- Use SF Symbols for the mail, arrow, and to-do/list concept.
- Show a Google sign-in button.
- The sign-in button text is direct and minimal.

Footer:

- No footer.

## Onboarding Animation

Header:

- No header.

Body:

- Goal: keep the user calmly occupied while Gmail is fetched and AI-derived Inbox/To-do data is prepared.
- Current version may be text-only.
- Later versions may become a richer onboarding screen.
- The current animation can show status text for roughly 30-60 seconds.
- Text animation should feel quiet and focused, not playful or noisy.

Footer:

- No footer.

## To-do Page

Purpose:

- A compact work page derived from Inbox.
- It shows what the user should react to now.
- It must feel related to Inbox, but it is not an Inbox clone.

Layout:

- To-do content is centered.
- Content width is narrow compared with Inbox.
- Preferred max width: 1000px.
- The content should visually occupy the middle area, leaving meaningful space on both sides.
- Do not stretch To-do content to full window width.

Header:

- Top-left: greeting or date, depending on the active product decision.
- Top-right: current contextual label such as weather or time, depending on the active product decision.
- If this contract conflicts with code, stop and ask before changing the header meaning.

Calendar card:

- Appears below the header.
- Shows today's calendar information only.
- If there are events, use this format:

```text
TIME  Event title
9:00  Meeting with Sam
```

- If there are no events, show that the user is free today.
- Keep this card compact.
- Do not turn it into a large calendar view.

Sections:

- Always render exactly these three sections:
  - Now
  - Later Today
  - Worth Knowing
- Section title: `large`, semibold, `sectionText`.
- Section divider: `dividerText`.
- Gap between section title/divider and rows must match the Inbox row rhythm.

Rows:

- Rows are derived from Inbox items.
- Row title text must match the Inbox-derived title exactly.
- Do not show Inbox sender/date columns in To-do rows.
- Row/item text: `medium`, regular, `primaryText`.
- Row/body line height: 40pt.
- Each row has a circle checkbox on the left.
- Checkbox uses SF Symbol `circle`.
- Completed/loading visual may use `checkmark.circle.fill`.
- Checkbox should be visually aligned with the row title baseline/rhythm.
- Gap between checkbox and title should be compact and consistent.

Row expansion:

- Clicking a row expands it.
- Clicking another row expands that row and collapses the previous row.
- Clicking the currently expanded row collapses it.
- Expansion must not make the clicked row jump to a new position.
- The row should expand in place.
- Avoid blue selected-row state on To-do rows.
- The interaction should behave like the reference app recording: the collapsed row becomes the expanded card.
- The card surface, row height, and detail reveal must feel like one continuous motion.
- Do not create a frame where a large empty card opens first and the detail text appears later.
- The title and checkbox lane stay visually anchored while the detail area grows underneath.
- Rows and section headers below the active row must stay visible and move together as one page reflow.
- Treat the interaction like one vertical stack: the clicked row changes height, and every sibling below it is pushed down by that height change.
- The To-do page stack should carry one expansion animation keyed to the active expanded row so row labels, checkboxes, and section headers share the same layout transaction.
- Opening should feel smooth and readable, roughly 280-320ms.
- Closing should be slightly quicker, roughly 220-260ms, without snapping or leaving ghost content.
- Detail content should live in its final row position; it should not be driven by a delayed fade, hidden duplicate measurer, or separate staged animation.
- Do not use z-index, elevation, overlay layers, opacity hiding, or section layering to fake the dropdown; those make rows disappear and return instead of sliding.

Expanded row layout:

```text
________________________________________________________________________
|                                                                      |
|  [circle] AI-generated title derived from Inbox                       |
|                                                                      |
|           Summary                                                    |
|                                                                      |
|           Action item                                      Source    |
|______________________________________________________________________|
```

Expanded row typography:

- Title: `medium`, regular, `primaryText`.
- Summary: `small`, regular, `secondaryText`.
- Action item: `small` or `medium` depending on available space, semibold, green.
- Secondary action: `small`, semibold, `secondaryText`.
- Source: `extraSmall`, regular, `mutedText`.

Expanded row spacing:

- Title row follows the same 40pt row rhythm as collapsed rows.
- Gap from title row to summary: 12-16pt.
- Gap from summary to actions/source row: 12-16pt.
- These two gaps must be visually equal.
- Source must have a trailing inset and must not touch the card edge.
- Summary and action text should align to the title text column, not to the checkbox.
- Checkbox remains in the checkbox lane.

Expanded row card:

- Background uses the shared panel fill.
- Border uses the shared panel border.
- Corner radius: 7pt.
- The card can extend slightly left/right to create containment, but not so much that it looks detached from the row.

### To-do Known Failure Cases And Tests

These are known visual failures from the screenshot and recording review.
Treat each one as a regression even when the app builds successfully.

#### 1. Header hierarchy is too heavy

Failure:

- The greeting and morning/afternoon brief can become too bold, too large, or too similar in weight.
- The brief can visually compete with the section rows.
- The header can contain too much text for the amount of space it occupies.

How to test:

- Open the To-do page in a normal desktop window and in a wide/full-screen window.
- Compare the greeting, the brief, the calendar card, section headers, and row titles in one screenshot.
- The greeting should be readable but calm.
- The brief should feel secondary to the actionable rows.
- The brief must not look like the loudest text on the page.
- If the brief wraps, it must still feel intentional and must not crowd the calendar card or first section.

#### 2. Header placement is too low and too centered

Failure:

- The greeting can appear too far down from the app chrome.
- The To-do header can feel disconnected from the hamburger/navigation anchor.
- The top content can drift into the middle of the page instead of starting from a stable top grid.

How to test:

- Open the To-do page after a cold launch.
- Draw an imaginary horizontal guide from the hamburger row into the content area.
- The greeting should sit on the same intentional top grid, not far below it.
- The time/weather/refresh control must align with the same header row.
- The header must keep the same top metrics after refresh, sidebar open/close, and page navigation.

#### 3. To-do content column is too wide

Failure:

- The To-do content can stretch too wide and lose the compact work-page feel.
- The content can feel like it occupies nearly the full page instead of the middle working area.
- The left and right side gaps can become visually unbalanced.

How to test:

- Test at full-screen width, a 1440px-ish desktop width, and a narrower window.
- The To-do content column should occupy the centered working area, roughly the middle half of the usable window on large screens.
- There should be meaningful empty space on both sides.
- The plus buttons, section dividers, calendar card, rows, and expanded cards must all align to the same column width.

#### 4. Calendar card background and rhythm are inconsistent

Failure:

- The calendar card can look visually different from the expanded To-do card or new To-do composer.
- The card can feel like a separate style instead of part of the same panel system.
- The card text can be too strong relative to the page.

How to test:

- Open To-do with no events and with calendar events if possible.
- Open a To-do row and also open the new To-do composer.
- Compare fill color, border, corner radius, height, text size, and text weight.
- The calendar card, expanded row card, and new To-do composer must feel like the same component family.

#### 5. Section title to row gap is too tight

Failure:

- `Now`, `Later Today`, and `Worth Knowing` can sit too close to the first row.
- The section title, divider, and first item can collapse into one visual line.
- The rhythm can differ across sections.

How to test:

- Open the To-do page with rows in all three sections.
- Compare the gap from section title/divider to the first checkbox/title row for each section.
- The gap must be clearly visible and consistent across `Now`, `Later Today`, and `Worth Knowing`.
- The divider should not visually collide with the first row checkbox or title.

#### 6. Row typography is inconsistent

Failure:

- To-do row titles can become too large or too bold.
- Collapsed rows, expanded titles, and Inbox-derived text can use visibly different sizes without reason.
- Section titles can become either too faint or too dominant.

How to test:

- Compare collapsed To-do rows, an expanded To-do title, and Inbox row text.
- Row titles should use the shared `medium`, regular style.
- Supporting summary text should stay smaller and secondary.
- Section labels should be readable but quiet.
- No row title should look like a page heading.

#### 7. Checkbox lane is not aligned

Failure:

- The circle checkbox can sit too far left, too high, or too close to the card edge.
- The checkbox and title can feel vertically mismatched.
- The checkbox lane can move between collapsed and expanded states.

How to test:

- Take screenshots of collapsed To-do rows and expanded rows.
- Draw a vertical guide through the center of all checkboxes.
- Every checkbox in every section should sit on the same x-axis.
- In an expanded card, the title baseline should visually align with the checkbox center.
- The top and left padding around the checkbox inside the card must feel balanced.

#### 8. Expanded card horizontal spacing is uneven

Failure:

- The expanded card can have visibly uneven left and right side gaps.
- The card can feel attached to one edge or detached from the row list.
- The content inside the card can fail to align with the collapsed row title column.

How to test:

- Expand the first item in `Now`.
- Expand one item in `Later Today`.
- Compare card left edge, card right edge, checkbox lane, title x-position, summary x-position, and action row x-position.
- The title, summary, and action text must share one text column.
- The card must have balanced horizontal breathing room inside the content column.

#### 9. Expanded row source metadata is misplaced

Failure:

- `Source: Gmail` can float too far left, sit too close to the card edge, or look unrelated to the action row.
- The source can fail to align vertically with `Open group` and `Open source`.
- The source can overlap or wrap in smaller widths.

How to test:

- Expand rows with Gmail sources in multiple sections.
- Check the action row at the bottom of the expanded card.
- `Open group` and `Open source` should sit on the left side of the action row.
- `Source: Gmail` should sit on the right side of the same row with a stable trailing inset.
- The source must never touch the card edge, overlap actions, or move to a random middle position.

#### 10. Expanded row internal gaps are inconsistent

Failure:

- The gap between title, summary, actions, and source can differ from one part of the card to another.
- The summary can appear too close to the title.
- The action row can appear too close to the summary or too close to the card bottom.

How to test:

- Expand a long-summary item and a short-summary item.
- Compare the title-to-summary gap and summary-to-action-row gap.
- These gaps should feel visually equal, roughly 12-16pt.
- The action row must have a stable bottom inset.

#### 11. New To-do composer does not match expanded row background

Failure:

- The new To-do composer can use a different panel fill or border than expanded rows.
- Opening the composer below an expanded row can make the page look like two unrelated card systems.

How to test:

- Expand a To-do row.
- Click the plus button for `Now`, `Later Today`, and `Worth Knowing`.
- Compare the composer background, border, corner radius, checkbox lane, text sizes, and button placement against the expanded row card.
- The composer must use the same panel language as expanded rows.

#### 12. Refresh failure message is inconsistent between Inbox and To-do

Failure:

- Inbox can show `Inbox could not refresh. Showing last saved state.` as a bottom toast.
- To-do can show `To-do's could not refresh. Showing last saved state.` inline near the greeting.
- The inline To-do message can overlap the greeting/header and visually break the page.

How to test:

- Force a refresh failure or use the saved-state/offline path for Inbox and To-do.
- On both pages, verify the failure notice appears as the same toast component in the same visual position.
- The toast must not change layout, push content, overlap the greeting, or appear as inline header text.
- The wording may be page-specific, but the visual treatment must be shared.

#### 13. Expansion animation pops instead of dropping down

Failure:

- Expanded content can appear instantly instead of being revealed by the card.
- Detail text can pop from above, teleport, or start at the wrong y-position.
- The card can open faster than the text, or close faster than the text.
- During close, detail text can lag behind and overlap the next row.

How to test:

- Record the To-do page at 60fps or capture timed screenshots during the animation.
- Click the first `Now` row to open it.
- Pause or inspect frames around 0.05s, 0.15s, 0.30s, and the final resting state.
- Repeat while closing the same row.
- Repeat by switching from one expanded row to another.
- The row title and checkbox must not change x/y position before, during, or after the animation.
- The card should reveal downward from the row in place.
- Detail text should be clipped/revealed by the expanding card, not painted over neighboring rows.
- Lower rows and section headers should move together as one group.

#### 14. Animation creates ghost text and row overlap

Failure:

- Text from the second row can appear inside the expanded card.
- Multiple row titles can overlap during open/close.
- Section headers such as `Later Today` or `Worth Knowing` can pass through row text.
- The page can show blank vertical chasms while rows are animating.

How to test:

- Open and close the first `Now` row several times.
- Then open the first `Later Today` row while the `Now` row is open.
- Inspect the recording frame by frame.
- At no frame should inactive row text appear inside the active card.
- At no frame should section headers overlap row titles.
- There should never be two visible versions of the same row title.
- At rest, only one row may be expanded.

#### 15. Expanded row must not move its anchor

Failure:

- The clicked item can shift its title position while expanding.
- The checkbox can move relative to the title.
- The row can feel like it jumps to a new card instead of opening in place.

How to test:

- Before clicking a row, take a screenshot or note the checkbox and title position.
- Click the row and capture frames during open and final state.
- Close the row and capture frames during close and final state.
- The checkbox center and title baseline for the clicked row must stay anchored.
- Only the height below the title row should change.

## Inbox Page

Purpose:

- A typical inbox view.
- It is full-width and optimized for scanning many messages.

Layout:

- Inbox content is full-width.
- Inbox rows have columns:
  - status/disclosure lane
  - sender
  - subject/title
  - time/date
- Do not center or narrow the Inbox content.

Header:

- Top-left shows hamburger and title `Inbox`.
- Title uses the shared header title rule.

Sections:

- Example sections:
  - Today
  - Yesterday
  - Last seven days
- Section title: `large`, semibold, `sectionText`.
- Divider: `dividerText`.
- Section title and row rhythm must match the shared 40pt rhythm.

Rows:

- Inbox row text: `medium`, regular.
- Row/body line height: 40pt.
- Read rows: `mutedText`.
- Unread rows: `primaryText`.
- Selected row may use the platform blue highlight.
- Do not apply the To-do checkbox style to Inbox rows.

### Inbox Known Failure Cases And Tests

These Inbox failures are separate from To-do failures.
Do not fix them by making Inbox look like the To-do page.

#### 1. Inbox section hierarchy is too faint or uneven

Failure:

- `Today`, `Yesterday`, and `Last seven days` can become too faint or too visually detached from their rows.
- Section dividers can be misaligned with the columns.
- The selected row highlight can make the section hierarchy harder to read.

How to test:

- Open Inbox with read, unread, and selected rows visible.
- Verify that section titles are quiet but still readable.
- Compare section title x-position, divider start/end, and row column alignment.
- The selected row may use platform blue, but it must not change section spacing or column positions.

#### 2. Inbox read row opacity can become too dim

Failure:

- Read Inbox rows can be so muted that sender, subject, and date are hard to scan.
- Unread rows can become too loud compared with the rest of the page.

How to test:

- Open Inbox with a mix of read and unread rows.
- Confirm read rows are secondary but legible.
- Confirm unread rows are clear without looking like oversized headings.
- Sender, subject, and date columns must stay aligned in both read and unread rows.

## Interaction Rules

- Interactions should be fast and calm.
- Prefer opacity and small movement over large motion.
- Use short ease-in-out animations.
- Typical durations:
  - Small UI state: 120-180ms.
  - Navigation/sidebar: 180-220ms.
  - Page/stage transition: 350-450ms.
- Avoid animations that make text jump or change position unexpectedly.

## Required Visual QA Workflow

Use this workflow after every macOS visual change.
Do not mark a visual issue fixed from a final resting screenshot only.

1. Build and relaunch the macOS app.
2. Open the exact affected page, not a stale screenshot or a video player window.
3. Capture the resting state before interaction.
4. Perform the interaction once slowly and once repeatedly.
5. For animations, record or inspect at 60fps when possible.
6. Check at least four animation moments: start, early frame, mid frame, final frame.
7. Verify both open and close directions.
8. Verify switching from one expanded row directly to another expanded row.
9. Verify the same interaction in `Now`, `Later Today`, and `Worth Knowing`.
10. Verify refresh-failure/saved-state UI on both Inbox and To-do.
11. Attach or save the failing frame when a problem is visible only during motion.

Minimum To-do screenshots to inspect:

- Collapsed To-do page.
- To-do with first `Now` row expanded.
- To-do with one `Later Today` row expanded.
- To-do with new item composer open.
- To-do refresh failure toast.
- At least three animation frames from an open/close recording.

Minimum Inbox screenshots to inspect:

- Inbox with no selected row.
- Inbox with a selected row.
- Inbox refresh failure toast.
- Inbox with `Today`, `Yesterday`, and `Last seven days` visible.

## AI Agent Rules

- Before editing UI, identify which screen owns the requested change.
- Edit only the files needed for that screen.
- Do not touch unrelated views.
- Do not change backend contracts unless the user explicitly asks.
- Do not change Inbox layout when fixing To-do.
- Do not change To-do width when fixing expanded-row spacing.
- Do not change calendar card when fixing row typography.
- If the request conflicts with this contract, ask before implementing.
- After implementation, build the macOS app.
- If the change is visual, relaunch the app and inspect the affected screen when possible.
