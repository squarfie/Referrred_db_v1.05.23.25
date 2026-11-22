# Inventory workflow tabs

Goal: distinguish temporary Site ID storage, ongoing Main Lab confirmation, and verified final banking while retaining site/batch accordions.

1. Add regression coverage in `apps/laboratory/test_inventory_workflow_tabs.py` for tab membership, final-completion evidence, counts independent of search, and preserved storage/revival history after queueing.
2. Update `_biobank_inventory_context` in `apps/laboratory/views.py` with accession counts and filters before batch pagination. Include temporarily banked isolates throughout their subsequent ID workflow.
3. Update `apps/templates/laboratory/lab_biobank_list.html` with tabs, preserved search/page parameters, accurate status labels, and the existing Bench A queue POST action. No additional revival step; physical endorsement remains whole-batch.
4. Run inventory, Receiving, Bench A, Main Lab, and navigation regressions, Django checks, and migration drift checks. Inspect the rendered page. Commit only relevant files, excluding existing workbook changes.
