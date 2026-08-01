"""Unit tests for the web-research URL SSRF guard (TAP-5364)."""

from __future__ import annotations

import socket
from unittest.mock import patch

import pytest

from tapps_brain.url_guard import (
    UrlGuardConfig,
    UrlGuardError,
    validate_url,
)


def _make_config(
    *,
    allow_http: bool = False,
    allow_private_hosts: tuple[str, ...] = (),
    max_bytes: int = 5 * 1024 * 1024,
) -> UrlGuardConfig:
    return UrlGuardConfig(
        allow_http=allow_http,
        allow_private_hosts=frozenset(h.lower() for h in allow_private_hosts),
        max_bytes=max_bytes,
    )


class TestSchemeGuard:
    def test_https_passes(self) -> None:
        with patch(
            "tapps_brain.url_guard.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
        ):
            assert (
                validate_url("https://example.com/docs.md", _make_config())
                == "https://example.com/docs.md"
            )

    def test_http_rejected_by_default(self) -> None:
        with pytest.raises(UrlGuardError, match="http:// scheme not allowed"):
            validate_url("http://example.com/docs.md", _make_config())

    def test_http_allowed_when_opted_in(self) -> None:
        with patch(
            "tapps_brain.url_guard.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
        ):
            assert (
                validate_url(
                    "http://example.com/docs.md",
                    _make_config(allow_http=True),
                )
                == "http://example.com/docs.md"
            )

    def test_file_scheme_rejected(self) -> None:
        with pytest.raises(UrlGuardError, match="unsupported scheme"):
            validate_url("file:///etc/passwd", _make_config())

    def test_missing_host_rejected(self) -> None:
        with pytest.raises(UrlGuardError, match="missing host"):
            validate_url("https:///path", _make_config())


class TestSsrfGuard:
    def test_imds_literal_rejected(self) -> None:
        with pytest.raises(UrlGuardError, match="blocked address"):
            validate_url(
                "http://169.254.169.254/latest/meta-data/",
                _make_config(allow_http=True),
            )

    def test_loopback_literal_rejected(self) -> None:
        with pytest.raises(UrlGuardError, match="blocked address"):
            validate_url(
                "http://127.0.0.1:8080/admin",
                _make_config(allow_http=True),
            )

    def test_localhost_rejected(self) -> None:
        with pytest.raises(UrlGuardError, match="blocked address"):
            validate_url(
                "http://localhost:8080/admin",
                _make_config(allow_http=True),
            )

    def test_private_rfc1918_rejected(self) -> None:
        with pytest.raises(UrlGuardError, match="blocked address"):
            validate_url(
                "http://10.0.0.5/admin",
                _make_config(allow_http=True),
            )

    def test_ipv6_loopback_rejected(self) -> None:
        with pytest.raises(UrlGuardError, match="blocked address"):
            validate_url(
                "http://[::1]/admin",
                _make_config(allow_http=True),
            )

    def test_allowlisted_host_passes(self) -> None:
        assert (
            validate_url(
                "http://localhost:8080/admin",
                _make_config(allow_http=True, allow_private_hosts=("localhost",)),
            )
            == "http://localhost:8080/admin"
        )

    def test_allowlist_is_case_insensitive(self) -> None:
        assert (
            validate_url(
                "http://INTERNAL-DOCS/path",
                _make_config(allow_http=True, allow_private_hosts=("internal-docs",)),
            )
            == "http://INTERNAL-DOCS/path"
        )

    def test_dns_resolution_failure_propagates(self) -> None:
        with (
            patch(
                "tapps_brain.url_guard.socket.getaddrinfo",
                side_effect=socket.gaierror("no such host"),
            ),
            pytest.raises(UrlGuardError, match="unable to resolve"),
        ):
            validate_url(
                "https://does-not-exist.invalid/docs",
                _make_config(),
            )

    def test_dns_resolved_private_address_rejected(self) -> None:
        with (
            patch(
                "tapps_brain.url_guard.socket.getaddrinfo",
                return_value=[(2, 1, 6, "", ("10.0.0.5", 0))],
            ),
            pytest.raises(UrlGuardError, match="blocked address"),
        ):
            validate_url(
                "https://looks-public.example/docs",
                _make_config(),
            )


def test_url_guard_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESEARCH_ALLOW_HTTP", "1")
    monkeypatch.setenv("RESEARCH_ALLOW_PRIVATE_HOSTS", "localhost,Internal-Docs")
    monkeypatch.setenv("RESEARCH_MAX_BYTES", "1024")
    cfg = UrlGuardConfig.from_env()
    assert cfg.allow_http is True
    assert cfg.allow_private_hosts == frozenset({"localhost", "internal-docs"})
    assert cfg.max_bytes == 1024
