# Phase 4 item 7: pinned as tag@sha256:digest at the version that ran before
# pinning. Node 22.23.4 bundles npm 10.9.9, the version package.json's
# packageManager names; CI's setup-node uses the same Node version, and
# scripts/check_pins.py fails CI if they differ.
FROM node:26.13.0-slim@sha256:5adad64f9a1fca2d51972caa65983634c8a774eb300d72b20f7f2a0f60368072

WORKDIR /app

COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci

COPY apps/web .

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
