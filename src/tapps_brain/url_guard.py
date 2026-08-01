"""Outbound URL SSRF guard for web research (TAP-5364 / ADR-0030).

Brain-local port of tapps-mcp ``tapps_core.knowledge.url_guard`` semantics.
Does not import tapps-mcp at runtime.

Guards applied:
- Scheme must be https:// (or http:// only when ``allow_http`` is True).
- Host must resolve to a non-private/non-loopback/non-link-local IP, unless the
  hostname is explicitly listed in ``allow_private_hosts``.
- ``max_bytes`` is carried for fetch helpers that stream response bodies.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

DEFAULT_MAX_BYTES = 5 * 1024 * 1024
ALLOW_HTTP_ENV = "RESEARCH_ALLOW_HTTP"
ALLOW_PRIVATE_HOSTS_ENV = "RESEARCH_ALLOW_PRIVATE_HOSTS"
MAX_BYTES_ENV = "RESEARCH_MAX_BYTES"


class UrlGuardError(ValueError):
    """Raised when a URL fails the SSRF guard."""


@dataclass(frozen=True)
class UrlGuardConfig:
    """Runtime configuration for outbound URL validation."""

    allow_http: bool
    allow_private_hosts: frozenset[str]
    max_bytes: int

    @classmethod
    def from_env(cls) -> UrlGuardConfig:
        """Build config from ``RESEARCH_*`` environment variables."""
        allow_http_raw = os.environ.get(ALLOW_HTTP_ENV, "0").strip().lower()
        allow_http = allow_http_raw in {"1", "true", "yes", "on"}
        hosts_raw = os.environ.get(ALLOW_PRIVATE_HOSTS_ENV, "").strip()
        allow_private = frozenset(h.strip().lower() for h in hosts_raw.split(",") if h.strip())
        max_bytes = DEFAULT_MAX_BYTES
        max_raw = os.environ.get(MAX_BYTES_ENV, "").strip()
        if max_raw:
            try:
                parsed = int(max_raw)
            except ValueError:
                parsed = DEFAULT_MAX_BYTES
            if parsed > 0:
                max_bytes = parsed
        return cls(
            allow_http=allow_http,
            allow_private_hosts=allow_private,
            max_bytes=max_bytes,
        )


def validate_url(url: str, config: UrlGuardConfig) -> str:
    """Validate *url* against the SSRF guard.

    Returns the URL unchanged if valid. Raises :class:`UrlGuardError` otherwise.
    """
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme == "https":
        pass
    elif scheme == "http":
        if not config.allow_http:
            raise UrlGuardError(f"http:// scheme not allowed: {url}")
    else:
        raise UrlGuardError(f"unsupported scheme {scheme!r}: {url}")

    host = (parsed.hostname or "").strip()
    if not host:
        raise UrlGuardError(f"missing host: {url}")

    host_lower = host.lower()
    if host_lower in config.allow_private_hosts:
        return url

    for address in _resolve_addresses(host):
        if _is_blocked_address(address):
            raise UrlGuardError(
                f"host {host!r} resolves to blocked address {address}: {url}",
            )
    return url


def _resolve_addresses(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Return parsed IP addresses for *host* (literal or resolved via DNS)."""
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UrlGuardError(f"unable to resolve host {host!r}: {exc}") from exc

    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in infos:
        sockaddr = info[4]
        try:
            addresses.append(ipaddress.ip_address(sockaddr[0]))
        except ValueError:
            continue
    if not addresses:
        raise UrlGuardError(f"no usable addresses for host {host!r}")
    return addresses


def _is_blocked_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified,
    )
