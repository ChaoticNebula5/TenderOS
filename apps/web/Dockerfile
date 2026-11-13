# Plan item 27 (decision 0085): the web app as a static build served by nginx.
# Three stages; the last (runtime) is the default target, so compose and CI's
# build-push-action build it with no target set.
#
# Phase 4 item 7: every image is pinned as tag@sha256:digest. Node 26.13.0
# (LTS "Lithium") bundles npm 11.20.0, the version both package.json files'
# packageManager names; CI's setup-node uses the same Node version, and
# scripts/check_pins.py fails CI if they differ.

# build: install from the lockfile and produce dist/. VITE_API_BASE_URL is
# baked into the bundle at build time: /api, the same origin, proxied by nginx.
FROM node:26.13.0-slim@sha256:5adad64f9a1fca2d51972caa65983634c8a774eb300d72b20f7f2a0f60368072 AS build
WORKDIR /app
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci
COPY apps/web .
ARG VITE_API_BASE_URL=/api
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build

# dev: the Vite dev server with hot reload, used only by docker-compose.dev.yml
# (which bind-mounts the sources over /app). It listens on 8080 like the
# runtime image, so the base compose file's port mapping serves both.
FROM node:26.13.0-slim@sha256:5adad64f9a1fca2d51972caa65983634c8a774eb300d72b20f7f2a0f60368072 AS dev
WORKDIR /app
COPY --from=build /app /app
ENV VITE_API_BASE_URL=/api
EXPOSE 8080
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "8080"]

# runtime: nginx and the built files only; no Node, no node_modules, no Vite.
# The unprivileged image runs nginx as uid 101 on port 8080 (the official one
# runs its master process as root).
FROM nginxinc/nginx-unprivileged:1.31.7-alpine@sha256:b5047b9b76845eac124358373c6852377c52e4b05834a47647e955dd91cb8f1e AS runtime
COPY apps/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY apps/web/proxy_headers.inc /etc/nginx/conf.d/proxy_headers.inc
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
