FROM ghcr.io/astral-sh/uv:0.11.23 AS uv

FROM postgres:17.6-bookworm AS postgres-client

FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    XDG_CACHE_HOME=/tmp/fire-inspect-cache \
    TMPDIR=/tmp \
    PATH="/app/.venv/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        fonts-noto-cjk \
        libreoffice-writer \
        poppler-utils \
        libpq5 \
        tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 fire-inspect \
    && useradd --system --uid 10001 --gid fire-inspect --home-dir /app fire-inspect

COPY --from=uv /uv /uvx /usr/local/bin/
COPY --from=postgres-client /usr/lib/postgresql/17/bin/pg_dump /usr/local/bin/pg_dump
COPY --from=postgres-client /usr/lib/postgresql/17/bin/pg_restore /usr/local/bin/pg_restore
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY manage.py ./
COPY src ./src
COPY docs/PRD.md ./docs/PRD.md
COPY docs/templates ./docs/templates
COPY testdata/knowledge/reg_ec997c582c9c29a6.pdf ./testdata/knowledge/reg_ec997c582c9c29a6.pdf
COPY scripts/docker-entrypoint.sh /usr/local/bin/fire-inspect-entrypoint
COPY scripts/backup.sh /usr/local/bin/fire-inspect-backup
COPY scripts/restore.sh /usr/local/bin/fire-inspect-restore
RUN uv sync --frozen --no-dev \
    && chmod 0555 \
        /usr/local/bin/fire-inspect-entrypoint \
        /usr/local/bin/fire-inspect-backup \
        /usr/local/bin/fire-inspect-restore \
    && mkdir -p /data \
    && chown -R fire-inspect:fire-inspect /app /data

USER fire-inspect
EXPOSE 8000
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/fire-inspect-entrypoint"]
CMD ["web"]
