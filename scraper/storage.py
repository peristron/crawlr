from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit


@dataclass
class Resource:
    url: str
    requested_url: str
    kind: str
    content: bytes
    content_type: str
    status_code: int
    depth: int
    fetched_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


@dataclass
class CrawlResult:
    seed_url: str
    resources: dict[str, Resource] = field(default_factory=dict)
    external_links: set[str] = field(default_factory=set)
    errors: list[dict[str, object]] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)
    stopped_reason: str | None = None
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: str | None = None

    @property
    def pages(self) -> dict[str, Resource]:
        return {url: item for url, item in self.resources.items() if item.kind == "page"}

    @property
    def assets(self) -> dict[str, Resource]:
        return {url: item for url, item in self.resources.items() if item.kind == "asset"}

    @property
    def documents(self) -> dict[str, Resource]:
        return {url: item for url, item in self.resources.items() if item.kind == "document"}

    @property
    def total_bytes(self) -> int:
        return sum(len(item.content) for item in self.resources.values())


def safe_host(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9.-]+", "-", value).strip(".-") or "site"
