###############
# Build stage #
###############
FROM python:3.12-slim-bookworm AS builder

# Telephony provider: acs (default), twilio, infobip, or genesys
ARG TELEPHONY=acs

# Install and configure UV, see https://docs.astral.sh/uv/ for more information
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# - Silence uv complaining about not being able to use hard links,
# - tell uv to byte-compile packages for faster application startups,
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Create virtual environment (without pip — uv handles installs)
RUN python -m venv --without-pip /app/.venv

# Copy dependency files first for better layer caching
COPY pyproject.toml uv.lock /app/

# Install core dependencies plus the selected telephony provider's extras
RUN uv sync --frozen --no-dev --no-install-project --no-editable --all-packages --extra ${TELEPHONY}

COPY *.py *.md /app/
COPY app /app/app/
COPY static /app/static/

###############
# Final image #
###############
FROM python:3.12-slim-bookworm

# Patch system packages and remove pip/setuptools (not needed at runtime — uv handles builds)
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/* && \
    rm -rf /usr/local/lib/python3.12/site-packages/pip* \
           /usr/local/lib/python3.12/site-packages/setuptools* \
           /usr/local/lib/python3.12/dist-packages/pip* \
           /usr/local/lib/python3.12/dist-packages/setuptools* \
           /usr/local/bin/pip* \
           /usr/local/lib/python3.12/ensurepip/_bundled/pip* \
           /usr/local/lib/python3.12/ensurepip/_bundled/setuptools*

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
