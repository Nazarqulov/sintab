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


@pytest.fixture
async def tg(session):
    """Haqiqiy dispatcher + soxta Telegram sessiya. `tg.feed(update)`, `tg.sent()`, `tg.last_text()`..."""
    from aiogram import Bot
    from aiogram.fsm.storage.memory import MemoryStorage

    from app.bot.bot_instance import dp
    from app.bot.middlewares.throttling import ThrottlingMiddleware
    from tests.telegram_fake import FakeSession

    fake = FakeSession()
    bot = Bot("123456:TEST-TOKEN", session=fake)
    # Router'lar faqat bitta dispatcher'ga ulanadi — modul darajasidagisini qayta ishlatib, holatini tozalaymiz.
    dp.fsm.storage = MemoryStorage()

    def reset_throttling() -> None:
        for middleware in dp.callback_query.outer_middleware:
            if isinstance(middleware, ThrottlingMiddleware):
                middleware._buckets.clear()

    async def feed(update):
        await dp.feed_update(bot, update)

    reset_throttling()
    fake.feed = feed
    fake.reset_throttling = reset_throttling
    return fake
