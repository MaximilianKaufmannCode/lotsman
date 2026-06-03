# ─── Stage 1: build ─────────────────────────────────────────────────────────
FROM node:22-alpine AS builder

# Install pnpm
RUN corepack enable && corepack prepare pnpm@10 --activate

WORKDIR /app

# Install deps (frozen lockfile in CI)
COPY package.json pnpm-lock.yaml* ./
RUN pnpm install --frozen-lockfile

# Copy source and build
COPY . .
RUN pnpm build

# ─── Stage 2: serve ─────────────────────────────────────────────────────────
FROM nginx:1.27-alpine AS runner

# Remove default nginx config
RUN rm /etc/nginx/conf.d/default.conf

# Custom SPA config
COPY nginx.conf /etc/nginx/conf.d/lotsman.conf

# Copy built assets
COPY --from=builder /app/dist /usr/share/nginx/html

# Non-root user (nginx uses its own uid 101)
USER nginx

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
