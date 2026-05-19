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
open /tmp/ElectronicMailDerivedData/Build/Products/Debug/ElectronicMail.app
```

If the app is already running and the change is visual, relaunch it:

```sh
pkill -x ElectronicMail || true
open /tmp/ElectronicMailDerivedData/Build/Products/Debug/ElectronicMail.app
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
- Confirm the section divider, plus button, row text, and expanded card feel like one column system.
- Confirm expanded card does not start far left of the row content.

Pass:

- To-do uses the current agreed width.
- Expanded row containment feels attached to the row, not floating as a separate panel.

### 3. Typography Drift

Failure:

- To-do and Inbox used different font weights, sizes, or colors in a way that made them feel like separate apps.
- To-do section title was forgotten when listing or changing typography.
- Brief/greeting text became too bold, too large, or visually louder than the work list.
- Login/onboarding typography drifted into unrelated custom sizes.

Test:

- Inspect these elements on both Inbox and To-do:
  - header title/greeting
  - section titles
  - row/item text
  - summary/body text
  - action text
  - source/metadata text
- Confirm app-wide font family is SF Pro Rounded.
- Confirm icons are SF Symbols, not emoji.
- Confirm text hierarchy mostly uses opacity, not many unrelated weights/colors.

Expected tokens:

- Section title: 22pt, semibold, sectionText.
- Row/item text: 20pt, regular, primaryText.
- Expanded title: 20pt, regular, primaryText.
- Expanded summary: 18pt, regular, secondaryText.
- Expanded action: 18pt or 20pt depending on space, semibold, green.
- Expanded source: 16pt, mutedText.

Pass:

- To-do rows are not oversized.
- Inbox rows and To-do rows share the same 20pt item rhythm.
- Section titles are visually quiet but readable.
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
  - sectionText: 25%
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
- Worth Knowing looked disconnected from Now and Later Today.

Test:

- Compare To-do section rhythm with Inbox section rhythm.
- Check the gap from section title/divider to first row.
- Check row-to-row spacing within a section.
- Check spacing before and after an expanded row.
- Check the gap between sections when rows are collapsed and when one row is expanded.

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
7. Repeat across Now, Later Today, and Worth Knowing.
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
- Now, Later Today, and Worth Knowing rows share the same motion behavior.

### 9. Add Button Placement

Failure:

- Plus button appeared only once or felt unrelated to sections.
- Plus button alignment drifted from section edge.
- Plus button was too visually loud or too far from the section it controls.

Test:

- Confirm every To-do section has its own plus affordance.
- Confirm plus aligns consistently with the section width.
- Confirm plus does not overlap section title/divider/rows.

Pass:

- Plus appears as a section action, not a floating unrelated button.

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

## Manual Visual QA Flow

Run this full flow after any To-do or shared typography change:

1. Build the app.
2. Relaunch the app.
3. Open To-do.
4. Check header, calendar card, Now, Later Today, and Worth Knowing.
5. Expand and collapse at least three rows:
   - first row
   - middle row
   - row near the bottom of a visible section
6. Verify one-click expansion/collapse.
7. Verify only one row remains expanded.
8. Check checkbox lane alignment.
9. Check title, summary, action, and source alignment.
10. Open Inbox.
11. Check full-width layout, columns, section titles, row text, and selected row.
12. Hard-scroll Inbox.
13. Open sidebar and navigate back to To-do.
14. If a refresh warning is visible, confirm Inbox and To-do use the same bottom capsule toast style.

Do not skip Inbox after a shared token change.

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
- plus button right alignment
- refresh warning toast position and style
- row baseline alignment
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
- To-do row text is 20pt regular.
- Inbox row text is 20pt regular.
- Section titles are 22pt semibold at 25% opacity.
- Row/body rhythm is 40pt.
- Circles align in one lane and do not dominate.
- Expanded row opens in place.
- Expanded card content aligns internally.
- Source has proper trailing inset.
- One click expands/collapses.
- One expanded row at a time.
- Calendar card is unchanged unless explicitly requested.
- Inbox does not freeze during fast scrolling.
- Refresh warnings use the same bottom capsule toast treatment on Inbox and To-do.
