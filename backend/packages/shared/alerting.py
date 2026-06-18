"""Alerting service for sending webhook notifications on job failures."""

from __future__ import annotations

import ipaddress
import socket
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

from packages.shared.config import settings
from packages.shared.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# SSRF protection (SEC-ME-002)
# ---------------------------------------------------------------------------
#
# A misconfigured ``ALERT_WEBHOOK_URL`` could turn the FastAPI process into
# an SSRF proxy: an attacker who can influence the env var (or who
# compromises a less-trusted service that sets it) could point the
# webhook at internal addresses such as the cloud metadata service
# (``169.254.169.254``) or a private database server.  We validate the
# URL at startup so a bad config fails fast instead of silently
# exfiltrating data on every job failure.
#
# Rules:
#   * Scheme must be ``https://`` (plain http is rejected — webhooks
#     carry operational data and must be authenticated in transit).
#   * Hostname must resolve to a non-internal IP.  We accept both
#     bare IPs and DNS names; in the latter case we resolve once and
#     check every returned address — a malicious operator can register
#     a DNS name that resolves to a private IP, and we don't want the
#     first hop to bypass the check.
#   * The resolved address must NOT be in any of the IANA-special
#     ranges: loopback, private (RFC1918), link-local, multicast,
#     reserved, or unspecified.


class WebhookURLValidationError(ValueError):
    """Raised when ``ALERT_WEBHOOK_URL`` is unsafe (SEC-ME-002)."""


def _is_internal_address(addr: str) -> bool:
    """Return True if ``addr`` is in any IANA-special / private range.

    ``ip.is_global`` is True ONLY for globally-routable addresses — any
    loopback / private / link-local / multicast / reserved / unspecified
    address returns False, so we invert it to get the "internal" answer.
    """
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        # Could be an IPv6 with zone id, etc. — let DNS-resolution code
        # handle it.
        return False
    return not ip.is_global


def validate_webhook_url(url: str | None) -> str | None:
    """Validate the alert webhook URL for SSRF safety.

    Args:
        url: The webhook URL to validate.  ``None`` / empty is treated
            as "alerting disabled" and returned as-is so callers can
            short-circuit without raising.

    Returns:
        The input URL (unchanged) on success; ``None`` / ``""`` for the
        disabled sentinel.

    Raises:
        WebhookURLValidationError: When the URL is unsafe.  The message
            is intentionally short and safe to log.
    """
    if url is None or url == "":
        return url

    parsed = urlparse(url)

    # ---- scheme check ----
    if parsed.scheme.lower() != "https":
        raise WebhookURLValidationError(
            f"ALERT_WEBHOOK_URL must use https:// (got {parsed.scheme!r})"
        )

    host = parsed.hostname
    if not host:
        raise WebhookURLValidationError(
            "ALERT_WEBHOOK_URL must include a hostname"
        )

    # ---- IP / hostname policy ----
    # If the host is a bare IP literal, we apply the rules directly.
    # Otherwise we resolve it once and check every returned address —
    # a malicious operator can register a DNS name that resolves to a
    # private IP, and we don't want the first hop to bypass the check.
    addresses: list[str] = []
    try:
        # ``getaddrinfo`` returns tuples of (family, ..., sockaddr).
        addr_info = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise WebhookURLValidationError(
            f"ALERT_WEBHOOK_URL hostname could not be resolved: {host}"
        ) from exc

    for family, _type, _proto, _canon, sockaddr in addr_info:
        if family == socket.AF_INET:
            addresses.append(sockaddr[0])
        elif family == socket.AF_INET6:
            # Strip the scope id (e.g. ``fe80::1%eth0``) before parsing.
            addresses.append(sockaddr[0].split("%", 1)[0])

    if not addresses:
        raise WebhookURLValidationError(
            f"ALERT_WEBHOOK_URL hostname resolved to no addresses: {host}"
        )

    for addr in addresses:
        if _is_internal_address(addr):
            raise WebhookURLValidationError(
                f"ALERT_WEBHOOK_URL resolves to internal address: {addr}"
            )

    return url


def _validate_or_warn(url: str | None) -> str | None:
    """Run :func:`validate_webhook_url`, logging a warning instead of raising.

    Used for lifespan-level validation where we want the app to keep
    running with alerting disabled rather than refuse to boot.  Returns
    the (possibly empty) URL to feed into ``AlertingService.__init__``.
    """
    if not url:
        return url
    try:
        return validate_webhook_url(url)
    except WebhookURLValidationError as exc:
        logger.warning(
            "ALERT_WEBHOOK_URL failed SSRF validation; alerting will be "
            "disabled at runtime: %s",
            exc,
        )
        return None


class AlertingService:
    """Sends webhook alerts when cron jobs fail or encounter errors."""

    def __init__(self, webhook_url: str | None = None, enabled: bool = False):
        # Validate the webhook URL at construction time.  ``None`` /
        # empty is the "alerting disabled" sentinel and is accepted.
        if webhook_url is not None and webhook_url != "":
            webhook_url = validate_webhook_url(webhook_url)

        self._webhook_url = webhook_url or settings.alert_webhook_url
        self._enabled = enabled or settings.alert_enabled

    async def send_alert(
        self,
        job_name: str,
        status: str,
        message: str,
        details: dict | None = None,
        execution_id: str | None = None,
    ) -> bool:
        """Send an alert webhook notification.

        Args:
            job_name: Name of the cron job (e.g., "daily-sync")
            status: Status string (e.g., "failed", "timeout", "warning")
            message: Human-readable message
            details: Optional dict with additional context
            execution_id: Optional job execution ID for reference

        Returns:
            True if alert was sent successfully, False otherwise
        """
        if not self._enabled or not self._webhook_url:
            logger.debug(
                f"Alerting disabled or no webhook URL configured, skipping alert for {job_name}"
            )
            return False

        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "service": "whatismytip-backend",
            "job_name": job_name,
            "status": status,
            "message": message,
            "execution_id": execution_id,
            "details": details or {},
        }

        try:
            async with httpx.AsyncClient(timeout=settings.alert_timeout_seconds) as client:
                response = await client.post(
                    self._webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                if response.status_code < 400:
                    logger.info(f"Alert sent for {job_name}: {status}")
                    return True
                else:
                    logger.warning(
                        f"Alert webhook returned {response.status_code}: {response.text}"
                    )
                    return False
        except Exception as e:
            logger.error(f"Failed to send alert webhook: {e}")
            return False

    async def send_failure_alert(
        self,
        job_name: str,
        error: str,
        execution_id: str | None = None,
        duration_seconds: float | None = None,
    ) -> bool:
        """Convenience method for sending failure alerts."""
        details: dict[str, str | float] = {}
        if duration_seconds is not None:
            details["duration_seconds"] = duration_seconds
        return await self.send_alert(
            job_name=job_name,
            status="failed",
            message=f"Job {job_name} failed: {error}",
            details=details,
            execution_id=execution_id,
        )

    async def send_timeout_alert(
        self,
        job_name: str,
        elapsed_seconds: float,
        remaining_work: str | None = None,
    ) -> bool:
        """Convenience method for sending timeout warnings."""
        details = {"elapsed_seconds": elapsed_seconds}
        if remaining_work:
            details["remaining_work"] = remaining_work
        return await self.send_alert(
            job_name=job_name,
            status="timeout_warning",
            message=f"Job {job_name} approached timeout after {elapsed_seconds:.0f}s",
            details=details,
        )
