FROM python:3.11-slim

WORKDIR /app

# Install uv for fast dependency management
RUN pip install uv

# Copy project files
COPY pyproject.toml .
COPY src/ src/

# Install dependencies
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

ENV PYTHONPATH="/app"

EXPOSE 8000

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
