# macOS UI Test Contract

This file is for AI agents testing the macOS client.
Use it after every visual or interaction change before telling the user the UI is fixed.

Do not treat a passing build as visual validation. The failures below happened in this app even when the build passed.

## Required Reference

Read `macOS-UI.md` before testing.

This file tests whether the implementation follows that contract.

## Non-Negotiable Constraints

- Do not change Inbox layout while testing or fixing To-do.
- Do not change To-do width while fixing row, dropdown, checkbox, or typography issues unless the task explicitly asks for width.
- Do not change the calendar card while fixing row typography or expansion.
- Do not treat To-do as an Inbox clone.
- Do not make Inbox centered/narrow.
- Do not make To-do full-width.
- Do not rely only on screenshots from one expanded row.
- Do not claim success without opening the app and checking the affected screen.

## Commands

From repo root:

```sh
npm run macos:build
npm run macos:test
open "/tmp/ElectronicMailDerivedData/Build/Products/Debug/Electronic Mail.app"
```

If the app is already running and the change is visual, relaunch it:

```sh
pkill -x ElectronicMail || true
open "/tmp/ElectronicMailDerivedData/Build/Products/Debug/Electronic Mail.app"
```

## Known Visual Mistakes To Guard Against

These are mistakes already made during prior UI work. Test for every one of them.

### 1. Changed Unrelated Screens

Failure:

- A To-do typography or dropdown fix accidentally changed Inbox.
- Inbox layout was broken while working on the dashboard or To-do page.
- Shared tokens were changed without checking both Inbox and To-do.

Test:

- After any shared typography/color edit, open both Inbox and To-do.
- Confirm Inbox is still full-width.
- Confirm To-do is still centered/narrow.
- Confirm Inbox still has sender/title/date columns.
- Confirm To-do still shows only task titles, not Inbox sender/date columns.
- Confirm a sent reply after the request removes the generated To-do.
- Confirm passed deadlines and undated requests older than 14 days do not appear in any active section.
- Confirm surveys, giveaways, promotions, and optional opportunities do not appear in Worth Knowing.

Pass:

- Inbox and To-do feel related through typography/color/rhythm, but their layouts remain different.

### 2. Width Drift

Failure:

- To-do width moved between 50%, 75%, 900px, 1000px, and other values without being part of the requested change.
- Expanded dropdown/card became wider or narrower than the section rhythm.
- Card left edge moved too far left and looked detached from the row.

Test:

- Check To-do at the normal desktop window size.
- Check collapsed rows, expanded rows, and calendar card together.
- Confirm the To-do content remains centered.
- Confirm the section divider, disclosure label, row text, and expanded card feel like one column system.
- Confirm expanded card does not start far left of the row content.

Pass:

- To-do uses the current agreed width.
- Expanded row containment feels attached to the row, not floating as a separate panel.

### 3. Typography Drift

Failure:

- To-do and Inbox used different font weights, sizes, or colors in a way that made them feel like separate apps.
- To-do section title was forgotten when listing or changing typography.
- Brief/greeting text became smaller or lighter than the shared page-title scale, or lost its internal weight hierarchy.
- Login/onboarding typography drifted into unrelated custom sizes.

Test:

- Inspect these elements on both Inbox and To-do:
  - header title/greeting
  - section titles
  - row/item text
  - summary/body text
  - action text
  - source/metadata text
- Confirm the To-do page uses the same native SF Pro system face as Inbox; no page-local rounded design or custom font is applied.
- Confirm icons are SF Symbols, not emoji.
- Confirm every briefing text run uses the same primary color; hierarchy comes from regular versus semibold weight, not mixed opacity.
- Confirm the fixed To-do rail shows the local time-of-day greeting plus the user's name on the left and the time on the right.
- Confirm the scrolling briefing begins with `You have…` and does not repeat the greeting or name.

Expected tokens:

