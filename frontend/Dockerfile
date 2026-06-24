# syntax=docker/dockerfile:1

# ---- deps: install node_modules from lockfile ----
FROM node:22-alpine AS deps
# Next.js SWC binaries need libc compat on alpine
RUN apk add --no-cache libc6-compat
WORKDIR /app
COPY package.json package-lock.json ./
# 该项目存在 peer 依赖冲突,装包需 --legacy-peer-deps(与本地 npm install 一致)
RUN npm ci --legacy-peer-deps

# ---- builder: compile the Next.js standalone bundle ----
FROM node:22-alpine AS builder
WORKDIR /app
# NEXT_PUBLIC_* is inlined into the browser bundle at BUILD time. Empty default =
# same-origin relative "/api/..." calls, which the nginx edge proxy routes to the
# backend (no CORS). Override only if the frontend is served without the proxy.
ARG NEXT_PUBLIC_API_URL=
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

# ---- runner: minimal runtime image ----
FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
RUN addgroup -g 1001 -S nodejs && adduser -S nextjs -u 1001
COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
USER nextjs
EXPOSE 3000
ENV PORT=3000
ENV HOSTNAME=0.0.0.0
CMD ["node", "server.js"]
