# --- build stage ---
FROM node:22-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# --- serve stage ---
FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
# Templated so INGEST_AUTH_TOKEN can be injected into the proxied request at
# container start (envsubst runs automatically via the base image's
# /docker-entrypoint.d/ scripts for anything under /etc/nginx/templates/).
COPY nginx.conf.template /etc/nginx/templates/default.conf.template

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget --spider -q http://localhost/ || exit 1
