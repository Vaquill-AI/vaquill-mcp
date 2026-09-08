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

# FastMCP's OAuthProxy keeps its client registrations and token mappings in a
# file store under `settings.home`, which defaults to a per-user data directory
# and is created eagerly with `mkdir(parents=True)` during construction. This
# image's user is made with `--no-create-home`, so that path is unwritable and
# the constructor raises: uvicorn never starts and the container exits 1 while
# Swarm keeps the previous healthy task serving. The symptom is a server that
# looks fine on every probe and silently never picks up the new configuration.
#
# This path is now the FALLBACK, not the production store. It is still created
# because `OAuthProxy` builds its default file store eagerly, but a deployment
# that sets VAQUILL_OAUTH_REDIS_URL / _JWT_SIGNING_KEY / _STORAGE_ENCRYPTION_KEY
# never writes here. Without them, a redeploy clears the store and signs every
# connected user out: measured 2026-09-08, twice in one afternoon, on deploys
# for unrelated changes. See `_durable_storage` in oauth.py.
#
# A volume was considered and rejected. It would fix the restart case and not
# the other one: FastMCP keeps the in-flight authorization TRANSACTION here, so
# with two replicas /authorize and /oauth/callback stop sharing state and OAuth
# fails outright rather than merely expiring.
ENV FASTMCP_HOME=/data
RUN mkdir -p /data && chown 65532:65532 /data

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
