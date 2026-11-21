"""Compact 38 x 17 mm laboratory labels, five columns by fifteen rows on A4."""
import base64
from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas


COLUMNS = 5
ROWS = 15
LABELS_PER_PAGE = COLUMNS * ROWS


def render_sticker_pdf(labels):
    output = BytesIO()
    pdf = Canvas(output, pagesize=A4)
    pdf.setTitle('Laboratory accession stickers')
    width, height, gap = 38 * mm, 17 * mm, 1 * mm
    for index, label in enumerate(labels):
        if index and index % LABELS_PER_PAGE == 0:
            pdf.showPage()
        cell = index % LABELS_PER_PAGE
        x = 8 * mm + (cell % COLUMNS) * (width + gap)
        y = A4[1] - 8 * mm - (cell // COLUMNS) * (height + gap) - height
        pdf.setStrokeColorRGB(.7, .7, .7)
        pdf.setLineWidth(.25)
        pdf.rect(x, y, width, height)
        accession = label['isolate'].accession_number
        # Fit unusually long historical accessions without clipping their text.
        size = 8.5
        while pdf.stringWidth(accession, 'Helvetica-Bold', size) > width - 3 * mm and size > 3:
            size -= .25
        pdf.setFillColorRGB(0, 0, 0)
        pdf.setFont('Helvetica-Bold', size)
        pdf.drawCentredString(x + width / 2, y + 13.7 * mm, accession)
        image = ImageReader(BytesIO(base64.b64decode(label['qr_data_uri'].split(',', 1)[1])))
        pdf.drawImage(image, x + 1 * mm, y + .6 * mm, 12 * mm, 12 * mm, mask='auto')
        code_size = 13
        while pdf.stringWidth(label['code'], 'Helvetica-Bold', code_size) > 23 * mm and code_size > 4:
            code_size -= .5
        pdf.setFont('Helvetica-Bold', code_size)
        pdf.drawCentredString(x + 25.5 * mm, y + 7.3 * mm, label['code'])
        pdf.setFont('Helvetica', 7)
        pdf.drawCentredString(x + 25.5 * mm, y + 2.4 * mm, label['harvest'])
    pdf.save()
    return output.getvalue()
