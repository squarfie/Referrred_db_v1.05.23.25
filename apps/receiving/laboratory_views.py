import csv
import base64
from datetime import datetime
from io import BytesIO, StringIO
from itertools import islice
from zipfile import BadZipFile
from hashlib import sha256

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .qr import render_qr_png
from .laboratory_workflow import (WorkflowError, queue_for_revival, transition_batch,
    record_revival_failure, retry_revival, complete_revival_isolate, complete_lyophilization, save_biobank)
from .models import (
    LaboratoryAccession, LaboratoryBiobank, LaboratoryIdentification, LaboratoryIsolate,
)
from .permissions import (
    can_access_laboratory_worklist, can_manage_id_testing, can_manage_biobank,
    can_review_id_results, lab_staff_required, lab_review_required, lab_worklist_required,
)

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_ROWS = 1000
UPLOAD_COLUMNS = ("accession_number", "organism_reported", "instrument", "cassette_reference")



@login_required
@lab_worklist_required
def laboratory_biobank_list(request):
    search = (request.GET.get("q") or "").strip()
    isolates = LaboratoryIsolate.objects.select_related(
        "accession_range__laboratory_receiving__receipt__site", "biobank"
    ).order_by("accession_number")
    if search:
        isolates = isolates.filter(accession_number__icontains=search)
    return render(request, "receiving/lab_biobank_list.html", {
        "isolates": isolates[:250], "query": search,
        "can_bank": can_manage_biobank(request.user),
    })


@login_required
@lab_worklist_required
def laboratory_worklist(request):
    isolates = LaboratoryIsolate.objects.select_related(
        "accession_range__laboratory_receiving__receipt__site"
    ).prefetch_related("identifications").order_by("accession_number")
    query = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip()
    if query:
        isolates = isolates.filter(accession_number__icontains=query)
    if status in LaboratoryIsolate.Status.values:
        isolates = isolates.filter(status=status)
    return render(request, "receiving/lab_worklist.html", {
        "isolates": isolates[:250], "query": query, "selected_status": status,
        "statuses": LaboratoryIsolate.Status.choices,
        "can_test": can_manage_id_testing(request.user),
        "can_review": can_review_id_results(request.user),
        "can_bank": can_manage_biobank(request.user),
    })


@login_required
@lab_review_required
@require_POST
def laboratory_endorse_id(request, accession_id):
    """Legacy POST route now queues the accession range for revival."""
    try:
        created = queue_for_revival(accession_id, request.user)
        messages.success(request, "Accession range queued for revival." if created else
                         "Isolates already exist for this range.")
    except WorkflowError as exc:
        messages.error(request, str(exc))
    accession = get_object_or_404(LaboratoryAccession, pk=accession_id)
    return redirect("laboratory_receiving_workspace",
                    receiving_id=accession.laboratory_receiving.receipt.receiving_id)


@login_required
@lab_review_required
@require_POST
def laboratory_batch_transition(request, accession_id, action):
    try:
        changed = transition_batch(accession_id, action, request.user)
        messages.success(request, f"Batch transition '{action}' completed." if changed else
                         "Batch already at this workflow stage.")
    except WorkflowError as exc:
        messages.error(request, str(exc))
    accession = get_object_or_404(LaboratoryAccession, pk=accession_id)
    return redirect("laboratory_receiving_workspace",
                    receiving_id=accession.laboratory_receiving.receipt.receiving_id)


@login_required
@lab_review_required
@require_POST
def laboratory_revival_failed(request, isolate_id):
    status = (request.POST.get("status") or "").strip()
    reason = (request.POST.get("reason") or "").strip()
    try:
        record_revival_failure(isolate_id, status, reason, request.user)
        messages.success(request, f"Revival outcome recorded: {status.replace('_', ' ')}.")
    except WorkflowError as exc:
        messages.error(request, str(exc))
    return redirect("laboratory_isolate_detail", isolate_id=isolate_id)


@login_required
@lab_review_required
@require_POST
def laboratory_revival_retry(request, isolate_id):
    try:
        retry_revival(isolate_id, request.user)
        messages.success(request, "Revival retry started; previous failure reason remains recorded.")
    except WorkflowError as exc:
        messages.error(request, str(exc))
    return redirect("laboratory_isolate_detail", isolate_id=isolate_id)


@login_required
@lab_review_required
@require_POST
def laboratory_revival_retry_complete(request, isolate_id):
    try:
        complete_revival_isolate(isolate_id, request.user)
        messages.success(request, "Retried isolate revived successfully.")
    except WorkflowError as exc:
        messages.error(request, str(exc))
    return redirect("laboratory_isolate_detail", isolate_id=isolate_id)


