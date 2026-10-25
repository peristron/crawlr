"""Safe, bounded website archiver used by the Streamlit app."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .crawler import CrawlConfig, Crawler

__all__ = ["CrawlConfig", "Crawler", "build_zip"]


def __getattr__(name: str) -> Any:
    """Load optional runtime dependencies only when their public API is requested."""
    if name in {"CrawlConfig", "Crawler"}:
        from .crawler import CrawlConfig, Crawler

        return {"CrawlConfig": CrawlConfig, "Crawler": Crawler}[name]
    if name == "build_zip":
        from .packager import build_zip

        return build_zip
    raise AttributeError(name)
