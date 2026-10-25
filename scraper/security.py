from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit


class UnsafeUrlError(ValueError):
    """Raised when a URL could reach a non-public network target."""


def _is_forbidden_ip(value: str) -> bool:
    ip = ipaddress.ip_address(value.split("%", 1)[0])
    return not ip.is_global


async def validate_public_url(url: str) -> None:
    """Reject credentials, local hostnames, and DNS answers that are not public."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise UnsafeUrlError("Only public http:// or https:// URLs are allowed.")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeUrlError("URLs containing credentials are not allowed.")

    hostname = parsed.hostname.rstrip(".").lower()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith((".local", ".internal")):
        raise UnsafeUrlError("Local and internal hostnames are not allowed.")

    try:
        literal = ipaddress.ip_address(hostname.split("%", 1)[0])
    except ValueError:
        literal = None
    if literal is not None:
        if _is_forbidden_ip(str(literal)):
            raise UnsafeUrlError("Private, loopback, link-local, and reserved IP addresses are not allowed.")
        return

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        answers = await asyncio.to_thread(socket.getaddrinfo, hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"Hostname could not be resolved: {hostname}") from exc
    addresses = {answer[4][0] for answer in answers}
    if not addresses or any(_is_forbidden_ip(address) for address in addresses):
        raise UnsafeUrlError("The hostname resolves to a non-public IP address.")
