from dataclasses import dataclass

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.home.models import (
    Batch_Table,
    Referred_Data,
    SiteData,
    TATform,
    accession_ref_sequence,
)
from apps.home_final.models import Final_Data


class BatchCreationError(Exception):
    pass


class InvalidBatchRange(BatchCreationError):
    pass


class BatchAlreadyExists(BatchCreationError):
    def __init__(self, batch, raw_count=0, final_count=0):
        self.batch = batch
        self.raw_count = raw_count
        self.final_count = final_count
        super().__init__(f"Batch '{batch.bat_Batch_Code}' already exists.")


class AccessionConflict(BatchCreationError):
    def __init__(self, conflicts):
        self.conflicts = list(conflicts)
        super().__init__("One or more accession numbers already belong to another record.")


@dataclass(frozen=True)
class BatchCreationResult:
    batch: Batch_Table
    accession_numbers: list[str]
    isolate_count: int
    recovered_orphan: bool = False


def parse_batch_ref_range(ref_no_raw):
    ref_no_raw = (ref_no_raw or "").strip()
    if not ref_no_raw:
        raise ValueError("Missing reference number.")
    parts = [part.strip() for part in ref_no_raw.split("-", 1)]
    start_raw, end_raw = (parts[0], parts[0]) if len(parts) == 1 else parts
    if not (start_raw.isdigit() and end_raw.isdigit()):
        raise ValueError("Invalid Ref No format.")
    start_ref, end_ref = int(start_raw), int(end_raw)
    if start_ref > end_ref:
        start_ref, end_ref = end_ref, start_ref
    return start_ref, end_ref, max(len(start_raw), len(end_raw), 4)


def build_batch_accessions(site_code, referral_date, ref_no_raw):
    start_ref, end_ref, width = parse_batch_ref_range(ref_no_raw)
    return [
        f"{referral_date:%y}ARS_{site_code}{number:0{width}d}"
        for number in range(start_ref, end_ref + 1)
    ]


def build_batch_code(site_code, referral_date, batch_no, total_batch, ref_no_raw):
    return f"{site_code}_{referral_date:%m%d%Y}_{batch_no}.{total_batch}_{ref_no_raw}"


def batch_accession_summary(accession_numbers):
    if not accession_numbers:
        return ""
    if len(accession_numbers) == 1:
        return accession_numbers[0]
    return f"{accession_numbers[0]} - {accession_numbers[-1]}"


def ref_no_from_batch_code(batch_code):
    value = (batch_code or "").strip()
    return value.rsplit("_", 1)[-1].strip() if "_" in value else ""


def batch_ref_no(batch):
    return (batch.bat_RefNo or "").strip() or ref_no_from_batch_code(batch.bat_Batch_Code)


def isolate_ref_no(isolate):
    return (
        (getattr(isolate, "RefNo", None) or "").strip()
        or batch_ref_no(getattr(isolate, "Batch_id", None))
        or ref_no_from_batch_code(getattr(isolate, "Batch_Code", ""))
    )


def batch_personnel_defaults(batch):
    return {
        "arsp_Encoder": batch.bat_Encoder or "",
        "arsp_Enc_Lic": batch.bat_Enc_Lic or "",
        "arsp_Checker": batch.bat_Checker or "",
        "arsp_Chec_Lic": batch.bat_Chec_Lic or "",
        "arsp_Verifier": batch.bat_Verifier or "",
        "arsp_Ver_Lic": batch.bat_Ver_Lic or "",
        "arsp_LabManager": batch.bat_LabManager or "",
        "arsp_Lab_Lic": batch.bat_Lab_Lic or "",
        "arsp_Head": batch.bat_Head or "",
        "arsp_Head_Lic": batch.bat_Head_Lic or "",
        "Date_Accomplished_ARSP": batch.bat_Date_Accomplished,
    }


