from io import BytesIO

from django.conf import settings
from django.urls import reverse
import qrcode


def build_absolute_qr_url(*, path, request):
    base_url = getattr(settings, "RECEIVING_QR_BASE_URL", "").rstrip("/")
    return f"{base_url}{path}" if base_url else request.build_absolute_uri(path)


def build_receipt_detail_url(*, receipt, request):
    path = reverse("receiving_detail", kwargs={"receiving_id": receipt.receiving_id})
    return build_absolute_qr_url(path=path, request=request)


def build_receipt_scan_url(*, receipt, request):
    path = reverse("receiving_scan", kwargs={"receiving_id": receipt.receiving_id})
    return build_absolute_qr_url(path=path, request=request)


def build_isolate_scan_url(*, isolate, request):
    path = reverse("laboratory_isolate_scan", args=[isolate.accession_number])
    return build_absolute_qr_url(path=path, request=request)


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
