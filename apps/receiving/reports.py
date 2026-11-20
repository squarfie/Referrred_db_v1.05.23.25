"""Compatibility imports; laboratory behavior lives in apps.laboratory.reports."""

from apps.laboratory.reports import (
    build_register_rows,
    render_register_pdf,
    REGISTER_COLUMNS,
    REGISTER_CODES,
)

__all__ = ['build_register_rows', 'render_register_pdf', 'REGISTER_COLUMNS', 'REGISTER_CODES']
