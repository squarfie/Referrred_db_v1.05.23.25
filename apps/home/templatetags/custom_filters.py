import builtins
import re
from datetime import timedelta
from django import template
from django.db.models import Q
from operator import attrgetter
from django.utils.html import escape
from django.utils.safestring import mark_safe
from apps.home.models import Organism_List

register = template.Library()

FASTIDIOUS_PLUS_LAYOUT_SPECIES_GROUPS = {
    "ABI", "GCT", "AGT", "BD-", "BR-", "CAM", "CAR", "EIK", "FRA",
    "HA-", "HEL", "KIN", "LEG", "MOR", "NE-", "NV", "N/A", "NA",
    "N.A.", "NONE", "NULL", "NAN",
}


def _uses_fastidious_plus_layout(organism):
    if not organism:
        return False

    organism_type = str(organism.get("Organism_Type") or "").strip()
    if organism_type == "+":
        return True

    codes = {
        str(organism.get(field) or "").strip().upper()
        for field in ("Whonet_Org_Code", "Replaced_by", "Species_Group", "Genus_Group", "Genus_Code")
    }
    codes.discard("")
    if codes & FASTIDIOUS_PLUS_LAYOUT_SPECIES_GROUPS:
        return True

    return _organism_name_uses_fastidious_plus_layout(organism.get("Organism")) or bool(
        {"HIN", "NME"} & codes
    )


def _organism_name_uses_fastidious_plus_layout(organism_name):
    name = str(organism_name or "").strip().lower()
    return name.startswith(("haemophilus ", "neisseria "))


@register.filter
def dict_lookup(dictionary, key):
    """Safely retrieve a value from a dictionary using a key."""
    if isinstance(dictionary, dict):
        return dictionary.get(key, None)
    return None

@register.filter
def get_item(dictionary, key):
    """Retrieve a value from a dictionary using a key."""
    return dictionary.get(key, None)

@register.filter
def make_tuple(value1, value2):
    """Creates a tuple with two values."""
    return (value1, value2)

@register.filter
def age_result_display(age, isolate):
    """
    Display infant age as months/days for lab results while keeping Age numeric.
    Patients 1 year old and above stay as the stored integer age.
    """
    if age in (None, ""):
        return ""

    stored_display = builtins.getattr(isolate, "Age_Display", "")
    if stored_display:
        return stored_display

    birth_date = builtins.getattr(isolate, "Date_Birth", None)
    specimen_date = builtins.getattr(isolate, "Spec_Date", None)
    if not birth_date or not specimen_date or specimen_date < birth_date:
        return age

    years = specimen_date.year - birth_date.year
    months = specimen_date.month - birth_date.month
    days = specimen_date.day - birth_date.day

    if days < 0:
        previous_month_last_day = (
            specimen_date.replace(day=1) - timedelta(days=1)
        ).day
        days += previous_month_last_day
        months -= 1

    if months < 0:
        months += 12
        years -= 1

    if years > 0:
        return age

    if months > 0:
        return f"{months}m"
    return f"{days}d"

# @register.simple_tag
# def get_existing_value(existing_entries, entry_id, value_type):
#     """
#     Retrieves the existing value for a given breakpoint entry.
#     """
#     # entry = existing_entries.filter(ab_breakpoints_id=entry_id).first()
#     entry = existing_entries.filter(ab_breakpoints_id__in=[entry_id]).first()

#     if entry:
#         if value_type == 'disk':
#             return entry.ab_Disk_value 
#         elif value_type == 'mic':
#             return entry.ab_MIC_value
#         elif value_type == 'retest_disk':
#             return entry.ab_Retest_DiskValue
#         elif value_type == 'retest_mic':
#             return entry.ab_Retest_MICValue
#         elif value_type == 'mic_operand':
#             return entry.ab_MIC_operand or ''
#         elif value_type == 'retest_mic_operand':
#             return entry.ab_Retest_MIC_operand or ''
#         elif value_type == 'alert_mic':
#             return entry.ab_AlertMIC
#         elif value_type == 'retest_alert_mic':
#             return entry.ab_Retest_AlertMIC
#         # for encoded RIS values
#         elif value_type == 'disk_enris':
#             return entry.ab_Disk_enRIS
#         elif value_type == 'mic_enris':
#             return entry.ab_MIC_enRIS
#         elif value_type == 'retest_disk_enris':
#             return entry.ab_Retest_Disk_enRIS
#         elif value_type == 'retest_mic_enris':
#             return entry.ab_Retest_MIC_enRIS
#     return ''


