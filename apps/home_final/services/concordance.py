import re
import re
from collections import defaultdict

from django.db import transaction

from apps.home.models import Batch_Table
from apps.home.models import Organism_List
from apps.home_final.models import (
    ConcordanceDetail,
    ConcordanceOptions,
    ConcordanceReport,
    Final_AntibioticEntry,
    Final_Data,
)


MIXED_INDICATORS = ("mixed", "culture", "multiple", "various")
NONVIABLE_INDICATORS = ("not viable", "nonviable", "no growth")
NONVIABLE_CODES = {"nv"}
NO_IDENTIFICATION_VALUES = {"", "n/a", "na", "none", "null", "-"}
ALL_ORGANISMS_OPTION = "__ALL_ORGANISMS__"


def clean_str(value):
    if value is None:
        return ""
    return str(value).strip()


def normalize_result(value):
    result = clean_str(value).upper()
    if result in {"", "NA", "N/A", "NONE", "NULL", "ND"}:
        return None
    if result == "SDD":
        return "S"
    if result == "NS":
        return "R"
    return result


def normalize_ast_pair_code(value):
    """Group AST comparison keys by antibiotic and method, not disk potency."""
    code = clean_str(value).upper().replace(".", "_")
    if not code:
        return ""

    disk_match = re.match(r"^([A-Z0-9]+)_ND", code)
    if disk_match:
        return f"{disk_match.group(1)}_ND"

    mic_match = re.match(r"^([A-Z0-9]+)_NM", code)
    if mic_match:
        return f"{mic_match.group(1)}_NM"

    return code


def normalize_code(value):
    return clean_str(value).upper()


def ast_base_code(value):
    pair_code = normalize_ast_pair_code(value)
    match = re.match(r"^(.+)_N[DM]$", pair_code)
    return match.group(1) if match else pair_code


def split_option_codes(value):
    if value in (None, ""):
        return set()
    if isinstance(value, (list, tuple, set)):
        raw_values = value
    else:
        raw_values = re.split(r"[,;|]\s*", str(value))
    return {normalize_code(item) for item in raw_values if normalize_code(item)}


def _contains_any(text, indicators):
    text = clean_str(text).lower()
    return any(indicator in text for indicator in indicators)


def _is_no_identification_value(value):
    return clean_str(value).lower() in NO_IDENTIFICATION_VALUES


def _is_nonviable_identification(*values):
    cleaned_values = [clean_str(value) for value in values]
    if any(value.lower() in NONVIABLE_CODES for value in cleaned_values):
        return True
    return _contains_any(" ".join(cleaned_values), NONVIABLE_INDICATORS)


def _is_nonviable_ars_organism(isolate):
    return _is_nonviable_identification(
        getattr(isolate, "f_ars_OrgCode", ""),
        getattr(isolate, "f_ars_OrgName", ""),
    )


def resolve_ars_concordance_identification(isolate):
    ars_pre_edit = clean_str(getattr(isolate, "f_ars_Pre_ed", ""))
    ars_pre = clean_str(getattr(isolate, "f_ars_Pre", ""))
    ars_org = clean_str(getattr(isolate, "f_ars_OrgName", ""))
    preferred_ars_pre = ars_pre_edit or ars_pre

    if _is_nonviable_identification(preferred_ars_pre):
        return "Not viable"
    if _is_no_identification_value(ars_org) and not _is_no_identification_value(preferred_ars_pre):
        return preferred_ars_pre
    if _is_no_identification_value(preferred_ars_pre):
        return ars_org
    return preferred_ars_pre or ars_org


def _is_explicit_no_identification(ars_pre, ars_post=""):
    pre = clean_str(ars_pre).lower()
    post = clean_str(ars_post).lower()
    return "no" in pre and ("recovered" in (pre + " " + post) or "isolated" in (pre + " " + post))


def _normalize_taxon_token(value):
    """Remove punctuation around organism name tokens before comparison."""
    return re.sub(r"[^a-z0-9]+", "", clean_str(value).lower())


def _genus_name(organism_name):
    organism_name = clean_str(organism_name)
    if not organism_name:
        return ""
    return _normalize_taxon_token(organism_name.split()[0])


def _species_name(organism_name):
    organism_name = clean_str(organism_name)
    parts = organism_name.split()
    return " ".join(parts[1:]).lower() if len(parts) > 1 else ""


