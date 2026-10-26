import gzip
import unittest
import zlib

from scraper.http_content import DecodedContentTooLargeError, decode_http_content


class HttpContentTests(unittest.TestCase):
    def test_valid_gzip_is_decoded(self):
        payload = b"<html><body>Fiber U</body></html>"
        self.assertEqual(decode_http_content(gzip.compress(payload), "gzip", 1024), payload)

    def test_valid_zlib_deflate_is_decoded(self):
        payload = b"lesson page"
        self.assertEqual(decode_http_content(zlib.compress(payload), "deflate", 1024), payload)

    def test_raw_deflate_is_decoded(self):
        payload = b"lesson page"
        compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
        compressed = compressor.compress(payload) + compressor.flush()
        self.assertEqual(decode_http_content(compressed, "deflate", 1024), payload)

    def test_malformed_encoding_header_preserves_plain_body(self):
        payload = b"<html><body>Already plain</body></html>"
        self.assertEqual(decode_http_content(payload, "gzip", 1024), payload)
        self.assertEqual(decode_http_content(payload, "deflate", 1024), payload)

    def test_decoded_size_limit_is_enforced(self):
        payload = gzip.compress(b"x" * 2048)
        with self.assertRaises(DecodedContentTooLargeError):
            decode_http_content(payload, "gzip", 1024)


if __name__ == "__main__":
    unittest.main()
