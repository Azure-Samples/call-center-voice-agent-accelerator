###############
# Build stage #
###############
FROM python:3.12-slim-bookworm AS builder

# Install and configure UV, see https://docs.astral.sh/uv/ for more information
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# - Silence uv complaining about not being able to use hard links,
# - tell uv to byte-compile packages for faster application startups,
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Create virtual environment
RUN python -m venv /app/.venv

# Copy dependency files first for better layer caching
COPY pyproject.toml uv.lock /app/

# Sync the project into a new environment, using the frozen lockfile
RUN uv sync --frozen --no-dev --no-install-project --no-editable --all-packages

COPY *.py *.md /app/
COPY app /app/app/
COPY static /app/static/

###############
# Final image #
###############
FROM python:3.12-slim-bookworm

# Patch system packages
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*

# Upgrade system pip (fixes GHSA-jp4c-xjxw-mgf9)
RUN pip install --no-cache-dir --upgrade pip>=26.1

# Create non-root user
RUN groupadd -r app && useradd -r -d /app -g app -N app

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

# Copy built app from builder stage and set ownership
COPY --from=builder --chown=app:app /app /app

WORKDIR /app

# Switch to non-root user
USER app

EXPOSE 8000
ENTRYPOINT ["python", "server.py"]