def sync_batch_membership(batch, accession_numbers):
    new_accessions = list(dict.fromkeys(accession_numbers))
    modified_at = timezone.now()
    removed = list(
        Referred_Data.objects.filter(Batch_id=batch).exclude(AccessionNo__in=new_accessions)
    )
    removed_accessions = [item.AccessionNo for item in removed]
    if removed:
        Referred_Data.objects.filter(pk__in=[item.pk for item in removed]).update(
            Batch_id=None, bat_seq=None, Batch_Code="", Batch_Name="", RefNo="",
            BatchNo="", Total_batch="", Date_Modified=modified_at,
        )
        Final_Data.objects.filter(
            f_Batch_id=batch, f_AccessionNo__in=removed_accessions
        ).update(
            f_Batch_id=None, f_bat_seq=None, f_Batch_Code="", f_Batch_Name="",
            f_RefNo="", f_BatchNo="", f_Total_batch="", f_Date_Modified=modified_at,
        )

    personnel = batch_personnel_defaults(batch)
    ref_no = batch_ref_no(batch)
    for accession in new_accessions:
        Referred_Data.objects.get_or_create(AccessionNo=accession)
        Referred_Data.objects.filter(AccessionNo=accession).update(
            bat_seq=accession_ref_sequence(accession), Batch_id=batch,
            Batch_Code=batch.bat_Batch_Code, Referral_Date=batch.bat_Referral_Date,
            RefNo=ref_no, BatchNo=batch.bat_BatchNo, Total_batch=batch.bat_Total_batch,
            SiteCode=batch.bat_SiteCode, Site_Name=batch.bat_Site_Name,
            Batch_Name=batch.bat_Batch_Name, Date_Modified=modified_at, **personnel,
        )

    for isolate in Referred_Data.objects.filter(Batch_id=batch, AccessionNo__in=new_accessions):
        Final_Data.objects.filter(f_AccessionNo=isolate.AccessionNo).update(
            f_bat_seq=isolate.bat_seq, f_Batch_id=batch, f_Batch_Code=isolate.Batch_Code,
            f_Batch_Name=isolate.Batch_Name, f_RefNo=isolate_ref_no(isolate),
            f_BatchNo=isolate.BatchNo, f_Total_batch=isolate.Total_batch,
            f_SiteCode=isolate.SiteCode, f_Site_Name=isolate.Site_Name,
            f_Referral_Date=isolate.Referral_Date, f_arsp_Encoder=isolate.arsp_Encoder,
            f_arsp_Enc_Lic=isolate.arsp_Enc_Lic, f_arsp_Checker=isolate.arsp_Checker,
            f_arsp_Chec_Lic=isolate.arsp_Chec_Lic, f_arsp_Verifier=isolate.arsp_Verifier,
            f_arsp_Ver_Lic=isolate.arsp_Ver_Lic, f_arsp_LabManager=isolate.arsp_LabManager,
            f_arsp_Lab_Lic=isolate.arsp_Lab_Lic, f_arsp_Head=isolate.arsp_Head,
            f_arsp_Head_Lic=isolate.arsp_Head_Lic,
            f_Date_Accomplished_ARSP=isolate.Date_Accomplished_ARSP,
            f_Date_Modified=modified_at,
        )
    return len(new_accessions), len(removed_accessions)


def preview_referred_batch_conflicts(
    *, site_code, referral_date, start_number, end_number, batch_number, total_batches,
):
    """Read-only preview of conflicts checked by create_referred_batch.

    This is advisory: the atomic creation checks remain authoritative because
    another user may create a conflicting accession after the page loads.
    """
    ref_no = f"{start_number:04d}-{end_number:04d}"
    batch_code = build_batch_code(
        site_code, referral_date, batch_number, total_batches, ref_no
    )
    accessions = build_batch_accessions(site_code, referral_date, ref_no)
    warnings = []
    existing = Batch_Table.objects.filter(bat_Batch_Code=batch_code).first()
    orphan = None
    if existing:
        raw_count = Referred_Data.objects.filter(
            Q(Batch_id=existing) | Q(Batch_Code=batch_code)
        ).count()
        final_count = Final_Data.objects.filter(
            Q(f_Batch_id=existing) | Q(f_Batch_Code=batch_code)
        ).count()
        if raw_count or final_count or hasattr(existing, "laboratory_accession"):
            warnings.append(f"Batch code already exists: {batch_code}.")
        else:
            orphan = existing

    raw_query = Referred_Data.objects.filter(AccessionNo__in=accessions).exclude(
        Q(Batch_id__isnull=True) & Q(Batch_Code="")
    )
    # exclude(Batch_id=None) would incorrectly hide assigned-by-code records
    # whose Batch_id is NULL. Only ignore a real recoverable orphan batch.
    if orphan is not None:
        raw_query = raw_query.exclude(Batch_id=orphan)
    raw_conflicts = list(raw_query.values_list("AccessionNo", flat=True))
    final_conflicts = list(
        Final_Data.objects.filter(f_AccessionNo__in=accessions)
        .values_list("f_AccessionNo", flat=True)
    )
    conflicting_accessions = sorted(set(raw_conflicts + final_conflicts))
    if conflicting_accessions:
        visible = ", ".join(conflicting_accessions[:5])
        remaining = len(conflicting_accessions) - 5
        suffix = f" and {remaining} more" if remaining else ""
        warnings.append(f"Existing accession records: {visible}{suffix}.")

    return warnings


