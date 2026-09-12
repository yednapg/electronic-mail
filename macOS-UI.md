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

- Font family: the native SF Pro system face used by the mailbox UI.
- Use the shared system-font helpers; do not add page-local font designs.
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
- `sectionText`: 50% opacity.
- `dividerText`: 10% opacity.

Use focus through opacity first, then weight only when necessary.

### Typography Tokens

Use the shared `ElectronicMailType` sizes across the macOS client:

- `mailboxHeader`: 20pt.
- `sectionTitle`: 17pt.
- `body`: 15pt.
- `detail`, `small`, and `status`: 13pt.

Default line-height/rhythm:

- Header content: 28pt line rhythm.
- Section and body content: 20-24pt text rhythm inside the shared 35pt row height.
- Detail and metadata content: 18-20pt text rhythm.

Default weights:

- Section titles: `sectionTitle`, regular.
- Main row/item text: `body`, regular.
- Supporting summary and metadata/source text: `detail` or `small`, regular or semibold only when needed.

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

- The greeting/time rail shares the fixed app-header row with the menu, page title, Settings, and Search controls; it is not a second row inside the scrolling page.
- To-do's shows only Settings and Search in the trailing header rail. It does not show account, compose, or create controls.
- Settings and Search use the same 16pt `mailboxHeaderControlGap` used by Inbox and AI Inbox. Do not allow To-do's to fall back to the looser generic 32pt header gap.
- Within the centered To-do content column, `Good morning`, `Good afternoon`, or `Good evening` followed by the user's name sits at the left, and the current local time sits at the right.
- The greeting period and time update from the local system clock without requiring a refresh.
- The scrolling content begins directly with `You have…`; it must not repeat the greeting or user's name.
- Briefing concepts such as meetings, tasks, email, payments, and availability use SF Symbols rather than emoji.
- Use the shared `mailboxHeader` (20pt) SF Pro title scale for the briefing. The user's name, counts/action metrics, and status conclusions are semibold; connective copy is regular.
- Every text run in the briefing uses the same `primaryText` color. Hierarchy comes from weight only; semantic SF Symbols retain their semantic colors.

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
- Use the exact shared AI-summary rectangular surface: 12pt corner radius, clear Liquid Glass at 24% opacity on macOS 26+, and the matching material fallback on earlier systems.
- Event text and the empty-state message use the shared read-text color; do not mute the card until it becomes illegible.

Sections:

- Always render exactly these four sections:
  - Now
  - Later Today
  - Upcoming
  - Worth Knowing
- Section headers must use the exact Inbox section component role: 17pt regular SF Pro, 50% label opacity, a 38pt label row, and the system `Divider` beneath it.
- Each section header is a disclosure control. The title and chevron toggle that section between expanded and collapsed states without affecting the other sections.
- The first section has no extra top gap. Every later section begins 24pt after the preceding section's content, exactly like Inbox.
- There is no page-specific spacer between the divider and the first row; row placement follows the shared 35pt mailbox rhythm.
- An expanded empty section renders only its header and divider. Never show `Nothing here`, an empty-state row, or reserved row height.
- The page has no per-section plus button or inline create composer.

Data source and priority:

- AI Inbox matters are evidence sources, not To-do rows. Never copy every Gmail, Inbox, or `needs_you` row into To-do's.
- A separate AI projection may emit a To-do only when it identifies one concrete, unresolved action owned by the user, expressed as a verb and object, supported by cited message evidence, and classified as required, previously committed, or necessary to a declared goal.
- Optional opportunities, surveys, giveaways, marketing, vague suggestions, work owned by someone else, completed work, and low-confidence candidates are neither To-do's nor `Worth Knowing`. Reserve `Worth Knowing` for recent, materially useful status changes or work genuinely waiting on someone else; it has no completion checkbox.
- Before prioritizing, remove lifecycle-invalid actions: a later sent response resolves the request, a passed deadline expires it, and an undated request older than 14 days is stale. A future explicit deadline may keep an older request active. These are deterministic gates and must not depend on the model's claimed urgency.
- Never infer a due date. A passed deadline is expired, not overdue. Use `Now` only for an imminent or genuinely critical action supported by evidence from the last 24 hours; use `Later Today` only for an explicit today deadline; use `Upcoming` for a future explicit deadline or a still-fresh unscheduled action.
- Within each semantic section, sort explicit due dates first and then confidence and recency. Do not use arbitrary top-three/next-five buckets, starred state, or unread state as task eligibility.
- Manual tasks and calendar-derived tasks remain valid To-do sources and are merged with the independent AI To-do projection.
- Completing an AI-derived To-do changes only that To-do's status. It must not archive, mark read, or otherwise mutate the source Gmail thread. Opening its source navigates to the supporting AI Inbox matter.
- While unprojected candidates are being checked, retain already projected rows, show a short `Finding clear next actions` status, and refresh until classification finishes.

Rows:

