# Decision Pipeline Apple-Native UI/UX Guide

Decision Pipeline should feel strongly Apple-native, but it should not become a copy of Apple's default app layouts.

The rule is: **75% Apple system language, 25% Decision Pipeline's own product design.**

Apple provides the ingredients: SF fonts, SF Symbols, dynamic system colors, accessibility behavior, control quality, and platform polish. Decision Pipeline owns the composition: where things go, what order they appear in, what shapes hold them, how the dashboard is structured, and how the product expresses "work first, emails underneath."

## Design North Star

The app should feel like a focused Apple-quality productivity utility with its own layout and product point of view.

The user opens it and sees:

- What needs attention now.
- What can wait until later today.
- What is only useful context.
- The original email source when they need trust.

The app should not feel like:

- A web dashboard.
- A SaaS admin panel.
- A redesigned Gmail clone.
- A decorative AI app.
- A rigid Apple template with the product personality removed.

## 75 / 25 Design Rule

### The 75% Apple Part

These should come from Apple platform language:

- **Fonts**: SF Pro, SF Pro Rounded where intentionally softer, and SF Mono only for technical/time-like values.
- **Icons**: SF Symbols.
- **Colors**: Apple semantic and system colors.
- **Interaction quality**: native-feeling focus, hover, pressed, selected, disabled, loading, and error states.
- **Accessibility**: Dynamic Type thinking, VoiceOver labels, contrast, Reduce Motion, keyboard access.
- **Motion restraint**: small, purposeful transitions.
- **Control discipline**: buttons, toggles, rows, search, settings, and navigation should feel familiar to Apple users.

### The 25% Decision Pipeline Part

These belong to this product, not Apple defaults:

- Screen layout.
- Section order.
- Dashboard structure.
- Where controls live.
- Which shapes hold each piece of content.
- How task rows are grouped.
- How source email is revealed.
- How setup feels.
- How the work-first mental model is expressed.

Do not blindly copy Apple Mail, Reminders, Calendar, Notes, or Settings. Use their materials, typography, color logic, icon quality, and interaction polish, then compose the screen around this app's own workflow.

## Apple Reference Baseline

Designers should use Apple's own design references as the source of truth:

- Apple Human Interface Guidelines: Color  
  https://developer.apple.com/design/human-interface-guidelines/color

- Apple Human Interface Guidelines: SF Symbols  
  https://developer.apple.com/design/human-interface-guidelines/sf-symbols

- Apple Design Resources  
  https://developer.apple.com/design/resources

When there is a question about visual ingredients, use Apple. When there is a question about product layout, use Decision Pipeline's workflow.

## Platform Feel

Platform feel matters, but these are references, not layout requirements. Use Apple platform behavior to make the product feel natural, while keeping the actual information architecture owned by Decision Pipeline.

### iPhone

The iPhone version should feel like a fast daily work check-in.

Use:

- Single-column reading when it helps focus.
- Native-feeling list rhythm.
- Large titles only where they improve orientation.
- Compact navigation designed around the app's two main surfaces: Work and Inbox.
- Swipe-friendly row actions only where they fit the user's workflow.
- Sheets for focused secondary tasks when they feel lighter than a new screen.
- Pull-to-refresh if the user expects fresh mail/work state.

Avoid:

- Dense desktop dashboards.
- Tiny table-style layouts.
- Floating web-style cards everywhere.
- Custom controls that behave unlike iOS.

### iPad

The iPad version should feel calm and spacious, not like a stretched iPhone.

Use:

- More space for the app's own dashboard composition.
- Split views only when they improve source checking or inbox recovery.
- Larger reading space for email body content.
- Native-feeling toolbars for commands and filtering when needed.

Avoid:

- Empty oversized center columns.
- Overly wide task text lines.
- Marketing-page hero layouts.

### Mac

The Mac version should feel like a lightweight productivity app.

Use:

- Sidebar navigation only if it improves the product's layout.
- Toolbar actions where they feel natural.
- Keyboard-first navigation.
- Search/command entry that feels like a native command surface.
- Larger detail panes for source email.

Avoid:

- Browser-style navigation chrome.
- Web app settings panels that ignore Mac conventions.
- Overanimated transitions.

## Typography

Typography should use San Francisco as the foundation.

Use:

- **SF Pro** for interface text.
- **SF Pro Rounded** only if the final brand direction intentionally wants a softer, more personal Apple feel.
- **SF Mono** for debug, timestamps, identifiers, or technical values only when needed.
- Dynamic Type-style sizing logic for accessibility.
- Tabular numbers for clocks, counts, and time-sensitive labels.

The typography should feel like Apple system UI:

- Clear hierarchy.
- Strong readability.
- No decorative type.
- No marketing-style display typography inside the signed-in app.
- No negative letter spacing in normal interface text.

### Suggested Text Hierarchy

Use platform text styles conceptually:

- Large Title: main dashboard or high-level screen title.
- Title 2 / Title 3: section headers such as Now, Today, Worth Knowing.
- Headline: task titles and important row text.
- Body: summaries, email snippets, explanatory copy.
- Callout: small row metadata.
- Footnote / Caption: sync state, timestamps, quiet helper text.

Do not create a custom type scale that fights native platform expectations.

## SF Symbols

All iconography should come from SF Symbols unless there is a specific product mark or source logo requirement.

Symbols should behave like text:

- Match symbol weight to nearby text weight.
- Match symbol size to the control or label.
- Use filled variants only when selection or emphasis requires it.
- Use hierarchical or palette rendering only when it clarifies state.
- Prefer system tint and semantic colors.

### Suggested Symbols

Use these as starting points:

| Meaning | Suggested SF Symbol |
| --- | --- |
| Dashboard / work | `checklist`, `checkmark.circle`, `square.and.pencil` |
| Inbox | `tray`, `tray.full`, `envelope` |
| Thread / conversation | `bubble.left.and.bubble.right` |
| Search / command | `magnifyingglass` |
| Settings | `gearshape` |
| Refresh / sync | `arrow.clockwise` |
| Back | `chevron.left` |
| More | `ellipsis.circle` |
| Calendar | `calendar` |
| Time | `clock` |
| Source / link | `link` |
| Complete | `checkmark` |
| Warning / failed refresh | `exclamationmark.triangle` |

Avoid:

- Custom SVG icon sets.
- Decorative icons that do not map to real actions.
- Apple product symbols used in ways Apple restricts.
- Mixing icon families.

## Apple Color System

Use Apple dynamic system colors as the design foundation. Do not build a custom SaaS palette first.

The UI should rely on semantic colors that adapt between light mode, dark mode, high contrast, and accessibility settings.

### Semantic Color Roles

Use these conceptual roles:

| Design Role | Apple-style Color Direction |
| --- | --- |
| App background | `systemBackground` |
| Grouped areas | `systemGroupedBackground` |
| Secondary grouped areas | `secondarySystemGroupedBackground` |
| Primary text | `label` |
| Secondary text | `secondaryLabel` |
| Tertiary text | `tertiaryLabel` |
| Separators | `separator` / `opaqueSeparator` |
| Main tint | `systemBlue` |
| Success / completion | `systemGreen` |
| Warning | `systemOrange` or `systemYellow` |
| Error | `systemRed` |
| Neutral fill | `systemFill`, `secondarySystemFill` |

The app should mostly be neutral. Color should communicate state, interactivity, selection, or priority.

### Product Tint

The primary tint should be Apple-like, not brand-heavy.

Default recommendation:

- Use `systemBlue` as the main tint.
- Use `systemGreen` for completion.
- Use `systemOrange` only for warning or needs-attention states.
- Use `systemRed` only for destructive or failed states.

Avoid:

- Purple AI gradients.
- Custom neon colors.
- Marketing palettes.
- Red urgency everywhere.
- Using color as the only state indicator.

## Materials And Surfaces

Apple materials are ingredients. Decision Pipeline decides the shapes and placement.

Use:

- Grouped backgrounds where they help the product's custom layout.
- Subtle separators between rows.
- Sheets and popovers for contained secondary interactions when they fit the flow.
- Native-feeling toolbar surfaces where needed.
- Materials only where platform-appropriate.