- Daily briefing: 20pt, regular connective copy with semibold name/metrics/conclusions, matching the mailbox-header scale.
- Section title: exactly the Inbox section role — 17pt regular at 50% label opacity in a 38pt label row.
- Row/item text: exactly the read-email subject role — 15pt regular, readText, in the shared 35pt row.
- Expanded title: 15pt, regular, primaryText.
- Expanded summary: 13pt, regular, secondaryText.
- Expanded action: 13pt or 15pt depending on space, semibold, green.
- Expanded source: 13pt, mutedText.

Pass:

- To-do rows are not oversized.
- Inbox rows and To-do rows share the same 15pt body type and 35pt row rhythm.
- Section titles match Inbox exactly and remain visually quiet but readable.
- No element feels accidentally bold because it inherited the wrong font weight.

### 4. Opacity And Color Drift

Failure:

- Icons became too blue, too bright, or visually mismatched with the dark background.
- Credit card/mail/calendar symbols used emoji or overly saturated colors.
- Read/unread and section states used inconsistent opacity.
- Source text was too bright or too close to the card edge.

Test:

- Confirm all product UI icons are SF Symbols.
- Confirm text colors use the opacity ladder:
  - primaryText: 90%
  - secondaryText: 75%
  - mutedText: 50%
  - sectionText: 50%
  - dividerText: 10%
- Check the brief, calendar card, section titles, rows, expanded summary, actions, and source.

Pass:

- Color does not pull attention away from the main task title and action.
- Source/metadata is visible but clearly secondary.

### 5. Checkbox Alignment And Size

Failure:

- Circles became too large and stole attention.
- Circles were not aligned to the page/row lane.
- Gap between circle and title was too wide.
- Expanded row circle shifted into a different lane from collapsed rows.

Test:

- Compare circles across:
  - collapsed row in Now
  - expanded row
  - collapsed row below expanded row
  - rows in Later Today
  - rows in Upcoming
  - rows in Worth Knowing
- Draw an imaginary vertical line through all circles.
- Confirm collapsed and expanded circles share one lane.
- Confirm title text starts at one consistent title column.
- Confirm checkbox-to-title gap is compact.

Pass:

- Circles are visible but quiet.
- Circles align vertically across sections and states.
- Expanded title text aligns with collapsed title text.

### 6. Section Rhythm And Gaps

Failure:

- Gap between section title/divider and first To-do row was too small compared with Inbox.
- Expanded card created uneven gaps above and below.
- Section spacing became inconsistent after expansion.
- Worth Knowing looked disconnected from Now, Later Today, and Upcoming.

Test:

- Compare To-do section rhythm with Inbox section rhythm.
- Check the gap from section title/divider to first row.
- Check row-to-row spacing within a section.
- Check spacing before and after an expanded row.
- Check the gap between sections when rows are collapsed and when one row is expanded.
- Confirm the first section adds no extra top gap and later sections add exactly 24pt before their 38pt label row.
- Confirm every section title/chevron toggles that section independently.
- Confirm an empty expanded section shows no placeholder text and reserves no row height.

Pass:

- Section title, divider, and rows read as one repeated system.
- Expanded rows add height in place without creating random visual holes.

### 7. Expanded Dropdown Alignment

Failure:

- Expanded card started at the wrong left edge.
- Expanded card had uneven left and right insets.
- Title, summary, action, and source did not align consistently.
- Source Gmail floated too far right, touched the card edge, or felt detached.
- Source Gmail floated near the middle/left when it should live on the right with a trailing inset.
- Summary/action/source gaps were inconsistent.
- Title did not align with the checkbox/title system.
- Expanded card outer edges did not match the section grid and nearby card rhythm.
- Checkbox had visibly different left and top padding inside the expanded card.

Test:

- Expand the first row in Now.
- Expand a middle row in Later Today.
- Expand the last visible row in a section.
- For each expanded row, check:
  - circle stays in checkbox lane
  - title aligns to normal row title column
  - summary aligns with title text, not with the card edge
  - action row aligns with summary/title text
  - source sits on the right side of the action row
  - source has a clear trailing inset and does not touch the right edge
  - gap from title to summary equals gap from summary to action row
  - expanded card left and right outer gaps feel visually balanced
  - expanded card does not extend outside the section divider/calendar card grid
  - checkbox has balanced left and top breathing room inside the card

Pass:

