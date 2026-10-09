# --- build stage ---
FROM node:22-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# vite.config.ts reads ../VERSION, i.e. /VERSION here. From the repo root via
# additional_contexts in docker-compose.yml.
COPY --from=repo VERSION /VERSION
RUN npm run build

# --- serve stage ---
FROM nginx:1.29-alpine
COPY --from=build /app/dist /usr/share/nginx/html
# Templated so INGEST_AUTH_TOKEN can be injected into the proxied request at
# container start (envsubst runs automatically via the base image's
# /docker-entrypoint.d/ scripts for anything under /etc/nginx/templates/).
COPY nginx.conf.template /etc/nginx/templates/default.conf.template

EXPOSE 80

# Checks the proxy path, not just the static files. Fetching / only proved
# nginx was up: with ingest down or the token mismatched, every /api/ call
# 401s or 502s while the container still reported healthy.
# 127.0.0.1, not localhost: busybox wget resolves localhost to ::1 and nginx
# listens on IPv4 only, so the check was refused and never went healthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget --spider -q http://127.0.0.1/api/summary || exit 1