@login_required
@lab_review_required
@require_POST
def laboratory_lyophilization_complete(request, isolate_id):
    try:
        complete_lyophilization(
            isolate_id, request.POST.get("date_harvested"),
            request.POST.get("date_lyophilized"), request.user,
            whonet_code=request.POST.get("whonet_code") or "",
        )
        messages.success(request, "Harvest and lyophilization recorded. Isolate is ready for biobanking.")
    except WorkflowError as exc:
        messages.error(request, str(exc))
    return redirect("laboratory_isolate_detail", isolate_id=isolate_id)


@login_required
@lab_staff_required
@require_POST
@transaction.atomic
def laboratory_acknowledge_id(request, isolate_id):
    isolate = get_object_or_404(LaboratoryIsolate.objects.select_for_update(), pk=isolate_id)
    if isolate.status != LaboratoryIsolate.Status.ID_PENDING:
        messages.error(request, "Only pending isolates may be acknowledged.")
    else:
        isolate.status = LaboratoryIsolate.Status.ID_TESTING
        isolate.acknowledged_by = request.user
        isolate.acknowledged_at = timezone.now()
        isolate.save(update_fields=("status", "acknowledged_by", "acknowledged_at"))
        messages.success(request, f"Received {isolate.accession_number} for ID testing.")
    return redirect("laboratory_isolate_detail", isolate_id=isolate.pk)


@login_required
@lab_worklist_required
def laboratory_isolate_detail(request, isolate_id):
    isolate = get_object_or_404(
        LaboratoryIsolate.objects.select_related(
            "accession_range__laboratory_receiving__receipt__site", "reviewed_by"
        ).prefetch_related("identifications__recorded_by"), pk=isolate_id,
    )
    biobank = LaboratoryBiobank.objects.filter(isolate=isolate).first()
    return render(request, "receiving/lab_isolate_detail.html", {
        "isolate": isolate, "biobank": biobank,
        "can_test": can_manage_id_testing(request.user),
        "can_review": can_review_id_results(request.user),
        "can_bank": can_manage_biobank(request.user),
    })


@login_required
@lab_staff_required
@require_POST
def laboratory_record_id(request, isolate_id):
    isolate = get_object_or_404(LaboratoryIsolate, pk=isolate_id)
    if isolate.status != LaboratoryIsolate.Status.ID_TESTING:
        messages.error(request, "ID results may be added only while testing is in progress.")
        return redirect("laboratory_isolate_detail", isolate_id=isolate.pk)
    organism = (request.POST.get("organism_reported") or "").strip()
    if not organism or len(organism) > 255:
        messages.error(request, "Enter an identified organism (maximum 255 characters).")
        return redirect("laboratory_isolate_detail", isolate_id=isolate.pk)
    LaboratoryIdentification.objects.create(
        isolate=isolate, organism_reported=organism,
        instrument=(request.POST.get("instrument") or "").strip()[:100],
        cassette_reference=(request.POST.get("cassette_reference") or "").strip()[:100],
        recorded_by=request.user,
    )
    messages.success(request, "Instrument identification saved.")
    return redirect("laboratory_isolate_detail", isolate_id=isolate.pk)


@login_required
@lab_staff_required
@require_POST
@transaction.atomic
def laboratory_return_id(request, isolate_id):
    isolate = get_object_or_404(LaboratoryIsolate.objects.select_for_update(), pk=isolate_id)
    if isolate.status != LaboratoryIsolate.Status.ID_TESTING or not isolate.identifications.exists():
        messages.error(request, "At least one identification is required before return.")
    else:
        isolate.status = LaboratoryIsolate.Status.ID_RETURNED
        isolate.returned_at = timezone.now()
        isolate.returned_by = request.user
        isolate.save(update_fields=("status", "returned_at", "returned_by"))
        messages.success(request, "Identification returned to Laboratory Receiving.")
    return redirect("laboratory_isolate_detail", isolate_id=isolate.pk)


@login_required
@lab_review_required
@require_POST
@transaction.atomic
def laboratory_review_id(request, isolate_id):
    isolate = get_object_or_404(LaboratoryIsolate.objects.select_for_update(), pk=isolate_id)
    organism = (request.POST.get("final_organism") or "").strip()
    if isolate.status != LaboratoryIsolate.Status.ID_RETURNED:
        messages.error(request, "Only returned identifications may be reviewed.")
    elif not organism or len(organism) > 255:
        messages.error(request, "An approved organism is required (maximum 255 characters).")
    else:
        isolate.final_organism = organism
        isolate.status = LaboratoryIsolate.Status.ID_REVIEWED
        isolate.reviewed_by = request.user
        isolate.reviewed_at = timezone.now()
        isolate.save(update_fields=("final_organism", "status", "reviewed_by", "reviewed_at"))
        messages.success(request, "Identification reviewed and approved.")
    return redirect("laboratory_isolate_detail", isolate_id=isolate.pk)


