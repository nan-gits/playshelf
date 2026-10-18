# Playshelf container image, based on the official slim Python image.
# Games, database and inbox live outside the container (volumes).

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# libarchive reads RAR, 7z and tar archives for imports
RUN apt-get update \
    && apt-get install -y --no-install-recommends libarchive13t64 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/app
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install . && rm -rf /opt/app/app /opt/app/build

# Fixed paths inside the container; mount host folders here.
# PUID/PGID choose the user the app runs as (see docker/entrypoint.sh).
ENV GAMES_DIR=/srv/games \
    DATA_DIR=/srv/data \
    INBOX_DIR=/srv/inbox \
    HOST=0.0.0.0 \
    PORT=8000 \
    PUID=1000 \
    PGID=1000
RUN mkdir -p /srv/games /srv/data /srv/inbox
COPY --chmod=0755 docker/entrypoint.sh /usr/local/bin/playshelf-entrypoint

EXPOSE 8000
VOLUME ["/srv/games", "/srv/data", "/srv/inbox"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --start-interval=2s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('PORT', '8000')}/api/health\", timeout=4)"]

ENTRYPOINT ["playshelf-entrypoint"]
CMD ["playshelf"]
