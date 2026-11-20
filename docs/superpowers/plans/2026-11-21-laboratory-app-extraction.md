# Laboratory app extraction plan

**Goal:** Place laboratory processes, views, URL configuration and templates in a dedicated Django app without changing workflow behavior.

**Architecture:** Register apps.laboratory. Move workflow views/services/forms/reports into it and laboratory templates to apps/templates/laboratory. Laboratory URLs remain at their current public paths and names, included from the new app. Existing model migration ownership and database tables stay in receiving; laboratory.models exposes the same model classes, avoiding schema changes and content-type/permission churn. Secretariat and courier custody services stay in receiving, with narrow compatibility exports for old Python imports.

**Scope:** User directly requested app extraction, not a workflow redesign or database migration. Preserve unrelated local settings, workbooks and uploads; stage only the app registration hunk in settings. Work on feature/laboratory-app-extraction.

## Tasks

- [x] Add ownership/routing/template regression tests and observe failures.
- [x] Register app and move laboratory views, forms, URL declarations; extract shared receipt dashboard rendering so Secretariat stays independent.
- [x] Move workflow services, guidance, reports, lookup and sticker rendering; preserve old import compatibility.
- [x] Move laboratory templates and update render/include references and template checks; shared receipt detail stays reusable.
- [x] Verify Django checks, migration dry run, receiving/laboratory/home regression suites, and review the complete diff.
- [x] Commit app extraction without unrelated local files. No push or merge requested.

## Verification focus

URL resolution must point to apps.laboratory while names and paths remain stable. No new migration or database table may appear. Tests must preserve direct POST authorization, receipt custody, batch conflicts, scientific workflow order, ID uploads, organism matching, revival retry history, QR/sticker printing and separate laboratory/Secretariat dashboards.

## Validation

278 receiving/laboratory/home tests passed. Django checks passed and migration dry run found no changes. Review verified equivalent route maps, permission guards, and moved view/service implementations. Model registry and table ownership remain unchanged.
