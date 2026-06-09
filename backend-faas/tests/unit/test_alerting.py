"""Unit tests for the AlertingService."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from packages.shared.alerting import AlertingService


class TestAlertDisabled:
    """When alert_enabled=False, no HTTP call is made."""

    @pytest.mark.asyncio
    async def test_alert_disabled(self):
        """When alert_enabled=False, send_alert returns False without HTTP call."""
        service = AlertingService(webhook_url="https://example.com/webhook", enabled=False)

        with patch("packages.shared.alerting.httpx") as mock_httpx:
            result = await service.send_alert(
                job_name="daily-sync",
                status="failed",
                message="Job failed",
            )

        assert result is False
        mock_httpx.AsyncClient.assert_not_called()

    @pytest.mark.asyncio
    async def test_alert_no_webhook_url(self):
        """When webhook_url is None, send_alert returns False without HTTP call."""
        service = AlertingService(webhook_url=None, enabled=True)

        with patch("packages.shared.alerting.httpx") as mock_httpx:
            result = await service.send_alert(
                job_name="daily-sync",
                status="failed",
                message="Job failed",
            )

        assert result is False
        mock_httpx.AsyncClient.assert_not_called()


class TestAlertSuccess:
    """When alerting is enabled and webhook responds with 2xx."""

    @pytest.mark.asyncio
    async def test_alert_success(self):
        """Successful alert returns True and sends correct payload."""
        service = AlertingService(webhook_url="https://example.com/webhook", enabled=True)

        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("packages.shared.alerting.httpx.AsyncClient", return_value=mock_client):
            result = await service.send_alert(
                job_name="daily-sync",
                status="failed",
                message="Sync failed",
                details={"error_code": 500},
                execution_id="123",
            )

        assert result is True
        mock_client.post.assert_awaited_once()
        call_kwargs = mock_client.post.call_args
        assert call_kwargs.kwargs["json"]["job_name"] == "daily-sync"
        assert call_kwargs.kwargs["json"]["status"] == "failed"
        assert call_kwargs.kwargs["json"]["message"] == "Sync failed"
        assert call_kwargs.kwargs["json"]["execution_id"] == "123"
        assert call_kwargs.kwargs["json"]["details"] == {"error_code": 500}
        assert call_kwargs.kwargs["json"]["service"] == "whatismytip-backend"
        assert "timestamp" in call_kwargs.kwargs["json"]


class TestAlertWebhookError:
    """When webhook returns an error status code."""

    @pytest.mark.asyncio
    async def test_alert_webhook_error(self):
        """Webhook returning 500 should return False but not raise."""
        service = AlertingService(webhook_url="https://example.com/webhook", enabled=True)

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("packages.shared.alerting.httpx.AsyncClient", return_value=mock_client):
            result = await service.send_alert(
                job_name="daily-sync",
                status="failed",
                message="Job failed",
            )

        assert result is False


class TestAlertNetworkError:
    """When httpx raises a network exception."""

    @pytest.mark.asyncio
    async def test_alert_network_error(self):
        """Network error should return False but not raise."""
        service = AlertingService(webhook_url="https://example.com/webhook", enabled=True)

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=ConnectionError("Network unreachable"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("packages.shared.alerting.httpx.AsyncClient", return_value=mock_client):
            result = await service.send_alert(
                job_name="daily-sync",
                status="failed",
                message="Job failed",
            )

        assert result is False


class TestSendFailureAlert:
    """Test the send_failure_alert convenience method."""

    @pytest.mark.asyncio
    async def test_send_failure_alert(self):
        """send_failure_alert formats the payload correctly."""
        service = AlertingService(webhook_url="https://example.com/webhook", enabled=True)

        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("packages.shared.alerting.httpx.AsyncClient", return_value=mock_client):
            result = await service.send_failure_alert(
                job_name="tip-generation",
                error="Model timeout",
                execution_id="42",
                duration_seconds=120.5,
            )

        assert result is True
        call_kwargs = mock_client.post.call_args
        payload = call_kwargs.kwargs["json"]
        assert payload["status"] == "failed"
        assert payload["job_name"] == "tip-generation"
        assert "Model timeout" in payload["message"]
        assert payload["execution_id"] == "42"
        assert payload["details"]["duration_seconds"] == 120.5


class TestSendTimeoutAlert:
    """Test the send_timeout_alert convenience method."""

    @pytest.mark.asyncio
    async def test_send_timeout_alert(self):
        """send_timeout_alert formats the payload correctly."""
        service = AlertingService(webhook_url="https://example.com/webhook", enabled=True)

        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("packages.shared.alerting.httpx.AsyncClient", return_value=mock_client):
            result = await service.send_timeout_alert(
                job_name="historic-refresh",
                elapsed_seconds=780.0,
                remaining_work="8 seasons remaining",
            )

        assert result is True
        call_kwargs = mock_client.post.call_args
        payload = call_kwargs.kwargs["json"]
        assert payload["status"] == "timeout_warning"
        assert payload["job_name"] == "historic-refresh"
        assert "780" in payload["message"]
        assert payload["details"]["elapsed_seconds"] == 780.0
        assert payload["details"]["remaining_work"] == "8 seasons remaining"

    @pytest.mark.asyncio
    async def test_send_timeout_alert_without_remaining_work(self):
        """send_timeout_alert without remaining_work omits it from details."""
        service = AlertingService(webhook_url="https://example.com/webhook", enabled=True)

        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("packages.shared.alerting.httpx.AsyncClient", return_value=mock_client):
            result = await service.send_timeout_alert(
                job_name="historic-refresh",
                elapsed_seconds=780.0,
            )

        assert result is True
        call_kwargs = mock_client.post.call_args
        payload = call_kwargs.kwargs["json"]
        assert payload["status"] == "timeout_warning"
        assert "remaining_work" not in payload["details"]
