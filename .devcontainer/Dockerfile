# Dev container image for Ook.
#
# Project-only: a Python 3.14 + uv toolchain matching pyproject.toml's
# requires-python (>=3.14) and the production Dockerfile's base image. Stoker
# derives its sandbox from this image and layers its own concerns (firewall,
# repo clone, harness, commit signing) separately, so this file stays free of
# those — no firewall, sudo, gh, or signing config, and no COPY of repo files.

FROM mcr.microsoft.com/devcontainers/python:1-3.14-bookworm

# System packages needed to build dependencies that lack wheels. libpq-dev
# provides pg_config for building psycopg2 (the source dist in the `nox`
# dependency group), used by the Postgres testcontainer in the test suite.
# git and build-essential (which provides make) already ship with the base
# image.
RUN apt-get update \
    && export DEBIAN_FRONTEND=noninteractive \
    && apt-get -y install --no-install-recommends libpq-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install uv, pinned to match the production Dockerfile.
COPY --from=ghcr.io/astral-sh/uv:0.11.21 /uv /uvx /bin/
