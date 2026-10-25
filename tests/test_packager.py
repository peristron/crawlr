import hashlib
import io
import json
import unittest
import zipfile

from bs4 import BeautifulSoup

from scraper.packager import build_zip
from scraper.storage import CrawlResult, Resource


class PackagerTests(unittest.TestCase):
    def test_archive_rewrites_links_and_manifest_hashes_match(self):
        result = CrawlResult(seed_url="https://example.com/course/start.html")
        result.resources = {
            "https://example.com/course/start.html": Resource(
                url="https://example.com/course/start.html",
                requested_url="https://example.com/course/start.html",
                kind="page",
                content=(
                    b'<html><head><link rel="stylesheet" href="/css/site.css"></head>'
                    b'<body><img src="/img/logo.png"><a href="next.html">Next</a></body></html>'
                ),
                content_type="text/html",
                status_code=200,
                depth=0,
            ),
            "https://example.com/course/next.html": Resource(
                url="https://example.com/course/next.html",
                requested_url="https://example.com/course/next.html",
                kind="page",
                content=b"<html><title>Next</title><body>Lesson</body></html>",
                content_type="text/html",
                status_code=200,
                depth=1,
            ),
            "https://example.com/css/site.css": Resource(
                url="https://example.com/css/site.css",
                requested_url="https://example.com/css/site.css",
                kind="asset",
                content=b"body { background: url('../img/logo.png'); }",
                content_type="text/css",
                status_code=200,
                depth=0,
            ),
            "https://example.com/img/logo.png": Resource(
                url="https://example.com/img/logo.png",
                requested_url="https://example.com/img/logo.png",
                kind="asset",
                content=b"PNG",
                content_type="image/png",
                status_code=200,
                depth=0,
            ),
        }
        result.aliases = {url: url for url in result.resources}

        archive_bytes = build_zip(result)
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            self.assertIsNone(archive.testzip())
            names = set(archive.namelist())
            page_name = "example-com/pages/course/start.html"
            self.assertIn(page_name, names)
            page = archive.read(page_name).decode()
            soup = BeautifulSoup(page, "lxml")
            self.assertEqual(soup.img["src"], "../../assets/img/logo.png")
            self.assertEqual(soup.a["href"], "next.html")
            self.assertNotIn("https://example.com", page)

            css = archive.read("example-com/assets/css/site.css").decode()
            self.assertIn('../img/logo.png', css)

            manifest = json.loads(archive.read("example-com/manifest.json"))
            self.assertEqual(manifest["resource_count"], 4)
            for entry in manifest["resources"]:
                payload = archive.read(f"example-com/{entry['zip_path']}")
                self.assertEqual(entry["sha256"], hashlib.sha256(payload).hexdigest())


if __name__ == "__main__":
    unittest.main()