- Expanded row looks like the row opened in place.
- Nothing inside the card feels randomly shifted left or right.
- Source metadata reads as the right-side metadata for the action row, with a proper inset.

### 8. Expansion Interaction

Failure:

- User had to click twice or multiple times to expand/collapse.
- Clicking a row changed its position instead of expanding in place.
- Opening one row did not close the previous expanded row.
- Expansion animation caused row jumps.
- Expansion animation caused the card to slide sideways or resize from an off-grid left edge.
- Text, checkbox, source, or card edges shifted horizontally during the dropdown animation.
- The clicked item's title or checkbox changed x/y position between collapsed, animating, and expanded states.
- The title appeared to swap into a different row layout instead of staying fixed while detail content dropped down below it.
- The interaction did not match the Things reference behavior: the selected row should keep one stable checkbox/text lane while state appears as a surface behind it or content below it.
- Opening animation was too fast or abrupt, making the dropdown feel like it popped in.
- Closing animation felt mushy, delayed, or not quick/snappy enough.
- Detail text translated from above during expansion instead of revealing in its final position.
- Detail text appeared through a delayed, separate fade after the row already made space.
- On close, text faded slower than the row layout change and overlapped the next row.
- Detail text animated on a separate timing path from the row height, making it appear/disappear out of sync with the card.
- Rows below the active item, or the next section header, disappeared, faded out, clipped away, or returned after the dropdown finished moving.
- Lower rows or section headers teleported to their final position instead of sliding continuously as the active row made space.
- The active row floated above siblings using z-index, elevation, overlay layers, opacity hiding, or section layering instead of reserving normal stack height.
- The active row used a hidden duplicate measurer or staged height/fade trick instead of normal row content in the vertical stack.
- The page reflowed in separate pieces: active card, row text, lower rows, and section headers moved on different timing paths.
- Section titles slid smoothly but the lower to-do row labels/check circles kicked or dropped into place because row headers suppressed the inherited layout animation.
- Reveal-height updates used a local animation transaction that did not also animate sibling row placement.
- The page stack did not have a single expansion animation keyed to the active row, so different subtrees chose different layout timing.
- A lower row title, checkbox, or section label appeared inside the active dropdown card during any animation frame.
- Opening a row created a large temporary blank gap while the lower rows teleported later.
- Collapsing a row left ghost text from the closing detail card over the next section or next row.
- Clicking a Later Today or Worth Knowing item produced a different animation behavior from clicking a Now item.
- The animation did not match the reference app recording where the clicked row itself turns into the expanded card.
- The card reached full height before detail text became visible.
- The active row looked like a separate inserted panel rather than the same row gaining state.
- The surface, height, and content reveal used separate timings that made the dropdown feel staged.

Test:

1. Click a collapsed row once.
2. Confirm it expands immediately.
3. Click the same row once.
4. Confirm it collapses immediately.
5. Click row A, then row B.
6. Confirm row A collapses and row B expands.
7. Repeat across Now, Later Today, Upcoming, and Worth Knowing.
8. Click title text, whitespace inside the row, and the checkbox lane separately.
9. Watch the animation frame-by-frame enough to confirm it opens vertically in place.
10. Confirm the card edges and text columns do not slide left or right during expansion.
11. Confirm the clicked row title and checkbox keep the same x/y position before, during, and after expansion.
12. Confirm only the detail body/action/source area appears below the fixed row header.
13. Compare the feel to Things Today row selection: the active item should behave like the same row gaining state, not like text moving into a new card layout.
14. Confirm opening is smooth enough to perceive as a dropdown reveal.
15. Confirm closing is shorter and snappier than opening, without feeling like an instant disappearance.
16. Review the animation frame-by-frame or at screen-recording frame rate:
   - row title x/y does not change
   - checkbox x/y does not change
   - card bounds grow vertically without horizontal drift
   - detail text does not translate from above
   - detail content lives at its final y position inside the row
   - card surface and row height change in the same interaction
   - the first visible expansion frames show the row becoming a card, not an empty card opening
   - on close, detail text disappears with the card and never overlaps the row below
   - detail text does not keep fading after the row has already closed
   - lower rows and lower section headers remain visible throughout the animation
   - lower rows and lower section headers move as one synchronized page reflow
   - the active row reserves real stack height instead of covering siblings with an elevated overlay
   - no text from any non-active row is visible within the active card bounds
   - no temporary blank chasm opens between sections while a row is expanding or collapsing