def classify_id_concordance(site_org, ars_pre, ars_org, ars_post="", no_serotyping=False):
    site = clean_str(site_org).lower()
    ars = clean_str(ars_org).lower()
    pre = clean_str(ars_pre).lower()
    post = clean_str(ars_post).lower()

    if _contains_any(" ".join([site, ars, pre, post]), MIXED_INDICATORS):
        return "M", "M"

    # "No ... recovered or isolated" is a discordant identification, not a non-viable isolate.
    if _is_explicit_no_identification(pre, post):
        return "X", "X"

    if _is_nonviable_identification(ars):
        return "X", "X"

    if _is_nonviable_identification(pre, post):
        return "N", "N"

    if site and _is_no_identification_value(ars):
        return "X", "X"

    if not site or not ars:
        return None, None

    if site == ars:
        return "G", "S"

    site_genus = _genus_name(site)
    ars_genus = _genus_name(ars)
    site_species = _species_name(site)
    ars_species = _species_name(ars)

    genus_con = "G" if site_genus and ars_genus and site_genus == ars_genus else "X"

    if genus_con != "G":
        return "X", "X"

    if no_serotyping:
        return "G", "S"

    if site_species and ars_species and (
        site_species == ars_species
        or site_species in ars_species
        or ars_species in site_species
    ):
        return "G", "S"

    return "G", "X"


def classify_ast_deviation(site_ris, ars_ris):
    site = normalize_result(site_ris)
    ars = normalize_result(ars_ris)

    if not site or not ars:
        return None, False

    if site == ars:
        return "S", False

    if site == "S" and ars == "R":
        return "A", True

    if site == "R" and ars == "S":
        return "B", True

    if "I" in {site, ars}:
        return "C", True

    return None, False


def get_site_ris(entry, antibiotic_code=""):
    pair_code = normalize_ast_pair_code(antibiotic_code)
    if pair_code.endswith("_ND"):
        return normalize_result(entry.ab_Disk_enRIS)
    if pair_code.endswith("_NM"):
        return normalize_result(entry.ab_MIC_enRIS)
    return normalize_result(entry.ab_Disk_enRIS) or normalize_result(entry.ab_MIC_enRIS)


def get_ars_ris(entry, antibiotic_code=""):
    pair_code = normalize_ast_pair_code(antibiotic_code)
    if pair_code.endswith("_ND"):
        return normalize_result(entry.ab_Retest_Disk_enRIS)
    if pair_code.endswith("_NM"):
        return normalize_result(entry.ab_Retest_MIC_enRIS)
    return normalize_result(entry.ab_Retest_Disk_enRIS) or normalize_result(entry.ab_Retest_MIC_enRIS)


def get_site_antibiotic_code(entry):
    return clean_str(entry.ab_Abx_code).upper()


def get_ars_antibiotic_code(entry):
    return clean_str(entry.ab_Retest_Abx_code).upper()


def _method_for_pair_code(pair_code):
    if pair_code.endswith("_ND"):
        return "disk"
    if pair_code.endswith("_NM"):
        return "mic"
    return ""


def _set_result_once(results, names, key, ris, antibiotic_name):
    ris = normalize_result(ris)
    if key and ris and key not in results:
        results[key] = ris
        names.setdefault(key, antibiotic_name or key)


def _tested_result(value, ris):
    if value is None:
        return None
    return normalize_result(ris)


