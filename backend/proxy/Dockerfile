# syntax=docker/dockerfile:1.7
# ──────────────────────────────────────────────────────────────────────
# whatismytip-proxy: thin nginx reverse proxy in front of the FastAPI
# app container.  Forwards all requests (with the standard reverse-
# proxy headers) to the FastAPI component over the App Platform
# private network.
# ──────────────────────────────────────────────────────────────────────
FROM nginx:1.27-alpine@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10

# wget is not in the alpine base; we need it for the HEALTHCHECK.
# curl is included for ad-hoc debugging in the image.  --no-cache
# keeps the layer small; --no-install-recommends is apk's default.
RUN apk add --no-cache wget curl

# Drop the default site config and install ours.
RUN rm /etc/nginx/conf.d/default.conf

# The nginx config now references `fastapi:8000` directly via App
# Platform's internal DNS, so no envsubst is needed at container
# start — the image is fully self-contained.
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Validate the config at build time so a bad config fails the build
# rather than the deploy.
RUN nginx -t

# `nginx -t` validates the config at startup if we let it crash, so
# we add a simple healthcheck that hits our own `/healthz` endpoint
# (which is served by nginx itself and does NOT depend on FastAPI).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget --quiet --tries=1 --spider \
        http://127.0.0.1:8080/healthz || exit 1

EXPOSE 8080

# Drop the master to a non-root user.  nginx 1.27-alpine ships a
# `nginx` user (uid/gid 101) and the default config sets `user nginx;`
# so workers run unprivileged; the master still binds 8080 as root,
# which is fine for non-privileged ports.
USER nginx
