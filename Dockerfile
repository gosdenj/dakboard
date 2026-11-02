# A self-hosted family wall board.
#
# Python standard library only - no pip dependencies at runtime. The image is
# just python + the application, which keeps the rebuild fast and the
# attack surface small.

FROM python:3.12-slim

# tzdata so ZoneInfo("Europe/London") works in the container.
# Debian slim does not always ship the zone database.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DAKBOARD_DATA=/data \
    DAKBOARD_TZ=Europe/London \
    DAKBOARD_PORT=8099

WORKDIR /app

COPY dakboard/ /app/dakboard/
COPY static/ /app/static/

# Run as a non-root user. The data directory is a bind mount owned by the
# host, so it must be writable by this uid.
#
# The group is pinned to 1000 rather than left to --system, which would
# pick a gid from the system range (999 here). A named volume takes its
# ownership from this directory, so the uid and gid the image publishes
# have to be the ones the stack asks for - otherwise the volume is owned
# by a group the container is not in.
RUN groupadd --gid 1000 board \
 && useradd --uid 1000 --gid 1000 --create-home --home-dir /home/board board \
 && mkdir -p /data \
 && chown -R board:board /app /data
USER board

VOLUME ["/data"]
EXPOSE 8099

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
  CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8099/api/health',timeout=5).status==200 else 1)"

CMD ["python3", "-u", "-m", "dakboard"]
