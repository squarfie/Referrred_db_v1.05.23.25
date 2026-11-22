# Receiving and laboratory template fields

Forms use explicit named fields in their templates, rather than iterating over a form or using `as_p`. Keep each field's label, widget, help text and errors together when changing its layout. Repeated database records and organism inventory columns still use loops.

## Styling hooks

The intake, custody, accessioning, register and isolate pages have a `data-page` attribute on their main element. Model-backed fields have `data-model="ModelName"` and `data-field="field_name"`. Django form widgets use those attributes on the surrounding `.receiving-field` wrapper; hand-written process and storage inputs use them directly on the control. Explicit form wrappers also have `.receiving-field--field_name` classes.

For example:

```css
/* Change the whole intake field's layout. */
[data-page="secretariat-intake"] .receiving-field--tracking_number {
  max-width: 36rem;
}

/* Style the revival timestamp control. */
[data-page="laboratory-isolate"]
input[data-model="LaboratoryIsolate"][data-field="revival_started_at"] {
  border-color: #246477;
}
```

These attributes are developer hooks, not additional visible labels for staff.

## Template and model mapping

| Template under `apps/templates/` | Model and explicitly rendered fields |
| --- | --- |
| `receiving/receipt_form.html` | `ReferralReceipt`: tracking_number, site, date_sent, date_received, time_received, remarks |
| `laboratory/lab_receipt_form.html` | `ReferralReceipt`: lab_date_received, lab_time_received |
| `laboratory/laboratory_workspace.html` | `LaboratoryReceiving`: specimen_condition, discrepancies, remarks, total_isolates_received, isolates_rejected, isolates_accepted, date_accepted, total_isolates_processed |
| Same workspace, accession form and inline editing | `LaboratoryAccession`: date_referred, start_number, end_number, batch_number, total_batches |
| Same workspace, organism matrix | `LaboratoryOrganismCount.count`; `data-organism-code` identifies the column's organism code |
| `laboratory/lab_isolate_detail.html`, instrument report | `LaboratoryIdentification`: whonet_organism_code, organism_reported, instrument, cassette_reference |
| Same isolate page, revival | `LaboratoryIsolate`: revival_started_at, revival_completed_at, status, revival_failed_reason |
| Same isolate page, approved identification | `LaboratoryIsolate`: approved_organism_code, final_organism |
| Same isolate page, harvest and lyophilization | `LaboratoryBiobank`: date_harvested, date_lyophilized, whonet_code |
| `laboratory/_lab_isolate_storage_form.html` | `LaboratoryBiobank`: each named storage input is marked with its exact model field, including dates, container, preservation_method, lyophilization storage, supplementary storage, additional storage and remarks |
| `laboratory/laboratory_register.html` | month and year are filter inputs, not model fields; each has a `data-field` wrapper without `data-model` |

Some request parameters differ from their model field names. Keep the existing `name` attributes because the views consume them:

| Submitted name | Stored field |
| --- | --- |
| organism_reported_code | LaboratoryIdentification.whonet_organism_code |
| started_at | LaboratoryIsolate.revival_started_at |
| completed_at | LaboratoryIsolate.revival_completed_at |
| reason | LaboratoryIsolate.revival_failed_reason (workflow appends attempt observations) |
| final_organism_code | LaboratoryIsolate.approved_organism_code |

`whonet_organism_name` is a lookup helper for resolving `LaboratoryBiobank.whonet_code`, not a separate biobank model field. Its control uses `data-input-role="organism-name-lookup"`. Organism search boxes and action buttons are also workflow helpers rather than persisted model fields.

The laboratory model imports alias the models in `apps.receiving.models`; these template hooks use class names, without changing Django app labels or schema ownership.
