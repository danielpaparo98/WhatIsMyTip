"""Tests for SEC-ME-002: alert webhook URL SSRF protection.

The ``AlertingService`` posts to a webhook URL on job failures.  If the
URL is misconfigured to point at an internal address (RFC1918, loopback,
link-local) or a non-https scheme, an attacker who can influence
``ALERT_WEBHOOK_URL`` could pivot the FastAPI process into an SSRF
proxy that hits internal services (e.g. the cloud metadata endpoint
``169.254.169.254``).

This module tests the helper that validates the URL at startup.

Acceptance criteria
-------------------
* ``https://`` scheme is required.
* Resolved host must not be in a private / loopback / link-local range.
* Both bare IPs and DNS names are accepted; DNS names are resolved
  once and every returned address is checked.
* ``None`` / empty string is the "alerting disabled" sentinel and is
  allowed (it returns the input unchanged so callers can no-op).
* An obviously valid public URL passes (using ``localhost`` would
  resolve to a loopback address and be rejected, so we use a
  deliberately unresolvable internal name ``hooks.example.com`` that
  still satisfies the URL parser — we monkeypatch getaddrinfo to
  return a routable address).
"""

from __future__ import annotations

import socket
from unittest.mock import patch

import pytest

from packages.shared.alerting import WebhookURLValidationError, validate_webhook_url


# A stable, "globally routable" address used in tests to avoid hitting DNS.
# (Cloudflare 1.1.1.1 is a real anycast address; we use it only to feed
# the validator's address-check logic, never to make a real request.)
PUBLIC_IPV4 = "1.1.1.1"


def _fake_getaddrinfo_public(host: str, *args, **kwargs):
    """Patch :func:`socket.getaddrinfo` so any hostname returns a public IP."""
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IPV4, 0))]


class TestValidateWebhookURL:
    """The webhook URL must be https + a non-internal host."""

    # ---- scheme ----

    def test_http_scheme_rejected(self) -> None:
        with pytest.raises(WebhookURLValidationError) as exc:
            validate_webhook_url("http://hooks.example.com/alert")
        assert "https" in str(exc.value).lower()

    def test_ftp_scheme_rejected(self) -> None:
        with pytest.raises(WebhookURLValidationError):
            validate_webhook_url("ftp://hooks.example.com/alert")

    def test_no_scheme_rejected(self) -> None:
        with pytest.raises(WebhookURLValidationError):
            validate_webhook_url("hooks.example.com/alert")

    def test_file_scheme_rejected(self) -> None:
        with pytest.raises(WebhookURLValidationError):
            validate_webhook_url("file:///etc/passwd")

    # ---- internal IPs ----

    def test_loopback_rejected(self) -> None:
        with pytest.raises(WebhookURLValidationError) as exc:
            validate_webhook_url("https://127.0.0.1/alert")
        assert "internal" in str(exc.value).lower() or "loopback" in str(exc.value).lower()

    def test_rfc1918_10_rejected(self) -> None:
        with pytest.raises(WebhookURLValidationError):
            validate_webhook_url("https://10.0.0.5/alert")

    def test_rfc1918_172_rejected(self) -> None:
        with pytest.raises(WebhookURLValidationError):
            validate_webhook_url("https://172.16.0.1/alert")

    def test_rfc1918_192_rejected(self) -> None:
        with pytest.raises(WebhookURLValidationError):
            validate_webhook_url("https://192.168.1.10/alert")

    def test_link_local_rejected(self) -> None:
        """Cloud metadata endpoint lives at 169.254.169.254 — must be blocked."""
        with pytest.raises(WebhookURLValidationError):
            validate_webhook_url("https://169.254.169.254/latest/meta-data/")

    def test_localhost_hostname_rejected(self) -> None:
        with pytest.raises(WebhookURLValidationError):
            validate_webhook_url("https://localhost:8000/alert")

    # ---- good cases ----

    def test_public_https_accepted(self) -> None:
        # A real public URL — no DNS resolution is performed here
        # (we just sanity-check the URL shape).  The function should
        # return the input unchanged on success.
        url = "https://hooks.slack.com/services/T00000000/B00000000/XXX"
        assert validate_webhook_url(url) == url

    def test_public_https_with_path_and_query_accepted(self) -> None:
        url = "https://example.com/webhook?token=abc"
        assert validate_webhook_url(url) == url

    def test_empty_string_is_disabled_sentinel(self) -> None:
        """``""`` means "alerting disabled" — return unchanged, do not raise."""
        assert validate_webhook_url("") == ""

    def test_none_is_disabled_sentinel(self) -> None:
        """``None`` means "alerting disabled" — return unchanged, do not raise."""
        assert validate_webhook_url(None) is None


class TestValidateWebhookURL_DNSResolution:
    """Hostname-based URLs are resolved and every IP is checked."""

    def test_dns_resolves_to_public_ip_accepted(self) -> None:
        with patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo_public):
            url = "https://hooks.example.com/services/abc"
            assert validate_webhook_url(url) == url

    def test_dns_resolves_to_internal_ip_rejected(self) -> None:
        def fake_resolve_private(host, *args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))]

        with patch("socket.getaddrinfo", side_effect=fake_resolve_private):
            with pytest.raises(WebhookURLValidationError) as exc:
                validate_webhook_url("https://hooks.example.com/abc")
            assert "internal" in str(exc.value).lower() or "10.0.0.5" in str(exc.value)

    def test_unresolvable_hostname_rejected(self) -> None:
        def fake_resolve_fail(host, *args, **kwargs):
            raise socket.gaierror("Name or service not known")

        with patch("socket.getaddrinfo", side_effect=fake_resolve_fail):
            with pytest.raises(WebhookURLValidationError):
                validate_webhook_url("https://does-not-exist.invalid/abc")


class TestAlertingServiceRejectsBadURL:
    """The service's ``__init__`` must surface the validation error."""

    def test_init_rejects_http(self) -> None:
        from packages.shared.alerting import AlertingService, WebhookURLValidationError

        with pytest.raises(WebhookURLValidationError):
            AlertingService(webhook_url="http://internal/hook", enabled=True)

    def test_init_rejects_loopback(self) -> None:
        from packages.shared.alerting import AlertingService, WebhookURLValidationError

        with pytest.raises(WebhookURLValidationError):
            AlertingService(webhook_url="https://127.0.0.1/hook", enabled=True)

    def test_init_accepts_valid_url(self) -> None:
        from packages.shared.alerting import AlertingService

        with patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo_public):
            svc = AlertingService(
                webhook_url="https://hooks.example.com/abc",
                enabled=True,
            )
        assert svc is not None
        # And the URL is preserved (not blanked out)
        assert svc._webhook_url == "https://hooks.example.com/abc"

    def test_init_accepts_disabled_state(self) -> None:
        """``None`` / ``""`` / no URL is the disabled sentinel — never raises."""
        from packages.shared.alerting import AlertingService

        for url in (None, ""):
            svc = AlertingService(webhook_url=url, enabled=False)
            assert svc is not None
