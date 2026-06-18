"""Fetch the build log for a specific deployment of the whatismytip app.

Run with:

    $env:DIGITALOCEAN_ACCESS_TOKEN = 'dop_v1_...'
    python get_logs.py [<APP_ID> [<DEPLOYMENT_ID>]]

Defaults to the most recent successful production build on
APP_ID `ecc5a2a0-917b-4659-add6-e558c2d00902` /
DEPLOYMENT_ID `862395c7-5a3c-47ab-a99a-c73e71318291`.
"""

from __future__ import annotations

import os
import subprocess
import sys

APP_ID = "ecc5a2a0-917b-4659-add6-e558c2d00902"
DEPLOYMENT_ID = "862395c7-5a3c-47ab-a99a-c73e71318291"


def _authenticate() -> None:
    """Set the `doctl` access token from the DIGITALOCEAN_ACCESS_TOKEN env var.

    `doctl` itself checks this env var before its on-disk config file, so
    we don't need to write to the config -- we just need to forward the
    env var to the subprocess (which `subprocess.run` does by default).
    """
    if not os.environ.get("DIGITALOCEAN_ACCESS_TOKEN"):
        sys.exit(
            "DIGITALOCEAN_ACCESS_TOKEN env var is not set. "
            "Generate a token at https://cloud.digitalocean.com/account/api/tokens "
            "and re-run with:\n\n"
            "    $env:DIGITALOCEAN_ACCESS_TOKEN = 'dop_v1_...'\n"
            "    python get_logs.py\n"
        )


def main() -> None:
    _authenticate()
    app_id = sys.argv[1] if len(sys.argv) > 1 else APP_ID
    deployment_id = sys.argv[2] if len(sys.argv) > 2 else DEPLOYMENT_ID

    r = subprocess.run(
        [
            "doctl", "apps", "logs", app_id,
            "--deployment", deployment_id,
            "--type", "build",
        ],
        capture_output=True,
    )
    if r.returncode != 0:
        sys.exit(f"doctl failed (exit {r.returncode}):\n{r.stderr.decode('utf-8', errors='ignore')}")

    # Write raw bytes to file
    with open("build_log_raw.bin", "wb") as f:
        f.write(r.stdout)
        f.write(b"\n--- STDERR ---\n")
        f.write(r.stderr)
    print("stdout size:", len(r.stdout))
    print("stderr size:", len(r.stderr))
    # Print first portion of stdout, ignoring errors
    print("--- STDOUT (first 4000 chars, errors=ignore) ---")
    print(r.stdout.decode("utf-8", errors="ignore")[:4000])
    print("--- STDERR (first 1500 chars, errors=ignore) ---")
    print(r.stderr.decode("utf-8", errors="ignore")[:1500])


if __name__ == "__main__":
    main()
