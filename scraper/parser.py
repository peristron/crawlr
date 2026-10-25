from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

from .normalize import canonicalize, classify_url

CSS_URL_RE = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE)


@dataclass(frozen=True)
class DiscoveredUrl:
    url: str
    kind: str


def _srcset_urls(value: str) -> list[str]:
    return [candidate.strip().split()[0] for candidate in value.split(",") if candidate.strip()]


def extract_links(page_url: str, html: str) -> list[DiscoveredUrl]:
    soup = BeautifulSoup(html, "lxml")
    found: dict[str, str] = {}

    def add(raw: str | None, forced_kind: str | None = None) -> None:
        normalized = canonicalize(page_url, raw)
        if normalized:
            found[normalized] = forced_kind or classify_url(normalized)

    for tag in soup.find_all("a", href=True):
        add(tag.get("href"))
    for tag in soup.find_all(["img", "script", "iframe", "audio", "video", "source"], src=True):
        add(tag.get("src"), "asset")
    for tag in soup.find_all(["img", "source"], srcset=True):
        for raw in _srcset_urls(tag.get("srcset", "")):
            add(raw, "asset")
    for tag in soup.find_all("video", poster=True):
        add(tag.get("poster"), "asset")
    for tag in soup.find_all("link", href=True):
        rel = {str(value).lower() for value in (tag.get("rel") or [])}
        if rel.intersection({"stylesheet", "icon", "preload", "manifest", "apple-touch-icon"}):
            add(tag.get("href"), "asset")
    for tag in soup.find_all("object", data=True):
        add(tag.get("data"), classify_url(canonicalize(page_url, tag.get("data")) or ""))
    for tag in soup.find_all(style=True):
        for raw in extract_css_urls(tag.get("style", "")):
            add(raw, "asset")
    for style in soup.find_all("style"):
        for raw in extract_css_urls(style.get_text(" ")):
            add(raw, "asset")

    return [DiscoveredUrl(url, kind) for url, kind in found.items()]


def extract_css_urls(css: str) -> list[str]:
    return [match.group(2).strip() for match in CSS_URL_RE.finditer(css) if match.group(2).strip()]


def decode_html(content: bytes, declared_encoding: str | None = None) -> str:
    """Decode HTML while recovering common UTF-8-as-Latin-1 mojibake."""
    candidates = [declared_encoding, "utf-8", "windows-1252", "latin-1"]
    text = ""
    for encoding in dict.fromkeys(value for value in candidates if value):
        try:
            text = content.decode(encoding)
            break
        except (LookupError, UnicodeDecodeError):
            continue
    if not text:
        text = content.decode("utf-8", errors="replace")
    if any(marker in text for marker in ("â€œ", "â€", "Ã©", "Â ")):
        try:
            repaired = text.encode("latin-1").decode("utf-8")
            if repaired.count("�") <= text.count("�"):
                text = repaired
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return text


def extract_text(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.get_text(" ", strip=True) if soup.title else "Untitled"
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript"]):
        tag.decompose()
    return title, soup.get_text("\n", strip=True)
