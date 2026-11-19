from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from apps.home.models import Batch_Table, Referred_Data, TATform
from apps.home.services.batch_creation import (
    batch_accession_summary as _batch_accession_summary,
    parse_batch_ref_range as _parse_batch_ref_range,
    sync_batch_membership as _sync_batch_membership,
)
from apps.home_final.models import Final_Data


class Command(BaseCommand):
    help = "Find and optionally repair Batch_Table rows with no linked raw/final records."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually create/link raw Referred_Data rows for orphan batches.",
        )
        parser.add_argument(
            "--batch-code",
            dest="batch_code",
            help="Limit the audit/repair to one batch code.",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        batch_code = options.get("batch_code")

        batches = Batch_Table.objects.all().order_by("bat_Referral_Date", "bat_Batch_Code")
        if batch_code:
            batches = batches.filter(bat_Batch_Code=batch_code)

        found = 0
        repaired = 0
        skipped = 0

        for batch in batches:
            raw_count = Referred_Data.objects.filter(
                Q(Batch_id=batch) | Q(Batch_Code=batch.bat_Batch_Code)
            ).count()
            final_count = Final_Data.objects.filter(
                Q(f_Batch_id=batch) | Q(f_Batch_Code=batch.bat_Batch_Code)
            ).count()

            if raw_count or final_count:
                continue

            found += 1

            try:
                accessions = self._accessions_for_batch(batch)
            except ValueError as exc:
                skipped += 1
                self.stdout.write(self.style.WARNING(
                    f"SKIP {batch.bat_Batch_Code}: {exc}"
                ))
                continue

            conflicts = list(
                Referred_Data.objects
                .filter(AccessionNo__in=accessions)
                .exclude(Q(Batch_id__isnull=True) & Q(Batch_Code=""))
                .values_list("AccessionNo", "Batch_Code", "Batch_id__bat_Batch_Code")[:10]
            )
            if conflicts:
                skipped += 1
                details = ", ".join(
                    f"{acc} ({batch_code or linked_code or 'existing batch'})"
                    for acc, batch_code, linked_code in conflicts
                )
                self.stdout.write(self.style.WARNING(
                    f"SKIP {batch.bat_Batch_Code}: accession conflict(s): {details}"
                ))
                continue

            self.stdout.write(
                f"ORPHAN {batch.bat_Batch_Code}: would create/link {len(accessions)} raw accession(s) "
                f"({_batch_accession_summary(accessions)})"
            )

            if not apply_changes:
                continue

            with transaction.atomic():
                synced_count, _ = _sync_batch_membership(batch, accessions)
                tat, _ = TATform.objects.get_or_create(tat_Batch_Isolates=batch)
                tat.tat_SiteCode = batch.bat_SiteCode
                tat.tat_Batch_Code = batch.bat_Batch_Code
                tat.tat_Referral_Date = batch.bat_Referral_Date
                tat.tat_Num_Isolate = synced_count
                tat.tat_BatchNumber = batch.bat_BatchNo
                tat.tat_Total_Batch = batch.bat_Total_batch
                tat.save(update_fields=[
                    "tat_SiteCode",
                    "tat_Batch_Code",
                    "tat_Referral_Date",
                    "tat_Num_Isolate",
                    "tat_BatchNumber",
                    "tat_Total_Batch",
                ])

            repaired += 1
            self.stdout.write(self.style.SUCCESS(
                f"REPAIRED {batch.bat_Batch_Code}: {synced_count} raw accession(s)"
            ))

        mode = "applied" if apply_changes else "dry-run"
        self.stdout.write(self.style.SUCCESS(
            f"Done ({mode}). Orphans found: {found}. Repaired: {repaired}. Skipped: {skipped}."
        ))

    def _accessions_for_batch(self, batch):
        if not batch.bat_Referral_Date:
            raise ValueError("missing referral date")

        site_code = (batch.bat_SiteCode or "").strip()
        if not site_code:
            raise ValueError("missing site code")

        ref_no = (batch.bat_RefNo or "").strip() or self._ref_no_from_batch_code(batch.bat_Batch_Code)
        start_ref, end_ref, width = _parse_batch_ref_range(ref_no)
        year_short = batch.bat_Referral_Date.strftime("%y")
        return [
            f"{year_short}ARS_{site_code}{str(ref).zfill(width)}"
            for ref in range(start_ref, end_ref + 1)
        ]

    def _ref_no_from_batch_code(self, batch_code):
        batch_code = (batch_code or "").strip()
        if "_" not in batch_code:
            return ""
        return batch_code.rsplit("_", 1)[-1].strip()
