# PRU Math Engine — production Docker image.
#
# Build:   docker build -t pru-math-engine .
# Run:     docker run --rm -p 8000:8000 -v $(pwd)/data:/data pru-math-engine
# Then:    open http://localhost:8000
#
# To talk to a host-side Ollama:
#   docker run --rm -p 8000:8000 \
#     -e OLLAMA_ENABLED=true \
#     -e OLLAMA_HOST=http://host.docker.internal:11434 \
#     -v $(pwd)/data:/data pru-math-engine

FROM python:3.11-slim AS base

# System packages: a tiny curl for healthchecks, libgomp for scipy/numpy.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Two-stage caching: copy only the metadata first so dep installs cache
# across source-only changes.
COPY pyproject.toml README.md LICENSE /app/
COPY pru_math /app/pru_math
COPY tests /app/tests

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir ".[all]"

# Persistent state lives on a mounted volume; defaults from .env.example
# point inside /app/data so the volume "just works".
RUN mkdir -p /data
ENV PRU_DB_PATH=/data/pru_math.sqlite \
    PRU_GRAPH_PATH=/data/pru_graph.gpickle \
    PRU_SETTINGS_PATH=/data/settings.json \
    OLLAMA_ENABLED=false \
    PYTHONUNBUFFERED=1

EXPOSE 8000

# Use the published console script so future versions pick up any
# argparse improvements automatically.
CMD ["pru-math-server", "--host", "0.0.0.0", "--port", "8000"]

HEALTHCHECK --interval=30s --timeout=4s --retries=3 \
    CMD curl -fsS http://localhost:8000/db/stats >/dev/null || exit 1
