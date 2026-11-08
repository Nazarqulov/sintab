from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import samsara_webhook, telegram_webhook
from app.core.logging import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    yield


app = FastAPI(title="Samsara Fleet Bot", lifespan=lifespan)
app.include_router(telegram_webhook.router)
app.include_router(samsara_webhook.router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
