# UI Rules

## Visual Direction

The app should feel like a quiet personal dashboard: white page, black text, generous whitespace, and no decorative product imagery. The reference is the daily brief screenshot: date and time at the top, a large natural-language summary, a soft agenda block, then checklist sections.

The product mark is emoji-only:

```text
📨 → ✅
```

Use that mark on login and transition screens. Do not create app-owned logos, SVG mascots, illustrations, photos, gradients, or decorative icon art. Small third-party brand icons are allowed only when they identify an external provider, such as the Google icon on a Google sign-in button. Functional control icons, such as the theme switch, are allowed when they replace explanatory text.

## Typography

Use SF Pro Rounded as the primary typeface for web and iOS. On web, load the bundled `web/font/SF-Pro-Rounded-*.otf` files and fall back to Apple system fonts. On iOS, use `.system(..., design: .rounded)`.

Use a small, shared type scale:

- Auth mark: 56px desktop, 58px compact.
- Auth title: 32px desktop, 38px compact, bold, rounded, one line when the copy fits.
- Auth button text: 21px desktop, 22px compact.
- Page meta: 24px regular, tabular digits for time.
- Main summary: 25px desktop, 19px compact, light body with bold emphasis.
- Agenda rows: 21px desktop, 15px compact.
- Section titles: 23px desktop, 16px compact, bold.
- Checklist copy: 21px desktop, 16px compact.
- Expanded task detail: 16px desktop, 15px compact.
- Expanded task source text: 14px desktop, 14px compact.
- Debug title: 18px desktop, 20px compact.
- Debug section title: 14px desktop, 16px compact.
- Debug copy: 14px desktop, 15px compact.
- Raw payload/code blocks only: 12px SF Mono or platform monospace. All headings, labels, timestamps, and body text still use SF Pro Rounded.

Use `letter-spacing: 0`. Keep desktop and compact sizes in named tokens so every page can be audited against this scale instead of hiding one-off pixel values in individual screens.

## Layout

Use one centered readable column. The dashboard shell should stay compact, roughly `47.25rem` / `756px` wide on desktop and full width with 18px side padding on mobile. This desktop scale intentionally matches the visual density of a 90% browser zoom view at normal 100% browser zoom.

Vertical rhythm:

- Auth matches the Figma composition: centered product mark, a large single-line title, then the Google button.
- Dashboard top page padding: about 53px desktop, 28px mobile.
- Summary sits below the date/time and before the agenda.
- Agenda sits in one light-gray block.
- Sections have a thin rule under the title.
- Checklist rows use large readable text with roughly 13px row gaps on desktop.

Avoid nested cards. Use cards only for actual repeated records or debug blocks; the main digest is an open page, not a dashboard of boxes.

## Colors

Use a mostly neutral palette with matching light and dark tokens. On first visit, follow the OS/browser `prefers-color-scheme` setting. After the user clicks the bottom-left theme icon, persist that explicit light or dark choice.

- Light page: `#ffffff`
- Dark page: flat warm charcoal, not pure black and not blue-tinted.
- Primary text: near black in light mode, warm near-white in dark mode.
- Muted text: Apple-style gray in light mode, warm gray in dark mode.
- Rule: light gray in light mode, low-contrast warm gray in dark mode.
- Agenda/detail surfaces: very light gray in light mode, lifted charcoal in dark mode.
- Time/action blue: bright system-like blue
- Done/archive green: bright readable green

Do not introduce gradients, vignettes, page-specific palettes, or decorative backgrounds. Theme all surfaces through shared CSS tokens so login, post-login, dashboard, dropdown details, raw feed, and trace stay consistent.

## Components

Login, post-login, dashboard, raw feed, trace, and iOS screens should share the same shell, font family, color tokens, and section rhythm.

The bottom-left theme icon is global. It should be icon-only, compact, flat, and switch between explicit light and dark modes while falling back to the OS/browser theme on first visit.

The dashboard has a small top-right settings icon. It opens a compact flat panel for demo-safe view controls: Brief, Calendar, Now, Today, and Worth Knowing. These switches only change what is visible in the UI; they should not mutate backend data or task state.

Buttons should use plain, readable shapes. Auth buttons may include the provider brand icon. Checklist CTAs should look like text links: bold, colored, and underlined.

Checkboxes are small square controls, not large toggles. They can be local-only UI state unless persistence is explicitly added.

Tasks that need clarification can expand in place when the user clicks the task text. The expanded panel should be a soft gray block under the task row, with short explanatory lines, plain text actions, a muted source line, and a smooth height/opacity animation. Do not navigate away for this interaction.

Debug surfaces such as raw feed and trace replay use the debug type scale above. Monospace is only for raw JSON/code blocks; headings and body text still use SF Pro Rounded.

## Don'ts

- Do not use app-owned SVG logos or decorative illustrations.
- Do not use image assets for the app identity.
- Do not add hero sections, marketing copy, pills, badges, or card grids.
- Do not mix unrelated font families.
- Do not invent random shadows, radii, or colors per page.
- Do not make the UI feel more designed by adding decoration; make it feel better by improving hierarchy, spacing, and consistency.
