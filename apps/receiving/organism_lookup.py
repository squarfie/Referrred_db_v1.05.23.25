"""Compatibility imports; laboratory behavior lives in apps.laboratory.organism_lookup."""

from apps.laboratory.organism_lookup import (
    resolve_organism,
    find_organisms,
)

__all__ = ['resolve_organism', 'find_organisms']
