# Website Archiver

A safety-bounded Streamlit crawler that creates an offline-browsable ZIP of public website pages,
assets, documents, extracted text, external links, and crawl metadata.

## Run locally

Python 3.11 or 3.12 is recommended.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
streamlit run app.py
```

Enter a public start URL and an operator contact email or URL. The contact is sent in the crawler's
User-Agent. It is not saved in the downloaded archive.

## Safety and crawl limits

- Reads and honors `robots.txt`, including `Crawl-delay` when present.
- Rejects localhost, internal hostnames, credentials in URLs, and non-public IP addresses.
- Revalidates every redirect destination before connecting.
- Uses a minimum 0.5-second delay per host and at most 10 concurrent requests.
- Stops at configured page, depth, time, per-resource, and total-byte limits.
- Does not bypass authentication or download external videos.
- Keeps copyright and attribution in archived pages.

## ZIP layout

The archive contains `pages/`, `assets/`, `documents/`, `manifest.json`, `full_text.jsonl`,
`external_links.json`, `errors.json`, and `skipped.json` under a host-named root directory.
Same-site links and downloaded asset references are rewritten to relative ZIP paths.

## Streamlit Community Cloud

Deploy `app.py` as the main file. No secrets or system packages are required. The crawl result and
ZIP are kept in `st.session_state`, so widget reruns and downloads do not repeat the crawl. Cloud
containers are ephemeral; download the archive before the session expires.

## Tests

```bash
python -m unittest discover -s tests -v
```
