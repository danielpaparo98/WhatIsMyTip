"""Ad-hoc runner for the weekly ``weighted_tip`` model retrain.

Lets ops/admin trigger a retrain manually (e.g. after seeding historical
data or before the weekly cron fires) without waiting for the scheduler.
Builds the session factory the same way the other sibling scripts do and
prints the retrain summary dict.

Usage:
    uv run python scripts/run_model_retrain.py
"""

import asyncio
import json
import os
import sys

# Setup path for imports — scripts/ is inside backend/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from packages.shared.db import _get_session_factory, dispose_engine
from packages.shared.services.model_retrain import run_model_retrain


async def main() -> None:
    SessionLocal = _get_session_factory()
    had_error = False
    try:
        async with SessionLocal() as session:
            result = await run_model_retrain(session)

        print("\n" + "=" * 60)
        print("Weighted-tip model retrain")
        print("=" * 60)
        if result.get("status") == "trained":
            metrics = result.get("metrics", {}) or {}
            print(
                f"status        : trained\n"
                f"model_name    : {result['model_name']}\n"
                f"version       : {result['version']}\n"
                f"training_rows : {result['training_rows']}\n"
                f"intercept     : {result['intercept']:.6f}\n"
                f"r2            : {metrics.get('r2')}\n"
                f"mae           : {metrics.get('mae')}\n"
                f"coefficients  : {len(result.get('coefficients', {}))} features"
            )
        else:
            print(
                f"status        : {result.get('status')}\n"
                f"reason        : {result.get('reason')}\n"
                f"rows          : {result.get('rows')}\n"
                f"min_required  : {result.get('min_required')}\n"
                "Active model left unchanged."
            )
        print("=" * 60 + "\n")
        print(json.dumps(result, indent=2, default=str))
    except Exception as exc:  # noqa: BLE001
        had_error = True
        print(f"Error: {exc}")
        sys.exit(1)
    finally:
        await dispose_engine(force=had_error)


if __name__ == "__main__":
    asyncio.run(main())
