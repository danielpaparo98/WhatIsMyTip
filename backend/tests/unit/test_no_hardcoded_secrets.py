"""
Regression test: no DigitalOcean personal access tokens may live in the repo.

A DO PAT looks like `dop_v1_<64 hex chars>`. The previous incident
(see git log on `feature/remove-exposed-do-token`) was that the
diagnostic scripts `check.py` and `get_logs.py` had one hardcoded
and shipped it to GitHub. This test guards against that happening
again by walking the tracked source tree and failing on any match.

Excluded from the scan (we never want to false-positive on these):
- `.git/`        -- git's own bookkeeping, not project source.
- `.venv/`, `venv/`, `node_modules/`, `.pytest_cache/` -- vendored /
  generated / cache; never committed, and full of "looks-suspicious"
  hex blobs.
- `bun.lockb`    -- binary lockfile, not human-readable.
- The test file itself (its module docstring quotes the prefix as
  part of explaining what we're scanning for).

Everything else (including all `.py`, `.ts`, `.vue`, `.js`, `.yml`,
`.yaml`, `.json`, `.md`, `.sh`, `.conf` files) is in scope.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ── Configuration ───────────────────────────────────────────────────────
#
# Match `dop_v1_` followed by 56 hex chars (the actual length of a
# DO PAT after the prefix).  We deliberately anchor on `dop_v1_` rather
# than a generic "secret-like" regex so the test only trips on a real
# DO PAT and stays readable when it fails.
DO_PAT_PATTERN = re.compile(r"dop_v1_[a-f0-9]{56}")

# Anything under these directories is vendored / generated / cache and
# not interesting for this scan.
EXCLUDED_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".output",  # Nuxt build output
    "__pycache__",
}

# Files we never want to scan.  `bun.lockb` is binary; this test file
# itself quotes `dop_v1_` in its module docstring as documentation.
EXCLUDED_FILE_NAMES = {
    "bun.lockb",
    "test_no_hardcoded_secrets.py",
}

# Limit the file types we scan to text-based source / config.  This
# keeps the test fast and avoids opening binary files we'd only ever
# skip.
SCAN_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".vue",
    ".yml", ".yaml", ".json", ".md", ".sh", ".bash", ".ps1",
    ".conf", ".cfg", ".ini", ".toml", ".txt", ".env", ".example",
    ".html", ".css", ".scss", ".sql",
}


def _repo_root() -> Path:
    """Resolve the repo root from this test file's location.

    `backend/tests/unit/test_no_hardcoded_secrets.py` -> 3 levels up.
    """
    return Path(__file__).resolve().parents[3]


def _is_excluded_dir(path: Path) -> bool:
    return any(part in EXCLUDED_DIR_NAMES for part in path.parts)


def _iter_scannable_files(root: Path) -> list[Path]:
    """Yield every text-source file under `root` that's in scope.

    Walks the tree once, returns a list (not a generator) so failures
    can report the total count and a sample of offenders.
    """
    found: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if _is_excluded_dir(path):
            continue
        if path.name in EXCLUDED_FILE_NAMES:
            continue
        if path.suffix.lower() not in SCAN_EXTENSIONS:
            continue
        found.append(path)
    return found


def _scan_for_tokens(path: Path) -> list[tuple[int, str]]:
    """Return `[(line_no, line), ...]` for every DO-PAT match in `path`.

    Reads the file as UTF-8 with `errors='replace'` so a stray
    non-UTF-8 byte in a config file doesn't crash the scan.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    hits: list[tuple[int, str]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if DO_PAT_PATTERN.search(line):
            hits.append((line_no, line))
    return hits


# ── The actual test ─────────────────────────────────────────────────────


def test_no_hardcoded_digitalocean_tokens() -> None:
    """Fail if any tracked-source file contains a `dop_v1_...` PAT."""
    root = _repo_root()
    files = _iter_scannable_files(root)
    assert files, (
        f"Scan found no scannable files under {root} -- "
        "the path resolution is probably wrong."
    )

    offenders: list[str] = []
    for path in files:
        for line_no, line in _scan_for_tokens(path):
            # Redact the secret itself in the failure message so the
            # test output is safe to paste into a public issue tracker.
            redacted = DO_PAT_PATTERN.sub("dop_v1_<REDACTED>", line)
            offenders.append(f"{path.relative_to(root)}:{line_no}: {redacted.strip()}")

    assert not offenders, (
        "Hardcoded DigitalOcean personal access token(s) found in the "
        "repo. Personal access tokens must NEVER be committed. \n"
        "Rotate the leaked token(s) in the DO control panel, then load "
        "them at runtime via the `DIGITALOCEAN_ACCESS_TOKEN` env var.\n\n"
        f"Offending lines ({len(offenders)}):\n  " + "\n  ".join(offenders)
    )


# ── Sanity checks: the scanner actually works ───────────────────────────
#
# Without these, an accidentally-over-broad exclude list could silently
# make the test pass on a polluted repo.  These cases verify the regex
# both detects real PATs and doesn't false-positive on lookalikes.
# (`tmp_path` is a pytest built-in fixture; it's auto-injected.)

def _make_fake_pat() -> str:
    # 56 hex chars, lowercase, all valid hex digits.  Looks identical
    # to a real PAT to the regex but is obviously synthetic.
    return "dop_v1_" + "a" * 56


def test_scanner_detects_synthetic_pat_in_temp_file(
    tmp_path: Path,
) -> None:
    """A file containing a `dop_v1_...` token MUST be flagged."""
    fake = tmp_path / "leaked.py"
    fake.write_text(
        f"# oops\nTOKEN = '{_make_fake_pat()}'\n",
        encoding="utf-8",
    )
    hits = _scan_for_tokens(fake)
    assert len(hits) == 1, f"scanner missed the synthetic PAT, got hits={hits!r}"
    assert hits[0][0] == 2, f"wrong line number, got {hits[0][0]!r}"


def test_scanner_ignores_lookalike_strings() -> None:
    """Things that *look* like a PAT but aren't must not be flagged.

    Guards against an over-eager regex that would block legitimate
    `dop_v1_...` references in docs (e.g. a URL example).
    """
    safe_strings = [
        # Too short -- fewer than 56 hex chars after the prefix
        "dop_v1_short",
        "dop_v1_" + "a" * 55,

        # Non-hex char somewhere in the 56-char window -- breaks the
        # `[a-f0-9]{56}` run.
        "dop_v1_" + "Z" * 56,
        "dop_v1_" + "a" * 30 + "Z" + "a" * 30,  # broken in the middle

        # No `dop_v1_` prefix at all
        "the prefix is dop_v1_",

        # Wrong case on the prefix (only lowercase `dop_v1_` counts)
        "DOP_V1_" + "a" * 56,

        # URL fragment, not a real token
        "docs.digitalocean.com/dop_v1_xxx",
    ]
    for s in safe_strings:
        assert not DO_PAT_PATTERN.search(s), (
            f"scanner false-positively matched safe string: {s!r}"
        )


def test_scanner_detects_pat_embedded_in_larger_string() -> None:
    """A PAT surrounded by other text on the same line MUST be flagged.

    The scanner uses `re.search`, not a full-line match, so it has to
    find a PAT even when there's noise on either side of it.
    """
    with_string = f"export DIGITALOCEAN_ACCESS_TOKEN='{_make_fake_pat()}'"
    pattern = re.compile(re.escape(with_string))
    assert DO_PAT_PATTERN.search(with_string), (
        f"scanner missed an embedded PAT in: {with_string!r}"
    )
