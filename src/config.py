from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Gemini
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-3.1-flash-lite-preview"

    # Local OpenAI-compatible server (LM Studio, Ollama). When LLM_BASE_URL is
    # set it replaces Gemini, e.g. http://localhost:1234/v1
    LLM_BASE_URL: str = ""
    LLM_MODEL: str = ""
    LLM_API_KEY: str = "lm-studio"

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
    # Cross-encoder logit floor (only applied with reranking on). Measured on
    # the local index: on-topic best hits score -4..+2, off-topic ~-11.
    RAG_MIN_RELEVANCE: float = -5.0
    # Full rebuild fails if it yields fewer rows than this, or less than
    # RAG_REBUILD_MIN_RATIO of the previous table. A complete DAN index is
    # about 17,800 rows; a rebuild that kept a stale cursor produced 2,049.
    RAG_MIN_ROWS: int = 10_000
    RAG_REBUILD_MIN_RATIO: float = 0.8

    # Prompt
    PROMPT_VERSION: int = 5

    # Agent loop
    AGENT_MAX_STEPS: int = 8  # tool rounds per message before forcing an answer
    AGENT_TOOL_TEMPERATURE: float = 0.2  # first round: picking tools
    AGENT_TEMPERATURE: float = 0.8  # later rounds: writing the answer

    # Output caps per model call (thinking tokens count against them too).
    # A roast is ~120 words; a follow-up answer a few paragraphs.
    CHAT_MAX_OUTPUT_TOKENS: int = 2048
    SUMMARY_MAX_OUTPUT_TOKENS: int = 1024  # the worst-dive jabs, one JSON array

    # Sessions (in-memory)
    SESSION_TTL_SECONDS: int = 6 * 60 * 60
    MAX_SESSIONS: int = 500

    # Per-IP rate limits (per hour). Generous on purpose: a conference room or
    # office shares one IP, and the session and daily budgets cap the cost.
    UPLOADS_PER_IP_PER_HOUR: int = 60
    CHATS_PER_IP_PER_HOUR: int = 300
    # Trust X-Real-IP from the reverse proxy. Only true behind the production
    # nginx; anywhere the backend is reachable directly the header is forgeable.
    TRUST_PROXY_HEADERS: bool = False

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

    # Admin
    ADMIN_SECRET: str = ""  # Set in .env / Secret Manager to protect admin endpoints

    # DAN scraping
    DAN_BASE_URL: str = "https://dan.org/wp-json/wp/v2/"
    DAN_PER_PAGE: int = 100
    DAN_START_DATE: str = "2000-01-01T00:00:00"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