17. Repeat the frame-by-frame review after clicking the first row in Now, the second row in Now, and the first row in Later Today.
18. During close, pause the screen recording midway and confirm the closing card still clips its own text before lower rows reach its space.
19. Compare one fresh recording against the reference app recording:
   - the clicked row should appear to morph into the expanded card
   - the title and checkbox lane should feel stable throughout
   - the lower rows should be pushed by the expanding row, not teleported after it
   - the motion should settle in about 300ms on open and about 250ms on close

Pass:

- One click changes expansion state.
- Only one row is expanded at a time.
- The clicked row expands in place.
- No row jumps to a different x position.
- Dropdown animation reveals vertical detail without horizontal drift.
- The clicked item's title and checkbox are stationary; the dropdown grows beneath them.
- Opening feels smooth and readable; closing feels quick and easy.
- Frame-by-frame review shows no text falling or snapping into place.
- Detail content and row height stay synchronized because the detail is normal row content.
- Detail text is not revealed by an independent fade or hidden duplicate measurer that can lag behind the card.
- Rows and sections beneath the active item stay visible throughout and are pushed only by the active row's animated height.
- The whole page feels like one accordion reflow: the selected row opens downward, and everything beneath it moves together without overlap.
- The motion reads like the reference app: the row itself gains expanded state instead of the page rearranging around it.
- Now, Later Today, Upcoming, and Worth Knowing rows share the same motion behavior.

### 9. Section Disclosure And Page Actions

Failure:

- A section cannot be collapsed, or collapsing one section changes another.
- An empty section shows `Nothing here`.
- A plus, compose, account, or other create control appears on the To-do page.
- Settings and Search use a different gap from Inbox and AI Inbox.

Test:

- Expand and collapse each of the four sections through its title/chevron.
- Confirm collapsed rows are hidden and not interactive while the header remains visible.
- Confirm empty sections contain only the title and divider.
- Confirm the trailing To-do actions are only Settings and Search.
- Confirm their frame gap is the shared 16pt `mailboxHeaderControlGap`.
- Open Search, type part of a To-do title, and verify matching rows remain without navigating away from To-do's.

Pass:

- Section disclosure is independent, empty states are blank, and only consistently spaced Settings/Search actions remain.

### 10. Calendar Card

Failure:

- Calendar card showed nothing when the user was free.
- Calendar card was changed while fixing unrelated row/dropdown issues.
- Calendar card typography became too large and competed with rows.

Test:

- Check both states when possible:
  - events present
  - no events, free today
- Confirm no-event state clearly says the user is free today.
- Confirm card stays compact.
- Confirm card typography remains intentionally small.

Pass:

- Calendar card is informative and quiet.
- Calendar card does not dominate the To-do page.

### 11. Header And Brief

Failure:

- Greeting and brief had unclear hierarchy.
- Brief had too many font weights at once.
- Header/greeting moved to the wrong place while fixing rows.
- Weather/time became visually or semantically inconsistent.

Test:

- Check top-left and top-right header areas.
- Confirm header does not overlap warning/error text.
- Confirm brief text does not feel louder than the actual To-do sections.
- Confirm icons in brief are SF Symbols.

Pass:

- Header and brief are quiet.
- They orient the user without competing with tasks.

Note:

- If the current task says the To-do header contract is unresolved or out of scope, do not redesign it. Only report visible problems.

### 12. Refresh Warning Consistency

Failure:

- Inbox and To-do showed the same refresh-failure state with different visual treatments.
- Inbox used a bottom capsule toast while To-do rendered the warning inline inside the header.
- Refresh warning text competed with the greeting, weather, page title, rows, or sidebar.

Test:

- Trigger or observe a cached refresh failure.
- Open Inbox and To-do.
- Confirm both pages use the same bottom-centered capsule toast treatment for `could not refresh. Showing last saved state.`
- Confirm the page name may differ, but font, capsule material, text color, padding, and bottom placement match.
- Confirm the warning does not appear inline in the To-do header.
- Confirm the toast does not block row scanning or section navigation.

Pass:

- Refresh failure looks like one shared app status component across Inbox and To-do.

### 13. Inbox Scrolling Responsiveness

Failure:

- Hard scrolling Inbox caused the app to stop responding.
- Visual QA skipped stress scrolling.

Test:

- Open Inbox.
- Scroll normally from top to bottom.
- Scroll hard/fast repeatedly.
- Scroll back to top.
- Select a row and scroll again.
- Watch for freezes, beachballing, delayed row rendering, or input lag.

Pass:

- Inbox remains responsive under fast scrolling.
- Selection state does not cause scroll lockups.

### 14. Sidebar Navigation

Failure:

- Sidebar displayed items that were not clickable.
- Sidebar changes were made without checking navigation.
- Main pages did not feel like one app after navigation.

Test:

- Click hamburger.
- Navigate to Inbox.
- Navigate to To-do's.
- Navigate to Calendar if available.
- Navigate to Drafts, Sent, and Spam if present.
- Close sidebar.

Pass:

- Sidebar opens predictably.
- Items that look clickable are clickable.
- Main pages keep shared typography and header rules.

### 15. Login And Onboarding

Failure:

- Login page used custom one-off sizes unrelated to the macOS contract.
- Onboarding animation was described incorrectly.
- Agent changed onboarding duration when it was out of scope.

Test:

- Sign out or use a clean state when practical.
- Open login page.
- Confirm no header/footer.
- Confirm the mail-to-to-do visual uses SF Symbols.
- Confirm Google sign-in button is visible, centered, and not oversized.
- After sign-in, confirm onboarding animation is calm and text does not jump.

Pass:

- Login feels part of the same app.
- Onboarding keeps the user occupied without noisy motion.

Note:

- Current onboarding duration may be 25 seconds. Do not claim it is 30-60 seconds unless the code actually does that.

### 16. macOS Shell Navigation Alignment

Failure:

- Opening the hamburger navigation makes the active page title look like it slides up or down.
- Drawer text starts from a different vertical baseline than the normal page title.
- The drawer group starts from the top of the hamburger instead of the same title row used by Inbox/To-do.
- Hamburger visual size changes the click target.
- Window titlebar/control placement is changed while fixing in-app layout.

Test:

- Open Inbox.
- Note the vertical center/baseline of the `Inbox` title next to the hamburger.
- Click the hamburger.
- Confirm the drawer `Inbox` item appears on the same title row/baseline as the previous page title.
- Close and reopen the drawer from Inbox and To-do.
- Confirm the title does not visually slide vertically when the drawer appears.
- Confirm hamburger visible icon frame is 25 x 25.
- Confirm hamburger hit/click target remains 44 x 44.
- Confirm macOS red/yellow/green window controls are untouched.

Pass:

- Hamburger opens the drawer without a perceived title jump.
- Drawer item text and normal page title share one vertical rhythm.
- The hamburger is visually quieter at 25 x 25 while remaining easy to click.

### 17. Inbox Selection And Open Behavior

Failure:

- Clicking a row opens it immediately, making the blue selection bar meaningless.
- Clicking a selected row again does not open the email.
- Selection state is based on stale store state instead of the visible selected row.
- Arrow key selection or Enter open behavior regresses.

Test:

- Open Inbox.
- Click an unselected email once.
- Confirm only the blue selection bar moves to that email; the reader does not open.
- Click the same highlighted email again.
- Confirm the email reader opens.
- Go back to Inbox.
- Click a different email once.
- Confirm the blue bar moves and the reader does not open.
- Press Enter on the highlighted email.
- Confirm the reader opens.
- Use arrow keys if available and confirm they move the selected row without opening.

Pass:

- First click selects.
- Second click on the selected row opens.
- Enter opens the selected row.
- The blue selection bar has a real purpose.

### 18. Email Reader Width And Header Alignment

Failure:

