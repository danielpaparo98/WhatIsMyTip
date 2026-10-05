# notebooks/ — boosted_tip experiment sandbox

`boosted_tip_experiments.ipynb` is the tuning surface for the `boosted_tip` XGBoost
heuristic (BT-1): ingest the same training rows the weekly retrain gathers, play with
hyper-parameters, inspect SHAP explanations, and verify the serialization roundtrip.
The notebook is strictly **read-only** against the database — promotion to production
happens only via the weekly job (or its manual runner).

## Prerequisites

- Python 3.12+ toolchain via [uv](https://docs.astral.sh/uv/)
- A reachable Postgres with the backend's data (`games`, `model_predictions`)
- `DATABASE_URL` configured in `backend/.env` (the same file the backend itself uses), e.g.

  ```
  DATABASE_URL=postgresql+asyncpg://user:password@host:5432/whatismytip
  ```

## How to run

From the repository root:

```bash
cd backend
uv sync --group notebooks
uv run --group notebooks jupyter lab ../notebooks/boosted_tip_experiments.ipynb
```

`uv run --group notebooks` re-syncs the environment with the `notebooks` group before
launching, so the `uv sync` line is technically optional — but run it explicitly if you
want the environment materialised up front. `uv run --group notebooks jupyter notebook
../notebooks/boosted_tip_experiments.ipynb` works identically (notebook v7 UI; jupyterlab
is pulled in transitively).

## What the notebook does (by section)

1. **Setup** — walks upward from the kernel's working directory to find `backend/`, puts
   it on `sys.path`, loads `backend/.env` via python-dotenv, and reads `DATABASE_URL`
   (fails fast with instructions when missing). Prints credentials redacted.
2. **Ingestion** — builds a *sync* SQLAlchemy engine, rewriting `+asyncpg` to `+psycopg2`
   in the URL (psycopg2-binary is already a main backend dependency, so nothing extra to
   install; if a foreign kernel environment lacks it, the cell prints the
   `uv pip install psycopg2-binary` fix instead of a confusing driver traceback). Then it
   mirrors `_gather_training_rows` from `packages/shared/services/model_retrain.py`:
   latest 3 seasons, completed games with final scores, at least 4 `model_predictions`
   per game — X built through `build_feature_vector` (the feature contract), y =
   `home_score - away_score`. Prints row counts and a per-season summary.
3. **Split** — time-based: older seasons train, latest season validates (no shuffling).
4. **PARAM PLAY** — the baseline `params` dict (mirrors `BOOSTED_PARAMS` in
   `packages/shared/services/boosted_retrain.py` — see that file for the production
   baseline; the notebook keeps its own editable copy) with a knob table + suggested
   ranges; trains `XGBRegressor`, prints r2/mae for train+val and gain-based feature
   importances.
5. **Experiment helper** — `eval_params(params) -> dict` for quick sweeps (commented
   learning-rate loop included).
6. **SHAP** — `TreeExplainer` on the val split: beeswarm, mean-|SHAP| bar, dependence
   plot for the top feature, the exact persisted importance dict (what lands in
   `model_coefficients` rows for boosted_tip versions, plus `metrics[shap_base_value]`),
   and an additivity sanity check (`base + sum(shap) == prediction`).
7. **Serialization roundtrip** — `save_raw(raw_format='json')` bytes →
   `XGBRegressor().load_model(bytes)` → identical-prediction assert + byte size. These
   are exactly the bytes the weekly job stores in `model_versions.artifact`.

## Feature-contract warning

The 16-feature order comes from `FEATURE_NAMES` in
`packages/shared/heuristics/weighted_tip.py` — `<model>_margin_home`, `<model>_conf`
interleaved per model, in registry order: `elo`, `form`, `home_advantage`, `value`,
`weather_impact`, `injury_impact`, `matchup`, `player_form`. That order **is** the
contract between training and runtime prediction; a reordered vector silently misweights
every prediction. Always build vectors through `build_feature_vector` — never reorder,
never hand-roll.

## Tuning workflow (mini-guide)

1. Run the notebook top-to-bottom with the baseline params (Run All) to establish the
   reference r2/mae.
2. Sweep candidates with `eval_params({**params, ...})` — judge on **val** r2/mae, never
   on train metrics.
3. Cross-check the SHAP bar plot: are the drivers sensible (elo/form near the top)?
4. Keep `tree_method='hist'`, `eval_metric='rmse'`, `random_state=42` fixed so runs stay
   deterministic and comparable to the weekly job.
5. Promote winners by editing `BOOSTED_PARAMS` in
   `backend/packages/shared/services/boosted_retrain.py`; Monday's `ModelRetrainJob`
   (05:00 AWST) picks them up, or train immediately via
   `uv run python scripts/run_boosted_retrain.py` (from `backend/`).

## Dependency policy

- `xgboost` and `shap` are **main** backend dependencies: the weekly retrain runs
  in-process, so the production image must be able to train.
- `jupyter`, `notebook`, `matplotlib`, `python-dotenv` live **only** in the `notebooks`
  dependency group — a plain `uv sync` never installs them, keeping the production image
  lean.
- `numpy`/`pandas` need no extra group: `shap` requires both, so they are always present
  in a synced backend environment.
