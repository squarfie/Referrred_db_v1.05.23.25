# Collapsible Sidebar Icon Rail Design

## Intent

Give desktop users more horizontal workspace by letting them reduce the existing 250px sidebar to a narrow icon-only rail and restore it with the same control.

## Goals

- Place an accessible collapse/expand button in the sidebar header beside the logo.
- On desktop, reduce the sidebar to approximately 72px, keep navigation icons visible, hide labels and section headings, and expand the main content into the released space.
- Keep the active navigation state visually clear in both modes.
- Remember the user's choice across navigation and reloads.
- Avoid a visible expanded-to-collapsed flash during page load.

## Non-goals and anti-goals

- Do not change permissions, menu contents, routes, or the current receiving workflow.
- Do not replace or alter Argon's mobile off-canvas navigation behavior.
- Do not require a new JavaScript or CSS dependency.
- Do not make the minimized rail expand merely on hover.

## Constraints

- Build on the existing Django sidebar include and Argon layout.
- Apply the icon rail only at desktop width (1200px and wider); smaller screens retain the existing responsive behavior.
- Respect `prefers-reduced-motion` for width and margin transitions.
- Use a stable local-storage key and tolerate unavailable/corrupt browser storage.

## Approaches considered

1. **Dedicated icon-rail state (chosen):** Add a project-specific body class, focused CSS, and a small controller that toggles the class and persists it in `localStorage`. This cleanly provides a persistent icon rail without interfering with Argon's existing pin/unpin behavior.
2. **Adapt Argon's built-in sidenav state:** Reuse `g-sidenav-pinned` and related scripts. Rejected because those states currently hide/reveal the whole sidebar and add hover behavior, which conflicts with the requested persistent rail.

## Interaction and layout

- Expanded is the default for users with no stored preference.
- The header control uses a left-pointing collapse icon while expanded and a right-pointing expand icon while minimized, with updated `aria-label`, `title`, and `aria-expanded` values.
- In rail mode, the logo becomes compact, navigation icons are centered, link text and utility/settings headings are hidden, and links expose their existing names as tooltips/titles.
- The main content's left margin changes with the sidebar width so no empty 250px gutter remains.
- A tiny early script applies the stored desktop state before the page is painted; the sidebar controller handles toggling, accessibility attributes, storage, and desktop breakpoint changes.

## Verification

- Add a Django template regression test for the control, state hooks, accessible attributes, and persistence controller.
- Run the focused test, Django system check, and the full test suite.
- Browser-smoke-test expanded, collapsed, reload persistence, active link styling, and the unchanged narrow-screen behavior when browser automation is available.
