"""Ad-hoc runner for the weekly ``boosted_tip`` XGBoost model retrain (BT-1).

Lets ops/admin trigger the boosted-tip retrain manually (e.g. after seeding
historical data or before the weekly cron fires) without waiting for the
scheduler.  Mirrors ``scripts/run_model_retrain.py``: builds the session
factory the same way the sibling scripts do and prints the retrain summary
dict.

Usage:
    uv run python scripts/run_boosted_retrain.py
"""

import asyncio
import json
import os
import sys

# Setup path for imports — scripts/ is inside backend/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from packages.shared.db import _get_session_factory, dispose_engine
from packages.shared.services.boosted_retrain import run_boosted_retrain


async def main() -> None:
    SessionLocal = _get_session_factory()  # noqa: N806 — mirrors run_model_retrain.py
    had_error = False
    try:
        async with SessionLocal() as session:
            result = await run_boosted_retrain(session)

        print("\n" + "=" * 60)
        print("Boosted-tip (XGBoost) model retrain")
        print("=" * 60)
        if result.get("status") == "trained":
            metrics = result.get("metrics", {}) or {}
            print(
                f"status          : trained\n"
                f"model_name      : {result['model_name']}\n"
                f"version         : {result['version']}\n"
                f"training_rows   : {result['training_rows']}\n"
                f"r2              : {metrics.get('r2')}\n"
                f"mae             : {metrics.get('mae')}\n"
                f"shap_base_value : {metrics.get('shap_base_value')}\n"
                f"importances     : {len(result.get('shap_importance', {}))} features"
            )
        else:
            print(
                f"status          : {result.get('status')}\n"
                f"reason          : {result.get('reason')}\n"
                f"rows            : {result.get('rows')}\n"
                f"min_required    : {result.get('min_required')}\n"
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
