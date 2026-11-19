# Collapsible Sidebar Icon Rail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a persistent, accessible desktop control that collapses the 250px sidebar into a 72px icon-only rail and expands the main content into the released space.

**Architecture:** Keep the current Django menu and Argon mobile behavior intact. Add a sidebar toggle and semantic hooks in the existing include, project-specific icon-rail CSS, and a small dependency-free controller; apply the stored state immediately after `<body>` opens to prevent a layout flash.

**Tech Stack:** Django 5 templates/tests, Bootstrap/Argon CSS, vanilla JavaScript, browser `localStorage` and `matchMedia`.

**Spec:** `docs/superpowers/specs/2026-11-20-collapsible-icon-rail-design.md`

## Global Constraints

- The rail is 72px wide and is active only at viewport widths of 1200px or greater.
- Mobile/off-canvas Argon navigation behavior remains unchanged.
- Expanded is the default when no valid stored preference exists.
- No new dependency is allowed.
- Transitions must respect `prefers-reduced-motion`.
- Existing menu permissions, links, active-state logic, and receiving behavior must not change.

## Review Focus

- Missing or blocked `localStorage`: the sidebar still loads expanded and the toggle still works for the current page.
- Corrupt stored values: only the exact collapsed value activates the rail.
- Crossing the 1200px breakpoint: narrow screens ignore the rail visually and desktop restores the saved preference.
- Keyboard and assistive-technology use: the control is a real button with accurate label, title, and expanded state.
- Active and modal-trigger links: icons remain visible/clickable and active styling remains recognizable in rail mode.

---

### Task 1: Persistent desktop icon rail

**Files:**
- Modify: `apps/home/tests.py:21-24`
- Modify: `apps/templates/layouts/base.html:21-68`
- Modify: `apps/templates/includes/sidenav.html:19-76`
- Create: `apps/static/assets/css/sidebar-icon-rail.css`
- Create: `apps/static/assets/js/sidebar-icon-rail.js`

**Interfaces:**
- Consumes: existing `#sidenav-main`, adjacent `#panel.main-content`, `.nav-link-text`, `.navbar-heading`, and Argon desktop breakpoint.
- Produces: `#sidenavIconRailToggle`, `body.sidenav-icon-rail-collapsed`, storage key `referred-sidenav-icon-rail`, and a controller that keeps the button's `aria-expanded`, `aria-label`, and `title` synchronized.

- [ ] **Step 1: Write the failing rendered-template test**

Extend `SharedTemplateSyntaxTests` with `test_base_layout_exposes_accessible_sidebar_icon_rail_controls`. Render the real base/sidebar templates with a request and permissive `user_access`, then assert the output contains a button named `sidenavIconRailToggle`, `type="button"`, `aria-controls="sidenav-main"`, and initial `aria-expanded="true"`. Assert the layout includes the project CSS and controller script URLs.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `..\referred\Scripts\python.exe manage.py test apps.home.tests.SharedTemplateSyntaxTests --verbosity 2`

Expected: FAIL because the toggle and icon-rail assets do not exist yet; the existing compile test remains green.

- [ ] **Step 3: Add the early stored-state bootstrap and asset links**

In `base.html`, load `/static/assets/css/sidebar-icon-rail.css`, add a guarded inline bootstrap immediately after `<body>` that applies `sidenav-icon-rail-collapsed` only for the exact stored collapsed value on desktop, and load `/static/assets/js/sidebar-icon-rail.js` with the other project scripts.

- [ ] **Step 4: Add the accessible toggle markup**

In `sidenav.html`, place `button#sidenavIconRailToggle` beside the logo with `type="button"`, `aria-controls="sidenav-main"`, initial `aria-expanded="true"`, an expand/collapse icon pair, and visible-focus support. Add `title` attributes to navigation links from their existing labels so rail-mode icons remain discoverable.

- [ ] **Step 5: Implement desktop rail styling**

In `sidebar-icon-rail.css`, scope all behavior to `@media (min-width: 1200px)` and `body.sidenav-icon-rail-collapsed`. Set the sidebar and adjacent main-content offset to 72px; compact the header/logo; hide link text, headings, and dividers; center icons; preserve active indication; animate width/margin; and disable transitions under `prefers-reduced-motion`.

- [ ] **Step 6: Implement the dependency-free controller**

In `sidebar-icon-rail.js`, initialize on `DOMContentLoaded`, toggle the body class on button activation, persist `collapsed`/`expanded` under `referred-sidenav-icon-rail` inside `try/catch`, synchronize icon visibility and accessible attributes, and listen to desktop breakpoint changes without overwriting the saved preference.

- [ ] **Step 7: Run the focused test and verify GREEN**

Run: `..\referred\Scripts\python.exe manage.py test apps.home.tests.SharedTemplateSyntaxTests --verbosity 2`

Expected: both template tests PASS.

- [ ] **Step 8: Verify Django and regression coverage**

Run: `..\referred\Scripts\python.exe manage.py check`

Expected: `System check identified no issues`.

Run: `..\referred\Scripts\python.exe manage.py test --verbosity 1`

Expected: no new failures; report any pre-existing failures by name.

- [ ] **Step 9: Browser-smoke-test the interaction**

At `http://127.0.0.1:8000/receiving/`, verify collapse to a 72px icon rail, expanded content, active icon, keyboard toggle, reload persistence, expansion, and unchanged narrow-screen behavior. Confirm no console errors.

- [ ] **Step 10: Review the final diff**

Run `git diff --check` and `git status --short`; preserve and clearly separate all pre-existing user changes. Do not commit unless the user requests it.
