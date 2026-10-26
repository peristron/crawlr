from __future__ import annotations

import asyncio
import email.utils
import time
import urllib.robotparser
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urljoin, urlsplit

import httpx

from .http_content import DecodedContentTooLargeError, decode_http_content
from .normalize import canonicalize, classify_url, same_host
from .parser import decode_html, extract_css_urls, extract_links
from .security import UnsafeUrlError, validate_public_url
from .storage import CrawlResult, Resource

ProgressCallback = Callable[[dict[str, object]], None]
StopCallback = Callable[[], bool]


class ResourceTooLargeError(RuntimeError):
    pass


class OutOfScopeRedirectError(RuntimeError):
    def __init__(self, url: str):
        self.url = url
        super().__init__(f"Redirect leaves the configured host: {url}")


class RobotsDisallowedError(RuntimeError):
    pass


@dataclass(frozen=True)
class CrawlConfig:
    seed_url: str
    contact: str
    max_pages: int = 200
    max_depth: int = 5
    same_domain_only: bool = True
    include_assets: bool = True
    min_delay: float = 0.5
    concurrency: int = 5
    timeout: float = 20.0
    time_budget: float = 240.0
    max_resource_bytes: int = 25 * 1024 * 1024
    max_total_bytes: int = 200 * 1024 * 1024
    verify_tls: bool = True

    @property
    def user_agent(self) -> str:
        return f"WebsiteArchiver/1.0 (+{self.contact.strip()})"


@dataclass
class _Fetched:
    requested_url: str
    final_url: str
    status_code: int
    headers: httpx.Headers
    content: bytes


@dataclass
class _RobotsPolicy:
    parser: urllib.robotparser.RobotFileParser
    delay: float | None = None