@login_required
@lab_worklist_required
@require_POST
def laboratory_biobank_save(request, isolate_id):
    if not can_manage_biobank(request.user):
        messages.error(request, "You do not have permission to update biobank inventory.")
        return redirect("laboratory_isolate_detail", isolate_id=isolate_id)
    limits = {
        "whonet_code": 30, "lyo_box_name": 100, "lyo_box_number": 50,
        "lyo_position": 50, "refrigerator_number": 50, "lyo_location": 100,
        "supplementary_box_name": 100, "supplementary_box_number": 50,
        "supplementary_position": 50, "freezer_number": 50,
        "supplementary_location": 100, "preservation_method": 100, "container": 100,
        "storage_location": 150, "storage_box": 100, "box_position": 30,
    }
    details = {key: (request.POST.get(key) or "").strip() for key in limits}
    if any(len(details[key]) > maximum for key, maximum in limits.items()):
        messages.error(request, "One or more biobank fields exceed their maximum length.")
        return redirect("laboratory_isolate_detail", isolate_id=isolate_id)
    details["remarks"] = (request.POST.get("remarks") or "").strip()
    for field in ("lyo_date_encoded", "supplementary_date_encoded"):
        value = (request.POST.get(field) or "").strip()
        if value:
            try:
                date = datetime.strptime(value, "%Y-%m-%d").date()
            except ValueError:
                messages.error(request, f"Enter a valid {field.replace('_', ' ')}.")
                return redirect("laboratory_isolate_detail", isolate_id=isolate_id)
            if date > timezone.localdate():
                messages.error(request, "Date encoded cannot be in the future.")
                return redirect("laboratory_isolate_detail", isolate_id=isolate_id)
            details[field] = date
        else:
            details[field] = None
    details["skimmed_milk"] = request.POST.get("skimmed_milk") == "on"
    details["tsbg"] = request.POST.get("tsbg") == "on"
    try:
        save_biobank(isolate_id, details, request.user)
        messages.success(request, "Biobank storage saved.")
    except (WorkflowError, ValueError) as exc:
        messages.error(request, str(exc))
    return redirect("laboratory_isolate_detail", isolate_id=isolate_id)


def _label_item(isolate):
    """Render only fully identified/harvested isolates; QR is accession only."""
    bank = LaboratoryBiobank.objects.filter(isolate=isolate).first()
    if not bank or not bank.date_harvested or not bank.whonet_code.strip() or not isolate.final_organism:
        return None
    if isolate.status not in {
        LaboratoryIsolate.Status.READY_FOR_BIOBANK, LaboratoryIsolate.Status.BIOBANKED
    }:
        return None
    png = render_qr_png(isolate.accession_number)
    return {
        "isolate": isolate,
        "code": bank.whonet_code.strip().upper(),
        "harvest": bank.date_harvested.strftime("%m/%d/%Y"),
        "qr_data_uri": "data:image/png;base64," + base64.b64encode(png).decode("ascii"),
    }


@login_required
@lab_worklist_required
def laboratory_biobank_label(request, isolate_id):
    isolate = get_object_or_404(LaboratoryIsolate, pk=isolate_id)
    label = _label_item(isolate)
    if not label:
        messages.error(request, "A reviewed organism, WHONET code, and harvest date are required for sticker printing.")
        return redirect("laboratory_isolate_detail", isolate_id=isolate.pk)
    return render(request, "receiving/lab_biobank_label.html", {
        "labels": [label], "unready": [], "batch": None,
    })


@login_required
@lab_worklist_required
def laboratory_batch_labels(request, accession_id):
    accession = get_object_or_404(
        LaboratoryAccession.objects.select_related("laboratory_receiving__receipt"),
        pk=accession_id,
    )
    labels, unready = [], []
    for isolate in accession.isolate_records.order_by("accession_number"):
        label = _label_item(isolate)
        if label:
            labels.append(label)
        else:
            unready.append(isolate.accession_number)
    return render(request, "receiving/lab_biobank_label.html", {
        "labels": labels, "unready": unready, "batch": accession,
    })