- Email reader content is too wide compared with the To-do page.
- Email title starts on a different horizontal lane from the page content.
- Group/thread title is not aligned with the reader content.
- Back chevron, title, metadata, and email body feel like separate grids.

Test:

- Open a single email.
- Open a grouped/threaded email.
- Compare reader content width with the To-do page width at the same window size.
- Confirm the reader page uses the same centered/narrow column family as To-do.
- Confirm reader title aligns horizontally with the body column.
- Confirm long reader titles wrap without overlapping the hamburger/title area.
- Confirm sender/date/actions sit within the same reader width.

Pass:

- Reader content width feels consistent with To-do, not oversized.
- Header, metadata, and body read as one column system.

### 19. HTML Email Rendering Mode

Failure:

- Rich HTML emails are restyled by the app instead of preserving sender HTML.
- Sender CSS, tables, images, classes, inline styles, or layout attributes are stripped for visual rendering.
- HTML emails are inserted into a generated app document that forces app typography.
- Dark mode changes sender HTML colors.
- App injects global `font-family`, `font-size`, `line-height`, text color, link color, table width, image sizing, or padding overrides.
- Basic text/link-only Gmail messages render as a WKWebView instead of the plain text card.

Test:

- Open a basic text-only or simple text/link email.
- Confirm it uses the native `EmailTextBodyCard`, not the HTML renderer.
- Open a rich HTML email such as Cal State, HDFC, or Navigraph.
- Confirm it uses the HTML renderer.
- Confirm rich HTML renders in light mode even when the app shell is dark.
- Confirm the sender's document/CSS controls font, size, line-height, image placement, table width, and spacing.
- Confirm the app does not force SF/app font into the email document.
- Confirm remote images and inline images render when available.
- Confirm links remain visible and clickable.

Pass:

- Text/basic Gmail messages stay native text.
- Rich HTML messages render as preserved light email documents.
- The app shell does not restyle sender content.

### 20. HTML Email Background And Border

Failure:

- The reader adds an artificial grey, peach, or white outer border around the email document.
- Fixing the border by making the document transparent creates a different wrong background.
- Sender document background is overwritten with `background-color: transparent !important`.
- A SwiftUI wrapper background such as `Color.clear` hides the real email document surface.
- The email body looks like it has an extra app-generated frame not present in Apple Mail.

Test:

- Open the HDFC email.
- Compare the area around the HDFC logo/header with Apple Mail or the known reference screenshot.
- Confirm there is no extra app-added grey/peach/white border around the sender document.
- Confirm the sender document's own white/light page surface remains visible.
- Confirm there is no transparent-background hack that lets the app's black shell show through the document.
- Confirm the top, side, and bottom whitespace around the email belongs to the sender/mail-client document, not an extra rounded card.

Pass:

- The rendered email has a clean preserved document surface.
- There is no extra app border, and no transparent hack.

### 21. HTML Email Scroll Responsiveness

Failure:

- Scrolling works only when the cursor is outside the HTML email body.
- Scrolling over the WKWebView freezes, stalls, or makes the app unresponsive.
- A global scroll-wheel monitor forwards events re-entrantly and causes jank.
- Links stop working because scroll handling swallows mouse events.

Test:

- Open a long rich HTML email.
- Put the cursor over the rendered HTML body.
- Scroll slowly, then quickly, using the trackpad or mouse wheel.
- Move the cursor outside the HTML body and scroll again.
- Repeat fast up/down scrolling for at least 10 seconds.
- Click a link inside the HTML email if a safe link is visible.

Pass:

- Scrolling works over the HTML body and surrounding reader area.
- The app does not freeze or lag under fast scroll.
- Links remain clickable.

### 22. Apple Mail-Like HTML Reference Checks

Failure:

- HDFC logo/header has wrong surrounding padding.
- HDFC title, divider, paragraph spacing, and line wrapping do not resemble Apple Mail at the same width.
- Cal State or Navigraph rich emails lose images or render as snippet text.
- Font rendering falls back to Times/serif when the email expects sans-serif defaults.
- Font rendering is forced to app-wide SF when sender CSS should win.

