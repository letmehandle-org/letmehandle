# Multi-stage so that the image that runs in production contains no build toolchain, no
# package manager, and no source it does not need. Smaller is a side effect; the point is that
# what is not in the image cannot be exploited in it.

FROM python:3.14-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first, as their own layer. They change far less often than the source, so a
# code change does not reinstall the world.
COPY apps/backend/pyproject.toml apps/backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY apps/backend/src ./src
COPY apps/backend/README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


FROM python:3.14-slim AS runtime

# A named, unprivileged user. Root in a container is still root against a kernel escape.
RUN groupadd --system --gid 1001 app \
    && useradd --system --uid 1001 --gid app --home /app --no-create-home app

WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/src /app/src

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER app
EXPOSE 8000

# Liveness, not readiness: this answers whether the process should be restarted, and a
# database outage is not a reason to restart a healthy application.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"

# The project's own entry point, not a raw uvicorn invocation: it validates configuration
# before binding a port, and it hands uvicorn log_config=None so that structlog owns logging
# rather than competing with uvicorn's own dictConfig.
CMD ["letmehandle"]
