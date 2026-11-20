"""Compatibility imports; laboratory behavior lives in apps.laboratory.sticker_pdf."""

from apps.laboratory.sticker_pdf import (
    render_sticker_pdf,
    COLUMNS,
    ROWS,
    LABELS_PER_PAGE,
)

__all__ = ['render_sticker_pdf', 'COLUMNS', 'ROWS', 'LABELS_PER_PAGE']