def _build_standard_ast_result_maps(isolate):
    site_results = {}
    ars_results = {}
    antibiotic_names = {}

    for entry in isolate.final_entries.all():
        site_code = get_site_antibiotic_code(entry)
        ars_code = get_ars_antibiotic_code(entry)
        site_base_code = ast_base_code(site_code)
        ars_base_code = ast_base_code(ars_code)
        site_method = _method_for_pair_code(normalize_ast_pair_code(site_code))
        ars_method = _method_for_pair_code(normalize_ast_pair_code(ars_code))

        if site_method in {"", "disk"}:
            _set_result_once(
                site_results,
                antibiotic_names,
                f"{site_base_code}_ND" if site_base_code else "",
                _tested_result(entry.ab_Disk_value, entry.ab_Disk_enRIS),
                entry.ab_Antibiotic or site_base_code,
            )
        if site_method in {"", "mic"}:
            _set_result_once(
                site_results,
                antibiotic_names,
                f"{site_base_code}_NM" if site_base_code else "",
                _tested_result(entry.ab_MIC_value, entry.ab_MIC_enRIS),
                entry.ab_Antibiotic or site_base_code,
            )
        if ars_method in {"", "disk"}:
            _set_result_once(
                ars_results,
                antibiotic_names,
                f"{ars_base_code}_ND" if ars_base_code else "",
                _tested_result(entry.ab_Retest_DiskValue, entry.ab_Retest_Disk_enRIS),
                entry.ab_Retest_Antibiotic or ars_base_code,
            )
        if ars_method in {"", "mic"}:
            _set_result_once(
                ars_results,
                antibiotic_names,
                f"{ars_base_code}_NM" if ars_base_code else "",
                _tested_result(entry.ab_Retest_MICValue, entry.ab_Retest_MIC_enRIS),
                entry.ab_Retest_Antibiotic or ars_base_code,
            )

    return site_results, ars_results, antibiotic_names


def _preferred_retest_ris(entry, preferred_method):
    if preferred_method == "mic":
        return (
            _tested_result(entry.ab_Retest_MICValue, entry.ab_Retest_MIC_enRIS)
            or _tested_result(entry.ab_Retest_DiskValue, entry.ab_Retest_Disk_enRIS)
        )
    return (
        _tested_result(entry.ab_Retest_DiskValue, entry.ab_Retest_Disk_enRIS)
        or _tested_result(entry.ab_Retest_MICValue, entry.ab_Retest_MIC_enRIS)
    )


def _build_site_prioritized_ast_result_maps(isolate, preferred_method):
    site_results = {}
    ars_results = {}
    antibiotic_names = {}

    for entry in isolate.final_entries.all():
        site_code = get_site_antibiotic_code(entry)
        ars_code = get_ars_antibiotic_code(entry)
        site_base_code = ast_base_code(site_code)
        ars_base_code = ast_base_code(ars_code)

        if (
            site_base_code
            and site_base_code not in site_results
        ):
            site_ris = (
                _tested_result(entry.ab_MIC_value, entry.ab_MIC_enRIS)
                if preferred_method == "mic"
                else _tested_result(entry.ab_Disk_value, entry.ab_Disk_enRIS)
            )
            if site_ris:
                site_results[site_base_code] = site_ris
                antibiotic_names[site_base_code] = entry.ab_Antibiotic or site_code

        if ars_base_code and ars_base_code not in ars_results:
            ars_ris = _preferred_retest_ris(entry, preferred_method)
            if ars_ris:
                ars_results[ars_base_code] = ars_ris
                antibiotic_names.setdefault(
                    ars_base_code,
                    entry.ab_Retest_Antibiotic or ars_code,
                )

    return site_results, ars_results, antibiotic_names


def build_ast_result_maps(isolate, rule_context=None):
    rule_context = rule_context or {}
    if rule_context.get("prioritize_mic_site"):
        return _build_site_prioritized_ast_result_maps(isolate, "mic")
    if rule_context.get("prioritize_disk_site"):
        return _build_site_prioritized_ast_result_maps(isolate, "disk")
    return _build_standard_ast_result_maps(isolate)


def _serialize_option_groups(value):
    return ",".join(sorted(split_option_codes(value)))


def normalize_applied_org(value):
    value = normalize_code(value)
    if value in {"", "-", "N/A", "NA"}:
        return ""
    if value in {"ALL", "ALL ORGANISMS", ALL_ORGANISMS_OPTION}:
        return ALL_ORGANISMS_OPTION
    return value


def is_all_organisms_option(value):
    return normalize_applied_org(value) == ALL_ORGANISMS_OPTION


def _option_defaults(options_data):
    return {
        "prioritize_mic_site": bool(options_data.get("prioritize_mic_site")),
        "prioritize_disk_site": bool(options_data.get("prioritize_disk_site")),
        "no_serotyping": bool(options_data.get("no_serotyping")),
        "applied_org": normalize_applied_org(options_data.get("applied_org")),
        "applied_org_grp": _serialize_option_groups(options_data.get("applied_org_grp")),
        "is_active": bool(options_data.get("is_active", True)),
    }