class Crawler:
    def __init__(self, config: CrawlConfig):
        seed = canonicalize(config.seed_url, config.seed_url)
        if not seed:
            raise ValueError("The start URL must be a valid http:// or https:// URL.")
        if not config.contact.strip():
            raise ValueError("A contact email or URL is required for the crawler User-Agent.")
        if len(config.contact) > 200 or any(character in config.contact for character in "\r\n"):
            raise ValueError("The operator contact must be one line and no more than 200 characters.")
        if not 1 <= config.max_pages <= 500:
            raise ValueError("max_pages must be between 1 and 500.")
        if config.min_delay < 0.5:
            raise ValueError("min_delay cannot be less than 0.5 seconds.")
        self.config = config
        self.seed = seed
        self.result = CrawlResult(seed_url=seed)
        self._started = 0.0
        self._robots: dict[str, _RobotsPolicy] = {}
        self._robots_locks: dict[str, asyncio.Lock] = {}
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._last_request: dict[str, float] = {}

    async def run(
        self,
        progress_cb: ProgressCallback | None = None,
        stop_cb: StopCallback | None = None,
    ) -> CrawlResult:
        await validate_public_url(self.seed)
        self._started = time.monotonic()
        page_queue: deque[tuple[str, int, str]] = deque()
        resource_queue: deque[tuple[str, int, str]] = deque()
        seed_item = (self.seed, 0, classify_url(self.seed))
        if seed_item[2] == "page":
            page_queue.append(seed_item)
        else:
            resource_queue.append(seed_item)
        seen = {self.seed}
        transport = httpx.AsyncHTTPTransport(retries=0, verify=self.config.verify_tls)
        headers = {
            "User-Agent": self.config.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en",
            "Accept-Encoding": "identity",
        }

        async with httpx.AsyncClient(
            headers=headers,
            timeout=self.config.timeout,
            follow_redirects=False,
            http2=True,
            transport=transport,
        ) as client:
            while page_queue or resource_queue:
                if stop_cb and stop_cb():
                    self.result.stopped_reason = "Stopped by the user."
                    break
                if self._budget_expired():
                    self.result.stopped_reason = "The crawl time limit was reached."
                    break
                if len(self.result.pages) >= self.config.max_pages:
                    self.result.stopped_reason = "The maximum page count was reached."
                    if page_queue:
                        page_queue.clear()
                    if not resource_queue:
                        break

                batch: list[tuple[str, int, str]] = []
                batch_limit = min(self.config.concurrency, 10)
                while resource_queue and len(batch) < batch_limit:
                    batch.append(resource_queue.popleft())
                page_capacity = self.config.max_pages - len(self.result.pages)
                while page_queue and len(batch) < batch_limit and page_capacity > 0:
                    batch.append(page_queue.popleft())
                    page_capacity -= 1

                fetched_batch = await asyncio.gather(
                    *(self._fetch_resource(client, *item) for item in batch),
                    return_exceptions=True,
                )
                for (requested_url, depth, requested_kind), outcome in zip(batch, fetched_batch):
                    if isinstance(outcome, Exception):
                        self._record_exception(requested_url, outcome)
                        continue
                    if outcome is None:
                        continue
                    resource, discovered = outcome
                    final_url = resource.url
                    self.result.aliases[requested_url] = final_url
                    if final_url not in self.result.resources:
                        if self.result.total_bytes + len(resource.content) > self.config.max_total_bytes:
                            self.result.stopped_reason = "The 200 MB crawl byte limit was reached."
                            page_queue.clear()
                            resource_queue.clear()
                            break
                        self.result.resources[final_url] = resource

                    for link, kind in discovered:
                        if link in seen or link in self.result.resources:
                            continue
                        if self.config.same_domain_only and not same_host(self.seed, link):
                            self.result.external_links.add(link)
                            continue
                        if kind in {"asset", "document"} and not self.config.include_assets:
                            continue
                        if kind == "page" and depth + 1 > self.config.max_depth:
                            self.result.skipped.append({"url": link, "reason": "Maximum depth exceeded"})
                            continue
                        seen.add(link)
                        item = (link, depth + 1 if kind == "page" else depth, kind)
                        if kind == "page":
                            page_queue.append(item)
                        else:
                            resource_queue.append(item)

                    if progress_cb:
                        progress_cb(
                            {
                                "url": final_url,
                                "pages": len(self.result.pages),
                                "assets": len(self.result.assets),
                                "documents": len(self.result.documents),
                                "bytes": self.result.total_bytes,
                                "queued": len(page_queue) + len(resource_queue),
                            }
                        )

        self.result.completed_at = datetime.now(timezone.utc).isoformat()
        return self.result

    async def _fetch_resource(
        self, client: httpx.AsyncClient, requested_url: str, depth: int, requested_kind: str
    ) -> tuple[Resource, list[tuple[str, str]]] | None:
        policy = await self._robots_for(client, requested_url)
        if not policy.parser.can_fetch("WebsiteArchiver", requested_url):
            self.result.skipped.append({"url": requested_url, "reason": "Disallowed by robots.txt"})
            return None

        fetched = await self._request(client, requested_url, policy.delay)
        content_type = fetched.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type in {"text/html", "application/xhtml+xml"}:
            actual_kind = "page"
        elif content_type == "text/css" or requested_kind == "asset":
            actual_kind = "asset"
        else:
            actual_kind = "document" if requested_kind in {"page", "document"} else "asset"

        resource = Resource(
            url=fetched.final_url,
            requested_url=fetched.requested_url,
            kind=actual_kind,
            content=fetched.content,
            content_type=content_type or "application/octet-stream",
            status_code=fetched.status_code,
            depth=depth,
        )
        discovered: list[tuple[str, str]] = []
        if actual_kind == "page":
            encoding = _encoding_from_content_type(fetched.headers.get("content-type", ""))
            html = decode_html(fetched.content, encoding)
            discovered = [(item.url, item.kind) for item in extract_links(fetched.final_url, html)]
        elif content_type == "text/css" and self.config.include_assets:
            css = decode_html(fetched.content, _encoding_from_content_type(fetched.headers.get("content-type", "")))
            for raw in extract_css_urls(css):
                normalized = canonicalize(fetched.final_url, raw)
                if normalized:
                    discovered.append((normalized, "asset"))
        return resource, discovered

    async def _robots_for(self, client: httpx.AsyncClient, url: str) -> _RobotsPolicy:
        parsed = urlsplit(url)
        origin = f"{parsed.scheme}://{parsed.netloc.lower()}"
        if origin in self._robots:
            return self._robots[origin]
        lock = self._robots_locks.setdefault(origin, asyncio.Lock())
        async with lock:
            if origin in self._robots:
                return self._robots[origin]
            robots_url = f"{origin}/robots.txt"
            parser = urllib.robotparser.RobotFileParser(robots_url)
            try:
                fetched = await self._request(client, robots_url, None, allow_not_found=True, check_scope=False)
                if fetched.status_code == 404:
                    parser.parse([])
                else:
                    parser.parse(decode_html(fetched.content, "utf-8").splitlines())
            except Exception as exc:
                parser.parse([])
                self.result.errors.append(
                    {"url": robots_url, "error": f"robots.txt could not be read; defaulting to allow: {exc}"}
                )
            delay = parser.crawl_delay("WebsiteArchiver") or parser.crawl_delay("*")
            policy = _RobotsPolicy(parser=parser, delay=float(delay) if delay is not None else None)
            self._robots[origin] = policy
            return policy

    async def _request(
        self,
        client: httpx.AsyncClient,
        url: str,
        robots_delay: float | None,
        *,
        allow_not_found: bool = False,
        check_scope: bool = True,
    ) -> _Fetched:
        requested_url = url
        current = url
        current_delay = robots_delay
        for redirect_number in range(11):
            if redirect_number == 10:
                raise httpx.TooManyRedirects("More than 10 redirects", request=httpx.Request("GET", current))
            await validate_public_url(current)
            if check_scope and self.config.same_domain_only and not same_host(self.seed, current):
                self.result.external_links.add(current)
                raise OutOfScopeRedirectError(current)
            response = await self._request_with_retries(client, current, current_delay)
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise httpx.HTTPStatusError(
                        "Redirect response did not include Location",
                        request=response.request,
                        response=response,
                    )
                redirected = canonicalize(current, urljoin(current, location)) or ""
                if not redirected:
                    raise ValueError("Redirect target is not an HTTP(S) URL.")
                if check_scope and _origin(current) != _origin(redirected):
                    redirect_policy = await self._robots_for(client, redirected)
                    if not redirect_policy.parser.can_fetch("WebsiteArchiver", redirected):
                        raise RobotsDisallowedError(
                            f"Redirect target is disallowed by robots.txt: {redirected}"
                        )
                    current_delay = redirect_policy.delay
                current = redirected
                continue
            if response.status_code == 404 and allow_not_found:
                return _Fetched(requested_url, current, 404, response.headers, b"")
            response.raise_for_status()
            return _Fetched(requested_url, current, response.status_code, response.headers, response.content)
        raise RuntimeError("Unreachable redirect state")

    async def _request_with_retries(
        self, client: httpx.AsyncClient, url: str, robots_delay: float | None
    ) -> httpx.Response:
        for attempt in range(3):
            await self._throttle(url, max(self.config.min_delay, robots_delay or 0.0))
            try:
                retry_wait: float | None = None
                async with client.stream("GET", url) as response:
                    if response.status_code == 429 or response.status_code >= 500:
                        if attempt < 2:
                            retry_wait = _retry_delay(response, attempt)
                    if retry_wait is None:
                        content = bytearray()
                        async for chunk in response.aiter_raw():
                            content.extend(chunk)
                            if len(content) > self.config.max_resource_bytes:
                                raise ResourceTooLargeError(
                                    f"Resource exceeds {self.config.max_resource_bytes // (1024 * 1024)} MB"
                                )
                        try:
                            decoded = decode_http_content(
                                bytes(content),
                                response.headers.get("content-encoding", ""),
                                self.config.max_resource_bytes,
                            )
                        except DecodedContentTooLargeError as exc:
                            raise ResourceTooLargeError(str(exc)) from exc
                        decoded_headers = httpx.Headers(response.headers)
                        decoded_headers.pop("content-encoding", None)
                        decoded_headers.pop("content-length", None)
                        return httpx.Response(
                            status_code=response.status_code,
                            headers=decoded_headers,
                            content=decoded,
                            request=response.request,
                        )
                if retry_wait is not None:
                    await asyncio.sleep(retry_wait)
                    continue
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt == 2:
                    raise
                await asyncio.sleep((1, 3, 9)[attempt])
        raise RuntimeError("Retry loop ended unexpectedly")

    async def _throttle(self, url: str, delay: float) -> None:
        host = (urlsplit(url).hostname or "").lower()
        lock = self._host_locks.setdefault(host, asyncio.Lock())
        async with lock:
            wait = delay - (time.monotonic() - self._last_request.get(host, 0.0))
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request[host] = time.monotonic()

    def _budget_expired(self) -> bool:
        return time.monotonic() - self._started >= self.config.time_budget

    def _record_exception(self, url: str, exc: Exception) -> None:
        if isinstance(exc, (OutOfScopeRedirectError, RobotsDisallowedError)):
            self.result.skipped.append({"url": url, "reason": str(exc)})
            return
        self.result.errors.append({"url": url, "error": f"{type(exc).__name__}: {exc}"})


def _encoding_from_content_type(content_type: str) -> str | None:
    for part in content_type.split(";")[1:]:
        key, separator, value = part.strip().partition("=")
        if separator and key.lower() == "charset":
            return value.strip(" \"'")
    return None


def _origin(url: str) -> tuple[str, str]:
    parsed = urlsplit(url)
    return parsed.scheme.lower(), parsed.netloc.lower()


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    retry_after = response.headers.get("retry-after")
    if retry_after:
        try:
            return min(float(retry_after), 30.0)
        except ValueError:
            try:
                parsed = email.utils.parsedate_to_datetime(retry_after)
                now = datetime.now(parsed.tzinfo or timezone.utc)
                return max(0.0, min((parsed - now).total_seconds(), 30.0))
            except (TypeError, ValueError, OverflowError):
                pass
    return (1.0, 3.0, 9.0)[attempt]
