FROM python:3.11-slim

WORKDIR /app

# Install uv for fast dependency management
RUN pip install uv

# Copy only the dependency manifest first — everything below this line is
# cached between deploys as long as pyproject.toml hasn't changed.
COPY pyproject.toml .

# Stub out the package so `uv pip install .` can resolve dependencies
# without needing the real source tree.
RUN mkdir -p src && touch src/__init__.py

# Install dependencies (cached layer — only busts when pyproject.toml changes)
RUN uv pip install --system .

# Pre-download cross-encoder model into image cache to avoid HuggingFace
# downloads on Cloud Run cold starts (no outbound internet in production).
RUN uv run python -c "from sentence_transformers import CrossEncoder; CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2'); print('Cross-encoder cached')"

# Create dlt config (embedding provider must be set before ingestion)
RUN mkdir -p .dlt \
    && printf '[runtime]\ndlthub_telemetry = true\n' > .dlt/config.toml \
    && printf '[destination.lancedb]\nembedding_model_provider = "sentence-transformers"\nembedding_model = "all-MiniLM-L6-v2"\n\n[destination.lancedb.credentials]\nuri = ".lancedb"\n' > .dlt/secrets.toml

# Copy pre-built LanceDB data (avoids sentence-transformers segfault under QEMU cross-compilation)
COPY .lancedb/ .lancedb/

# Copy source last — only this layer busts on code changes.
# All expensive layers above stay cached.
COPY src/ src/

ENV PYTHONPATH="/app"

EXPOSE 8000

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