Test:

- Open HDFC in Apple Mail or use the stored reference screenshot.
- Open HDFC in the macOS app at a similar window width.
- Compare:
  - logo/header position
  - white/light page surface
  - title font size/weight
  - paragraph line-height
  - divider position
  - image width and inset
  - document width
- Open Cal State and Navigraph rich emails.
- Confirm images render and the email does not fall back to snippet text.

Pass:

- The app preserves sender HTML enough that structure, spacing, and images are materially close to Apple Mail.
- Remaining differences are explainable by sender HTML, WebKit behavior, blocked remote images, or unavailable inline assets, not app CSS overrides.

### 23. Threaded Email Reader Layout

Failure:

- Grouped/threaded email view shows redundant subtitle text such as `3 emails from ...`.
- It shows an extra `Thread` section header or plus button inside the reader.
- It shows a bottom compact row style unrelated to the email conversation.
- Every message looks like an inbox preview card.
- Older messages show long snippets/body previews.
- Latest message appears first or in the middle instead of last.
- Latest message is collapsed by default.

Test:

- Open a grouped/threaded email.
- Confirm there is only the reader title/header, then the message conversation.
- Confirm there is no redundant group subtitle, `Thread` header, plus button, or unrelated bottom row.
- Confirm messages are ordered oldest to newest.
- Confirm the latest message is last and expanded by default.
- Confirm older messages are collapsed compact headers.
- Confirm collapsed older messages show only sender, subject, and date/time.
- Click an older collapsed message.
- Confirm it expands without changing the latest-message default behavior unexpectedly.

Pass:

- The page feels like an email thread reader, not a list of inbox preview cards.
- The newest email is visible as the active expanded message at the bottom.

### 24. Thread Text Decoding

Failure:

- Thread cards show raw HTML entities such as `You&#39;ve`.
- Subject/body fallback text displays encoded entities.
- Decoding changes rich HTML rendering instead of only native text surfaces.

Test:

- Open a thread whose preview text contains an apostrophe or encoded entity.
- Confirm collapsed subject text displays `You've`, not `You&#39;ve`.
- Confirm expanded plain-text fallback displays decoded text.
- Confirm rich HTML body still renders through preserved HTML and is not converted into plain text.

Pass:

- Native SwiftUI text surfaces are decoded for display.
- Preserved HTML rendering remains unchanged.

### 25. macOS Typography Token Limits

Failure:

- macOS app UI introduces title/body sizes above the agreed maximum.
- A normal app page invents a font size outside `ElectronicMailType`.
- Checkbox size drifts from the agreed 18pt.
- App UI font-size changes are made while trying to fix sender HTML font rendering.

Test:

- Inspect macOS UI tokens and visible UI.
- Confirm normal app UI resolves through the shared `ElectronicMailType` roles.
- Confirm:
  - sectionTitleSize: 22
  - bodySize: 20
  - bodyLineHeight: 40
  - iconSize: 22
  - smallSize: 16
  - detailSize: 18
  - statusSize: 13
  - reader title: 22
  - checkbox: 18
- Confirm sender HTML font rendering is not controlled by these app UI tokens.

Pass:

- App shell typography follows the agreed macOS tokens.
- Sender HTML typography remains document-owned.

## Manual Visual QA Flow

Run this full flow after any To-do or shared typography change:

1. Build the app.
2. Relaunch the app.
3. Open To-do.
4. Check header, calendar card, Now, Later Today, Upcoming, and Worth Knowing.
5. Expand and collapse all four section disclosures and confirm they operate independently.
6. Check an empty section and confirm it has no `Nothing here` row.
7. Open To-do Search and confirm it filters To-do rows without navigating to Inbox.
8. Expand and collapse at least three rows:
   - first row
   - middle row
   - row near the bottom of a visible section
9. Verify one-click row expansion/collapse.
10. Verify only one row remains expanded.
11. Check checkbox lane alignment.
12. Check title, summary, action, and source alignment.
13. Open Inbox.
14. Check full-width layout, columns, section titles, row text, selected row, and the Settings/Search control gap.
15. Hard-scroll Inbox.
16. Open sidebar and navigate back to To-do.
17. If a refresh warning is visible, confirm Inbox and To-do use the same bottom capsule toast style.

