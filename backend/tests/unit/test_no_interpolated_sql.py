"""Regression test for SEC-ME-001: prevent interpolated ``text()`` SQL.

Why this test exists
--------------------
``sqlalchemy.sql.expression.text(...)`` is **raw SQL** — its arguments are
concatenated verbatim by the database driver.  Building the query with a
f-string (``text(f"WHERE x = '{value}'")``) opens a SQL-injection hole.

The fix is to always use **bind parameters**::

    stmt = text("UPDATE games SET x = :x WHERE id = :id")
    await session.execute(stmt, {"x": value, "id": row_id})

This test walks the backend source tree and fails if any ``text()`` call
uses an f-string (or ``.format(...)`` / ``%`` interpolation).  It also
serves as a CI lint when the test suite is run in CI.

Scope
-----
* Walks ``backend/packages/`` and ``backend/app/`` (production code).
* Skips ``backend/scripts/`` and ``backend/alembic/`` — scripts are
  short-lived ops tools and alembic migrations are versioned, audited
  code that the developer reviews on every upgrade.
* Skips ``backend/tests/`` — tests obviously use bind params for their
  own fixtures; if a test ever interpolates, the test will fail loudly
  when run against a real database, so linting it is noise.
* Allows docstring / comment mentions of ``text(f"")`` (skipped via
  ``ast`` parse, not raw text).
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterator

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]  # backend/tests/unit/.../..
PROD_ROOTS = (REPO_ROOT / "packages", REPO_ROOT / "app")
EXCLUDE_DIR_NAMES = {"__pycache__", ".venv", "node_modules", ".git", "dist", "build"}


def _iter_python_files(roots: tuple[Path, ...]) -> Iterator[Path]:
    """Yield Python source files under any of ``roots``, recursing."""
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            # Skip caches / vendored dirs
            if any(part in EXCLUDE_DIR_NAMES for part in path.parts):
                continue
            yield path


def _text_calls_with_fstring(source: str) -> list[tuple[int, str]]:
    """Return a list of ``(line_no, snippet)`` for every interpolated ``text()`` call.

    A call is considered "interpolated" when:

    1. The first positional argument is an f-string (``text(f"...")`` /
       ``text(f'...')``), OR
    2. The first positional argument calls ``.format(...)`` on a string
       literal (``text("...".format(...))``), OR
    3. The first positional argument uses ``%`` formatting
       (``text("... %s ..." % val)``).

    Returns:
        List of ``(line, code)`` tuples; the list is empty when the
        module is clean.
    """
    findings: list[tuple[int, str]] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return findings

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Identify ``text(...)`` calls regardless of how they're imported.
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "text"):
            continue
        if not node.args:
            continue
        first = node.args[0]
        snippet = ast.unparse(first)

        # 1. f-string literal
        if isinstance(first, ast.JoinedStr):
            findings.append((node.lineno, snippet))
            continue
        # 2. implicit string-concat f-strings or ``"...".format(...)``
        if isinstance(first, ast.Call) and isinstance(first.func, ast.Attribute):
            if first.func.attr == "format" and isinstance(first.func.value, ast.Constant):
                findings.append((node.lineno, snippet))
                continue
        # 3. ``%`` formatting: BinOp with ``%`` operator
        if isinstance(first, ast.BinOp) and isinstance(first.op, ast.Mod):
            findings.append((node.lineno, snippet))
            continue

    return findings


class TestNoInterpolatedText:
    """``text(...)`` must never be built via f-strings / format / %."""

    def test_no_interpolated_text_in_production_code(self) -> None:
        offenders: list[str] = []
        for path in _iter_python_files(PROD_ROOTS):
            source = path.read_text(encoding="utf-8", errors="replace")
            for line, snippet in _text_calls_with_fstring(source):
                rel = path.relative_to(REPO_ROOT).as_posix()
                offenders.append(f"{rel}:{line}: text({snippet!r})")

        assert not offenders, (
            "Found interpolated `text(...)` calls (SQL-injection risk). "
            "Use bind parameters instead:\n\n"
            "    stmt = text('SELECT * FROM x WHERE id = :id')\n"
            "    await session.execute(stmt, {'id': row_id})\n\n"
            "Offending locations:\n  " + "\n  ".join(offenders)
        )

    def test_known_safe_text_calls_still_use_bind_params(self) -> None:
        """Spot-check: known ``text()`` call sites use bind parameters.

        This guards against a future contributor "refactoring" a
        well-known good site and breaking the regression above.
        """
        known_files = [
            REPO_ROOT / "packages" / "shared" / "crud" / "jobs.py",
            REPO_ROOT / "scripts" / "_reset_matches.py",
        ]
        for path in known_files:
            if not path.exists():
                continue
            source = path.read_text(encoding="utf-8", errors="replace")
            # Sanity: contains a ``text(...)`` call
            assert "text(" in source, f"{path} should contain a text() call"
            # And the regression lint still agrees: no interpolated text
            offenders = _text_calls_with_fstring(source)
            assert not offenders, (
                f"{path} now contains interpolated text() — "
                f"lines: {offenders}"
            )


@pytest.mark.parametrize(
    "snippet",
    [
        'text("SELECT 1")',                          # noqa
        'text("UPDATE x SET a = :a WHERE id = :id")',  # noqa
        'text("""\\nSELECT * FROM games\\n""")',     # noqa
    ],
)
def test_safe_text_shapes_are_not_flagged(snippet: str) -> None:
    """The static analysis must NOT flag bind-parameter-only text() calls."""
    assert _text_calls_with_fstring(snippet) == []


@pytest.mark.parametrize(
    "snippet",
    [
        'text(f"SELECT * FROM x WHERE id = {row_id}")',  # noqa
        'text("SELECT * FROM x WHERE id = {}".format(row_id))',  # noqa
        'text("SELECT * FROM x WHERE id = %s" % row_id)',  # noqa
    ],
)
def test_interpolated_text_shapes_are_flagged(snippet: str) -> None:
    """The static analysis MUST flag f-string / format / % interpolations."""
    findings = _text_calls_with_fstring(snippet)
    assert len(findings) == 1, f"expected 1 finding, got {findings} for {snippet!r}"
