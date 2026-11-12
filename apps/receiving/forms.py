from datetime import datetime

from django import forms
from django.utils import timezone

from .models import ReferralReceipt


class ReferralReceiptForm(forms.ModelForm):
    class Meta:
        model = ReferralReceipt
        fields = (
            "tracking_number",
            "site",
            "date_sent",
            "date_received",
            "time_received",
            "remarks",
        )
        widgets = {
            "tracking_number": forms.TextInput(attrs={"class": "form-control"}),
            "site": forms.Select(attrs={"class": "form-control"}),
            "date_sent": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "date_received": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "time_received": forms.TimeInput(attrs={"class": "form-control", "type": "time"}),
            "remarks": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, now=None, **kwargs):
        super().__init__(*args, **kwargs)
        local_now = timezone.localtime(now or timezone.now())
        self.initial.setdefault("date_received", local_now.date())
        self.initial.setdefault("time_received", local_now.time().replace(second=0, microsecond=0))
        self.fields["site"].queryset = self.fields["site"].queryset.order_by("SiteCode", "SiteName")

    def clean_tracking_number(self):
        return " ".join((self.cleaned_data.get("tracking_number") or "").split())

    def clean(self):
        cleaned_data = super().clean()
        date_sent = cleaned_data.get("date_sent")
        date_received = cleaned_data.get("date_received")
        if date_sent and date_received and date_sent > date_received:
            self.add_error(
                "date_sent",
                "Date sent by courier cannot be later than the Secretariat received date.",
            )
        return cleaned_data


class LabReceiptForm(forms.Form):
    lab_date_received = forms.DateField(
        label="Date Received by Laboratory",
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}),
    )
    lab_time_received = forms.TimeField(
        label="Time Received by Laboratory",
        widget=forms.TimeInput(attrs={"class": "form-control", "type": "time"}),
    )

    def __init__(self, *args, receipt, now=None, **kwargs):
        self.receipt = receipt
        super().__init__(*args, **kwargs)
        local_now = timezone.localtime(now or timezone.now())
        self.initial.setdefault("lab_date_received", local_now.date())
        self.initial.setdefault(
            "lab_time_received",
            local_now.time().replace(second=0, microsecond=0),
        )

    def clean(self):
        cleaned_data = super().clean()
        received_date = cleaned_data.get("lab_date_received")
        received_time = cleaned_data.get("lab_time_received")
        if not received_date or not received_time or not self.receipt.endorsed_at:
            return cleaned_data

        received_at = timezone.make_aware(
            datetime.combine(received_date, received_time),
            timezone.get_current_timezone(),
        )
        if received_at < self.receipt.endorsed_at:
            self.add_error(
                "lab_time_received",
                "Laboratory receipt cannot be earlier than endorsement.",
            )
        return cleaned_data
