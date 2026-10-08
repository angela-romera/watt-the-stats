FROM python:3.13-slim@sha256:bf44cdfcb76cd3b41e879bc058fc37ec5872002ccfde7fcb765e218cde0cd79c

COPY --from=ghcr.io/astral-sh/uv:0.12.22@sha256:f513a91fc62fe7c17567eee97230dd198e43edb8a9fbecca843714a4358fe1bc /uv /usr/local/bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_NO_CACHE=1 \
    PATH="/app/.venv/bin:$PATH" \
    SE_CHROME_PATH=/usr/local/bin/chromium \
    SE_OFFLINE=true \
    MPLCONFIGDIR=/tmp/matplotlib

WORKDIR /app

RUN apt-get update \
    && apt-get install --yes --no-install-recommends chromium chromium-driver chromium-sandbox tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app \
    && printf '%s\n' '#!/bin/sh' 'exec /usr/bin/chromium --disable-dev-shm-usage "$@"' > /usr/local/bin/chromium \
    && chmod 755 /usr/local/bin/chromium

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project

COPY main.py ./
COPY src ./src
COPY config/settings.json ./config/settings.json

RUN mkdir -p data && chown app:app data

USER app

CMD ["python", "main.py"]
