"""Tests for the site-rebuild webhook service (SSG freshness).

The frontend is a statically-generated Nuxt site: the tips HTML shown to
crawlers/first-paint is only as fresh as the last ``nuxt generate``.  To
keep it fresh, ``TipGenerationJob`` fires a deploy webhook (e.g.
DigitalOcean App Platform deploy webhook URL) after a *successful* tip
generation so the site rebuilds with the new tips.

Contract:
* No webhook URL configured → no HTTP call, job result unaffected.
* Webhook configured + success → one POST, ``site_rebuild`` in result.
* Webhook configured + HTTP/network error → logged, must NOT fail the
  tip-generation job (rebuilding is best-effort).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from packages.shared.config import settings
from packages.shared.services.site_rebuild import trigger_site_rebuild


@pytest.fixture(autouse=True)
def _clean_settings(monkeypatch):
    monkeypatch.setattr(settings, "site_rebuild_webhook_url", None)
    monkeypatch.setattr(settings, "site_rebuild_timeout_seconds", 10)


class TestTriggerSiteRebuild:
    @pytest.mark.asyncio
    async def test_no_url_configured_is_a_noop(self):
        """Unconfigured webhook → no HTTP call, returns None."""
        http = MagicMock()
        http.post = AsyncMock()
        with patch("packages.shared.services.site_rebuild.httpx.AsyncClient", return_value=http):
            result = await trigger_site_rebuild(tips_created=27)

        assert result is None
        http.post.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_configured_url_posts_once_and_reports(self, monkeypatch):
        """Configured webhook → exactly one POST with JSON payload."""
        monkeypatch.setattr(
            settings, "site_rebuild_webhook_url", "https://hook.example/deploy"
        )
        response = MagicMock()
        response.status_code = 200
        http = MagicMock()
        http.__aenter__ = AsyncMock(return_value=http)
        http.__aexit__ = AsyncMock(return_value=False)
        http.post = AsyncMock(return_value=response)
        with patch("packages.shared.services.site_rebuild.httpx.AsyncClient", return_value=http):
            result = await trigger_site_rebuild(tips_created=27)

        http.post.assert_awaited_once()
        args, kwargs = http.post.await_args
        assert args[0] == "https://hook.example/deploy"
        assert kwargs["json"] == {"event": "tips_generated", "tips_created": 27}
        assert kwargs["timeout"] == 10
        assert result == {"triggered": True, "status_code": 200}

    @pytest.mark.asyncio
    async def test_webhook_error_does_not_raise(self, monkeypatch):
        """A webhook failure must never fail tip generation."""
        monkeypatch.setattr(
            settings, "site_rebuild_webhook_url", "https://hook.example/deploy"
        )
        http = MagicMock()
        http.__aenter__ = AsyncMock(return_value=http)
        http.__aexit__ = AsyncMock(return_value=False)
        http.post = AsyncMock(side_effect=RuntimeError("network down"))
        with patch("packages.shared.services.site_rebuild.httpx.AsyncClient", return_value=http):
            result = await trigger_site_rebuild(tips_created=0)

        assert result == {"triggered": False, "error": "network down"}


class TestTipGenerationJobFiresRebuild:
    """TipGenerationJob.run triggers the site rebuild after success."""

    def _make_job(self, monkeypatch):
        from app.cron.tip_generation import TipGenerationJob

        session = AsyncMock()

        @asynccontextmanager_factory
        async def factory():
            yield session

        job = TipGenerationJob(factory)
        return job

    @pytest.mark.asyncio
    async def test_rebuild_fired_when_configured(self, monkeypatch):
        monkeypatch.setattr(
            settings, "site_rebuild_webhook_url", "https://hook.example/deploy"
        )
        mock_rebuild = AsyncMock(return_value={"triggered": True, "status_code": 200})
        with patch(
            "app.cron.tip_generation.trigger_site_rebuild", new=mock_rebuild
        ), patch(
            "app.cron.tip_generation.run_tip_generation",
            new=AsyncMock(return_value={"tips_created": 27}),
        ):
            job = self._make_job(monkeypatch)
            result = await job.run()

        mock_rebuild.assert_awaited_once()
        assert result["site_rebuild"] == {"triggered": True, "status_code": 200}

    @pytest.mark.asyncio
    async def test_rebuild_skipped_when_unconfigured(self, monkeypatch):
        """Unconfigured URL → service returns None → no result key added."""
        monkeypatch.setattr(settings, "site_rebuild_webhook_url", None)
        # Unconfigured: the real service short-circuits to None.
        mock_rebuild = AsyncMock(return_value=None)
        with patch(
            "app.cron.tip_generation.trigger_site_rebuild", new=mock_rebuild
        ), patch(
            "app.cron.tip_generation.run_tip_generation",
            new=AsyncMock(return_value={"tips_created": 0}),
        ):
            job = self._make_job(monkeypatch)
            result = await job.run()

        mock_rebuild.assert_awaited_once()
        assert "site_rebuild" not in result


# Helper: BaseJob expects a zero-arg callable returning an async CM.
def asynccontextmanager_factory(fn):
    from contextlib import asynccontextmanager

    return asynccontextmanager(fn)
