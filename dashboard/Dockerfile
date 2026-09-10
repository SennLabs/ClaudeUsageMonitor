# --- build stage ---
FROM node:22-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
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
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget --spider -q http://localhost/api/summary || exit 1
