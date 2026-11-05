# syntax=docker/dockerfile:1

FROM python:3.15.0-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0
WORKDIR /app

# Dependencies first, so source changes do not invalidate this layer.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev


FROM python:3.15.0-slim-bookworm
ARG APP_UID=10001
RUN groupadd --system --gid "$APP_UID" app \
    && useradd --system --uid "$APP_UID" --gid app --no-create-home app
WORKDIR /app
COPY --from=builder --chown=app:app /app /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
# Numeric, so the runtime can verify the user is not root without reading /etc/passwd.
USER $APP_UID:$APP_UID
CMD ["python", "-m", "oncall_rag.ingest"]
