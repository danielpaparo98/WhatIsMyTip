"""FastAPI application package.

Scaffolds the WhatIsMyTip API on top of FastAPI while keeping the existing
``packages.shared`` services intact.  Routers are added in Phase 2; this
package only contains the foundation (lifespan, middleware, security,
exception handling, /health).
"""
