"""
Regression test: no high-value credentials may live in the repo.

The original incident (see git log on ``feature/remove-exposed-do-token``)
was that the diagnostic scripts ``check.py`` and ``get_logs.py`` had a
DigitalOcean PAT hardcoded and shipped it to GitHub.  That test was
specific to ``dop_v1_...`` and is now generalized (CR-006) into a
multi-pattern scanner that catches the most common forms of leaked
credentials:

* DigitalOcean PATs         (``dop_v1_<56 hex>``)
* OpenAI / OpenRouter keys  (``sk-or-...``)
* GitHub fine-grained PATs  (``github_pat_<82 alnum>``)
* GitHub classic PATs       (``ghp_<36 alnum>``)
* Slack tokens              (``xox[baprs]-...``)
* AWS access key IDs        (``AKIA<16 upper alnum>``)
* Vercel ``EV[...]``-style env snippets (used in the deploy script)

Excluded from the scan (we never want to false-positive on these):
- `.git/`        -- git's own bookkeeping, not project source.
- `.venv/`, `venv/`, `node_modules/`, `.pytest_cache/` -- vendored /
  generated / cache; never committed, and full of "looks-suspicious"
  hex blobs.
- `bun.lockb`    -- binary lockfile, not human-readable.
- The test file itself (its module docstring quotes the prefixes as
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
# The scanner iterates over ``SECRET_PATTERNS``; any single match on a
# line flags the file.  Each pattern is anchored on the specific
# prefix that real tokens use, so a URL example or env-var name
# (``dop_v1_...`` mentioned in a docstring, for example) won't trip a
# false positive.

SECRET_PATTERNS: list[re.Pattern[str]] = [
    # DigitalOcean PATs (original incident).
    re.compile(r"dop_v1_[a-f0-9]{56}"),
    # Vercel encrypted-env snippets (``EV[0]:base64:base64]``) used by
    # the deploy script to inject secrets at build time.
    re.compile(r"EV\[[0-9]+:[A-Za-z0-9+/=]+:[A-Za-z0-9+/=]+\]"),
    # OpenRouter / OpenAI project keys.
    re.compile(r"sk-or-[A-Za-z0-9_-]{20,}"),
    # GitHub classic PATs (``ghp_...``).
    re.compile(r"ghp_[A-Za-z0-9]{36}"),
    # GitHub fine-grained PATs (``github_pat_<82 chars>``).
    re.compile(r"github_pat_[A-Za-z0-9_]{82}"),
    # Slack bot/app/user/legacy tokens.
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    # AWS access key IDs.
    re.compile(r"AKIA[0-9A-Z]{16}"),
]

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
# itself quotes the prefixes in its module docstring as documentation.
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

    Skips directories and files we can't ``stat()`` (e.g. Windows
    reparse points under ``node_modules/.bin/`` which are file
    symlinks the OS refuses to read).
    """
    found: list[Path] = []
    for path in root.rglob("*"):
        try:
            if not path.is_file():
                continue
        except OSError:
            # Unreadable / reparse point -- treat as not a regular file.
            continue
        if _is_excluded_dir(path):
            continue
        if path.name in EXCLUDED_FILE_NAMES:
            continue
        if path.suffix.lower() not in SCAN_EXTENSIONS:
            continue
        found.append(path)
    return found


def _redact(line: str) -> str:
    """Replace any token-shaped match in `line` with a redacted form.

    Used only when building the failure message so the test output is
    safe to paste into a public issue tracker.
    """
    redacted = line
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub("<REDACTED>", redacted)
    return redacted