def _upload_rows(upload):
    if upload.size > MAX_UPLOAD_BYTES:
        raise ValueError("Maximum upload size is 2 MB.")
    name = upload.name.lower()
    if name.endswith(".csv"):
        try:
            data = upload.read().decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("CSV files must be UTF-8 encoded.") from exc
        reader = csv.DictReader(StringIO(data))
        headings = reader.fieldnames or []
        rows = list(islice(reader, MAX_ROWS + 1))
    elif name.endswith(".xlsx"):
        from openpyxl import load_workbook
        from openpyxl.utils.exceptions import InvalidFileException
        try:
            workbook = load_workbook(BytesIO(upload.read()), read_only=True, data_only=True)
        except (BadZipFile, InvalidFileException, ValueError, OSError) as exc:
            raise ValueError("Invalid Excel workbook.") from exc
        try:
            values = workbook.active.iter_rows(values_only=True)
            headings = [str(v or "").strip() for v in next(values, [])]
            rows = [dict(zip(headings, row)) for row in islice(values, MAX_ROWS + 1)]
        finally:
            workbook.close()
    else:
        raise ValueError("Use a .csv or .xlsx identification file.")
    if not set(UPLOAD_COLUMNS[:2]).issubset(set(headings)):
        raise ValueError("Required headers: accession_number, organism_reported.")
    if len(rows) > MAX_ROWS:
        raise ValueError("Uploads are limited to 1,000 rows.")
    normalized = []
    for number, row in enumerate(rows, start=2):
        values = {key: str(row.get(key) or "").strip() for key in UPLOAD_COLUMNS}
        if not values["accession_number"] or not values["organism_reported"]:
            raise ValueError(f"Row {number}: accession and organism are required.")
        if len(values["accession_number"]) > 80 or len(values["organism_reported"]) > 255:
            raise ValueError(f"Row {number}: accession or organism exceeds its length limit.")
        if len(values["instrument"]) > 100 or len(values["cassette_reference"]) > 100:
            raise ValueError(f"Row {number}: instrument or cassette reference too long.")
        normalized.append(values)
    if not normalized:
        raise ValueError("The uploaded file contains no ID results.")
    return normalized


@login_required
@lab_staff_required
def laboratory_id_upload(request):
    if request.method != "POST":
        return render(request, "receiving/lab_id_upload.html", {})
    file = request.FILES.get("file")
    if not file:
        messages.error(request, "Choose a CSV or Excel file.")
        return redirect("laboratory_id_upload")
    if file.size > MAX_UPLOAD_BYTES:
        messages.error(request, "Maximum upload size is 2 MB.")
        return redirect("laboratory_id_upload")
    digest = sha256(file.read()).hexdigest()
    file.seek(0)
    if request.POST.get("action") == "commit" and request.session.get("lab_id_preview_digest") != digest:
        messages.error(request, "Preview this exact file before importing it.")
        return redirect("laboratory_id_upload")
    try:
        rows = _upload_rows(file)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("laboratory_id_upload")
    accession_numbers = {row["accession_number"] for row in rows}
    isolates = {
        isolate.accession_number: isolate
        for isolate in LaboratoryIsolate.objects.filter(accession_number__in=accession_numbers)
    }
    problems = [
        row["accession_number"] for row in rows
        if row["accession_number"] not in isolates
        or isolates[row["accession_number"]].status != LaboratoryIsolate.Status.ID_TESTING
    ]
    if problems:
        messages.error(request, "Unknown or not-in-testing accessions: " + ", ".join(problems[:8]))
        return redirect("laboratory_id_upload")

    if request.POST.get("action") != "commit":
        request.session["lab_id_preview_digest"] = digest
        return render(request, "receiving/lab_id_upload.html", {
            "rows": rows, "preview": True,
            "warning": "Review the records before saving. Upload the same file again to confirm."
        })

    # Recheck inside the transaction before writing; original instrument records
    # remain separate and are never overwritten.
    with transaction.atomic():
        locked = {
            isolate.accession_number: isolate
            for isolate in LaboratoryIsolate.objects.select_for_update().filter(
                accession_number__in=accession_numbers
            )
        }
        if any(number not in locked or locked[number].status != LaboratoryIsolate.Status.ID_TESTING
               for number in accession_numbers):
            messages.error(request, "One or more isolate statuses changed. No results were imported.")
            return redirect("laboratory_id_upload")
        LaboratoryIdentification.objects.bulk_create([
            LaboratoryIdentification(
                isolate=locked[row["accession_number"]],
                organism_reported=row["organism_reported"],
                instrument=row["instrument"],
                cassette_reference=row["cassette_reference"],
                source=(LaboratoryIdentification.Source.CSV
                        if file.name.lower().endswith(".csv")
                        else LaboratoryIdentification.Source.XLSX),
                recorded_by=request.user,
            ) for row in rows
        ])
    request.session.pop("lab_id_preview_digest", None)
    messages.success(request, f"Imported {len(rows)} identification results.")
    return redirect("laboratory_worklist")
