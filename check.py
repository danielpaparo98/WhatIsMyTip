"""Quick diagnostic: dump the live spec for the whatismytip App Platform app.

Run with:

    $env:DIGITALOCEAN_ACCESS_TOKEN = 'dop_v1_...'
    python check.py [<APP_ID>]

If APP_ID is omitted, uses the production app `ecc5a2a0-917b-4659-add6-e558c2d00902`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

APP_ID = "ecc5a2a0-917b-4659-add6-e558c2d00902"


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
            "    python check.py\n"
        )


def main() -> None:
    _authenticate()
    app_id = sys.argv[1] if len(sys.argv) > 1 else APP_ID

    r = subprocess.run(
        ["doctl", "apps", "spec", "get", app_id, "--format", "json"],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        sys.exit(f"doctl failed (exit {r.returncode}):\n{r.stderr}")

    d = json.loads(r.stdout)
    print("=== TOP KEYS ===")
    print(list(d.keys()))
    print()
    print("=== INGRESS ===")
    print(json.dumps(d.get("ingress"), indent=2))
    print()
    print("=== FUNCTIONS (count and names) ===")
    fns = d.get("functions", [])
    print(f"count: {len(fns)}")
    for f in fns:
        print(f"  - name: {f.get('name'):<30} source_dir: {f.get('source_dir')}")
    print()
    print("=== ACTIVE DEPLOYMENT ===")
    ad = d.get("active_deployment", {})
    print(json.dumps(ad, indent=2)[:500])


if __name__ == "__main__":
    main()
