"""Site-rebuild webhook for SSG freshness.

The frontend is a statically-generated Nuxt site (``nitro preset:
'static'``): the tips HTML that crawlers and first-paint visitors see is
only as fresh as the last ``nuxt generate``.  To keep it fresh without
manual rebuilds, :func:`trigger_site_rebuild` POSTs to a configured
deploy webhook (``SITE_REBUILD_WEBHOOK_URL`` — e.g. a DigitalOcean App
Platform deploy webhook) after a successful tip-generation run.

Best-effort by design: a rebuild failure is logged and reported in the
job result, but must never fail tip generation itself.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import httpx

from ..config import settings
from ..logger import get_logger

logger = get_logger(__name__)


async def trigger_site_rebuild(tips_created: int) -> Optional[Dict[str, Any]]:
    """Fire the site-rebuild webhook (if configured).

    Args:
        tips_created: Number of tips created by the generation run —
            included in the webhook payload for observability.

    Returns:
        ``None`` when no webhook is configured, otherwise a small dict
        describing the outcome (``{"triggered": True, "status_code":
        ...}`` or ``{"triggered": False, "error": "..."}``).
    """
    url = settings.site_rebuild_webhook_url
    if not url:
        return None

    payload = {"event": "tips_generated", "tips_created": tips_created}
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
