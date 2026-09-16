"""Network client: many small HTTP downloads with adaptive concurrency, hedging and retries.

Spec: ``docs/specs/net-download.md``. Provider knowledge (placeholders, parent fallback, URL
templates) lives in ``orthostudio.imagery``; this package only moves bytes.
"""

from orthostudio.net.fetch import (
    Fetcher,
    FetchRequest,
    FetchResult,
    FetchStats,
    fetch_all,
)

__all__ = ["FetchRequest", "FetchResult", "FetchStats", "Fetcher", "fetch_all"]
