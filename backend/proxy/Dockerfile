# ──────────────────────────────────────────────────────────────────────
# whatismytip-proxy: thin nginx reverse proxy in front of the function
# component.  Forwards `/api/...` (incoming from the public ingress) to
# `http://${FUNCTION_HOST}:${FUNCTION_PORT}/api/api/...` (the function
# gateway) so the OpenWhisk runtime sees the `/api/<package>/<action>`
# path it expects.
# ──────────────────────────────────────────────────────────────────────
FROM nginx:1.27-alpine

# Drop the default site config and install ours.
RUN rm /etc/nginx/conf.d/default.conf

# Templated config: substitute ${FUNCTION_HOST} / ${FUNCTION_PORT} at
# container start via `envsubst` so the same image works in any
# environment (staging, prod, local docker-compose, etc.).
COPY nginx.conf /etc/nginx/templates/default.conf.template

# `nginx -t` validates the config at startup if we let it crash, so
# we add a simple healthcheck that hits our own `/api/games/health`
# proxy endpoint (which transitively checks the function gateway).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD wget --quiet --tries=1 --spider \
        http://127.0.0.1:8080/api/games/health || exit 1

EXPOSE 8080