def _options_to_dict(options):
    if not options:
        return {}
    return {
        "prioritize_mic_site": options.prioritize_mic_site,
        "prioritize_disk_site": options.prioritize_disk_site,
        "no_serotyping": options.no_serotyping,
        "applied_org": options.applied_org,
        "applied_org_grp": options.applied_org_grp,
        "is_active": options.is_active,
    }


def get_global_concordance_options():
    return (
        ConcordanceOptions.objects
        .filter(report__isnull=True)
        .order_by("id")
        .first()
    )


def get_global_concordance_rules():
    return (
        ConcordanceOptions.objects
        .filter(report__isnull=True)
        .order_by("id")
    )


def _options_queryset_to_data(options):
    return [
        _options_to_dict(option)
        for option in options
    ]


def get_global_concordance_options_data():
    return _options_queryset_to_data(
        get_global_concordance_rules().filter(is_active=True)
    )


def save_global_concordance_options(options_data):
    return ConcordanceOptions.objects.create(
        report=None,
        **_option_defaults(options_data),
    )


def update_global_concordance_options(option, options_data):
    defaults = _option_defaults(options_data)
    for field, value in defaults.items():
        setattr(option, field, value)
    option.save(update_fields=list(defaults))
    return option


def _existing_options_for_batch(batch):
    return get_global_concordance_options_data()


def _existing_options_for_isolate(isolate):
    return get_global_concordance_options_data()


def _normalize_options_data(options_data):
    if not options_data:
        return []
    if isinstance(options_data, dict):
        return [options_data]
    return list(options_data)


def _build_rule_matcher(options_data):
    rule_rows = [
        row
        for row in _normalize_options_data(options_data)
        if row.get("is_active", True)
        and any(row.get(field) for field in ("prioritize_mic_site", "prioritize_disk_site", "no_serotyping"))
        and (
            is_all_organisms_option(row.get("applied_org"))
            or normalize_applied_org(row.get("applied_org"))
            or split_option_codes(row.get("applied_org_grp"))
        )
    ]

    if not rule_rows:
        return lambda isolate: {}

    org_group_cache = {}
    org_name_code_cache = {}

    def get_org_group(org_code):
        org_code = normalize_code(org_code)
        if not org_code:
            return ""
        if org_code not in org_group_cache:
            org_group_cache[org_code] = normalize_code(
                Organism_List.objects
                .filter(Whonet_Org_Code__iexact=org_code)
                .values_list("Genus_Code", flat=True)
                .first()
            )
        return org_group_cache[org_code]

    def get_org_code_from_name(org_name):
        org_name = clean_str(org_name).lower()
        if not org_name:
            return ""
        if org_name not in org_name_code_cache:
            org_name_code_cache[org_name] = normalize_code(
                Organism_List.objects
                .filter(Organism__iexact=org_name)
                .values_list("Whonet_Org_Code", flat=True)
                .first()
            )
        return org_name_code_cache[org_name]

    def matcher(isolate):
        site_codes = {
            code
            for code in (
                normalize_code(getattr(isolate, "f_Site_Org", "")),
                get_org_code_from_name(getattr(isolate, "f_Site_OrgName", "")),
            )
            if code
        }
        ars_codes = {
            code
            for code in (
                normalize_code(getattr(isolate, "f_ars_OrgCode", "")),
                get_org_code_from_name(getattr(isolate, "f_ars_OrgName", "")),
            )
            if code
        }
        isolate_codes = site_codes | ars_codes
        matched_context = {
            "prioritize_mic_site": False,
            "prioritize_disk_site": False,
            "no_serotyping": False,
            "force_id_concordance": False,
        }

        for row in rule_rows:
            applied_org = normalize_applied_org(row.get("applied_org"))
            applied_groups = split_option_codes(row.get("applied_org_grp"))
            organism_matches = (
                is_all_organisms_option(applied_org)
                or (applied_org and applied_org in isolate_codes)
            )
            group_matches = applied_groups and bool(
                (isolate_codes | {get_org_group(code) for code in isolate_codes}) & applied_groups
            )
            if not (organism_matches or group_matches):
                continue

            if row.get("no_serotyping"):
                matched_context["no_serotyping"] = True
                matched_context["force_id_concordance"] = True

            if (
                not matched_context["prioritize_mic_site"]
                and not matched_context["prioritize_disk_site"]
            ):
                matched_context["prioritize_mic_site"] = bool(row.get("prioritize_mic_site"))
                matched_context["prioritize_disk_site"] = bool(row.get("prioritize_disk_site"))

        return matched_context

    return matcher


