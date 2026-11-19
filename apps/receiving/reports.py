from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .models import LaboratoryOrganismCount


REGISTER_COLUMNS = [
    ("HA", "HA", "Haemophilus"), ("NGO", "NGO", "Neisseria gonorrhoeae"),
    ("SPN", "SPN", "Streptococcus pneumoniae"), ("FAST_OTHER", "Other", "Other fastidious"),
    ("ECO", "ECO", "Escherichia coli"), ("KLE", "KLE", "Klebsiella"),
    ("ENTERO_OTHER", "Other", "Other Enterobacterales"),
    ("SAL", "SAL", "Salmonella"), ("SHI", "SHI", "Shigella"),
    ("VIB", "VIB", "Vibrio"), ("GNR_OTHER", "Other", "Other Gram-negative"),
    ("PAE", "PAE", "Pseudomonas aeruginosa"), ("ACI", "ACI", "Acinetobacter"),
    ("NFO_OTHER", "Other", "Other non-fermenting"),
    ("SAU", "SAU", "Staphylococcus aureus"), ("ENT", "ENT", "Enterococcus"),
    ("STR", "STR", "Streptococcus"), ("GP_OTHER", "Other", "Other Gram-positive"),
]
REGISTER_CODES = [code for code, _short, _label in REGISTER_COLUMNS]


def build_register_rows(records):
    rows = []
    totals = {"received": 0, "rejected": 0, "accepted": 0, "processed": 0, "batch": 0}
    organism_totals = {code: 0 for code in REGISTER_CODES}
    for record in records:
        ranges = list(record.accession_ranges.all()) or [None]
        totals["received"] += record.total_isolates_received
        totals["rejected"] += record.isolates_rejected
        totals["accepted"] += record.isolates_accepted
        totals["processed"] += record.total_isolates_processed
        for index, accession in enumerate(ranges):
            counts = {code: 0 for code in REGISTER_CODES}
            if accession:
                for item in accession.organism_counts.all():
                    counts[item.organism_code] = item.count
                    organism_totals[item.organism_code] += item.count
                totals["batch"] += accession.isolate_count
            rows.append({
                "record": record,
                "accession": accession,
                "first": index == 0,
                "organisms": counts,
            })
    return rows, totals, organism_totals


def render_register_pdf(records, month, year):
    rows, totals, organism_totals = build_register_rows(records)
    output = BytesIO()
    document = SimpleDocTemplate(
        output, pagesize=landscape(A4), leftMargin=7 * mm, rightMargin=7 * mm,
        topMargin=7 * mm, bottomMargin=7 * mm,
        title="Receiving and Inventory of Referred Isolates",
    )
    styles = getSampleStyleSheet()
    story = [
        Paragraph("<b>HEALTH CARE DELIVERY</b>", styles["Title"]),
        Paragraph("<b>RECEIVING AND INVENTORY OF REFERRED ISOLATES</b>", styles["Heading2"]),
        Paragraph(f"Month: {month:02d} &nbsp;&nbsp;&nbsp; Year: {year}", styles["Normal"]),
        Spacer(1, 3 * mm),
    ]
    headers = ["Date RCVD", "RI", "Rcvd", "Rej", "Acc", "DR", "Batch", "#", "Accession / Stock No"]
    headers += [short for _code, short, _label in REGISTER_COLUMNS] + ["Staff"]
    group_headers = ["", "", "", "", "", "", "", "", "", "Fastidious", "", "", "", "Enterobacterales", "", "", "Other Gram-negative", "", "", "", "Non-fermenters", "", "", "Gram-positive", "", "", "", ""]
    data = [group_headers, headers]
    for row in rows:
        record, accession = row["record"], row["accession"]
        receipt = record.receipt
        prefix = [
            receipt.lab_date_received.strftime("%d %b") if row["first"] else "",
            receipt.site.SiteCode if row["first"] else "",
            record.total_isolates_received if row["first"] else "",
            record.isolates_rejected if row["first"] else "",
            record.isolates_accepted if row["first"] else "",
            accession.date_referred.strftime("%d %b") if accession else "",
            f"{accession.batch_number}/{accession.total_batches}" if accession else "",
            accession.isolate_count if accession else "",
            accession.reference_range if accession else "",
        ]
        staff = receipt.lab_received_by.get_full_name() or receipt.lab_received_by.username if receipt.lab_received_by else ""
        data.append(prefix + [row["organisms"][code] or "" for code in REGISTER_CODES] + [staff])
    data.append([
        "TOTAL", "", totals["received"], totals["rejected"], totals["accepted"], "", "",
        totals["batch"], "",
    ] + [organism_totals[code] or "" for code in REGISTER_CODES] + [""])

    usable_width = landscape(A4)[0] - 14 * mm
    fixed = [17, 15, 11, 11, 11, 17, 14, 9, 28]
    widths = [value * mm for value in fixed]
    remaining = usable_width - sum(widths) - 25 * mm
    widths += [remaining / len(REGISTER_CODES)] * len(REGISTER_CODES) + [25 * mm]
    table = Table(data, repeatRows=2, colWidths=widths)
    table.setStyle(TableStyle([
        ("SPAN", (9, 0), (12, 0)), ("SPAN", (13, 0), (15, 0)),
        ("SPAN", (16, 0), (19, 0)), ("SPAN", (20, 0), (22, 0)),
        ("SPAN", (23, 0), (26, 0)),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.black),
        ("BACKGROUND", (0, 0), (-1, 1), colors.HexColor("#d9eef2")),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#e8f4ee")),
        ("FONTNAME", (0, 0), (-1, 1), "Helvetica-Bold"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 4.7),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 1),
        ("RIGHTPADDING", (0, 0), (-1, -1), 1),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(table)
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph("Legend: RCVD - Received; RI - Referring Institution; DR - Date Referred/Processed", styles["Normal"]))
    document.build(story)
    return output.getvalue()