# @register.simple_tag
# def get_existing_value(existing_entries, entry_id, value_type):
#     """
#     Retrieves the existing value for a given breakpoint entry.
#     Supports main and retest antibiotics.
#     """
#     # Try to fetch main entry first
#     entry = existing_entries.filter(ab_breakpoints_id=entry_id, ab_Abx_code__isnull=False).first()

#     # If not found, try retest entry
#     if not entry:
#         entry = existing_entries.filter(ab_breakpoints_id=entry_id, ab_Retest_Abx_code__isnull=False).first()
    
#     if entry:
#         if value_type == 'disk':
#             return entry.ab_Disk_value or ''
#         elif value_type == 'mic':
#             return entry.ab_MIC_value or ''
#         elif value_type == 'retest_disk':
#             return entry.ab_Retest_DiskValue or ''
#         elif value_type == 'retest_mic':
#             return entry.ab_Retest_MICValue or ''
#         elif value_type == 'mic_operand':
#             return entry.ab_MIC_operand or ''
#         elif value_type == 'retest_mic_operand':
#             return entry.ab_Retest_MIC_operand or ''
#         elif value_type == 'alert_mic':
#             return entry.ab_AlertMIC
#         elif value_type == 'retest_alert_mic':
#             return entry.ab_Retest_AlertMIC
#         elif value_type == 'disk_enris':
#             return entry.ab_Disk_enRIS or ''
#         elif value_type == 'mic_enris':
#             return entry.ab_MIC_enRIS or ''
#         elif value_type == 'retest_disk_enris':
#             return entry.ab_Retest_Disk_enRIS or ''
#         elif value_type == 'retest_mic_enris':
#             return entry.ab_Retest_MIC_enRIS or ''
#     return ''



