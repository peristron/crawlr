from __future__ import annotations

import asyncio
import io
import zipfile
from urllib.parse import urlsplit

import streamlit as st

from scraper import CrawlConfig, Crawler, build_zip
from scraper.normalize import canonicalize
from scraper.parser import decode_html, extract_text
from scraper.security import UnsafeUrlError
from scraper.storage import CrawlResult, safe_host

st.set_page_config(page_title="Website Archiver", layout="wide")

st.title("Website Archiver")
st.caption("Create a bounded, offline-browsable archive of a public website.")

for key, default in {
    "crawl_result": None,
    "zip_bytes": None,
    "stop_flag": False,
    "crawl_log": [],
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

with st.sidebar:
    st.header("Crawl settings")
    start_url = st.text_input("Start URL", value="https://fiberu.org/basic/LP1.html")
    contact = st.text_input(
        "Operator contact",
        placeholder="you@example.com or https://example.com/contact",
        help="Included in the crawler User-Agent so site operators can identify the source.",
    )
    max_pages = st.slider("Maximum HTML pages", 1, 500, 25)
    max_depth = st.slider("Maximum link depth", 0, 8, 5)
    same_domain_only = st.checkbox("Stay on the exact host", value=True)
    include_assets = st.checkbox("Download page assets and documents", value=True)
    min_delay = st.slider("Minimum delay per host (seconds)", 0.5, 2.0, 0.75, 0.25)
    with st.expander("Advanced limits"):
        concurrency = st.slider("Maximum concurrent requests", 1, 10, 5)
        time_budget = st.slider("Time limit (seconds)", 30, 300, 240, 30)
        verify_tls = st.checkbox("Verify TLS certificates", value=True)
        if not verify_tls:
            st.warning("TLS verification is disabled. Use this only for a host you trust.")

    start_clicked = st.button("Start crawl", type="primary", use_container_width=True)
    if st.button("Stop crawl", use_container_width=True):
        st.session_state.stop_flag = True

st.info(
    "Archive only public content you are allowed to download. Copyright and attribution remain with "
    "the original owner; redistribution may require permission. Authentication walls are not bypassed."
)


def validate_inputs(url: str, operator_contact: str) -> str | None:
    normalized = canonicalize(url, url)
    if not normalized or not urlsplit(normalized).hostname:
        return "Enter a complete public URL beginning with http:// or https://."
    if not operator_contact.strip():
        return "Enter an operator email address or contact URL for the crawler User-Agent."
    if len(operator_contact) > 200 or any(character in operator_contact for character in "\r\n"):
        return "The operator contact must be one line and no more than 200 characters."
    return None


def run_crawl() -> None:
    error = validate_inputs(start_url, contact)
    if error:
        st.error(error)
        return

    st.session_state.stop_flag = False
    st.session_state.crawl_active = True
    st.session_state.crawl_result = None
    st.session_state.zip_bytes = None
    st.session_state.crawl_log = []

    config = CrawlConfig(
        seed_url=start_url,
        contact=contact,
        max_pages=max_pages,
        max_depth=max_depth,
        same_domain_only=same_domain_only,
        include_assets=include_assets,
        min_delay=min_delay,
        concurrency=concurrency,
        time_budget=float(time_budget),
        verify_tls=verify_tls,
    )
    progress = st.progress(0.0, text="Preparing crawl...")
    log_box = st.empty()

    with st.status("Crawling website...", expanded=True) as status:
        def update_progress(event: dict[str, object]) -> None:
            pages = int(event["pages"])
            current_url = str(event["url"])
            st.session_state.crawl_log = (st.session_state.crawl_log + [current_url])[-10:]
            progress.progress(
                min(pages / max_pages, 1.0),
                text=f"{pages} of {max_pages} pages; {event['queued']} queued",
            )
            log_box.code("\n".join(st.session_state.crawl_log), language=None)

        try:
            crawler = Crawler(config)
            result = asyncio.run(
                crawler.run(
                    progress_cb=update_progress,
                    stop_cb=lambda: bool(st.session_state.stop_flag),
                )
            )
            status.update(label="Building archive...", state="running")
            archive = build_zip(result)
            _verify_archive(archive)
            st.session_state.crawl_result = result
            st.session_state.zip_bytes = archive
            label = result.stopped_reason or "Crawl and archive complete."
            status.update(label=label, state="complete", expanded=False)
            progress.progress(1.0, text="Archive ready")
        except (ValueError, UnsafeUrlError) as exc:
            status.update(label="The crawl could not start.", state="error")
            st.error(str(exc))
        except Exception as exc:
            status.update(label="The crawl stopped unexpectedly.", state="error")
            st.error(f"The archive could not be completed: {type(exc).__name__}: {exc}")
        finally:
            st.session_state.crawl_active = False


def _verify_archive(archive: bytes) -> None:
    with zipfile.ZipFile(io.BytesIO(archive)) as candidate:
        bad_file = candidate.testzip()
        if bad_file:
            raise RuntimeError(f"ZIP integrity check failed for {bad_file}")


if start_clicked:
    run_crawl()


def show_results(result: CrawlResult, archive: bytes) -> None:
    st.subheader("Archive summary")
    columns = st.columns(5)
    columns[0].metric("Pages", len(result.pages))
    columns[1].metric("Assets", len(result.assets))
    columns[2].metric("Documents", len(result.documents))
    columns[3].metric("Downloaded", f"{result.total_bytes / (1024 * 1024):.1f} MB")
    columns[4].metric("Errors", len(result.errors))

    if result.stopped_reason:
        st.warning(result.stopped_reason)
    if result.errors:
        with st.expander(f"Errors and warnings ({len(result.errors)})"):
            st.dataframe(result.errors, use_container_width=True, hide_index=True)
    if result.skipped:
        with st.expander(f"Skipped URLs ({len(result.skipped)})"):
            st.dataframe(result.skipped, use_container_width=True, hide_index=True)

    host = safe_host(urlsplit(result.seed_url).hostname or "website").replace(".", "-")
    st.download_button(
        "Download site ZIP",
        data=archive,
        file_name=f"{host}.zip",
        mime="application/zip",
        type="primary",
    )

    with st.expander("Archive contents"):
        for label, resources in (
            ("Pages", result.pages),
            ("Assets", result.assets),
            ("Documents", result.documents),
        ):
            st.markdown(f"**{label} ({len(resources)})**")
            if resources:
                st.code("\n".join(sorted(resources)), language=None)

    if result.pages:
        st.subheader("Page text preview")
        selected = st.selectbox("Crawled page", sorted(result.pages))
        page = result.pages[selected]
        title, text = extract_text(decode_html(page.content))
        st.markdown(f"**{title}**")
        st.text_area("Extracted text", value=text, height=320, disabled=True)


if st.session_state.crawl_result is not None and st.session_state.zip_bytes is not None:
    show_results(st.session_state.crawl_result, st.session_state.zip_bytes)
