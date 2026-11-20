import csv
from datetime import datetime
from io import BytesIO, StringIO
from itertools import islice
from hashlib import sha256

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

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
@transaction.atomic
def laboratory_endorse_id(request, accession_id):
    accession = get_object_or_404(
        LaboratoryAccession.objects.select_for_update().select_related(
            "laboratory_receiving__receipt__site"
        ), pk=accession_id,
    )
    if accession.isolate_records.exists():
        messages.info(request, "This accession range has already been endorsed.")
    else:
        # A whole range is endorsed together; duplicates must never silently overwrite.
        accession_numbers = accession.accession_numbers
        if LaboratoryIsolate.objects.filter(accession_number__in=accession_numbers).exists():
            messages.error(request, "One or more accession numbers already exist in ID testing.")
            return redirect("laboratory_receiving_workspace",
                            receiving_id=accession.laboratory_receiving.receipt.receiving_id)
        LaboratoryIsolate.objects.bulk_create([
            LaboratoryIsolate(
                accession_range=accession, accession_number=number,
                endorsed_by=request.user, endorsed_at=timezone.now(),
            ) for number in accession_numbers
        ])
        messages.success(request, f"Endorsed {len(accession_numbers)} isolates for ID testing.")
    return redirect("laboratory_receiving_workspace",
                    receiving_id=accession.laboratory_receiving.receipt.receiving_id)


@login_required
@lab_staff_required
@require_POST
@transaction.atomic
def laboratory_acknowledge_id(request, isolate_id):
    isolate = get_object_or_404(LaboratoryIsolate.objects.select_for_update(), pk=isolate_id)
    if isolate.status != LaboratoryIsolate.Status.PENDING:
        messages.error(request, "Only pending isolates may be acknowledged.")
    else:
        isolate.status = LaboratoryIsolate.Status.TESTING
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
    if isolate.status != LaboratoryIsolate.Status.TESTING:
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
    if isolate.status != LaboratoryIsolate.Status.TESTING or not isolate.identifications.exists():
        messages.error(request, "At least one identification is required before return.")
    else:
        isolate.status = LaboratoryIsolate.Status.RETURNED
        isolate.returned_at = timezone.now()
        isolate.save(update_fields=("status", "returned_at"))
        messages.success(request, "Identification returned to Laboratory Receiving.")
    return redirect("laboratory_isolate_detail", isolate_id=isolate.pk)


@login_required
@lab_review_required
@require_POST
@transaction.atomic
def laboratory_review_id(request, isolate_id):
    isolate = get_object_or_404(LaboratoryIsolate.objects.select_for_update(), pk=isolate_id)
    organism = (request.POST.get("final_organism") or "").strip()
    if isolate.status != LaboratoryIsolate.Status.RETURNED:
        messages.error(request, "Only returned identifications may be reviewed.")
    elif not organism or len(organism) > 255:
        messages.error(request, "An approved organism is required (maximum 255 characters).")
    else:
        isolate.final_organism = organism
        isolate.status = LaboratoryIsolate.Status.REVIEWED
        isolate.reviewed_by = request.user
        isolate.reviewed_at = timezone.now()
        isolate.save(update_fields=("final_organism", "status", "reviewed_by", "reviewed_at"))
        messages.success(request, "Identification reviewed and approved.")
    return redirect("laboratory_isolate_detail", isolate_id=isolate.pk)


@login_required
@lab_staff_required
@require_POST
def laboratory_biobank_save(request, isolate_id):
    isolate = get_object_or_404(LaboratoryIsolate, pk=isolate_id)
    harvested = (request.POST.get("date_harvested") or "").strip()
    try:
        date_harvested = datetime.strptime(harvested, "%Y-%m-%d").date() if harvested else None
    except ValueError:
        messages.error(request, "Enter a valid harvested date.")
        return redirect("laboratory_isolate_detail", isolate_id=isolate.pk)
    if date_harvested and date_harvested > timezone.localdate():
        messages.error(request, "Harvested date cannot be in the future.")
        return redirect("laboratory_isolate_detail", isolate_id=isolate.pk)
    limits = {"preservation_method": 100, "container": 100, "storage_location": 150,
              "storage_box": 100, "box_position": 30}
    data = {key: (request.POST.get(key) or "").strip() for key in limits}
    if any(len(data[key]) > limit for key, limit in limits.items()):
        messages.error(request, "One or more biobank fields exceed their maximum length.")
        return redirect("laboratory_isolate_detail", isolate_id=isolate.pk)
    LaboratoryBiobank.objects.update_or_create(
        isolate=isolate, defaults={
            **data, "date_harvested": date_harvested,
            "remarks": (request.POST.get("remarks") or "").strip(),
            "recorded_by": request.user,
        }
    )
    messages.success(request, "Biobank storage information saved.")
    return redirect("laboratory_isolate_detail", isolate_id=isolate.pk)


@login_required
@lab_worklist_required
def laboratory_biobank_label(request, isolate_id):
    isolate = get_object_or_404(LaboratoryIsolate, pk=isolate_id)
    if isolate.status != LaboratoryIsolate.Status.REVIEWED or not isolate.final_organism:
        messages.error(request, "An approved identification is required before printing a final sticker.")
        return redirect("laboratory_isolate_detail", isolate_id=isolate.pk)
    return render(request, "receiving/lab_biobank_label.html", {"isolate": isolate})


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
        workbook = load_workbook(BytesIO(upload.read()), read_only=True, data_only=True)
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
        or isolates[row["accession_number"]].status != LaboratoryIsolate.Status.TESTING
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
        if any(number not in locked or locked[number].status != LaboratoryIsolate.Status.TESTING
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