Avoid:

- Card grids.
- Nested cards.
- Heavy box shadows.
- Web-dashboard panels.
- Decorative blurred blobs or gradients.

The dashboard should feel like an Apple-quality productivity surface, but its layout can be unique to Decision Pipeline.

## Navigation Model

The navigation model should feel native and predictable, but the placement and shape of navigation are product decisions.

### Primary Destinations

There are two primary destinations:

- **Today / Work**: the dashboard.
- **Inbox**: source email threads.

On iPhone, this could be a compact tab model or another simple native-feeling control. On iPad and Mac, it could become a sidebar, split view, or another layout that better supports this app's workflow.

### Secondary Destinations

Secondary destinations should feel like native drill-ins, sheets, or detail panes:

- Thread reader.
- Task detail.
- Settings.
- Sync/import status.

Use navigation pushes, sheets, or split-view detail panes depending on platform and screen size.

Avoid:

- Web-style top nav as the primary mental model.
- Too many top-level destinations.
- Settings that feel like floating custom web panels.
- Copying Apple app navigation exactly when it weakens the work-first flow.

## Core Screens

### Login

The login screen should feel like an Apple setup screen:

- Minimal.
- Centered.
- Large clear promise.
- One primary action.
- No marketing sections.

Use native-feeling typography and spacing. The Google button can exist because Google is the source account, but the rest of the screen should feel Apple-native.

Good copy:

- "Work first."
- "Emails underneath."
- "Continue with Google"

### Post-Login Setup

This should feel like a native preparation state, not a technical process.

Use:

- Centered status.
- Smooth, restrained animation.
- Short plain language.
- No progress dashboard.

Status examples:

- "Importing emails..."
- "Understanding threads..."
- "Finding what needs action..."
- "Building your dashboard..."
- "Almost ready."

The screen should feel patient and trustworthy.

### Dashboard

The dashboard is the home screen.

It should feel like an Apple-quality daily work surface, but the layout is owned by this app:

- Date/time context.
- Short human summary.
- Calendar strip if needed.
- Sections for Now, Today, Worth Knowing.
- Rows that can complete, expand, or open source.

The layout should be clear and intentional. It can use custom placement, custom grouping, and custom shapes as long as the typography, iconography, color, accessibility, and interaction polish feel Apple-native.

### Dashboard Row Design

Rows should borrow the clarity of native list rows without being forced into default Apple row templates:

- Clear leading affordance for completion or status.
- Main title in strong readable text.
- Optional subtitle or source hint.
- Trailing action or chevron only when navigation exists.
- Native tap target size.
- Clear selected/pressed/highlight state.

Do not overload a row with badges. If a row needs more explanation, use a detail view, expanded disclosure, or a custom product-owned shape that still feels native.

### Expanded Task Detail

Expanded detail should feel native in behavior, but its shape can be designed specifically for Decision Pipeline.

It should show:

- Next action.
- Why it matters.
- Source email link.
- Completion action.

Use Apple colors, SF typography, native-feeling spacing, and clear labels. Keep the main row simple.

### Inbox

Inbox is source context.

It should feel familiar but quieter than Gmail. Use Apple materials and list discipline, but do not copy Apple Mail exactly:

- Group by date.
- Sender first.
- Subject/title second.
- Time trailing.
- Unread or action-needed state visible but restrained.

Do not make inbox the most visually interesting screen. The dashboard is the product.

### Thread Reader

The thread reader should feel like trustworthy source reading. It can borrow from native mail detail behavior without becoming a clone of Apple Mail:

- Back navigation.
- Subject/title.
- Summary if available.
- Message cards or grouped message sections.
- Sender, recipients, timestamp, body.

The email body should be readable and trustworthy. Avoid compressing source content too aggressively.

### Command / Search

The command surface should feel like Spotlight or native search.

Use:

- `magnifyingglass`.
- Immediate input focus.
- Results grouped by relevance.
- Short titles.
- Quiet subtitles.
- Keyboard navigation.

This should be fast and lightweight, not a modal dashboard.

## Interaction Design

### Completion

Completion should be instant.