@register.simple_tag
def get_existing_value(existing_entries, identifier, value_type):
    """
    Retrieves the existing value for a given antibiotic entry.
    Supports both Breakpoints-linked entries (by ID)
    and Antibiotic_List entries (by Whonet_Abx string).
    Also supports both main and retest antibiotic fields.
    """

    def code_variants(value):
        code = str(value or "").strip().upper()
        variants = [code]
        if not code.endswith("_VAL"):
            variants.append(f"{code}_VAL")
        return variants

    def antibiotic_family(value):
        return str(value or "").strip().upper().split("_", 1)[0]

    def entry_has_requested_value(entry_obj, requested_type):
        if requested_type in {"disk", "disk_enris"}:
            return entry_obj.ab_Disk_value is not None or bool((entry_obj.ab_Disk_enRIS or "").strip())
        if requested_type in {"mic", "mic_operand", "mic_enris", "alert_mic"}:
            return any([
                entry_obj.ab_MIC_value is not None,
                bool((entry_obj.ab_MIC_operand or "").strip()),
                bool((entry_obj.ab_MIC_enRIS or "").strip()),
                bool(entry_obj.ab_AlertMIC),
            ])
        if requested_type in {"retest_disk", "retest_disk_enris"}:
            return entry_obj.ab_Retest_DiskValue is not None or bool((entry_obj.ab_Retest_Disk_enRIS or "").strip())
        if requested_type in {"retest_mic", "retest_mic_operand", "retest_mic_enris", "retest_alert_mic"}:
            return any([
                entry_obj.ab_Retest_MICValue is not None,
                bool((entry_obj.ab_Retest_MIC_operand or "").strip()),
                bool((entry_obj.ab_Retest_MIC_enRIS or "").strip()),
                bool(entry_obj.ab_Retest_AlertMIC),
            ])
        return False

    def find_code_entry(queryset, variants, requested_type):
        retest = requested_type.startswith("retest_")
        code_field = "ab_Retest_Abx_code" if retest else "ab_Abx_code"
        exact_entry = None

        for variant in variants:
            filter_kwargs = {f"{code_field}__iexact": variant}
            exact_entry = queryset.filter(**filter_kwargs).first()
            if exact_entry and entry_has_requested_value(exact_entry, requested_type):
                return exact_entry

        family = antibiotic_family(variants[0])
        if family:
            for candidate in queryset:
                candidate_code = builtins.getattr(candidate, code_field, "")
                if antibiotic_family(candidate_code) == family and entry_has_requested_value(candidate, requested_type):
                    return candidate

        return exact_entry

    # --- Try to detect identifier type ---
    entry = None
    if str(identifier).isdigit():
        # Numeric = BreakpointsTable ID
        entry = existing_entries.filter(ab_breakpoints_id=identifier).first()
    else:
        # String = Whonet_Abx code (e.g., "AMC_ND20")
        variants = code_variants(identifier)
        if value_type.startswith("retest_"):
            entry = find_code_entry(existing_entries, variants, value_type)
        else:
            entry = find_code_entry(existing_entries, variants, value_type)

    # --- If found, return appropriate field ---
    if entry:
        match value_type:
            case 'disk':
                return entry.ab_Disk_value or ''
            case 'mic':
                return entry.ab_MIC_value or ''
            case 'retest_disk':
                return entry.ab_Retest_DiskValue or ''
            case 'retest_mic':
                return entry.ab_Retest_MICValue or ''
            case 'mic_operand':
                return entry.ab_MIC_operand or ''
            case 'retest_mic_operand':
                return entry.ab_Retest_MIC_operand or ''
            case 'alert_mic':
                return entry.ab_AlertMIC
            case 'retest_alert_mic':
                return entry.ab_Retest_AlertMIC
            case 'disk_enris':
                return entry.ab_Disk_enRIS or ''
            case 'mic_enris':
                return entry.ab_MIC_enRIS or ''
            case 'retest_disk_enris':
                return entry.ab_Retest_Disk_enRIS or ''
            case 'retest_mic_enris':
                return entry.ab_Retest_MIC_enRIS or ''
            case _:
                return ''
    return ''



@register.filter
def multi_sort(queryset, fields):
    fields = fields.split(',')
    return sorted(queryset, key=attrgetter(*fields))

@register.filter
def getattr(obj, attr_name):
    """Template filter to dynamically get object attribute."""
    return builtins.getattr(obj, attr_name, "")

# auto format into scientific only

# @register.filter
# def scientific_name(value):
#     """
#     Format organism names for result printouts:
#     Genus capitalized, species/subsequent words lowercase.

#     Example: "Staphylococcus Aureus" -> "Staphylococcus aureus"
#     """
#     text = re.sub(r"\s+", " ", str(value or "").strip())
#     if not text:
#         return ""

#     words = text.split(" ")
#     formatted = [words[0][:1].upper() + words[0][1:].lower()]
#     formatted.extend(word.lower() for word in words[1:])
#     return " ".join(formatted)




# updated auto scientific format but when n/a value put an empty string
# @register.filter
# def scientific_name(value):
#     """
#     Format organism names for result printouts:
#     Genus capitalized, species/subsequent words lowercase.

#     If value is N/A, NA, None, null, or nan, return blank.
#     """

#     text = re.sub(r"\s+", " ", str(value or "").strip())

#     if not text:
#         return ""

#     if text.lower() in ["n/a", "na", "n.a.", "none", "null", "nan"]:
#         return ""

#     words = text.split(" ")
#     formatted = [words[0][:1].upper() + words[0][1:].lower()]
#     formatted.extend(word.lower() for word in words[1:])

#     return " ".join(formatted)


# updated auto scientific format but when n/a value put an empty string and keep Typhi capitalized
@register.filter
def scientific_name(value):
    """
    Format organism names for result printouts:
    Genus capitalized, species/subsequent words lowercase.

    The word "Typhi" retains its proper capitalization.

    If value is N/A, NA, None, null, or nan, return blank.
    """

    text = re.sub(r"\s+", " ", str(value or "").strip())

    if not text:
        return ""

    if text.lower() in ["n/a", "na", "n.a.", "none", "null", "nan"]:
        return ""

    words = text.split(" ")

    formatted = [
        words[0][:1].upper() + words[0][1:].lower()
    ]

    formatted.extend(
        "Typhi" if word.lower() == "typhi" else word.lower()
        for word in words[1:]
    )

    return " ".join(formatted)




