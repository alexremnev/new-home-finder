# The worker must not depend on where it runs. This image is the contract:
# any host that can run it can run the worker, with configuration supplied
# entirely through environment variables and the database.
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev

COPY worker/ ./worker/
COPY scripts/ ./scripts/

ENTRYPOINT ["uv", "run", "python", "-m", "worker"]
CMD ["tick"]
