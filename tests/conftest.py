import os

# Testlar .env fayl bo'lmasa ham ishlashi uchun minimal majburiy qiymatlar.
os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("OWNER_TELEGRAM_ID", "123456")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/test")
