# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS build
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
COPY packages packages
COPY services/engine services/engine
COPY services/shop/pyproject.toml services/shop/pyproject.toml
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package gandalf-engine

FROM python:3.13-slim-bookworm
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
USER 65534
EXPOSE 8000
# One worker on purpose: the FAISS index (phase 4) lives in-process.
CMD ["uvicorn", "gandalf_engine.app:app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