def _persist_report_options(report, options_data):
    if options_data is None:
        return
    if not isinstance(options_data, dict):
        ConcordanceOptions.objects.filter(report=report).delete()
        return
    ConcordanceOptions.objects.update_or_create(
        report=report,
        defaults=_option_defaults(options_data),
    )


def calculate_isolate_concordance(isolate, rule_context=None):
    rule_context = rule_context or {}
    ars_concordance_identification = resolve_ars_concordance_identification(isolate)
    id_text = " ".join([
        clean_str(isolate.f_Site_OrgName),
        clean_str(isolate.f_ars_OrgName),
        clean_str(ars_concordance_identification),
        clean_str(isolate.f_ars_Post),
    ])

    if rule_context.get("force_id_concordance"):
        if _contains_any(id_text, MIXED_INDICATORS):
            genus_con, species_con = "M", "M"
        elif _is_explicit_no_identification(ars_concordance_identification, isolate.f_ars_Post):
            genus_con, species_con = "X", "X"
        elif _is_nonviable_ars_organism(isolate):
            genus_con, species_con = "X", "X"
        elif _is_nonviable_identification(
            ars_concordance_identification,
            isolate.f_ars_Post,
        ):
            genus_con, species_con = "N", "N"
        elif clean_str(isolate.f_Site_OrgName) and _is_no_identification_value(isolate.f_ars_OrgName):
            genus_con, species_con = "X", "X"
        elif (
            clean_str(isolate.f_Site_OrgName)
            or clean_str(isolate.f_ars_OrgName)
            or clean_str(isolate.f_Site_Org)
            or clean_str(isolate.f_ars_OrgCode)
        ):
            genus_con, species_con = "G", "S"
        else:
            genus_con, species_con = None, None
    else:
        genus_con, species_con = classify_id_concordance(
            isolate.f_Site_OrgName,
            ars_concordance_identification,
            isolate.f_ars_OrgName,
            isolate.f_ars_Post,
            no_serotyping=rule_context.get("no_serotyping", False),
        )

    site_results, ars_results, antibiotic_names = build_ast_result_maps(
        isolate,
        rule_context,
    )

    total_pairs = 0
    concordant_pairs = 0
    vmd = 0
    md = 0
    minor = 0
    details = []

    for antibiotic_code in sorted(set(site_results) & set(ars_results)):
        site_ris = site_results[antibiotic_code]
        ars_ris = ars_results[antibiotic_code]

        code, is_discordant = classify_ast_deviation(site_ris, ars_ris)
        if not code:
            continue

        total_pairs += 1

        if code == "S":
            concordant_pairs += 1
        elif code == "A":
            vmd += 1
        elif code == "B":
            md += 1
        elif code == "C":
            minor += 1

        details.append(
            ConcordanceDetail(
                accession_no=isolate.f_AccessionNo,
                isolate_id=isolate.id,
                organism=isolate.f_Site_OrgName or "",
                antibiotic=antibiotic_names.get(antibiotic_code, antibiotic_code),
                site_ris=site_ris,
                ars_ris=ars_ris,
                deviation_code=code,
                is_discordant=is_discordant,
                genus_con=genus_con,
                species_con=species_con,
            )
        )

    total_deviation = vmd + md + minor
    critical_deviation = vmd + md

    return {
        "genus_con": genus_con,
        "species_con": species_con,
        "genus_match": 1 if genus_con == "G" else 0,
        "species_match": 1 if species_con == "S" else 0,
        "mixed": 1 if genus_con == "M" else 0,
        "nonviable": 1 if genus_con == "N" else 0,
        "different_org": 1 if genus_con == "X" else 0,
        "total_pairs": total_pairs,
        "concordant_pairs": concordant_pairs,
        "vmd": vmd,
        "md": md,
        "minor": minor,
        "total_deviation": total_deviation,
        "critical_deviation": critical_deviation,
        "ast_concordance_rate": pct(concordant_pairs, total_pairs),
        "critical_deviation_rate": pct(critical_deviation, total_pairs),
        "total_deviation_rate": pct(total_deviation, total_pairs),
        "details": details,
    }


