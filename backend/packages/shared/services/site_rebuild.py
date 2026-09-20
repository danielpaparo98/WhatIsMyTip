"""Site-rebuild webhook for SSG freshness.

The frontend is a statically-generated Nuxt site (``nitro preset:
'static'``): the tips HTML that crawlers and first-paint visitors see is
only as fresh as the last ``nuxt generate``.  To keep it fresh without
manual rebuilds, :func:`trigger_site_rebuild` fires a rebuild after a
successful tip-generation run, in one of two modes:

* **Generic webhook** — POST to ``SITE_REBUILD_WEBHOOK_URL`` (any
  HTTP endpoint that triggers a deploy).
* **DigitalOcean API** (used when no generic webhook is configured) —
  POST ``https://api.digitalocean.com/v2/apps/{SITE_REBUILD_DO_APP_ID}/
  deployments`` with ``SITE_REBUILD_DO_TOKEN``.  DigitalOcean App
  Platform has no inbound deploy-webhook API; the deployments endpoint
  is the supported programmatic trigger.

Best-effort by design: a rebuild failure is logged and reported in the
job result, but must never fail tip generation itself.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import httpx

from ..config import settings
from ..logger import get_logger

logger = get_logger(__name__)

_DO_API_BASE = "https://api.digitalocean.com/v2"


async def trigger_site_rebuild(
    tips_created: int, extra: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """Fire the site-rebuild trigger (if configured).

    Args:
        tips_created: Number of tips created by the generation run —
            included in the generic-webhook payload for observability.
        extra: Optional additional content signals (tips updated, match
            analyses / GF reports created) merged into the webhook
            payload — GF-TRIGGER observability.

    Returns:
        ``None`` when no rebuild trigger is configured, otherwise a
        small dict describing the outcome (``{"triggered": True, ...}``
        or ``{"triggered": False, "error": "..."}``).
    """
    if settings.site_rebuild_webhook_url:
        return await _fire_generic_webhook(tips_created, extra)
    if settings.site_rebuild_do_app_id and settings.site_rebuild_do_token:
        return await _fire_do_api_deployment()
    return None


async def _fire_generic_webhook(
    tips_created: int, extra: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """POST a JSON payload to the configured generic webhook URL."""
    url = settings.site_rebuild_webhook_url
    payload: Dict[str, Any] = {
        "event": "tips_generated",
        "tips_created": tips_created,
        **(extra or {}),
    }
    timeout = settings.site_rebuild_timeout_seconds

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, json=payload, timeout=timeout)
        logger.info(
            "site rebuild webhook fired (status=%s, tips_created=%s)",
            response.status_code,
            tips_created,
        )
        return {"triggered": True, "status_code": response.status_code}
    except Exception as exc:  # noqa: BLE001 — best-effort, never fail the job
        logger.warning(
            "site rebuild webhook failed (non-fatal): %r", exc
        )
        return {"triggered": False, "error": str(exc)}


async def _fire_do_api_deployment() -> Dict[str, Any]:
    """Trigger an App Platform deployment via the DigitalOcean API."""
    app_id = settings.site_rebuild_do_app_id
    token = settings.site_rebuild_do_token
    url = f"{_DO_API_BASE}/apps/{app_id}/deployments"
    timeout = settings.site_rebuild_timeout_seconds

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=timeout,
            )
        logger.info(
            "site rebuild DO deployment triggered (status=%s, app=%s)",
            response.status_code,
            app_id,
        )
        return {"triggered": True, "mode": "do_api", "status_code": response.status_code}
    except Exception as exc:  # noqa: BLE001 — best-effort, never fail the job
        logger.warning(
            "site rebuild DO deployment failed (non-fatal): %r", exc
        )
        return {"triggered": False, "error": str(exc)}
