from io import BytesIO

from django.conf import settings
from django.urls import reverse
import qrcode


def build_receipt_detail_url(*, receipt, request):
    path = reverse("receiving_detail", kwargs={"receiving_id": receipt.receiving_id})
    base_url = getattr(settings, "RECEIVING_QR_BASE_URL", "").rstrip("/")
    if base_url:
        return f"{base_url}{path}"
    return request.build_absolute_uri(path)


def render_qr_png(payload):
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=4,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()
