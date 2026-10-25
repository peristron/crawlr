from __future__ import annotations

import hashlib
import io
import json
import posixpath
import re
import zipfile
from datetime import datetime, timezone
from urllib.parse import unquote, urljoin, urlsplit

from bs4 import BeautifulSoup

from .normalize import canonicalize
from .parser import CSS_URL_RE, decode_html, extract_text
from .storage import CrawlResult, Resource, safe_host


def _safe_segment(value: str) -> str:
    value = unquote(value).replace("\\", "-").strip()
    if value in {"", ".", ".."}:
        return "_"
    return re.sub(r"[^a-zA-Z0-9._@+() -]+", "_", value)


def _path_for(resource: Resource, seed_host: str) -> str:
    parsed = urlsplit(resource.url)
    parts = [_safe_segment(part) for part in parsed.path.split("/") if part]
    if parsed.hostname and parsed.hostname.lower() != seed_host:
        parts.insert(0, safe_host(parsed.netloc))

    if resource.kind == "page":
        folder = "pages"
        if not parts or parsed.path.endswith("/"):
            parts.append("index.html")
        elif "." not in parts[-1]:
            parts[-1] += ".html"
    elif resource.kind == "document":
        folder = "documents"
        if not parts or parsed.path.endswith("/"):
            parts.append("download")
    else:
        folder = "assets"
        if not parts or parsed.path.endswith("/"):
            parts.append("asset.bin")

    path = posixpath.join(folder, *parts)
    if parsed.query:
        stem, extension = posixpath.splitext(path)
        path = f"{stem}_{hashlib.sha256(parsed.query.encode()).hexdigest()[:8]}{extension}"
    return path


def _build_path_map(result: CrawlResult) -> dict[str, str]:
    seed_host = (urlsplit(result.seed_url).hostname or "").lower()
    path_map: dict[str, str] = {}
    used: dict[str, str] = {}
    for url, resource in result.resources.items():
        candidate = _path_for(resource, seed_host)
        if candidate in used and used[candidate] != url:
            stem, extension = posixpath.splitext(candidate)
            candidate = f"{stem}_{hashlib.sha256(url.encode()).hexdigest()[:8]}{extension}"
        used[candidate] = url
        path_map[url] = candidate
    for alias, final in result.aliases.items():
        if final in path_map:
            path_map[alias] = path_map[final]
    return path_map


def _relative_target(page_path: str, target_path: str) -> str:
    return posixpath.relpath(target_path, posixpath.dirname(page_path) or ".")


def _rewrite_html(html: str, page_url: str, page_path: str, path_map: dict[str, str]) -> str:
    soup = BeautifulSoup(html, "lxml")

    def replacement(raw: str | None) -> str | None:
        target = canonicalize(page_url, raw)
        if target and target in path_map:
            rewritten = _relative_target(page_path, path_map[target])
            fragment = urlsplit(urljoin(page_url, raw or "")).fragment
            return f"{rewritten}#{fragment}" if fragment else rewritten
        return None

    attributes = {
        "href": ["a", "link"],
        "src": ["img", "script", "iframe", "audio", "video", "source"],
        "poster": ["video"],
        "data": ["object"],
    }
    for attribute, tags in attributes.items():
        for tag in soup.find_all(tags):
            raw = tag.get(attribute)
            rewritten = replacement(raw)
            if rewritten:
                tag[attribute] = rewritten
    for tag in soup.find_all(["img", "source"], srcset=True):
        candidates = []
        for candidate in tag.get("srcset", "").split(","):
            fields = candidate.strip().split()
            if fields:
                fields[0] = replacement(fields[0]) or fields[0]
                candidates.append(" ".join(fields))
        tag["srcset"] = ", ".join(candidates)
    for tag in soup.find_all(style=True):
        tag["style"] = _rewrite_css(tag.get("style", ""), page_url, page_path, path_map)
    for style in soup.find_all("style"):
        if style.string:
            style.string.replace_with(_rewrite_css(str(style.string), page_url, page_path, path_map))
    return str(soup)


def _rewrite_css(css: str, css_url: str, css_path: str, path_map: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        raw = match.group(2).strip()
        target = canonicalize(css_url, raw)
        if target and target in path_map:
            return f'url("{_relative_target(css_path, path_map[target])}")'
        return match.group(0)

    return CSS_URL_RE.sub(replace, css)


def build_zip(result: CrawlResult) -> bytes:
    """Create an offline-browsable ZIP and manifests entirely in memory."""
    path_map = _build_path_map(result)
    root = safe_host(urlsplit(result.seed_url).hostname or "website").replace(".", "-")
    packaged: dict[str, bytes] = {}
    full_text: list[dict[str, str]] = []

    for url, resource in result.resources.items():
        archive_path = path_map[url]
        content = resource.content
        if resource.kind == "page":
            html = decode_html(content)
            rewritten = _rewrite_html(html, url, archive_path, path_map)
            content = rewritten.encode("utf-8")
            title, text = extract_text(rewritten)
            full_text.append({"url": url, "title": title, "text": text})
        elif resource.content_type == "text/css":
            css = decode_html(content)
            content = _rewrite_css(css, url, archive_path, path_map).encode("utf-8")
        packaged[url] = content

    manifest_resources = []
    for url, resource in result.resources.items():
        content = packaged[url]
        manifest_resources.append(
            {
                "url": url,
                "requested_url": resource.requested_url,
                "kind": resource.kind,
                "status": resource.status_code,
                "content_type": resource.content_type,
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "zip_path": path_map[url],
                "depth": resource.depth,
                "fetched_at": resource.fetched_at,
            }
        )
    manifest = {
        "seed_url": result.seed_url,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "started_at": result.started_at,
        "completed_at": result.completed_at,
        "stopped_reason": result.stopped_reason,
        "resource_count": len(manifest_resources),
        "resources": manifest_resources,
        "aliases": result.aliases,
    }

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for url, resource in result.resources.items():
            archive.writestr(f"{root}/{path_map[url]}", packaged[url])
        archive.writestr(f"{root}/manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        archive.writestr(
            f"{root}/full_text.jsonl",
            "\n".join(json.dumps(item, ensure_ascii=False) for item in full_text),
        )
        archive.writestr(
            f"{root}/external_links.json",
            json.dumps(sorted(result.external_links), indent=2, ensure_ascii=False),
        )
        archive.writestr(f"{root}/errors.json", json.dumps(result.errors, indent=2, ensure_ascii=False))
        archive.writestr(f"{root}/skipped.json", json.dumps(result.skipped, indent=2, ensure_ascii=False))
    return buffer.getvalue()