Do not skip Inbox after a shared token change.

## Manual Mail Reader QA Flow

Run this flow after any Inbox, EmailReader, HTML rendering, navigation shell, or macOS typography change:

1. Build the app.
2. Relaunch the app.
3. Open Inbox.
4. Confirm hamburger visible frame is 25 x 25 and hit target is still easy to click.
5. Open and close the hamburger drawer.
6. Confirm drawer `Inbox` text aligns vertically with the normal Inbox page title and does not slide.
7. Click an unselected Inbox row once.
8. Confirm only the blue selection bar moves.
9. Click the selected row again.
10. Confirm the reader opens.
11. Go back and press Enter on a selected row.
12. Confirm the reader opens.
13. Open a single plain-text/basic email.
14. Confirm it uses the native text card.
15. Open a rich HTML email.
16. Confirm it renders as a light preserved document.
17. Scroll over the HTML body repeatedly.
18. Confirm no freeze, lag, or scroll lock.
19. Open HDFC rich email.
20. Compare document width, logo/header padding, title spacing, divider, and body text with the Apple Mail reference.
21. Confirm no extra grey/peach/white app border and no transparent-background hack.
22. Open Cal State or Navigraph rich email.
23. Confirm images render and the body does not fall back to snippet text.
24. Open a grouped/threaded email.
25. Confirm latest message is last and expanded.
26. Confirm older messages are compact rows with sender, subject, and date only.
27. Confirm no raw entities like `&#39;` appear in native text labels.
28. Confirm no redundant grouped subtitle, `Thread` header, plus button, or unrelated bottom row exists.

## Screenshot Review Checklist

When reviewing a screenshot, inspect these exact edges and lines:

- left edge of section title
- left edge of divider
- checkbox vertical lane
- title text vertical lane
- expanded card left edge
- summary left edge
- action row left edge
- source right inset
- section disclosure chevron alignment
- refresh warning toast position and style
- row baseline alignment
- hamburger icon visual bounds
- drawer first item baseline
- normal page title baseline
- email reader title left edge
- email reader body left edge
- rich HTML document outer edge
- rich HTML sender content edge
- thread collapsed row sender/subject/date columns
- latest expanded thread message position
- gap between title and summary
- gap between summary and action row
- gap between section title/divider and first row
- gap above and below expanded card

If any of these are visibly inconsistent, do not claim the UI is fixed.

## Agent Reporting Rules

When reporting completion, include:

- files changed
- build/test commands run
- whether the app was relaunched
- which screens were visually checked
- any excluded areas not changed

Do not say "fixed" if:

- only the build passed
- only one screenshot was checked
- only one row state was checked
- Inbox was not checked after shared typography/color changes
- To-do expansion was not clicked manually

## Minimal Pass Criteria

A To-do UI change passes only when:

- To-do remains centered/narrow.
- Inbox remains full-width.
- To-do row text is 15pt regular.
- Inbox row text is 15pt regular.
- Section titles are 17pt regular at 50% opacity.
- Row/body rhythm is 35pt.
- Circles align in one lane and do not dominate.
- Expanded row opens in place.
- Expanded card content aligns internally.
- Source has proper trailing inset.
- The SF Symbol play control opens the source workflow and never marks the task complete.
- One click expands/collapses.
- One expanded row at a time.
- Every section expands and collapses independently.
- Empty sections show no placeholder text or phantom row height.
- To-do's has only Settings and Search in its trailing action rail, separated by the shared 16pt mailbox gap.
- Calendar card is unchanged unless explicitly requested.
- Inbox does not freeze during fast scrolling.
- Refresh warnings use the same bottom capsule toast treatment on Inbox and To-do.
- Every email-backed To-do is a concrete user-owned action with cited source evidence.
- Optional or informational mail appears only in Worth Knowing and has no completion checkbox.
- Completing an AI To-do does not archive or mutate its Gmail source.
- Now, Later Today, and Upcoming are semantic urgency buckets, never arbitrary rank slices.
