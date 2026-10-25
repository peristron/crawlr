import unittest

from scraper.normalize import canonicalize, classify_url


class NormalizeTests(unittest.TestCase):
    def test_relative_fragment_tracking_and_index_are_normalized(self):
        self.assertEqual(
            canonicalize(
                "https://Example.com/basic/page.html",
                "../index.html?utm_source=x&lesson=1#section",
            ),
            "https://example.com/?lesson=1",
        )

    def test_path_case_is_preserved(self):
        self.assertEqual(
            canonicalize("https://example.com", "/Basic/LP1.HTML"),
            "https://example.com/Basic/LP1.HTML",
        )

    def test_non_http_schemes_are_rejected(self):
        for value in ("mailto:x@example.com", "javascript:alert(1)", "data:text/plain,x"):
            self.assertIsNone(canonicalize("https://example.com", value))

    def test_credentials_are_rejected_during_normalization(self):
        self.assertIsNone(canonicalize("https://example.com", "https://user:secret@example.com/"))

    def test_filename_ending_in_index_is_not_treated_as_directory_index(self):
        self.assertEqual(
            canonicalize("https://example.com", "/course/myindex.html"),
            "https://example.com/course/myindex.html",
        )

    def test_classification(self):
        self.assertEqual(classify_url("https://example.com/file.pdf"), "document")
        self.assertEqual(classify_url("https://example.com/site.css?v=1"), "asset")
        self.assertEqual(classify_url("https://example.com/lesson"), "page")


if __name__ == "__main__":
    unittest.main()
