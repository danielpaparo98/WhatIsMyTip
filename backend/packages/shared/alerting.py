"""Alerting service for sending webhook notifications on job failures."""

from datetime import datetime, timezone

import httpx

from packages.shared.config import settings
from packages.shared.logger import get_logger

logger = get_logger(__name__)


class AlertingService:
    """Sends webhook alerts when cron jobs fail or encounter errors."""

    def __init__(self, webhook_url: str | None = None, enabled: bool = False):
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
