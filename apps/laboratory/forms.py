from datetime import datetime, timedelta
from django import forms
from django.utils import timezone
from .models import LaboratoryAccession, LaboratoryOrganismCount, LaboratoryReceiving

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
        self.local_now = local_now
        # A freshly endorsed batch may have seconds within the current minute.
        # Round up the suggested minute so the default never precedes endorsement.
        suggested = local_now.replace(second=0, microsecond=0)
        if receipt.endorsed_at and suggested < timezone.localtime(receipt.endorsed_at):
            suggested += timedelta(minutes=1)
        self.initial.setdefault("lab_date_received", suggested.date())
        self.initial.setdefault("lab_time_received", suggested.time())

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
        if received_at > self.local_now:
            self.add_error(
                "lab_time_received",
                "Laboratory receipt cannot be later than the current time.",
            )
        return cleaned_data


class LaboratoryReceivingForm(forms.ModelForm):
    class Meta:
        model = LaboratoryReceiving
        fields = (
            "specimen_condition", "discrepancies", "remarks",
            "total_isolates_received", "isolates_rejected", "isolates_accepted",
            "date_accepted", "total_isolates_processed",
        )
        labels = {
            "specimen_condition": "Condition on receipt",
            "isolates_rejected": "Rejected - LAB decision",
            "isolates_accepted": "Accepted - LAB decision",
        }
        widgets = {
            "specimen_condition": forms.TextInput(attrs={"class": "form-control"}),
            "discrepancies": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
            "remarks": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
            "total_isolates_received": forms.NumberInput(attrs={"class": "form-control", "min": 0}),
            "isolates_rejected": forms.NumberInput(attrs={"class": "form-control", "min": 0}),
            "isolates_accepted": forms.NumberInput(attrs={"class": "form-control", "min": 0}),
            "date_accepted": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "total_isolates_processed": forms.NumberInput(attrs={"class": "form-control", "min": 0}),
        }


class LaboratoryAccessionForm(forms.ModelForm):
    class Meta:
        model = LaboratoryAccession
        fields = ("date_referred", "start_number", "end_number", "batch_number", "total_batches")
        widgets = {
            "date_referred": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "start_number": forms.NumberInput(attrs={"class": "form-control", "min": 1, "max": 9999}),
            "end_number": forms.NumberInput(attrs={"class": "form-control", "min": 1, "max": 9999}),
            "batch_number": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
            "total_batches": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
        }


class LaboratoryOrganismCountForm(forms.ModelForm):
    class Meta:
        model = LaboratoryOrganismCount
        fields = ("organism_code", "count")
        widgets = {
            "organism_code": forms.Select(attrs={"class": "form-control"}),
            "count": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
        }


class LaboratoryRegisterFilterForm(forms.Form):
    month = forms.IntegerField(min_value=1, max_value=12, widget=forms.NumberInput(attrs={"class": "form-control"}))
    year = forms.IntegerField(min_value=2000, max_value=2100, widget=forms.NumberInput(attrs={"class": "form-control"}))