def pct(numerator, denominator):
    return round((numerator / denominator) * 100, 2) if denominator else 0


def build_id_stats(isolates, options_data=None):
    stats = {
        "total_isolates": 0,
        "genus_match": 0,
        "species_match": 0,
        "different_org": 0,
        "mixed_count": 0,
        "nonviable_count": 0,
        "discordant_rows": [],
    }

    rule_matcher = _build_rule_matcher(options_data)

    for isolate in isolates:
        stats["total_isolates"] += 1
        result = calculate_isolate_concordance(isolate, rule_matcher(isolate))

        stats["genus_match"] += result["genus_match"]
        stats["species_match"] += result["species_match"]
        stats["mixed_count"] += result["mixed"]
        stats["nonviable_count"] += result["nonviable"]
        stats["different_org"] += result["different_org"]

        if result["different_org"]:
            ars_identification = resolve_ars_concordance_identification(isolate)
            stats["discordant_rows"].append({
                "refno": isolate.f_RefNo,
                "bat_seq": isolate.f_bat_seq if isolate.f_bat_seq is not None else "",
                "site_org": isolate.f_Site_OrgName,
                "ars_org": ars_identification,
            })

    viable_pure = (
        stats["total_isolates"]
        - stats["mixed_count"]
        - stats["nonviable_count"]
    )
    stats["viable_pure"] = max(viable_pure, 0)
    stats["genus_rate"] = pct(stats["genus_match"], stats["viable_pure"])
    stats["species_rate"] = pct(stats["species_match"], stats["viable_pure"])

    return stats


def _report_defaults_from_totals(totals, user=None):
    total_pairs = totals["total_pairs"]
    total_deviation = totals["vmd"] + totals["md"] + totals["minor"]
    critical_deviation = totals["vmd"] + totals["md"]

    return {
        "created_by": user,
        "total_isolates": totals["total_isolates"],
        "total_pairs": total_pairs,
        "concordant_pairs": totals["concordant_pairs"],
        "vmd": totals["vmd"],
        "md": totals["md"],
        "minor": totals["minor"],
        "total_deviation": total_deviation,
        "critical_deviation": critical_deviation,
        "ast_concordance_rate": pct(totals["concordant_pairs"], total_pairs),
        "critical_deviation_rate": pct(critical_deviation, total_pairs),
        "total_deviation_rate": pct(total_deviation, total_pairs),
        "genus_match": totals["genus_match"],
        "species_match": totals["species_match"],
        "genus_rate": pct(totals["genus_match"], totals["viable_pure"]),
        "species_rate": pct(totals["species_match"], totals["viable_pure"]),
    }


def summarize_isolates(isolates, options_data=None):
    totals = defaultdict(int)
    detail_objects = []

    isolate_list = list(isolates)
    totals["total_isolates"] = len(isolate_list)
    rule_matcher = _build_rule_matcher(options_data)

    for isolate in isolate_list:
        result = calculate_isolate_concordance(isolate, rule_matcher(isolate))

        totals["genus_match"] += result["genus_match"]
        totals["species_match"] += result["species_match"]
        totals["mixed_count"] += result["mixed"]
        totals["nonviable_count"] += result["nonviable"]
        totals["different_org"] += result["different_org"]
        totals["total_pairs"] += result["total_pairs"]
        totals["concordant_pairs"] += result["concordant_pairs"]
        totals["vmd"] += result["vmd"]
        totals["md"] += result["md"]
        totals["minor"] += result["minor"]
        detail_objects.extend(result["details"])

    totals["viable_pure"] = max(
        totals["total_isolates"] - totals["mixed_count"] - totals["nonviable_count"],
        0,
    )

    return totals, detail_objects


@transaction.atomic
def generate_concordance_for_batch(batch_id, user=None, options_data=None):
    batch = Batch_Table.objects.get(id=batch_id)
    if options_data is None:
        options_data = _existing_options_for_batch(batch)
    isolates = (
        Final_Data.objects
        .filter(f_Batch_id=batch)
        .prefetch_related("final_entries")
    )
    totals, detail_objects = summarize_isolates(isolates, options_data)
    defaults = _report_defaults_from_totals(totals, user)

    report, _ = ConcordanceReport.objects.update_or_create(
        batch=batch,
        final_data=None,
        defaults=defaults,
    )

    report.details.all().delete()
    for obj in detail_objects:
        obj.report = report
    ConcordanceDetail.objects.bulk_create(detail_objects)
    _persist_report_options(report, options_data)

    return report


