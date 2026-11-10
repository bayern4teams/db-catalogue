# One image per package (report Part 3.2): build with `scripts/build_images.py`, which passes
# PACKAGE and EXTRAS. Images are built on a machine with internet and moved to the LAN host as a
# `docker save` tarball (owner decision D6), so nothing here runs on the host.

# Digest-pinned so a rebuild uses the same bytes; bump both together with the CI uv pin.
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.19@sha256:04d046b13e60d6bcec73cbc5e1cad25d680dea90c8573340950a0ac2d1aef424

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ARG PACKAGE
ARG EXTRAS=""
# The venv is built at its final path: console scripts carry its interpreter path.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /src
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY packages ./packages
# The locked versions only, without dev tools, installed (not linked) into one venv.
RUN uv sync --locked --no-dev --no-editable --package "${PACKAGE}" ${EXTRAS}

FROM ${PYTHON_IMAGE}
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin dbig \
    # A new named volume copies these owners, so uid 10001 can write its own state there.
    && mkdir -p /data/bundles /data/snapshots /var/lib/dbig/state \
    && chown -R 10001:10001 /data /var/lib/dbig
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:${PATH} PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001
WORKDIR /var/lib/dbig