- Email-backed rows are derived from the independent AI To-do projection and keep an evidence link to their source matter.
- Row title text uses the AI-derived action sentence rather than repeating the Inbox subject.
- When the action verb is present in the sentence, emphasize only that verb or phrase with the semantic blue or green accent.
- Do not show Inbox sender/date columns in To-do rows.
- Row/item text uses the exact read-email subject role: 15pt regular SF Pro with `readText` color. The semantic action phrase may change color, but it must not change size or weight.
- Row container height follows the shared 35pt mailbox row rhythm.
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

- Title: `body`, regular, `primaryText`.
- Summary: `detail`, regular, `secondaryText`.
- Action item: `detail` or `body` depending on available space, semibold, green.
- Secondary action: `detail`, semibold, `secondaryText`.
- Source: `small`, regular, `mutedText`.

Start control:

- The primary expanded-row control uses SF Symbol `play.fill` followed by the contextual action label.
- It means `Start this task`, not complete it.
- For `Reply`, open the source matter/thread with the reply workflow available. For `Review`, `Open`, and `Track`, open the relevant source. For trusted external flows such as `Pay`, `Register`, and `Confirm`, opening the supporting source is the safe fallback until a validated destination is available; never submit automatically.
- The adjacent `Open source` control always opens the supporting AI Inbox matter or Gmail thread without changing task state.
- Manual rows reveal their notes/editing state and do not show an inert play control.

Expanded row spacing:

- Title row follows the same shared 35pt row rhythm as collapsed rows.
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

#### 1. Daily briefing hierarchy drifts from the title scale

Failure:

- The briefing can become too small or faint and stop functioning as the page's primary summary.
- The entire sentence can become uniformly bold, erasing the scan hierarchy between connective copy and facts.
- Wrapping can create awkward short lines or crowd the calendar card.

How to test:

- Open the To-do page in a normal desktop window and in a wide/full-screen window.
- Compare the greeting, the brief, the calendar card, section headers, and row titles in one screenshot.
- The briefing must use the same 20pt scale as the mailbox page title and greeting rail.
- The user's name, counts, and status conclusion are semibold; connective copy is regular, with all text sharing the same primary color.
- The weight changes must remain visible even though the whole sentence shares one title-scale size.
- If the brief wraps, it must still feel intentional and must not crowd the calendar card or first section.

#### 2. Greeting/time rail placement is too low and too centered

Failure:

- The greeting/time rail can appear too far down from the app chrome or become a second body header.
- The To-do greeting/time rail can feel disconnected from the hamburger/navigation anchor.
- The top content can drift into the middle of the page instead of starting from a stable top grid.

How to test:

- Open the To-do page after a cold launch.
- Draw an imaginary horizontal guide from the hamburger row into the centered content column.
- The greeting and time must sit on that same fixed app-header grid.
- The briefing below the rail must begin directly with `You have…` and must not repeat the greeting.
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
- The section disclosures, dividers, calendar card, rows, and expanded cards must all align to the same column width.

#### 4. Calendar card background and rhythm are inconsistent

Failure:

- The calendar card can drift away from the existing AI-summary rectangle.
- A solid custom fill or border can make it feel like a separate style.
- The card text can be too strong relative to the page.

How to test:

- Open To-do with no events and with calendar events if possible.
- Open AI Inbox and compare its summary disclosure with the To-do calendar card.
- Compare glass/material behavior, 12pt corner radius, opacity, padding, and text contrast.
- The two summary rectangles must use the same shared surface implementation.

#### 5. Section title to row gap is too tight

Failure:

- `Now`, `Later Today`, and `Worth Knowing` can sit too close to the first row.
- The section title, divider, and first item can collapse into one visual line.
- The rhythm can differ across sections.

How to test:

- Open the To-do page with rows in all four sections.
- Compare the gap from section title/divider to the first checkbox/title row for each section.
- The divider-to-row relationship must exactly match Inbox; do not add a To-do-only spacer.
- The first section has no extra top spacing, and each later section adds exactly the Inbox 24pt section spacing.

#### 6. Row typography is inconsistent

Failure:

- To-do row titles can become too large or too bold.
- Collapsed rows, expanded titles, and Inbox-derived text can use visibly different sizes without reason.
- Section titles can become either too faint or too dominant.

How to test:

- Compare collapsed To-do rows, an expanded To-do title, and Inbox row text.
- Row titles should use the exact read-email subject style: 15pt regular SF Pro and read-text color.
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

#### 11. Empty and collapsed sections leave phantom content

Failure:

- An empty section displays `Nothing here` or reserves the height of a row.
- Collapsing a section hides its header or leaves its rows interactive.

How to test:

- Check an expanded empty section; only its title and divider should remain.
- Toggle every section through its title/chevron.
- Confirm each section collapses independently and all of its rows disappear.
- Confirm the page has no plus or inline create control.

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
- To-do with one whole section collapsed and another expanded.
- To-do with empty sections showing no placeholder rows.
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