@register.filter
def professional_caps(value):
    """
    Keep professional credentials/designations uppercase in printouts.

    Example: "Sonia B. Sia, Md" -> "Sonia B. Sia, MD"
    """
    text = str(value or "").strip()
    if not text:
        return ""

    credential_tokens = [
        "md", "rmt", "rn", "mt", "mls", "phd", "mph", "msc", "ms", "ma",
        "drph", "fpsp", "fpcp", "oic", "prc", "cmo", "rpmt",
    ]
    pattern = r"\b(" + "|".join(re.escape(token) for token in credential_tokens) + r")\b"
    return re.sub(pattern, lambda match: match.group(0).upper(), text, flags=re.IGNORECASE)


@register.filter
def with_staff_credentials(value):
    """
    Append ARSRL staff credentials to signatory names when available.
    """
    name = str(value or "").strip()
    if not name:
        return ""

    from apps.home.models import arsStaff_Details

    staff = (
        arsStaff_Details.objects
        .filter(Staff_Name__iexact=name)
        .values("Staff_Credentials")
        .first()
    )
    credentials = str((staff or {}).get("Staff_Credentials") or "").strip()
    if not credentials:
        return name

    if re.search(rf"(?:,\s*|\s+){re.escape(credentials)}$", name, flags=re.IGNORECASE):
        return name

    return f"{name}, {credentials}"


@register.filter
def staff_license(value):
    name = re.sub(r"\s+", " ", str(value or "").strip())
    if not name:
        return ""

    credential_pattern = r"(?:,\s*|\s+)(MD|RMT|RN|MT|MLS|PHD|MPH|MSC|MS|MA|DRPH|FPSP|FPCP|OIC|PRC|RPMT)$"
    candidates = [
        name,
        re.sub(credential_pattern, "", name, flags=re.IGNORECASE).strip(),
    ]

    from apps.home.models import arsStaff_Details

    for candidate in dict.fromkeys(candidates):
        if not candidate:
            continue
        staff = (
            arsStaff_Details.objects
            .filter(Staff_Name__iexact=candidate)
            .values("Staff_License")
            .first()
        )
        license_no = str((staff or {}).get("Staff_License") or "").strip()
        if license_no:
            return license_no

    return ""


@register.filter
def break_long_words(value, width=20):
    if not value:
        return value
    return re.sub(r'(\S{%d})' % width, r'\1 ', value)


@register.filter
def accession_ref_no(accession):
    """
    Extract the numeric reference suffix from accession numbers.

    Examples:
    - 25ARS_APM0278 -> 278
    - CMC_04212025_1.1_0029-0037 -> 29
    """
    text = str(accession or "").strip()
    if not text:
        return ""

    match = re.search(r"(\d+)(?!.*\d)", text)
    if not match:
        return text

    number = match.group(1).lstrip("0")
    return number or "0"


# Custom filter to format MIC values with dynamic decimal places

# if 32.0000 make it 32.0
# @register.filter
# def mic_format(value):
#     try:
#         val = float(value)

#         if val > 0.25:
#             return f"{val:.1f}"   # 1 decimal place
#         else:
#             return f"{val:.3f}".rstrip('0').rstrip('.')  # clean trailing zeros

#     except (TypeError, ValueError):
#         return value

# if 32.0000 make it 32
@register.filter
def mic_format(value):
    try:
        val = float(value)

        if val > 0.25:
            return f"{val:.1f}".rstrip('0').rstrip('.')
        else:
            return f"{val:.3f}".rstrip('0').rstrip('.')

    except (TypeError, ValueError):
        return value