def _scan_for_tokens(path: Path) -> list[tuple[int, str]]:
    """Return `[(line_no, line), ...]` for every secret-shaped match in `path`.

    Reads the file as UTF-8 with `errors='replace'` so a stray
    non-UTF-8 byte in a config file doesn't crash the scan.  A line is
    reported if ANY pattern in ``SECRET_PATTERNS`` matches it.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    hits: list[tuple[int, str]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for pattern in SECRET_PATTERNS:
            if pattern.search(line):
                hits.append((line_no, line))
                break
    return hits


# ── The actual test ─────────────────────────────────────────────────────


def test_no_hardcoded_secrets() -> None:
    """Fail if any tracked-source file contains a leaked credential."""
    root = _repo_root()
    files = _iter_scannable_files(root)
    assert files, (
        f"Scan found no scannable files under {root} -- "
        "the path resolution is probably wrong."
    )

    offenders: list[str] = []
    for path in files:
        for line_no, line in _scan_for_tokens(path):
            offenders.append(
                f"{path.relative_to(root)}:{line_no}: {_redact(line).strip()}"
            )

    assert not offenders, (
        "Hardcoded credential(s) found in the repo.  Secrets must NEVER "
        "be committed — load them at runtime via environment variables.  "
        "If a leaked token has been pushed, rotate it in the issuing "
        "service's console BEFORE removing it from the tree.\n\n"
        f"Offending lines ({len(offenders)}):\n  " + "\n  ".join(offenders)
    )


# ── Sanity checks: the scanner actually works ───────────────────────────
#
# Without these, an accidentally-over-broad exclude list could silently
# make the test pass on a polluted repo.  These cases verify the regex
# both detects real tokens and doesn't false-positive on lookalikes.
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


def test_scanner_detects_github_pat(tmp_path: Path) -> None:
    """A `ghp_...` classic GitHub PAT MUST be flagged."""
    fake = tmp_path / "leaked.py"
    fake.write_text(
        "GITHUB_TOKEN = 'ghp_" + "a" * 36 + "'\n",
        encoding="utf-8",
    )
    hits = _scan_for_tokens(fake)
    assert len(hits) == 1, f"scanner missed a GitHub PAT, got hits={hits!r}"


def test_scanner_detects_aws_access_key(tmp_path: Path) -> None:
    """An `AKIA...` AWS access key id MUST be flagged."""
    fake = tmp_path / "leaked.py"
    fake.write_text(
        "AWS_KEY = 'AKIA" + "A" * 16 + "'\n",
        encoding="utf-8",
    )
    hits = _scan_for_tokens(fake)
    assert len(hits) == 1, f"scanner missed an AWS key, got hits={hits!r}"


def test_scanner_detects_vercel_ev(tmp_path: Path) -> None:
    """A Vercel ``EV[...]`` env snippet MUST be flagged."""
    fake = tmp_path / "leaked.sh"
    fake.write_text(
        "echo 'EV[0:c2VjcmV0aGVyZT0xMjM0NTY3ODk=:YWJjZGVmZ2hpamtsbW5vcA==]'\n",
        encoding="utf-8",
    )
    hits = _scan_for_tokens(fake)
    assert len(hits) == 1, f"scanner missed a Vercel EV snippet, got hits={hits!r}"


def test_scanner_ignores_lookalike_strings() -> None:
    """Things that *look* like a token but aren't must not be flagged.

    Guards against an over-eager regex that would block legitimate
    prefix mentions in docs (e.g. a URL example).
    """
    safe_strings = [
        # DO PAT: too short / broken / wrong case.
        "dop_v1_short",
        "dop_v1_" + "a" * 55,
        "dop_v1_" + "Z" * 56,
        "dop_v1_" + "a" * 30 + "Z" + "a" * 30,
        "the prefix is dop_v1_",
        "DOP_V1_" + "a" * 56,
        "docs.digitalocean.com/dop_v1_xxx",
        # GitHub PAT: too short.
        "ghp_" + "a" * 35,
        # AWS key: too short (regex needs 16 chars after AKIA).
        "AKIA" + "A" * 15,
        # AWS key: lowercase in the 16-char window breaks the [0-9A-Z] run.
        "AKIA" + "a" + "A" * 15,
        # Vercel EV: malformed brackets.
        "EV[abc:def:ghi]",
        # Slack token: too short.
        "xoxb-abc",
    ]
    for s in safe_strings:
        matched = any(p.search(s) for p in SECRET_PATTERNS)
        assert not matched, (
            f"scanner false-positively matched safe string: {s!r}"
        )


def test_scanner_detects_pat_embedded_in_larger_string() -> None:
    """A PAT surrounded by other text on the same line MUST be flagged.

    The scanner uses ``re.search`` on each pattern, not a full-line
    match, so it has to find a token even when there's noise on either
    side of it.
    """
    with_string = f"export DIGITALOCEAN_ACCESS_TOKEN='{_make_fake_pat()}'"
    matched = any(p.search(with_string) for p in SECRET_PATTERNS)
    assert matched, (
        f"scanner missed an embedded PAT in: {with_string!r}"
    )
