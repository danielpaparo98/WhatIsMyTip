# ──────────────────────────────────────────────────────────────────────
# whatismytip-proxy: thin nginx reverse proxy in front of the FastAPI
# app container.  Forwards all requests (with the standard reverse-
# proxy headers) to the FastAPI component over the App Platform
# private network.
# ──────────────────────────────────────────────────────────────────────
FROM nginx:1.27-alpine

# Drop the default site config and install ours.
RUN rm /etc/nginx/conf.d/default.conf

# The nginx config now references `fastapi:8000` directly via App
# Platform's internal DNS, so no envsubst is needed at container
# start — the image is fully self-contained.
COPY nginx.conf /etc/nginx/conf.d/default.conf

# `nginx -t` validates the config at startup if we let it crash, so
# we add a simple healthcheck that hits our own `/healthz` endpoint
# (which is served by nginx itself and does NOT depend on FastAPI).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget --quiet --tries=1 --spider \
        http://127.0.0.1:8080/healthz || exit 1

EXPOSE 8080
