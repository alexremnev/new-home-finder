# The worker must not depend on where it runs. This image is the contract: any host
# that can run it can run the worker, with configuration supplied entirely through
# environment variables and the database.
#
# ── two things this image got wrong before ───────────────────────────────────
#
#   * `uv sync --no-dev` installs no extras, and the Telegram reader lives in the
#     `ingest` extra. The image built, started, and failed at the first read with
#     "Telethon is not installed" — a container that is only good for `drain`.
#   * `CMD ["tick"]` ran the scheduler job, which reads the `schedules` table to
#     decide what is due. That table no longer drives anything: the two jobs are
#     `ingest` and `drain`, and what runs them is the host's timer or the loop below.

FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies before source, so editing a worker file does not reinstall psycopg.
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --extra ingest

COPY worker/ ./worker/
COPY scripts/ ./scripts/

# Unbuffered, because the only way to see what a container is doing is its stdout,
# and a buffered process appears to be doing nothing until it exits.
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["uv", "run", "python", "-m", "worker"]

# One pass of one job, and nothing more. Which job, and how often, is the host's
# decision — `docker run … ingest`, a cron entry, or the loop script for a host that
# only knows how to keep a process alive.
CMD ["--help"]
