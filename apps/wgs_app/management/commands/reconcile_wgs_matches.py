"""Reconcile WGS result links with available Final Data accessions."""
from django.core.management.base import BaseCommand

from apps.wgs_app.reconciliation import reconcile_wgs_matches


class Command(BaseCommand):
    help = (
        "Relink orphaned WGS results to uniquely matching Final accessions. "
        "Run after bulk Final Data imports; does not delete any WGS results."
    )

    def add_arguments(self, parser):
        parser.add_argument("--accession", help="Only reconcile this accession")
        parser.add_argument(
            "--custom-only", action="store_true",
            help="Skip built-in sequencing results that need project reconstruction",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Preview proposed updates without changing records",
        )

    def handle(self, *args, **options):
        result = reconcile_wgs_matches(
            accession=options.get("accession"),
            include_builtin=not options["custom_only"],
            dry_run=options["dry_run"],
        )
        prefix = "DRY RUN: " if options["dry_run"] else ""
        self.stdout.write(
            self.style.SUCCESS(prefix + ", ".join(
                f"{name}={count}" for name, count in result.items()
            ))
        )
        if result["ambiguous_or_unmatched"]:
            self.stdout.write(
                self.style.WARNING(
                    "Some WGS rows could not be unambiguously relinked. "
                    "They were preserved unchanged; inspect accession/run identities."
                )
            )