@register.filter
def highlight_interpretations(value):
    """Highlight standalone antimicrobial interpretations in a result string."""
    if value in (None, "", "-"):
        return ""

    escaped_value = str(escape(value))

    def badge(match):
        interpretation = match.group(1)
        return (
            f'<span class="interpretation-badge interpretation-{interpretation.lower()}" '
            f'aria-label="Interpretation {interpretation}">{interpretation}</span>'
        )

    return mark_safe(
        re.sub(r"(?<![A-Za-z0-9_])(SDD|R|I|S)(?![A-Za-z0-9_])", badge, escaped_value)
    )


@register.filter
def blank_if_dash(value):
    """Render internal dash placeholders as visually empty table cells."""
    return "" if value in (None, "", "-") else value


# def _recommendation_items(value):
#     if value is None or str(value).strip() == "":
#         return []

#     text = str(value).strip()

#     # Convert HTML line breaks to normal newlines
#     text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)

#     # Split text when it sees existing numbering like:
#     # 1. text
#     # 2. text
#     # 1) text
#     # 2) text
#     # also splits on newlines to handle unnumbered recommendations
    
#     parts = re.split(r'(?=\b\d+[\.\)]\s* )', text)

#     cleaned_items = []

#     for part in parts:
#         part = part.strip()

#         if not part:
#             continue

#         # Remove old numbering
#         part = re.sub(r'^\d+[\.\)]\s*', '', part).strip()

#         if part:
#             cleaned_items.append(part)

#     return cleaned_items







# # to format numbering list in recommnedation field

# @register.filter
# def number_recommendations(value):
#     """
#     Forces recommendation text into a numbered list.

#     Example output:
#     1. First recommendation
#     2. Second recommendation
#     """

#     cleaned_items = _recommendation_items(value)

#     if not cleaned_items:
#         return mark_safe("&nbsp;")

#     numbered_lines = []

#     for index, item in enumerate(cleaned_items, start=1):
#         numbered_lines.append(
#             f'<strong>{index}.</strong> {escape(item)}'
#         )

#     return mark_safe("<br/>".join(numbered_lines))








def _recommendation_items(value):
    if value is None or str(value).strip() == "":
        return []

    text = str(value)

    # Convert HTML <br> tags into actual newlines
    text = re.sub(
        r'<br\s*/?>',
        '\n',
        text,
        flags=re.IGNORECASE
    )

    # Normalize line endings
    text = text.replace('\r\n', '\n').replace('\r', '\n')

    lines = text.split('\n')

    cleaned_items = []
    current_item = None

    for line in lines:

        # Skip completely empty lines
        if not line.strip():
            continue

        # Check if the line was originally indented
        is_indented = bool(re.match(r'^[ \t]+', line))

        stripped_line = line.strip()

        # Check for existing numbering:
        #
        # 1. Recommendation
        # 2) Recommendation
        numbered_match = re.match(
            r'^\d+\s*[\.\)]\s*(.*)$',
            stripped_line
        )

        if numbered_match:
            # Save previous recommendation first
            if current_item is not None:
                cleaned_items.append(current_item)

            # Start new recommendation without old numbering
            current_item = numbered_match.group(1).strip()

        elif is_indented and current_item is not None:
            # Indented line = continuation/sub-item of current recommendation
            current_item += '\n' + line

        else:
            # Unnumbered, non-indented line = new recommendation
            if current_item is not None:
                cleaned_items.append(current_item)

            current_item = stripped_line

    # Add final item
    if current_item is not None:
        cleaned_items.append(current_item)

    return cleaned_items


def _preserve_recommendation_spacing(line):
    return str(escape(line.expandtabs(4))).replace(" ", "&nbsp;")


@register.filter
def number_recommendations(value):
    """
    Formats recommendation text as a numbered list.

    Indented lines remain under the preceding recommendation
    without receiving their own number.
    """

    cleaned_items = _recommendation_items(value)

    if not cleaned_items:
        return mark_safe("&nbsp;")

    numbered_lines = []

    for index, item in enumerate(cleaned_items, start=1):

        # Separate main recommendation from continuation lines
        item_lines = item.split('\n')

        formatted_lines = []

        for line_number, line in enumerate(item_lines):

            if line_number == 0:
                # Main recommendation
                formatted_lines.append(
                    f'<strong>{index}.</strong> {escape(line.strip())}'
                )

            else:
                formatted_lines.append(
                    '<span class="recommendation-continuation">'
                    f'{_preserve_recommendation_spacing(line)}'
                    '</span>'
                )

        numbered_lines.append(
            '<br/>'.join(formatted_lines)
        )

    return mark_safe(
        '<br/>'.join(numbered_lines)
    )



