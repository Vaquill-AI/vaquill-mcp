# syntax=docker/dockerfile:1

# =============================================================================
# Stage 1: Build -- install dependencies and package
# =============================================================================
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /build

# Copy dependency metadata FIRST for layer caching.
# Code changes won't re-download all deps.
COPY pyproject.toml README.md ./

# Create isolated venv for clean copy to runtime stage.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install dependencies (cached until pyproject.toml changes).
RUN pip install --no-cache-dir ".[remote]" 2>/dev/null || true

# Copy source code LAST (changes most frequently).
COPY src/ src/

# Reinstall with source available (fast -- deps already cached).
RUN pip install --no-cache-dir ".[remote]"


# =============================================================================
# Stage 2: Runtime (production)
# =============================================================================
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Non-root user (UID 65532 matches distroless convention).
RUN groupadd --gid 65532 appuser && \
    useradd --uid 65532 --gid 65532 --no-create-home --shell /usr/sbin/nologin appuser && \
    pip uninstall -y pip setuptools 2>/dev/null; \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/* /root/.cache

# Copy venv from builder.
COPY --from=builder --chown=65532:65532 /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Dokploy writes the app's saved environment variables to `.env` in the build
# context. Nothing else brings them into the container: this image declares no
# ENV for them and the package reads `os.environ` only, with no dotenv. Without
# this COPY the variables exist in the Dokploy UI and nowhere else, which is
# exactly how the OAuth group appeared "set" while `oauth_enabled()` stayed
# false and /mcp kept answering 200 instead of 401.
#
# The trailing `*` keeps the build working on a machine with no .env (local
# builds, CI), where the glob simply matches nothing.
COPY --chown=65532:65532 .env* ./

USER 65532:65532

EXPOSE 8000

# Health check using Python stdlib (no curl/wget needed).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import http.client; c = http.client.HTTPConnection('localhost', 8000); c.request('GET', '/health'); r = c.getresponse(); exit(0 if r.status < 500 else 1)"]

# `set -a` exports everything sourced from .env, then `exec` replaces the shell
# so uvicorn still receives SIGTERM directly -- the property the previous exec
# form existed to protect. Sourcing is guarded, so a container run with real
# environment variables and no .env behaves identically.
CMD ["sh", "-c", "set -a; [ -f /.env ] && . /.env; set +a; exec vaquill-mcp-remote"]
