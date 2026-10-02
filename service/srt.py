"""Backward-compatible imports for code that still references service.srt."""

from service.ktx import (
    KTX,
    KTX_STATIONS,
    build_search_url,
    get_schedule,
)

SRT = KTX

__all__ = [
    "KTX",
    "KTX_STATIONS",
    "SRT",
    "build_search_url",
    "get_schedule",
]
