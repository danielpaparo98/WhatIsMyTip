"""Unit tests guarding the lazy scikit-learn import (S3).

scikit-learn (and the numpy/scipy it drags in) is the single biggest
resident-memory contributor in the image.  The weekly ``weighted_tip``
retrain is the ONLY code path that needs it, so the heavy imports MUST
live inside ``run_model_retrain()`` and never at module top-level —
otherwise every always-on API worker pays the full RSS cost even though
retrain runs once a week.  The in-process scheduler wires the retrain
cron job at import time (``app.core.scheduler`` imports
``ModelRetrainJob``), so the leak propagates to the whole app.

We assert this in an ISOLATED subprocess so the result is not polluted by
other tests that legitimately import sklearn (e.g. the retrain postgres
suite, which imports the service at collection time).
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]  # backend/

# Heavy ML libraries that must NOT be loaded by importing the always-on path.
_HEAVY_LIBS = ("sklearn", "scipy", "numpy")


def _assert_import_does_not_load_heavy_libs(import_stmts: str) -> None:
    """Run *import_stmts* in a fresh interpreter and assert no heavy ML lib loads."""
    code = textwrap.dedent(
        f"""
        import sys
        {import_stmts}
        offenders = sorted(lib for lib in {_HEAVY_LIBS!r} if lib in sys.modules)
        assert not offenders, (
            "Importing the always-on path loaded heavy ML libs at module "
            f"load time: {{offenders}}"
        )
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(BACKEND_DIR),
        timeout=120,
    )
    assert result.returncode == 0, (
        "Isolated import check failed — a heavy ML lib was loaded at import "
        "time (or the import itself errored):\n"
        f"--- code ---\n{code}"
        f"--- stdout ---\n{result.stdout}"
        f"--- stderr ---\n{result.stderr}"
    )


def test_importing_retrain_service_module_does_not_load_sklearn():
    """``packages.shared.services.model_retrain`` must not pull sklearn/scipy/numpy.

    This is the direct target of the lazy-import fix: importing the service
    module must NOT execute ``import sklearn`` / ``import numpy`` at the
    top level — only ``run_model_retrain()`` may.
    """
    _assert_import_does_not_load_heavy_libs(
        "import packages.shared.services.model_retrain"
    )


def test_importing_retrain_cron_job_does_not_load_sklearn():
    """The scheduler imports the retrain cron job eagerly; it must stay lean.

    ``app.core.scheduler`` does ``from app.cron.model_retrain import
    ModelRetrainJob`` at module top, and that module does
    ``from packages.shared.services.model_retrain import run_model_retrain``.
    If the service still imported sklearn at module top, every API worker
    would pay the cost — so importing the cron job must not load sklearn.
    """
    _assert_import_does_not_load_heavy_libs(
        "from app.cron.model_retrain import ModelRetrainJob"
    )
