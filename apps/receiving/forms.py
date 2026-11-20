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
        self.local_now = local_now
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
        received_time = cleaned_data.get("time_received")
        if date_received and received_time:
            received_at = timezone.make_aware(
                datetime.combine(date_received, received_time),
                timezone.get_current_timezone(),
            )
            if received_at > self.local_now:
                self.add_error(
                    "time_received",
                    "Secretariat receipt cannot be later than the current time.",
                )
        return cleaned_data
