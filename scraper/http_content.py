from __future__ import annotations

import zlib


class DecodedContentTooLargeError(RuntimeError):
    pass


def decode_http_content(content: bytes, content_encoding: str, max_bytes: int) -> bytes:
    """Decode common transfer encodings without trusting a malformed header."""
    decoded = content
    encodings = [value.strip().lower() for value in content_encoding.split(",") if value.strip()]
    for encoding in reversed(encodings):
        if encoding in {"", "identity"}:
            continue
        if encoding in {"gzip", "x-gzip"}:
            decoded = _decode_or_preserve(decoded, 16 + zlib.MAX_WBITS, max_bytes)
        elif encoding == "deflate":
            decoded = _decode_deflate_or_preserve(decoded, max_bytes)
        # The client requests identity encoding. Preserve unexpected encodings
        # rather than allowing a mislabeled response to crash the whole crawl.
    if len(decoded) > max_bytes:
        raise DecodedContentTooLargeError(f"Decoded response exceeds {max_bytes} bytes")
    return decoded


def _decode_or_preserve(content: bytes, window_bits: int, max_bytes: int) -> bytes:
    try:
        return _bounded_zlib_decode(content, window_bits, max_bytes)
    except zlib.error:
        return content


def _decode_deflate_or_preserve(content: bytes, max_bytes: int) -> bytes:
    try:
        return _bounded_zlib_decode(content, zlib.MAX_WBITS, max_bytes)
    except zlib.error:
        try:
            return _bounded_zlib_decode(content, -zlib.MAX_WBITS, max_bytes)
        except zlib.error:
            return content


def _bounded_zlib_decode(content: bytes, window_bits: int, max_bytes: int) -> bytes:
    decoder = zlib.decompressobj(window_bits)
    output = bytearray()
    for offset in range(0, len(content), 64 * 1024):
        chunk = content[offset : offset + 64 * 1024]
        while chunk:
            remaining = max_bytes + 1 - len(output)
            if remaining <= 0:
                raise DecodedContentTooLargeError(f"Decoded response exceeds {max_bytes} bytes")
            output.extend(decoder.decompress(chunk, remaining))
            chunk = decoder.unconsumed_tail
            if len(output) > max_bytes:
                raise DecodedContentTooLargeError(f"Decoded response exceeds {max_bytes} bytes")
    output.extend(decoder.flush(max_bytes + 1 - len(output)))
    if len(output) > max_bytes:
        raise DecodedContentTooLargeError(f"Decoded response exceeds {max_bytes} bytes")
    return bytes(output)
