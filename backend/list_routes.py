"""Quick script to print FastAPI routes for verification."""
import sys

try:
    from main import app
except Exception as exc:
    print(f"IMPORT FAILED: {exc!r}", file=sys.stderr)
    raise

print(f"TOTAL routes: {len(app.routes)}", file=sys.stderr)
for r in app.routes:
    if hasattr(r, "methods") and r.methods:
        methods = ",".join(sorted(r.methods - {"HEAD"}))
        print(f"  {methods:10s} {r.path}")
    elif type(r).__name__ == "_IncludedRouter":
        # Drill into the included router to surface its routes
        prefix = r.include_context.prefix
        print(f"  --- included router with prefix={prefix!r} ---")
        for sub in r.original_router.routes:
            if hasattr(sub, "methods") and sub.methods:
                methods = ",".join(sorted(sub.methods - {"HEAD"}))
                full = prefix + sub.path
                print(f"  {methods:10s} {full}")
    else:
        print(f"  [?] {r!r}")