@transaction.atomic
def generate_concordance_for_isolate(isolate, user=None, options_data=None):
    isolate = (
        Final_Data.objects
        .filter(pk=isolate.pk)
        .prefetch_related("final_entries")
        .first()
    )
    if not isolate:
        return None

    if options_data is None:
        options_data = _existing_options_for_isolate(isolate)
    rule_matcher = _build_rule_matcher(options_data)
    result = calculate_isolate_concordance(isolate, rule_matcher(isolate))
    totals = defaultdict(int)
    totals.update({
        "total_isolates": 1,
        "genus_match": result["genus_match"],
        "species_match": result["species_match"],
        "mixed_count": result["mixed"],
        "nonviable_count": result["nonviable"],
        "total_pairs": result["total_pairs"],
        "concordant_pairs": result["concordant_pairs"],
        "vmd": result["vmd"],
        "md": result["md"],
        "minor": result["minor"],
    })
    totals["viable_pure"] = max(1 - result["mixed"] - result["nonviable"], 0)

    defaults = _report_defaults_from_totals(totals, user)
    defaults["batch"] = isolate.f_Batch_id

    report, _ = ConcordanceReport.objects.update_or_create(
        final_data=isolate,
        defaults=defaults,
    )

    report.details.all().delete()
    for obj in result["details"]:
        obj.report = report
    ConcordanceDetail.objects.bulk_create(result["details"])
    _persist_report_options(report, options_data)

    return report


@transaction.atomic
def refresh_saved_concordance_reports(user=None):
    options_data = get_global_concordance_options_data()
    refreshed = 0

    batch_ids = (
        ConcordanceReport.objects
        .filter(final_data__isnull=True, batch__isnull=False)
        .values_list("batch_id", flat=True)
        .distinct()
    )
    for batch_id in batch_ids:
        generate_concordance_for_batch(batch_id, user, options_data=options_data)
        refreshed += 1

    isolates = (
        Final_Data.objects
        .filter(concordance_reports__final_data__isnull=False)
        .distinct()
        .prefetch_related("final_entries")
    )
    for isolate in isolates:
        generate_concordance_for_isolate(isolate, user, options_data=options_data)
        refreshed += 1

    return refreshed


