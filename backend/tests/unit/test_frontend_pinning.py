"""Tests for SEC-LO-004: pin frontend production runtime deps to exact versions.

``package.json`` ``^`` ranges let a fresh ``bun install`` pull in newer
minor/patch versions, which is fine for dev tools but a supply-chain
risk for production runtime dependencies.  A malicious patch release
of ``chart.js`` or ``nuxt`` would be silently picked up the next
time someone runs ``bun install`` without first regenerating the
lockfile.

The fix: pin the production runtime deps (``dependencies`` block)
to exact versions.  Dev tooling (``devDependencies``) can stay on
``^`` ranges because it's not shipped to users.

This test is a static check of ``frontend/package.json`` and runs
in the Python test suite (which is what the CI executes).  It
re-reads the file on every run so version bumps don't require
re-touching this test.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]  # backend/tests/unit/...
FRONTEND_PKG = REPO_ROOT.parent / "frontend" / "package.json"


def _load_frontend_package() -> dict:
    if not FRONTEND_PKG.is_file():
        pytest.skip(f"frontend/package.json not found at {FRONTEND_PKG}")
    return json.loads(FRONTEND_PKG.read_text(encoding="utf-8"))


# SemVer is ``major.minor.patch[-prerelease][+build]``.  We accept the
# ``v`` prefix optionally and disallow any range / wildcard characters.
_EXACT_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?$")


class TestProductionDepsPinned:
    """``dependencies`` must use exact versions, not ``^x.y.z``."""

    @pytest.fixture(scope="class")
    def pkg(self) -> dict:
        return _load_frontend_package()

    def test_dependencies_block_exists(self, pkg) -> None:
        assert "dependencies" in pkg, "frontend/package.json must have a `dependencies` block"

    @pytest.mark.parametrize(
        "dep_name, version",
        [
            ("nuxt", _load_frontend_package().get("dependencies", {}).get("nuxt")),
            ("chart.js", _load_frontend_package().get("dependencies", {}).get("chart.js")),
            (
                "vue-chartjs",
                _load_frontend_package().get("dependencies", {}).get("vue-chartjs"),
            ),
            (
                "@nuxtjs/tailwindcss",
                _load_frontend_package().get("dependencies", {}).get("@nuxtjs/tailwindcss"),
            ),
        ],
    )
    def test_production_dep_pinned(self, dep_name: str, version: str | None) -> None:
        """Each production dep must be pinned to an exact version.

        A ``^x.y.z`` range lets ``bun install`` pick up newer minor /
        patch releases without a lockfile refresh — exactly the supply
        chain footgun SEC-LO-004 is closing.  An exact version forces
        every developer / CI to regenerate the lockfile to upgrade.
        """
        assert version is not None, f"production dep {dep_name!r} missing from dependencies"

        assert not version.startswith("^"), (
            f"SEC-LO-004: production dep {dep_name!r} is a caret range "
            f"({version!r}).  Pin to an exact version (e.g. `4.4.2`) to "
            "prevent silent upgrades via `bun install`."
        )
        assert not version.startswith("~"), (
            f"SEC-LO-004: production dep {dep_name!r} is a tilde range "
            f"({version!r}).  Pin to an exact version."
        )
        assert not version.startswith(">"), (
            f"SEC-LO-004: production dep {dep_name!r} uses a range "
            f"({version!r}).  Pin to an exact version."
        )
        assert _EXACT_VERSION_RE.match(version), (
            f"SEC-LO-004: production dep {dep_name!r} has version "
            f"{version!r}, which is not an exact semver.  Pin to "
            "`major.minor.patch`."
        )

    def test_no_wildcard_or_x_range_in_dependencies(self, pkg) -> None:
        """No ``*`` or ``x`` ranges in production deps."""
        for name, version in pkg.get("dependencies", {}).items():
            assert "*" not in version, (
                f"SEC-LO-004: production dep {name!r} uses a wildcard "
                f"version ({version!r})"
            )
            assert "x" not in version.split(".") or version.count("x") == 0, (
                f"SEC-LO-004: production dep {name!r} uses an 'x' range "
                f"({version!r})"
            )


class TestDevDepsStillFlexible:
    """``devDependencies`` may stay on caret ranges (dev tooling only)."""

    def test_dev_dependencies_can_use_caret(self) -> None:
        pkg = _load_frontend_package()
        # This test is intentionally permissive: it asserts that the
        # fix did NOT over-reach into devDependencies.  If a future
        # contributor wants to pin devDeps too, they can; but
        # SEC-LO-004 only requires the production runtime deps to be
        # pinned.
        for _name, version in pkg.get("devDependencies", {}).items():
            # No assertion — just a structural sanity check.
            assert isinstance(version, str)


class TestLockfilePresent:
    """The binary lockfile ``bun.lockb`` must be present so the
    pinned versions in ``package.json`` are actually used."""

    def test_bun_lockfile_present(self) -> None:
        lockfile = REPO_ROOT.parent / "frontend" / "bun.lockb"
        assert lockfile.is_file(), (
            f"frontend/bun.lockb must be committed so the pinned "
            f"versions in package.json are used (without it `bun "
            f"install` will resolve versions again, which can still "
            f"pull from `^` ranges if a future maintainer re-introduces "
            f"them).  Missing: {lockfile}"
        )
