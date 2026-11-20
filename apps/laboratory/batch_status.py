"""Read-only batch existence checks shared by the laboratory task queue."""
from django.db.models import CharField, Exists, OuterRef, Value
from django.db.models.functions import Cast, Concat, LPad

from apps.home.models import Batch_Table
from .models import LaboratoryAccession


def pending_batch_accessions():
    # Both the DMU and laboratory creation service store these canonical fields.
    # A missing laboratory OneToOne link alone does not mean the batch is absent.
    reference_range = Concat(
        LPad(Cast(OuterRef("start_number"), CharField()), 4, Value("0")),
        Value("-"),
        LPad(Cast(OuterRef("end_number"), CharField()), 4, Value("0")),
    )
    existing = Batch_Table.objects.filter(
        bat_SiteCode=OuterRef("laboratory_receiving__receipt__site__SiteCode"),
        bat_Referral_Date=OuterRef("date_referred"),
        bat_RefNo=reference_range,
        bat_BatchNo=Cast(OuterRef("batch_number"), CharField()),
        bat_Total_batch=Cast(OuterRef("total_batches"), CharField()),
    )
    return LaboratoryAccession.objects.filter(batch__isnull=True).alias(
        matching_batch_exists=Exists(existing),
    ).filter(matching_batch_exists=False)
