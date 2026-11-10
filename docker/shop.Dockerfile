# syntax=docker/dockerfile:1
FROM node:24-alpine AS js
WORKDIR /t
COPY services/shop/telemetry/telemetry.ts .
RUN npx --yes esbuild@0.25.0 telemetry.ts --bundle --minify --target=es2020 --outfile=telemetry.js

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS build
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
COPY packages packages
COPY services/shop services/shop
COPY services/engine/pyproject.toml services/engine/pyproject.toml
COPY --from=js /t/telemetry.js services/shop/src/gandalf_shop/static/telemetry.js
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package gandalf-shop

FROM python:3.13-slim-bookworm
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
USER 65534
EXPOSE 8080
# --forwarded-allow-ips is NOT set: the app reads the proxy headers itself, explicitly.
CMD ["uvicorn", "gandalf_shop.app:app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
