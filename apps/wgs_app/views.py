
from collections import defaultdict
import datetime
from decimal import Decimal, InvalidOperation
from io import TextIOWrapper
import io
import re
from django.db import models, transaction
from django.db.utils import OperationalError, ProgrammingError
import csv
from django.db.models import Q, F, Func
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import render, redirect

from apps.home.views import nz
from apps.home.permissions import (
    ROLE_ADMIN,
    ROLE_CHECKER,
    ROLE_ENCODER,
    ROLE_LAB_ENCODER,
    ROLE_LAB_MANAGER,
    ROLE_VERIFIER,
    can_manage_batch,
    role_flags,
    role_required,
    verification_case_blocks_operational_outputs,
)

from .forms import *
from apps.home.forms import *
from apps.home_final.forms import *
import pandas as pd
from apps.home.models import *
from apps.home_final.models import *
from .models import *
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404
from django.contrib import messages
import os
from django.core.paginator import Paginator
import re
from .utils import format_accession
from django.contrib.auth.decorators import login_required
from django.conf import settings
from datetime import datetime
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.text import slugify
from django.template.loader import render_to_string
from django.views.decorators.http import require_POST
import openpyxl
from datetime import date, datetime, timedelta

WGS_WRITE_ALLOWED_ROLES = (ROLE_ADMIN, ROLE_CHECKER, ROLE_LAB_ENCODER)
UPLOAD_CENTER_ALLOWED_ROLES = (ROLE_ADMIN, ROLE_CHECKER, ROLE_ENCODER, ROLE_LAB_ENCODER)
BUILTIN_WGS_PIPELINES = [
    {"key": "sample_information", "name": "Sample Information", "upload": True, "overview": False, "sort": 10},
    {"key": "bactscout", "name": "BactScout", "upload": True, "overview": True, "sort": 20},
    {"key": "gtdbtk", "name": "GTDB-Tk", "upload": True, "overview": False, "sort": 30},
    {"key": "gambit", "name": "Gambit", "upload": True, "overview": True, "sort": 40},
    {"key": "mlst", "name": "MLST", "upload": True, "overview": True, "sort": 50},
    {"key": "checkm2", "name": "CheckM2", "upload": True, "overview": True, "sort": 60},
    {"key": "assembly", "name": "Assembly Scan", "upload": True, "overview": True, "sort": 70},
    {"key": "amrfinder", "name": "AMRFinderPlus", "upload": True, "overview": True, "sort": 80},
]


def ensure_builtin_wgs_pipeline_settings():
    for item in BUILTIN_WGS_PIPELINES:
        BuiltinWGSPipelineSetting.objects.get_or_create(
            pipeline_key=item["key"],
            defaults={
                "display_name": item["name"],
                "show_in_upload_center": item["upload"],
                "show_in_overview": item["overview"],
                "sort_order": item["sort"],
            },
        )


def visible_builtin_wgs_keys(target):
    ensure_builtin_wgs_pipeline_settings()
    field_name = "show_in_upload_center" if target == "upload" else "show_in_overview"
    return set(
        BuiltinWGSPipelineSetting.objects
        .filter(**{field_name: True})
        .values_list("pipeline_key", flat=True)
    )


# helper to read uploaded file (csv, tsv, or excel)
def read_uploaded_file(uploaded_file, sheet_name=None):
    import pandas as pd

    def read_delimited_upload(default_sep):
        try:
            sample = uploaded_file.read(4096)
            uploaded_file.seek(0)
        except Exception:
            sample = ""

        if isinstance(sample, bytes):
            sample_text = sample.decode("utf-8-sig", errors="ignore")
        else:
            sample_text = str(sample or "")

        header_line = sample_text.splitlines()[0] if sample_text.splitlines() else ""
        if default_sep and default_sep in header_line:
            delimiter = default_sep
        else:
            delimiter = default_sep
            try:
                delimiter = csv.Sniffer().sniff(sample_text, delimiters="\t,;|").delimiter
            except csv.Error:
                pass

        return pd.read_csv(
            uploaded_file,
            sep=delimiter,
            dtype=str,
            keep_default_na=False,
        )

    filename = uploaded_file.name.lower()
    if filename.endswith('.csv'):
        df = read_delimited_upload(",")
    elif filename.endswith('.tsv'):
        df = read_delimited_upload("\t")
    elif filename.endswith(('.xls', '.xlsx')):
        if sheet_name:
            excel_file = pd.ExcelFile(uploaded_file)
            requested_sheets = (
                [sheet_name]
                if isinstance(sheet_name, str)
                else list(sheet_name)
            )
            normalized_sheets = {
                str(name).strip().lower(): name
                for name in excel_file.sheet_names
            }
            for requested_sheet in requested_sheets:
                matched_sheet = normalized_sheets.get(str(requested_sheet).strip().lower())
                if matched_sheet:
                    df = pd.read_excel(excel_file, sheet_name=matched_sheet)
                    break
            else:
                raise ValueError(
                    "Required sheet not found. Expected one of "
                    f"{requested_sheets}; available sheets: {excel_file.sheet_names}"
                )
        else:
            df = pd.read_excel(uploaded_file)
    else:
        raise ValueError("Unsupported file format. Please upload a CSV, TSV, or Excel file.")

    df.columns = [
        str(column).replace("\ufeff", "").strip()
        for column in df.columns
    ]
    return df


def normalize_custom_column_name(value):
    return str(value or "").strip().lower()


def custom_row_value(row, column_options):
    normalized_row = {
        normalize_custom_column_name(key): value
        for key, value in row.items()
    }
    for column in column_options:
        key = normalize_custom_column_name(column)
        if key in normalized_row:
            value = normalized_row[key]
            if pd.isna(value):
                return ""
            return value
    return ""


def json_safe_value(value):
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def coerce_custom_pipeline_value(value, data_type):
    value = json_safe_value(value)
    if value == "":
        return ""
    if data_type == "integer":
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return str(value).strip()
    if data_type == "decimal":
        try:
            return str(Decimal(str(value).strip()))
        except (InvalidOperation, TypeError, ValueError):
            return str(value).strip()
    if data_type == "date":
        parsed = pd.to_datetime(value, errors="coerce")
        if pd.isna(parsed):
            return str(value).strip()
        return parsed.date().isoformat()
    if data_type == "boolean":
        return str(value).strip().lower() in {"true", "yes", "y", "1", "done", "passed"}
    return str(value).strip()


def custom_pipeline_accession_variants(value):
    text = str(value or "").strip()
    if not text:
        return []

    basename = os.path.basename(text)
    without_ext = os.path.splitext(basename)[0].strip()
    candidates = [text, basename, without_ext]
    for candidate in list(candidates):
        prefix_match = re.match(
            r"^(\d{2}ARS[-_][A-Z]+[-_]?\d+)",
            str(candidate or "").strip(),
            flags=re.IGNORECASE,
        )
        if prefix_match:
            candidates.append(prefix_match.group(1))
    normalized_candidates = []

    for candidate in candidates:
        candidate = str(candidate or "").strip()
        if not candidate:
            continue
        normalized_candidates.extend([
            candidate,
            candidate.replace("-", "_"),
            candidate.replace("_", "-"),
        ])

    variants = []
    seen = set()
    for candidate in normalized_candidates:
        key = candidate.upper()
        if key not in seen:
            seen.add(key)
            variants.append(candidate)
    return variants


def find_custom_pipeline_match(accession):
    accession_variants = custom_pipeline_accession_variants(accession)
    if not accession_variants:
        return None, ""

    final_filter = Q()
    wgs_filter = Q()
    for candidate in accession_variants:
        final_filter |= Q(f_AccessionNo__iexact=candidate)
        wgs_filter |= (
            Q(WGS_SampleInfo_Acc__iexact=candidate)
            | Q(WGS_BactScout_Acc__iexact=candidate)
            | Q(WGS_GtdbTk_Acc__iexact=candidate)
            | Q(WGS_Gambit_Acc__iexact=candidate)
            | Q(WGS_Mlst_Acc__iexact=candidate)
            | Q(WGS_Checkm2_Acc__iexact=candidate)
            | Q(WGS_Assembly_Acc__iexact=candidate)
            | Q(WGS_Amrfinder_Acc__iexact=candidate)
        )

    final_match = Final_Data.objects.filter(final_filter).first()
    if final_match:
        return final_match, "final"

    wgs_match = (
        WGS_Project.objects
        .filter(wgs_filter, Ref_Accession__isnull=False)
        .select_related("Ref_Accession")
        .first()
    )
    if wgs_match:
        return wgs_match.Ref_Accession, "wgs_project"

    return None, ""


def custom_record_accession_keys(record_data):
    """
    Return display/search accession keys for a custom WGS row.
    Uploaded files often contain names like 26ARS_ZMC0023.fna, while the
    database row is keyed as 26ARS_ZMC0023.
    """
    keys = []
    final_accession = record_data.get("matched_final_data_id")
    uploaded_accession = record_data.get("accession")

    for value in (final_accession,):
        value = str(value or "").strip()
        if value:
            keys.append(value)

    for value in custom_pipeline_accession_variants(uploaded_accession):
        value = str(value or "").strip()
        if value:
            keys.append(value)

    unique_keys = []
    seen = set()
    for key in keys:
        normalized = key.upper()
        if normalized not in seen:
            seen.add(normalized)
            unique_keys.append(key)
    return unique_keys


def custom_record_primary_accession(record_data):
    keys = custom_record_accession_keys(record_data)
    return keys[0] if keys else ""


def custom_record_display_accession(record):
    return (
        (getattr(record, "matched_final_data_id", "") or "").strip()
        or custom_record_primary_accession({"accession": getattr(record, "accession", "")})
        or (getattr(record, "accession", "") or "").strip()
    )


def ensure_custom_wgs_project(final_match):
    if not final_match:
        return None
    project = (
        WGS_Project.objects
        .filter(Ref_Accession=final_match)
        .order_by("id")
        .first()
    )
    if project:
        return project
    return WGS_Project.objects.create(
        Ref_Accession=final_match,
        WGS_SampleInfoSummary=False,
        WGS_BactScoutSummary=False,
        WGS_GtdbTkSummary=False,
        WGS_GambitSummary=False,
        WGS_MlstSummary=False,
        WGS_Checkm2Summary=False,
        WGS_AssemblySummary=False,
        WGS_AmrfinderSummary=False,
    )


def infer_custom_field_type(series):
    non_empty = series.dropna()
    non_empty = non_empty[non_empty.astype(str).str.strip() != ""]
    if non_empty.empty:
        return "text"
    bool_values = {"true", "false", "yes", "no", "y", "n", "1", "0", "passed", "failed"}
    normalized_values = {str(value).strip().lower() for value in non_empty.head(50)}
    if normalized_values and normalized_values.issubset(bool_values):
        return "boolean"
    numeric_values = pd.to_numeric(non_empty, errors="coerce")
    if numeric_values.notna().mean() >= 0.9:
        if (numeric_values.dropna() % 1 == 0).all():
            return "integer"
        return "decimal"
    parsed_dates = pd.to_datetime(non_empty, errors="coerce")
    if parsed_dates.notna().mean() >= 0.9:
        return "date"
    return "text"


def build_custom_pipeline_column_preview(df):
    columns = [str(column).strip() for column in df.columns if str(column).strip()]
    normalized_columns = {column: normalize_custom_column_name(column) for column in columns}

    def score_column(column, keywords):
        normalized = normalized_columns[column]
        return max((keyword in normalized for keyword in keywords), default=False)

    accession_keywords = ["accession", "accession no", "accession_no", "acc no", "acc_no", "ref no", "ref_no"]
    sample_keywords = ["sample", "sample name", "sample_name", "isolate", "name"]
    date_keywords = ["date", "uploaded", "run date", "run_date"]

    accession_guess = next((column for column in columns if score_column(column, accession_keywords)), columns[0] if columns else "")
    sample_guess = next((column for column in columns if score_column(column, sample_keywords) and column != accession_guess), "")
    date_guess = next((column for column in columns if score_column(column, date_keywords)), "")

    preview_columns = []
    for index, column in enumerate(columns):
        suggested_label = column.replace("_", " ").replace("-", " ").strip().title()
        data_type = infer_custom_field_type(df[column])
        preview_columns.append({
            "index": index,
            "name": column,
            "label": suggested_label,
            "data_type": data_type,
            "include": column not in {accession_guess, sample_guess, date_guess},
        })

    return preview_columns, accession_guess, sample_guess, date_guess


def build_custom_pipeline_column_preview_from_post(post_data):
    indices = []
    for key in post_data:
        if key.startswith("column_name_"):
            indices.append(key.replace("column_name_", "", 1))
    preview_columns = []
    selected_indices = set(post_data.getlist("selected_column_indices"))
    for index in sorted(indices, key=lambda value: int(value) if value.isdigit() else value):
        column = post_data.get(f"column_name_{index}", "").strip()
        if not column:
            continue
        preview_columns.append({
            "index": index,
            "name": column,
            "label": post_data.get(f"field_label_{index}", column).strip() or column,
            "data_type": post_data.get(f"field_type_{index}", "text"),
            "include": index in selected_indices,
        })
    return preview_columns


def get_or_create_wgs_project_for_upload(
    accession,
    referred_obj,
    accession_field,
    summary_field,
    existing_project=None,
):
    """
    Re-use the WGS connector for a specific WGS sample/run during uploads.

    One Final_Data isolate can have many WGS_Project rows. Therefore this
    helper must not collapse all rows for the same accession into one project.
    Callers should pass existing_project only when they have matched the same
    module sample/run identity (for example BactScout.name/sample_id). If no
    exact sample/run match exists, a new WGS_Project is created.
    """
    accession = (accession or "").strip()
    project = None

    project = existing_project

    if not project:
        project = WGS_Project.objects.create(
            Ref_Accession=referred_obj if referred_obj else None,
            WGS_SampleInfoSummary=False,
            WGS_BactScoutSummary=False,
            WGS_GtdbTkSummary=False,
            WGS_GambitSummary=False,
            WGS_MlstSummary=False,
            WGS_Checkm2Summary=False,
            WGS_AssemblySummary=False,
            WGS_AmrfinderSummary=False,
        )

    if referred_obj and project.Ref_Accession_id != referred_obj.f_AccessionNo:
        project.Ref_Accession = referred_obj

    setattr(project, accession_field, accession)
    setattr(
        project,
        summary_field,
        bool(accession)
        and bool(project.Ref_Accession)
        and accession == getattr(project.Ref_Accession, "f_AccessionNo", None)
    )
    project.save(update_fields=["Ref_Accession", accession_field, summary_field])
    return project


def find_existing_wgs_module_record(model, accession_field, accession, sample_field, sample_value):
    accession = (accession or "").strip()
    sample_value = (sample_value or "").strip()
    if not sample_value:
        return None
    return (
        model.objects
        .filter(**{accession_field: accession, sample_field: sample_value})
        .select_related()
        .order_by("-id")
        .first()
    )


def find_existing_wgs_module_record_by_fields(model, match_fields):
    cleaned_fields = {}
    for field, value in match_fields.items():
        try:
            if pd.isna(value):
                value = ""
        except (TypeError, ValueError):
            pass
        cleaned_fields[field] = value.strip() if isinstance(value, str) else value
    if not any(value not in (None, "") for value in cleaned_fields.values()):
        return None
    return (
        model.objects
        .filter(**cleaned_fields)
        .select_related()
        .order_by("-id")
        .first()
    )


WGS_SAMPLEINFO_LINK_FIELDS = {
    "BactScout": {
        "project_field": "bactscout_project",
        "accession_field": "BactScout_Accession",
        "project_accession_field": "WGS_BactScout_Acc",
        "summary_field": "WGS_BactScoutSummary",
        "sample_fields": ("sample_id",),
    },
    "GtdbTk": {
        "project_field": "gtdbtk_project",
        "accession_field": "GtdbTk_Accession",
        "project_accession_field": "WGS_GtdbTk_Acc",
        "summary_field": "WGS_GtdbTkSummary",
        "sample_fields": ("user_genome",),
    },
    "Gambit": {
        "project_field": "gambit_project",
        "accession_field": "Gambit_Accession",
        "project_accession_field": "WGS_Gambit_Acc",
        "summary_field": "WGS_GambitSummary",
        "sample_fields": ("sample",),
    },
    "Mlst": {
        "project_field": "mlst_project",
        "accession_field": "Mlst_Accession",
        "project_accession_field": "WGS_Mlst_Acc",
        "summary_field": "WGS_MlstSummary",
        "sample_fields": ("file",),
    },
    "Checkm2": {
        "project_field": "checkm2_project",
        "accession_field": "Checkm2_Accession",
        "project_accession_field": "WGS_Checkm2_Acc",
        "summary_field": "WGS_Checkm2Summary",
        "sample_fields": ("Name",),
    },
    "AssemblyScan": {
        "project_field": "assembly_project",
        "accession_field": "Assembly_Accession",
        "project_accession_field": "WGS_Assembly_Acc",
        "summary_field": "WGS_AssemblySummary",
        "sample_fields": ("sample",),
    },
    "Amrfinderplus": {
        "project_field": "amrfinder_project",
        "accession_field": "Amrfinder_Accession",
        "project_accession_field": "WGS_Amrfinder_Acc",
        "summary_field": "WGS_AmrfinderSummary",
        "sample_fields": ("name",),
    },
}


def normalize_wgs_sample_identity(value):
    text = upload_text_or_none(value)
    if not text:
        return ""
    base = os.path.basename(text)
    return os.path.splitext(base)[0].strip()


def build_wgs_sample_identity_variants(value):
    normalized = normalize_wgs_sample_identity(value)
    text = upload_text_or_none(value) or ""
    variants = [text, normalized]
    if normalized:
        variants.extend([
            normalized.replace("-", "_"),
            normalized.replace("_", "-"),
        ])

    unique_variants = []
    seen = set()
    for variant in variants:
        variant = str(variant or "").strip()
        if not variant:
            continue
        key = variant.upper()
        if key not in seen:
            seen.add(key)
            unique_variants.append(variant)
    return unique_variants


def format_wgs_accession_from_sample_name(raw_name, site_codes=None, batch_code=""):
    """
    Normalize WGS sample names to the app accession format.

    Only extracts accessions from standard names that contain the ARS accession
    prefix, such as 26ARS-ZMC0005-20260822A. Nonstandard batch/sample names are
    still imported by each uploader, but their accession is intentionally blank.
    """
    if not raw_name:
        return ""

    site_codes = {
        str(code or "").strip().upper()
        for code in (site_codes or SiteData.objects.values_list("SiteCode", flat=True))
        if str(code or "").strip()
    }
    name = normalize_wgs_sample_identity(raw_name).upper()
    name = re.sub(r"\s+", "", name)
    if not name or any(skip in name for skip in ("UTPR", "UTPN", "BL")):
        return ""

    direct_match = re.search(
        r"(?P<prefix>\d{2,4}ARS)[-_]?(?P<site>[A-Z]{2,6})[-_]?(?P<number>\d{3,6})",
        name,
    )
    if direct_match:
        site = direct_match.group("site")
        if not site_codes or site in site_codes:
            return f"{direct_match.group('prefix')}_{site}{direct_match.group('number')}"

    parts = re.split(r"[-_]", name)
    if not parts:
        return ""

    prefix = next((part for part in parts if re.match(r"^\d{2,4}ARS$", part)), "")
    if prefix:
        for part in parts:
            match = re.match(r"^([A-Z]{2,6})(\d{3,6})", part)
            if match and match.group(1) in site_codes:
                return f"{prefix}_{match.group(1)}{match.group(2)}"

        for index, part in enumerate(parts):
            if part in site_codes:
                digits = ""
                if index + 1 < len(parts):
                    digit_match = re.search(r"(\d{3,6})", parts[index + 1])
                    if digit_match:
                        digits = digit_match.group(1)
                return f"{prefix}_{part}{digits}" if digits else f"{prefix}_{part}"

    return ""


def resolve_wgs_accession_from_sample_name(raw_name, site_codes=None, batch_code=""):
    accession = format_wgs_accession_from_sample_name(raw_name, site_codes, batch_code=batch_code)
    if accession:
        return accession

    normalized = normalize_wgs_sample_identity(raw_name).upper()
    if not re.search(r"\d{2,4}ARS", normalized):
        return ""

    variants = build_wgs_sample_identity_variants(raw_name)
    if not variants:
        return ""

    sampleinfo = (
        SampleInformation.objects
        .filter(sample_name__in=variants)
        .exclude(sample_accession__isnull=True)
        .exclude(sample_accession="")
        .order_by("-Date_uploaded_si", "-pk")
        .first()
    )
    return (sampleinfo.sample_accession or "").strip() if sampleinfo else ""


def build_wgs_sample_identity_filter(link_config, sample_name):
    sample_name = (sample_name or "").strip()
    normalized = normalize_wgs_sample_identity(sample_name)
    if not sample_name:
        return Q(pk__isnull=True)

    identity_filter = Q(**{link_config["accession_field"]: sample_name})
    if normalized and normalized != sample_name:
        identity_filter |= Q(**{link_config["accession_field"]: normalized})

    for field in link_config["sample_fields"]:
        identity_filter |= Q(**{field: sample_name})
        identity_filter |= Q(**{f"{field}__icontains": sample_name})
        if normalized and normalized != sample_name:
            identity_filter |= Q(**{field: normalized})
            identity_filter |= Q(**{f"{field}__icontains": normalized})

    return identity_filter


def sync_wgs_records_from_sampleinfo(sampleinfo):
    sample_name = (sampleinfo.sample_name or "").strip()
    sample_accession = (sampleinfo.sample_accession or "").strip()
    if not sample_name or not sample_accession:
        return 0

    referred_obj = (
        Final_Data.objects.filter(f_AccessionNo=sample_accession).first()
        if sample_accession else None
    )
    project = get_or_create_wgs_project_for_upload(
        sample_accession,
        referred_obj,
        "WGS_SampleInfo_Acc",
        "WGS_SampleInfoSummary",
        existing_project=sampleinfo.sample_project,
    )
    if sampleinfo.sample_project_id != project.pk:
        sampleinfo.sample_project = project
        sampleinfo.save(update_fields=["sample_project"])

    linked_count = 0
    for model_name, link_config in WGS_SAMPLEINFO_LINK_FIELDS.items():
        model = globals().get(model_name)
        if not model:
            continue

        records = list(model.objects.filter(
            build_wgs_sample_identity_filter(link_config, sample_name)
        ))
        for record in records:
            update_fields = []
            if getattr(record, link_config["accession_field"]) != sample_accession:
                setattr(record, link_config["accession_field"], sample_accession)
                update_fields.append(link_config["accession_field"])

            if getattr(record, f"{link_config['project_field']}_id") != project.pk:
                setattr(record, link_config["project_field"], project)
                update_fields.append(link_config["project_field"])

            if update_fields:
                record.save(update_fields=update_fields)
                linked_count += 1

        setattr(project, link_config["project_accession_field"], sample_accession)
        setattr(project, link_config["summary_field"], bool(records))
        project.save(update_fields=[
            link_config["project_accession_field"],
            link_config["summary_field"],
        ])

    return linked_count


def sync_wgs_record_from_sampleinfo(record):
    link_config = WGS_SAMPLEINFO_LINK_FIELDS.get(record.__class__.__name__)
    if not link_config:
        return False

    identities = [
        getattr(record, link_config["accession_field"], None),
        *[getattr(record, field, None) for field in link_config["sample_fields"]],
    ]
    candidates = {
        identity
        for raw_identity in identities
        for identity in build_wgs_sample_identity_variants(raw_identity)
        if identity
    }
    if not candidates:
        return False

    sampleinfo = (
        SampleInformation.objects
        .filter(sample_name__in=candidates)
        .exclude(sample_accession__isnull=True)
        .exclude(sample_accession="")
        .select_related("sample_project")
        .order_by("-Date_uploaded_si", "-pk")
        .first()
    )
    if not sampleinfo:
        return False

    sync_wgs_records_from_sampleinfo(sampleinfo)
    return True


def save_or_update_wgs_module_record(existing_record, defaults, overwrite=True):
    model = defaults.pop("_model")
    if existing_record:
        if not overwrite:
            return existing_record, "skipped"
        for field, value in defaults.items():
            setattr(existing_record, field, value)
        existing_record.save(update_fields=list(defaults.keys()))
        sync_wgs_record_from_sampleinfo(existing_record)
        return existing_record, "updated"
    record = model.objects.create(**defaults)
    sync_wgs_record_from_sampleinfo(record)
    return record, "created"


def bulk_save_wgs_module_records(
    model,
    rows,
    *,
    accession_field,
    project_field,
    project_accession_field,
    summary_field,
    key_fields,
    value_fields,
    overwrite=True,
    batch_size=1000,
):
    cleaned_rows = []
    for row in rows:
        cleaned = {}
        for field, value in row.items():
            if field in {project_field, "matched_final_data"}:
                cleaned[field] = value
            elif field.endswith("_id"):
                cleaned[field] = value
            elif isinstance(value, (int, float, Decimal, date, datetime)):
                try:
                    cleaned[field] = None if pd.isna(value) else value
                except (TypeError, ValueError):
                    cleaned[field] = value
            else:
                cleaned[field] = upload_text_or_none(value)
        cleaned_rows.append(cleaned)

    deduped_rows = {}
    duplicate_count = 0
    for row in cleaned_rows:
        key = tuple(str(row.get(field) or "").strip() for field in key_fields)
        if not any(key):
            continue
        if key in deduped_rows:
            duplicate_count += 1
        deduped_rows[key] = row
    rows = list(deduped_rows.values())

    accessions = {
        (row.get(accession_field) or "").strip()
        for row in rows
        if (row.get(accession_field) or "").strip()
    }
    final_by_accession = {
        item.f_AccessionNo: item
        for item in Final_Data.objects.filter(f_AccessionNo__in=accessions)
    }

    existing_filter = Q()
    for key_field in key_fields:
        key_values = [
            str(row.get(key_field) or "").strip()
            for row in rows
            if str(row.get(key_field) or "").strip()
        ]
        if key_values:
            existing_filter |= Q(**{f"{key_field}__in": key_values})
    existing_records = []
    if existing_filter:
        existing_records = list(model.objects.filter(existing_filter).select_related(project_field))

    existing_by_key = {}
    for record in existing_records:
        key = tuple(str(getattr(record, field, "") or "").strip() for field in key_fields)
        existing_by_key.setdefault(key, record)

    project_by_accession = {}
    for row in rows:
        accession = (row.get(accession_field) or "").strip()
        if not accession:
            row[project_field] = None
            continue
        key = tuple(str(row.get(field) or "").strip() for field in key_fields)
        existing_record = existing_by_key.get(key)
        if existing_record and getattr(existing_record, f"{project_field}_id"):
            project = getattr(existing_record, project_field)
        else:
            project = project_by_accession.get(accession)
            if not project:
                project = get_or_create_wgs_project_for_upload(
                    accession,
                    final_by_accession.get(accession),
                    project_accession_field,
                    summary_field,
                )
                project_by_accession[accession] = project
        row[project_field] = project

    create_objects = []
    update_objects = []
    skipped_count = 0
    update_fields = list(dict.fromkeys([
        accession_field,
        project_field,
        *value_fields,
    ]))

    for row in rows:
        key = tuple(str(row.get(field) or "").strip() for field in key_fields)
        existing_record = existing_by_key.get(key)
        if existing_record:
            if not overwrite:
                skipped_count += 1
                continue
            for field in update_fields:
                setattr(existing_record, field, row.get(field))
            update_objects.append(existing_record)
        else:
            create_objects.append(model(**{field: row.get(field) for field in update_fields}))

    with transaction.atomic():
        if create_objects:
            model.objects.bulk_create(create_objects, batch_size=batch_size)
        if update_objects:
            model.objects.bulk_update(update_objects, update_fields, batch_size=batch_size)

    return {
        "created": len(create_objects),
        "updated": len(update_objects),
        "skipped": skipped_count,
        "duplicates": duplicate_count,
        "processed": len(cleaned_rows),
    }


def relink_all_wgs_records_from_sampleinfo():
    linked_count = 0
    for sampleinfo in SampleInformation.objects.exclude(sample_name__isnull=True).exclude(sample_name=""):
        linked_count += sync_wgs_records_from_sampleinfo(sampleinfo)
    return linked_count


def build_wgs_search_terms(query):
    query = (query or "").strip()
    if not query:
        return []

    raw_terms = {
        query,
        query.replace("_", "-"),
        query.replace("-", "_"),
        query.replace(" ", ""),
        query.replace(" ", "-"),
        query.replace(" ", "_"),
        re.sub(r"[\s_-]+", "", query),
    }
    search_terms = []
    seen_terms = set()
    for term in raw_terms:
        term = str(term or "").strip()
        if not term:
            continue
        key = term.upper()
        if key not in seen_terms:
            seen_terms.add(key)
            search_terms.append(term)
    return search_terms


def apply_wgs_text_search(queryset, query):
    search_terms = build_wgs_search_terms(query)
    if not search_terms:
        return queryset

    model = queryset.model
    searchable_fields = [
        field.name
        for field in model._meta.fields
        if isinstance(field, (models.CharField, models.TextField))
    ]
    search_filter = Q()
    for term in search_terms:
        for field_name in searchable_fields:
            search_filter |= Q(**{f"{field_name}__icontains": term})
    return queryset.filter(search_filter)


def apply_wgs_related_accession_search(queryset, query, related_accession_lookup):
    search_terms = build_wgs_search_terms(query)
    if not search_terms:
        return queryset

    model = queryset.model
    searchable_fields = [
        field.name
        for field in model._meta.fields
        if isinstance(field, (models.CharField, models.TextField))
    ]
    search_filter = Q()
    for term in search_terms:
        search_filter |= Q(**{f"{related_accession_lookup}__icontains": term})
        for field_name in searchable_fields:
            search_filter |= Q(**{f"{field_name}__icontains": term})
    return queryset.filter(search_filter)


def wgs_upload_overwrite_enabled(request):
    return request.POST.get("overwrite", "false").lower() == "true"


def upload_text_or_none(value):
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text if text and text.lower() != "nan" else None


def upload_number_or_none(value, integer=False):
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, str):
        text = value.strip()
        if not text or text.lower() == "nan" or ";" in text:
            return None
        text = text.replace(",", "")
    else:
        text = value
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return int(number) if integer else number


def upload_bool_or_false(value):
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "checked", "x"}


def normalize_mlst_upload_dataframe(df):
    df.columns = [
        str(column).replace("\ufeff", "").strip().replace(".", "")
        if column is not None else ""
        for column in df.columns
    ]
    df = df.loc[:, [column for column in df.columns if column and column.lower() != "nan"]]

    column_lookup = {
        str(column).strip().lower(): column
        for column in df.columns
    }

    def add_alias(target, *sources):
        if target in df.columns:
            return
        for source in sources:
            source_column = column_lookup.get(source.lower())
            if source_column:
                df[target] = df[source_column]
                return
        df[target] = ""

    add_alias("FILE", "FILE", "file", "name")
    add_alias("SCHEME", "SCHEME", "scheme")
    add_alias("ST", "ST", "MLST", "mlst")
    add_alias("STATUS", "STATUS", "status")
    add_alias("SCORE", "SCORE", "score")

    if "ALLELES" not in df.columns:
        allele_columns = [
            column for column in df.columns
            if re.match(r"^allele\d+$", str(column), re.IGNORECASE)
        ]
        if allele_columns:
            df["ALLELES"] = df[allele_columns].apply(
                lambda row: ";".join(
                    text for value in row
                    for text in [upload_text_or_none(value)]
                    if text
                ),
                axis=1,
            )
        else:
            df["ALLELES"] = ""

    return df


def has_tested_antibiotic_value(*values):
    for value in values:
        if value is None:
            continue
        try:
            if pd.isna(value):
                continue
        except (TypeError, ValueError):
            pass
        text = str(value).strip()
        if text and text.lower() not in {"nan", "none", "n/a", "na", "-"}:
            return True
    return False
    

# handles the connection of WGS project to referred data
@login_required
@role_required(*UPLOAD_CENTER_ALLOWED_ROLES)
def upload_wgs_view(request):

    if request.method == "POST":
        access = role_flags(request.user)
        form = WGSProjectForm(request.POST)
        sampleinfo_form = SampleInfoUploadForm(request.POST, request.FILES)
        bactscout_form = BactScoutUploadForm(request.POST, request.FILES)
        gtdbtk_form = GtdbTkUploadForm(request.POST, request.FILES)
        gambit_form = GambitUploadForm(request.POST, request.FILES)
        mlst_form = MlstUploadForm(request.POST, request.FILES)
        checkm2_form = Checkm2UploadForm(request.POST, request.FILES)
        assembly_form = AssemblyUploadForm(request.POST, request.FILES)
        amrfinder_form = AmrUploadForm(request.POST, request.FILES)
        demogs_form = DemogsDataUploadForm(request.POST, request.FILES)
        antibiotic_form = FinalAntibioticUploadForm(request.POST, request.FILES)
        raw_antibiotic_form = RawAntibioticUploadForm(request.POST, request.FILES)

        final_data_uploaded = False
        final_antibiotic_uploaded = False
        raw_antibiotic_uploaded = False
        project_saved = False
        sampleinfo_uploaded = False
        bactscout_uploaded = False
        gtdbtk_uploaded = False
        gambit_uploaded = False
        mlst_uploaded = False
        checkm2_uploaded = False
        assembly_uploaded = False
        amrfinder_uploaded = False
        
        
        referred_upload_fields = {"DemogsDataFile", "FinalAntibioticFile", "RawAntibioticFile"}
        wgs_upload_fields = {
            "sampleinfo",
            "bactscoutfile",
            "GtdbTkFile",
            "GambitFile",
            "Mlstfile",
            "Checkm2file",
            "Assemblyfile",
            "Amrfinderfile",
        }

        if not access["can_create"] and referred_upload_fields.intersection(request.FILES):
            messages.error(request, "You do not have permission to upload raw or final referred data.")
            return redirect("upload_wgs_view")

        if not access["can_wgs_create"] and wgs_upload_fields.intersection(request.FILES):
            messages.error(request, "You do not have permission to upload WGS data.")
            return redirect("upload_wgs_view")

        # Final Data upload
        if access["can_create"] and demogs_form.is_valid():
            demogs_form.save()
            final_data_uploaded = True

        # Final antibiotic upload
        if access["can_create"] and antibiotic_form.is_valid():
            antibiotic_form.save()
            final_antibiotic_uploaded = True

        # Raw antibiotic upload
        if access["can_create"] and raw_antibiotic_form.is_valid():
            raw_antibiotic_form.save()
            raw_antibiotic_uploaded = True


        # WGS Project
        if access["can_wgs_create"] and form.is_valid():
            form.save()
            project_saved = True

        # SampleInfo Upload
        if access["can_wgs_create"] and sampleinfo_form.is_valid():
            sampleinfo_form.save()
            sampleinfo_uploaded = True

        # Bacscout Upload
        if access["can_wgs_create"] and bactscout_form.is_valid():
            bactscout_form.save()
            bactscout_uploaded = True


        # Bacscout Upload
        if access["can_wgs_create"] and gtdbtk_form.is_valid():
            gtdbtk_form.save()
            gtdbtk_uploaded = True

        # Gambit Upload
        if access["can_wgs_create"] and gambit_form.is_valid():
            gambit_form.save()
            gambit_uploaded = True
        
        # Mlst Upload
        if access["can_wgs_create"] and mlst_form.is_valid():
            mlst_form.save()
            mlst_uploaded = True
        
        # Checkm2 Upload
        if access["can_wgs_create"] and checkm2_form.is_valid():
            checkm2_form.save()
            checkm2_uploaded = True
        
        # Assembly scan Upload
        if access["can_wgs_create"] and assembly_form.is_valid():
            assembly_form.save()
            assembly_uploaded = True

        
        # Amrfinder Upload
        if access["can_wgs_create"] and amrfinder_form.is_valid():
            amrfinder_form.save()
            amrfinder_uploaded = True

        # If any form worked, refresh
        if project_saved or final_data_uploaded or final_antibiotic_uploaded or raw_antibiotic_uploaded or sampleinfo_uploaded or bactscout_uploaded or gtdbtk_uploaded or gambit_uploaded or mlst_uploaded or checkm2_uploaded or assembly_uploaded or amrfinder_uploaded:
            return redirect("upload_wgs_view")

    else:
        form = WGSProjectForm()
        demogs_form = DemogsDataUploadForm()
        antibiotic_form = FinalAntibioticUploadForm()
        raw_antibiotic_form = RawAntibioticUploadForm()
        sampleinfo_form = SampleInfoUploadForm()
        bactscout_form = BactScoutUploadForm()
        gtdbtk_form = GtdbTkUploadForm()
        gambit_form = GambitUploadForm()
        mlst_form = MlstUploadForm()
        checkm2_form = Checkm2UploadForm()
        assembly_form = AssemblyUploadForm()
        amrfinder_form = AmrUploadForm()

    return render(
        request,
        "wgs_app/Add_wgs.html",
        {
            "form": form,
            "demogs_form": demogs_form,
            "antibiotic_form": antibiotic_form,
            "raw_antibiotic_form": raw_antibiotic_form,
            "sampleinfo_form": sampleinfo_form,
            "bactscout_form": bactscout_form,
            "gtdbtk_form": gtdbtk_form,
            "gambit_form": gambit_form,
            "mlst_form": mlst_form,
            "checkm2_form": checkm2_form,
            "assembly_form": assembly_form,
            "amrfinder_form": amrfinder_form,
            "custom_pipelines": CustomWGSPipeline.objects.filter(
                is_active=True,
                show_in_upload_center=True,
            ).prefetch_related("fields").order_by("sequencing_type", "name"),
            "visible_builtin_upload_keys": visible_builtin_wgs_keys("upload"),
            "editing": False,
        },
    )


@login_required
@role_required(ROLE_ADMIN)
def custom_pipeline_list(request):
    ensure_builtin_wgs_pipeline_settings()
    if request.method == "POST":
        if not role_flags(request.user).get("can_wgs_create"):
            messages.error(request, "You do not have permission to manage WGS pipeline settings.")
            return redirect("custom_pipeline_list")

        upload_keys = set(request.POST.getlist("builtin_upload"))
        overview_keys = set(request.POST.getlist("builtin_overview"))
        for setting in BuiltinWGSPipelineSetting.objects.all():
            setting.show_in_upload_center = setting.pipeline_key in upload_keys
            setting.show_in_overview = setting.pipeline_key in overview_keys
            setting.save(update_fields=["show_in_upload_center", "show_in_overview", "updated_at"])

        custom_upload_keys = set(request.POST.getlist("custom_upload"))
        custom_overview_keys = set(request.POST.getlist("custom_overview"))
        for pipeline in CustomWGSPipeline.objects.all():
            pipeline.show_in_upload_center = str(pipeline.id) in custom_upload_keys
            pipeline.show_in_overview = str(pipeline.id) in custom_overview_keys
            pipeline.save(update_fields=["show_in_upload_center", "show_in_overview", "updated_at"])

        messages.success(request, "WGS pipeline visibility settings updated.")
        next_url = request.POST.get("next") or ""
        if next_url:
            return redirect(next_url)
        return redirect("custom_pipeline_list")

    builtin_settings = BuiltinWGSPipelineSetting.objects.all()
    pipelines = CustomWGSPipeline.objects.prefetch_related("fields").order_by("sequencing_type", "name")
    pipeline_cards = []
    for pipeline in pipelines:
        records = pipeline.records.all()
        pipeline_cards.append({
            "pipeline": pipeline,
            "field_count": pipeline.fields.count(),
            "record_count": records.count(),
            "matched_count": records.filter(match_status="matched").count(),
            "unmatched_count": records.exclude(match_status="matched").count(),
            "last_upload": pipeline.upload_batches.first(),
        })
    return render(
        request,
        "wgs_app/custom_pipeline_list.html",
        {
            "pipeline_cards": pipeline_cards,
            "builtin_settings": builtin_settings,
        },
    )


@login_required
@role_required(ROLE_ADMIN)
def custom_pipeline_create(request):
    if request.method == "POST":
        action = request.POST.get("action")
        form = CustomWGSPipelineForm(request.POST)

        if action == "preview_sample":
            sample_file = request.FILES.get("sample_file")
            if not sample_file:
                messages.error(request, "Please upload a sample CSV/TSV/XLSX file.")
                return render(request, "wgs_app/custom_pipeline_form.html", {"form": form, "pipeline": None})
            try:
                df = read_uploaded_file(sample_file, sheet_name=request.POST.get("sheet_name") or None)
                preview_columns, accession_guess, sample_guess, date_guess = build_custom_pipeline_column_preview(df)
            except Exception as exc:
                messages.error(request, f"Sample file could not be read: {exc}")
                return render(request, "wgs_app/custom_pipeline_form.html", {"form": form, "pipeline": None})

            if not preview_columns:
                messages.error(request, "No columns were found in the sample file.")
                return render(request, "wgs_app/custom_pipeline_form.html", {"form": form, "pipeline": None})

            form_data = request.POST.copy()
            form_data["accession_column"] = accession_guess
            form_data["sample_name_column"] = sample_guess
            form_data["date_column"] = date_guess
            form = CustomWGSPipelineForm(form_data)
            return render(
                request,
                "wgs_app/custom_pipeline_form.html",
                {
                    "form": form,
                    "pipeline": None,
                    "preview_columns": preview_columns,
                    "accession_guess": accession_guess,
                    "sample_guess": sample_guess,
                    "date_guess": date_guess,
                },
            )

        if form.is_valid():
            pipeline = form.save(commit=False)
            if not pipeline.slug:
                pipeline.slug = slugify(pipeline.name)
            pipeline.created_by = request.user
            pipeline.save()
            selected_indices = request.POST.getlist("selected_column_indices")
            created_fields = 0
            used_keys = set()
            for order, column_index in enumerate(selected_indices, start=1):
                column = request.POST.get(f"column_name_{column_index}", "").strip()
                if not column:
                    continue
                label = request.POST.get(f"field_label_{column_index}", column).strip() or column
                data_type = request.POST.get(f"field_type_{column_index}", "text")
                field_key = slugify(label) or slugify(column) or f"field-{order}"
                base_key = field_key
                counter = 2
                while field_key in used_keys:
                    field_key = f"{base_key}-{counter}"
                    counter += 1
                used_keys.add(field_key)
                CustomWGSPipelineField.objects.create(
                    pipeline=pipeline,
                    field_key=field_key,
                    display_label=label,
                    source_column=column,
                    data_type=data_type,
                    show_in_table=True,
                    show_in_detail=True,
                    show_in_export=True,
                    sort_order=order,
                )
                created_fields += 1

            if created_fields:
                messages.success(request, f"{pipeline.name} pipeline created with {created_fields} mapped field(s).")
                return redirect("custom_pipeline_upload", slug=pipeline.slug)
            messages.success(request, f"{pipeline.name} pipeline created. Add its upload fields next.")
            return redirect("custom_pipeline_fields", slug=pipeline.slug)
        preview_columns = build_custom_pipeline_column_preview_from_post(request.POST)
        if preview_columns:
            return render(
                request,
                "wgs_app/custom_pipeline_form.html",
                {
                    "form": form,
                    "pipeline": None,
                    "preview_columns": preview_columns,
                    "accession_guess": request.POST.get("accession_column", ""),
                    "sample_guess": request.POST.get("sample_name_column", ""),
                    "date_guess": request.POST.get("date_column", ""),
                },
            )
    else:
        form = CustomWGSPipelineForm()
    return render(request, "wgs_app/custom_pipeline_form.html", {"form": form, "pipeline": None})


@login_required
@role_required(ROLE_ADMIN)
def custom_pipeline_edit(request, slug):
    pipeline = get_object_or_404(CustomWGSPipeline, slug=slug)
    if request.method == "POST":
        form = CustomWGSPipelineForm(request.POST, instance=pipeline)
        if form.is_valid():
            form.save()
            messages.success(request, f"{pipeline.name} pipeline updated.")
            return redirect("custom_pipeline_list")
    else:
        form = CustomWGSPipelineForm(instance=pipeline)
    return render(request, "wgs_app/custom_pipeline_form.html", {"form": form, "pipeline": pipeline})


@login_required
@role_required(ROLE_ADMIN)
@require_POST
def custom_pipeline_deactivate(request, slug):
    pipeline = get_object_or_404(CustomWGSPipeline, slug=slug)
    pipeline.is_active = False
    pipeline.show_in_upload_center = False
    pipeline.show_in_overview = False
    pipeline.save(update_fields=[
        "is_active",
        "show_in_upload_center",
        "show_in_overview",
        "updated_at",
    ])
    messages.success(
        request,
        f"{pipeline.name} was removed from active WGS pipeline options. Existing records were kept.",
    )
    next_url = request.POST.get("next") or ""
    if next_url:
        return redirect(next_url)
    return redirect("custom_pipeline_list")


@login_required
@role_required(ROLE_ADMIN)
@require_POST
def custom_pipeline_delete(request, slug):
    pipeline = get_object_or_404(CustomWGSPipeline, slug=slug)
    pipeline_name = pipeline.name
    record_count = pipeline.records.count()
    field_count = pipeline.fields.count()
    batch_count = pipeline.upload_batches.count()
    pipeline.delete()
    messages.success(
        request,
        f"{pipeline_name} pipeline was deleted with {record_count} record(s), {field_count} field(s), and {batch_count} upload batch(es).",
    )
    next_url = request.POST.get("next") or ""
    if next_url:
        return redirect(next_url)
    return redirect("custom_pipeline_list")


@login_required
@role_required(ROLE_ADMIN)
def custom_pipeline_fields(request, slug):
    pipeline = get_object_or_404(CustomWGSPipeline, slug=slug)
    if request.method == "POST":
        form = CustomWGSPipelineFieldForm(request.POST)
        if form.is_valid():
            field = form.save(commit=False)
            field.pipeline = pipeline
            if not field.field_key:
                field.field_key = slugify(field.display_label)
            field.save()
            messages.success(request, f"{field.display_label} field added.")
            return redirect("custom_pipeline_fields", slug=pipeline.slug)
    else:
        form = CustomWGSPipelineFieldForm()
    return render(
        request,
        "wgs_app/custom_pipeline_fields.html",
        {"pipeline": pipeline, "form": form, "fields": pipeline.fields.all()},
    )


@login_required
@role_required(ROLE_ADMIN)
def custom_pipeline_field_edit(request, slug, field_id):
    pipeline = get_object_or_404(CustomWGSPipeline, slug=slug)
    field = get_object_or_404(CustomWGSPipelineField, pk=field_id, pipeline=pipeline)
    record_count = pipeline.records.count()
    original_data_type = field.data_type

    if request.method == "POST":
        form = CustomWGSPipelineFieldForm(request.POST, instance=field)
        if form.is_valid():
            updated_field = form.save(commit=False)
            if not updated_field.field_key:
                updated_field.field_key = slugify(updated_field.display_label)
            updated_field.save()
            messages.success(request, f"{updated_field.display_label} field updated.")
            if record_count and original_data_type != updated_field.data_type:
                messages.warning(
                    request,
                    "Field type changed. Existing uploaded records were kept as-is; the new type applies to future uploads.",
                )
            return redirect("custom_pipeline_fields", slug=pipeline.slug)
    else:
        form = CustomWGSPipelineFieldForm(instance=field)

    return render(
        request,
        "wgs_app/custom_pipeline_field_form.html",
        {
            "pipeline": pipeline,
            "field": field,
            "form": form,
            "record_count": record_count,
        },
    )


@login_required
@role_required(ROLE_ADMIN)
@require_POST
def custom_pipeline_field_delete(request, slug, field_id):
    pipeline = get_object_or_404(CustomWGSPipeline, slug=slug)
    field = get_object_or_404(CustomWGSPipelineField, pk=field_id, pipeline=pipeline)
    label = field.display_label
    field.delete()
    messages.success(request, f"{label} field removed.")
    return redirect("custom_pipeline_fields", slug=pipeline.slug)


@login_required
@role_required(ROLE_ADMIN)
def custom_pipeline_upload(request, slug):
    pipeline = get_object_or_404(CustomWGSPipeline, slug=slug, is_active=True)
    fields = list(pipeline.fields.all())
    if not fields:
        messages.error(request, "Add at least one field mapping before uploading this pipeline.")
        return redirect("custom_pipeline_fields", slug=pipeline.slug)

    if request.method == "POST":
        form = CustomPipelineUploadForm(request.POST, request.FILES)
        if form.is_valid():
            upload_file = form.cleaned_data["file"]
            replace_existing = form.cleaned_data["replace_existing"]
            try:
                df = read_uploaded_file(upload_file, sheet_name=pipeline.sheet_name or None)
                df = df.where(pd.notnull(df), "")
                rows = df.to_dict("records")
            except Exception as exc:
                messages.error(request, f"Upload could not be read: {exc}")
                return redirect("custom_pipeline_upload", slug=pipeline.slug)

            created_count = 0
            skipped_count = 0
            matched_count = 0
            unmatched_count = 0
            warnings = []
            replaced_accessions = set()
            records_to_create = []

            with transaction.atomic():
                batch = CustomWGSPipelineUploadBatch.objects.create(
                    pipeline=pipeline,
                    file_name=upload_file.name,
                    sheet_name=pipeline.sheet_name,
                    row_count=len(rows),
                    uploaded_by=request.user,
                )
                for index, row in enumerate(rows, start=2):
                    accession = str(custom_row_value(row, [pipeline.accession_column])).strip()
                    if not accession:
                        skipped_count += 1
                        warnings.append(f"Row {index}: missing accession.")
                        continue

                    values = {}
                    row_valid = True
                    for field in fields:
                        value = custom_row_value(row, field.upload_column_options())
                        if value == "" and field.default_value:
                            value = field.default_value
                        if value == "" and field.required:
                            row_valid = False
                            warnings.append(f"Row {index}: missing required field {field.display_label}.")
                        values[field.field_key] = coerce_custom_pipeline_value(value, field.data_type)
                    if not row_valid:
                        skipped_count += 1
                        continue

                    sample_name = ""
                    if pipeline.sample_name_column:
                        sample_name = str(custom_row_value(row, [pipeline.sample_name_column])).strip()

                    final_match, match_source = find_custom_pipeline_match(accession)
                    match_status = "matched" if match_source else "unmatched"
                    if match_status == "matched":
                        matched_count += 1
                        ensure_custom_wgs_project(final_match)
                    else:
                        unmatched_count += 1

                    stored_accession = (
                        final_match.f_AccessionNo
                        if final_match
                        else custom_record_primary_accession({"accession": accession}) or accession
                    )

                    if replace_existing:
                        replacement_keys = custom_pipeline_accession_variants(accession)
                        replacement_keys.extend(custom_pipeline_accession_variants(stored_accession))
                        replacement_filter = Q()
                        for replacement_key in replacement_keys:
                            replacement_key = str(replacement_key or "").strip()
                            if not replacement_key:
                                continue
                            normalized_key = replacement_key.upper()
                            if normalized_key in replaced_accessions:
                                continue
                            replacement_filter |= Q(accession__iexact=replacement_key)
                            replaced_accessions.add(normalized_key)
                        if replacement_filter:
                            CustomWGSPipelineRecord.objects.filter(
                                replacement_filter,
                                pipeline=pipeline,
                            ).delete()

                    records_to_create.append(
                        CustomWGSPipelineRecord(
                            pipeline=pipeline,
                            upload_batch=batch,
                            accession=stored_accession,
                            sample_name=sample_name,
                            matched_final_data=final_match,
                            match_status=match_status,
                            match_source=match_source,
                            values_json=values,
                            raw_row_json={str(key): json_safe_value(value) for key, value in row.items()},
                            uploaded_by=request.user,
                        )
                    )
                    created_count += 1

                if records_to_create:
                    CustomWGSPipelineRecord.objects.bulk_create(records_to_create, batch_size=1000)

                batch.created_count = created_count
                batch.skipped_count = skipped_count
                batch.matched_count = matched_count
                batch.unmatched_count = unmatched_count
                batch.status = "completed_with_warnings" if warnings else "completed"
                batch.error_log = "\n".join(warnings[:100])
                batch.save(update_fields=[
                    "created_count", "skipped_count", "matched_count",
                    "unmatched_count", "status", "error_log",
                ])

            messages.success(
                request,
                f"{pipeline.name} uploaded: {created_count} row(s), {matched_count} matched, {unmatched_count} unmatched.",
            )
            if warnings:
                messages.warning(request, f"{len(warnings)} row warning(s). Check the upload batch details if needed.")
            return redirect("custom_pipeline_records", slug=pipeline.slug)
    else:
        form = CustomPipelineUploadForm()

    return render(
        request,
        "wgs_app/custom_pipeline_upload.html",
        {"pipeline": pipeline, "form": form, "fields": fields},
    )


@login_required
@role_required(ROLE_ADMIN)
def custom_pipeline_records(request, slug):
    pipeline = get_object_or_404(CustomWGSPipeline, slug=slug)
    fields = list(pipeline.fields.filter(show_in_table=True))
    q = request.GET.get("q", "").strip()
    match_status = request.GET.get("match_status", "").strip()

    records = pipeline.records.select_related("upload_batch", "matched_final_data")
    if q:
        search_filter = Q()
        for term in build_wgs_search_terms(q):
            search_filter |= (
                Q(accession__icontains=term)
                | Q(sample_name__icontains=term)
                | Q(matched_final_data__f_AccessionNo__icontains=term)
            )
        records = records.filter(search_filter)
    if match_status in {"matched", "unmatched", "invalid"}:
        records = records.filter(match_status=match_status)

    filtered_records = records.count()
    paginator = Paginator(records, 50)
    page_obj = paginator.get_page(request.GET.get("page"))
    for record in page_obj.object_list:
        record.display_accession = custom_record_display_accession(record)
        record.show_uploaded_accession = (
            (record.accession or "").strip()
            and (record.accession or "").strip() != record.display_accession
        )
    params = request.GET.copy()
    params.pop("page", None)

    return render(
        request,
        "wgs_app/custom_pipeline_records.html",
        {
            "pipeline": pipeline,
            "fields": fields,
            "page_obj": page_obj,
            "q": q,
            "match_status": match_status,
            "preserved_params": params.urlencode(),
            "filtered_records": filtered_records,
        },
    )


@login_required
@role_required(ROLE_ADMIN)
def custom_pipeline_export(request, slug):
    pipeline = get_object_or_404(CustomWGSPipeline, slug=slug)
    fields = list(pipeline.fields.filter(show_in_export=True))
    match_status = request.GET.get("match_status", "").strip()
    records = pipeline.records.all()
    if match_status in {"matched", "unmatched", "invalid"}:
        records = records.filter(match_status=match_status)

    export_rows = []
    for record in records.order_by("accession", "id"):
        display_accession = custom_record_display_accession(record)
        row = {
            "pipeline": pipeline.name,
            "sequencing_type": pipeline.get_sequencing_type_display(),
            "platform": pipeline.get_platform_display(),
            "accession": display_accession,
            "uploaded_accession": record.accession,
            "sample_name": record.sample_name,
            "match_status": record.match_status,
            "match_source": record.match_source,
            "uploaded_at": record.uploaded_at.replace(tzinfo=None) if record.uploaded_at else "",
        }
        for field in fields:
            row[field.display_label] = record.values_json.get(field.field_key, "")
        export_rows.append(row)

    output = io.BytesIO()
    pd.DataFrame(export_rows).to_excel(output, index=False, sheet_name="custom_wgs")
    output.seek(0)
    filename = f"{pipeline.slug}_wgs_records.xlsx"
    response = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
def export_builtin_wgs_pipeline(request, pipeline_key):
    export_map = {
        "sample_information": (SampleInformation, "Sample_Information"),
        "bactscout": (BactScout, "BactScout"),
        "gtdbtk": (GtdbTk, "GTDB_Tk"),
        "gambit": (Gambit, "Gambit"),
        "mlst": (Mlst, "MLST"),
        "checkm2": (Checkm2, "CheckM2"),
        "assembly": (AssemblyScan, "Assembly_Scan"),
        "amrfinder": (Amrfinderplus, "AMRFinderPlus"),
    }
    if pipeline_key not in export_map:
        messages.error(request, "Unknown WGS pipeline export.")
        return redirect("upload_wgs_view")

    model, label = export_map[pipeline_key]
    if pipeline_key == "bactscout":
        export_fields = ["BactScout_Accession", *BactScout.UPLOAD_FIELDS, "Date_uploaded_bs"]
        rows = list(model.objects.all().values(*export_fields))
    elif pipeline_key == "gambit":
        export_fields = ["Gambit_Accession", *Gambit.UPLOAD_FIELDS, "Date_uploaded_g"]
        rows = list(model.objects.all().values(*export_fields))
    elif pipeline_key == "mlst":
        export_fields = ["Mlst_Accession", *Mlst.UPLOAD_FIELDS, "Date_uploaded_m"]
        rows = list(model.objects.all().values(*export_fields))
    elif pipeline_key == "checkm2":
        export_fields = ["Checkm2_Accession", *Checkm2.UPLOAD_FIELDS, "Date_uploaded_c"]
        rows = list(model.objects.all().values(*export_fields))
    elif pipeline_key == "amrfinder":
        export_fields = ["Amrfinder_Accession", *Amrfinderplus.UPLOAD_FIELDS, "Date_uploaded_am"]
        rows = list(model.objects.all().values(*export_fields))
    else:
        rows = list(model.objects.all().values())
    df = pd.DataFrame.from_records(rows)
    if df.empty:
        df = pd.DataFrame([{"message": f"No {label} records found."}])

    for column in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[column]):
            df[column] = pd.to_datetime(df[column], errors="coerce").dt.tz_localize(None)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=label[:31])
    output.seek(0)

    filename = f"{label}_{pd.Timestamp.now().date()}.xlsx"
    response = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
def show_wgs_projects(request):
    q = (request.GET.get("q") or "").strip()
    projects = WGS_Project.objects.select_related("Ref_Accession").order_by("-id")
    total_records = projects.count()
    projects = apply_wgs_related_accession_search(projects, q, "Ref_Accession__f_AccessionNo")
    filtered_records = projects.count()

    paginator = Paginator(projects, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    context = {
        "page_obj": page_obj,
        "q": q,
        "total_records": total_records,
        "filtered_records": filtered_records,
    }
    return render(request, 'wgs_app/show_wgs_proj.html', context)


@login_required
@role_required(*WGS_WRITE_ALLOWED_ROLES)
@require_POST
def delete_wgs(request, pk):
    wgs_item = get_object_or_404(WGS_Project, pk=pk)

    if request.method == "POST":
        wgs_item.delete()
        messages.success(request, f"Record {wgs_item.Ref_Accession} deleted successfully!")
        return redirect('show_wgs_projects')  # <-- Correct URL name

    messages.error(request, "Invalid request for deletion.")
    return redirect('show_wgs_projects')  # <-- Correct URL name




#########   Gambit
@login_required
@role_required(*WGS_WRITE_ALLOWED_ROLES)
def upload_gambit(request):
    form = WGSProjectForm()
    gambit_form = GambitUploadForm()
    editing = False  

    if request.method == "POST" and request.FILES.get("GambitFile"):
        overwrite = wgs_upload_overwrite_enabled(request)
        gambit_form = GambitUploadForm(request.POST, request.FILES)
        if gambit_form.is_valid():
            try:
                upload = gambit_form.save()
                df = read_uploaded_file(upload.GambitFile, sheet_name=["gambit", "Sheet1"])
                df.columns = [
                    str(column).strip().lower().replace(".", "_")
                    for column in df.columns
                ]
            except Exception as e:
                messages.error(request, f"Error processing FASTQ file: {e}")
                return render(request, "wgs_app/Add_wgs.html", {
                    "form": form,
                    "gambit_form": gambit_form,
                    "mlst_form": MlstUploadForm(),
                    "checkm2_form": Checkm2UploadForm(),
                    "amrfinder_form": AmrUploadForm(),
                    "assembly_form": AssemblyUploadForm(),
                    "demogs_form": DemogsDataUploadForm(),
                    "antibiotic_form": FinalAntibioticUploadForm(),
                    "sampleinfo_form" : SampleInfoUploadForm(),
                    "bactscout_form": BactScoutUploadForm(),
                    "gtdbtk_form": GtdbTkUploadForm(),
                    "raw_antibiotic_form": RawAntibioticUploadForm(),
                    "editing": editing,
                    "visible_builtin_upload_keys": visible_builtin_wgs_keys("upload"),
                })

            site_codes = {
                str(code or "").strip().upper()
                for code in SiteData.objects.values_list("SiteCode", flat=True)
                if str(code or "").strip()
            }
            # helper to build accession
            def format_gambit_accession(raw_name: str, site_codes: set) -> str:
                """
                Parse Gambit sample names like 18ARS-BGH0055-20220426A
                into the app accession format 18ARS_BGH0055.
                """
                if not raw_name:
                    return ""

                name = os.path.splitext(os.path.basename(str(raw_name).strip()))[0].upper()
                name = re.sub(r"\s+", "", name)

                # Reject invalid patterns
                if "UTPR" in name or "UTPN" in name or "BL" in name:
                    return ""
                direct_match = re.search(
                    r"(?P<prefix>\d{2,4}ARS)[-_]?(?P<site>[A-Z]{2,6})[-_]?(?P<number>\d{3,6})",
                    name,
                )
                if direct_match:
                    return (
                        f"{direct_match.group('prefix')}_"
                        f"{direct_match.group('site')}{direct_match.group('number')}"
                    )
                
                # ✅ Must contain 'ARS' - if not, return empty immediately
                if "ARS" not in name:
                    return ""

                # ✅ Find if any valid SiteCode from DB exists in the sample name
                # Use word boundaries to match complete site codes only
                valid_code = None
                for code in site_codes:
                    # Look for the site code with word boundaries (hyphens, start/end of string)
                    # Pattern: site code must be followed by a hyphen and digits
                    pattern = rf"[-_]?{re.escape(code)}[-_]?\d+"
                    if re.search(pattern, name):
                        valid_code = code
                        break

                # No valid site code found → blank
                if not valid_code:
                    return ""

                # ✅ Extract prefix that includes ARS (e.g., "18ARS")
                prefix_match = re.search(r"(\d{2,4}ARS)", name)
                prefix = prefix_match.group(1) if prefix_match else "ARS"

                # ✅ Extract numeric digits after the site code (e.g., 0055)
                num_match = re.search(rf"{re.escape(valid_code)}[-_]?(\d+)", name)
                digits = num_match.group(1) if num_match else ""

                return f"{prefix}_{valid_code}{digits}" if digits else ""


            rows = []
            skipped_count = 0
            for row in df.to_dict("records"):
                sample_name = (
                    upload_text_or_none(row.get("sample"))
                    or upload_text_or_none(row.get("query"))
                    or ""
                )
                gambit_accession = resolve_wgs_accession_from_sample_name(sample_name, site_codes)

                if not sample_name:
                    skipped_count += 1
                    continue

                rows.append({
                        "sample": sample_name,
                        "Gambit_Accession": gambit_accession,
                        "predicted_name": upload_text_or_none(row.get("predicted_name")),
                        "predicted_rank": upload_text_or_none(row.get("predicted_rank")),
                        "predicted_ncbi_id": upload_text_or_none(row.get("predicted_ncbi_id")),
                        "predicted_threshold": upload_text_or_none(row.get("predicted_threshold")),
                        "closest_distance": upload_text_or_none(row.get("closest_distance")),
                        "closest_description": upload_text_or_none(row.get("closest_description")),
                        "next_name": upload_text_or_none(row.get("next_name")),
                        "next_rank": upload_text_or_none(row.get("next_rank")),
                        "next_ncbi_id": upload_text_or_none(row.get("next_ncbi_id")),
                        "next_threshold": upload_text_or_none(row.get("next_threshold")),
                })

            result = bulk_save_wgs_module_records(
                Gambit,
                rows,
                accession_field="Gambit_Accession",
                project_field="gambit_project",
                project_accession_field="WGS_Gambit_Acc",
                summary_field="WGS_GambitSummary",
                key_fields=("Gambit_Accession", "sample"),
                value_fields=Gambit.UPLOAD_FIELDS,
                overwrite=overwrite,
            )
            created_count = result["created"]
            updated_count = result["updated"]
            skipped_count += result["skipped"]

            if created_count or updated_count:
                messages.success(
                    request,
                    f"Gambit upload complete: {created_count} created, "
                    f"{updated_count} updated, {skipped_count} skipped."
                )
            else:
                messages.warning(
                    request,
                    "No Gambit rows were uploaded. Please check that the file has a "
                    "'sample' or 'query' column with accession-like sample names."
                )
            return redirect("show_gambit")
        messages.error(request, f"Gambit file upload failed: {gambit_form.errors.as_text()}")

    return render(request, "wgs_app/Add_wgs.html", {
        "form": form,
        "gambit_form": gambit_form,
        "mlst_form": MlstUploadForm(),
        "checkm2_form": Checkm2UploadForm(),
        "assembly_form": AssemblyUploadForm(),
        "amrfinder_form": AmrUploadForm(),
        "demogs_form": DemogsDataUploadForm(),
        "antibiotic_form": FinalAntibioticUploadForm(),
        "sampleinfo_form" : SampleInfoUploadForm(),
        "bactscout_form": BactScoutUploadForm(),
        "gtdbtk_form": GtdbTkUploadForm(),
        "raw_antibiotic_form": RawAntibioticUploadForm(),
        "editing": editing,
        "visible_builtin_upload_keys": visible_builtin_wgs_keys("upload"),
    })




@login_required
def show_gambit(request):
    q = (request.GET.get("q") or "").strip()
    gambit_summaries = Gambit.objects.all().order_by('-Date_uploaded_g')
    gambit_summaries = apply_wgs_text_search(gambit_summaries, q)
    upload_dates = (
        Gambit.objects.exclude(Date_uploaded_g__isnull=True)
        .values_list('Date_uploaded_g', flat=True)
        .distinct()
        .order_by('-Date_uploaded_g')
    )

    total_records = Gambit.objects.count()
    filtered_records = gambit_summaries.count()
     # Paginate the queryset to display 20 records per page
    paginator = Paginator(gambit_summaries, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, "wgs_app/show_gambit.html", {
        "page_obj": page_obj,
        "upload_dates": upload_dates,
        "total_records": total_records,
        "filtered_records": filtered_records,
        "q": q,
        "gambit_fields": Gambit.FIELD_LABELS,
        "gambit_colspan": len(Gambit.FIELD_LABELS) + 3,
    })




@login_required
@role_required(*WGS_WRITE_ALLOWED_ROLES)
@require_POST
def delete_gambit(request, pk):
    gambit_item = get_object_or_404(Gambit, pk=pk)

    if request.method == "POST":
        # Before deleting, clear related field in WGS_Project
        WGS_Project.objects.filter(WGS_Gambit_Acc=gambit_item.Gambit_Accession).update(
            WGS_Gambit_Acc="",
            WGS_GambitSummary=False
        )

        gambit_item.delete()
        messages.success(request, f"Record {gambit_item.sample} deleted successfully!")
        return redirect('show_gambit')

    messages.error(request, "Invalid request for deletion.")
    return redirect('show_gambit')


# @login_required
# def delete_all_gambit(request):
#     Gambit.objects.all().delete()
#     messages.success(request, "Gambit Records have been deleted successfully.")
#     return redirect('show_gambit')  # Redirect to the table view



@login_required
@role_required(*WGS_WRITE_ALLOWED_ROLES)
@require_POST
def delete_all_gambit(request):
    """
    Safely delete all Gambit records but preserve WGS_Project links
    for other WGS data types (BactScout, MLST, CheckM2, Assembly, AMRFinder, etc.).
    """
    # Step 1: Clear only Gambit fields in existing WGS_Project records
    updated_count = WGS_Project.objects.filter(
        WGS_Gambit_Acc__isnull=False
    ).exclude(WGS_Gambit_Acc="").update(
        WGS_Gambit_Acc="",
        WGS_GambitSummary=False
    )

    # Step 2: Delete all Gambit summary data
    Gambit.objects.all().delete()

    # Step 3: Display success message
    messages.success(
        request,
        f"All Gambit records deleted successfully, and {updated_count} WGS Project(s) were unlinked from Gambit data."
    )

    return redirect("show_gambit")




@login_required
@role_required(*WGS_WRITE_ALLOWED_ROLES)
@require_POST
def delete_gambit_by_date(request):
    if request.method == "POST":
        upload_date_str = request.POST.get("upload_date")
        print("🕒 Received upload_date_str:", upload_date_str)

        if not upload_date_str:
            messages.error(request, "Please select an upload date to delete.")
            return redirect("show_gambit")

        # Use Django’s date parser
        upload_date = parse_date(upload_date_str)

        if not upload_date:
            messages.error(request, f"Invalid date format: {upload_date_str}")
            return redirect("show_gambit")

        deleted_count, _ = Gambit.objects.filter(Date_uploaded_g=upload_date).delete()
        messages.success(request, f"✅ Deleted {deleted_count} Gambit records uploaded on {upload_date}.")
        return redirect("show_gambit")

    messages.error(request, "Invalid request method.")
    return redirect("show_gambit")


#########   MLST
@login_required
@role_required(*WGS_WRITE_ALLOWED_ROLES)
def upload_mlst(request):
    form = WGSProjectForm()
    mlst_form = MlstUploadForm()
    editing = False  

    if request.method == "POST" and request.FILES.get("Mlstfile"):
        overwrite = wgs_upload_overwrite_enabled(request)
        mlst_form = MlstUploadForm(request.POST, request.FILES)
        try:
            upload = mlst_form.save()
            df = read_uploaded_file(upload.Mlstfile, sheet_name=["mlst_new", "mlst"])
            df = normalize_mlst_upload_dataframe(df)
        except Exception as e:
            messages.error(request, f"Error processing MLST file: {e}")
            return render(request, "wgs_app/Add_wgs.html", {
                "form": form,
                "gambit_form": GambitUploadForm(),
                "mlst_form": mlst_form,
                "checkm2_form": Checkm2UploadForm(),
                "amrfinder_form": AmrUploadForm(),
                "assembly_form": AssemblyUploadForm(),
                "demogs_form": DemogsDataUploadForm(),
                "antibiotic_form": FinalAntibioticUploadForm(),
                "sampleinfo_form" : SampleInfoUploadForm(),
                "bactscout_form": BactScoutUploadForm(),
                "gtdbtk_form": GtdbTkUploadForm(),
                "raw_antibiotic_form": RawAntibioticUploadForm(),
                "editing": editing,
                "visible_builtin_upload_keys": visible_builtin_wgs_keys("upload"),
            })

        # ✅ Load all valid site codes from the SiteData table
        site_codes = set(SiteData.objects.values_list("SiteCode", flat=True))

        # === Helper: build accession from file name ===
        def format_mlst_accession(raw_name: str, site_codes: set) -> str:
            if not raw_name:
                return ""

            base_noext = os.path.splitext(os.path.basename(raw_name))[0].strip()

            # Must contain 'ARS' to be valid
            if "ARS" not in base_noext:
                return ""

            parts = re.split(r"[-_]", base_noext)
            if not parts:
                return ""

            prefix = parts[0]

            # Look for SITE#### pattern where SITE is valid
            for part in parts[1:]:
                match = re.match(r"^([A-Za-z]{2,6})(\d+)$", part)
                if match:
                    letters, digits = match.group(1).upper(), match.group(2)
                    if letters in site_codes:
                        return f"{prefix}_{letters}{digits}"

            # 2Look for a separate valid site code, then grab digits from next part
            for i in range(1, len(parts)):
                part = parts[i]
                if part.upper() in site_codes:
                    letters = part.upper()
                    digits = ""

                    if i + 1 < len(parts):
                        next_part = parts[i + 1]
                        next_match = re.match(r"^([A-Za-z]{2,6})(\d+)$", next_part)
                        if next_match:
                            digits = next_match.group(2)
                        else:
                            digit_match = re.search(r"(\d+)", next_part)
                            if digit_match:
                                digits = digit_match.group(1)

                    # fallback — digits inside current part
                    if not digits:
                        digit_match2 = re.search(r"(\d+)", part)
                        if digit_match2:
                            digits = digit_match2.group(1)

                    return f"{prefix}_{letters}{digits}" if digits else f"{prefix}_{letters}"

            return ""

        print("✅ Total rows in DataFrame:", len(df))

        rows = []
        skipped_count = 0
        for row in df.to_dict("records"):
            full_path = str(row.get("FILE", "")).strip()
            if not full_path:
                skipped_count += 1
                continue
            mlst_accession = resolve_wgs_accession_from_sample_name(full_path, site_codes)
            scheme = row.get("SCHEME", "")

            rows.append({
                    "Mlst_Accession": mlst_accession,
                    "file": full_path,
                    "scheme": scheme,
                    "st": row.get("ST", ""),
                    "status": row.get("STATUS", ""),
                    "score": row.get("SCORE", ""),
                    "alleles": row.get("ALLELES", ""),
            })

        result = bulk_save_wgs_module_records(
            Mlst,
            rows,
            accession_field="Mlst_Accession",
            project_field="mlst_project",
            project_accession_field="WGS_Mlst_Acc",
            summary_field="WGS_MlstSummary",
            key_fields=("Mlst_Accession", "file", "scheme"),
            value_fields=Mlst.UPLOAD_FIELDS,
            overwrite=overwrite,
        )

        messages.success(
            request,
            f"MLST upload complete: {result['created']} created, "
            f"{result['updated']} updated, {result['skipped'] + skipped_count} skipped.",
        )
        return redirect("show_mlst")

    # === GET request fallback ===
    return render(request, "wgs_app/Add_wgs.html", {
        "form": form,
        "gambit_form": GambitUploadForm(),
        "mlst_form": mlst_form,
        "checkm2_form": Checkm2UploadForm(),
        "amrfinder_form": AmrUploadForm(),
        "assembly_form": AssemblyUploadForm(),
        "demogs_form": DemogsDataUploadForm(),
        "antibiotic_form": FinalAntibioticUploadForm(),
        "sampleinfo_form" : SampleInfoUploadForm(),
        "bactscout_form": BactScoutUploadForm(),
        "gtdbtk_form": GtdbTkUploadForm(),
        "raw_antibiotic_form": RawAntibioticUploadForm(),
        "editing": editing,
        "visible_builtin_upload_keys": visible_builtin_wgs_keys("upload"),
    })



@login_required
def show_mlst(request):
    q = (request.GET.get("q") or "").strip()
    mlst_summaries = Mlst.objects.all().order_by('-Date_uploaded_m')
    mlst_summaries = apply_wgs_text_search(mlst_summaries, q)
    upload_dates = (
        Mlst.objects.exclude(Date_uploaded_m__isnull=True)
        .values_list('Date_uploaded_m', flat=True)
        .distinct()
        .order_by('-Date_uploaded_m')
    )

    total_records = Mlst.objects.count()
    filtered_records = mlst_summaries.count()
     # Paginate the queryset to display 20 records per page
    paginator = Paginator(mlst_summaries, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, "wgs_app/show_mlst.html", {
        "page_obj": page_obj,
        "upload_dates": upload_dates,
        "total_records": total_records,
        "filtered_records": filtered_records,
        "q": q,
        "mlst_fields": Mlst.FIELD_LABELS,
    })




# @login_required
# def delete_mlst(request, pk):
#     mlst_item = get_object_or_404(Mlst, pk=pk)

#     if request.method == "POST":
#         mlst_item.delete()
#         messages.success(request, f"Record {mlst_item.sample} deleted successfully!")
#         return redirect('show_mlst')  # <-- Correct URL name

#     messages.error(request, "Invalid request for deletion.")
#     return redirect('show_mlst')  # <-- Correct URL name


@login_required
@role_required(*WGS_WRITE_ALLOWED_ROLES)
@require_POST
def delete_mlst(request, pk):
    mlst_item = get_object_or_404(Mlst, pk=pk)

    if request.method == "POST":
        # Before deleting, clear related field in WGS_Project
        WGS_Project.objects.filter(WGS_Mlst_Acc=mlst_item.Mlst_Accession).update(
            WGS_Mlst_Acc="",
            WGS_MlstSummary=False
        )

        mlst_item.delete()
        messages.success(request, f"Record {mlst_item.file} deleted successfully!")
        return redirect('show_mlst')

    messages.error(request, "Invalid request for deletion.")
    return redirect('show_mlst')



# @login_required
# def delete_all_mlst(request):
#     Mlst.objects.all().delete()
#     messages.success(request, "Mlst Records have been deleted successfully.")
#     return redirect('show_mlst')  # Redirect to the table view


@login_required
@role_required(*WGS_WRITE_ALLOWED_ROLES)
@require_POST
def delete_all_mlst(request):
    """
    Safely delete all MLST records but preserve WGS_Project links
    for other WGS data types (BactScout, CheckM2, Assembly, Gambit, AMRFinder, etc.).
    """
    # Step 1: Clear only MLST fields in existing WGS_Project records
    updated_count = WGS_Project.objects.filter(
        WGS_Mlst_Acc__isnull=False
    ).exclude(WGS_Mlst_Acc="").update(
        WGS_Mlst_Acc="",
        WGS_MlstSummary=False
    )

    # Step 2: Delete all MLST summary data
    Mlst.objects.all().delete()

    # Step 3: Display success message
    messages.success(
        request,
        f"All MLST records deleted successfully, and {updated_count} WGS Project(s) were unlinked from MLST data."
    )

    return redirect("show_mlst")




@login_required
@role_required(*WGS_WRITE_ALLOWED_ROLES)
@require_POST
def delete_mlst_by_date(request):
    if request.method == "POST":
        upload_date_str = request.POST.get("upload_date")
        print("🕒 Received upload_date_str:", upload_date_str)

        if not upload_date_str:
            messages.error(request, "Please select an upload date to delete.")
            return redirect("show_mlst")

        # Use Django’s date parser
        upload_date = parse_date(upload_date_str)

        if not upload_date:
            messages.error(request, f"Invalid date format: {upload_date_str}")
            return redirect("show_mlst")

        deleted_count, _ = Mlst.objects.filter(Date_uploaded_m=upload_date).delete()
        messages.success(request, f"✅ Deleted {deleted_count} Mlst records uploaded on {upload_date}.")
        return redirect("show_mlst")

    messages.error(request, "Invalid request method.")
    return redirect("show_mlst")




###################  Checkm2 
@login_required
@role_required(*WGS_WRITE_ALLOWED_ROLES)
def upload_checkm2(request):
    form = WGSProjectForm()
    checkm2_form = Checkm2UploadForm()
    editing = False

    if request.method == "POST" and request.FILES.get("Checkm2file"):
        overwrite = wgs_upload_overwrite_enabled(request)
        checkm2_form = Checkm2UploadForm(request.POST, request.FILES)
        try:
            upload = checkm2_form.save()
            df = read_uploaded_file(upload.Checkm2file, sheet_name="checkm2")
            df.columns = df.columns.str.strip().str.replace(".", "", regex=False)
        except Exception as e:
            messages.error(request, f"Error processing MLST file: {e}")
            return render(request, "wgs_app/Add_wgs.html", {
                "form": form,
                "gambit_form": GambitUploadForm(),
                "mlst_form": MlstUploadForm(),
                "checkm2_form": checkm2_form,
                "amrfinder_form": AmrUploadForm(),
                "assembly_form": AssemblyUploadForm(),
                "demogs_form": DemogsDataUploadForm(),
                "antibiotic_form": FinalAntibioticUploadForm(),
                "sampleinfo_form" : SampleInfoUploadForm(),
                "bactscout_form": BactScoutUploadForm(),
                "gtdbtk_form": GtdbTkUploadForm(),
                "raw_antibiotic_form": RawAntibioticUploadForm(),
                "editing": editing,
                "visible_builtin_upload_keys": visible_builtin_wgs_keys("upload"),
            })

        site_codes = set(SiteData.objects.values_list("SiteCode", flat=True))

        # Helper to build accession
        def format_checkm2_accession(raw_name: str) -> str:
            if not raw_name:
                return ""
            # Take basename and remove extension
            base = os.path.basename(raw_name)
            base_noext = os.path.splitext(base)[0].strip()

            if "ARS" not in base_noext:
                return ""

            parts = re.split(r"[-_]", base_noext)
            if not parts:
                return ""

            prefix = parts[0]  # e.g. "18ARS"

            # Look for a part that matches sitecode+digits (e.g. BGH0055, CVM0162)
            for part in parts[1:]:
                m = re.match(r"^([A-Za-z]{2,6})(\d+)", part)
                if m:
                    letters = m.group(1).upper()
                    digits = m.group(2)
                    if letters in site_codes:
                        return f"{prefix}_{letters}{digits}"

            # If sitecode and digits are separated (rare case)
            for i in range(1, len(parts) - 1):
                if parts[i].upper() in site_codes:
                    letters = parts[i].upper()
                    digits_match = re.search(r"(\d+)", parts[i + 1])
                    if digits_match:
                        return f"{prefix}_{letters}{digits_match.group(1)}"
                    return f"{prefix}_{letters}"

            return ""

        print("Total rows in dataframe:", len(df))

        rows = []
        skipped_count = 0
        for row in df.to_dict("records"):
            sample_name = str(row.get("Name", "")).strip().replace(".fna", "")
            if not sample_name:
                skipped_count += 1
                continue
            checkm2_accession = resolve_wgs_accession_from_sample_name(sample_name, site_codes)

            checkm2_values = {
                "Checkm2_Accession": checkm2_accession,
                "Name": sample_name,
            }
            for field in Checkm2.UPLOAD_FIELDS:
                if field == "Name":
                    continue
                checkm2_values[field] = row.get(field, "")
            rows.append(checkm2_values)

        result = bulk_save_wgs_module_records(
            Checkm2,
            rows,
            accession_field="Checkm2_Accession",
            project_field="checkm2_project",
            project_accession_field="WGS_Checkm2_Acc",
            summary_field="WGS_Checkm2Summary",
            key_fields=("Checkm2_Accession", "Name"),
            value_fields=Checkm2.UPLOAD_FIELDS,
            overwrite=overwrite,
        )

        messages.success(
            request,
            f"CheckM2 upload complete: {result['created']} created, "
            f"{result['updated']} updated, {result['skipped'] + skipped_count} skipped.",
        )
        return redirect("show_checkm2")

    return render(request, "wgs_app/Add_wgs.html", {
        "form": form,
        "gambit_form": GambitUploadForm(),
        "mlst_form": MlstUploadForm(),
        "checkm2_form": checkm2_form,
        "assembly_form": AssemblyUploadForm(),
        "amrfinder_form": AmrUploadForm(),
        "demogs_form": DemogsDataUploadForm(),
        "antibiotic_form": FinalAntibioticUploadForm(),
        "sampleinfo_form" : SampleInfoUploadForm(),
        "bactscout_form": BactScoutUploadForm(),
        "gtdbtk_form": GtdbTkUploadForm(),
        "raw_antibiotic_form": RawAntibioticUploadForm(),
        "editing": editing,
        "visible_builtin_upload_keys": visible_builtin_wgs_keys("upload"),
    })



@login_required
def show_checkm2(request):
    q = (request.GET.get("q") or "").strip()
    checkm2_summaries = Checkm2.objects.all().order_by('-Date_uploaded_c')
    checkm2_summaries = apply_wgs_text_search(checkm2_summaries, q)
    upload_dates = (
        Checkm2.objects.exclude(Date_uploaded_c__isnull=True)
        .values_list('Date_uploaded_c', flat=True)
        .distinct()
        .order_by('-Date_uploaded_c')
    )

    total_records = Checkm2.objects.count()
    filtered_records = checkm2_summaries.count()
     # Paginate the queryset to display 20 records per page
    paginator = Paginator(checkm2_summaries, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, "wgs_app/show_checkm2.html", {
        "page_obj": page_obj,
        "upload_dates": upload_dates,
        "total_records": total_records,
        "filtered_records": filtered_records,
        "q": q,
        "checkm2_fields": Checkm2.FIELD_LABELS,
    })




# @login_required
# def delete_checkm2(request, pk):
#     checkm2_item = get_object_or_404(Checkm2, pk=pk)

#     if request.method == "POST":
#         checkm2_item.delete()
#         messages.success(request, f"Record {checkm2_item.Name} deleted successfully!")
#         return redirect('show_checkm2')  # <-- Correct URL name

#     messages.error(request, "Invalid request for deletion.")
#     return redirect('show_checkm2')  # <-- Correct URL name



@login_required
@role_required(*WGS_WRITE_ALLOWED_ROLES)
@require_POST
def delete_checkm2(request, pk):
    checkm2_item = get_object_or_404(Checkm2, pk=pk)

    if request.method == "POST":
        # Before deleting, clear related field in WGS_Project
        WGS_Project.objects.filter(WGS_Checkm2_Acc=checkm2_item.Checkm2_Accession).update(
            WGS_Checkm2_Acc="",
            WGS_Checkm2Summary=False
        )

        checkm2_item.delete()
        messages.success(request, f"Record {checkm2_item.Name} deleted successfully!")
        return redirect('show_checkm2')

    messages.error(request, "Invalid request for deletion.")
    return redirect('show_checkm2')




# @login_required
# def delete_all_checkm2(request):
#     Checkm2.objects.all().delete()
#     messages.success(request, "Checkm2 Records have been deleted successfully.")
#     return redirect('show_checkm2')  # Redirect to the table view


@login_required
@role_required(*WGS_WRITE_ALLOWED_ROLES)
@require_POST
def delete_all_checkm2(request):
    """
    Safely delete all CheckM2 records but preserve WGS_Project links
    for other WGS data types (BactScout, MLST, Assembly, Gambit, AMRFinder, etc.).
    """
    # Step 1: Clear only CheckM2 fields in existing WGS_Project records
    updated_count = WGS_Project.objects.filter(
        WGS_Checkm2_Acc__isnull=False
    ).exclude(WGS_Checkm2_Acc="").update(
        WGS_Checkm2_Acc="",
        WGS_Checkm2Summary=False
    )

    # Step 2: Delete all CheckM2 summary data
    Checkm2.objects.all().delete()

    # Step 3: Display success message
    messages.success(
        request,
        f"All CheckM2 records deleted successfully, and {updated_count} WGS Project(s) were unlinked from CheckM2 data."
    )

    return redirect("show_checkm2")




@login_required
@role_required(*WGS_WRITE_ALLOWED_ROLES)
@require_POST
def delete_checkm2_by_date(request):
    if request.method == "POST":
        upload_date_str = request.POST.get("upload_date")
        print("🕒 Received upload_date_str:", upload_date_str)

        if not upload_date_str:
            messages.error(request, "Please select an upload date to delete.")
            return redirect("show_checkm2")

        # Use Django’s date parser
        upload_date = parse_date(upload_date_str)

        if not upload_date:
            messages.error(request, f"Invalid date format: {upload_date_str}")
            return redirect("show_checkm2")

        deleted_count, _ = Checkm2.objects.filter(Date_uploaded_c=upload_date).delete()
        messages.success(request, f"✅ Deleted {deleted_count} Checkm2 records uploaded on {upload_date}.")
        return redirect("show_checkm2")

    messages.error(request, "Invalid request method.")
    return redirect("show_checkm2")




###################  Assembly Scan
@login_required
@role_required(*WGS_WRITE_ALLOWED_ROLES)
def upload_assembly(request):
    form = WGSProjectForm()
    assembly_form = AssemblyUploadForm()
    editing = False

    if request.method == "POST" and request.FILES.get("Assemblyfile"):
        overwrite = wgs_upload_overwrite_enabled(request)
        assembly_form = AssemblyUploadForm(request.POST, request.FILES)
        try:
            upload = assembly_form.save()
            df = read_uploaded_file(upload.Assemblyfile, sheet_name="assembly-scan")
            df.columns = df.columns.str.strip().str.replace(".", "", regex=False)
        except Exception as e:
            messages.error(request, f"Error processing Assembly file: {e}")
            return render(request, "wgs_app/Add_wgs.html", {
                "form": form,
                "gambit_form": GambitUploadForm(),
                "mlst_form": MlstUploadForm(),
                "checkm2_form": Checkm2UploadForm(),
                "amrfinder_form": AmrUploadForm(),
                "assembly_form": assembly_form,
                "demogs_form": DemogsDataUploadForm(),
                "antibiotic_form": FinalAntibioticUploadForm(),
                "sampleinfo_form" : SampleInfoUploadForm(),
                "bactscout_form": BactScoutUploadForm(),
                "gtdbtk_form": GtdbTkUploadForm(),
                "raw_antibiotic_form": RawAntibioticUploadForm(),
                "editing": editing,
                "visible_builtin_upload_keys": visible_builtin_wgs_keys("upload"),
            })

        site_codes = set(SiteData.objects.values_list("SiteCode", flat=True))

        # Helper to build accession
        def format_assembly_accession(raw_name: str) -> str:
            if not raw_name:
                return ""
            # Take basename and remove extension
            base = os.path.basename(raw_name)
            base_noext = os.path.splitext(base)[0].strip()

            if "ARS" not in base_noext:
                return ""

            parts = re.split(r"[-_]", base_noext)
            if not parts:
                return ""

            prefix = parts[0]  # e.g. "18ARS"

            # Look for a part that matches sitecode+digits (e.g. BGH0055, CVM0162)
            for part in parts[1:]:
                m = re.match(r"^([A-Za-z]{2,6})(\d+)", part)
                if m:
                    letters = m.group(1).upper()
                    digits = m.group(2)
                    if letters in site_codes:
                        return f"{prefix}_{letters}{digits}"

            # If sitecode and digits are separated (rare case)
            for i in range(1, len(parts) - 1):
                if parts[i].upper() in site_codes:
                    letters = parts[i].upper()
                    digits_match = re.search(r"(\d+)", parts[i + 1])
                    if digits_match:
                        return f"{prefix}_{letters}{digits_match.group(1)}"
                    return f"{prefix}_{letters}"

            return ""

        assembly_fields = (
            "total_contig",
            "total_contig_length",
            "max_contig_length",
            "mean_contig_length",
            "median_contig_length",
            "min_contig_length",
            "n50_contig_length",
            "l50_contig_count",
            "num_contig_non_acgtn",
            "contig_percent_a",
            "contig_percent_c",
            "contig_percent_g",
            "contig_percent_t",
            "contig_percent_n",
            "contig_non_acgtn",
            "contigs_greater_1m",
            "contigs_greater_100k",
            "contigs_greater_10k",
            "contigs_greater_1k",
            "percent_contigs_greater_1m",
            "percent_contigs_greater_100k",
            "percent_contigs_greater_10k",
            "percent_contigs_greater_1k",
        )
        rows = []
        skipped_count = 0
        for row in df.to_dict("records"):
            sample_name = str(row.get("sample", "")).strip()
            if not sample_name:
                skipped_count += 1
                continue
            assembly_accession = resolve_wgs_accession_from_sample_name(sample_name, site_codes)

            rows.append({
                "Assembly_Accession": assembly_accession,
                "sample": sample_name,
                **{field: row.get(field, "") for field in assembly_fields},
            })

        result = bulk_save_wgs_module_records(
            AssemblyScan,
            rows,
            accession_field="Assembly_Accession",
            project_field="assembly_project",
            project_accession_field="WGS_Assembly_Acc",
            summary_field="WGS_AssemblySummary",
            key_fields=("Assembly_Accession", "sample"),
            value_fields=("sample", *assembly_fields),
            overwrite=overwrite,
        )

        messages.success(
            request,
            f"AssemblyScan upload complete: {result['created']} created, "
            f"{result['updated']} updated, {result['skipped'] + skipped_count} skipped.",
        )
        return redirect("show_assembly")

    return render(request, "wgs_app/Add_wgs.html", {
        "form": form,
        "gambit_form": GambitUploadForm(),
        "mlst_form": MlstUploadForm(),
        "checkm2_form": Checkm2UploadForm(),
        "amrfinder_form": AmrUploadForm(),
        "assembly_form": assembly_form,
        "demogs_form": DemogsDataUploadForm(),
        "antibiotic_form": FinalAntibioticUploadForm(),
        "sampleinfo_form" : SampleInfoUploadForm(),
        "bactscout_form": BactScoutUploadForm(),
        "gtdbtk_form": GtdbTkUploadForm(),
        "raw_antibiotic_form": RawAntibioticUploadForm(),
        "editing": editing,
        "visible_builtin_upload_keys": visible_builtin_wgs_keys("upload"),
    })




@login_required
def show_assembly(request):
    q = (request.GET.get("q") or "").strip()
    assembly_summaries = AssemblyScan.objects.all().order_by('-Date_uploaded_as')
    assembly_summaries = apply_wgs_text_search(assembly_summaries, q)
    upload_dates = (
        AssemblyScan.objects.exclude(Date_uploaded_as__isnull=True)
        .values_list('Date_uploaded_as', flat=True)
        .distinct()
        .order_by('-Date_uploaded_as')
    )

    total_records = AssemblyScan.objects.count()
    filtered_records = assembly_summaries.count()
     # Paginate the queryset to display 20 records per page
    paginator = Paginator(assembly_summaries, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, "wgs_app/show_assembly.html", {
        "page_obj": page_obj,
        "upload_dates": upload_dates,
        "total_records": total_records,
        "filtered_records": filtered_records,
        "q": q,
    })




@login_required
@role_required(*WGS_WRITE_ALLOWED_ROLES)
@require_POST
def delete_assembly(request, pk):
    assembly_item = get_object_or_404(AssemblyScan, pk=pk)

    if request.method == "POST":
        # Before deleting, clear related field in WGS_Project
        WGS_Project.objects.filter(WGS_Assembly_Acc=assembly_item.Assembly_Accession).update(
            WGS_Assembly_Acc="",
            WGS_AssemblySummary=False
        )

        assembly_item.delete()
        messages.success(request, f"Record {assembly_item.sample} deleted successfully!")
        return redirect('show_assembly')

    messages.error(request, "Invalid request for deletion.")
    return redirect('show_assembly')




@login_required
@role_required(*WGS_WRITE_ALLOWED_ROLES)
@require_POST
def delete_all_assembly(request):
    """
    Safely delete all Assembly records but preserve WGS_Project links
    for other WGS data types (BactScout, MLST, CheckM2, Gambit, AMRFinder, etc.).
    """
    # Step 1: Clear only Assembly fields in existing WGS_Project records
    updated_count = WGS_Project.objects.filter(
        WGS_Assembly_Acc__isnull=False
    ).exclude(WGS_Assembly_Acc="").update(
        WGS_Assembly_Acc="",
        WGS_AssemblySummary=False
    )

    # Step 2: Delete all Assembly summary data
    AssemblyScan.objects.all().delete()

    # Step 3: Display success message
    messages.success(
        request,
        f"All Assembly records deleted successfully, and {updated_count} WGS Project(s) were unlinked from Assembly data."
    )

    return redirect("show_assembly")




@login_required
@role_required(*WGS_WRITE_ALLOWED_ROLES)
@require_POST
def delete_assembly_by_date(request):
    if request.method == "POST":
        upload_date_str = request.POST.get("upload_date")
        print("🕒 Received upload_date_str:", upload_date_str)

        if not upload_date_str:
            messages.error(request, "Please select an upload date to delete.")
            return redirect("show_assembly")

        # Use Django’s date parser
        upload_date = parse_date(upload_date_str)

        if not upload_date:
            messages.error(request, f"Invalid date format: {upload_date_str}")
            return redirect("show_assembly")

        deleted_count, _ = AssemblyScan.objects.filter(Date_uploaded_as=upload_date).delete()
        messages.success(request, f"✅ Deleted {deleted_count} AssemblyScan records uploaded on {upload_date}.")
        return redirect("show_assembly")

    messages.error(request, "Invalid request method.")
    return redirect("show_assembly")




###################  Amr finder
@login_required
@role_required(*WGS_WRITE_ALLOWED_ROLES)
def upload_amrfinder(request):
    form = WGSProjectForm()
    amrfinder_form = AmrUploadForm()
    editing = False

    if request.method == "POST" and request.FILES.get("Amrfinderfile"):
        overwrite = wgs_upload_overwrite_enabled(request)
        amrfinder_form = AmrUploadForm(request.POST, request.FILES)
        try:
            upload = amrfinder_form.save()
            df = read_uploaded_file(upload.Amrfinderfile, sheet_name=["amrfinderplus", "Sheet1"])
            df.columns = [str(column).strip() if column is not None else "" for column in df.columns]
            df = df.loc[:, [column for column in df.columns if column and column.lower() != "nan"]]
        except Exception as e:
            messages.error(request, f"Error processing AMRFinderPlus file: {e}")
            return render(request, "wgs_app/Add_wgs.html", {
                "form": form,
                "gambit_form": GambitUploadForm(),
                "mlst_form": MlstUploadForm(),
                "checkm2_form": Checkm2UploadForm(),
                "amrfinder_form": amrfinder_form,
                "assembly_form": AssemblyUploadForm(),
                "demogs_form": DemogsDataUploadForm(),
                "antibiotic_form": FinalAntibioticUploadForm(),
                "sampleinfo_form" : SampleInfoUploadForm(),
                "bactscout_form": BactScoutUploadForm(),
                "gtdbtk_form": GtdbTkUploadForm(),
                "raw_antibiotic_form": RawAntibioticUploadForm(),
                "editing": editing,
                "visible_builtin_upload_keys": visible_builtin_wgs_keys("upload"),
            })

        amrfinder_source_columns = {
            label: field
            for field, label in Amrfinderplus.FIELD_LABELS
        }

        # Preload all valid site codes from SiteData (uppercase)
        site_codes = set(SiteData.objects.values_list("SiteCode", flat=True))

        rows = []
        sample_names = set()
        fallback_sample_variants = set()

        for row in df.to_dict("records"):
            sample_name = upload_text_or_none(row.get("Name", "")) or ""
            if sample_name:
                sample_names.add(sample_name)
            direct_accession = format_wgs_accession_from_sample_name(sample_name, site_codes)
            if not direct_accession and re.search(r"\d{2,4}ARS", normalize_wgs_sample_identity(sample_name).upper()):
                fallback_sample_variants.update(build_wgs_sample_identity_variants(sample_name))

            amrfinder_values = {
                field: upload_text_or_none(row.get(label, "")) or ""
                for label, field in amrfinder_source_columns.items()
            }
            rows.append({
                "sample_name": sample_name,
                "direct_accession": direct_accession,
                "values": amrfinder_values,
            })

        sampleinfo_accessions = {}
        if fallback_sample_variants:
            sampleinfo_matches = (
                SampleInformation.objects
                .filter(sample_name__in=fallback_sample_variants)
                .exclude(sample_accession__isnull=True)
                .exclude(sample_accession="")
                .order_by("-Date_uploaded_si", "-pk")
            )
            for sampleinfo in sampleinfo_matches:
                for variant in build_wgs_sample_identity_variants(sampleinfo.sample_name):
                    sampleinfo_accessions.setdefault(
                        variant,
                        (sampleinfo.sample_accession or "").strip(),
                    )

        def resolve_amrfinder_accession(row_data):
            if row_data["direct_accession"]:
                return row_data["direct_accession"]
            for variant in build_wgs_sample_identity_variants(row_data["sample_name"]):
                accession = sampleinfo_accessions.get(variant)
                if accession:
                    return accession
            return ""

        def amrfinder_row_key(accession, sample_name, values):
            return (
                accession or "",
                sample_name or "",
                values.get("protein_id", "") or "",
                values.get("contig_id", "") or "",
                values.get("start", "") or "",
                values.get("stop", "") or "",
                values.get("element_symbol", "") or "",
            )

        prepared_rows = {}
        duplicate_rows = 0
        accessions = set()
        for row_data in rows:
            accession = resolve_amrfinder_accession(row_data)
            row_data["accession"] = accession
            row_key = amrfinder_row_key(accession, row_data["sample_name"], row_data["values"])
            if row_key in prepared_rows:
                duplicate_rows += 1
            prepared_rows[row_key] = row_data
            if accession:
                accessions.add(accession)

        final_data_by_accession = {
            item.f_AccessionNo: item
            for item in Final_Data.objects.filter(f_AccessionNo__in=accessions)
        }

        project_filter = Q(pk__isnull=True)
        if accessions:
            project_filter = Q(WGS_Amrfinder_Acc__in=accessions) | Q(Ref_Accession_id__in=accessions)
        project_by_accession = {}
        for project in WGS_Project.objects.filter(project_filter).select_related("Ref_Accession").order_by("id"):
            for accession in (project.WGS_Amrfinder_Acc, project.Ref_Accession_id):
                accession = (accession or "").strip()
                if accession:
                    project_by_accession.setdefault(accession, project)

        for accession in accessions:
            if accession not in project_by_accession:
                project_by_accession[accession] = get_or_create_wgs_project_for_upload(
                    accession,
                    final_data_by_accession.get(accession),
                    "WGS_Amrfinder_Acc",
                    "WGS_AmrfinderSummary",
                )

        project_updates = {}
        for accession, project in project_by_accession.items():
            final_data = final_data_by_accession.get(accession)
            desired_summary = (
                bool(accession)
                and bool(final_data)
                and accession == getattr(final_data, "f_AccessionNo", None)
            )
            changed = False
            if final_data and project.Ref_Accession_id != final_data.f_AccessionNo:
                project.Ref_Accession = final_data
                changed = True
            if (project.WGS_Amrfinder_Acc or "") != accession:
                project.WGS_Amrfinder_Acc = accession
                changed = True
            if project.WGS_AmrfinderSummary != desired_summary:
                project.WGS_AmrfinderSummary = desired_summary
                changed = True
            if changed:
                project_updates[project.pk] = project

        existing_filter = Q(pk__isnull=True)
        if accessions:
            existing_filter |= Q(Amrfinder_Accession__in=accessions)
        if sample_names:
            existing_filter |= Q(name__in=sample_names)

        existing_by_key = {}
        existing_records = (
            Amrfinderplus.objects
            .filter(existing_filter)
            .select_related("amrfinder_project")
            .order_by("-id")
        )
        for existing in existing_records:
            existing_key = amrfinder_row_key(
                existing.Amrfinder_Accession or "",
                existing.name or "",
                {
                    "protein_id": existing.protein_id or "",
                    "contig_id": existing.contig_id or "",
                    "start": existing.start or "",
                    "stop": existing.stop or "",
                    "element_symbol": existing.element_symbol or "",
                },
            )
            existing_by_key.setdefault(existing_key, existing)

        create_records = []
        update_records = []
        skipped_count = 0
        update_fields = list(Amrfinderplus.UPLOAD_FIELDS) + [
            "Amrfinder_Accession",
            "amrfinder_project",
        ]

        for row_key, row_data in prepared_rows.items():
            accession = row_data["accession"]
            values = row_data["values"]
            project = project_by_accession.get(accession) if accession else None
            existing = existing_by_key.get(row_key)

            if existing:
                if not overwrite:
                    skipped_count += 1
                    continue
                for field, value in values.items():
                    setattr(existing, field, value)
                existing.Amrfinder_Accession = accession
                existing.name = row_data["sample_name"]
                existing.amrfinder_project = project or existing.amrfinder_project
                update_records.append(existing)
            else:
                create_values = values.copy()
                create_values.update({
                    "Amrfinder_Accession": accession,
                    "name": row_data["sample_name"],
                    "amrfinder_project": project,
                })
                create_records.append(Amrfinderplus(**create_values))

        with transaction.atomic():
            if project_updates:
                WGS_Project.objects.bulk_update(
                    list(project_updates.values()),
                    ["Ref_Accession", "WGS_Amrfinder_Acc", "WGS_AmrfinderSummary"],
                    batch_size=500,
                )
            if create_records:
                Amrfinderplus.objects.bulk_create(create_records, batch_size=1000)
            if update_records:
                Amrfinderplus.objects.bulk_update(update_records, update_fields, batch_size=500)

        messages.success(
            request,
            (
                "AMRFinderPlus uploaded: "
                f"{len(rows)} row(s) processed, "
                f"{len(create_records)} created, "
                f"{len(update_records)} updated, "
                f"{skipped_count} skipped"
                + (f", {duplicate_rows} duplicate row(s) collapsed." if duplicate_rows else ".")
            ),
        )
        return redirect("show_amrfinder")

    return render(request, "wgs_app/Add_wgs.html", {
        "form": form,
        "gambit_form": GambitUploadForm(),
        "mlst_form": MlstUploadForm(),
        "checkm2_form": Checkm2UploadForm(),
        "assembly_form": AssemblyUploadForm(),
        "amrfinder_form": amrfinder_form,
        "demogs_form": DemogsDataUploadForm(),
        "antibiotic_form": FinalAntibioticUploadForm(),
        "sampleinfo_form" : SampleInfoUploadForm(),
        "bactscout_form": BactScoutUploadForm(),
        "gtdbtk_form": GtdbTkUploadForm(),
        "raw_antibiotic_form": RawAntibioticUploadForm(),
        "editing": editing,
        "visible_builtin_upload_keys": visible_builtin_wgs_keys("upload"),
    })




@login_required
def show_amrfinder(request):
    q = (request.GET.get("q") or "").strip()
    amrfinder_summaries = Amrfinderplus.objects.all().order_by('-Date_uploaded_am')
    amrfinder_summaries = apply_wgs_text_search(amrfinder_summaries, q)
    upload_dates = (
        Amrfinderplus.objects.exclude(Date_uploaded_am__isnull=True)
        .values_list('Date_uploaded_am', flat=True)
        .distinct()
        .order_by('-Date_uploaded_am')
    )

    total_records = Amrfinderplus.objects.count()
    filtered_records = amrfinder_summaries.count()
     # Paginate the queryset to display 20 records per page
    paginator = Paginator(amrfinder_summaries, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, "wgs_app/show_amrfinder.html", {
        "page_obj": page_obj,
        "upload_dates": upload_dates,
        "total_records": total_records,
        "filtered_records": filtered_records,
        "q": q,
        "amrfinder_fields": Amrfinderplus.FIELD_LABELS,
    })






@login_required
@role_required(*WGS_WRITE_ALLOWED_ROLES)
@require_POST
def delete_amrfinder(request, pk):
    amrfinder_item = get_object_or_404(Amrfinderplus, pk=pk)

    if request.method == "POST":
        # Before deleting, clear related field in WGS_Project
        WGS_Project.objects.filter(WGS_Amrfinder_Acc=amrfinder_item.Amrfinder_Accession).update(
            WGS_Amrfinder_Acc="",
            WGS_AmrfinderSummary=False
        )

        amrfinder_item.delete()
        messages.success(request, f"Record {amrfinder_item.name} deleted successfully!")
        return redirect('show_amrfinder')

    messages.error(request, "Invalid request for deletion.")
    return redirect('show_amrfinder')





@login_required
@role_required(*WGS_WRITE_ALLOWED_ROLES)
@require_POST
def delete_all_amrfinder(request):
    """
    Safely delete all AMRFinder records but preserve WGS_Project links
    for other WGS data types (BactScout, MLST, CheckM2, Assembly, Gambit, etc.).
    """
    # Step 1: Clear only AMRFinder fields in existing WGS_Project records
    updated_count = WGS_Project.objects.filter(
        WGS_Amrfinder_Acc__isnull=False
    ).exclude(WGS_Amrfinder_Acc="").update(
        WGS_Amrfinder_Acc="",
        WGS_AmrfinderSummary=False
    )

    # Step 2: Delete all AMRFinder summary data
    Amrfinderplus.objects.all().delete()

    # Step 3: Display success message
    messages.success(
        request,
        f"All AMRFinder records deleted successfully, and {updated_count} WGS Project(s) were unlinked from AMRFinder data."
    )

    return redirect("show_amrfinder")




@login_required
@role_required(*WGS_WRITE_ALLOWED_ROLES)
@require_POST
def delete_amrfinder_by_date(request):
    if request.method == "POST":
        upload_date_str = request.POST.get("upload_date")
        if not upload_date_str:
            messages.error(request, "Please select an upload date to delete.")
            return redirect("show_amrfinder")

        try:
            target_date = datetime.strptime(upload_date_str, "%Y-%m-%d").date()
        except ValueError:
            messages.error(request, "Invalid date format. Please use YYYY-MM-DD.")
            return redirect("show_amrfinder")

        start = datetime.combine(target_date, datetime.min.time())
        end = datetime.combine(target_date + timedelta(days=1), datetime.min.time())

        deleted_count, _ = Amrfinderplus.objects.filter(
            Date_uploaded_am__gte=start,
            Date_uploaded_am__lt=end
        ).delete()

        messages.success(request, f"✅ Deleted {deleted_count} records uploaded on {target_date}.")

    else:
        messages.error(request, "Invalid request method.")

    return redirect("show_amrfinder")




############## Sample Information
@login_required
@role_required(*WGS_WRITE_ALLOWED_ROLES)
def upload_sample_information(request):

    form = WGSProjectForm()
    sampleinfo_form = SampleInfoUploadForm()
    editing = False

    if request.method == "POST" and request.FILES.get("sampleinfo"):
        overwrite = wgs_upload_overwrite_enabled(request)

        sampleinfo_form = SampleInfoUploadForm(request.POST, request.FILES)

        try:
            upload = sampleinfo_form.save()

            df = read_uploaded_file(upload.sampleinfo, sheet_name="batch")

            df.columns = df.columns.str.strip().str.replace(".", "", regex=False)

        except Exception as e:

            messages.error(request, f"Error processing Sample Information file: {e}")

            return render(request, "wgs_app/Add_wgs.html", {
                "form": form,
                "sampleinfo_form": sampleinfo_form,
                "gambit_form": GambitUploadForm(),
                "mlst_form": MlstUploadForm(),
                "checkm2_form": Checkm2UploadForm(),
                "amrfinder_form": AmrUploadForm(),
                "assembly_form": AssemblyUploadForm(),
                "demogs_form": DemogsDataUploadForm(),
                "antibiotic_form": FinalAntibioticUploadForm(),
                "bactscout_form": BactScoutUploadForm(),
                "gtdbtk_form": GtdbTkUploadForm(),
                "raw_antibiotic_form": RawAntibioticUploadForm(),
                "editing": editing,
                "visible_builtin_upload_keys": visible_builtin_wgs_keys("upload"),
            })

        site_codes = set(SiteData.objects.values_list("SiteCode", flat=True))

        # Same extraction logic used in Assembly
        def format_sample_accession(raw_name: str) -> str:

            if not raw_name:
                return ""

            base = os.path.basename(raw_name)

            base_noext = os.path.splitext(base)[0].strip()

            if "ARS" not in base_noext:
                return ""

            parts = re.split(r"[-_]", base_noext)

            if not parts:
                return ""

            prefix = parts[0]

            for part in parts[1:]:

                m = re.match(r"^([A-Za-z]{2,6})(\d+)", part)

                if m:

                    letters = m.group(1).upper()

                    digits = m.group(2)

                    if letters in site_codes:
                        return f"{prefix}_{letters}{digits}"

            for i in range(1, len(parts) - 1):

                if parts[i].upper() in site_codes:

                    letters = parts[i].upper()

                    digits_match = re.search(r"(\d+)", parts[i + 1])

                    if digits_match:
                        return f"{prefix}_{letters}{digits_match.group(1)}"

                    return f"{prefix}_{letters}"

            return ""

        # Process rows
        for _, row in df.iterrows():

            sample_name = str(row.get("sample_name", "")).strip()

            batch_code = row.get("batch_code", "")
            sample_accession = resolve_wgs_accession_from_sample_name(
                sample_name,
                site_codes,
                batch_code=batch_code,
            )

            existing_sample = find_existing_wgs_module_record_by_fields(
                SampleInformation,
                {
                    "sample_accession": sample_accession,
                    "sample_name": sample_name,
                    "batch_code": batch_code,
                },
            )
            if existing_sample and not overwrite:
                continue

            referred_obj = (
                Final_Data.objects.filter(f_AccessionNo=sample_accession).first()
                if sample_accession else None
            )
            classification = None
            if referred_obj:
                classification = Classification_Table.objects.filter(
                    Class_idNumReferred=referred_obj
                ).first()
            if not classification and sample_accession:
                classification = Classification_Table.objects.filter(
                    Class_AccessionNo=sample_accession
                ).first()

            connect_project = None
            if sample_accession:
                connect_project = get_or_create_wgs_project_for_upload(
                    sample_accession,
                    referred_obj,
                    "WGS_SampleInfo_Acc",
                    "WGS_SampleInfoSummary",
                    existing_project=existing_sample.sample_project if existing_sample else None,
                )

            def row_bool(*column_names):
                for column_name in column_names:
                    if column_name in row:
                        return upload_bool_or_false(row.get(column_name, False))
                return False

            def classification_bool(field_name):
                return bool(getattr(classification, field_name, False)) if classification else False

            def row_bool_or_class(classification_field, *column_names):
                for column_name in column_names:
                    if column_name in row:
                        return upload_bool_or_false(row.get(column_name, False))
                return classification_bool(classification_field)

            ghru_all = row_bool_or_class(
                "Class_Chk_GHRU_all",
                "ghru_all",
                "GHRU_all",
                "Class_Chk_GHRU_all",
                "ghru",
            )
            ghru_neo = row_bool_or_class("Class_Chk_GHRU_Neo", "ghru_neo", "GHRU_neo", "Class_Chk_GHRU_Neo")

            sampleinfo_record, _ = save_or_update_wgs_module_record(
                existing_sample,
                {
                    "_model": SampleInformation,
                    "sample_project": connect_project,
                    "sample_accession": sample_accession,
                    "batch_code": batch_code,
                    "sample_name": sample_name,
                    "status": row.get("status", ""),
                    "emerging": row_bool_or_class("Class_Chk_Emerging", "emerging", "Class_Chk_Emerging"),
                    "structured": row_bool_or_class("Class_Chk_Structured", "structured", "Class_Chk_Structured"),
                    "satscan": row_bool_or_class("Class_Chk_Satscan", "satscan", "Class_Chk_Satscan"),
                    "serotyping": row_bool_or_class("Class_Chk_Serotyping", "serotyping", "Class_Chk_Serotyping"),
                    "ghru": row_bool("ghru") or ghru_all,
                    "ghru_all": ghru_all,
                    "ghru_neo": ghru_neo,
                    "egasp": row_bool_or_class("Class_Chk_EGASP", "egasp", "Class_Chk_EGASP"),
                    "tricycle": row_bool_or_class("Class_Chk_Tricycle", "tricycle", "Class_Chk_Tricycle"),
                    "pulsenet": row_bool_or_class("Class_Chk_Pulsenet", "pulsenet", "Class_Chk_Pulsenet"),
                    "tulip": row_bool_or_class("Class_Chk_Tulip", "tulip", "Class_Chk_Tulip"),
                },
                overwrite=overwrite,
            )
            sync_wgs_records_from_sampleinfo(sampleinfo_record)

        messages.success(request, "Sample Information records uploaded successfully.")

        return redirect("show_sample_information")

    return render(request, "wgs_app/Add_wgs.html", {

        "form": form,
        "sampleinfo_form": sampleinfo_form,
        "bactscout_form": BactScoutUploadForm(),
        "gtdbtk_form" : GtdbTkUploadForm(),
        "gambit_form": GambitUploadForm(),
        "mlst_form": MlstUploadForm(),
        "checkm2_form": Checkm2UploadForm(),
        "amrfinder_form": AmrUploadForm(),
        "assembly_form": AssemblyUploadForm(),
        "demogs_form": DemogsDataUploadForm(),
        "antibiotic_form": FinalAntibioticUploadForm(),
        "raw_antibiotic_form": RawAntibioticUploadForm(),
        "editing": editing,
        "visible_builtin_upload_keys": visible_builtin_wgs_keys("upload"),
    })


@login_required
@role_required(*WGS_WRITE_ALLOWED_ROLES)
@require_POST
def relink_sample_information_matches(request):
    linked_count = relink_all_wgs_records_from_sampleinfo()
    messages.success(
        request,
        f"{linked_count} WGS record(s) relinked from Sample Information sample names."
    )
    return redirect("upload_wgs_view")






@login_required
def show_sample_information(request):

    q = (request.GET.get("q") or "").strip()
    search_terms = []
    if q:
        raw_terms = {
            q,
            q.replace("_", "-"),
            q.replace("-", "_"),
            q.replace(" ", ""),
            q.replace(" ", "-"),
            q.replace(" ", "_"),
            re.sub(r"[\s_-]+", "", q),
        }
        seen_terms = set()
        for term in raw_terms:
            term = str(term or "").strip()
            if not term:
                continue
            key = term.upper()
            if key not in seen_terms:
                seen_terms.add(key)
                search_terms.append(term)

    records = SampleInformation.objects.all().order_by('-Date_uploaded_si')

    if q:
        search_filter = Q()
        for term in search_terms:
            search_filter |= (
                Q(sample_name__icontains=term)
                | Q(sample_accession__icontains=term)
                | Q(batch_code__icontains=term)
                | Q(status__icontains=term)
            )
        records = records.filter(search_filter)

    upload_dates = (
        SampleInformation.objects
        .exclude(Date_uploaded_si__isnull=True)
        .values_list('Date_uploaded_si', flat=True)
        .distinct()
        .order_by('-Date_uploaded_si')
    )

    total_records = SampleInformation.objects.count()
    filtered_records = records.count()

    paginator = Paginator(records, 20)

    page_number = request.GET.get('page')

    page_obj = paginator.get_page(page_number)

    return render(request, "wgs_app/show_sampleinfo.html", {

        "page_obj": page_obj,

        "upload_dates": upload_dates,

        "total_records": total_records,
        "filtered_records": filtered_records,
        "q": q,

    })



@login_required
@role_required(*WGS_WRITE_ALLOWED_ROLES)
@require_POST
def delete_sample_information(request, pk):

    item = get_object_or_404(SampleInformation, pk=pk)

    if request.method == "POST":

        item.delete()

        messages.success(
            request,
            f"Sample {item.sample_name} deleted successfully!"
        )

        return redirect("show_sample_information")

    messages.error(request, "Invalid request.")

    return redirect("show_sample_information")



@login_required
@role_required(*WGS_WRITE_ALLOWED_ROLES)
@require_POST
def delete_all_sample_information(request):

    deleted_count, _ = SampleInformation.objects.all().delete()

    messages.success(
        request,
        f"{deleted_count} Sample Information records deleted successfully."
    )

    return redirect("show_sample_information")


@login_required
@role_required(*WGS_WRITE_ALLOWED_ROLES)
@require_POST
def delete_sample_information_by_date(request):

    if request.method == "POST":

        upload_date_str = request.POST.get("upload_date")

        if not upload_date_str:

            messages.error(request, "Please select a date.")

            return redirect("show_sample_information")

        upload_date = parse_date(upload_date_str)

        if not upload_date:

            messages.error(request, "Invalid date format.")

            return redirect("show_sample_information")

        deleted_count, _ = SampleInformation.objects.filter(
            Date_uploaded_si=upload_date
        ).delete()

        messages.success(
            request,
            f"Deleted {deleted_count} records uploaded on {upload_date}."
        )

        return redirect("show_sample_information")

    messages.error(request, "Invalid request method.")

    return redirect("show_sample_information")


################## Bactscout
@login_required
@role_required(*WGS_WRITE_ALLOWED_ROLES)
def upload_bactscout(request):

    form = WGSProjectForm()
    bactscout_form = BactScoutUploadForm()
    editing = False

    if request.method == "POST" and request.FILES.get("bactscoutfile"):
        overwrite = wgs_upload_overwrite_enabled(request)

        bactscout_form = BactScoutUploadForm(request.POST, request.FILES)

        try:
            upload = bactscout_form.save()
            df = read_uploaded_file(upload.bactscoutfile, sheet_name=["bactscout", "Sheet1"])
            df = df.dropna(axis=1, how="all")
            df.columns = [
                str(column).strip().replace(".", "")
                for column in df.columns
            ]
            df = df[[column for column in BactScout.UPLOAD_FIELDS if column in df.columns]]

        except Exception as e:

            messages.error(request, f"Error processing BactScout file: {e}")

            return render(request, "wgs_app/Add_wgs.html", {
                "form": form,
                "sampleinfo_form": SampleInfoUploadForm(),
                "bactscout_form": bactscout_form,
                "gtdbtk_form" : GtdbTkUploadForm(),
                "gambit_form": GambitUploadForm(),
                "mlst_form": MlstUploadForm(),
                "checkm2_form": Checkm2UploadForm(),
                "amrfinder_form": AmrUploadForm(),
                "assembly_form": AssemblyUploadForm(),
                "demogs_form": DemogsDataUploadForm(),
                "antibiotic_form": FinalAntibioticUploadForm(),
                "raw_antibiotic_form": RawAntibioticUploadForm(),
                "editing": editing,
                "visible_builtin_upload_keys": visible_builtin_wgs_keys("upload"),
            })

        site_codes = set(SiteData.objects.values_list("SiteCode", flat=True))

        # accession extractor
        def format_bactscout_accession(raw_name: str):

            if not raw_name:
                return ""

            base = os.path.basename(raw_name)
            base_noext = os.path.splitext(base)[0].strip()

            if "ARS" not in base_noext:
                return ""

            parts = re.split(r"[-_]", base_noext)

            if not parts:
                return ""

            prefix = parts[0]

            for part in parts[1:]:

                m = re.match(r"^([A-Za-z]{2,6})(\d+)", part)

                if m:

                    letters = m.group(1).upper()
                    digits = m.group(2)

                    if letters in site_codes:
                        return f"{prefix}_{letters}{digits}"

            for i in range(1, len(parts) - 1):

                if parts[i].upper() in site_codes:

                    letters = parts[i].upper()

                    digits_match = re.search(r"(\d+)", parts[i + 1])

                    if digits_match:
                        return f"{prefix}_{letters}{digits_match.group(1)}"

                    return f"{prefix}_{letters}"

            return ""

        rows = []
        skipped_count = 0
        for row in df.to_dict("records"):

            sample_id = upload_text_or_none(row.get("sample_id"))
            sample_id = str(sample_id or "").strip()
            if not sample_id:
                skipped_count += 1
                continue

            bactscout_accession = resolve_wgs_accession_from_sample_name(sample_id, site_codes)

            bactscout_defaults = {
                "BactScout_Accession": bactscout_accession,
                "sample_id": sample_id,

                "a_final_status": upload_text_or_none(row.get("a_final_status")),
                "adapter_detection_status": upload_text_or_none(row.get("adapter_detection_status")),
                "contamination_status": upload_text_or_none(row.get("contamination_status")),
                "species_status": upload_text_or_none(row.get("species_status")),
                "coverage_status": upload_text_or_none(row.get("coverage_status")),
                "coverage_estimate_qualibact_status": upload_text_or_none(row.get("coverage_estimate_qualibact_status")),
                "duplication_status": upload_text_or_none(row.get("duplication_status")),
                "gc_content_status": upload_text_or_none(row.get("gc_content_status")),
                "mlst_status": upload_text_or_none(row.get("mlst_status")),
                "n_content_status": upload_text_or_none(row.get("n_content_status")),
                "read_length_status": upload_text_or_none(row.get("read_length_status")),
                "read_q30_status": upload_text_or_none(row.get("read_q30_status")),

                "species": upload_text_or_none(row.get("species")),
                "species_abundance": upload_text_or_none(row.get("species_abundance")),
                "species_coverage": upload_text_or_none(row.get("species_coverage")),
                "species_message": upload_text_or_none(row.get("species_message")),

                "contamination_message": upload_text_or_none(row.get("contamination_message")),

                "coverage_estimate_sylph": upload_number_or_none(row.get("coverage_estimate_sylph")),
                "coverage_estimate_sylph_message": upload_text_or_none(row.get("coverage_estimate_sylph_message")),
                "coverage_estimate_qualibact": upload_number_or_none(row.get("coverage_estimate_qualibact")),
                "coverage_estimate_qualibact_message": upload_text_or_none(row.get("coverage_estimate_qualibact_message")),

                "duplication_rate": upload_number_or_none(row.get("duplication_rate")),
                "duplication_message": upload_text_or_none(row.get("duplication_message")),

                "gc_content": upload_number_or_none(row.get("gc_content")),
                "gc_content_lower": upload_number_or_none(row.get("gc_content_lower"), integer=True),
                "gc_content_upper": upload_number_or_none(row.get("gc_content_upper"), integer=True),
                "gc_content_message": upload_text_or_none(row.get("gc_content_message")),

                "n_content_rate": upload_number_or_none(row.get("n_content_rate")),
                "n_content_message": upload_text_or_none(row.get("n_content_message")),

                "mlst_st": upload_text_or_none(row.get("mlst_st")),
                "mlst_message": upload_text_or_none(row.get("mlst_message")),

                "read1_mean_length": upload_number_or_none(row.get("read1_mean_length"), integer=True),
                "read2_mean_length": upload_number_or_none(row.get("read2_mean_length"), integer=True),
                "read_length_message": upload_text_or_none(row.get("read_length_message")),

                "read_q20_bases": upload_number_or_none(row.get("read_q20_bases")),
                "read_q20_rate": upload_number_or_none(row.get("read_q20_rate")),
                "read_q30_bases": upload_number_or_none(row.get("read_q30_bases")),
                "read_q30_rate": upload_number_or_none(row.get("read_q30_rate")),
                "read_q30_message": upload_text_or_none(row.get("read_q30_message")),
                "read_total_bases": upload_number_or_none(row.get("read_total_bases")),
                "read_total_reads": upload_number_or_none(row.get("read_total_reads"), integer=True),

                "adapter_detection_message": upload_text_or_none(row.get("adapter_detection_message")),

                "ref_genome": upload_text_or_none(row.get("ref_genome")),
                "genome_size_expected": upload_number_or_none(row.get("genome_size_expected"), integer=True),
            }
            rows.append(bactscout_defaults)

        result = bulk_save_wgs_module_records(
            BactScout,
            rows,
            accession_field="BactScout_Accession",
            project_field="bactscout_project",
            project_accession_field="WGS_BactScout_Acc",
            summary_field="WGS_BactScoutSummary",
            key_fields=("BactScout_Accession", "sample_id"),
            value_fields=BactScout.UPLOAD_FIELDS,
            overwrite=overwrite,
        )

        messages.success(
            request,
            f"BactScout upload complete: {result['created']} created, "
            f"{result['updated']} updated, {result['skipped'] + skipped_count} skipped.",
        )

        return redirect("show_bactscout")

    return render(request, "wgs_app/Add_wgs.html", {

        "form": form,
        "sampleinfo_form": SampleInfoUploadForm(),
        "bactscout_form": bactscout_form,
        "gtdbtk_form" : GtdbTkUploadForm(),
        "gambit_form": GambitUploadForm(),
        "mlst_form": MlstUploadForm(),
        "checkm2_form": Checkm2UploadForm(),
        "amrfinder_form": AmrUploadForm(),
        "assembly_form": AssemblyUploadForm(),
        "demogs_form": DemogsDataUploadForm(),
        "antibiotic_form": FinalAntibioticUploadForm(),
        "raw_antibiotic_form": RawAntibioticUploadForm(),
        "editing": editing,
        "visible_builtin_upload_keys": visible_builtin_wgs_keys("upload"),

    })


@login_required
def show_bactscout(request):

    q = (request.GET.get("q") or "").strip()
    records = BactScout.objects.all().order_by('-Date_uploaded_bs')
    records = apply_wgs_text_search(records, q)

    upload_dates = (
        BactScout.objects.exclude(Date_uploaded_bs__isnull=True)
        .values_list('Date_uploaded_bs', flat=True)
        .distinct()
        .order_by('-Date_uploaded_bs')
    )

    total_records = BactScout.objects.count()
    filtered_records = records.count()

    paginator = Paginator(records, 20)

    page_number = request.GET.get('page')

    page_obj = paginator.get_page(page_number)

    return render(request, "wgs_app/show_bactscout.html", {

        "page_obj": page_obj,
        "upload_dates": upload_dates,
        "total_records": total_records,
        "filtered_records": filtered_records,
        "q": q,
        "bactscout_fields": BactScout.FIELD_LABELS,
        "bactscout_colspan": len(BactScout.FIELD_LABELS) + 3,

    })


@login_required
@role_required(*WGS_WRITE_ALLOWED_ROLES)
@require_POST
def delete_bactscout(request, pk):

    item = get_object_or_404(BactScout, pk=pk)

    if request.method == "POST":

        item.delete()

        messages.success(request, f"Record {item.sample_id} deleted successfully!")

        return redirect("show_bactscout")

    messages.error(request, "Invalid request.")

    return redirect("show_bactscout")


@login_required
@role_required(*WGS_WRITE_ALLOWED_ROLES)
@require_POST
def delete_all_bactscout(request):

    deleted_count, _ = BactScout.objects.all().delete()

    messages.success(request, f"{deleted_count} BactScout records deleted.")

    return redirect("show_bactscout")



@login_required
@role_required(*WGS_WRITE_ALLOWED_ROLES)
@require_POST
def delete_bactscout_by_date(request):

    if request.method == "POST":

        upload_date_str = request.POST.get("upload_date")

        if not upload_date_str:
            messages.error(request, "Please select an upload date.")
            return redirect("show_bactscout")

        upload_date = parse_date(upload_date_str)

        if not upload_date:
            messages.error(request, "Invalid date format.")
            return redirect("show_bactscout")

        deleted_count, _ = BactScout.objects.filter(
            Date_uploaded_bs=upload_date
        ).delete()

        messages.success(
            request,
            f"Deleted {deleted_count} records uploaded on {upload_date}."
        )

        return redirect("show_bactscout")

    messages.error(request, "Invalid request.")

    return redirect("show_bactscout")

############## GtdbTk 

@login_required
@role_required(*WGS_WRITE_ALLOWED_ROLES)
def upload_gtdbtk(request):

    form = WGSProjectForm()
    gtdbtk_form = GtdbTkUploadForm()
    editing = False

    if request.method == "POST" and request.FILES.get("GtdbTkFile"):
        overwrite = wgs_upload_overwrite_enabled(request)

        gtdbtk_form = GtdbTkUploadForm(request.POST, request.FILES)

        try:
            upload = gtdbtk_form.save()
            df = read_uploaded_file(upload.GtdbTkFile, sheet_name=["gtdbtk", "gtdb-tk", "gtdb_tk"])

            df.columns = df.columns.str.strip().str.replace(".", "", regex=False)

        except Exception as e:

            messages.error(request, f"Error processing GTDB-Tk file: {e}")

            return render(request, "wgs_app/Add_wgs.html", {
            "form": form,
            "sampleinfo_form": SampleInfoUploadForm(),
            "bactscout_form": BactScoutUploadForm(),
            "gtdbtk_form" : gtdbtk_form,
            "gambit_form": GambitUploadForm(),
            "mlst_form": MlstUploadForm(),
            "checkm2_form": Checkm2UploadForm(),
            "amrfinder_form": AmrUploadForm(),
            "assembly_form": AssemblyUploadForm(),
            "demogs_form": DemogsDataUploadForm(),
            "antibiotic_form": FinalAntibioticUploadForm(),
            "raw_antibiotic_form": RawAntibioticUploadForm(),
            "editing": editing,
            "visible_builtin_upload_keys": visible_builtin_wgs_keys("upload"),

            })

        site_codes = set(SiteData.objects.values_list("SiteCode", flat=True))

        # accession extractor
        def format_gtdbtk_accession(raw_name: str):

            if not raw_name:
                return ""

            base = os.path.basename(raw_name)
            base_noext = os.path.splitext(base)[0].strip()

            if "ARS" not in base_noext:
                return ""

            parts = re.split(r"[-_]", base_noext)

            if not parts:
                return ""

            prefix = parts[0]

            for part in parts[1:]:

                m = re.match(r"^([A-Za-z]{2,6})(\d+)", part)

                if m:

                    letters = m.group(1).upper()
                    digits = m.group(2)

                    if letters in site_codes:
                        return f"{prefix}_{letters}{digits}"

            for i in range(1, len(parts) - 1):

                if parts[i].upper() in site_codes:

                    letters = parts[i].upper()

                    digits_match = re.search(r"(\d+)", parts[i + 1])

                    if digits_match:
                        return f"{prefix}_{letters}{digits_match.group(1)}"

                    return f"{prefix}_{letters}"

            return ""

        gtdbtk_fields = (
            "classification",
            "closest_genome_reference",
            "closest_genome_reference_radius",
            "closest_genome_taxonomy",
            "closest_genome_ani",
            "closest_genome_af",
            "closest_placement_reference",
            "closest_placement_radius",
            "closest_placement_taxonomy",
            "closest_placement_ani",
            "closest_placement_af",
            "pplacer_taxonomy",
            "classification_method",
            "note",
            "other_related_references",
            "msa_percent",
            "translation_table",
            "red_value",
            "warnings",
        )
        rows = []
        skipped_count = 0
        for row in df.to_dict("records"):

            user_genome = str(row.get("user_genome", "")).strip()
            if not user_genome:
                skipped_count += 1
                continue

            gtdbtk_accession = resolve_wgs_accession_from_sample_name(user_genome, site_codes)

            rows.append({
                    "GtdbTk_Accession": gtdbtk_accession,
                    "user_genome": user_genome,
                    **{field: row.get(field) for field in gtdbtk_fields},
            })

        result = bulk_save_wgs_module_records(
            GtdbTk,
            rows,
            accession_field="GtdbTk_Accession",
            project_field="gtdbtk_project",
            project_accession_field="WGS_GtdbTk_Acc",
            summary_field="WGS_GtdbTkSummary",
            key_fields=("GtdbTk_Accession", "user_genome"),
            value_fields=("user_genome", *gtdbtk_fields),
            overwrite=overwrite,
        )

        messages.success(
            request,
            f"GTDB-Tk upload complete: {result['created']} created, "
            f"{result['updated']} updated, {result['skipped'] + skipped_count} skipped.",
        )

        return redirect("show_gtdbtk")

    return render(request, "wgs_app/Add_wgs.html", {

        "form": form,
        "sampleinfo_form": SampleInfoUploadForm(),
        "bactscout_form": BactScoutUploadForm(),
        "gtdbtk_form" : gtdbtk_form,
        "gambit_form": GambitUploadForm(),
        "mlst_form": MlstUploadForm(),
        "checkm2_form": Checkm2UploadForm(),
        "amrfinder_form": AmrUploadForm(),
        "assembly_form": AssemblyUploadForm(),
        "demogs_form": DemogsDataUploadForm(),
        "antibiotic_form": FinalAntibioticUploadForm(),
        "raw_antibiotic_form": RawAntibioticUploadForm(),
        "editing": editing,
        "visible_builtin_upload_keys": visible_builtin_wgs_keys("upload"),


    })



@login_required
def show_gtdbtk(request):

    q = (request.GET.get("q") or "").strip()
    records = GtdbTk.objects.all().order_by('-Date_uploaded_gt')
    records = apply_wgs_text_search(records, q)

    upload_dates = (
        GtdbTk.objects.exclude(Date_uploaded_gt__isnull=True)
        .values_list('Date_uploaded_gt', flat=True)
        .distinct()
        .order_by('-Date_uploaded_gt')
    )

    total_records = GtdbTk.objects.count()
    filtered_records = records.count()

    paginator = Paginator(records, 20)

    page_number = request.GET.get('page')

    page_obj = paginator.get_page(page_number)

    return render(request, "wgs_app/show_gtdbtk.html", {

        "page_obj": page_obj,
        "upload_dates": upload_dates,
        "total_records": total_records,
        "filtered_records": filtered_records,
        "q": q,

    })


@login_required
@role_required(*WGS_WRITE_ALLOWED_ROLES)
@require_POST
def delete_gtdbtk(request, pk):

    item = get_object_or_404(GtdbTk, pk=pk)

    if request.method == "POST":

        item.delete()

        messages.success(request, f"Record {item.user_genome} deleted successfully!")

        return redirect("show_gtdbtk")

    messages.error(request, "Invalid request.")

    return redirect("show_gtdbtk")


@login_required
@role_required(*WGS_WRITE_ALLOWED_ROLES)
@require_POST
def delete_all_gtdbtk(request):

    deleted_count, _ = GtdbTk.objects.all().delete()

    messages.success(request, f"{deleted_count} GTDB-Tk records deleted.")

    return redirect("show_gtdbtk")


@login_required
@role_required(*WGS_WRITE_ALLOWED_ROLES)
@require_POST
def delete_gtdbtk_by_date(request):

    if request.method == "POST":

        upload_date_str = request.POST.get("upload_date")

        if not upload_date_str:
            messages.error(request, "Please select an upload date.")
            return redirect("show_gtdbtk")

        upload_date = parse_date(upload_date_str)

        if not upload_date:
            messages.error(request, "Invalid date format.")
            return redirect("show_gtdbtk")

        deleted_count, _ = GtdbTk.objects.filter(
            Date_uploaded_gt=upload_date
        ).delete()

        messages.success(
            request,
            f"Deleted {deleted_count} records uploaded on {upload_date}."
        )

        return redirect("show_gtdbtk")

    messages.error(request, "Invalid request.")

    return redirect("show_gtdbtk")


########### Show all WGS project entries for one Referred_Data AccessionNo,
##########  including BactScout, CheckM2, AMRFinder tables.

# @login_required

@login_required
def view_wgs_overview(request):
    """
    Displays only isolates (Final_Data) that have matched WGS data
    across any WGS table (BactScout, MLST, CheckM2, Assembly, Gambit, AMRFinder).
    Keeps the first page light by paginating before loading per-accession flags.
    """
    q = request.GET.get("q", "").strip()
    selected_year = request.GET.get("year", "").strip()
    selected_organism = request.GET.get("organism", "").strip()
    visible_builtin_overview_keys = visible_builtin_wgs_keys("overview")

    overview_source_map = {
        "bactscout": (BactScout, "BactScout_Accession", "Date_uploaded_bs"),
        "mlst": (Mlst, "Mlst_Accession", "Date_uploaded_m"),
        "checkm2": (Checkm2, "Checkm2_Accession", "Date_uploaded_c"),
        "assembly": (AssemblyScan, "Assembly_Accession", "Date_uploaded_as"),
        "gambit": (Gambit, "Gambit_Accession", "Date_uploaded_g"),
        "amrfinder": (Amrfinderplus, "Amrfinder_Accession", "Date_uploaded_am"),
    }
    wgs_sources = [
        source
        for key, source in overview_source_map.items()
        if key in visible_builtin_overview_keys
    ]
    custom_overview_pipelines = list(
        CustomWGSPipeline.objects.filter(is_active=True, show_in_overview=True).order_by("sequencing_type", "name")
    )
    selected_custom_slugs = request.GET.getlist("custom_tables") or [
        pipeline.slug for pipeline in custom_overview_pipelines
    ]
    selected_custom_pipelines = [
        pipeline for pipeline in custom_overview_pipelines if pipeline.slug in selected_custom_slugs
    ]

    def accession_year(value):
        text = str(value or "").strip().upper()
        match = re.match(r"^(\d{2})ARS", text)
        if match:
            return str(2000 + int(match.group(1)))
        match = re.match(r"^(\d{4})ARS", text)
        if match:
            return match.group(1)
        return ""

    def source_queryset(model, accession_field):
        return model.objects.exclude(**{f"{accession_field}__isnull": True}).exclude(
            **{accession_field: ""}
        )

    def accession_set(model, field_name):
        return {
            str(acc).strip()
            for acc in source_queryset(model, field_name)
            .values_list(field_name, flat=True)
            .distinct()
            if acc and str(acc).strip()
        }

    all_wgs_accessions = set().union(*[
        accession_set(model, accession_field)
        for model, accession_field, date_field in wgs_sources
    ]) if wgs_sources else set()
    custom_wgs_accessions = set()
    for row in (
        CustomWGSPipelineRecord.objects
        .filter(pipeline__in=custom_overview_pipelines, match_status="matched")
        .values("accession", "matched_final_data_id")
        .distinct()
    ):
        custom_wgs_accessions.update(custom_record_accession_keys(row))
    all_wgs_accessions |= custom_wgs_accessions
    available_years = sorted(
        {year for year in (accession_year(acc) for acc in all_wgs_accessions) if year},
        reverse=True,
    )
    available_organisms = list(
        Final_Data.objects
        .filter(f_AccessionNo__in=all_wgs_accessions)
        .exclude(f_ars_OrgCode__isnull=True)
        .exclude(f_ars_OrgCode="")
        .order_by("f_ars_OrgCode")
        .values_list("f_ars_OrgCode", flat=True)
        .distinct()
    )

    wgs_sets = {
        key: accession_set(model, accession_field) if key in visible_builtin_overview_keys else set()
        for key, (model, accession_field, date_field) in overview_source_map.items()
    }
    wgs_accessions = set().union(*wgs_sets.values()) if wgs_sets else set()
    selected_custom_accessions = set()
    for row in (
        CustomWGSPipelineRecord.objects
        .filter(pipeline__in=selected_custom_pipelines, match_status="matched")
        .values("accession", "matched_final_data_id")
        .distinct()
    ):
        selected_custom_accessions.update(custom_record_accession_keys(row))
    wgs_accessions |= selected_custom_accessions
    if selected_year:
        wgs_accessions = {
            accession
            for accession in wgs_accessions
            if accession_year(accession) == selected_year
        }

    referred_list = Final_Data.objects.only(
        "f_AccessionNo",
        "f_Patient_ID",
        "f_Last_Name",
        "f_First_Name",
        "f_Mid_Name",
        "f_Age",
        "f_Sex",
        "f_Ward",
        "f_Spec_Type",
        "f_ars_OrgCode",
        "f_SiteCode",
        "f_Diagnosis_ICD10",
        "f_Growth",
        "f_Spec_Date",
        "f_Referral_Date",
    ).filter(
        f_AccessionNo__in=wgs_accessions
    )

    if q:
        referred_list = referred_list.filter(
            Q(f_AccessionNo__icontains=q) |
            Q(f_Patient_ID__icontains=q) |
            Q(f_Last_Name__icontains=q) |
            Q(f_First_Name__icontains=q) |
            Q(f_ars_OrgCode__icontains=q) |
            Q(f_SiteCode__icontains=q)
        )

    if selected_organism:
        referred_list = referred_list.filter(f_ars_OrgCode=selected_organism)

    referred_list = referred_list.order_by("f_AccessionNo")

    paginator = Paginator(referred_list, 50)
    page_obj = paginator.get_page(request.GET.get("page"))
    page_accessions = [
        item.f_AccessionNo.strip()
        for item in page_obj.object_list
        if item.f_AccessionNo and item.f_AccessionNo.strip()
    ]

    antibiotics_qs = Final_AntibioticEntry.objects.filter(
        ab_idNum_f_referred__f_AccessionNo__in=page_accessions
    ).select_related("ab_idNum_f_referred").only(
        "ab_idNum_f_referred__f_AccessionNo",
        "ab_Abx_code",
        "ab_MIC_RIS",
        "ab_MIC_value",
        "ab_Disk_value",
    )
    abx_map = {}
    for ab in antibiotics_qs:
        if not has_tested_antibiotic_value(ab.ab_Disk_value, ab.ab_MIC_value):
            continue
        acc = getattr(ab.ab_idNum_f_referred, "f_AccessionNo", None)
        if acc:
            abx_map.setdefault(acc, []).append({
                "code": ab.ab_Abx_code,
                "ris": ab.ab_MIC_RIS or "",
                "disk": ab.ab_Disk_value or "",
                "mic": ab.ab_MIC_value or "",
            })

    sampleinfo_map = {}
    for sampleinfo in (
        SampleInformation.objects
        .filter(sample_accession__in=page_accessions)
        .order_by("sample_accession", "-Date_uploaded_si", "-pk")
    ):
        accession = (sampleinfo.sample_accession or "").strip()
        if accession:
            sampleinfo_map.setdefault(accession, sampleinfo)

    table_data = []
    custom_counts_by_accession = defaultdict(lambda: defaultdict(int))
    seen_custom_pipeline_pairs = set()
    for row in (
        CustomWGSPipelineRecord.objects
        .filter(pipeline__in=selected_custom_pipelines, match_status="matched")
        .filter(
            Q(matched_final_data_id__in=page_accessions)
            | Q(accession__in=page_accessions)
        )
        .values("accession", "matched_final_data_id", "pipeline_id")
        .distinct()
    ):
        for custom_accession in custom_record_accession_keys(row):
            if custom_accession not in page_accessions:
                continue
            pair_key = (custom_accession.upper(), row["pipeline_id"])
            if pair_key in seen_custom_pipeline_pairs:
                continue
            seen_custom_pipeline_pairs.add(pair_key)
            custom_counts_by_accession[custom_accession][row["pipeline_id"]] += 1

    for referred in page_obj.object_list:
        acc = referred.f_AccessionNo.strip() if referred.f_AccessionNo else None
        if not acc:
            continue

        summary_flags = {
            key: acc in accessions for key, accessions in wgs_sets.items()
        }

        abx_entries = abx_map.get(acc, [])

        table_data.append({
            "accession": acc,
            "patient_id": referred.f_Patient_ID,
            "patient_name": f"{referred.f_Last_Name}, {referred.f_First_Name} {referred.f_Mid_Name or ''}".strip(),
            "age": referred.f_Age,
            "sex": referred.f_Sex,
            "ward": referred.f_Ward,
            "specimen": referred.f_Spec_Type,
            "organism": referred.f_ars_OrgCode,
            "sitecode": referred.f_SiteCode,
            "diagnosis": referred.f_Diagnosis_ICD10,
            "growth": referred.f_Growth,
            "date_collected": referred.f_Spec_Date,
            "referral_date": referred.f_Referral_Date,
            "summary_flags": summary_flags,
            "custom_pipeline_flags": [
                {
                    "pipeline": pipeline,
                    "count": custom_counts_by_accession.get(acc, {}).get(pipeline.id, 0),
                    "filter_key": f"custom{pipeline.id}",
                }
                for pipeline in selected_custom_pipelines
            ],
            "antibiotics": abx_entries,
            "sample_information": sampleinfo_map.get(acc),
        })

    filtered_accessions = {
        str(acc).strip()
        for acc in referred_list.values_list("f_AccessionNo", flat=True)
        if acc and str(acc).strip()
    }
    counts = {
        "total": paginator.count,
        "bactscout": len(wgs_sets["bactscout"] & filtered_accessions),
        "mlst": len(wgs_sets["mlst"] & filtered_accessions),
        "checkm2": len(wgs_sets["checkm2"] & filtered_accessions),
        "assembly": len(wgs_sets["assembly"] & filtered_accessions),
        "gambit": len(wgs_sets["gambit"] & filtered_accessions),
        "amrfinder": len(wgs_sets["amrfinder"] & filtered_accessions),
        "custom": len(selected_custom_accessions & filtered_accessions),
        "with_antibiotics": Final_AntibioticEntry.objects.filter(
            ab_idNum_f_referred__f_AccessionNo__in=filtered_accessions
        ).values("ab_idNum_f_referred__f_AccessionNo").distinct().count(),
    }

    params = request.GET.copy()
    params.pop("page", None)
    preserved_params = params.urlencode()

    return render(request, "wgs_app/Wgs_overview.html", {
        "table_data": table_data,
        "page_obj": page_obj,
        "counts": counts,
        "q": q,
        "available_years": available_years,
        "selected_year": selected_year,
        "available_organisms": available_organisms,
        "selected_organism": selected_organism,
        "preserved_params": preserved_params,
        "custom_overview_pipelines": custom_overview_pipelines,
        "selected_custom_pipelines": selected_custom_pipelines,
        "selected_custom_slugs": selected_custom_slugs,
        "visible_builtin_overview_keys": visible_builtin_overview_keys,
        "detail_colspan": len(visible_builtin_overview_keys & set(overview_source_map.keys())) + len(selected_custom_pipelines) + 2,
    })




# View WGS overview with antibiotic entries but optimized to reduce queries
# Shows isolates that have WGS data in ANY of the WGS tables

@login_required
def get_wgs_details(request, accession):
    print(f"\n=== FETCHING DETAILS FOR ACCESSION: {accession} ===")

    # Fetch the referred isolate
    referred = Final_Data.objects.filter(f_AccessionNo=accession).first()
    if not referred:
        return JsonResponse({"error": "Accession not found."}, status=404)

    # ============================================================
    # ✔ FIX: Convert antibiotic entries to SAFE dictionary format
    # ============================================================
    antibiotics_qs = Final_AntibioticEntry.objects.filter(
        ab_idNum_f_referred__f_AccessionNo=accession
    ).only(
        "ab_Abx_code",
        "ab_Disk_value", "ab_Disk_enRIS",
        "ab_MIC_value", "ab_MIC_enRIS",
        "ab_MIC_operand",
    )

    antibiotics = []
    for ab in antibiotics_qs:
        if not has_tested_antibiotic_value(ab.ab_Disk_value, ab.ab_MIC_value):
            continue
        antibiotics.append({
            "code": ab.ab_Abx_code,

            # Disk
            "disk": ab.ab_Disk_value or "",
            "d_ris": ab.ab_Disk_enRIS or "",

            # MIC
            "mic": ab.ab_MIC_value or "",
            "m_ris": ab.ab_MIC_enRIS or "",
            "m_op": ab.ab_MIC_operand or "",
        })

    print(f" Found {len(antibiotics)} antibiotic entries (SAFE FORMAT)")

    classification = (
        Classification_Table.objects
        .filter(Class_idNumReferred=referred)
        .first()
    )
    classification_items = []
    if classification:
        classification_labels = [
            ("Emerging", classification.Class_Chk_Emerging),
            ("Structured", classification.Class_Chk_Structured),
            ("SatScan", classification.Class_Chk_Satscan),
            ("Serotyping", classification.Class_Chk_Serotyping),
            ("GHRU All", classification.Class_Chk_GHRU_all),
            ("GHRU Neo", classification.Class_Chk_GHRU_Neo),
            ("EGASP", classification.Class_Chk_EGASP),
            ("Tricycle", classification.Class_Chk_Tricycle),
            ("PulseNet", classification.Class_Chk_Pulsenet),
            ("TULIP", classification.Class_Chk_Tulip),
        ]
        classification_items = [
            {"label": label}
            for label, active in classification_labels
            if active
        ]

    sample_information = (
        SampleInformation.objects
        .filter(sample_accession=accession)
        .order_by("-Date_uploaded_si", "-pk")
        .first()
    )

    # ============================================================
    # WGS Related Data
    # ============================================================
    visible_builtin_overview_keys = visible_builtin_wgs_keys("overview")
    requested_builtin_keys = request.GET.getlist("builtin_tables")
    if requested_builtin_keys:
        selected_builtin_detail_keys = set(requested_builtin_keys) & set(visible_builtin_overview_keys)
    else:
        selected_builtin_detail_keys = set(visible_builtin_overview_keys)
    projects = WGS_Project.objects.filter(
        Q(WGS_BactScout_Acc=accession)
        | Q(WGS_Mlst_Acc=accession)
        | Q(WGS_Checkm2_Acc=accession)
        | Q(WGS_Assembly_Acc=accession)
        | Q(WGS_Gambit_Acc=accession)
        | Q(WGS_Amrfinder_Acc=accession)
    ).distinct()

    related_data = {
        "bactscout": list(BactScout.objects.filter(
            Q(bactscout_project__in=projects) | Q(BactScout_Accession=accession)
        )),
        "mlst": list(Mlst.objects.filter(
            Q(mlst_project__in=projects) | Q(mlst_project__WGS_Mlst_Acc=accession)
        )),
        "checkm2": list(Checkm2.objects.filter(
            Q(checkm2_project__in=projects) | Q(checkm2_project__WGS_Checkm2_Acc=accession)
        )),
        "assembly": list(AssemblyScan.objects.filter(
            Q(assembly_project__in=projects) | Q(assembly_project__WGS_Assembly_Acc=accession)
        )),
        "gambit": list(Gambit.objects.filter(
            Q(gambit_project__in=projects) | Q(Gambit_Accession=accession)
        )),
        "amrfinder": list(Amrfinderplus.objects.filter(
            Q(amrfinder_project__in=projects) | Q(amrfinder_project__WGS_Amrfinder_Acc=accession)
        )),
    }
    for key in list(related_data.keys()):
        if key not in selected_builtin_detail_keys:
            related_data[key] = []
    requested_custom_slugs = request.GET.getlist("custom_tables")
    custom_pipeline_filter = CustomWGSPipeline.objects.filter(is_active=True, show_in_overview=True)
    if requested_custom_slugs:
        custom_pipeline_filter = custom_pipeline_filter.filter(slug__in=requested_custom_slugs)
    custom_related_data = []
    accession_variants = custom_pipeline_accession_variants(accession)
    custom_accession_filter = Q(matched_final_data__f_AccessionNo__iexact=accession)
    for accession_variant in accession_variants:
        custom_accession_filter |= Q(accession__iexact=accession_variant)
    for pipeline in custom_pipeline_filter.prefetch_related("fields").order_by("sequencing_type", "name"):
        records = list(
            CustomWGSPipelineRecord.objects
            .filter(pipeline=pipeline, match_status="matched")
            .filter(custom_accession_filter)
            .order_by("-uploaded_at", "id")
        )
        if records:
            custom_related_data.append({
                "pipeline": pipeline,
                "fields": list(pipeline.fields.filter(show_in_detail=True)),
                "records": records,
            })

    # ============================================================
    # Build context required by the template
    # ============================================================
    context = {
        "entry": {
            "accession": accession,
            "patient_id": referred.f_Patient_ID,
            "patient_name": f"{referred.f_Last_Name}, {referred.f_First_Name} {referred.f_Mid_Name or ''}".strip(),
            "age": referred.f_Age,
            "sex": referred.f_Sex,
            "ward": referred.f_Ward,
            "specimen": referred.f_Spec_Type,
            "organism": referred.f_ars_OrgCode,
            "sitecode": referred.f_SiteCode,
            "diagnosis": referred.f_Diagnosis_ICD10,
            "growth": referred.f_Growth,
            "date_collected": referred.f_Spec_Date,
            "referral_date": referred.f_Referral_Date,
            "classification": classification,
            "classification_items": classification_items,
            "sample_information": sample_information,
            "antibiotics": antibiotics,  
            "related_data": related_data,
            "custom_related_data": custom_related_data,
            "bactscout_fields": BactScout.FIELD_LABELS,
            "gambit_fields": Gambit.FIELD_LABELS,
            "mlst_fields": Mlst.FIELD_LABELS,
            "checkm2_fields": Checkm2.FIELD_LABELS,
            "amrfinder_fields": Amrfinderplus.FIELD_LABELS,
        }
    }

    # Render HTML for AJAX response
    html = render_to_string(
        "wgs_app/Wgs_detail.html",
        context,
        request=request,
    )

    return JsonResponse({"html": html})




@login_required
def download_matched_wgs_data(request):
    """
    Export Final_Data + Antibiotic results (MIC, Disk, RIS)
    along with WGS data (BactScout, MLST, CheckM2, Assembly, AMRFinder, Gambit).

    Mode options:
        ?mode=all  → Complete sets (present in ALL WGS tables)
        ?mode=any  → Partial sets (present in ANY WGS table)
    """
    import io
    import pandas as pd
    from django.http import HttpResponse

    mode = request.GET.get("mode", "any").lower()
    selected_year = request.GET.get("year", "").strip()

    def request_date(value):
        parsed = pd.to_datetime(value or "", errors="coerce")
        if pd.isna(parsed):
            return None
        return parsed.date()

    date_from = request_date(request.GET.get("date_from"))
    date_to = request_date(request.GET.get("date_to"))

    def normalize_accession(value):
        return str(value or "").strip().upper()

    final_filter = (
        Final_Data.objects
        .exclude(f_AccessionNo__isnull=True)
        .exclude(f_AccessionNo="")
    )
    if selected_year:
        final_filter = final_filter.filter(f_Referral_Date__year=selected_year)
    if date_from:
        final_filter = final_filter.filter(f_Referral_Date__gte=date_from)
    if date_to:
        final_filter = final_filter.filter(f_Referral_Date__lte=date_to)

    final_acc_map = {
        normalize_accession(acc): acc
        for acc in final_filter.values_list("f_AccessionNo", flat=True)
        if normalize_accession(acc)
    }

    visible_builtin_overview_keys = visible_builtin_wgs_keys("overview")
    download_source_map = {
        "bactscout": (BactScout, "BactScout_Accession", "Date_uploaded_bs"),
        "mlst": (Mlst, "Mlst_Accession", "Date_uploaded_m"),
        "checkm2": (Checkm2, "Checkm2_Accession", "Date_uploaded_c"),
        "assembly": (AssemblyScan, "Assembly_Accession", "Date_uploaded_as"),
        "amrfinder": (Amrfinderplus, "Amrfinder_Accession", "Date_uploaded_am"),
        "gambit": (Gambit, "Gambit_Accession", "Date_uploaded_g"),
    }
    wgs_sources = [
        source
        for key, source in download_source_map.items()
        if key in visible_builtin_overview_keys
    ]

    def wgs_accession_set(model, accession_field):
        qs = model.objects.exclude(**{f"{accession_field}__isnull": True}).exclude(
            **{accession_field: ""}
        )
        return {
            normalized
            for acc in qs.values_list(accession_field, flat=True).distinct()
            for normalized in [normalize_accession(acc)]
            if normalized in final_acc_map
        }

    def collect_wgs_sets():
        return [
            wgs_accession_set(model, accession_field)
            for model, accession_field, _date_field in wgs_sources
        ]

    def custom_download_accession_set():
        custom_filter = CustomWGSPipelineRecord.objects.filter(
            pipeline__is_active=True,
            pipeline__show_in_overview=True,
            match_status="matched",
        )

        accessions = set()
        for row in custom_filter.values("accession", "matched_final_data_id").distinct():
            for key in custom_record_accession_keys(row):
                normalized = normalize_accession(key)
                if normalized in final_acc_map:
                    accessions.add(normalized)
        return accessions

    # ---- Step 1-2: Collect matching Final_Data/WGS accessions ----
    wgs_sets = collect_wgs_sets()
    custom_wgs_set = custom_download_accession_set()
    if custom_wgs_set and mode != "all":
        wgs_sets.append(custom_wgs_set)

    # ---- Step 3: Combine or intersect ----
    if mode == "all":
        matched_accessions = set.intersection(*wgs_sets) if wgs_sets else set()
        filename_suffix = "Complete"
    else:
        matched_accessions = set().union(*wgs_sets) if wgs_sets else set()
        filename_suffix = "Partial"

    date_filter_was_used = bool(date_from or date_to)
    if not matched_accessions and date_filter_was_used:
        fallback_final_filter = (
            Final_Data.objects
            .exclude(f_AccessionNo__isnull=True)
            .exclude(f_AccessionNo="")
        )
        if selected_year:
            fallback_final_filter = fallback_final_filter.filter(
                f_Referral_Date__year=selected_year
            )

        final_acc_map = {
            normalize_accession(acc): acc
            for acc in fallback_final_filter.values_list("f_AccessionNo", flat=True)
            if normalize_accession(acc)
        }

        wgs_sets = collect_wgs_sets()
        custom_wgs_set = custom_download_accession_set()
        if custom_wgs_set and mode != "all":
            wgs_sets.append(custom_wgs_set)
        if mode == "all":
            matched_accessions = set.intersection(*wgs_sets) if wgs_sets else set()
        else:
            matched_accessions = set().union(*wgs_sets) if wgs_sets else set()
        final_filter = fallback_final_filter
        filename_suffix = f"{filename_suffix}_AllDates"

    if not matched_accessions:
        return HttpResponse(
            "No matching WGS accessions found in Final_Data for the selected referral date filter.",
            content_type="text/plain"
        )

    matched_accessions = {
        final_acc_map[normalized]
        for normalized in matched_accessions
        if normalized in final_acc_map
    }

    # ---- Step 4: Query datasets ----
    final_qs = final_filter.filter(f_AccessionNo__in=matched_accessions)
    abx_qs = Final_AntibioticEntry.objects.filter(
        ab_idNum_f_referred__f_AccessionNo__in=matched_accessions
    )
    classification_qs = Classification_Table.objects.filter(
        Class_idNumReferred__f_AccessionNo__in=matched_accessions
    )
    sampleinfo_qs = SampleInformation.objects.filter(
        sample_accession__in=matched_accessions
    ).order_by("sample_accession", "-Date_uploaded_si", "-pk")
    bactscout_qs = BactScout.objects.filter(BactScout_Accession__in=matched_accessions) if "bactscout" in visible_builtin_overview_keys else BactScout.objects.none()
    mlst_qs = Mlst.objects.filter(Mlst_Accession__in=matched_accessions) if "mlst" in visible_builtin_overview_keys else Mlst.objects.none()
    checkm2_qs = Checkm2.objects.filter(Checkm2_Accession__in=matched_accessions) if "checkm2" in visible_builtin_overview_keys else Checkm2.objects.none()
    assembly_qs = AssemblyScan.objects.filter(Assembly_Accession__in=matched_accessions) if "assembly" in visible_builtin_overview_keys else AssemblyScan.objects.none()
    amrfinder_qs = Amrfinderplus.objects.filter(Amrfinder_Accession__in=matched_accessions) if "amrfinder" in visible_builtin_overview_keys else Amrfinderplus.objects.none()
    gambit_qs = Gambit.objects.filter(Gambit_Accession__in=matched_accessions) if "gambit" in visible_builtin_overview_keys else Gambit.objects.none()

    # ---- Step 5: Convert querysets to DataFrames ----
    final_df = pd.DataFrame.from_records(final_qs.values())
    abx_df = pd.DataFrame.from_records(abx_qs.values())
    classification_fields = [
        "Class_idNumReferred_id",
        "Class_AccessionNo",
        "Class_Chk_Emerging",
        "Class_Chk_Structured",
        "Class_Chk_Satscan",
        "Class_Chk_Serotyping",
        "Class_Chk_GHRU_all",
        "Class_Chk_GHRU_Neo",
        "Class_Chk_EGASP",
        "Class_Chk_Tricycle",
        "Class_Chk_Pulsenet",
        "Class_Chk_Tulip",
    ]
    classification_export_labels = [
        ("Class_Chk_Emerging", "Emerging"),
        ("Class_Chk_Structured", "Structured"),
        ("Class_Chk_Satscan", "SatScan"),
        ("Class_Chk_Serotyping", "Serotyping"),
        ("Class_Chk_GHRU_all", "GHRU All"),
        ("Class_Chk_GHRU_Neo", "GHRU Neo"),
        ("Class_Chk_EGASP", "EGASP"),
        ("Class_Chk_Tricycle", "Tricycle"),
        ("Class_Chk_Pulsenet", "PulseNet"),
        ("Class_Chk_Tulip", "TULIP"),
    ]
    classification_df = pd.DataFrame.from_records(
        classification_qs.values(*classification_fields)
    )
    sampleinfo_fields = [
        "sample_accession",
        "batch_code",
        "sample_name",
        "status",
        "DNA_extraction",
        "dna_performed_by",
        "dna_performed_date",
        "library_preparation",
        "library_performed_by",
        "library_performed_date",
        "sequencing_platform",
        "Date_uploaded_si",
        "id",
    ]
    sampleinfo_df = pd.DataFrame.from_records(sampleinfo_qs.values(*sampleinfo_fields))

    def qs_to_df(qs, model_name, acc_field):
        if not qs.exists():
            return pd.DataFrame()
        df = pd.DataFrame.from_records(qs.values())
        df.insert(0, "Table", model_name)
        df.insert(1, "f_AccessionNo", df[acc_field])
        return df

    bactscout_df = qs_to_df(bactscout_qs, "BactScout", "BactScout_Accession")
    if not bactscout_df.empty:
        bactscout_export_columns = [
            "Table",
            "f_AccessionNo",
            "BactScout_Accession",
            *BactScout.UPLOAD_FIELDS,
            "Date_uploaded_bs",
        ]
        bactscout_df = bactscout_df[
            [column for column in bactscout_export_columns if column in bactscout_df.columns]
        ]
    mlst_df = qs_to_df(mlst_qs, "Mlst", "Mlst_Accession")
    if not mlst_df.empty:
        mlst_export_columns = [
            "Table",
            "f_AccessionNo",
            "Mlst_Accession",
            *Mlst.UPLOAD_FIELDS,
            "Date_uploaded_m",
        ]
        mlst_df = mlst_df[
            [column for column in mlst_export_columns if column in mlst_df.columns]
        ]
    checkm2_df = qs_to_df(checkm2_qs, "Checkm2", "Checkm2_Accession")
    if not checkm2_df.empty:
        checkm2_export_columns = [
            "Table",
            "f_AccessionNo",
            "Checkm2_Accession",
            *Checkm2.UPLOAD_FIELDS,
            "Date_uploaded_c",
        ]
        checkm2_df = checkm2_df[
            [column for column in checkm2_export_columns if column in checkm2_df.columns]
        ]
    assembly_df = qs_to_df(assembly_qs, "AssemblyScan", "Assembly_Accession")
    amrfinder_df = qs_to_df(amrfinder_qs, "Amrfinderplus", "Amrfinder_Accession")
    if not amrfinder_df.empty:
        amrfinder_export_columns = [
            "Table",
            "f_AccessionNo",
            "Amrfinder_Accession",
            *Amrfinderplus.UPLOAD_FIELDS,
            "Date_uploaded_am",
        ]
        amrfinder_df = amrfinder_df[
            [column for column in amrfinder_export_columns if column in amrfinder_df.columns]
        ]
    gambit_df = qs_to_df(gambit_qs, "Gambit", "Gambit_Accession")
    if not gambit_df.empty:
        gambit_export_columns = [
            "Table",
            "f_AccessionNo",
            "Gambit_Accession",
            *Gambit.UPLOAD_FIELDS,
            "Date_uploaded_g",
        ]
        gambit_df = gambit_df[
            [column for column in gambit_export_columns if column in gambit_df.columns]
        ]

    final_df["id"] = final_df["id"].astype(str)
    if not classification_df.empty:
        classification_df["Classification_Summary"] = classification_df.apply(
            lambda row: ", ".join(
                label
                for field, label in classification_export_labels
                if bool(row.get(field))
            ),
            axis=1,
        )
        classification_df["Class_idNumReferred_id"] = classification_df[
            "Class_idNumReferred_id"
        ].astype(str)
        combined_classification_df = classification_df.rename(
            columns={"Class_idNumReferred_id": "id"}
        )
        final_df = final_df.merge(
            combined_classification_df,
            on="id",
            how="left",
        )

    sampleinfo_export_columns = [
        "SampleInfo_batch_code",
        "SampleInfo_sample_name",
        "SampleInfo_status",
        "SampleInfo_DNA_extraction",
        "SampleInfo_dna_performed_by",
        "SampleInfo_dna_performed_date",
        "SampleInfo_library_preparation",
        "SampleInfo_library_performed_by",
        "SampleInfo_library_performed_date",
        "SampleInfo_sequencing_platform",
    ]
    if not sampleinfo_df.empty:
        sampleinfo_df = sampleinfo_df.sort_values(
            ["sample_accession", "Date_uploaded_si", "id"],
            ascending=[True, False, False],
        ).drop_duplicates("sample_accession", keep="first")
        sampleinfo_df = sampleinfo_df.rename(
            columns={
                "sample_accession": "f_AccessionNo",
                "batch_code": "SampleInfo_batch_code",
                "sample_name": "SampleInfo_sample_name",
                "status": "SampleInfo_status",
                "DNA_extraction": "SampleInfo_DNA_extraction",
                "dna_performed_by": "SampleInfo_dna_performed_by",
                "dna_performed_date": "SampleInfo_dna_performed_date",
                "library_preparation": "SampleInfo_library_preparation",
                "library_performed_by": "SampleInfo_library_performed_by",
                "library_performed_date": "SampleInfo_library_performed_date",
                "sequencing_platform": "SampleInfo_sequencing_platform",
            }
        )[["f_AccessionNo", *sampleinfo_export_columns]]
        final_df = final_df.merge(sampleinfo_df, on="f_AccessionNo", how="left")
    else:
        for column in sampleinfo_export_columns:
            final_df[column] = ""

    combined_df = final_df.copy()
    if not abx_df.empty:
        abx_df["ab_idNum_f_referred_id"] = abx_df["ab_idNum_f_referred_id"].astype(str)
        abx_df = abx_df.merge(
            final_df[["id", "f_AccessionNo"]],
            left_on="ab_idNum_f_referred_id",
            right_on="id",
            how="left"
        )

        def pivot_antibiotic(df, value_field, suffix):
            pivot = df.pivot_table(
                index="f_AccessionNo",
                columns="ab_Abx_code",
                values=value_field,
                aggfunc="first"
            )
            pivot.columns = [f"{col}_{suffix}" for col in pivot.columns]
            return pivot

        abx_mic_val = pivot_antibiotic(abx_df, "ab_MIC_value", "MIC")
        abx_mic_ris = pivot_antibiotic(abx_df, "ab_MIC_RIS", "MIC_RIS")
        abx_mic_enris = pivot_antibiotic(abx_df, "ab_MIC_enRIS", "MIC_enRIS")
        abx_disk_val = pivot_antibiotic(abx_df, "ab_Disk_value", "Disk")
        abx_disk_ris = pivot_antibiotic(abx_df, "ab_Disk_RIS", "Disk_RIS")
        abx_disk_enris = pivot_antibiotic(abx_df, "ab_Disk_enRIS", "Disk_enRIS")

        abx_pivot = pd.concat(
            [
                abx_mic_val,
                abx_mic_ris,
                abx_mic_enris,
                abx_disk_val,
                abx_disk_ris,
                abx_disk_enris,
            ],
            axis=1
        )
        abx_pivot.reset_index(inplace=True)
        combined_df = final_df.merge(abx_pivot, on="f_AccessionNo", how="left")

    # ---- Step 7: Make datetimes timezone-naive ----
    def make_tz_naive(df):
        if df.empty:
            return df
        for col in df.select_dtypes(include=["datetimetz", "datetime"]).columns:
            df[col] = pd.to_datetime(df[col], errors="coerce").dt.tz_localize(None)
            df[col] = df[col].dt.strftime("%Y-%m-%d")
        return df

    combined_df = make_tz_naive(combined_df)

    custom_pipeline_exports = []
    custom_export_pipelines = (
        CustomWGSPipeline.objects
        .filter(is_active=True, show_in_overview=True)
        .prefetch_related("fields")
        .order_by("sequencing_type", "name")
    )
    matched_accession_lookup = {normalize_accession(accession): accession for accession in matched_accessions}
    used_sheet_names = {
        "Final_Data_With_Antibiotics",
        "BactScout",
        "MLST",
        "CheckM2",
        "Assembly",
        "AMRFinder",
        "Gambit",
    }

    def excel_sheet_name(value):
        base = re.sub(r"[\[\]\:\*\?\/\\]", "_", str(value or "Custom")).strip() or "Custom"
        base = base[:31]
        candidate = base
        counter = 2
        while candidate in used_sheet_names:
            suffix = f"_{counter}"
            candidate = f"{base[:31 - len(suffix)]}{suffix}"
            counter += 1
        used_sheet_names.add(candidate)
        return candidate

    for pipeline in custom_export_pipelines:
        fields = list(pipeline.fields.all())
        records = list(
            CustomWGSPipelineRecord.objects
            .filter(pipeline=pipeline, match_status="matched")
            .filter(
                Q(matched_final_data_id__in=matched_accessions)
                | Q(accession__in=matched_accessions)
            )
            .order_by("accession", "-uploaded_at", "id")
        )
        custom_rows = []
        for record in records:
            normalized_keys = [
                normalize_accession(key)
                for key in custom_record_accession_keys({
                    "accession": record.accession,
                    "matched_final_data_id": record.matched_final_data_id,
                })
            ]
            canonical_accession = next(
                (matched_accession_lookup[key] for key in normalized_keys if key in matched_accession_lookup),
                record.matched_final_data_id or record.accession,
            )
            export_row = {
                "f_AccessionNo": canonical_accession,
                "Uploaded accession": record.accession,
                "Sample name": record.sample_name,
                "Match source": record.match_source,
                "Uploaded at": record.uploaded_at,
            }
            for field in fields:
                export_row[field.display_label] = (record.values_json or {}).get(field.field_key, "")
            custom_rows.append(export_row)
        if custom_rows:
            custom_pipeline_exports.append((excel_sheet_name(pipeline.name), pd.DataFrame(custom_rows)))

    # ---- Step 8: Write all to Excel ----
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        combined_df.to_excel(writer, index=False, sheet_name="Final_Data_With_Antibiotics")
        if not bactscout_df.empty: bactscout_df.to_excel(writer, index=False, sheet_name="BactScout")
        if not mlst_df.empty: mlst_df.to_excel(writer, index=False, sheet_name="MLST")
        if not checkm2_df.empty: checkm2_df.to_excel(writer, index=False, sheet_name="CheckM2")
        if not assembly_df.empty: assembly_df.to_excel(writer, index=False, sheet_name="Assembly")
        if not amrfinder_df.empty: amrfinder_df.to_excel(writer, index=False, sheet_name="AMRFinder")
        if not gambit_df.empty: gambit_df.to_excel(writer, index=False, sheet_name="Gambit")
        for sheet_name, custom_df in custom_pipeline_exports:
            custom_df = make_tz_naive(custom_df)
            custom_df.to_excel(writer, index=False, sheet_name=sheet_name)

    output.seek(0)
    filename = f"FinalData_WGS_{filename_suffix}_{pd.Timestamp.now().date()}.xlsx"

    response = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
def projects_page(request):

    active_tab = request.GET.get("tab", "wgs_classification")

    referred_list = list(
        Final_Data.objects
        .all()
        .order_by("-id")
    )
    referred_accessions = [
        item.f_AccessionNo for item in referred_list if item.f_AccessionNo
    ]
    sampleinfo_by_accession = {}
    for item in (
        SampleInformation.objects
        .filter(sample_accession__in=referred_accessions)
        .order_by("sample_accession", "-Date_uploaded_si", "-pk")
    ):
        sampleinfo_by_accession.setdefault(item.sample_accession, item)
    for referred in referred_list:
        referred.sampleinfo_flags = sampleinfo_by_accession.get(referred.f_AccessionNo)

    context = {
        "active_tab":      active_tab,
        "referred_list":   referred_list,

        # WGS upload forms — passed to the modal forms rendered inside the tab
        "bactscout_form":  BactScoutUploadForm(),
        "gambit_form":     GambitUploadForm(),
        "mlst_form":       MlstUploadForm(),
        "checkm2_form":    Checkm2UploadForm(),
        "assembly_form":   AssemblyUploadForm(),
        "amrfinder_form":  AmrUploadForm(),
    }
    return render(request, "projects/projects.html", context)





##############################  New Upload Final View HELPERS




# ─────────────────────────────────────────────────────────────────────────────
# MAIN UPLOAD VIEW
# ─────────────────────────────────────────────────────────────────────────────
#######  refrain from using this until i polished it
@login_required
@role_required(ROLE_ADMIN, ROLE_CHECKER, ROLE_ENCODER)
@require_POST
def upload_final_data(request):
    """
    POST — receives a multipart CSV, TSV, or Excel file and saves rows into
    Final_Data and Final_AntibioticEntry (retest antibiotic entries only).

    Returns JSON: { success, created, updated, skipped, errors }
    """
    f = request.FILES.get("final_data_file")
    if not f:
        return JsonResponse({"success": False, "error": "No file uploaded."}, status=400)

    overwrite = request.POST.get("overwrite", "false").lower() == "true"

    try:
        file_name = f.name.lower()
        wb = None
        if file_name.endswith(".csv"):
            df = pd.read_csv(f, dtype=object)
            sheet_rows = [(f.name, [tuple(df.columns), *df.itertuples(index=False, name=None)])]
        elif file_name.endswith(".tsv"):
            df = pd.read_csv(f, sep="\t", dtype=object)
            sheet_rows = [(f.name, [tuple(df.columns), *df.itertuples(index=False, name=None)])]
        elif file_name.endswith((".xlsx", ".xls")):
            wb = openpyxl.load_workbook(f, read_only=True, data_only=True)
            sheet_rows = [
                (sheet_name, list(wb[sheet_name].iter_rows(values_only=True)))
                for sheet_name in wb.sheetnames
            ]
        else:
            return JsonResponse(
                {"success": False, "error": "Unsupported file format. Please upload CSV, TSV, XLSX, or XLS."},
                status=400,
            )
    except Exception as e:
        return JsonResponse({"success": False, "error": f"Cannot open file: {e}"}, status=400)

    results = {"created": 0, "updated": 0, "skipped": 0, "errors": []}

    for sheet_name, rows in sheet_rows:
        if not rows:
            continue

        headers = rows[0]
        h_lower = [str(c).lower().strip() if c else "" for c in headers]
        col     = {name: idx for idx, name in enumerate(h_lower) if name}

        accession_col = next(
            (
                key for key in (
                    "accession_no",
                    "accession",
                    "f_accessionno",
                    "f_accession_no",
                )
                if key in col
            ),
            None,
        )

        if not accession_col:
            continue

        # Detect retest antibiotic column groups once per sheet
        abx_groups = _parse_abx_columns(headers)

        for row_num, row in enumerate(rows[1:], start=2):

            accession_no = _clean(row[col[accession_col]])
            if not accession_no:
                continue

            # ── Helper: safe column read ──────────────────────────────────────
            def g(key, default=""):
                return _clean(row[col[key]], default) if key in col else default

            def first_present(*keys):
                for key in keys:
                    if key in col:
                        return key
                return None

            def add_text(target, *keys, default=""):
                key = first_present(*keys)
                if key:
                    fd_kwargs[target] = _clean(row[col[key]], default)

            def add_date(target, *keys):
                key = first_present(*keys)
                if key:
                    fd_kwargs[target] = _date(row[col[key]])

            def add_int(target, *keys):
                key = first_present(*keys)
                if key:
                    fd_kwargs[target] = _int(row[col[key]])

            # ── Final_Data fields ─────────────────────────────────────────────
            fd_kwargs = {"f_AccessionNo": accession_no}
            add_text("f_Batch_Code", "batch_code", "f_batch_code")
            add_text("f_Batch_Name", "batch_name", "f_batch_name")
            add_text("f_SiteCode", "site_code", "sitecode", "f_sitecode")
            add_text("f_BatchNo", "batchno", "batch_no", "f_batchno")
            add_text("f_Total_batch", "total_batch", "f_total_batch")
            add_text("f_RefNo", "refno", "ref_no", "f_refno")
            add_date("f_Referral_Date", "referral_date", "f_referral_date")
            add_text("f_Patient_ID", "patient_id", "f_patient_id")
            add_text("f_First_Name", "first_name", "f_first_name")
            add_text("f_Mid_Name", "mid_name", "middle_name", "f_mid_name")
            add_text("f_Last_Name", "last_name", "f_last_name")
            add_date("f_Date_Birth", "date_birth", "birth_date", "f_date_birth")
            add_int("f_Age", "age", "f_age")
            add_text("f_Sex", "sex", "f_sex", default="n/a")
            add_date("f_Date_Admis", "date_admis", "admission_date", "f_date_admis")
            add_text("f_Nosocomial", "nosocomial", "f_nosocomial", default="n/a")
            add_text("f_Diagnosis", "diagnosis", "f_diagnosis")
            add_text("f_Diagnosis_ICD10", "diagnosis_icd10", "f_diagnosis_icd10")
            add_text("f_Ward", "ward", "f_ward")
            add_text("f_Ward_Type", "ward_type", "f_ward_type")
            add_text("f_Service_Type", "service_type", "f_service_type", default="n/a")
            add_text("f_Spec_Num", "spec_num", "f_spec_num")
            add_date("f_Spec_Date", "spec_date", "specimen_date", "year", "f_spec_date")
            add_text("f_Reason", "reason", "f_reason", default="n/a")
            add_text("f_Growth", "growth", "f_growth")
            add_text("f_Urine_ColCt", "urine_colct", "f_urine_colct")
            add_text("f_ars_Pre", "arsrl_pre", "ars_pre", "f_ars_pre")
            add_text("f_ars_OrgName", "arsrl_org", "ars_org", "f_ars_orgname")
            add_text("f_ars_OrgCode", "organismcode", "organism_code", "ars_org_code", "f_ars_orgcode")
            add_text("f_ars_Post", "arsrl_post", "ars_post", "f_ars_post")
            add_text("f_x_mrse", "x_mrse", "f_x_mrse")
            add_text("f_x_mrsamrse", "x_mrsamrse", "f_x_mrsamrse")
            add_text("f_x_entbac", "x_entbac", "f_x_entbac")
            add_text("f_edta", "edta", "f_edta")
            add_text("f_ars_ct_ctl", "ct.ctl", "ct_ctl", "f_ars_ct_ctl")
            add_text("f_ars_tz_tzl", "tz.tzl", "tz_tzl", "f_ars_tz_tzl")
            add_text("f_ars_cn_cni", "cn.cni", "cn_cni", "f_ars_cn_cni")
            add_text("f_ars_ip_ipi", "ip.ipi", "ip_ipi", "f_ars_ip_ipi")
            add_text("f_Comments", "comments", "f_comments")
            add_text("f_ars_reco", "recommendation", "ars_reco", "f_ars_reco")

            # Phenotype fields
            for excel_key, model_field in PHENO_FIELD_MAP.items():
                if excel_key in col:
                    fd_kwargs[model_field] = _pheno(row[col[excel_key]])
            for excel_key, model_field in SITE_PHENO_MAP.items():
                if excel_key in col:
                    fd_kwargs[model_field] = _pheno(row[col[excel_key]])

            # Resolve SpecimenType FK
            spec_type_key = first_present("spec_type", "f_spec_type")
            spec_type_code = _clean(row[col[spec_type_key]]) if spec_type_key else ""
            if spec_type_key and spec_type_code:
                fd_kwargs["f_Spec_Type"] = SpecimenTypeModel.objects.filter(
                    Specimen_code__iexact=spec_type_code
                ).first()

            # Resolve Batch FK
            if fd_kwargs.get("f_Batch_Code"):
                batch_obj = Batch_Table.objects.filter(
                    bat_Batch_Code=fd_kwargs["f_Batch_Code"]
                ).first()
                if batch_obj:
                    fd_kwargs["f_Batch_id"] = batch_obj

            # ── Persist Final_Data ────────────────────────────────────────────
            try:
                with transaction.atomic():
                    existing = Final_Data.objects.filter(
                        f_AccessionNo=accession_no
                    ).first()

                    if existing and not overwrite:
                        results["skipped"] += 1
                        continue

                    if existing:
                        for k, v in fd_kwargs.items():
                            if k != "f_AccessionNo":
                                setattr(existing, k, v)
                        update_fields = [
                            k for k in fd_kwargs
                            if k != "f_AccessionNo"
                        ]
                        if update_fields:
                            existing.save(update_fields=update_fields)
                            results["updated"] += 1
                        else:
                            results["skipped"] += 1
                        isolate = existing
                    else:
                        isolate = Final_Data(**fd_kwargs)
                        isolate.save()
                        results["created"] += 1

                    # ── Effective breakpoint year (same logic as edit view) ────
                    effective_year   = _resolve_effective_year(isolate.f_Spec_Date)
                    # ── ARSRL organism code for breakpoint lookup ─────────────
                    resolved_ars_org = (isolate.f_ars_OrgCode or "").strip()

                    # ── Remove old retest entries on overwrite ────────────────
                    if existing and overwrite and not partial_update:
                        Final_AntibioticEntry.objects.filter(
                            ab_idNum_f_referred=isolate,
                            ab_Retest_Abx_code__isnull=False,
                        ).exclude(ab_Retest_Abx_code="").delete()

                    # ── Save one retest entry per antibiotic with data ─────────
                    for abx_code, grp in abx_groups.items():

                        disk_raw = row[grp['disk_col']]  if grp['disk_col']  is not None else None
                        disk_ris = _clean(row[grp['disk_ris_col']]) if grp['disk_ris_col'] is not None else ""
                        mic_raw  = row[grp['mic_col']]   if grp['mic_col']   is not None else None
                        mic_ris  = _clean(row[grp['mic_ris_col']])  if grp['mic_ris_col']  is not None else ""

                        # Skip entirely empty antibiotic columns
                        if all(
                            v is None or _clean(v) == ""
                            for v in [disk_raw, disk_ris, mic_raw, mic_ris]
                        ):
                            continue

                        disk_int              = _int(disk_raw)
                        mic_value, mic_operand = _decimal(mic_raw)

                        # Nothing numeric to save
                        if disk_int is None and mic_value is None:
                            continue

                        _save_retest_entry(
                            isolate          = isolate,
                            abx_code         = abx_code,
                            disk_int         = disk_int,
                            disk_ris         = disk_ris,
                            mic_value        = mic_value,
                            mic_operand      = mic_operand,
                            mic_ris          = mic_ris,
                            resolved_ars_org = resolved_ars_org,
                            effective_year   = effective_year,
                        )

            except Exception as e:
                results["errors"].append({
                    "row":       row_num,
                    "accession": accession_no,
                    "sheet":     sheet_name,
                    "error":     str(e),
                })

    if wb:
        wb.close()
    results["success"] = True
    return JsonResponse(results)






########### uploading of Final Data - Demographics 


## new version aligns with the new final data model


def parse_int(val):

    if val is None:
        return None

    s = str(val).strip().lower()

    if s in {"", "nan", "none"}:
        return None

    # extract numeric portion (10d → 10)
    match = re.search(r"\d+", s)

    if match:
        return int(match.group())

    return None



# @login_required
# @transaction.atomic
# def upload_final_combined_table(request):



#     if request.method == "POST" and request.FILES.get("DemogsDataFile"):


#         try:

#             uploaded_file = request.FILES["DemogsDataFile"]
#             file_name = uploaded_file.name.lower()

#             # ================= LOAD FILE =================
#             if file_name.endswith(".csv"):
#                 df = pd.read_csv(uploaded_file)

#             elif file_name.endswith((".xlsx", ".xls")):
#                 df = pd.read_excel(uploaded_file)

#             else:
#                 messages.error(request, "Unsupported file format.")
#                 return redirect("upload_final_combined_table")

#             # ================= PREP DATA =================
#             df.columns = [str(c).strip() for c in df.columns]
#             rows = df.to_dict("records")

#             model_fields = {f.name for f in Final_Data._meta.fields}

#             IMMUTABLE_FIELDS = {
#                 "f_Date_of_Entry",
#                 "f_Date_Modified",
#             }

#             INT_FIELDS = {
#                 "f_Age",
#                 "f_bat_seq",
#             }

#             created = 0
#             updated = 0

#             # ================= FK LOOKUP CACHE =================
#             specimen_map = {
#                 str(s.Specimen_code).strip().lower(): s
#                 for s in SpecimenTypeModel.objects.all()
#             }

#             site_map = {
#                 str(s.SiteCode).strip().upper(): s.SiteName
#                 for s in SiteData.objects.all()
#             }

#             # ================= HELPERS =================
#             def parse_date(val):

#                 if not val or str(val).lower() in {"nan", "nat", "none", ""}:
#                     return None

#                 dt = pd.to_datetime(val, errors="coerce")
#                 return None if pd.isna(dt) else dt.date()

#             # ================= PROCESS ROWS =================
#             for row in rows:

#                 accession = str(row.get("f_AccessionNo", "")).strip()
#                 batch_code = str(row.get("f_Batch_Code", "")).strip()

#                 if not accession or not batch_code:
#                     continue

#                 # ---- normalize accession
#                 accession = accession.upper()

#                 # ---- parse date fields
#                 for d in (
#                     "f_Referral_Date",
#                     "f_Spec_Date",
#                     "f_Date_Birth",
#                     "f_Date_Admis",
#                 ):
#                     if d in row:
#                         row[d] = parse_date(row[d])

#                 # ---- keep ONLY Final_Data fields
#                 clean_row = {
#                     k: v
#                     for k, v in row.items()
#                     if k in model_fields and k not in IMMUTABLE_FIELDS
#                 }

#                 # ---- normalize integers
#                 for f in INT_FIELDS:
#                     if f in clean_row:
#                         clean_row[f] = parse_int(clean_row[f])

#                 # ================= FK RESOLUTION =================

#                 # Specimen Type FK
#                 if "f_Spec_Type" in clean_row:

#                     raw_val = clean_row["f_Spec_Type"]

#                     if pd.isna(raw_val) or raw_val is None:
#                         clean_row["f_Spec_Type"] = None

#                     else:
#                         spec_val = str(raw_val).strip().lower()
#                         clean_row["f_Spec_Type"] = specimen_map.get(spec_val)

                
#                 # ================= SITE NAME AUTO ASSIGN =================
#                 site_code = str(clean_row.get("f_SiteCode", "")).strip().upper()

#                 if site_code:
#                     clean_row["f_Site_Name"] = site_map.get(site_code, "")


#                 # NEVER assign batch FK directly
#                 clean_row.pop("f_Batch_id", None)

#                 # ================= UPSERT =================
#                 obj, is_created = Final_Data.objects.update_or_create(
#                     f_AccessionNo=accession,
#                     f_Batch_Code=batch_code,
#                     defaults=clean_row
#                 )

#                 created += int(is_created)
#                 updated += int(not is_created)

#             # ================= SUCCESS =================
#             messages.success(
#                 request,
#                 f"Upload complete! {created} created, {updated} updated."
#             )

#             return redirect("show_final_data")

#         except Exception as e:

#             import traceback
#             traceback.print_exc()

#             messages.error(request, f"Upload failed: {e}")
#             return redirect("upload_final_combined_table")

#     return render(request, "wgs_app/Add_wgs.html")

### HELPER TO RECOGNIZE EITHER DATASET FROM REFERRED_DATA AND FINAL_DATA
def get_value(row, *keys):
    """
    Return first existing value from possible column names
    """
    def normalize_key(value):
        return re.sub(r"[^a-z0-9]", "", str(value).lower())

    for k in keys:
        if k in row and str(row.get(k)).strip() != "":
            return row.get(k)

    normalized_row = {
        normalize_key(k): v
        for k, v in row.items()
    }
    for k in keys:
        normalized = normalize_key(k)
        if normalized in normalized_row and str(normalized_row.get(normalized)).strip() != "":
            return normalized_row.get(normalized)

    return None



# @login_required
# @transaction.atomic
# def upload_final_combined_table(request):

#     if request.method != "POST" or not request.FILES.get("DemogsDataFile"):
#         return render(request, "wgs_app/Add_wgs.html")

#     try:

#         uploaded_file = request.FILES["DemogsDataFile"]
#         file_name = uploaded_file.name.lower()

#         # ================= LOAD FILE =================
#         if file_name.endswith(".csv"):
#             df = pd.read_csv(uploaded_file)

#         elif file_name.endswith((".xlsx", ".xls")):
#             df = pd.read_excel(uploaded_file)

#         else:
#             messages.error(request, "Unsupported file format.")
#             return redirect("upload_final_combined_table")

#         df.columns = [str(c).strip() for c in df.columns]
#         rows = df.to_dict("records")

#         # ================= FIELD DEFINITIONS =================
#         final_fields = {f.name for f in Final_Data._meta.fields}

#         IMMUTABLE_FIELDS = {
#             "f_Date_of_Entry",
#             "f_Date_Modified",
#         }

#         INT_FIELDS = {
#             "f_Age",
#             "f_bat_seq",
#         }

#         # ================= LOOKUP CACHE =================
#         specimen_map = {
#             str(s.Specimen_code).strip().lower(): s
#             for s in SpecimenTypeModel.objects.all()
#         }

#         site_map = {
#             str(s.SiteCode).strip().upper(): s.SiteName
#             for s in SiteData.objects.all()
#         }

#         created = 0
#         updated = 0

#         # ================= PROCESS ROWS =================

#         for row in rows:

#             accession = str(get_value(row, "f_AccessionNo", "AccessionNo") or "").strip().upper()
#             batch_code = str(get_value(row, "f_Batch_Code", "Batch_Code") or "").strip()

#             if not accession:
#                 continue

#             referral_date = parse_date(get_value(row, "f_Referral_Date", "Referral_Date"))
#             spec_date = parse_date(get_value(row, "f_Spec_Date", "Spec_Date"))
#             birth_date = parse_date(get_value(row, "f_Date_Birth", "Date_Birth"))
#             admis_date = parse_date(get_value(row, "f_Date_Admis", "Date_Admis"))

#             site_code = str(get_value(row, "f_SiteCode", "SiteCode") or "").strip().upper()
#             site_name = site_map.get(site_code, "")

#             # ================= REFERRED DATA =================

#             Referred_Data.objects.update_or_create(
#                 AccessionNo=accession,
#                 defaults={
#                     "SiteCode": site_code,
#                     "Site_Name": site_name,
#                     "Referral_Date": referral_date,
#                 }
#             )

#             # ================= PREP FINAL DATA =================

#             clean_row = {}

#             for k, v in row.items():

#                 if k not in final_fields or k in IMMUTABLE_FIELDS:
#                     continue

#                 clean_row[k] = v

#             clean_row["f_AccessionNo"] = accession
#             clean_row["f_Batch_Code"] = batch_code
#             clean_row["f_Referral_Date"] = referral_date
#             clean_row["f_Spec_Date"] = spec_date
#             clean_row["f_Date_Birth"] = birth_date
#             clean_row["f_Date_Admis"] = admis_date
#             clean_row["f_SiteCode"] = site_code
#             clean_row["f_Site_Name"] = site_name

#             # normalize integers
#             for field in INT_FIELDS:
#                 if field in clean_row:
#                     clean_row[field] = parse_int(clean_row[field])

#             # specimen FK
#             if "f_Spec_Type" in clean_row:

#                 raw_val = clean_row["f_Spec_Type"]

#                 if pd.isna(raw_val) or raw_val is None:
#                     clean_row["f_Spec_Type"] = None
#                 else:
#                     spec_val = str(raw_val).strip().lower()
#                     clean_row["f_Spec_Type"] = specimen_map.get(spec_val)

#             # ================= FINAL DATA UPSERT =================

#             obj, is_created = Final_Data.objects.update_or_create(
#                 f_AccessionNo=accession,
#                 defaults=clean_row
#             )

#             if is_created:
#                 created += 1
#             else:
#                 updated += 1

#         # ================= SUCCESS =================
#         created_batches = generate_batches_from_referred()

#         messages.success(
#             request,
#             f"Upload complete! {created} created, {updated} updated. "
#             f"{created_batches} batch(es) generated automatically."
#         )


#         return redirect("show_final_data")

#     except Exception as e:

#         import traceback
#         traceback.print_exc()

#         messages.error(request, f"Upload failed: {e}")
#         return redirect("upload_wgs_view")


def parse_date(val):

    if val is None:
        return None

    if pd.isna(val):
        return None

    if isinstance(val, pd.Timestamp):
        return val.date()

    if isinstance(val, datetime):
        return val.date()

    if isinstance(val, date):
        return val

    try:
        parsed = pd.to_datetime(val, errors="coerce")
        if pd.isna(parsed):
            return None
        return parsed.date()
    except Exception:
        return None
    


########### uploding of demogs view

# @login_required
# @transaction.atomic
# def upload_final_combined_table(request):

#     if request.method != "POST" or not request.FILES.get("DemogsDataFile"):
#         return render(request, "wgs_app/Add_wgs.html")

#     try:

#         uploaded_file = request.FILES["DemogsDataFile"]
#         file_name = uploaded_file.name.lower()

#         # ================= READ FILE =================
#         if file_name.endswith(".csv"):
#             df = pd.read_csv(uploaded_file)

#         elif file_name.endswith((".xlsx", ".xls")):
#             df = pd.read_excel(uploaded_file)

#         else:
#             messages.error(request, "Unsupported file format.")
#             return redirect("upload_final_combined_table")

#         df.columns = [str(c).strip() for c in df.columns]
#         rows = df.to_dict("records")

#         # ================= FIELD LISTS =================
#         referred_fields = {
#             f.name for f in Referred_Data._meta.fields
#             if not f.auto_created
#         }

#         final_fields = {
#             f.name for f in Final_Data._meta.fields
#             if not f.auto_created
#         }

#         IMMUTABLE_FIELDS = {
#             "f_Date_of_Entry",
#             "f_Date_Modified",
#         }

#         INT_FIELDS = {
#             "Age",
#             "f_Age",
#             "bat_seq",
#             "f_bat_seq"
#         }

#         # ================= LOOKUP CACHE =================
#         specimen_map = {
#             str(s.Specimen_code).strip().lower(): s
#             for s in SpecimenTypeModel.objects.all()
#         }

#         site_map = {
#             str(s.SiteCode).strip().upper(): s.SiteName
#             for s in SiteData.objects.all()
#         }

#         # ================= EXISTING CACHE =================
#         existing_referred = {
#             obj.AccessionNo: obj
#             for obj in Referred_Data.objects.all()
#         }

#         existing_final = {
#             obj.f_AccessionNo: obj
#             for obj in Final_Data.objects.all()
#         }

#         referred_create = []
#         referred_update = []

#         final_create = []
#         final_update = []

#         # ================= PROCESS ROWS =================
#         for row in rows:

#             row = {str(k).strip(): v for k, v in row.items()}

#             accession = str(get_value(row, "AccessionNo", "accession_no")).strip().upper()

#             if not accession:
#                 continue

#             site_code = str(get_value(row, "SiteCode", "site_code") or "").strip().upper()
#             site_name = site_map.get(site_code, "")

#             referral_date = parse_date(get_value(row, "Referral_Date", "referral_date"))
#             birth_date = parse_date(get_value(row, "Date_Birth", "date_birth"))
#             admis_date = parse_date(get_value(row, "Date_Admis", "date_admis"))
#             spec_date = parse_date(get_value(row, "Spec_Date", "spec_date"))

#             batch_code = get_value(row, "Batch_Code", "batch_code")

#             # ================= REFERRED DATA =================
#             clean_referred = {
#                 k: v
#                 for k, v in row.items()
#                 if k in referred_fields
#             }

#             clean_referred["AccessionNo"] = accession
#             clean_referred["Referral_Date"] = referral_date
#             clean_referred["Date_Birth"] = birth_date
#             clean_referred["Date_Admis"] = admis_date
#             clean_referred["Spec_Date"] = spec_date
#             clean_referred["SiteCode"] = site_code
#             clean_referred["Site_Name"] = site_name
#             clean_referred["Batch_Code"] = batch_code

#             # integer cleanup
#             for field in INT_FIELDS:
#                 if field in clean_referred:
#                     clean_referred[field] = parse_int(clean_referred[field])

#             # specimen FK
#             if "Spec_Type" in clean_referred:

#                 raw_val = clean_referred["Spec_Type"]

#                 if raw_val:
#                     clean_referred["Spec_Type"] = specimen_map.get(
#                         str(raw_val).strip().lower()
#                     )

#             if accession in existing_referred:

#                 obj = existing_referred[accession]

#                 for k, v in clean_referred.items():
#                     setattr(obj, k, v)

#                 referred_update.append(obj)

#             else:

#                 referred_create.append(
#                     Referred_Data(**clean_referred)
#                 )

#             # ================= FINAL DATA =================
#             clean_final = {}

#             for k, v in row.items():

#                 fk = f"f_{k}"

#                 if fk in final_fields and fk not in IMMUTABLE_FIELDS:
#                     clean_final[fk] = v

#             clean_final["f_AccessionNo"] = accession
#             clean_final["f_Referral_Date"] = referral_date
#             clean_final["f_Date_Birth"] = birth_date
#             clean_final["f_Date_Admis"] = admis_date
#             clean_final["f_Spec_Date"] = spec_date
#             clean_final["f_SiteCode"] = site_code
#             clean_final["f_Site_Name"] = site_name
#             clean_final["f_Batch_Code"] = batch_code

#             for field in INT_FIELDS:
#                 if field in clean_final:
#                     clean_final[field] = parse_int(clean_final[field])

#             if "f_Spec_Type" in clean_final:

#                 raw_val = clean_final["f_Spec_Type"]

#                 if raw_val:
#                     clean_final["f_Spec_Type"] = specimen_map.get(
#                         str(raw_val).strip().lower()
#                     )

#             if accession in existing_final:

#                 obj = existing_final[accession]

#                 for k, v in clean_final.items():
#                     setattr(obj, k, v)

#                 final_update.append(obj)

#             else:

#                 final_create.append(
#                     Final_Data(**clean_final)
#                 )

#         # ================= REMOVE DUPLICATES =================
#         referred_update = list({o.AccessionNo: o for o in referred_update}.values())
#         final_update = list({o.f_AccessionNo: o for o in final_update}.values())

#         # ================= BULK SAVE =================
#         if referred_create:
#             Referred_Data.objects.bulk_create(referred_create)

#         if referred_update:
#             Referred_Data.objects.bulk_update(
#                 referred_update,
#                 [f for f in referred_fields if f != "id"]
#             )

#         if final_create:
#             Final_Data.objects.bulk_create(final_create)

#         if final_update:
#             Final_Data.objects.bulk_update(
#                 final_update,
#                 [f for f in final_fields if f not in IMMUTABLE_FIELDS and f != "id"]
#             )

#         created = len(final_create)
#         updated = len(final_update)

#         # ================= BATCH GENERATION =================
#         created_batches = generate_batches_from_referred()

#         messages.success(
#             request,
#             f"Upload complete! {created} created, {updated} updated. "
#             f"{created_batches} batch(es) generated."
#         )

#         return redirect("show_final_data")

#     except Exception as e:

#         import traceback
#         traceback.print_exc()

#         messages.error(request, f"Upload failed: {e}")
#         return redirect("upload_wgs_view")

def copy_batches_to_final(batch_ids):
    from apps.home_final.signals import suspend_import_signals

    copied = 0
    last_final_obj = None
    final_text_fields = {
        field.name
        for field in Final_Data._meta.fields
        if isinstance(field, (models.CharField, models.TextField))
        and not field.null
    }

    def clean_final_text(value):
        if value is None:
            return ""
        try:
            if pd.isna(value):
                return ""
        except TypeError:
            pass
        if str(value).strip().lower() in {"nan", "nat", "none", "null"}:
            return ""
        return str(value).strip()

    isolates = list(
        Referred_Data.objects
        .filter(Batch_id_id__in=batch_ids)
        .select_related("Batch_id", "Spec_Type")
        .order_by("Batch_id_id", "bat_seq")
    )
    accessions = [
        clean_final_text(isolate.AccessionNo)
        for isolate in isolates
        if clean_final_text(isolate.AccessionNo)
    ]
    existing_by_accession = {
        obj.f_AccessionNo: obj
        for obj in Final_Data.objects.filter(f_AccessionNo__in=accessions)
    }
    create_objects = []
    update_objects = []

    for isolate in isolates:

        spec_obj = isolate.Spec_Type

        final_defaults = {

                      # ===== BATCH / META =====
                "f_bat_seq": isolate.bat_seq,
                "f_Batch_id": isolate.Batch_id,
                "f_Hide": getattr(isolate, "Hide", False),
                "f_Batch_Code": isolate.Batch_Code,
                "f_Batch_Name": isolate.Batch_Name,
                "f_RefNo": isolate.RefNo,
                "f_BatchNo": isolate.BatchNo,
                "f_Total_batch": isolate.Total_batch,

                "f_AccessionNoGen": getattr(isolate, "AccessionNoGen", ""),

                "f_SiteCode": isolate.SiteCode,
                "f_Site_Name": isolate.Site_Name,
                "f_Referral_Date": isolate.Referral_Date,

                # ===== PATIENT =====
                "f_Patient_ID": isolate.Patient_ID,
                "f_First_Name": isolate.First_Name,
                "f_Mid_Name": isolate.Mid_Name,
                "f_Last_Name": isolate.Last_Name,
                "f_Date_Birth": isolate.Date_Birth,
                "f_Age": isolate.Age,
                "f_Age_Display": getattr(isolate, "Age_Display", ""),
                "f_Sex": isolate.Sex,

                "f_Date_Admis": isolate.Date_Admis,
                "f_Nosocomial": isolate.Nosocomial,
                "f_Diagnosis": isolate.Diagnosis,
                "f_Diagnosis_ICD10": isolate.Diagnosis_ICD10,
                "f_Ward": isolate.Ward,
                "f_Ward_Type": isolate.Ward_Type,
                "f_Service_Type": isolate.Service_Type,

                # ===== SPECIMEN =====
                "f_Spec_Num": isolate.Spec_Num,
                "f_Spec_Date": isolate.Spec_Date,
                "f_Reason": isolate.Reason,
                "f_Growth": isolate.Growth,
                "f_Urine_ColCt": isolate.Urine_ColCt,

                # ===== PHENOTYPE =====
                "f_ampC": isolate.ampC,
                "f_ESBL": isolate.ESBL,
                "f_CARB": isolate.CARB,
                "f_MBL": isolate.MBL,
                "f_BL": isolate.BL,
                "f_MR": isolate.MR,
                "f_mecA": isolate.mecA,
                "f_ICR": isolate.ICR,
                "f_OtherResMech": isolate.OtherResMech,


                "f_Spec_Type": spec_obj,


                # ===== SENTINEL ORGANISM =====
                "f_Site_Pre_ed": nz(isolate.Site_Pre_ed),
                "f_Site_Org": nz(isolate.Site_Org),
                "f_Site_OrgName": nz(isolate.Site_OrgName),
                "f_Site_Pos": nz(isolate.Site_Pos),
                "f_Comments": nz(isolate.Comments),

                # ===== ARSRL =====
                "f_ars_ampC": isolate.ars_ampC,
                "f_ars_ESBL": isolate.ars_ESBL,
                "f_ars_CARB": isolate.ars_CARB,
                "f_ars_ECIM": isolate.ars_ECIM,
                "f_ars_MCIM": isolate.ars_MCIM,
                "f_ars_EC_MCIM": isolate.ars_EC_MCIM,
                "f_ars_MBL": isolate.ars_MBL,
                "f_ars_BL": isolate.ars_BL,
                "f_ars_MR": isolate.ars_MR,
                "f_ars_mecA": isolate.ars_mecA,
                "f_ars_ICR": isolate.ars_ICR,

                "f_ars_Pre_ed": nz(isolate.ars_Pre_ed),
                "f_ars_Post": nz(isolate.ars_Post),
                "f_ars_OrgCode": nz(isolate.ars_OrgCode),
                "f_ars_OrgName": nz(isolate.ars_OrgName),

                "f_ars_ct_ctl": isolate.ars_ct_ctl,
                "f_ars_tz_tzl": isolate.ars_tz_tzl,
                "f_ars_cn_cni": isolate.ars_cn_cni,
                "f_ars_ip_ipi": isolate.ars_ip_ipi,

                "f_ars_reco_Code": isolate.ars_reco_Code,
                "f_ars_description": isolate.ars_description,
                "f_ars_reco": isolate.ars_reco,

                # ===== SIGNATORIES =====
                "f_arsp_Encoder": isolate.arsp_Encoder,
                "f_arsp_Enc_Lic": isolate.arsp_Enc_Lic,
                "f_arsp_Checker": isolate.arsp_Checker,
                "f_arsp_Chec_Lic": isolate.arsp_Chec_Lic,
                "f_arsp_Verifier": isolate.arsp_Verifier,
                "f_arsp_Ver_Lic": isolate.arsp_Ver_Lic,
                "f_arsp_LabManager": isolate.arsp_LabManager,
                "f_arsp_Lab_Lic": isolate.arsp_Lab_Lic,
                "f_arsp_Head": isolate.arsp_Head,
                "f_arsp_Head_Lic": isolate.arsp_Head_Lic,
                "f_Date_Accomplished_ARSP": isolate.Date_Accomplished_ARSP,

                # ===== EXTRA =====
                "f_x_mrse": getattr(isolate, "x_mrse", ""),
                "f_x_mrsamrse": getattr(isolate, "x_mrsamrse", ""),
                "f_x_entbac": getattr(isolate, "x_entbac", ""),
                "f_edta": getattr(isolate, "edta", ""),
        }

        for field_name in final_text_fields:
            if field_name in final_defaults:
                final_defaults[field_name] = clean_final_text(
                    final_defaults[field_name]
                )

        accession = clean_final_text(isolate.AccessionNo)
        final_obj = existing_by_accession.get(accession)

        if final_obj:
            for field_name, value in final_defaults.items():
                setattr(final_obj, field_name, value)
            update_objects.append(final_obj)
        else:
            final_obj = Final_Data(
                f_AccessionNo=accession,
                **final_defaults,
            )
            create_objects.append(final_obj)
            existing_by_accession[accession] = final_obj

        last_final_obj = final_obj
        copied += 1

    update_fields = sorted({
        field_name
        for obj in update_objects
        for field_name in final_defaults
    })

    with suspend_import_signals():
        if create_objects:
            Final_Data.objects.bulk_create(
                create_objects,
                batch_size=1000,
            )
        if update_objects:
            Final_Data.objects.bulk_update(
                update_objects,
                update_fields,
                batch_size=1000,
            )

        final_by_accession = {
            accession: final_obj
            for accession, final_obj in existing_by_accession.items()
            if final_obj.pk
        }
        final_ids = [obj.pk for obj in final_by_accession.values()]
        if final_ids:
            Final_AntibioticEntry.objects.filter(
                ab_idNum_f_referred_id__in=final_ids
            ).delete()

        raw_entries = list(
            AntibioticEntry.objects.filter(
                ab_idNum_referred_id__in=[
                    isolate.AccessionNo for isolate in isolates
                ]
            ).select_related("ab_idNum_referred")
        )
        raw_field_names = {
            field.name
            for field in AntibioticEntry._meta.concrete_fields
        }
        copy_fields = [
            field
            for field in Final_AntibioticEntry._meta.concrete_fields
            if field.name in raw_field_names
            and field.name not in {
                "id",
                "ab_idNum_f_referred",
                "ab_Date_uploaded_fd",
                "ab_Date_uploaded_rd",
            }
        ]
        final_antibiotics = []
        for raw_entry in raw_entries:
            final_obj = final_by_accession.get(
                raw_entry.ab_idNum_referred.AccessionNo
            )
            if not final_obj:
                continue
            copied_values = {}
            for field in copy_fields:
                value = getattr(raw_entry, field.name)
                if value is None and not field.null:
                    if field.has_default():
                        value = field.get_default()
                    elif isinstance(field, (models.CharField, models.TextField)):
                        value = ""
                    elif isinstance(field, models.BooleanField):
                        value = False
                copied_values[field.name] = value
            final_antibiotics.append(
                Final_AntibioticEntry(
                    ab_idNum_f_referred_id=final_obj.pk,
                    **copied_values,
                )
            )
        if final_antibiotics:
            Final_AntibioticEntry.objects.bulk_create(
                final_antibiotics,
                batch_size=1000,
            )

    return copied, last_final_obj


def regenerate_batch_concordance(batch_ids, user=None):
    from apps.home_final.services import concordance as concordance_service

    regenerated = 0

    for batch_id in batch_ids:
        concordance_service.generate_concordance_for_batch(batch_id, user=user)
        regenerated += 1

    return regenerated


def refresh_tat_for_batches(batch_ids):
    refreshed = 0

    tat_entries = TATform.objects.filter(tat_Batch_Isolates_id__in=batch_ids)

    for tat in tat_entries:
        total_isolates = Referred_Data.objects.filter(
            Batch_id_id=tat.tat_Batch_Isolates_id
        ).count()
        tat.tat_Num_Isolate = total_isolates
        tat.save()
        refreshed += 1

    return refreshed


def refresh_emerging_for_batches(batch_ids):
    from apps.home_final.signals import recalculate_emerging_for_final

    refreshed = 0

    isolates = Final_Data.objects.filter(f_Batch_id_id__in=batch_ids)

    for isolate in isolates:
        recalculate_emerging_for_final(isolate)
        refreshed += 1

    return refreshed


def refresh_emerging_for_final_ids(final_ids):
    from apps.home_final.signals import recalculate_emerging_for_final

    refreshed = 0

    isolates = Final_Data.objects.filter(pk__in=final_ids)

    for isolate in isolates:
        recalculate_emerging_for_final(isolate)
        refreshed += 1

    return refreshed


def regenerate_concordance_for_final_ids(final_ids, user=None):
    from apps.home_final.services import concordance as concordance_service

    regenerated = 0

    isolates = Final_Data.objects.filter(pk__in=final_ids)

    for isolate in isolates:
        concordance_service.generate_concordance_for_isolate(isolate, user=user)
        regenerated += 1

    return regenerated


def reapply_raw_breakpoints_for_batches(batch_ids):
    from apps.home.signals import determine_ris

    updated = 0
    changed_entries = []
    entry_breakpoints = {}
    effective_year_for, resolve_breakpoint = _build_breakpoint_resolver()

    entries = (
        AntibioticEntry.objects
        .filter(ab_idNum_referred__Batch_id_id__in=batch_ids)
        .select_related("ab_idNum_referred")
        .prefetch_related("ab_breakpoints_id")
    )

    for entry in entries:
        isolate = entry.ab_idNum_referred
        effective_year = effective_year_for(isolate.Spec_Date)
        site_org = (isolate.Site_Org or "").strip()
        ars_org = (
            (isolate.ars_OrgCode or "").strip()
            or site_org
        )
        breakpoint_ids = list(entry.ab_breakpoints_id.values_list("id", flat=True))

        if entry.ab_Abx_code:
            disk_bp = None
            mic_bp = None

            if entry.ab_Disk_value is not None:
                disk_bp = resolve_breakpoint(
                    entry.ab_Abx_code,
                    isolate.Spec_Date,
                    "DISK",
                    site_org,
                )

            if entry.ab_MIC_value is not None:
                mic_bp = resolve_breakpoint(
                    entry.ab_Abx_code,
                    isolate.Spec_Date,
                    "MIC",
                    site_org,
                )

            bp = mic_bp or disk_bp
            if bp:
                _clear_bp_fields(entry)
                flag_bp = None
                if mic_bp:
                    flag_bp = resolve_breakpoint(
                        entry.ab_Abx_code,
                        isolate.Spec_Date,
                        "MIC",
                        site_org,
                        require_emerging_flag=True,
                    )
                    _apply_bp_to_entry(
                        entry, bp, False, alert_mic=True,
                        set_relation=False, flag_bp=flag_bp
                    )
                else:
                    flag_bp = resolve_breakpoint(
                        entry.ab_Abx_code,
                        isolate.Spec_Date,
                        "DISK",
                        site_org,
                        require_emerging_flag=True,
                    )
                    _apply_bp_to_entry(
                        entry, bp, True, set_relation=False, flag_bp=flag_bp
                    )
                breakpoint_ids.append(bp.id)
                if flag_bp and flag_bp.id != bp.id:
                    breakpoint_ids.append(flag_bp.id)

        if entry.ab_Retest_Abx_code:
            retest_disk_bp = None
            retest_mic_bp = None

            if entry.ab_Retest_DiskValue is not None:
                retest_disk_bp = resolve_breakpoint(
                    entry.ab_Retest_Abx_code,
                    isolate.Spec_Date,
                    "DISK",
                    ars_org,
                )

            if entry.ab_Retest_MICValue is not None:
                retest_mic_bp = resolve_breakpoint(
                    entry.ab_Retest_Abx_code,
                    isolate.Spec_Date,
                    "MIC",
                    ars_org,
                )

            bp = retest_mic_bp or retest_disk_bp
            if bp:
                _clear_retest_bp_fields(entry)
                flag_bp = None
                if retest_mic_bp:
                    flag_bp = resolve_breakpoint(
                        entry.ab_Retest_Abx_code,
                        isolate.Spec_Date,
                        "MIC",
                        ars_org,
                        require_emerging_flag=True,
                    )
                    _apply_bp_to_retest_entry(
                        entry, bp, False, alert_mic=True,
                        set_relation=False, flag_bp=flag_bp
                    )
                else:
                    flag_bp = resolve_breakpoint(
                        entry.ab_Retest_Abx_code,
                        isolate.Spec_Date,
                        "DISK",
                        ars_org,
                        require_emerging_flag=True,
                    )
                    _apply_bp_to_retest_entry(
                        entry, bp, True, set_relation=False, flag_bp=flag_bp
                    )
                breakpoint_ids.append(bp.id)
                if flag_bp and flag_bp.id != bp.id:
                    breakpoint_ids.append(flag_bp.id)

        _recalculate_antibiotic_ris(entry, determine_ris)
        changed_entries.append(entry)
        entry_breakpoints[entry.pk] = breakpoint_ids
        updated += 1

    if changed_entries:
        AntibioticEntry.objects.bulk_update(
            changed_entries,
            [
                "ab_Site_Org", "ab_Ret_Org",
                "ab_Org_Flag", "ab_Abx_Flag", "ab_Abx_Phenotype",
                "ab_R_breakpoint", "ab_I_breakpoint",
                "ab_SDD_breakpoint", "ab_S_breakpoint", "ab_Alert_val",
                "ab_Ret_R_breakpoint", "ab_Ret_I_breakpoint",
                "ab_Ret_SDD_breakpoint", "ab_Ret_S_breakpoint",
                "ab_Retest_Alert_val",
                "ab_Disk_RIS", "ab_MIC_RIS",
                "ab_Retest_Disk_RIS", "ab_Retest_MIC_RIS",
            ],
            batch_size=1000,
        )
        _bulk_replace_breakpoint_links(
            AntibioticEntry,
            entry_breakpoints,
        )

    return updated


def reapply_final_breakpoints_for_batches(batch_ids, debug=False):
    from apps.home_final.signals import determine_ris

    updated = 0
    debug_rows = []
    changed_entries = []
    entry_breakpoints = {}
    effective_year_for, resolve_breakpoint = _build_breakpoint_resolver()

    entries = (
        Final_AntibioticEntry.objects
        .filter(ab_idNum_f_referred__f_Batch_id_id__in=batch_ids)
        .select_related("ab_idNum_f_referred")
        .prefetch_related("ab_breakpoints_id")
    )

    for entry in entries:
        isolate = entry.ab_idNum_f_referred
        effective_year = effective_year_for(isolate.f_Spec_Date)
        site_org = (isolate.f_Site_Org or "").strip()
        ars_org = (
            (isolate.f_ars_OrgCode or "").strip()
            or site_org
        )
        breakpoint_ids = list(entry.ab_breakpoints_id.values_list("id", flat=True))
        applied_notes = []

        if entry.ab_Abx_code:
            disk_bp = None
            mic_bp = None

            if entry.ab_Disk_value is not None:
                disk_bp = resolve_breakpoint(
                    entry.ab_Abx_code,
                    isolate.f_Spec_Date,
                    "DISK",
                    site_org,
                )

            if entry.ab_MIC_value is not None:
                mic_bp = resolve_breakpoint(
                    entry.ab_Abx_code,
                    isolate.f_Spec_Date,
                    "MIC",
                    site_org,
                )

            bp = mic_bp or disk_bp
            if bp:
                _clear_bp_fields(entry)
                flag_bp = None
                if mic_bp:
                    flag_bp = resolve_breakpoint(
                        entry.ab_Abx_code,
                        isolate.f_Spec_Date,
                        "MIC",
                        site_org,
                        require_emerging_flag=True,
                    )
                    _apply_bp_to_entry(
                        entry, bp, False, alert_mic=True,
                        set_relation=False, flag_bp=flag_bp
                    )
                    applied_notes.append(
                        f"main MIC {entry.ab_Abx_code} -> BP#{bp.id} {bp.Org or 'generic'}"
                    )
                else:
                    flag_bp = resolve_breakpoint(
                        entry.ab_Abx_code,
                        isolate.f_Spec_Date,
                        "DISK",
                        site_org,
                        require_emerging_flag=True,
                    )
                    _apply_bp_to_entry(
                        entry, bp, True, set_relation=False, flag_bp=flag_bp
                    )
                    applied_notes.append(
                        f"main DISK {entry.ab_Abx_code} -> BP#{bp.id} {bp.Org or 'generic'}"
                    )
                breakpoint_ids.append(bp.id)
                if flag_bp and flag_bp.id != bp.id:
                    breakpoint_ids.append(flag_bp.id)
            else:
                applied_notes.append(f"main {entry.ab_Abx_code} -> no breakpoint")

        if entry.ab_Retest_Abx_code:
            retest_disk_bp = None
            retest_mic_bp = None

            if entry.ab_Retest_DiskValue is not None:
                retest_disk_bp = resolve_breakpoint(
                    entry.ab_Retest_Abx_code,
                    isolate.f_Spec_Date,
                    "DISK",
                    ars_org,
                )

            if entry.ab_Retest_MICValue is not None:
                retest_mic_bp = resolve_breakpoint(
                    entry.ab_Retest_Abx_code,
                    isolate.f_Spec_Date,
                    "MIC",
                    ars_org,
                )

            bp = retest_mic_bp or retest_disk_bp
            if bp:
                _clear_retest_bp_fields(entry)
                flag_bp = None
                if retest_mic_bp:
                    flag_bp = resolve_breakpoint(
                        entry.ab_Retest_Abx_code,
                        isolate.f_Spec_Date,
                        "MIC",
                        ars_org,
                        require_emerging_flag=True,
                    )
                    _apply_bp_to_retest_entry(
                        entry, bp, False, alert_mic=True,
                        set_relation=False, flag_bp=flag_bp
                    )
                    applied_notes.append(
                        f"retest MIC {entry.ab_Retest_Abx_code} -> BP#{bp.id} {bp.Org or 'generic'}"
                    )
                else:
                    flag_bp = resolve_breakpoint(
                        entry.ab_Retest_Abx_code,
                        isolate.f_Spec_Date,
                        "DISK",
                        ars_org,
                        require_emerging_flag=True,
                    )
                    _apply_bp_to_retest_entry(
                        entry, bp, True, set_relation=False, flag_bp=flag_bp
                    )
                    applied_notes.append(
                        f"retest DISK {entry.ab_Retest_Abx_code} -> BP#{bp.id} {bp.Org or 'generic'}"
                    )
                breakpoint_ids.append(bp.id)
                if flag_bp and flag_bp.id != bp.id:
                    breakpoint_ids.append(flag_bp.id)
            else:
                applied_notes.append(f"retest {entry.ab_Retest_Abx_code} -> no breakpoint")

        _recalculate_antibiotic_ris(entry, determine_ris)
        changed_entries.append(entry)
        entry_breakpoints[entry.pk] = breakpoint_ids
        updated += 1

        if debug:
            debug_rows.append(
                f"{isolate.f_AccessionNo}: year={effective_year or 'none'}, "
                f"site_org={site_org or '-'}, ars_org={ars_org or '-'}, "
                f"{'; '.join(applied_notes) or 'no antibiotic code'}"
            )

    if changed_entries:
        Final_AntibioticEntry.objects.bulk_update(
            changed_entries,
            [
                "ab_Site_Org", "ab_Ret_Org",
                "ab_Org_Flag", "ab_Abx_Flag", "ab_Abx_Phenotype",
                "ab_R_breakpoint", "ab_I_breakpoint",
                "ab_SDD_breakpoint", "ab_S_breakpoint", "ab_Alert_val",
                "ab_Ret_R_breakpoint", "ab_Ret_I_breakpoint",
                "ab_Ret_SDD_breakpoint", "ab_Ret_S_breakpoint",
                "ab_Retest_Alert_val",
                "ab_Disk_RIS", "ab_MIC_RIS",
                "ab_Retest_Disk_RIS", "ab_Retest_MIC_RIS",
            ],
            batch_size=1000,
        )
        _bulk_replace_breakpoint_links(
            Final_AntibioticEntry,
            entry_breakpoints,
        )

    if debug:
        print("[FINAL ABX DEBUG] Breakpoint refresh summary:")
        if debug_rows:
            for row in debug_rows[:100]:
                print(f"[FINAL ABX DEBUG]   {row}")
            if len(debug_rows) > 100:
                print(f"[FINAL ABX DEBUG]   ... {len(debug_rows) - 100} more entries")
        else:
            print("[FINAL ABX DEBUG]   No final antibiotic entries found for affected batches.")

    return updated


def _referred_bulk_upload_skip_reason(user, isolate, *, final=False):
    """Protect existing Raw/Final accessions in mass uploads.

    Unlike an interactive correction form, bulk imports are not scoped to an
    approved correction field. Active review, sign-off and release are locked.
    A withdrawn review and Raw returned-to-encoder stage can be edited.
    Final encoding is editable while its case explicitly permits encoding.
    """
    from apps.verification.models import VerificationCase

    batch = isolate.f_Batch_id if final else isolate.Batch_id
    if batch is None and final:
        raw = Referred_Data.objects.select_related("Batch_id").filter(
            AccessionNo=isolate.f_AccessionNo
        ).first()
        batch = raw.Batch_id if raw else None

    if batch is None:
        # Same effective-role logic as other Django permission checks.
        from apps.home.permissions import get_user_roles
        if not get_user_roles(user).intersection({ROLE_ADMIN, ROLE_CHECKER}):
            return "unbatched existing record"
        return None

    if not can_manage_batch(user, batch):
        return "another user's batch"

    cases = VerificationCase.objects.filter(
        Q(final_batch_id=batch.pk)
        | Q(final_accession__f_Batch_id_id=batch.pk)
    ).distinct()
    # All active cases must allow this action, not merely the newest one.
    for case in cases:
        if final:
            allowed = {
                VerificationCase.STATUS_DRAFT,
                VerificationCase.STATUS_WITHDRAWN,
                VerificationCase.STATUS_FOR_FINAL_ENCODING,
            }
        else:
            allowed = {
                VerificationCase.STATUS_DRAFT,
                VerificationCase.STATUS_WITHDRAWN,
                VerificationCase.STATUS_RETURNED_TO_ENCODER,
            }
        if case.status not in allowed:
            return "verification or release lock"
    return None


########## UPLOADING DEMOGS
@login_required
@role_required(ROLE_ADMIN, ROLE_CHECKER, ROLE_ENCODER)
@transaction.atomic
def upload_referred_table(request):

    if request.method != "POST" or not request.FILES.get("DemogsDataFile"):
        return render(request, "wgs_app/Add_wgs.html")

    try:

        uploaded_file = request.FILES["DemogsDataFile"]
        upload_mode = request.POST.get("demographic_upload_mode", "raw").strip().lower()
        overwrite = request.POST.get("overwrite", "false").lower() == "true"
        file_name = uploaded_file.name.lower()

        # ================= READ FILE =================
        if file_name.endswith(".csv"):
            df = pd.read_csv(uploaded_file)

        elif file_name.endswith(".tsv"):
            df = pd.read_csv(uploaded_file, sep="\t")

        elif file_name.endswith((".xlsx", ".xls")):
            df = pd.read_excel(uploaded_file)

        else:
            messages.error(request, "Unsupported file format. Please upload CSV, TSV, XLSX, or XLS.")
            return redirect("upload_wgs_view")

        df.columns = [str(c).strip() for c in df.columns]
        rows = df.to_dict("records")

        def is_blank_value(value):
            if value is None:
                return True
            try:
                if pd.isna(value):
                    return True
            except TypeError:
                pass
            return str(value).strip().lower() in {"", "nan", "nat", "none", "null"}

        def clean_text_value(value):
            return "" if is_blank_value(value) else str(value).strip()

        def parse_bool_value(value):
            if is_blank_value(value):
                return False
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return bool(value)

            normalized = str(value).strip().lower()
            if normalized in {"true", "t", "yes", "y", "1", "checked"}:
                return True
            if normalized in {"false", "f", "no", "n", "0", "unchecked"}:
                return False

            return False

        def parse_date_value(value):
            if is_blank_value(value):
                return None
            if isinstance(value, datetime):
                return value.date()
            if isinstance(value, date):
                return value
            return parse_date(clean_text_value(value))

        if upload_mode == "final":
            final_fields = {
                f.name for f in Final_Data._meta.fields
                if not f.auto_created
            }
            final_field_map = {
                f.name: f for f in Final_Data._meta.fields
                if not f.auto_created
            }
            final_text_fields = {
                name for name, field in final_field_map.items()
                if isinstance(field, (models.CharField, models.TextField))
            }
            final_boolean_fields = {
                name for name, field in final_field_map.items()
                if isinstance(field, models.BooleanField)
            }
            final_date_fields = {
                name for name, field in final_field_map.items()
                if isinstance(field, models.DateField)
            }
            final_int_fields = {"f_Age", "f_bat_seq"}

            specimen_map = {
                str(s.Specimen_code).strip().lower(): s
                for s in SpecimenTypeModel.objects.all()
            }
            site_map = {
                str(s.SiteCode).strip().upper(): s.SiteName
                for s in SiteData.objects.all()
            }

            def ref_no_from_batch_code(batch_code):
                batch_code = clean_text_value(batch_code)
                if "_" not in batch_code:
                    return ""
                return batch_code.rsplit("_", 1)[-1].strip()

            normalized_rows = []
            uploaded_accessions = set()

            for row in rows:
                row = {str(k).strip(): v for k, v in row.items()}

                accession = clean_text_value(
                    get_value(row, "f_AccessionNo", "AccessionNo", "accession_no", "ID_Number")
                ).upper()
                if not accession:
                    continue

                uploaded_accessions.add(accession)
                normalized_rows.append({"row": row, "accession": accession})

            existing = {
                obj.f_AccessionNo: obj
                for obj in Final_Data.objects.filter(
                    f_AccessionNo__in=uploaded_accessions
                )
            }
            to_create = []
            to_update = []
            skipped = 0
            protected_skips = defaultdict(int)
            touched_batch_ids = set()

            for normalized in normalized_rows:
                row = normalized["row"]
                accession = normalized["accession"]
                clean = {}

                for field_name in final_fields:
                    if field_name in {"id", "f_AccessionNo", "f_Batch_id", "f_Spec_Type"}:
                        continue

                    raw_key = field_name[2:] if field_name.startswith("f_") else field_name
                    value = get_value(row, field_name, raw_key, raw_key.lower())
                    if value is None:
                        continue

                    clean[field_name] = value

                for field in final_text_fields:
                    if field in clean:
                        clean[field] = clean_text_value(clean[field])

                for field in final_boolean_fields:
                    if field in clean:
                        clean[field] = parse_bool_value(clean[field])

                for field in final_date_fields:
                    if field in clean:
                        clean[field] = parse_date_value(clean[field])

                for field in final_int_fields:
                    if field in clean:
                        clean[field] = parse_int(clean[field])

                site_code = clean_text_value(
                    get_value(row, "f_SiteCode", "SiteCode", "site_code")
                ).upper()
                if site_code:
                    clean["f_SiteCode"] = site_code
                    clean["f_Site_Name"] = clean.get("f_Site_Name") or site_map.get(site_code, "")

                spec_type = get_value(row, "f_Spec_Type", "Spec_Type", "spec_type")
                if not is_blank_value(spec_type):
                    clean["f_Spec_Type"] = specimen_map.get(
                        clean_text_value(spec_type).lower()
                    )

                batch_code = clean_text_value(
                    get_value(row, "f_Batch_Code", "Batch_Code", "batch_code", "f_Batch_Name", "Batch_Name")
                )
                if batch_code:
                    batch_obj = Batch_Table.objects.filter(
                        bat_Batch_Code=batch_code
                    ).first()
                    if batch_obj:
                        clean["f_Batch_id"] = batch_obj
                        clean["f_Batch_Code"] = batch_obj.bat_Batch_Code
                        clean["f_Batch_Name"] = batch_obj.bat_Batch_Name
                        clean["f_RefNo"] = (
                            clean.get("f_RefNo")
                            or batch_obj.bat_RefNo
                            or ref_no_from_batch_code(batch_obj.bat_Batch_Code)
                            or ""
                        )
                        clean["f_BatchNo"] = clean.get("f_BatchNo") or batch_obj.bat_BatchNo
                        clean["f_Total_batch"] = clean.get("f_Total_batch") or batch_obj.bat_Total_batch
                        clean["f_Referral_Date"] = clean.get("f_Referral_Date") or batch_obj.bat_Referral_Date

                for field in final_text_fields:
                    if field in clean:
                        clean[field] = clean_text_value(clean[field])

                final_obj = existing.get(accession)
                if final_obj:
                    skip_reason = _referred_bulk_upload_skip_reason(
                        request.user, final_obj, final=True
                    )
                    if skip_reason:
                        skipped += 1
                        protected_skips[skip_reason] += 1
                        continue
                    if not overwrite:
                        skipped += 1
                        continue
                    # Keep the original Final batch association and workflow
                    # metadata; upload content cannot reparent reviewed results.
                    for protected_name in (
                        "f_Batch_id", "f_Batch_Code", "f_Batch_Name",
                        "f_Copy_data", "f_bat_seq",
                    ):
                        clean.pop(protected_name, None)
                    for field_name, value in clean.items():
                        setattr(final_obj, field_name, value)
                    to_update.append(final_obj)
                    if final_obj.f_Batch_id_id:
                        touched_batch_ids.add(final_obj.f_Batch_id_id)
                else:
                    target_batch = clean.get("f_Batch_id")
                    # New Final rows must respect any existing Raw record's
                    # owning batch and review. A forged or missing batch code
                    # must not bypass that permission check.
                    raw_existing = Referred_Data.objects.select_related("Batch_id").filter(
                        AccessionNo=accession
                    ).first()
                    if raw_existing is not None:
                        reason = _referred_bulk_upload_skip_reason(
                            request.user, raw_existing, final=False
                        )
                    elif target_batch is not None:
                        from types import SimpleNamespace
                        reason = _referred_bulk_upload_skip_reason(
                            request.user,
                            SimpleNamespace(Batch_id=target_batch),
                            final=False,
                        )
                    else:
                        from apps.home.permissions import get_user_roles
                        reason = (
                            None if get_user_roles(request.user).intersection({ROLE_ADMIN, ROLE_CHECKER})
                            else "new unbatched Final record requires Admin or Checker"
                        )
                    if reason:
                        skipped += 1
                        protected_skips[reason] += 1
                        continue
                    if target_batch is not None:
                        touched_batch_ids.add(target_batch.id)
                    to_create.append(
                        Final_Data(f_AccessionNo=accession, **clean)
                    )

            update_fields = sorted({
                field_name
                for field_name in final_fields
                if field_name not in {"id", "f_AccessionNo"}
            })

            from apps.home_final.signals import suspend_import_signals

            with suspend_import_signals():
                if to_create:
                    Final_Data.objects.bulk_create(to_create, batch_size=1000)
                if to_update:
                    Final_Data.objects.bulk_update(
                        to_update,
                        update_fields,
                        batch_size=1000,
                    )

            touched_final_ids = list(
                Final_Data.objects
                .filter(f_AccessionNo__in=[
                    obj.f_AccessionNo for obj in to_create + to_update
                ])
                .values_list("id", flat=True)
            )
            refreshed_final_breakpoints = reapply_final_breakpoints_for_batches(
                list(touched_batch_ids)
            )
            refreshed_emerging = refresh_emerging_for_final_ids(touched_final_ids)
            regenerated_concordance = regenerate_concordance_for_final_ids(
                touched_final_ids,
                user=request.user,
            )
            refreshed_tat = refresh_tat_for_batches(list(touched_batch_ids))

            messages.success(
                request,
                f"Final demographic upload complete! "
                f"{len(to_create)} created, {len(to_update)} updated, {skipped} skipped. "
                f"{refreshed_final_breakpoints} final antibiotic breakpoint record(s) refreshed. "
                f"{refreshed_emerging} emerging record(s) refreshed. "
                f"{regenerated_concordance} concordance accession report(s) refreshed. "
                f"{refreshed_tat} TAT record(s) refreshed."
            )

            if protected_skips:
                messages.warning(
                    request,
                    "Protected Final demographics skipped: " + "; ".join(
                        f"{count} {reason}" for reason, count in sorted(protected_skips.items())
                    )
                )
            return redirect("show_final_data")

        # ================= FIELD LIST =================
        referred_fields = {
            f.name for f in Referred_Data._meta.fields
            if not f.auto_created
        }
        referred_field_map = {
            f.name: f for f in Referred_Data._meta.fields
            if not f.auto_created
        }
        text_fields = {
            name for name, field in referred_field_map.items()
            if isinstance(field, (models.CharField, models.TextField))
        }
        boolean_fields = {
            name for name, field in referred_field_map.items()
            if isinstance(field, models.BooleanField)
        }

        INT_FIELDS = {
            "Age",
            "bat_seq"
        }

        # ================= LOOKUPS =================
        specimen_map = {
            str(s.Specimen_code).strip().lower(): s
            for s in SpecimenTypeModel.objects.all()
        }

        site_map = {
            str(s.SiteCode).strip().upper(): s.SiteName
            for s in SiteData.objects.all()
        }

        # ================= EXISTING CACHE =================
        existing = {}

        to_create = []
        to_update = []
        uploaded_batch_codes = set()
        skipped = 0
        protected_skips = defaultdict(int)

        def normalize_ref_no(value, accession):
            inferred = infer_ref_from_accession(accession)

            if is_blank_value(value):
                return inferred

            raw_ref = clean_text_value(value)

            try:
                numeric_ref = float(raw_ref)
                if numeric_ref.is_integer():
                    raw_ref = str(int(numeric_ref))
            except ValueError:
                pass

            if not raw_ref.isdigit():
                return inferred

            if inferred and inferred.isdigit():
                return raw_ref.zfill(len(inferred))

            return raw_ref

        def infer_site_code_from_accession(accession):
            match = re.search(r"ARS_([A-Z]+)\d+$", accession or "", re.I)
            return match.group(1).upper() if match else ""

        def infer_ref_from_accession(accession):
            match = re.search(r"(\d+)$", accession or "")
            return match.group(1) if match else ""

        def extract_ref_range_from_batch_name(batch_name):
            if is_blank_value(batch_name):
                return ""

            match = re.search(
                r"_(\d{3,6}(?:-\d{3,6})?)\s*$",
                clean_text_value(batch_name)
            )
            return match.group(1) if match else ""

        def extract_batch_sequence(batch_code):
            if is_blank_value(batch_code):
                return "", ""

            match = re.search(
                r"_(\d+)\.(\d+)_\d{3,6}(?:-\d{3,6})?\s*$",
                clean_text_value(batch_code)
            )
            if not match:
                return "", ""

            return match.group(1), match.group(2)

        def parse_year_as_date(value):
            if is_blank_value(value):
                return None

            if isinstance(value, datetime):
                return date(value.year, 1, 1)

            if isinstance(value, date):
                return date(value.year, 1, 1)

            raw_value = clean_text_value(value)

            try:
                year = int(float(raw_value))
                if 1900 <= year <= 2100:
                    return date(year, 1, 1)
            except ValueError:
                pass

            parsed = parse_date(raw_value)
            return date(parsed.year, 1, 1) if parsed else None

        normalized_rows = []
        batch_groups = {}

        for row in rows:

            row = {str(k).strip(): v for k, v in row.items()}

            # Allow a final-data mapped workbook to be uploaded here without
            # failing on missing raw field names. Most final fields are the raw
            # field name with an "f_" prefix.
            for key, value in list(row.items()):
                if not key.startswith("f_"):
                    continue
                raw_key = key[2:]
                if raw_key in referred_fields and is_blank_value(row.get(raw_key)):
                    row[raw_key] = value

            accession = clean_text_value(
                get_value(row, "AccessionNo", "accession_no", "f_AccessionNo")
            ).upper()

            if not accession:
                continue

            site_code = clean_text_value(
                get_value(row, "SiteCode", "site_code", "f_SiteCode")
            ).upper()
            if not site_code:
                site_code = infer_site_code_from_accession(accession)

            referral_date = parse_date(
                get_value(row, "Referral_Date", "referral_date", "f_Referral_Date")
            )

            explicit_ref_no = clean_text_value(
                get_value(row, "RefNo", "ref_no", "f_RefNo")
            )
            inferred_ref_no = normalize_ref_no(explicit_ref_no, accession)
            explicit_batch_code = clean_text_value(
                get_value(
                    row,
                    "Batch_Code",
                    "batch_code",
                    "f_Batch_Code",
                    "Batch_Name",
                    "batch_name",
                    "f_Batch_Name",
                )
            )
            code_batch_no, code_total_batch = extract_batch_sequence(
                explicit_batch_code
            )
            batch_no = clean_text_value(
                get_value(row, "BatchNo", "batch_no", "f_BatchNo")
            ) or code_batch_no or "1"
            total_batch = clean_text_value(
                get_value(row, "Total_batch", "total_batch", "f_Total_batch")
            ) or code_total_batch or "1"

            group_key = (
                explicit_batch_code,
                site_code,
                referral_date,
                batch_no,
                total_batch,
            )

            normalized = {
                "row": row,
                "accession": accession,
                "site_code": site_code,
                "referral_date": referral_date,
                "batch_no": batch_no,
                "total_batch": total_batch,
                "ref_no": inferred_ref_no,
                "explicit_batch_code": explicit_batch_code,
                "group_key": group_key,
            }
            normalized_rows.append(normalized)
            batch_groups.setdefault(group_key, []).append(normalized)

        batch_codes_by_group = {}

        for group_key, group_rows in batch_groups.items():
            explicit_batch_code, site_code, referral_date, batch_no, total_batch = group_key

            if explicit_batch_code:
                ref_no = (
                    extract_ref_range_from_batch_name(explicit_batch_code)
                    or group_rows[0]["ref_no"]
                )
                batch_code = explicit_batch_code

            else:
                refs = [
                    row["ref_no"]
                    for row in group_rows
                    if row["ref_no"] and row["ref_no"].isdigit()
                ]

                if refs:
                    width = max(len(ref) for ref in refs)
                    ref_numbers = sorted(int(ref) for ref in refs)
                    start_ref = str(ref_numbers[0]).zfill(width)
                    end_ref = str(ref_numbers[-1]).zfill(width)
                    ref_no = (
                        start_ref
                        if start_ref == end_ref
                        else f"{start_ref}-{end_ref}"
                    )
                else:
                    ref_no = ""

                if not (site_code and referral_date and ref_no):
                    missing = []
                    if not site_code:
                        missing.append("SiteCode")
                    if not referral_date:
                        missing.append("Referral_Date")
                    if not ref_no:
                        missing.append("AccessionNo or RefNo")
                    raise ValueError(
                        "Cannot generate batch code. Missing: "
                        f"{', '.join(missing)}. Please map SiteCode, "
                        "Referral_Date, and AccessionNo or RefNo."
                    )

                batch_code = (
                    f"{site_code}_{referral_date.strftime('%m%d%Y')}_"
                    f"{batch_no}.{total_batch}_{ref_no}"
                )

            for row in group_rows:
                row["batch_code"] = batch_code
                row["batch_ref_no"] = ref_no
                row["batch_name"] = batch_code

        uploaded_accessions = {
            row["accession"]
            for row in normalized_rows
        }
        existing = {
            obj.AccessionNo: obj
            for obj in Referred_Data.objects.filter(
                AccessionNo__in=uploaded_accessions
            )
        }

        # ================= PROCESS ROWS =================
        for normalized in normalized_rows:

            row = normalized["row"]
            accession = normalized["accession"]
            site_code = normalized["site_code"]

            site_name = site_map.get(site_code, "")

            referral_date = normalized["referral_date"]

            birth_date = parse_date(
                get_value(row, "Date_Birth", "date_birth")
            )

            admis_date = parse_date(
                get_value(row, "Date_Admis", "date_admis")
            )

            spec_date = parse_date(
                get_value(row, "Spec_Date", "spec_date")
            )

            default_year = parse_year_as_date(
                get_value(row, "Default_Year", "default_year", "f_Default_Year")
            )

            batch_code = normalized["batch_code"]

            clean = {
                k: v for k, v in row.items()
                if k in referred_fields
            }

            for field in text_fields:
                if field in clean:
                    clean[field] = clean_text_value(clean[field])

            for field in boolean_fields:
                if field in clean:
                    clean[field] = parse_bool_value(clean[field])

            clean["AccessionNo"] = accession
            clean["Referral_Date"] = referral_date
            clean["Date_Birth"] = birth_date
            clean["Date_Admis"] = admis_date
            clean["Spec_Date"] = spec_date
            clean["Default_Year"] = default_year or (
                date(spec_date.year, 1, 1) if spec_date else None
            )
            clean["SiteCode"] = site_code
            clean["Site_Name"] = site_name
            clean["Batch_Code"] = batch_code
            clean["Batch_Name"] = normalized["batch_name"]
            clean["RefNo"] = normalized["batch_ref_no"]
            clean["BatchNo"] = normalized["batch_no"]
            clean["Total_batch"] = normalized["total_batch"]

            # integer cleanup
            for field in INT_FIELDS:
                if field in clean:
                    clean[field] = parse_int(clean[field])

            # specimen FK
            if "Spec_Type" in clean:

                raw_val = clean["Spec_Type"]

                if raw_val:
                    clean["Spec_Type"] = specimen_map.get(
                        str(raw_val).strip().lower()
                    )

            # ================= CREATE / UPDATE =================
            if accession in existing:
                skip_reason = _referred_bulk_upload_skip_reason(
                    request.user, existing[accession], final=False
                )
                if skip_reason:
                    skipped += 1
                    protected_skips[skip_reason] += 1
                    continue
                if not overwrite:
                    skipped += 1
                    continue

                obj = existing[accession]
                # An upload must not silently reassign an accession to another
                # batch or overwrite its system-managed workflow identity.
                for protected_name in (
                    "Batch_id", "Batch_Code", "Batch_Name",
                    "Copy_data", "Hide", "bat_seq",
                    "Date_of_Entry", "Date_Modified",
                ):
                    clean.pop(protected_name, None)

                for k, v in clean.items():
                    setattr(obj, k, v)

                to_update.append(obj)
                uploaded_batch_codes.add(batch_code)

            else:
                # A new accession cannot be injected into another user's
                # existing batch or one undergoing review.
                target_batch = Batch_Table.objects.filter(
                    bat_Batch_Code=batch_code
                ).first()
                if target_batch is not None:
                    from types import SimpleNamespace
                    reason = _referred_bulk_upload_skip_reason(
                        request.user,
                        SimpleNamespace(Batch_id=target_batch),
                        final=False,
                    )
                    if reason:
                        skipped += 1
                        protected_skips[reason] += 1
                        continue
                to_create.append(
                    Referred_Data(**clean)
                )
                uploaded_batch_codes.add(batch_code)

        # ================= REMOVE DUPLICATES =================
        to_update = list({o.AccessionNo: o for o in to_update}.values())

        # ================= SAVE =================
        if to_create:
            Referred_Data.objects.bulk_create(to_create, batch_size=1000)

        if to_update:
            Referred_Data.objects.bulk_update(
                to_update,
                [f for f in referred_fields if f != "id"],
                batch_size=1000,
            )

        created = len(to_create)
        updated = len(to_update)

        # Do not touch batch generation at all when every row was protected.
        created_batches = (
            generate_batches_from_referred() if created or updated else 0
        )
        batch_ids = list(
            Batch_Table.objects
            .filter(bat_Batch_Code__in=uploaded_batch_codes)
            .values_list("id", flat=True)
        )
        refreshed_breakpoints = reapply_raw_breakpoints_for_batches(batch_ids)
        messages.success(
            request,
            f"Upload complete! {created} created, {updated} updated, {skipped} skipped. "
            f"{created_batches} batches generated. "
            f"{refreshed_breakpoints} antibiotic breakpoint record(s) refreshed. "
            "Final data, emerging, concordance, and TAT were not refreshed."
        )

        if protected_skips:
            messages.warning(
                request,
                "Protected Raw demographics skipped: " + "; ".join(
                    f"{count} {reason}" for reason, count in sorted(protected_skips.items())
                )
            )
        return redirect("show_batches")

    except Exception:
        # The outer transaction.atomic would otherwise commit partial writes
        # because this handler catches the exception.
        transaction.set_rollback(True)
        import logging
        logging.getLogger(__name__).exception(
            "Demographic upload failed for user %s", request.user.pk
        )
        messages.error(
            request, "Upload rejected; no demographic records were saved. "
            "Check the mapped fields and input data."
        )
        return redirect("upload_wgs_view")



########## end of uploading view

@login_required
def show_final_data(request):
    q = (request.GET.get("q") or "").strip()
    finaldata_summaries = Final_Data.objects.all().order_by("f_Referral_Date")  # optional ordering
    finaldata_summaries = apply_wgs_text_search(finaldata_summaries, q)

    total_records = Final_Data.objects.count()
    filtered_records = finaldata_summaries.count()
     # Paginate the queryset to display 20 records per page
    paginator = Paginator(finaldata_summaries, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # Render the template with paginated data
    return render(
        request,
        "wgs_app/show_final_data.html",
        {"page_obj": page_obj,
         "total_records": total_records,
         "filtered_records": filtered_records,
         "q": q,
         },  # only send page_obj
    )



@login_required
def show_referred_data(request):
    q = (request.GET.get("q") or "").strip()
    rawdata_summaries = Referred_Data.objects.all().order_by("Referral_Date")  # optional ordering
    rawdata_summaries = apply_wgs_text_search(rawdata_summaries, q)

    total_records = Referred_Data.objects.count()
    filtered_records = rawdata_summaries.count()
     # Paginate the queryset to display 20 records per page
    paginator = Paginator(rawdata_summaries, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # Render the template with paginated data
    return render(
        request,
        "wgs_app/show_referred_data.html",
        {"page_obj": page_obj,
         "total_records": total_records,
         "filtered_records": filtered_records,
         "q": q,
         },  # only send page_obj
    )





@login_required
@role_required(ROLE_ADMIN, ROLE_CHECKER, ROLE_ENCODER)
@require_POST
@transaction.atomic
def delete_final_data(request, pk):

    final_item = get_object_or_404(Final_Data, pk=pk)
    if final_item.f_Batch_id and not can_manage_batch(request.user, final_item.f_Batch_id):
        messages.error(request, "You can only delete final records from batches that you created.")
        return redirect('show_final_data')
    if final_item.f_Batch_id_id:
        try:
            from apps.verification.models import VerificationCase
            verification_case = (
                VerificationCase.objects
                .filter(final_batch_id=final_item.f_Batch_id_id, final_accession__isnull=True)
                .exclude(status=VerificationCase.STATUS_WITHDRAWN)
                .first()
            )
        except (LookupError, ProgrammingError, OperationalError):
            verification_case = None
        if verification_case_blocks_operational_outputs(verification_case):
            messages.error(
                request,
                "Accession deletion is disabled while this case is in verification. Withdraw it or wait until Head marks it Ready for Release."
            )
            return redirect('show_final_data')

    if request.method == "POST":
        accession = final_item.f_AccessionNo
        final_item.delete()   #  This triggers the signal

        messages.success(
            request,
            f"Record {accession} deleted. Concordance report invalidated."
        )

        return redirect('show_final_data')

    messages.error(request, "Invalid request for deleting Final data.")
    return redirect('show_final_data')



@login_required
@role_required(ROLE_ADMIN, ROLE_CHECKER, ROLE_ENCODER)
@require_POST
@transaction.atomic
def delete_referred_data(request, pk):

    referred_item = get_object_or_404(Referred_Data, pk=pk)
    if referred_item.Batch_id and not can_manage_batch(request.user, referred_item.Batch_id):
        messages.error(request, "You can only delete referred records from batches that you created.")
        return redirect('show_referred_data')

    if request.method == "POST":
        accession = referred_item.AccessionNo
        referred_item.delete()   #  This triggers the signal

        messages.success(
            request,
            f"Record {accession} deleted. Concordance report invalidated."
        )

        return redirect('show_referred_data')

    messages.error(request, "Invalid request for deleting Final data.")
    return redirect('show_referred_data')




@login_required
@role_required(ROLE_ADMIN, ROLE_CHECKER)
@require_POST
@transaction.atomic
def delete_all_final_data(request):
    Final_Data.objects.all().delete()
    messages.success(request, "Final Referred Isolates have been deleted successfully.")
    return redirect('show_final_data')  # Redirect to the table view



@login_required
@role_required(ROLE_ADMIN, ROLE_CHECKER)
@require_POST
def delete_all_referred_data(request):
    Referred_Data.objects.all().delete()
    messages.success(request, "Referred Isolates have been deleted successfully.")
    return redirect('show_referred_data')  # Redirect to the table view



@login_required
@role_required(ROLE_ADMIN, ROLE_CHECKER)
@require_POST
@transaction.atomic
def delete_finaldata_by_date(request):
    if request.method == "POST":
        upload_date_str = request.POST.get("upload_date")
        print(" Received upload_date_str:", upload_date_str)

        if not upload_date_str:
            messages.error(request, "Please select an upload date to delete.")
            return redirect("show_final_data")

        # Use Django’s date parser
        upload_date = parse_date(upload_date_str)

        if not upload_date:
            messages.error(request, f"Invalid date format: {upload_date_str}")
            return redirect("show_final_data")

        deleted_count, _ = Final_Data.objects.filter(f_Date_Modified__date=upload_date).delete()
        messages.success(request, f" Deleted {deleted_count} Final Isolates records uploaded on {upload_date}.")
        return redirect("show_final_data")

    messages.error(request, "Invalid request method.")
    return redirect("show_final_data")


@login_required
@role_required(ROLE_ADMIN, ROLE_CHECKER)
@require_POST
def delete_referreddata_by_date(request):
    if request.method == "POST":
        upload_date_str = request.POST.get("upload_date")
        print(" Received upload_date_str:", upload_date_str)

        if not upload_date_str:
            messages.error(request, "Please select an upload date to delete.")
            return redirect("show_referred_data")

        # Use Django’s date parser
        upload_date = parse_date(upload_date_str)

        if not upload_date:
            messages.error(request, f"Invalid date format: {upload_date_str}")
            return redirect("show_referred_data")

        deleted_count, _ = Referred_Data.objects.filter(Date_Modified__date=upload_date).delete()
        messages.success(request, f" Deleted {deleted_count} Final Isolates records uploaded on {upload_date}.")
        return redirect("show_referred_data")

    messages.error(request, "Invalid request method.")
    return redirect("show_referred_data")



############# Uploading of Final Antibiotic Entries


########### HELPERS ################
# ─────────────────────────────────────────────────────────────────────────────
# COLUMN PATTERNS  (all retest / ARSRL results)
#   <abx>_nd<n>        → retest disk value
#   <abx>_nd<n>_ris    → retest disk RIS
#   <abx>_nm           → retest MIC value
#   <abx>_nm_ris       → retest MIC RIS
# ─────────────────────────────────────────────────────────────────────────────

DISK_PAT     = re.compile(r'^([a-z0-9]{2,6})_(nd\d+(?:_\d+)?)$')
DISK_RIS_PAT = re.compile(r'^([a-z0-9]{2,6})_(nd\d+(?:_\d+)?)_ris$')
MIC_PAT      = re.compile(r'^([a-z0-9]{2,6})_(nm)$')
MIC_RIS_PAT  = re.compile(r'^([a-z0-9]{2,6})_(nm)_ris$')

# Excel col → f_ars_* (ARSRL phenotype)
PHENO_FIELD_MAP = {
    'ampc':      'f_ars_ampC',
    'esbl':      'f_ars_ESBL',
    'carb':      'f_ars_CARB',
    'mbl':       'f_ars_MBL',
    'bl':        'f_ars_BL',
    'mr':        'f_ars_MR',
    'meca':      'f_ars_mecA',
    'icr':       'f_ars_ICR',
    'ecim':      'f_ars_ECIM',
    'mcim':      'f_ars_MCIM',
    'ecim.mcim': 'f_ars_EC_MCIM',
}

# Excel col → f_* (site phenotype)
SITE_PHENO_MAP = {
    'ampc': 'f_ampC',
    'esbl': 'f_ESBL',
    'carb': 'f_CARB',
    'mbl':  'f_MBL',
    'bl':   'f_BL',
    'mr':   'f_MR',
    'meca': 'f_mecA',
    'icr':  'f_ICR',
}


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _clean(val, default=""):
    if val is None:
        return default
    try:
        if pd.isna(val):
            return default
    except TypeError:
        pass
    s = str(val).strip()
    return s if s.lower() not in ("none", "nan", "nat", "null", "") else default


def _clean_ris(val):
    ris = _clean(val).upper()
    if not ris:
        return "", False
    if len(ris) > 4 or "," in ris:
        return "", True
    return ris, False


def _date(val):
    if val is None:
        return None
    if isinstance(val, (date, datetime)):
        return val.date() if isinstance(val, datetime) else val
    try:
        return datetime.strptime(str(val).strip(), "%Y-%m-%d").date()
    except Exception:
        return None


def _int(val):
    if val is None:
        return None
    try:
        if pd.isna(val):
            return None
    except TypeError:
        pass
    try:
        return int(float(str(val).strip()))
    except Exception:
        return None


def _decimal(val):

    if val is None:
        return None, ""
    try:
        if pd.isna(val):
            return None, ""
    except TypeError:
        pass

    s = str(val).strip()
    operand = ""

    if s.lower() in {"", "nan", "nat", "none", "null"}:
        return None, ""

    for op in ("<=", ">=", "<", ">"):
        if s.startswith(op):
            operand = op
            s = s[len(op):]
            break

    try:
        value = Decimal(s)
        if not value.is_finite():
            return None, operand
        return value, operand
    except (InvalidOperation, ValueError):
        return None, ""
    

def _pheno(val):
    s = _clean(val)
    return s if s in ("(+)", "(-)", "NT") else "n/a"


# ─────────────────────────────────────────────────────────────────────────────
# ANTIBIOTIC COLUMN DETECTION
# ─────────────────────────────────────────────────────────────────────────────

def _parse_abx_columns(headers):

    groups = {}
    markers = {"rt", "val", "value", "ris", "op", "operand", "enris"}

    def normalize_col(col):
        text = str(col or "").strip().lower()
        text = re.sub(r"[\s\-.]+", "_", text)
        text = re.sub(r"_+", "_", text)
        return text.strip("_")

    def column_info(col):
        parts = normalize_col(col).split("_")
        if len(parts) < 2:
            return None

        method = parts[1]
        if not (method.startswith("nd") or method == "nm"):
            return None

        is_retest = "rt" in parts[2:]
        field = ""
        if "ris" in parts[2:]:
            field = "ris"
        elif "op" in parts[2:] or "operand" in parts[2:]:
            field = "op"
        elif "val" in parts[2:] or "value" in parts[2:]:
            field = "val"
        else:
            field = "val"

        method_tokens = [method]
        for token in parts[2:]:
            if token in markers:
                break
            method_tokens.append(token)

        code = f"{parts[0].upper()}_{'_'.join(method_tokens).upper()}"
        group_key = f"{code}_RT" if is_retest else code

        return {
            "code": code,
            "group_key": group_key,
            "field": field,
            "is_disk": method.startswith("nd"),
            "is_mic": method == "nm",
            "is_retest": is_retest,
        }

    for idx, col in enumerate(headers):
        info = column_info(col)
        if not info:
            continue

        g = groups.setdefault(info["group_key"], {
            "disk_col": None,
            "disk_ris_col": None,
            "mic_col": None,
            "mic_ris_col": None,
            "mic_op_col": None,
            "disk_code": None,
            "mic_code": None,
        })

        code = f"{info['code']}_RT" if info["is_retest"] else info["code"]

        if info["is_disk"] and info["field"] == "ris":
            g["disk_ris_col"] = idx
            g["disk_code"] = code
        elif info["is_disk"]:
            g["disk_col"] = idx
            g["disk_code"] = code
        elif info["is_mic"] and info["field"] == "op":
            g["mic_op_col"] = idx
            g["mic_code"] = code
        elif info["is_mic"] and info["field"] == "ris":
            g["mic_ris_col"] = idx
            g["mic_code"] = code
        elif info["is_mic"]:
            g["mic_col"] = idx
            g["mic_code"] = code

    return groups


def _antibiotic_lookup_key(code):
    text = str(code or "").strip().upper()
    text = re.sub(r"[^A-Z0-9]+", "_", text)
    return re.sub(r"_+", "_", text).strip("_")


def _add_antibiotic_aliases(antibiotics, antibiotic):
    for code in (antibiotic.Abx_code, antibiotic.Whonet_Abx):
        raw_code = str(code or "").strip().upper()
        if not raw_code:
            continue
        antibiotics[raw_code] = antibiotic
        antibiotics[_antibiotic_lookup_key(raw_code)] = antibiotic


def _resolve_antibiotic(antibiotics, *codes):
    for code in codes:
        raw_code = str(code or "").strip().upper()
        if not raw_code:
            continue
        antibiotic = antibiotics.get(raw_code) or antibiotics.get(_antibiotic_lookup_key(raw_code))
        if antibiotic:
            return antibiotic
    return None


def _canonical_whonet_code(antibiotic, fallback):
    return (antibiotic.Whonet_Abx or fallback or "").strip().upper()



def _empty_group(abx):
    return {
        'abx_code':     abx,
        'disk_label':   None,
        'disk_col':     None,
        'disk_ris_col': None,
        'mic_col':      None,
        'mic_ris_col':  None,
    }




def _resolve_effective_year(spec_date):
    """
    Returns the nearest breakpoint year relative to the specimen year.

    Rules:
    1. Use closest breakpoint year <= specimen year
    2. If specimen year is earlier than all breakpoints → use earliest
    3. If specimen year is later than all breakpoints → use latest
    """

    years = list(
        BreakpointsTable.objects
        .values_list("Year", flat=True)
        .distinct()
    )

    if not years:
        return None

    years = sorted(int(y) for y in years)

    if spec_date:
        specimen_year = spec_date.year

        # find closest <= specimen year
        eligible = [y for y in years if y <= specimen_year]

        if eligible:
            return str(max(eligible))

        # specimen earlier than all breakpoints
        return str(years[0])

    # if specimen date missing → use newest
    return str(years[-1])


def _year_as_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _breakpoint_has_emerging_flag(bp):
    return (
        bool(bp.Emerging_Org_Flag)
        or bool(bp.Emerging_Abx_Flag)
        or bool((bp.Emerging_Pheno_Flag or "").strip())
    )


def _nearest_breakpoint_year(years, target_year):
    years = list(years)
    if not years:
        return None
    if target_year is None:
        return max(years)
    return sorted(
        years,
        key=lambda candidate_year: (
            abs(candidate_year - target_year),
            0 if candidate_year <= target_year else 1,
            -candidate_year,
        ),
    )[0]


def _select_breakpoint_candidate(candidates, target_year, org_code, require_emerging_flag=False):
    wanted_org = (org_code or "").strip().lower()

    if require_emerging_flag:
        candidates = [
            bp for bp in candidates
            if _breakpoint_has_emerging_flag(bp)
        ]

    pools = []
    if wanted_org:
        pools.append([
            bp for bp in candidates
            if (bp.Org or "").strip().lower() == wanted_org
        ])
    pools.append([
        bp for bp in candidates
        if not (bp.Org or "").strip()
    ])

    for pool in pools:
        by_year = {}
        for bp in pool:
            bp_year = _year_as_int(bp.Year)
            if bp_year is not None:
                by_year.setdefault(bp_year, []).append(bp)

        selected_year = _nearest_breakpoint_year(by_year.keys(), target_year)
        if selected_year is None:
            continue

        return sorted(
            by_year[selected_year],
            key=lambda bp: (
                0 if not (bp.Spec_code or "").strip() else 1,
                bp.pk or 0,
            ),
        )[0]

    return None


def _find_best_breakpoint(abx_code, year, method, org_code):
    if not abx_code or not method:
        return None

    abx_code = str(abx_code).strip().upper()
    org_code = (org_code or "").strip()
    target_year = _year_as_int(year)

    candidates = list(
        BreakpointsTable.objects
        .filter(
            Q(Antibiotic_list_id=abx_code) | Q(Whonet_Abx__iexact=abx_code),
            Test_Method__iexact=method,
        )
        .filter(
            Q(Org__iexact=org_code) |
            Q(Org__isnull=True) |
            Q(Org="")
        )
    )

    if not candidates:
        return None

    return _select_breakpoint_candidate(candidates, target_year, org_code)


def _find_best_emerging_breakpoint(abx_code, year, method, org_code):
    if not abx_code or not method:
        return None

    abx_code = str(abx_code).strip().upper()
    org_code = (org_code or "").strip()
    target_year = _year_as_int(year)

    candidates = list(
        BreakpointsTable.objects
        .filter(
            Q(Antibiotic_list_id=abx_code) | Q(Whonet_Abx__iexact=abx_code),
            Test_Method__iexact=method,
        )
        .filter(
            Q(Org__iexact=org_code) |
            Q(Org__isnull=True) |
            Q(Org="")
        )
    )

    if not candidates:
        return None

    return _select_breakpoint_candidate(
        candidates,
        target_year,
        org_code,
        require_emerging_flag=True,
    )


def _build_breakpoint_resolver():
    breakpoints = list(BreakpointsTable.objects.all())
    years = sorted({
        year
        for year in (_year_as_int(bp.Year) for bp in breakpoints)
        if year is not None
    })
    by_code_method = {}

    for bp in breakpoints:
        method = (bp.Test_Method or "").strip().upper()
        codes = {
            (bp.Whonet_Abx or "").strip().upper(),
            (bp.Antibiotic_list_id or "").strip().upper(),
        }
        for code in codes - {""}:
            by_code_method.setdefault((code, method), []).append(bp)

    def effective_year(spec_date):
        if not years:
            return None
        if not spec_date:
            return years[-1]
        eligible = [year for year in years if year <= spec_date.year]
        return max(eligible) if eligible else years[0]

    def resolve(abx_code, spec_date, method, org_code, require_emerging_flag=False):
        code = (abx_code or "").strip().upper()
        method = (method or "").strip().upper()
        org_code = (org_code or "").strip()
        target_year = spec_date.year if spec_date else effective_year(spec_date)
        candidates = [
            bp
            for bp in by_code_method.get((code, method), [])
            if not (bp.Org or "").strip()
            or (bp.Org or "").strip().lower() == org_code.lower()
        ]
        return _select_breakpoint_candidate(
            candidates,
            target_year,
            org_code,
            require_emerging_flag=require_emerging_flag,
        )

    return effective_year, resolve




############################
# BREAKPOINT HELPERS
############################

def _apply_bp_to_entry(entry, bp, is_disk, alert_mic=False, set_relation=True, flag_bp=None):

    if set_relation:
        linked_breakpoints = [bp]
        if flag_bp and flag_bp.pk != bp.pk:
            linked_breakpoints.append(flag_bp)
        entry.ab_breakpoints_id.set(linked_breakpoints)

    flag_source = flag_bp or bp

    entry.ab_Site_Org = bp.Org
    entry.ab_Org_Flag = bool(flag_source.Emerging_Org_Flag)
    entry.ab_Abx_Flag = bool(flag_source.Emerging_Abx_Flag)
    entry.ab_Abx_Phenotype = flag_source.Emerging_Pheno_Flag or ""

    entry.ab_R_breakpoint   = bp.R_val
    entry.ab_I_breakpoint   = bp.I_val
    entry.ab_SDD_breakpoint = bp.SDD_val
    entry.ab_S_breakpoint   = bp.S_val

    if not is_disk:
        entry.ab_Alert_val = bp.Alert_val if alert_mic else ""


def _apply_bp_to_retest_entry(entry, bp, is_disk, alert_mic=False, set_relation=True, flag_bp=None):

    if set_relation:
        linked_breakpoints = [bp]
        if flag_bp and flag_bp.pk != bp.pk:
            linked_breakpoints.append(flag_bp)
        entry.ab_breakpoints_id.set(linked_breakpoints)

    flag_source = flag_bp or bp

    entry.ab_Ret_Org = bp.Org
    entry.ab_Org_Flag = bool(flag_source.Emerging_Org_Flag)
    entry.ab_Abx_Flag = bool(flag_source.Emerging_Abx_Flag)
    entry.ab_Abx_Phenotype = flag_source.Emerging_Pheno_Flag or ""

    entry.ab_Ret_R_breakpoint   = bp.R_val
    entry.ab_Ret_I_breakpoint   = bp.I_val
    entry.ab_Ret_SDD_breakpoint = bp.SDD_val
    entry.ab_Ret_S_breakpoint   = bp.S_val

    if not is_disk:
        entry.ab_Retest_Alert_val = bp.Alert_val if alert_mic else ""


############################
# CLEAR BREAKPOINT FIELDS
############################

def _clear_bp_fields(entry):

    entry.ab_Site_Org = None
    entry.ab_Org_Flag = False
    entry.ab_Abx_Flag = False
    entry.ab_Abx_Phenotype = ""

    entry.ab_R_breakpoint   = None
    entry.ab_I_breakpoint   = None
    entry.ab_SDD_breakpoint = None
    entry.ab_S_breakpoint   = None

    entry.ab_Alert_val = ""
    entry.ab_Disk_RIS = ""
    entry.ab_MIC_RIS = ""


def _clear_retest_bp_fields(entry):

    entry.ab_Ret_Org = None
    entry.ab_Org_Flag = False
    entry.ab_Abx_Flag = False
    entry.ab_Abx_Phenotype = ""

    entry.ab_Ret_R_breakpoint   = None
    entry.ab_Ret_I_breakpoint   = None
    entry.ab_Ret_SDD_breakpoint = None
    entry.ab_Ret_S_breakpoint   = None

    entry.ab_Retest_Alert_val = ""
    entry.ab_Retest_Disk_RIS = ""
    entry.ab_Retest_MIC_RIS = ""


def _bulk_upsert_antibiotic_entries(entry_model, pending, overwrite=True):
    """
    Persist the final state of parsed antibiotic cells in batches.

    pending maps (mode, isolate_fk_value, code) to either:
      - None, meaning delete the matching entry
      - {"isolate": obj, "defaults": {...}}, meaning create/update
    """
    if not pending:
        return 0, 0, 0

    is_final = entry_model is Final_AntibioticEntry
    fk_name = "ab_idNum_f_referred" if is_final else "ab_idNum_referred"
    fk_id_name = f"{fk_name}_id"
    isolate_fk_values = {key[1] for key in pending}

    existing_entries = list(
        entry_model.objects.filter(**{f"{fk_id_name}__in": isolate_fk_values})
    )
    existing_main = {}
    existing_retest = {}
    for entry in existing_entries:
        main_code = (entry.ab_Abx_code or "").strip().upper()
        retest_code = (entry.ab_Retest_Abx_code or "").strip().upper()
        if main_code:
            existing_main.setdefault((getattr(entry, fk_id_name), main_code), entry)
        if retest_code:
            existing_retest.setdefault((getattr(entry, fk_id_name), retest_code), entry)

    to_create = []
    to_update = {}
    delete_ids = set()
    update_fields = set()

    for (mode, isolate_fk_value, code), operation in pending.items():
        lookup = existing_retest if mode == "retest" else existing_main
        entry = lookup.get((isolate_fk_value, code))

        if operation is None:
            if overwrite and entry and entry.pk:
                delete_ids.add(entry.pk)
            continue

        defaults = operation["defaults"]
        for ris_field in (
            "ab_Disk_enRIS",
            "ab_MIC_enRIS",
            "ab_Retest_Disk_enRIS",
            "ab_Retest_MIC_enRIS",
        ):
            if ris_field in defaults and defaults[ris_field]:
                defaults[ris_field] = str(defaults[ris_field]).upper()

        if entry is None:
            entry = entry_model(**{fk_id_name: isolate_fk_value}, **defaults)
            to_create.append(entry)
            lookup[(isolate_fk_value, code)] = entry
        elif not overwrite:
            continue
        else:
            for field_name, value in defaults.items():
                setattr(entry, field_name, value)
            to_update[entry.pk] = entry
            update_fields.update(defaults)

    if delete_ids:
        entry_model.objects.filter(pk__in=delete_ids).delete()

    if to_create:
        entry_model.objects.bulk_create(to_create, batch_size=1000)

    if to_update and update_fields:
        entry_model.objects.bulk_update(
            list(to_update.values()),
            sorted(update_fields),
            batch_size=1000,
        )

    return len(to_create), len(to_update), len(delete_ids)


def _bulk_replace_breakpoint_links(entry_model, entry_breakpoints):
    if not entry_breakpoints:
        return

    m2m_field = entry_model._meta.get_field("ab_breakpoints_id")
    through = m2m_field.remote_field.through
    source_field = m2m_field.m2m_field_name()
    target_field = m2m_field.m2m_reverse_field_name()
    entry_ids = list(entry_breakpoints)

    through.objects.filter(
        **{f"{source_field}_id__in": entry_ids}
    ).delete()

    links = [
        through(
            **{
                f"{source_field}_id": entry_id,
                f"{target_field}_id": breakpoint_id,
            }
        )
        for entry_id, breakpoint_ids in entry_breakpoints.items()
        for breakpoint_id in dict.fromkeys(breakpoint_ids)
    ]
    if links:
        through.objects.bulk_create(links, batch_size=2000)


def _recalculate_antibiotic_ris(entry, determine_ris):
    entry.ab_Disk_RIS = determine_ris(
        entry.ab_Disk_value,
        entry.ab_R_breakpoint,
        entry.ab_I_breakpoint,
        entry.ab_S_breakpoint,
        entry.ab_SDD_breakpoint,
        is_disk=True,
    )
    entry.ab_MIC_RIS = determine_ris(
        entry.ab_MIC_value,
        entry.ab_R_breakpoint,
        entry.ab_I_breakpoint,
        entry.ab_S_breakpoint,
        entry.ab_SDD_breakpoint,
    )
    entry.ab_Retest_Disk_RIS = determine_ris(
        entry.ab_Retest_DiskValue,
        entry.ab_Ret_R_breakpoint,
        entry.ab_Ret_I_breakpoint,
        entry.ab_Ret_S_breakpoint,
        entry.ab_Ret_SDD_breakpoint,
        is_disk=True,
    )
    entry.ab_Retest_MIC_RIS = determine_ris(
        entry.ab_Retest_MICValue,
        entry.ab_Ret_R_breakpoint,
        entry.ab_Ret_I_breakpoint,
        entry.ab_Ret_S_breakpoint,
        entry.ab_Ret_SDD_breakpoint,
    )


############################
# SAVE SITE ANTIBIOTIC
############################

def _save_site_entry(
    isolate,
    abx_code,
    disk_int,
    disk_ris,
    mic_value,
    mic_operand,
    mic_ris,
    resolved_org,
    effective_year,
    abx_obj=None
):

    entry, _ = AntibioticEntry.objects.update_or_create(

        ab_idNum_referred=isolate,
        ab_Abx_code=abx_code.upper(),

        defaults={

            "ab_AccessionNo": isolate.AccessionNo,
            "ab_RefNo": isolate.RefNo,

            "ab_Antibiotic": abx_obj.Antibiotic if abx_obj else abx_code,
            "ab_Abx": abx_obj.Abx_code if abx_obj else abx_code,

            "ab_Disk_value": disk_int,
            "ab_Disk_enRIS": disk_ris,

            "ab_MIC_value": mic_value,
            "ab_MIC_operand": mic_operand,
            "ab_MIC_enRIS": mic_ris,
        }
    )

    entry.ab_breakpoints_id.clear()

    bp_applied = False

    if disk_int is not None:

        bp = _find_best_breakpoint(abx_code, effective_year, "DISK", resolved_org)

        if bp:
            flag_bp = _find_best_emerging_breakpoint(
                abx_code, effective_year, "DISK", resolved_org
            )
            _apply_bp_to_entry(entry, bp, True, flag_bp=flag_bp)
            bp_applied = True

    if mic_value is not None:

        bp = _find_best_breakpoint(abx_code, effective_year, "MIC", resolved_org)

        if bp:
            flag_bp = _find_best_emerging_breakpoint(
                abx_code, effective_year, "MIC", resolved_org
            )
            _apply_bp_to_entry(entry, bp, False, flag_bp=flag_bp)
            bp_applied = True

    if not bp_applied:
        _clear_bp_fields(entry)

    entry.save()

    return entry


def _save_final_site_entry(
    isolate,
    abx_code,
    disk_int,
    disk_ris,
    mic_value,
    mic_operand,
    mic_ris,
    resolved_org,
    effective_year,
    abx_obj=None
):

    entry, created = Final_AntibioticEntry.objects.update_or_create(

        ab_idNum_f_referred=isolate,
        ab_Abx_code=abx_code.upper(),

        defaults={

            "ab_AccessionNo": isolate.f_AccessionNo,
            "ab_RefNo": isolate.f_RefNo,

            "ab_Antibiotic": abx_obj.Antibiotic if abx_obj else abx_code,
            "ab_Abx": abx_obj.Abx_code if abx_obj else abx_code,

            "ab_Disk_value": disk_int,
            "ab_Disk_enRIS": disk_ris,

            "ab_MIC_value": mic_value,
            "ab_MIC_operand": mic_operand,
            "ab_MIC_enRIS": mic_ris,
        }
    )

    entry.ab_breakpoints_id.clear()

    bp_applied = False

    if disk_int is not None:

        bp = _find_best_breakpoint(abx_code, effective_year, "DISK", resolved_org)

        if bp:
            flag_bp = _find_best_emerging_breakpoint(
                abx_code, effective_year, "DISK", resolved_org
            )
            _apply_bp_to_entry(entry, bp, True, flag_bp=flag_bp)
            bp_applied = True

    if mic_value is not None:

        bp = _find_best_breakpoint(abx_code, effective_year, "MIC", resolved_org)

        if bp:
            flag_bp = _find_best_emerging_breakpoint(
                abx_code, effective_year, "MIC", resolved_org
            )
            _apply_bp_to_entry(entry, bp, False, alert_mic=True, flag_bp=flag_bp)
            bp_applied = True

    if not bp_applied:
        _clear_bp_fields(entry)

    entry.save()

    return entry, created


############################
# SAVE RETEST ANTIBIOTIC
############################

def _save_retest_entry(
    entry_model,
    isolate,
    abx_code,
    disk_int,
    disk_ris,
    mic_value,
    mic_operand,
    mic_ris,
    resolved_ars_org,
    effective_year,
    abx_obj=None
):

    # --------------------------------
    # Resolve accession + refno
    # --------------------------------
    accession = getattr(isolate, "AccessionNo", None) or getattr(isolate, "f_AccessionNo", "")
    refno = getattr(isolate, "RefNo", None) or getattr(isolate, "f_RefNo", "")

    # --------------------------------
    # Determine correct FK field
    # --------------------------------
    fk_field = (
        "ab_idNum_f_referred"
        if entry_model.__name__ == "Final_AntibioticEntry"
        else "ab_idNum_referred"
    )

    filter_kwargs = {
        fk_field: isolate,
        "ab_Retest_Abx_code": abx_code.upper()
    }

    # --------------------------------
    # Create / Update entry
    # --------------------------------
    entry, created = entry_model.objects.update_or_create(

        **filter_kwargs,

        defaults={

            "ab_AccessionNo": accession,
            "ab_RefNo": refno,

            "ab_Retest_Antibiotic": abx_obj.Antibiotic if abx_obj else abx_code,
            "ab_Retest_Abx": abx_obj.Abx_code if abx_obj else abx_code,

            "ab_Retest_DiskValue": disk_int,
            "ab_Retest_Disk_enRIS": disk_ris,

            "ab_Retest_MICValue": mic_value,
            "ab_Retest_MIC_operand": mic_operand,
            "ab_Retest_MIC_enRIS": mic_ris,
        }
    )

    # --------------------------------
    # Clear breakpoints
    # --------------------------------
    entry.ab_breakpoints_id.clear()

    bp_applied = False

    # --------------------------------
    # DISK BREAKPOINT
    # --------------------------------
    if disk_int is not None:

        bp = _find_best_breakpoint(abx_code, effective_year, "DISK", resolved_ars_org)

        if bp:
            flag_bp = _find_best_emerging_breakpoint(
                abx_code, effective_year, "DISK", resolved_ars_org
            )
            _apply_bp_to_retest_entry(entry, bp, True, flag_bp=flag_bp)
            bp_applied = True

    # --------------------------------
    # MIC BREAKPOINT
    # --------------------------------
    if mic_value is not None:

        bp = _find_best_breakpoint(abx_code, effective_year, "MIC", resolved_ars_org)

        if bp:
            flag_bp = _find_best_emerging_breakpoint(
                abx_code, effective_year, "MIC", resolved_ars_org
            )
            _apply_bp_to_retest_entry(entry, bp, False, alert_mic=True, flag_bp=flag_bp)
            bp_applied = True

    # --------------------------------
    # No breakpoint found
    # --------------------------------
    if not bp_applied:
        _clear_retest_bp_fields(entry)

    entry.save()

    return entry, created

########## END OF HELPERS ################
# @login_required
# @require_POST
# @transaction.atomic
# def upload_final_antibiotics(request):

#     file = request.FILES.get("FinalAntibioticFile")

#     if not file:
#         messages.error(request, "No file uploaded")
#         return redirect("show_final_antibiotic")

#     try:
#         wb = openpyxl.load_workbook(file, read_only=True, data_only=True)
#     except Exception as e:
#         messages.error(request, str(e))
#         return redirect("show_final_antibiotic")

#     created = 0
#     updated = 0
#     skipped = 0

#     # -----------------------------
#     # LOAD REFERENCE DATA
#     # -----------------------------

#     breakpoint_keys = set(
#         (x or "").strip().upper()
#         for x in BreakpointsTable.objects.values_list("Whonet_Abx", flat=True)
#     )

#     antibiotics = {
#         (a.Abx_code or "").strip().upper(): a
#         for a in Antibiotic_List.objects.filter(Retest=True)
#     }

#     def normalize_accession(val):
#         return str(val).strip().replace(" ", "").upper()

#     isolates = {
#         normalize_accession(i.f_AccessionNo): i
#         for i in Final_Data.objects.all()
#     }
#     # -----------------------------
#     # PROCESS WORKBOOK
#     # -----------------------------

#     for sheet in wb.sheetnames:

#         ws = wb[sheet]
#         rows = ws.iter_rows(values_only=True)

#         headers = next(rows)
#         headers_lower = [str(c).lower().strip() if c else "" for c in headers]

#         if "f_accessionno" not in headers_lower:
#             continue

#         accession_idx = headers_lower.index("f_accessionno")

#         abx_groups = {
#             k: v for k, v in _parse_abx_columns(headers).items()
#             if v["disk_col"] is not None or v["mic_col"] is not None
#         }

#         for row in rows:

#             accession = _clean(row[accession_idx]).upper()

#             if not accession:
#                 continue

#             isolate = isolates.get(accession)

#             if not isolate:
#                 skipped += 1
#                 continue

#             effective_year = _resolve_effective_year(isolate.f_Spec_Date)
#             resolved_ars_org = (isolate.f_ars_OrgCode or "").strip()

#             for abx_code, grp in abx_groups.items():

#                 abx_code = abx_code.upper()

#                 # resolve antibiotic object
#                 abx_obj = antibiotics.get(abx_code)

#                 if not abx_obj:
#                     continue

#                 disk_key = headers[grp["disk_col"]].upper() if grp["disk_col"] is not None else None
#                 mic_key = headers[grp["mic_col"]].upper() if grp["mic_col"] is not None else None

#                 # remove accidental _OP
#                 if mic_key and mic_key.endswith("_OP"):
#                     mic_key = mic_key.replace("_OP", "")

#                 # ----------------------------
#                 # EXCEL VALUES
#                 # ----------------------------

#                 disk_raw = row[grp["disk_col"]] if grp["disk_col"] is not None else None
#                 disk_ris = _clean(row[grp["disk_ris_col"]]) if grp["disk_ris_col"] is not None else ""

#                 mic_raw = row[grp["mic_col"]] if grp["mic_col"] is not None else None
#                 mic_ris = _clean(row[grp["mic_ris_col"]]) if grp["mic_ris_col"] is not None else ""

#                 mic_operand = ""
#                 if grp["mic_op_col"] is not None:
#                     mic_operand = _clean(row[grp["mic_op_col"]])

#                 disk_val = _int(disk_raw)
#                 mic_val, parsed_operand = _decimal(mic_raw)

#                 if not mic_operand:
#                     mic_operand = parsed_operand

#                 # ----------------------------
#                 # DELETE IF EMPTY
#                 # --------------------  --------

#                 if disk_val is None and mic_val is None:

#                     Final_AntibioticEntry.objects.filter(
#                         ab_idNum_f_referred=isolate,
#                         ab_Retest_Abx_code=abx_code
#                     ).delete()

#                     continue

#                 # ----------------------------
#                 # CREATE / UPDATE ENTRY
#                 # ----------------------------
#                 whonet_code = disk_key if disk_val is not None else mic_key

#                 entry, created_flag = Final_AntibioticEntry.objects.update_or_create(

#                     ab_idNum_f_referred=isolate,
#                     ab_Retest_Abx_code=whonet_code,

#                     defaults={

#                         "ab_AccessionNo": isolate.f_AccessionNo,
#                         "ab_RefNo": isolate.f_RefNo,

#                         "ab_Retest_Antibiotic": abx_obj.Antibiotic,
#                         "ab_Retest_Abx": abx_obj.Abx_code,
                        
#                         "ab_Retest_DiskValue": disk_val,
#                         "ab_Retest_MICValue": mic_val,

#                         "ab_Retest_Disk_enRIS": disk_ris,
#                         "ab_Retest_MIC_enRIS": mic_ris,
#                         "ab_Retest_MIC_operand": mic_operand,
#                     }
#                 )

#                 entry.ab_breakpoints_id.clear()

#                 ret_bp_applied = False

#                 # ----------------------------
#                 # DISK BREAKPOINT
#                 # ----------------------------

#                 if disk_val is not None and disk_key and disk_key in breakpoint_keys:

#                     bp_disk = BreakpointsTable.objects.filter(
#                         Whonet_Abx__iexact=disk_key,
#                         Year=effective_year,
#                         Test_Method="DISK",
#                         Org__in=[resolved_ars_org, ""]
#                     ).order_by("-Org").first()

#                     if bp_disk:

#                         entry.ab_breakpoints_id.set([bp_disk])

#                         entry.ab_Ret_Org = bp_disk.Org
#                         entry.ab_Org_Flag = bool(bp_disk.Emerging_Org_Flag)
#                         entry.ab_Abx_Flag = bool(bp_disk.Emerging_Abx_Flag)
#                         entry.ab_Abx_Phenotype = bp_disk.Emerging_Pheno_Flag or ""

#                         entry.ab_Ret_R_breakpoint = bp_disk.R_val
#                         entry.ab_Ret_I_breakpoint = bp_disk.I_val
#                         entry.ab_Ret_SDD_breakpoint = bp_disk.SDD_val
#                         entry.ab_Ret_S_breakpoint = bp_disk.S_val

#                         ret_bp_applied = True

#                 # ----------------------------
#                 # MIC BREAKPOINT
#                 # ----------------------------

#                 if mic_val is not None and mic_key and mic_key in breakpoint_keys:

#                     bp_mic = BreakpointsTable.objects.filter(
#                         Whonet_Abx__iexact=mic_key,
#                         Year=effective_year,
#                         Test_Method="MIC",
#                         Org__in=[resolved_ars_org, ""]
#                     ).order_by("-Org").first()

#                     if bp_mic:

#                         entry.ab_breakpoints_id.set([bp_mic])

#                         entry.ab_Ret_Org = bp_mic.Org
#                         entry.ab_Org_Flag = bool(bp_mic.Emerging_Org_Flag)
#                         entry.ab_Abx_Flag = bool(bp_mic.Emerging_Abx_Flag)
#                         entry.ab_Abx_Phenotype = bp_mic.Emerging_Pheno_Flag or ""

#                         entry.ab_Ret_R_breakpoint = bp_mic.R_val
#                         entry.ab_Ret_I_breakpoint = bp_mic.I_val
#                         entry.ab_Ret_SDD_breakpoint = bp_mic.SDD_val
#                         entry.ab_Ret_S_breakpoint = bp_mic.S_val

#                         entry.ab_Retest_Alert_val = bp_mic.Alert_val

#                         ret_bp_applied = True

#                 # ----------------------------
#                 # NO BREAKPOINT
#                 # ----------------------------

#                 if not ret_bp_applied:

#                     entry.ab_Ret_Org = None
#                     entry.ab_Org_Flag = False
#                     entry.ab_Abx_Flag = False
#                     entry.ab_Abx_Phenotype = ""

#                     entry.ab_Ret_R_breakpoint = None
#                     entry.ab_Ret_I_breakpoint = None
#                     entry.ab_Ret_SDD_breakpoint = None
#                     entry.ab_Ret_S_breakpoint = None

#                     entry.ab_Retest_Alert_val = ""

#                 entry.save()

#                 if created_flag:
#                     created += 1
#                 else:
#                     updated += 1

#     wb.close()

#     messages.success(
#         request,
#         f"{created} antibiotics uploaded successfully. "
#         f"{updated} updated, {skipped} skipped."
#     )

#     return redirect("show_final_antibiotic")


########### implements sentinel site & ars antitbioc fields upload
@login_required
@role_required(ROLE_ADMIN, ROLE_CHECKER, ROLE_ENCODER)
@require_POST
@transaction.atomic
def upload_final_antibiotics(request):
    from apps.home_final.signals import suspend_import_signals

    file = request.FILES.get("FinalAntibioticFile")
    overwrite = request.POST.get("overwrite", "false").lower() == "true"
    partial_update = request.POST.get("partial_update", "false").lower() == "true"
    if partial_update:
        overwrite = True

    if not file:
        messages.error(request, "No file uploaded")
        return redirect("show_final_antibiotic")

    print(f"[FINAL ABX DEBUG] Upload started: {file.name}")

    try:
        file_name = file.name.lower()
        if file_name.endswith(".csv"):
            sheets = {"Final_AntibioticEntry": pd.read_csv(file)}
        elif file_name.endswith(".tsv"):
            sheets = {"Final_AntibioticEntry": pd.read_csv(file, sep="\t")}
        elif file_name.endswith((".xlsx", ".xls")):
            sheets = pd.read_excel(file, sheet_name=None)
        else:
            messages.error(request, "Unsupported file format. Please upload CSV, TSV, XLSX, or XLS.")
            return redirect("show_final_antibiotic")
    except Exception as e:
        print(f"[FINAL ABX DEBUG] Failed to read uploaded file: {e}")
        messages.error(request, str(e))
        return redirect("show_final_antibiotic")

    created = 0
    updated = 0
    skipped = 0
    protected_skips = defaultdict(int)
    invalid_ris_values = 0
    affected_batch_ids = set()
    pending_entries = {}

    # -----------------------------
    # LOAD REFERENCE DATA
    # -----------------------------

    antibiotics = {}
    for antibiotic in Antibiotic_List.objects.all():
        _add_antibiotic_aliases(antibiotics, antibiotic)


    def normalize_accession(val):
        return str(val).strip().replace(" ", "").upper()

    def normalize_header(val):
        return re.sub(r"[^a-z0-9]", "", str(val).lower())

    isolates = {
        normalize_accession(i.f_AccessionNo): i
        for i in Final_Data.objects.all()
    }
    print(f"[FINAL ABX DEBUG] Loaded {len(isolates)} Final_Data accession(s).")

    # -----------------------------
    # PROCESS WORKBOOK
    # -----------------------------

    processed_sheets = 0
    processed_abx_sheets = 0
    matched_accessions = 0

    for sheet_name, df in sheets.items():

        df.columns = [str(c).strip() for c in df.columns]
        headers = list(df.columns)
        print(
            f"[FINAL ABX DEBUG] Sheet '{sheet_name}': "
            f"{len(df)} row(s), {len(headers)} column(s)."
        )
        header_keys = [normalize_header(c) for c in headers]
        accession_candidates = {"faccessionno", "accessionno", "faccessionnumber"}
        accession_idx = next(
            (
                idx for idx, key in enumerate(header_keys)
                if key in accession_candidates
            ),
            None
        )

        if accession_idx is None:
            print(
                f"[FINAL ABX DEBUG] Sheet '{sheet_name}' skipped: "
                "no f_AccessionNo/AccessionNo column."
            )
            continue

        processed_sheets += 1
        print(
            f"[FINAL ABX DEBUG] Sheet '{sheet_name}' accession column: "
            f"{headers[accession_idx]}"
        )

        abx_groups = {
            k: v for k, v in _parse_abx_columns(headers).items()
            if v["disk_col"] is not None or v["mic_col"] is not None
        }

        if not abx_groups:
            print(
                f"[FINAL ABX DEBUG] Sheet '{sheet_name}' skipped: "
                "no antibiotic value columns detected."
            )
            continue

        processed_abx_sheets += 1
        print(
            f"[FINAL ABX DEBUG] Sheet '{sheet_name}' detected "
            f"{len(abx_groups)} antibiotic group(s): "
            f"{', '.join(sorted(abx_groups.keys())[:20])}"
            f"{'...' if len(abx_groups) > 20 else ''}"
        )

        for row in df.itertuples(index=False, name=None):

            accession = normalize_accession(row[accession_idx])

            if not accession:
                continue

            isolate = isolates.get(accession)

            if not isolate:
                skipped += 1
                print(f"[FINAL ABX DEBUG] Accession skipped: {accession} not found in Final_Data.")
                continue

            skip_reason = _referred_bulk_upload_skip_reason(
                request.user, isolate, final=True
            )
            if skip_reason:
                skipped += 1
                protected_skips[skip_reason] += 1
                continue

            matched_accessions += 1

            if isolate.f_Batch_id_id:
                affected_batch_ids.add(isolate.f_Batch_id_id)

            for abx_code, grp in abx_groups.items():

                # ----------------------------
                # EXCEL VALUES
                # ----------------------------

                disk_raw = row[grp["disk_col"]] if grp["disk_col"] is not None else None
                disk_ris, invalid_disk_ris = (
                    _clean_ris(row[grp["disk_ris_col"]])
                    if grp["disk_ris_col"] is not None
                    else ("", False)
                )

                mic_raw = row[grp["mic_col"]] if grp["mic_col"] is not None else None
                mic_ris, invalid_mic_ris = (
                    _clean_ris(row[grp["mic_ris_col"]])
                    if grp["mic_ris_col"] is not None
                    else ("", False)
                )
                invalid_ris_values += int(invalid_disk_ris) + int(invalid_mic_ris)

                mic_operand = ""
                if grp["mic_op_col"] is not None:
                    mic_operand = _clean(row[grp["mic_op_col"]])

                disk_val = _int(disk_raw)
                mic_val, parsed_operand = _decimal(mic_raw)

                if not mic_operand:
                    mic_operand = parsed_operand

                disk_key = grp.get("disk_code")
                mic_key = grp.get("mic_code")

                if mic_key and mic_key.endswith("_OP"):
                    mic_key = mic_key.replace("_OP", "")

                is_retest = False

                if disk_key and "_RT" in disk_key:
                    is_retest = True

                if mic_key and "_RT" in mic_key:
                    is_retest = True

                if disk_key:
                    disk_key = disk_key.replace("_RT", "")

                if mic_key:
                    mic_key = mic_key.replace("_RT", "")

                whonet_code = disk_key if disk_key else mic_key

                if not whonet_code:
                    continue

                abx_obj = _resolve_antibiotic(antibiotics, whonet_code, abx_code)

                if not abx_obj:
                    skipped += 1
                    continue

                resolved_whonet_code = _canonical_whonet_code(abx_obj, whonet_code)

                # ----------------------------
                # DELETE IF EMPTY
                # ----------------------------

                if disk_val is None and mic_val is None:
                    pending_entries[
                        (
                            "retest" if is_retest else "main",
                            isolate.pk,
                            resolved_whonet_code,
                        )
                    ] = None
                    continue

                # ----------------------------
                # SAVE USING HELPER
                # ----------------------------
                whonet_code = disk_key if disk_val is not None else mic_key
                resolved_whonet_code = _canonical_whonet_code(abx_obj, whonet_code)

                if is_retest:
                    defaults = {
                        "ab_AccessionNo": isolate.f_AccessionNo,
                        "ab_RefNo": isolate.f_RefNo,
                        "ab_Retest_Antibiotic": abx_obj.Antibiotic,
                        "ab_Retest_Abx_code": resolved_whonet_code,
                        "ab_Retest_Abx": abx_obj.Abx_code,
                        "ab_Retest_DiskValue": disk_val,
                        "ab_Retest_Disk_enRIS": disk_ris,
                        "ab_Retest_MICValue": mic_val,
                        "ab_Retest_MIC_operand": mic_operand,
                        "ab_Retest_MIC_enRIS": mic_ris,
                    }
                else:
                    defaults = {
                        "ab_AccessionNo": isolate.f_AccessionNo,
                        "ab_RefNo": isolate.f_RefNo,
                        "ab_Antibiotic": abx_obj.Antibiotic,
                        "ab_Abx_code": resolved_whonet_code,
                        "ab_Abx": abx_obj.Abx_code,
                        "ab_Disk_value": disk_val,
                        "ab_Disk_enRIS": disk_ris,
                        "ab_MIC_value": mic_val,
                        "ab_MIC_operand": mic_operand,
                        "ab_MIC_enRIS": mic_ris,
                    }

                pending_entries[
                        (
                            "retest" if is_retest else "main",
                            isolate.pk,
                            resolved_whonet_code,
                        )
                ] = {
                    "isolate": isolate,
                    "defaults": defaults,
                }

    with suspend_import_signals():
        created, updated, _ = _bulk_upsert_antibiotic_entries(
            Final_AntibioticEntry,
            pending_entries,
            overwrite=overwrite,
        )

    refreshed_breakpoints = 0
    refreshed_emerging = 0
    regenerated_concordance = 0
    refreshed_tat = 0

    if affected_batch_ids:
        with suspend_import_signals():
            refreshed_breakpoints = reapply_final_breakpoints_for_batches(
                affected_batch_ids,
                debug=True
            )
        refreshed_emerging = refresh_emerging_for_batches(affected_batch_ids)
        regenerated_concordance = regenerate_batch_concordance(
            affected_batch_ids,
            user=request.user
        )
        refreshed_tat = refresh_tat_for_batches(affected_batch_ids)

    print(
        "[FINAL ABX DEBUG] Upload complete: "
        f"created={created}, updated={updated}, skipped={skipped}, "
        f"invalid_ris_values={invalid_ris_values}, "
        f"matched_accessions={matched_accessions}, "
        f"affected_batches={len(affected_batch_ids)}, "
        f"breakpoints={refreshed_breakpoints}, emerging={refreshed_emerging}, "
        f"concordance={regenerated_concordance}, tat={refreshed_tat}."
    )

    if processed_sheets == 0:
        messages.error(
            request,
            "No final antibiotic sheet was processed. Include an accession column "
            "named f_AccessionNo or AccessionNo."
        )
        return redirect("show_final_antibiotic")

    if processed_abx_sheets == 0:
        messages.error(
            request,
            "No final antibiotic columns were found. Use mapped columns like "
            "AMP_ND10_Val / AMP_ND10_RIS or retest columns like "
            "AMP_ND10_RT_Val / AMP_ND10_RT_RIS."
        )
        return redirect("show_final_antibiotic")

    if matched_accessions == 0:
        messages.error(
            request,
            "No uploaded accessions matched Final Data. Upload/copy demographics "
            "to Final first, or check the accession column values."
        )
        return redirect("show_final_antibiotic")

    if created == 0 and updated == 0:
        messages.warning(
            request,
            "Final antibiotic upload found matching accessions, but no antibiotic "
            "values were saved. Check that the mapped antibiotic value columns are not blank."
        )
        return redirect("show_final_antibiotic")

    messages.success(
        request,
        f"{created} antibiotics uploaded successfully. "
        f"{updated} updated, {skipped} skipped. "
        f"{invalid_ris_values} invalid RIS value(s) ignored. "
        f"{refreshed_breakpoints} final breakpoint record(s) refreshed. "
        f"{refreshed_emerging} emerging record(s) refreshed. "
        f"{regenerated_concordance} concordance batch report(s) refreshed. "
        f"{refreshed_tat} TAT record(s) refreshed."
    )

    if protected_skips:
        messages.warning(
            request,
            "Protected Final antibiotic accessions skipped: " + "; ".join(
                f"{count} {reason}" for reason, count in sorted(protected_skips.items())
            )
        )
    return redirect("show_final_antibiotic")



# @login_required
# @require_POST
# @transaction.atomic
# def upload_raw_antibiotics(request):

#     file = request.FILES.get("RawAntibioticFile")

#     if not file:
#         messages.error(request, "No file uploaded")
#         return redirect("show_raw_antibiotic")

#     try:
#         wb = openpyxl.load_workbook(file, read_only=True, data_only=True)
#     except Exception as e:
#         messages.error(request, str(e))
#         return redirect("show_raw_antibiotic")

#     created = 0
#     updated = 0
#     skipped = 0

#     # -----------------------------
#     # LOAD REFERENCE DATA
#     # -----------------------------

#     breakpoint_keys = set(
#         (x or "").strip().upper()
#         for x in BreakpointsTable.objects.values_list("Whonet_Abx", flat=True)
#     )

#     antibiotics = {
#         (a.Abx_code or "").strip().upper(): a
#         for a in Antibiotic_List.objects.filter(Show=True)
#     }

#     def normalize_accession(val):
#         return str(val).strip().replace(" ", "").upper()

#     isolates = {
#         normalize_accession(i.AccessionNo): i
#         for i in Referred_Data.objects.all()
#     }
#     # -----------------------------
#     # PROCESS WORKBOOK
#     # -----------------------------

#     for sheet in wb.sheetnames:

#         ws = wb[sheet]
#         rows = ws.iter_rows(values_only=True)

#         headers = next(rows)
#         headers_lower = [str(c).lower().strip() if c else "" for c in headers]

#         if "accessionno" not in headers_lower:
#             continue

#         accession_idx = headers_lower.index("accessionno")

#         abx_groups = {
#             k: v for k, v in _parse_abx_columns(headers).items()
#             if v["disk_col"] is not None or v["mic_col"] is not None
#         }

#         for row in rows:

#             accession = _clean(row[accession_idx]).upper()

#             if not accession:
#                 continue

#             isolate = isolates.get(accession)

#             if not isolate:
#                 skipped += 1
#                 continue

#             effective_year = _resolve_effective_year(isolate.Spec_Date)
#             resolved_ars_org = (isolate.ars_OrgCode or "").strip()

#             for abx_code, grp in abx_groups.items():

#                 abx_code = abx_code.upper()

#                 # resolve antibiotic object
#                 abx_obj = antibiotics.get(abx_code)

#                 if not abx_obj:
#                     continue

#                 disk_key = headers[grp["disk_col"]].upper() if grp["disk_col"] is not None else None
#                 mic_key = headers[grp["mic_col"]].upper() if grp["mic_col"] is not None else None

#                 # remove accidental _OP
#                 if mic_key and mic_key.endswith("_OP"):
#                     mic_key = mic_key.replace("_OP", "")

#                 # ----------------------------
#                 # EXCEL VALUES
#                 # ----------------------------

#                 disk_raw = row[grp["disk_col"]] if grp["disk_col"] is not None else None
#                 disk_ris = _clean(row[grp["disk_ris_col"]]) if grp["disk_ris_col"] is not None else ""

#                 mic_raw = row[grp["mic_col"]] if grp["mic_col"] is not None else None
#                 mic_ris = _clean(row[grp["mic_ris_col"]]) if grp["mic_ris_col"] is not None else ""

#                 mic_operand = ""
#                 if grp["mic_op_col"] is not None:
#                     mic_operand = _clean(row[grp["mic_op_col"]])

#                 disk_val = _int(disk_raw)
#                 mic_val, parsed_operand = _decimal(mic_raw)

#                 if not mic_operand:
#                     mic_operand = parsed_operand

#                 # ----------------------------
#                 # DELETE IF EMPTY
#                 # --------------------  --------

#                 if disk_val is None and mic_val is None:

#                     AntibioticEntry.objects.filter(
#                         ab_idNum_referred=isolate,
#                         ab_Retest_Abx_code=abx_code
#                     ).delete()

#                     continue

#                 # ----------------------------
#                 # CREATE / UPDATE ENTRY
#                 # ----------------------------
#                 whonet_code = disk_key if disk_val is not None else mic_key

#                 entry, created_flag = AntibioticEntry.objects.update_or_create(

#                     ab_idNum_referred=isolate,
#                     ab_Abx_code=whonet_code,

#                     defaults={

#                         "ab_AccessionNo": isolate.AccessionNo,
#                         "ab_RefNo": isolate.RefNo,

#                         "ab_Antibiotic": abx_obj.Antibiotic,
#                         "ab_Abx_code": abx_obj.Abx_code,
                        
#                         "ab_Disk_value": disk_val,
#                         "ab_MIC_value": mic_val,

#                         "ab_Disk_enRIS": disk_ris,
#                         "ab_MIC_enRIS": mic_ris,
#                         "ab_MIC_operand": mic_operand,
#                     }
#                 )

#                 entry.ab_breakpoints_id.clear()

#                 bp_applied = False

#                 # ----------------------------
#                 # DISK BREAKPOINT
#                 # ----------------------------

#                 if disk_val is not None and disk_key and disk_key in breakpoint_keys:

#                     bp_disk = BreakpointsTable.objects.filter(
#                         Whonet_Abx__iexact=disk_key,
#                         Year=effective_year,
#                         Test_Method="DISK",
#                         Org__in=[resolved_ars_org, ""]
#                     ).order_by("-Org").first()

#                     if bp_disk:

#                         entry.ab_breakpoints_id.set([bp_disk])

#                         entry.ab_Site_Org = bp_disk.Org
#                         entry.ab_Org_Flag = bool(bp_disk.Emerging_Org_Flag)
#                         entry.ab_Abx_Flag = bool(bp_disk.Emerging_Abx_Flag)
#                         entry.ab_Abx_Phenotype = bp_disk.Emerging_Pheno_Flag or ""

#                         entry.ab_R_breakpoint = bp_disk.R_val
#                         entry.ab_I_breakpoint = bp_disk.I_val
#                         entry.ab_SDD_breakpoint = bp_disk.SDD_val
#                         entry.ab_S_breakpoint = bp_disk.S_val

#                         bp_applied = True

#                 # ----------------------------
#                 # MIC BREAKPOINT
#                 # ----------------------------

#                 if mic_val is not None and mic_key and mic_key in breakpoint_keys:

#                     bp_mic = BreakpointsTable.objects.filter(
#                         Whonet_Abx__iexact=mic_key,
#                         Year=effective_year,
#                         Test_Method="MIC",
#                         Org__in=[resolved_ars_org, ""]
#                     ).order_by("-Org").first()

#                     if bp_mic:

#                         entry.ab_breakpoints_id.set([bp_mic])

#                         entry.ab_Site_Org = bp_mic.Org
#                         entry.ab_Org_Flag = bool(bp_mic.Emerging_Org_Flag)
#                         entry.ab_Abx_Flag = bool(bp_mic.Emerging_Abx_Flag)
#                         entry.ab_Abx_Phenotype = bp_mic.Emerging_Pheno_Flag or ""

#                         entry.ab_R_breakpoint = bp_mic.R_val
#                         entry.ab_I_breakpoint = bp_mic.I_val
#                         entry.ab_SDD_breakpoint = bp_mic.SDD_val
#                         entry.ab_S_breakpoint = bp_mic.S_val

#                         entry.ab_Alert_val = bp_mic.Alert_val

#                         bp_applied = True

#                 # ----------------------------
#                 # NO BREAKPOINT
#                 # ----------------------------

#                 if not bp_applied:

#                     entry.ab_Site_Org = None
#                     entry.ab_Org_Flag = False
#                     entry.ab_Abx_Flag = False
#                     entry.ab_Abx_Phenotype = ""

#                     entry.ab_R_breakpoint = None
#                     entry.ab_I_breakpoint = None
#                     entry.ab_SDD_breakpoint = None
#                     entry.ab_S_breakpoint = None

#                     entry.ab_Alert_val = ""

#                 entry.save()

#                 if created_flag:
#                     created += 1
#                 else:
#                     updated += 1

#     wb.close()

#     messages.success(
#         request,
#         f"{created} antibiotics uploaded successfully. "
#         f"{updated} updated, {skipped} skipped."
#     )

#     return redirect("show_raw_antibiotic")


############## implements uploading on both sentinel and ars antibiotic fields

@login_required
@role_required(ROLE_ADMIN, ROLE_CHECKER, ROLE_ENCODER)
@require_POST
@transaction.atomic
def upload_raw_antibiotics(request):

    file = request.FILES.get("RawAntibioticFile")
    overwrite = request.POST.get("overwrite", "false").lower() == "true"

    if not file:
        messages.error(request, "No file uploaded")
        return redirect("show_raw_antibiotic")

    try:
        file_name = file.name.lower()
        wb = None
        if file_name.endswith(".csv"):
            df = pd.read_csv(file)
            sheet_rows = [(file.name, [tuple(df.columns), *df.itertuples(index=False, name=None)])]
        elif file_name.endswith(".tsv"):
            df = pd.read_csv(file, sep="\t")
            sheet_rows = [(file.name, [tuple(df.columns), *df.itertuples(index=False, name=None)])]
        elif file_name.endswith((".xlsx", ".xls")):
            wb = openpyxl.load_workbook(file, read_only=True, data_only=True)
            sheet_rows = [
                (sheet, wb[sheet].iter_rows(values_only=True))
                for sheet in wb.sheetnames
            ]
        else:
            messages.error(request, "Unsupported file format. Please upload CSV, TSV, XLSX, or XLS.")
            return redirect("show_raw_antibiotic")
    except Exception as e:
        messages.error(request, str(e))
        return redirect("show_raw_antibiotic")

    created = 0
    updated = 0
    skipped = 0
    protected_skips = defaultdict(int)
    invalid_ris_values = 0
    affected_batch_ids = set()
    pending_entries = {}

    # -----------------------------
    # LOAD REFERENCE DATA
    # -----------------------------

    antibiotics = {}
    for antibiotic in Antibiotic_List.objects.all():
        _add_antibiotic_aliases(antibiotics, antibiotic)

    def normalize_accession(val):
        return str(val).strip().replace(" ", "").upper()

    isolates = {
        normalize_accession(i.AccessionNo): i
        for i in Referred_Data.objects.all()
    }

    # -----------------------------
    # PROCESS UPLOADED TABLES
    # -----------------------------

    for _sheet_name, rows in sheet_rows:
        try:
            headers = next(iter(rows))
        except StopIteration:
            continue
        if not hasattr(rows, "__next__"):
            rows = iter(rows[1:])
        headers_lower = [str(c).lower().strip() if c else "" for c in headers]

        if "accessionno" not in headers_lower:
            continue

        accession_idx = headers_lower.index("accessionno")

        abx_groups = {
            k: v for k, v in _parse_abx_columns(headers).items()
            if v["disk_col"] is not None or v["mic_col"] is not None
        }

        for row in rows:

            accession = normalize_accession(row[accession_idx])

            if not accession:
                continue

            isolate = isolates.get(accession)

            if not isolate:
                skipped += 1
                continue

            skip_reason = _referred_bulk_upload_skip_reason(
                request.user, isolate, final=False
            )
            if skip_reason:
                skipped += 1
                protected_skips[skip_reason] += 1
                continue

            if isolate.Batch_id_id:
                affected_batch_ids.add(isolate.Batch_id_id)

            for abx_code, grp in abx_groups.items():

                abx_code = abx_code.upper()

                disk_key = grp.get("disk_code")
                mic_key = grp.get("mic_code")

                if mic_key and mic_key.endswith("_OP"):
                    mic_key = mic_key.replace("_OP", "")

                # ----------------------------
                # RT DETECTION
                # ----------------------------

                is_retest = False

                if disk_key and "_RT" in disk_key:
                    is_retest = True

                if mic_key and "_RT" in mic_key:
                    is_retest = True

                if disk_key:
                    disk_key = disk_key.replace("_RT", "")

                if mic_key:
                    mic_key = mic_key.replace("_RT", "")

                # ----------------------------
                # EXCEL VALUES
                # ----------------------------

                disk_raw = row[grp["disk_col"]] if grp["disk_col"] is not None else None
                disk_ris, invalid_disk_ris = (
                    _clean_ris(row[grp["disk_ris_col"]])
                    if grp["disk_ris_col"] is not None
                    else ("", False)
                )

                mic_raw = row[grp["mic_col"]] if grp["mic_col"] is not None else None
                mic_ris, invalid_mic_ris = (
                    _clean_ris(row[grp["mic_ris_col"]])
                    if grp["mic_ris_col"] is not None
                    else ("", False)
                )
                invalid_ris_values += int(invalid_disk_ris) + int(invalid_mic_ris)

                mic_operand = ""
                if grp["mic_op_col"] is not None:
                    mic_operand = _clean(row[grp["mic_op_col"]])

                disk_val = _int(disk_raw)
                mic_val, parsed_operand = _decimal(mic_raw)

                if not mic_operand:
                    mic_operand = parsed_operand

                whonet_code = disk_key if disk_val is not None else mic_key

                if not whonet_code:
                    continue

                abx_obj = _resolve_antibiotic(antibiotics, whonet_code, abx_code)

                if not abx_obj:
                    skipped += 1
                    continue

                resolved_whonet_code = _canonical_whonet_code(abx_obj, whonet_code)

                # ----------------------------
                # DELETE IF EMPTY
                # ----------------------------

                if disk_val is None and mic_val is None:
                    pending_entries[
                        (
                            "retest" if is_retest else "main",
                            isolate.AccessionNo,
                            resolved_whonet_code,
                        )
                    ] = None
                    continue

                # ----------------------------
                # CREATE / UPDATE ENTRY
                # ----------------------------

                if is_retest:
                    defaults = {
                        "ab_AccessionNo": isolate.AccessionNo,
                        "ab_RefNo": isolate.RefNo,
                        "ab_Retest_Antibiotic": abx_obj.Antibiotic,
                        "ab_Retest_Abx_code": resolved_whonet_code,
                        "ab_Retest_Abx": abx_obj.Abx_code,
                        "ab_Retest_DiskValue": disk_val,
                        "ab_Retest_MICValue": mic_val,
                        "ab_Retest_Disk_enRIS": disk_ris,
                        "ab_Retest_MIC_enRIS": mic_ris,
                        "ab_Retest_MIC_operand": mic_operand,
                    }
                else:
                    defaults = {
                        "ab_AccessionNo": isolate.AccessionNo,
                        "ab_RefNo": isolate.RefNo,
                        "ab_Antibiotic": abx_obj.Antibiotic,
                        "ab_Abx_code": resolved_whonet_code,
                        "ab_Abx": abx_obj.Abx_code,
                        "ab_Disk_value": disk_val,
                        "ab_MIC_value": mic_val,
                        "ab_Disk_enRIS": disk_ris,
                        "ab_MIC_enRIS": mic_ris,
                        "ab_MIC_operand": mic_operand,
                    }

                pending_entries[
                        (
                            "retest" if is_retest else "main",
                            isolate.AccessionNo,
                            resolved_whonet_code,
                        )
                ] = {
                    "isolate": isolate,
                    "defaults": defaults,
                }

    if wb:
        wb.close()

    created, updated, _ = _bulk_upsert_antibiotic_entries(
        AntibioticEntry,
        pending_entries,
        overwrite=overwrite,
    )

    refreshed_breakpoints = 0

    if affected_batch_ids:
        refreshed_breakpoints = reapply_raw_breakpoints_for_batches(
            affected_batch_ids
        )

    messages.success(
        request,
        f"{created} antibiotics uploaded successfully. "
        f"{updated} updated, {skipped} skipped. "
        f"{invalid_ris_values} invalid RIS value(s) ignored. "
        f"{refreshed_breakpoints} raw breakpoint record(s) refreshed. "
        "Final antibiotic data was not changed."
    )

    if protected_skips:
        messages.warning(
            request,
            "Protected Raw antibiotic accessions skipped: " + "; ".join(
                f"{count} {reason}" for reason, count in sorted(protected_skips.items())
            )
        )
    return redirect("show_raw_antibiotic")

















# updated version
@login_required
def show_final_antibiotic(request):
    q = (request.GET.get("q") or "").strip()

    entries = (
        Final_AntibioticEntry.objects
        .select_related("ab_idNum_f_referred")
        .order_by("ab_idNum_f_referred__f_AccessionNo")
    )
    total_records = (
        entries
        .exclude(ab_Abx_code__isnull=True)
        .exclude(ab_Abx_code="")
        .exclude(ab_idNum_f_referred__isnull=True)
        .values_list("ab_idNum_f_referred__f_AccessionNo", flat=True)
        .distinct()
        .count()
    )
    entries = apply_wgs_related_accession_search(
        entries,
        q,
        "ab_idNum_f_referred__f_AccessionNo",
    )

    abx_data = {}
    abx_columns = set()

    for entry in entries:

        # Skip invalid entries
        if not entry.ab_Abx_code or not entry.ab_idNum_f_referred:
            continue

        acc = entry.ab_idNum_f_referred.f_AccessionNo
        abx_code = entry.ab_Abx_code.strip().upper()

        # Column naming (value / operand / RIS)
        col_value = abx_code
        col_op    = f"{abx_code}_OP"
        col_ris   = f"{abx_code}_RIS"

        abx_columns.update([col_value, col_op, col_ris])

        if acc not in abx_data:
            abx_data[acc] = {}

        # Determine MIC vs DISK based on actual data
        if entry.ab_MIC_value is not None:
            abx_data[acc][col_value] = entry.ab_MIC_value or ""
            abx_data[acc][col_op]    = entry.ab_MIC_operand or ""
            abx_data[acc][col_ris]   = entry.ab_MIC_enRIS or ""
        else:
            abx_data[acc][col_value] = entry.ab_Disk_value or ""
            abx_data[acc][col_op]    = ""
            abx_data[acc][col_ris]   = entry.ab_Disk_enRIS or ""

    # Sort antibiotic columns consistently
    abx_columns = sorted(abx_columns)

    # Pagination (per accession)
    paginated_list = list(abx_data.items())
    paginator = Paginator(paginated_list, 10)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        "wgs_app/show_final_antibiotic.html",
        {
            "page_obj": page_obj,
            "abx_data": dict(page_obj.object_list),
            "abx_codes": abx_columns,
            "total_records": total_records,  # number of isolates
            "filtered_records": len(abx_data),
            "q": q,
        }
    )



# updated version
@login_required
def show_raw_antibiotic(request):
    q = (request.GET.get("q") or "").strip()

    entries = (
        AntibioticEntry.objects
        .select_related("ab_idNum_referred")
        .order_by("ab_idNum_referred__AccessionNo")
    )
    total_records = (
        entries
        .exclude(ab_Abx_code__isnull=True)
        .exclude(ab_Abx_code="")
        .exclude(ab_idNum_referred__isnull=True)
        .values_list("ab_idNum_referred__AccessionNo", flat=True)
        .distinct()
        .count()
    )
    entries = apply_wgs_related_accession_search(
        entries,
        q,
        "ab_idNum_referred__AccessionNo",
    )

    abx_data = {}
    abx_columns = set()

    for entry in entries:

        # Skip invalid entries
        if not entry.ab_Abx_code or not entry.ab_idNum_referred:
            continue

        acc = entry.ab_idNum_referred.AccessionNo
        abx_code = entry.ab_Abx_code.strip().upper()

        # Column naming (value / operand / RIS)
        col_value = abx_code
        col_op    = f"{abx_code}_OP"
        col_ris   = f"{abx_code}_RIS"

        abx_columns.update([col_value, col_op, col_ris])

        if acc not in abx_data:
            abx_data[acc] = {}

        # Determine MIC vs DISK based on actual data
        if entry.ab_MIC_value is not None:
            abx_data[acc][col_value] = entry.ab_MIC_value or ""
            abx_data[acc][col_op]    = entry.ab_MIC_operand or ""
            abx_data[acc][col_ris]   = entry.ab_MIC_enRIS or ""
        else:
            abx_data[acc][col_value] = entry.ab_Disk_value or ""
            abx_data[acc][col_op]    = ""
            abx_data[acc][col_ris]   = entry.ab_Disk_enRIS or ""

    # Sort antibiotic columns consistently
    abx_columns = sorted(abx_columns)

    # Pagination (per accession)
    paginated_list = list(abx_data.items())
    paginator = Paginator(paginated_list, 10)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        "wgs_app/show_raw_antibiotic.html",
        {
            "page_obj": page_obj,
            "abx_data": dict(page_obj.object_list),
            "abx_codes": abx_columns,
            "total_records": total_records,  # number of isolates
            "filtered_records": len(abx_data),
            "q": q,
        }
    )






### updated version
@login_required
@role_required(ROLE_ADMIN, ROLE_CHECKER, ROLE_ENCODER)
@require_POST
@transaction.atomic
def delete_final_antibiotic(request, pk):
    """Delete accession antibiotics only when its Final record is editable."""
    target = get_object_or_404(
        Final_AntibioticEntry.objects.select_related(
            "ab_idNum_f_referred", "ab_idNum_f_referred__f_Batch_id"
        ), pk=pk
    )
    parent = target.ab_idNum_f_referred
    reason = _referred_bulk_upload_skip_reason(request.user, parent, final=True)
    if reason:
        messages.error(
            request,
            f"Final antibiotic deletion blocked: {reason}."
        )
        return redirect("show_final_antibiotic")

    # Deleting all results for an accession is deliberate existing behavior.
    Final_AntibioticEntry.objects.filter(
        ab_idNum_f_referred=parent
    ).delete()
    messages.success(
        request,
        f"All final antibiotic records for accession {parent.f_AccessionNo} deleted successfully!"
    )
    return redirect("show_final_antibiotic")


@login_required
@role_required(ROLE_ADMIN, ROLE_CHECKER, ROLE_ENCODER)
@require_POST
@transaction.atomic
def delete_raw_antibiotic(request, pk):
    """Delete accession antibiotics only when its Raw record is editable."""
    target = get_object_or_404(
        AntibioticEntry.objects.select_related(
            "ab_idNum_referred", "ab_idNum_referred__Batch_id"
        ), pk=pk
    )
    parent = target.ab_idNum_referred
    reason = _referred_bulk_upload_skip_reason(request.user, parent)
    if reason:
        messages.error(
            request,
            f"Raw antibiotic deletion blocked: {reason}."
        )
        return redirect("show_raw_antibiotic")

    AntibioticEntry.objects.filter(
        ab_idNum_referred=parent
    ).delete()
    messages.success(
        request,
        f"All antibiotic records for accession {parent.AccessionNo} deleted successfully!"
    )
    return redirect("show_raw_antibiotic")



@login_required
@role_required(ROLE_ADMIN, ROLE_CHECKER)
@require_POST
def delete_all_final_antibiotic(request):
    Final_AntibioticEntry.objects.all().delete()
    messages.success(request, "Final antibiotic entries have been deleted successfully.")
    return redirect('show_final_antibiotic')  # Redirect to the table view




@login_required
@role_required(ROLE_ADMIN, ROLE_CHECKER)
@require_POST
def delete_all_raw_antibiotic(request):
    AntibioticEntry.objects.all().delete()
    messages.success(request, "Raw antibiotic entries have been deleted successfully.")
    return redirect('show_raw_antibiotic')  # Redirect to the table view





#### updated version
@login_required
@role_required(ROLE_ADMIN, ROLE_CHECKER)
@require_POST
def delete_finalantibiotic_by_date(request):

    if request.method == "POST":
        upload_date_str = request.POST.get("upload_date")

        if not upload_date_str:
            messages.error(request, "Please select an upload date to delete.")
            return redirect("show_final_antibiotic")

        upload_date = parse_date(upload_date_str)

        if not upload_date:
            messages.error(request, f"Invalid date format: {upload_date_str}")
            return redirect("show_final_antibiotic")

        deleted_count, _ = Final_AntibioticEntry.objects.filter(
            ab_Date_uploaded_fd=upload_date
        ).delete()

        messages.success(
            request,
            f"Deleted {deleted_count} Final Antibiotic entries uploaded on {upload_date}."
        )
        return redirect("show_final_antibiotic")

    messages.error(request, "Invalid request method.")
    return redirect("show_final_antibiotic")



@login_required
@role_required(ROLE_ADMIN, ROLE_CHECKER)
@require_POST
def delete_rawantibiotic_by_date(request):

    if request.method == "POST":
        upload_date_str = request.POST.get("upload_date")

        if not upload_date_str:
            messages.error(request, "Please select an upload date to delete.")
            return redirect("show_raw_antibiotic")

        upload_date = parse_date(upload_date_str)

        if not upload_date:
            messages.error(request, f"Invalid date format: {upload_date_str}")
            return redirect("show_raw_antibiotic")

        deleted_count, _ = AntibioticEntry.objects.filter(
            ab_Date_uploaded_rd=upload_date
        ).delete()

        messages.success(
            request,
            f"Deleted {deleted_count} Antibiotic entries uploaded on {upload_date}."
        )
        return redirect("show_raw_antibiotic")

    messages.error(request, "Invalid request method.")
    return redirect("show_raw_antibiotic")




@login_required(login_url="login")
def export_Final_Antibioticentry(request):
    objects = Final_AntibioticEntry.objects.select_related("ab_idNum_f_referred").all()
    data = []

    def excel_datetime(value):
        if not value:
            return None
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        return value.replace(tzinfo=None)

    for obj in objects:
        final = obj.ab_idNum_f_referred

        data.append({
            "ab_idNum_f_referred": final.f_AccessionNo if final else None,
            "f_Accession_No": obj.ab_AccessionNo,
            "f_Date_of_Entry": excel_datetime(final.f_Date_of_Entry) if final else None,
            "f_Date_Modified": excel_datetime(final.f_Date_Modified) if final else None,
            "f_arsp_Encoder": final.f_arsp_Encoder if final else "",
            "f_arsp_Checker": final.f_arsp_Checker if final else "",
            "f_Site_Org": obj.ab_Site_Org,
            "Antibiotic": obj.ab_Antibiotic,
            "Abx_code": obj.ab_Abx_code,
            "Abx": obj.ab_Abx,
            "Disk_value": obj.ab_Disk_value,
            "Disk_enRIS": obj.ab_Disk_enRIS,
            "MIC_operand": obj.ab_MIC_operand,
            "MIC_value": obj.ab_MIC_value,
            "MIC_enRIS": obj.ab_MIC_enRIS,
            "f_Ars_Org":obj.ab_Ret_Org,
            "Retest_Antibiotic": obj.ab_Retest_Antibiotic,
            "Retest_Abx_code": obj.ab_Retest_Abx_code,
            "Retest_Abx": obj.ab_Retest_Abx,
            "Retest_DiskValue": obj.ab_Retest_DiskValue,
            "Retest_Disk_enRIS": obj.ab_Retest_Disk_enRIS,
            "Ret_MIC_Operand": obj.ab_Retest_MIC_operand,
            "Retest_MICValue": obj.ab_Retest_MICValue,
            "Retest_MIC_enRIS": obj.ab_Retest_MIC_enRIS,
            "ab_R_breakpoint": obj.ab_R_breakpoint,
            "ab_I_breakpoint": obj.ab_I_breakpoint,
            "ab_SDD_breakpoint": obj.ab_SDD_breakpoint,
            "ab_S_breakpoint":obj.ab_S_breakpoint,
            "ab_Ret_R_breakpoint": obj.ab_Ret_R_breakpoint,
            "ab_Ret_I_breakpoint": obj.ab_Ret_I_breakpoint,
            "ab_Ret_SDD_breakpoint": obj.ab_Ret_SDD_breakpoint,
            "ab_Ret_S_breakpoint": obj.ab_Ret_S_breakpoint
        })
    
    # Define file path
    file_path = "Final_AntibioticEntry.xlsx"

    # Convert data to DataFrame and save as Excel
    df = pd.DataFrame(data)
    df.to_excel(file_path, index=False)

    # Return the file as a response
    return FileResponse(open(file_path, "rb"), as_attachment=True, filename="Final_AntibioticEntry.xlsx")





@login_required(login_url="login")
def export_raw_Antibioticentry(request):
    objects = AntibioticEntry.objects.select_related("ab_idNum_referred").all()
    data = []

    def excel_datetime(value):
        if not value:
            return None
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        return value.replace(tzinfo=None)

    for obj in objects:
        referred = obj.ab_idNum_referred

        data.append({
            "ab_idNum_f_referred": referred.AccessionNo if referred else None,
            "f_Accession_No": obj.ab_AccessionNo,
            "Date_of_Entry": excel_datetime(referred.Date_of_Entry) if referred else None,
            "Date_Modified": excel_datetime(referred.Date_Modified) if referred else None,
            "arsp_Encoder": referred.arsp_Encoder if referred else "",
            "arsp_Checker": referred.arsp_Checker if referred else "",
            "f_Site_Org": obj.ab_Site_Org,
            "Antibiotic": obj.ab_Antibiotic,
            "Abx_code": obj.ab_Abx_code,
            "Abx": obj.ab_Abx,
            "Disk_value": obj.ab_Disk_value,
            "Disk_enRIS": obj.ab_Disk_enRIS,
            "MIC_operand": obj.ab_MIC_operand,
            "MIC_value": obj.ab_MIC_value,
            "MIC_enRIS": obj.ab_MIC_enRIS,
            "f_Ars_Org":obj.ab_Ret_Org,
            "Retest_Antibiotic": obj.ab_Retest_Antibiotic,
            "Retest_Abx_code": obj.ab_Retest_Abx_code,
            "Retest_Abx": obj.ab_Retest_Abx,
            "Retest_DiskValue": obj.ab_Retest_DiskValue,
            "Retest_Disk_enRIS": obj.ab_Retest_Disk_enRIS,
            "Ret_MIC_Operand": obj.ab_Retest_MIC_operand,
            "Retest_MICValue": obj.ab_Retest_MICValue,
            "Retest_MIC_enRIS": obj.ab_Retest_MIC_enRIS,
            "ab_R_breakpoint": obj.ab_R_breakpoint,
            "ab_I_breakpoint": obj.ab_I_breakpoint,
            "ab_SDD_breakpoint": obj.ab_SDD_breakpoint,
            "ab_S_breakpoint":obj.ab_S_breakpoint,
            "ab_Ret_R_breakpoint": obj.ab_Ret_R_breakpoint,
            "ab_Ret_I_breakpoint": obj.ab_Ret_I_breakpoint,
            "ab_Ret_SDD_breakpoint": obj.ab_Ret_SDD_breakpoint,
            "ab_Ret_S_breakpoint": obj.ab_Ret_S_breakpoint
        })
    
    # Define file path
    file_path = "AntibioticEntry.xlsx"

    # Convert data to DataFrame and save as Excel
    df = pd.DataFrame(data)
    df.to_excel(file_path, index=False)

    # Return the file as a response
    return FileResponse(open(file_path, "rb"), as_attachment=True, filename="AntibioticEntry.xlsx")



################ BATCH GENERATOR
############ Auto-Generate Batch
def generate_batches_from_referred():

    from itertools import groupby

    isolates = (
        Referred_Data.objects
        .filter(Batch_id__isnull=True)
        .exclude(Batch_Code__exact="")
        .order_by("Batch_Code", "AccessionNo")
    )

    created_batches = 0

    for batch_code, group in groupby(isolates, key=lambda x: x.Batch_Code):

        group = list(group)

        if not group:
            continue

        first = group[0]

        site_code = first.SiteCode
        referral_date = first.Referral_Date
        batch_name = first.Batch_Name or batch_code
        batch_no = first.BatchNo or "1"
        total_batch = first.Total_batch or "1"
        ref_no = first.RefNo or ""

        site_name = ""
        site_obj = SiteData.objects.filter(SiteCode=site_code).first()
        if site_obj:
            site_name = site_obj.SiteName

        batch_obj, created = Batch_Table.objects.get_or_create(
            bat_Batch_Code=batch_code,
            defaults={
                "bat_Batch_Name": batch_name,
                "bat_Site_Name": site_name,
                "bat_SiteCode": site_code,
                "bat_Referral_Date": referral_date,
                "bat_RefNo": ref_no,
                "bat_BatchNo": batch_no,
                "bat_Total_batch": total_batch
            }
        )

        # -------- Assign bat_seq --------
        seq = 1
        updated_isolates = []

        for iso in group:

            iso.Batch_id = batch_obj
            iso.Batch_Code = batch_code
            iso.Batch_Name = batch_name
            iso.bat_seq = seq

            updated_isolates.append(iso)

            seq += 1

        Referred_Data.objects.bulk_update(
            updated_isolates,
            ["Batch_id", "Batch_Code", "Batch_Name", "bat_seq"]
        )

        total_isolates = Referred_Data.objects.filter(Batch_id=batch_obj).count()
        tat_obj, _ = TATform.objects.get_or_create(
            tat_Batch_Isolates=batch_obj,
            defaults={
                "tat_SiteCode": batch_obj.bat_SiteCode,
                "tat_Batch_Code": batch_obj.bat_Batch_Code,
                "tat_Referral_Date": batch_obj.bat_Referral_Date,
                "tat_Num_Isolate": str(total_isolates),
                "tat_BatchNumber": batch_obj.bat_BatchNo,
                "tat_Total_Batch": batch_obj.bat_Total_batch,
            }
        )

        tat_obj.tat_SiteCode = batch_obj.bat_SiteCode
        tat_obj.tat_Batch_Code = batch_obj.bat_Batch_Code
        tat_obj.tat_Referral_Date = batch_obj.bat_Referral_Date
        tat_obj.tat_Num_Isolate = str(total_isolates)
        tat_obj.tat_BatchNumber = batch_obj.bat_BatchNo
        tat_obj.tat_Total_Batch = batch_obj.bat_Total_batch
        tat_obj.save(update_fields=[
            "tat_SiteCode",
            "tat_Batch_Code",
            "tat_Referral_Date",
            "tat_Num_Isolate",
            "tat_BatchNumber",
            "tat_Total_Batch",
        ])

        created_batches += 1

    return created_batches





# @login_required
# @transaction.atomic
# def create_batch_from_referred(request):

#     isolates = (
#         Referred_Data.objects
#         .filter(Batch_id__isnull=True)
#         .order_by("SiteCode", "Referral_Date", "AccessionNo")
#     )

#     from itertools import groupby

#     created_batches = 0

#     for (site, ref_date), group in groupby(
#         isolates,
#         key=lambda x: (x.SiteCode, x.Referral_Date)
#     ):

#         group = list(group)

#         if not group:
#             continue

#         year_short = ref_date.strftime("%y")
#         year_long = ref_date.strftime("%m%d%Y")

#         ref_numbers = [
#             int(i.AccessionNo[-4:])
#             for i in group
#             if i.AccessionNo[-4:].isdigit()
#         ]

#         start_ref = min(ref_numbers)
#         end_ref = max(ref_numbers)

#         ref_range = f"{start_ref}-{end_ref}"

#         batch_code = f"{site}_{year_long}_1.1_{ref_range}"

#         site_name = ""
#         site_obj = SiteData.objects.filter(SiteCode=site).first()
#         if site_obj:
#             site_name = site_obj.SiteName

#         batch_obj = Batch_Table.objects.create(
#             bat_Batch_Name=batch_code,
#             bat_AccessionNo=", ".join(i.AccessionNo for i in group),
#             bat_Batch_Code=batch_code,
#             bat_Site_Name=site_name,
#             bat_SiteCode=site,
#             bat_Referral_Date=ref_date,
#             bat_RefNo=ref_range,
#             bat_BatchNo="1",
#             bat_Total_batch="1"
#         )

#         seq = 1

#         for iso in group:

#             iso.Batch_id = batch_obj
#             iso.Batch_Code = batch_code
#             iso.Batch_Name = batch_code
#             iso.bat_seq = seq

#             iso.save(update_fields=[
#                 "Batch_id",
#                 "Batch_Code",
#                 "Batch_Name",
#                 "bat_seq"
#             ])

#             seq += 1

#         created_batches += 1

#     messages.success(
#         request,
#         f"{created_batches} batch(es) created successfully."
#     )

#     return redirect("show_batches")


@login_required
@transaction.atomic
def create_batch_from_referred(request):

    created_batches = generate_batches_from_referred()

    messages.success(
        request,
        f"{created_batches} batch(es) created successfully."
    )

    return redirect("show_batches")
