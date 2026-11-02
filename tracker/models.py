"""What a scraper returns. Scrapers only describe listings; the pipeline
downloads documents, detects changes, and stores everything."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field


@dataclass
class DocLink:
    url: str
    label: str | None = None
    # Index into Posting.items when the document belongs to one agenda item.
    item_index: int | None = None


@dataclass
class Item:
    title: str
    number: str | None = None
    description: str | None = None


@dataclass
class Posting:
    # Stable within the agency across runs (e.g. the meeting date or agenda URL).
    external_key: str
    title: str
    kind: str = "meeting"  # meeting | opinion | initiative
    meeting_date: dt.date | None = None
    url: str | None = None
    items: list[Item] = field(default_factory=list)
    documents: list[DocLink] = field(default_factory=list)
    # Listed, but its detail page wasn't re-read this run (an older meeting).
    # A stored posting is left as it is; a new one is stored from the listing.
    partial: bool = False
