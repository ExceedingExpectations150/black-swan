# Black Swan frontend — Next.js 14 (standalone output).
# Multi-stage: build with the full toolchain, ship a minimal runtime image.
# NEXT_PUBLIC_* are inlined at BUILD time (the browser reads them), so the
# backend URL the browser will hit is baked in here via build args.

# ---- build ----
FROM node:20-alpine AS builder
WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm ci

COPY . .

# Where the browser reaches the backend. Defaults assume the compose setup
# (backend published on host :8010). Override for other deployments.
ARG NEXT_PUBLIC_API_BASE=http://localhost:8010
ARG NEXT_PUBLIC_WS_URL=ws://localhost:8010/ws
ENV NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE
ENV NEXT_PUBLIC_WS_URL=$NEXT_PUBLIC_WS_URL
RUN npm run build

# ---- runtime ----
FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV PORT=5055
ENV HOSTNAME=0.0.0.0

# Standalone output does NOT bundle public/ or the static chunks — copy both.
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static

EXPOSE 5055
CMD ["node", "server.js"]
