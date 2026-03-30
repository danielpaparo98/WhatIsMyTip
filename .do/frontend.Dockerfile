FROM oven/bun:1 AS base

WORKDIR /app

# Install dependencies
FROM base AS install
COPY frontend/package.json frontend/bun.lockb* ./
RUN bun install --frozen-lockfile

# Build the application
FROM base AS build
COPY --from=install /app/node_modules ./node_modules
COPY frontend/ ./
RUN bun run generate

# Production image
FROM base AS production
COPY --from=build /app/.output ./
EXPOSE 3000
CMD ["node", "server/index.mjs"]
