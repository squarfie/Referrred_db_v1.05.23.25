# Laboratory app

Laboratory receipt, accessioning, identification, revival, approval, lyophilization, biobanking and sticker printing live here.

- `apps.py`: Django app registration.
- `models.py`: explicit aliases of the existing persisted laboratory models.
- `receiving_views.py`: laboratory custody, accession ranges, batch creation and register endpoints.
- `views.py`: identification, isolate processing, inventory, uploads and stickers.
- `dashboard.py`: laboratory workflow tabs and summary counters.
- `urls.py`: laboratory route declarations; included under the existing `/receiving/` prefix to preserve bookmarks and URL names.
- `forms.py`, `permissions.py`: laboratory validation and server-side role enforcement.
- `workflow.py`, `guidance.py`: audited scientific transitions and next-action presentation.
- `organism_lookup.py`, `reports.py`, `sticker_pdf.py`: organism reference lookup, monthly reports and compact A4 labels.
- `admin.py`: existing read-only laboratory admin registrations.
- `tests.py`, `test_*.py`: app ownership, workflow, receipt queue and PDF regression tests.
- `migrations/__init__.py`: migration package; intentionally no schema migration.

Templates are in `apps/templates/laboratory/`. The receipt-detail page reuses the shared receipt history layout from Receiving. Shared CSS, QR encoding and atomic courier custody services remain shared.

The actual model classes, migration labels, content types and database tables retain their existing `receiving` ownership. Importing them through `apps.laboratory.models` returns the same classes, not duplicate or proxy models. Changing database ownership would be a separate schema migration project.

Legacy laboratory Python modules under `apps.receiving` are compatibility exports. New code should import the implementations from this app.

Verify with `python manage.py check`, `python manage.py makemigrations --check --dry-run`, and `python manage.py test apps.receiving apps.laboratory apps.home`.
