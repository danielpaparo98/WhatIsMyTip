"""Unit tests for ``backend/Dockerfile``.

These are pure-Python unit tests that validate the Dockerfile's
content — they do NOT require Docker (or any container runtime) to
be installed.  The goal is to pin the production-grade configuration
in source control so a regression cannot quietly relax it.

What we test (and why):

* ``uvicorn`` is launched with ``--proxy-headers`` (we want the
  trusted-proxy IP detection on) and a non-wildcard
  ``--forwarded-allow-ips`` value (we want the trust boundary
  closed to only the nginx reverse-proxy subnet).
* The forwarded-allow-ips value is env-var driven, with a
  default of ``10.0.0.0/8`` (the App Platform private network
  used by the nginx sidecar in our deployment).
* The CMD has been escaped correctly (single string) so the shell
  can interpolate ``${...}``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]   # backend/
DOCKERFILE = REPO_ROOT / "Dockerfile"


def _read_dockerfile() -> str:
    """Return the contents of ``backend/Dockerfile``.

    Skip the module if the file is missing (we don't want a missing
    file in the test environment to be reported as a failure).
    """
    if not DOCKERFILE.is_file():
        pytest.skip(f"Dockerfile not found at {DOCKERFILE}")
    return DOCKERFILE.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_cmd(dockerfile: str) -> str:
    """Return the literal string passed to the Dockerfile's ``CMD``.

    The Phase-4 Dockerfile uses a shell form so ``${PORT:-8000}`` is
    interpolated at container start.  We extract the
    ``exec uvicorn ...`` line so the tests can pin the uvicorn
    invocation.
    """
    match = re.search(
        r'CMD\s+\["sh"\s*,\s*"-c"\s*,\s*"([^"]+)"\s*\]',
        dockerfile,
        re.DOTALL,
    )
    if not match:
        pytest.fail(
            "Dockerfile does not contain a `CMD [\"sh\", \"-c\", \"...\"]` "
            "block; the test infrastructure assumes the shell-form CMD "
            "used in Phase 4+."
        )
    return match.group(1)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestUvicornForwardedAllowIps:
    """The uvicorn command MUST lock --forwarded-allow-ips to a private subnet."""

    def test_cmd_present(self):
        dockerfile = _read_dockerfile()
        cmd = _extract_cmd(dockerfile)
        # We only assert the basic shape here; the rest of the tests
        # pin specific properties of this string.
        assert "uvicorn" in cmd
        assert "--proxy-headers" in cmd

    def test_does_not_use_wildcard(self):
        """``--forwarded-allow-ips '*'`` is forbidden — it accepts forged
        ``X-Forwarded-For`` headers from ANY source.
        """
        dockerfile = _read_dockerfile()
        cmd = _extract_cmd(dockerfile)
        # Match both forms of the wildcard: '*' and "*".
        assert not re.search(r"--forwarded-allow-ips\s+['\"]?\*['\"]?", cmd), (
            "uvicorn is launched with `--forwarded-allow-ips '*'` which "
            "trusts X-Forwarded-For from ANY source. Lock it to the "
            "nginx reverse-proxy subnet instead (FORWARDED_ALLOW_IPS env "
            "var, default 10.0.0.0/8)."
        )

    def test_uses_env_var_with_safe_default(self):
        """The value MUST come from an env var with a private-subnet default."""
        dockerfile = _read_dockerfile()
        cmd = _extract_cmd(dockerfile)
        # We want:  --forwarded-allow-ips ${FORWARDED_ALLOW_IPS:-10.0.0.0/8}
        match = re.search(
            r"--forwarded-allow-ips\s+\$\{FORWARDED_ALLOW_IPS:-([^\s}]+)\}",
            cmd,
        )
        assert match, (
            "uvicorn is not using `${FORWARDED_ALLOW_IPS:-<default>}` for "
            "--forwarded-allow-ips. The env var lets ops override the "
            "trusted-proxy subnet per environment."
        )
        default_value = match.group(1)
        assert default_value == "10.0.0.0/8", (
            f"Default FORWARDED_ALLOW_IPS should be '10.0.0.0/8' (the App "
            f"Platform private network used by the nginx sidecar); got "
            f"{default_value!r}."
        )

    def test_default_is_private_subnet(self):
        """The default subnet MUST be RFC1918 private space — not 0.0.0.0/0."""
        dockerfile = _read_dockerfile()
        cmd = _extract_cmd(dockerfile)
        match = re.search(
            r"--forwarded-allow-ips\s+\$\{FORWARDED_ALLOW_IPS:-([^\s}]+)\}",
            cmd,
        )
        assert match, "FORWARDED_ALLOW_IPS env var not referenced"
        default = match.group(1)
        # 0.0.0.0/0 is the explicit "trust the entire internet" value;
        # a private-subnet default keeps the trust boundary closed.
        assert default != "0.0.0.0/0", (
            "Default FORWARDED_ALLOW_IPS is 0.0.0.0/0 — this is the "
            "wildcard. Use a private subnet like 10.0.0.0/8."
        )
        # Spot-check the RFC1918 ranges.
        assert re.match(
            r"^(10\.\d+\.\d+\.\d+/\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+/\d+|"
            r"192\.168\.\d+\.\d+/\d+)$",
            default,
        ), f"Default FORWARDED_ALLOW_IPS={default!r} is not in RFC1918 private space"


class TestDockerfileEnvVarInventory:
    """The Dockerfile should also declare the env var in the runtime stage.

    While not strictly required (the shell will still resolve the
    variable if it's set via ``docker run -e`` or compose
    ``environment:``), declaring it in the image's ENV keeps
    ``docker inspect`` honest about which knobs are available.
    """

    def test_forwarded_allow_ips_env_declared(self):
        dockerfile = _read_dockerfile()
        # Look for either an `ENV FORWARDED_ALLOW_IPS=...` line in the
        # runtime stage, or a `FORWARDED_ALLOW_IPS` mention in a
        # comment block alongside the CMD.
        env_declared = re.search(
            r"^ENV\s+FORWARDED_ALLOW_IPS=",
            dockerfile,
            re.MULTILINE,
        )
        assert env_declared, (
            "Dockerfile should declare `ENV FORWARDED_ALLOW_IPS=...` in "
            "the runtime stage so `docker inspect` shows the knob. "
            "(The shell ${FORWARDED_ALLOW_IPS:-...} still works without "
            "this, but a missing ENV is a UX regression.)"
        )
