# ──────────────────────────────────────────────────────────────────────
# whatismytip-proxy: thin nginx reverse proxy that adds the extra
# `/api/` prefix the App Platform functions component (Apache
# OpenWhisk) expects on its action URLs.
#
# The proxy receives `/api/<name>/<rest>` from the public App Platform
# ingress and forwards to
# `https://${FUNCTION_HOST}/api/api/<name>/<rest>` — the public DO
# Functions gateway, which routes by host.  The OpenWhisk runtime
# then resolves `/api/api/<name>/<rest>` to the `<name>` action in
# the `api` package.
#
# Why HTTPS to the public FaaS gateway (not the App Platform private
# network): the `functions` component is a *serverless* (OpenWhisk)
# component, not a regular service, so it has no private hostname on
# the App Platform private network.  The public gateway is the only
# way to reach it.
# ──────────────────────────────────────────────────────────────────────
FROM nginx:1.27-alpine

# Drop the default site config and install ours.
RUN rm /etc/nginx/conf.d/default.conf

# Templated config: substitute ${FUNCTION_HOST} / ${FUNCTION_PORT} at
# container start via `envsubst` so the same image works in any
# environment (staging, prod, local docker-compose, etc.).
COPY nginx.conf /etc/nginx/templates/default.conf.template

# `nginx -t` validates the config at startup if we let it crash, so
# we add a simple healthcheck that hits our own `/healthz` proxy
# endpoint (which does NOT go upstream, so it works even if the
# FaaS gateway is down).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD wget --quiet --tries=1 --spider \
        http://127.0.0.1:8080/healthz || exit 1

EXPOSE 8080