# do not put number list if only one recommendation is encoded
# @register.filter
# def number_recommendations(value):
#     """
#     Formats recommendation text:
#     - If only one recommendation, display without numbering.
#     - If multiple recommendations, display as numbered list.
#     """

#     if value is None or str(value).strip() == "":
#         return mark_safe("&nbsp;")

#     text = str(value).strip()

#     # Convert HTML line breaks to normal newlines
#     text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)

#     # Split text when it sees existing numbering like:
#     # 1. text
#     # 2. text
#     # 1) text
#     # 2) text
#     parts = re.split(r'(?=\b\d+[\.\)]\s*)', text)

#     cleaned_items = []

#     for part in parts:
#         part = part.strip()

#         if not part:
#             continue

#         # Remove old numbering
#         part = re.sub(r'^\d+[\.\)]\s*', '', part).strip()

#         if part:
#             cleaned_items.append(part)

#     if not cleaned_items:
#         return mark_safe("&nbsp;")

#     # If only one recommendation, do not show "1."
#     if len(cleaned_items) == 1:
#         return mark_safe(escape(cleaned_items[0]))

#     numbered_lines = []

#     for index, item in enumerate(cleaned_items, start=1):
#         numbered_lines.append(
#             f'<strong>{index}.</strong> {escape(item)}'
#         )

#     return mark_safe("<br/>".join(numbered_lines))



@register.filter
def blank_none(value):
    if value is None:
        return ""

    value_str = str(value).strip()

    if value_str.lower() in ["none", "null", "nan", "nat", "n/a", "na", "n.a.", "None"]:
        return ""

    return value


@register.simple_tag
def clean_firstof(*values):
    """
    Returns the first usable value.
    Treats None, 'None', 'null', 'nan', and blank strings as empty.
    """
    for value in values:
        if value is None:
            continue

        value_str = str(value).strip()

        if value_str == "":
            continue

        if value_str.lower() in ["none", "null", "nan", "nat", "n/a", "na", "n.a."]:
            continue

        return value_str

    return ""


@register.filter
def blank_nbsp(value):
    if value is None:
        return "\u00A0"

    value = str(value).strip()

    if value == "" or value.lower() in ["none", "null", "nan"]:
        return "\u00A0"

    return value


@register.filter
def prefer_text(value, fallback=""):
    value_str = "" if value is None else str(value).strip()
    if value_str.lower() in ["n/a", "na", "n.a.", "none", "null", "nan"]:
        return ""
    if value_str:
        return value

    fallback_str = "" if fallback is None else str(fallback).strip()
    if fallback_str.lower() in ["n/a", "na", "n.a.", "none", "null", "nan"]:
        return ""

    return "" if fallback is None else fallback


@register.filter
def organism_type_is_plus(org_code):
    code = str(org_code or "").strip()
    if _organism_name_uses_fastidious_plus_layout(code):
        return True
    if not code:
        return False

    organism = (
        Organism_List.objects
        .filter(Q(Whonet_Org_Code__iexact=code) | Q(Replaced_by__iexact=code) | Q(Organism__iexact=code))
        .values("Whonet_Org_Code", "Replaced_by", "Organism_Type", "Species_Group", "Genus_Group", "Genus_Code", "Organism")
        .first()
    )
    return _uses_fastidious_plus_layout(organism)


@register.filter
def blank_empty(value):
    if value is None:
        return ""

    value = str(value).strip()

    if value == "" or value.lower() in ["none", "null", "nan"]:
        return ""

    return value

@register.filter
def auto_font_size(value):
    if value is None:
        return 7

    value = str(value).strip()
    length = len(value)

    if length <= 8:
        return 7
    elif length <= 12:
        return 6.5
    elif length <= 16:
        return 6
    elif length <= 20:
        return 5.5
    elif length <= 25:
        return 5
    else:
        return 4.5
