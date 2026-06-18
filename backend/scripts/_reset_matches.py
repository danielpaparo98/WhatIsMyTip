"""Reset afltables_match_id for a season.

SEC-LO-005 — local-only data-wipe tool.

This script mutates the production database (``games.afltables_match_id``
is set back to NULL for an entire season, which forces the next sync
to re-scrape every match's details).  It is **local-only** by design
and is intentionally NOT exposed via the FastAPI app or any cron job.

The script still ships in the production image so the dev workflow
``docker compose exec api python scripts/_reset_matches.py --season=2026``
works, but as a defence-in-depth measure it refuses to run unless one
of the following env vars is set in the calling shell:

* ``WIMT_ALLOW_DESTRUCTIVE_SCRIPTS=1`` — explicit opt-in.
* ``ENVIRONMENT=development`` — opt-in via the standard development
  environment marker (set by docker-compose locally).

If neither is set, the script raises a ``PermissionError`` BEFORE
opening a database connection, so a misconfigured production shell
that happens to ship the file cannot accidentally wipe data.

If you need to use this in a real production scenario (e.g. after a
Squiggle outage forced a re-scrape), invoke it explicitly with
``WIMT_ALLOW_DESTRUCTIVE_SCRIPTS=1 docker compose exec api python
scripts/_reset_matches.py --season=2026`` and document the action
in the runbook.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import text

from packages.shared.db import get_engine


# Env vars that grant the script permission to run.  Both are explicit
# opt-ins: the first is the standard "I know what I'm doing" marker, the
# second is the dev-only environment marker we already set everywhere.
_ALLOW_VARS = ("WIMT_ALLOW_DESTRUCTIVE_SCRIPTS", "WIMT_DEV_DESTRUCTIVE_OK")
_DEV_ENV = "development"


def _assert_allowed() -> None:
    """Raise ``PermissionError`` if no opt-in env var is set.

    The check is intentionally coarse: any ``1`` / ``true`` / ``yes`` in
    one of ``_ALLOW_VARS`` is enough, OR ``ENVIRONMENT == "development"``.
    """
    if os.environ.get("ENVIRONMENT", "").lower() == _DEV_ENV:
        return
    for var in _ALLOW_VARS:
        val = os.environ.get(var, "").strip().lower()
        if val in ("1", "true", "yes"):
            return
    raise PermissionError(
        "_reset_matches.py is a local-only data-wipe tool.  Refusing to "
        "run without an opt-in.  Set WIMT_ALLOW_DESTRUCTIVE_SCRIPTS=1 "
        "(or run with ENVIRONMENT=development) to confirm you understand "
        "this will reset afltables_match_id for an entire season."
    )


async def reset(season: int = 2026):
    _assert_allowed()
    engine = get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            text("UPDATE games SET afltables_match_id=NULL WHERE season=:s"),
            {"s": season}
        )
        print(f"Reset afltables_match_id for {result.rowcount} games (season {season})")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(reset())
