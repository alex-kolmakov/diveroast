import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes import admin, chat, dashboard, donations, health, shared, upload
from src.config import settings
from src.observability import init_sentry, init_tracing
from src.storage.retention import purge_forever

# Libraries stay at WARNING; the app's own INFO lines (token usage per model
# call, budget events) go to the container log.
logging.basicConfig(
    level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logging.getLogger("src").setLevel(logging.INFO)

# Before the app exists, so the FastAPI integration can hook in.
init_sentry()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_tracing()
    retention = asyncio.create_task(purge_forever())
    yield
    retention.cancel()


app = FastAPI(title="DiveRoast API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.ALLOWED_ORIGINS.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(upload.router)
app.include_router(chat.router)
app.include_router(dashboard.router)
app.include_router(shared.router)
app.include_router(donations.router)
app.include_router(admin.router)
