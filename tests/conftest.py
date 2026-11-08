import base64
import os
import tempfile

# app modullari import qilinishidan OLDIN o'rnatilishi shart. DATABASE_URL majburan almashtiriladi —
# testlar hech qachon haqiqiy bazaga tegmasligi uchun.
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{tempfile.gettempdir()}/samsara_bot_test_{os.getpid()}.db"
os.environ["BOT_TOKEN"] = "123456:TEST-TOKEN"
os.environ["OWNER_TELEGRAM_ID"] = "1000"
os.environ["SAMSARA_WEBHOOK_SECRET"] = base64.b64encode(b"test-webhook-secret").decode()

import pytest  # noqa: E402

import app.db.models  # noqa: E402,F401
from app.db.session import AsyncSessionLocal, Base, engine  # noqa: E402


@pytest.fixture
async def session():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as db_session:
        yield db_session