def collect_concordance_dashboard(
    year=None,
    site_filter="",
    metric="ast_rate",
    sort_order="desc",
    min_pairs=0,
):
    isolates = Final_Data.objects.prefetch_related("final_entries")
    if year not in (None, "", "all"):
        isolates = isolates.filter(f_Referral_Date__year=year)

    report_qs = ConcordanceReport.objects.select_related("final_data", "batch")
    if year not in (None, "", "all"):
        report_qs = report_qs.filter(final_data__f_Referral_Date__year=year)

    site_choices = sorted({
        clean_str(report.final_data.f_SiteCode)
        for report in report_qs
        if report.final_data and clean_str(report.final_data.f_SiteCode)
    })
    if site_filter:
        isolates = isolates.filter(f_SiteCode=site_filter)
        report_qs = report_qs.filter(final_data__f_SiteCode=site_filter)

    totals, _ = summarize_isolates(
        isolates,
        get_global_concordance_options_data(),
    )

    site_buckets = {}
    for report in report_qs:
        isolate = report.final_data
        site_code = clean_str(getattr(isolate, "f_SiteCode", "")) or "N/A"
        site_name = clean_str(getattr(isolate, "f_Site_Name", ""))
        bucket = site_buckets.setdefault(site_code, {
            "site_code": site_code,
            "site_name": site_name,
            "report_count": 0,
            "total_isolates": 0,
            "total_pairs": 0,
            "concordant_pairs": 0,
            "vmd": 0,
            "md": 0,
            "minor": 0,
            "total_deviation": 0,
            "genus_match": 0,
            "species_match": 0,
        })
        bucket["report_count"] += 1
        bucket["total_isolates"] += report.total_isolates or 0
        bucket["total_pairs"] += report.total_pairs or 0
        bucket["concordant_pairs"] += report.concordant_pairs or 0
        bucket["vmd"] += report.vmd or 0
        bucket["md"] += report.md or 0
        bucket["minor"] += report.minor or 0
        bucket["total_deviation"] += report.total_deviation or 0
        bucket["genus_match"] += report.genus_match or 0
        bucket["species_match"] += report.species_match or 0
        if not bucket["site_name"] and site_name:
            bucket["site_name"] = site_name

    site_metric_labels = {
        "ast_rate": "AST Concordance %",
        "genus_rate": "Genus Concordance %",
        "species_rate": "Species Concordance %",
        "total_deviation": "Total Deviations",
        "critical_deviation": "Critical Deviations",
        "total_pairs": "AST Pairs",
        "report_count": "Reports",
    }
    if metric not in site_metric_labels:
        metric = "ast_rate"
    if sort_order not in {"asc", "desc"}:
        sort_order = "desc"

    site_rows = []
    for row in site_buckets.values():
        row["ast_rate"] = pct(row["concordant_pairs"], row["total_pairs"])
        row["genus_rate"] = pct(row["genus_match"], row["total_isolates"])
        row["species_rate"] = pct(row["species_match"], row["total_isolates"])
        row["critical_deviation"] = (row["vmd"] or 0) + (row["md"] or 0)
        row["metric_value"] = row[metric]
        if row["total_pairs"] >= min_pairs:
            site_rows.append(row)

    site_rows = sorted(
        site_rows,
        key=lambda item: (item["metric_value"], item["total_pairs"], item["report_count"]),
        reverse=sort_order == "desc",
    )
    site_chart_rows = site_rows[:12]
    max_metric_value = max([row["metric_value"] for row in site_chart_rows] or [0])
    for row in site_chart_rows:
        row["bar_pct"] = round((row["metric_value"] / max_metric_value) * 100, 1) if max_metric_value else 0

    site_summary = {
        "site_count": len(site_rows),
        "report_count": sum(row["report_count"] for row in site_rows),
        "total_pairs": sum(row["total_pairs"] for row in site_rows),
        "total_deviation": sum(row["total_deviation"] for row in site_rows),
        "avg_ast_rate": pct(
            sum(row["concordant_pairs"] for row in site_rows),
            sum(row["total_pairs"] for row in site_rows),
        ),
    }

    return {
        "total_isolates": totals["total_isolates"],
        "concordant_species": totals["species_match"],
        "species_rate": pct(totals["species_match"], totals["viable_pure"]),
        "concordant_genus": totals["genus_match"],
        "genus_rate": pct(totals["genus_match"], totals["viable_pure"]),
        "different_org": totals["different_org"],
        "mixed_count": totals["mixed_count"],
        "not_viable_count": totals["nonviable_count"],
        "total_pairs": totals["total_pairs"],
        "concordant_pairs": totals["concordant_pairs"],
        "ast_concordance_rate": pct(totals["concordant_pairs"], totals["total_pairs"]),
        "vmd": totals["vmd"],
        "vmd_rate": pct(totals["vmd"], totals["total_pairs"]),
        "md": totals["md"],
        "md_rate": pct(totals["md"], totals["total_pairs"]),
        "minor": totals["minor"],
        "minor_rate": pct(totals["minor"], totals["total_pairs"]),
        "isolates": isolates,
        "site_filter": site_filter,
        "site_metric": metric,
        "site_sort_order": sort_order,
        "site_min_pairs": min_pairs,
        "site_metric_label": site_metric_labels[metric],
        "site_metric_options": [
            {"value": value, "label": label}
            for value, label in site_metric_labels.items()
        ],
        "site_choices": site_choices,
        "site_concordance_rows": site_rows,
        "site_concordance_chart_rows": site_chart_rows,
        "site_concordance_summary": site_summary,
    }


def calculate_antibiotic_summary(details):
    summary = defaultdict(lambda: {"total": 0, "vmd": 0, "md": 0, "minor": 0})

    for detail in details:
        code = (detail.deviation_code or "").upper()
        if code not in {"A", "B", "C"}:
            continue

        summary[detail.antibiotic]["total"] += 1
        if code == "A":
            summary[detail.antibiotic]["vmd"] += 1
        elif code == "B":
            summary[detail.antibiotic]["md"] += 1
        elif code == "C":
            summary[detail.antibiotic]["minor"] += 1

    return dict(summary)
