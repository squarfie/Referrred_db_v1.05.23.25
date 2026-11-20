"""Shared organism lookup for laboratory ID and label entry.

Resolve codes against the existing WHONET organism reference table.
"""
from django.core.exceptions import ValidationError
from django.db.models import Q

from apps.home.models import Organism_List


def resolve_organism(*, code="", name="", require_code=False):
    code = (code or "").strip()
    name = (name or "").strip()
    if code:
        record = Organism_List.objects.filter(Whonet_Org_Code__iexact=code).first()
        if record is None:
            raise ValidationError("Select a valid WHONET organism code from the organism table.")
        if name and name.casefold() != record.Organism.strip().casefold():
            raise ValidationError("The selected WHONET code does not match the organism name.")
        return record.Whonet_Org_Code.upper(), record.Organism.strip()
    if name:
        record = Organism_List.objects.filter(Organism__iexact=name).order_by("Whonet_Org_Code").first()
        if record:
            return record.Whonet_Org_Code.upper(), record.Organism.strip()
        if not require_code:
            # Allow historical instrument free-text organisms and unlisted taxa.
            return "", name
    raise ValidationError("Search for an organism name or select its WHONET code.")


def find_organisms(query, limit=30):
    query = (query or "").strip()
    if not query:
        return []
    rows = (Organism_List.objects
            .filter(Q(Whonet_Org_Code__icontains=query) | Q(Organism__icontains=query))
            .order_by("Organism", "Whonet_Org_Code")[:limit])
    return [{"code": item.Whonet_Org_Code.upper(), "name": item.Organism}
            for item in rows]