Use Apple-like feedback:

- Checkmark state.
- Light haptic on iPhone if implemented.
- Row fades or moves out calmly.
- Summary updates without drama.

Avoid making the user wait for a network round trip before the interface responds.

### Source Trust

Every AI-derived or interpreted work item needs a path back to the source.

This can be:

- A source row.
- A "Read Email" action.
- A link icon.
- A thread detail push.

The user should never feel that the app invented work without proof.

### Sync And Refresh

Sync should feel like a native background behavior.

Use:

- Pull-to-refresh where appropriate.
- Small sync status in toolbar or footer.
- Quiet stale-state copy.
- Native progress indicators only when the user is blocked.

Avoid blocking the whole screen when saved content is still useful.

### Gestures

Use native gestures where they make sense:

- Swipe row actions for archive, complete, or mark done.
- Pull to refresh.
- Tap row to open detail.
- Long press/context menu for secondary actions.
- Keyboard shortcuts on iPad and Mac.

Do not invent gestures that are not standard for Apple platforms.

## UX Writing

Copy should be plain and Apple-like: short, clear, useful.

Good:

- "Nothing here yet."
- "No calendar items right now."
- "Processing older mail in background."
- "Showing last saved state."
- "Read Email"
- "Done"
- "Try Again"

Avoid:

- "AI-powered productivity platform"
- "Actionable intelligence"
- "Unlock insights"
- "Optimize your workflow"
- "Seamlessly manage your inbox"

The app should sound like a native utility, not a sales page.

## State Design

### Empty

Empty states should be quiet and literal.

Examples:

- "Nothing here yet."
- "No imported emails yet."
- "No calendar items right now."

Do not add illustrations unless they are very subtle and platform-native.

### Loading

Use native loading behavior:

- ProgressView-style indicators.
- Skeleton rows only if they match native list structure.
- Saved content first when available.

Avoid full-screen spinners after first setup.

### Error

Errors should be calm and recoverable.

Good:

- "Could not refresh. Showing last saved state."
- "Thread could not load. Try again."

Avoid:

- Raw system errors.
- Technical stack language.
- Scary permanent language.

### Stale Data

Stale data is acceptable if labeled clearly.

The design should communicate:

- You can still use this.
- Fresh data is being fetched.
- The app will update when ready.

## Accessibility

Native Apple feel includes accessibility.

Design for:

- Dynamic Type.
- VoiceOver labels.
- Sufficient contrast.
- Reduce Motion.
- Increase Contrast.
- Button Shapes where possible.
- Large hit targets.
- Keyboard navigation on iPad and Mac.

SF Symbols should have accessible labels when they communicate meaning. Decorative symbols should be hidden from assistive technology.

## Visual QA Checklist

Before accepting a design direction, check:

- Does it use Apple ingredients without losing Decision Pipeline's own layout?
- Are SF fonts used consistently?
- Are icons SF Symbols, not a custom icon pack?
- Are colors based on Apple dynamic system colors?
- Does light mode look native?
- Does dark mode look native?
- Does it still feel good with large text sizes?
- Does the dashboard remain the home?
- Does inbox remain secondary source context?
- Are task rows list-like rather than web-card-like?
- Are controls native in shape, spacing, and behavior?
- Are loading and refresh states non-blocking when saved data exists?
- Are the screen order, grouping, and content shapes serving the product instead of copying another Apple app?

## What To Remove From The Current Direction

Remove or avoid:

- Web-dashboard visual language.
- Generic SaaS cards.
- Custom icon drawing.
- Arbitrary custom color tokens not mapped to Apple semantics.
- Marketing-style typography.
- Settings popovers that feel webby rather than Apple-quality.
- Custom navigation chrome that feels unlike Apple platforms.
- Decorative animation.
- Exact copies of Apple app layouts that ignore this product's workflow.

Keep:

- The work-first product model.
- The dashboard as home.
- Gmail as source context.
- Fast warm state.
- Short human copy.
- Trust through source visibility.
- Product-owned layout, placement, shape, and ordering.

The final design should feel like a Decision Pipeline app built with Apple-native ingredients.
