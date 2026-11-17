# journal-service image, built for rootless Podman: podman build -t journal-service .
# Runtime settings (user mapping, mounts, no network) live in deploy/journal-service.container.

ARG PYTHON_IMAGE=docker.io/library/python:3.14-slim
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.9

FROM ${UV_IMAGE} AS uv

# -- build: resolve the locked dependencies and install the project into /opt/venv ---------
FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src

# Dependencies first: this layer is reused until pyproject.toml or uv.lock change.
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

# Then the project itself, as a regular (non-editable) install: /src is not needed at runtime.
COPY README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# -- runtime -------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE}

# Fixed non-root user. The Quadlet unit maps uid 10001 to your own host user (keep-id), so
# files in the bind-mounted XDG directories stay owned by you.
RUN groupadd --system --gid 10001 journal \
 && useradd --system --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent \
        --shell /usr/sbin/nologin journal \
 && install -d -m 0700 -o 10001 -g 10001 \
        /xdg/config/journal-service /xdg/data/journal-service /xdg/state/journal-service \
        /xdg/cache/journal-service /xdg/runtime/journal-service

COPY --from=build /opt/venv /opt/venv

# The XDG variables point at the mount points, so the app resolves the same layout as on the
# host: $XDG_DATA_HOME/journal-service/journal.db and so on.
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    XDG_CONFIG_HOME=/xdg/config \
    XDG_DATA_HOME=/xdg/data \
    XDG_STATE_HOME=/xdg/state \
    XDG_CACHE_HOME=/xdg/cache \
    XDG_RUNTIME_DIR=/xdg/runtime

USER 10001:10001
ENTRYPOINT ["journal-service"]
CMD ["serve"]
