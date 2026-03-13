from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Gemini
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-3.1-flash-lite-preview"

    # LanceDB / Embeddings
    LANCEDB_URI: str = ".lancedb"
    LANCEDB_TABLE_NAME: str = "dan_articles___texts"
    DESTINATION__LANCEDB__EMBEDDING_MODEL_PROVIDER: str = "sentence-transformers"
    DESTINATION__LANCEDB__EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"
    DESTINATION__LANCEDB__CREDENTIALS__URI: str = ".lancedb"

    # RAG
    RAG_TOP_K: int = 10
    CHUNK_SIZE: int = 900  # was 2000 — smaller for section-aware splits
    CHUNK_OVERLAP: int = 50  # was 100 — reduced proportionally
    CROSS_ENCODER_MODEL: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    ENABLE_RERANKING: bool = True  # set False in tests / offline environments

    # Prompt
    PROMPT_VERSION: int = 3

    # Phoenix
    PHOENIX_COLLECTOR_ENDPOINT: str = "http://localhost:6006/v1/traces"
    PHOENIX_CLIENT_ENDPOINT: str = "http://localhost:6006"
    PHOENIX_PROJECT_NAME: str = "diveroast"

    # CORS
    ALLOWED_ORIGINS: str = "http://localhost:3000"

    # Snapshot storage (backed by Docker named volume in production)
    SNAPSHOT_DIR: str = "/tmp/diveroast-snapshots"

    # Donated dive log storage
    DONATIONS_DIR: str = "/tmp/diveroast-donations"

    # DAN scraping
    DAN_BASE_URL: str = "https://dan.org/wp-json/wp/v2/"
    DAN_PER_PAGE: int = 100
    DAN_START_DATE: str = "2000-01-01T00:00:00"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
