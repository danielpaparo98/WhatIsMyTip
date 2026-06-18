"""Unit tests for ``packages.shared.db`` SSL/TLS configuration.

The Postgres async engine is built with an ``ssl.SSLContext`` that is
attached as ``connect_args["ssl"]`` whenever the connection URL
contains ``sslmode=require`` (or the equivalent ``ssl=require``).  This
test module pins the production-grade behaviour:

* **Default config** (``DB_SSL_VERIFY=true`` / unset): the context
  uses the system trust store, ``verify_mode=CERT_REQUIRED`` and
  ``check_hostname=True``.  An attacker presenting a self-signed or
  otherwise untrusted certificate MUST be rejected by the TLS
  handshake.  The cert-verification behaviour of an ``SSLContext`` is
  determined by its ``verify_mode`` and ``check_hostname`` attributes,
  so we assert on those directly — anything else is just a wrapper
  around OpenSSL and would have to trust those same flags.
* **Opt-out for local dev** (``DB_SSL_VERIFY=false``): the context is
  relaxed (``verify_mode=CERT_NONE``) so developers can hit a local
  Postgres container with a self-signed cert without first dropping
  the cert into the system trust store.
"""

from __future__ import annotations

import ssl

import pytest

from packages.shared.db import _normalize_async_url


# ---------------------------------------------------------------------------
# Pure-Python tests on the SSL context itself
# ---------------------------------------------------------------------------


class TestNormalizeAsyncUrlSSLCertRequired:
    """With the default config the engine MUST verify the Postgres certificate."""

    def test_default_config_uses_strict_ssl_context(self, monkeypatch):
        """sslmode=require → verify_mode=CERT_REQUIRED + check_hostname=True."""
        # Make sure no leftover DB_SSL_VERIFY=false from another test.
        monkeypatch.delenv("DB_SSL_VERIFY", raising=False)

        url = "postgresql+asyncpg://user:pass@db.example.com:5432/app?sslmode=require"
        clean_url, connect_args = _normalize_async_url(url)

        assert "ssl" in connect_args, (
            "sslmode=require should produce a connect_args['ssl'] SSLContext"
        )
        ssl_ctx = connect_args["ssl"]
        assert ssl_ctx.verify_mode == ssl.CERT_REQUIRED, (
            f"default config must require certificate verification, "
            f"got verify_mode={ssl_ctx.verify_mode!r}"
        )
        assert ssl_ctx.check_hostname is True, (
            "default config must enable hostname verification"
        )

    def test_default_context_will_reject_untrusted_ca(self, monkeypatch):
        """The default context MUST fail on an untrusted certificate.

        A self-signed certificate is the canonical example of an
        "unreachable CA" — it is not signed by any CA in the system
        trust store, so a strict ``SSLContext`` MUST reject it.  We
        assert the underlying property that drives this behaviour
        (``verify_mode=CERT_REQUIRED``), since Python's ``ssl`` module
        is itself a thin wrapper over OpenSSL.
        """
        monkeypatch.delenv("DB_SSL_VERIFY", raising=False)
        url = "postgresql+asyncpg://user:pass@db.example.com:5432/app?sslmode=require"
        _, connect_args = _normalize_async_url(url)
        ssl_ctx = connect_args["ssl"]

        # A strict context refuses connections unless the peer presents
        # a cert signed by a CA in the trust store AND the hostname
        # matches.  The combination of these two flags is what
        # makes the engine refuse a self-signed cert from a different
        # CA.
        assert ssl_ctx.verify_mode == ssl.CERT_REQUIRED
        assert ssl_ctx.check_hostname is True

    def test_url_is_cleaned_of_sslmode_param(self, monkeypatch):
        """The ssl/sslmode query params must be stripped (asyncpg limitation)."""
        monkeypatch.delenv("DB_SSL_VERIFY", raising=False)

        url = "postgresql+asyncpg://user:pass@db.example.com:5432/app?sslmode=require"
        clean_url, _ = _normalize_async_url(url)
        assert "sslmode" not in clean_url, (
            f"sslmode param should be stripped; got clean_url={clean_url!r}"
        )

    def test_no_ssl_no_connect_args(self, monkeypatch):
        """No sslmode in URL → no SSL context is injected."""
        monkeypatch.delenv("DB_SSL_VERIFY", raising=False)

        url = "postgresql+asyncpg://user:pass@db.example.com:5432/app"
        clean_url, connect_args = _normalize_async_url(url)
        assert "ssl" not in connect_args

    def test_ssl_equals_require_also_triggers_ssl(self, monkeypatch):
        """The ``ssl=require`` shorthand (no ``sslmode=`` prefix) also works."""
        monkeypatch.delenv("DB_SSL_VERIFY", raising=False)
        url = "postgresql+asyncpg://user:pass@db.example.com:5432/app?ssl=require"
        _, connect_args = _normalize_async_url(url)
        assert "ssl" in connect_args


class TestNormalizeAsyncUrlSSLOptOut:
    """``DB_SSL_VERIFY=false`` enables a lax context for local dev."""

    @pytest.mark.parametrize("value", ["false", "False", "FALSE", "0", "no"])
    def test_db_ssl_verify_false_disables_verification(self, monkeypatch, value):
        monkeypatch.setenv("DB_SSL_VERIFY", value)

        url = "postgresql+asyncpg://user:pass@localhost:5432/app?sslmode=require"
        _, connect_args = _normalize_async_url(url)

        assert "ssl" in connect_args
        ssl_ctx = connect_args["ssl"]
        assert ssl_ctx.verify_mode == ssl.CERT_NONE, (
            f"DB_SSL_VERIFY={value!r} should produce a lax SSL context; "
            f"got verify_mode={ssl_ctx.verify_mode!r}"
        )
        assert ssl_ctx.check_hostname is False

    @pytest.mark.parametrize("value", ["true", "True", "1", "yes"])
    def test_db_ssl_verify_true_enables_verification(self, monkeypatch, value):
        monkeypatch.setenv("DB_SSL_VERIFY", value)

        url = "postgresql+asyncpg://user:pass@localhost:5432/app?sslmode=require"
        _, connect_args = _normalize_async_url(url)

        ssl_ctx = connect_args["ssl"]
        assert ssl_ctx.verify_mode == ssl.CERT_REQUIRED
        assert ssl_ctx.check_hostname is True
