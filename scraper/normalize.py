from __future__ import annotations

import posixpath
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

TRACKING_PREFIXES = ("utm_", "fbclid", "gclid", "mc_")
HTML_EXTENSIONS = (".html", ".htm", ".shtml", ".xhtml", ".php", ".asp", ".aspx")
DOCUMENT_EXTENSIONS = (
    ".pdf",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
    ".txt",
    ".rtf",
    ".csv",
    ".zip",
)
ASSET_EXTENSIONS = (
    ".avif",
    ".bmp",
    ".css",
    ".eot",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".m4a",
    ".map",
    ".mp3",
    ".mp4",
    ".ogg",
    ".otf",
    ".png",
    ".svg",
    ".ttf",
    ".wav",
    ".webm",
    ".webp",
    ".woff",
    ".woff2",
)


def canonicalize(base_url: str, href: str | None) -> str | None:
    """Resolve and normalize an HTTP(S) reference without changing path case."""
    href = (href or "").strip()
    if not href or href.startswith("#"):
        return None
    absolute = urljoin(base_url, href)
    parsed = urlsplit(absolute)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None

    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower()
    try:
        port = parsed.port
    except ValueError:
        return None
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    host_for_netloc = f"[{host}]" if ":" in host else host
    netloc = host_for_netloc if port is None or default_port else f"{host_for_netloc}:{port}"

    path = parsed.path or "/"
    lower_path = path.lower()
    for suffix in ("/index.html", "/index.htm"):
        if lower_path.endswith(suffix):
            path = path[: -len(suffix) + 1] or "/"
            break

    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not any(key.lower().startswith(prefix) for prefix in TRACKING_PREFIXES)
    ]
    return urlunsplit((scheme, netloc, path, urlencode(query, doseq=True), ""))


def classify_url(url: str) -> str:
    """Classify a URL by its path extension before it is fetched."""
    extension = posixpath.splitext(urlsplit(url).path.lower())[1]
    if extension in DOCUMENT_EXTENSIONS:
        return "document"
    if extension in ASSET_EXTENSIONS:
        return "asset"
    return "page"


def same_host(first: str, second: str) -> bool:
    return urlsplit(first).netloc.lower() == urlsplit(second).netloc.lower()
