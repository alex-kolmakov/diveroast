FROM python:3.11-slim

WORKDIR /app

# Pinned uv: the lockfile format (revision 3) needs a recent uv.
RUN pip install --no-cache-dir "uv==0.12.19"

# Install from uv.lock into a venv, so the image runs the tested versions.
# torch comes from the CPU-only index on Linux (see [tool.uv.sources]); the
# uv cache lives in a BuildKit cache mount, not in an image layer.
ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project
ENV PATH="/app/.venv/bin:$PATH"

# Pre-download the cross-encoder model so the first request doesn't wait on a
# HuggingFace download.
RUN python -c "from sentence_transformers import CrossEncoder; CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2'); print('Cross-encoder cached')"

# Create dlt config (embedding provider must be set before ingestion).
# dlt telemetry is off: the server has no reason to report usage to dltHub.
RUN mkdir -p .dlt \
    && printf '[runtime]\ndlthub_telemetry = false\n' > .dlt/config.toml \
    && printf '[destination.lancedb]\nembedding_model_provider = "sentence-transformers"\nembedding_model = "all-MiniLM-L6-v2"\n\n[destination.lancedb.credentials]\nuri = ".lancedb"\n' > .dlt/secrets.toml

# LanceDB data is not baked in: .dockerignore excludes .lancedb/ and both
# compose files mount ./.lancedb at runtime. A COPY here fails on a clean clone.

# Copy source last — only this layer busts on code changes.
# All expensive layers above stay cached.
COPY src/ src/

ENV PYTHONPATH="/app"

EXPOSE 8000

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
