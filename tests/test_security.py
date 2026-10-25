import unittest
from unittest.mock import patch

from scraper.security import UnsafeUrlError, validate_public_url


class SecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_names_and_ip_literals_are_rejected(self):
        for url in (
            "http://localhost/test",
            "http://127.0.0.1/test",
            "http://10.1.2.3/test",
            "http://169.254.1.2/test",
            "http://service.internal/test",
        ):
            with self.subTest(url=url), self.assertRaises(UnsafeUrlError):
                await validate_public_url(url)

    async def test_credentials_are_rejected(self):
        with self.assertRaises(UnsafeUrlError):
            await validate_public_url("https://user:password@example.com/")

    async def test_dns_rebinding_answer_is_rejected(self):
        answer = [(2, 1, 6, "", ("192.168.1.5", 443))]
        with patch("socket.getaddrinfo", return_value=answer):
            with self.assertRaises(UnsafeUrlError):
                await validate_public_url("https://example.com/")


if __name__ == "__main__":
    unittest.main()
