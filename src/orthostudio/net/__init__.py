# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Network client: many small HTTP downloads with adaptive concurrency, hedging and retries.

Spec: ``docs/specs/net-download.md``. Provider knowledge (placeholders, parent fallback, URL
templates) lives in ``orthostudio.imagery``; this package only moves bytes.
"""

from orthostudio.net.fetch import (
    USER_AGENT,
    Fetcher,
    FetchRequest,
    FetchResult,
    FetchStats,
    fetch_all,
)

__all__ = ["USER_AGENT", "FetchRequest", "FetchResult", "FetchStats", "Fetcher", "fetch_all"]