@transaction.atomic
def create_referred_batch(
    *, site_code, referral_date, start_number, end_number, batch_number,
    total_batches, created_by, site_name="", personnel=None,
):
    site_code = (site_code or "").strip()
    if not site_code or referral_date is None:
        raise InvalidBatchRange("Site and referral date are required.")
    try:
        start_number, end_number = int(start_number), int(end_number)
        batch_number, total_batches = int(batch_number), int(total_batches)
    except (TypeError, ValueError) as exc:
        raise InvalidBatchRange("Range and batch values must be integers.") from exc
    if not (1 <= start_number <= end_number <= 9999):
        raise InvalidBatchRange("Accession range must be between 1 and 9999.")
    if not (1 <= batch_number <= total_batches):
        raise InvalidBatchRange("Batch number must be between 1 and total batches.")

    # A site row acts as a stable mutex for absent-batch concurrency.
    site = SiteData.objects.select_for_update().filter(SiteCode=site_code).first()
    if site and not site_name:
        site_name = site.SiteName
    ref_no = f"{start_number:04d}-{end_number:04d}"
    accessions = build_batch_accessions(site_code, referral_date, ref_no)
    batch_code = build_batch_code(site_code, referral_date, batch_number, total_batches, ref_no)

    existing = Batch_Table.objects.select_for_update().filter(bat_Batch_Code=batch_code).first()
    orphan = None
    if existing:
        raw_count = Referred_Data.objects.filter(Q(Batch_id=existing) | Q(Batch_Code=batch_code)).count()
        final_count = Final_Data.objects.filter(Q(f_Batch_id=existing) | Q(f_Batch_Code=batch_code)).count()
        if raw_count or final_count or hasattr(existing, "laboratory_accession"):
            raise BatchAlreadyExists(existing, raw_count, final_count)
        orphan = existing

    raw_query = # Lock only the Raw Data rows. Selecting the nullable Batch_id relation
    # for diagnostics would otherwise make PostgreSQL reject FOR UPDATE.
    Referred_Data.objects.select_for_update(of=("self",)).filter(
        AccessionNo__in=accessions
    ).exclude(Q(Batch_id__isnull=True) & Q(Batch_Code=""))
    if orphan is not None:
        raw_query = raw_query.exclude(Batch_id=orphan)
    raw_conflicts = list(
        raw_query.values("AccessionNo", "Batch_Code", "Batch_id__bat_Batch_Code")
    )
    final_conflicts = list(
        Final_Data.objects.select_for_update().filter(f_AccessionNo__in=accessions)
        .values("f_AccessionNo", "f_Batch_Code")
    )
    if raw_conflicts or final_conflicts:
        raise AccessionConflict(raw_conflicts + final_conflicts)

    personnel = personnel or {}
    values = {
        "created_by": created_by,
        "bat_Batch_Name": batch_code,
        "bat_AccessionNo": batch_accession_summary(accessions),
        "bat_AccessionNoGen": batch_accession_summary(accessions),
        "bat_Batch_Code": batch_code,
        "bat_Site_Name": site_name or "",
        "bat_Site_NameGen": site_name or "",
        "bat_SiteCode": site_code,
        "bat_Referral_Date": referral_date,
        "bat_RefNo": ref_no,
        "bat_BatchNo": str(batch_number),
        "bat_Total_batch": str(total_batches),
    }
    for field in (
        "bat_Encoder", "bat_Enc_Lic", "bat_Checker", "bat_Chec_Lic",
        "bat_Verifier", "bat_Ver_Lic", "bat_LabManager", "bat_Lab_Lic",
        "bat_Head", "bat_Head_Lic",
    ):
        values[field] = personnel.get(field, "") or ""
    if orphan:
        batch = orphan
        for field, value in values.items():
            setattr(batch, field, value)
        batch.save(update_fields=list(values))
    else:
        batch = Batch_Table.objects.create(**values)

    count, _ = sync_batch_membership(batch, accessions)
    tat, _ = TATform.objects.get_or_create(tat_Batch_Isolates=batch)
    tat.tat_SiteCode = batch.bat_SiteCode
    tat.tat_Batch_Code = batch.bat_Batch_Code
    tat.tat_Referral_Date = batch.bat_Referral_Date
    tat.tat_Num_Isolate = count
    tat.tat_BatchNumber = batch.bat_BatchNo
    tat.tat_Total_Batch = batch.bat_Total_batch
    tat.save()
    return BatchCreationResult(batch, accessions, count, recovered_orphan=bool(orphan))


# Backward-compatible private aliases while callers migrate to the service API.
_parse_batch_ref_range = parse_batch_ref_range
_build_batch_accessions = build_batch_accessions
_build_batch_code = build_batch_code
_batch_accession_summary = batch_accession_summary
_batch_personnel_defaults = batch_personnel_defaults
_sync_batch_membership = sync_batch_membership
