"""Uch tilli matnlar: app/locales/{uz,ru,en}.json. Kalit topilmasa — o'zbekcha, u ham bo'lmasa kalitning o'zi."""

import json
from pathlib import Path
from string import Formatter

LANGUAGES = ("uz", "ru", "en")
DEFAULT_LANGUAGE = "uz"
LANGUAGE_NAMES = {"uz": "🇺🇿 O'zbekcha", "ru": "🇷🇺 Русский", "en": "🇬🇧 English"}

_LOCALES_DIR = Path(__file__).resolve().parent.parent / "locales"


def _load() -> dict[str, dict[str, str]]:
    return {lang: json.loads((_LOCALES_DIR / f"{lang}.json").read_text(encoding="utf-8")) for lang in LANGUAGES}


CATALOG = _load()


def normalize_language(lang: str | None) -> str:
    return lang if lang in CATALOG else DEFAULT_LANGUAGE


def t(key: str, lang: str | None = None, /, **kwargs: object) -> str:
    text = CATALOG[normalize_language(lang)].get(key) or CATALOG[DEFAULT_LANGUAGE].get(key) or key
    return text.format(**kwargs) if kwargs else text


def variants(key: str) -> set[str]:
    """Kalitning barcha tillardagi matni — reply-tugma bosilganini tanib olish uchun."""
    return {CATALOG[lang][key] for lang in LANGUAGES if key in CATALOG[lang]}


def placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in Formatter().parse(text) if name}


class Text:
    """Keyinroq foydalanuvchi tilida chiqariladigan matn (xizmat qatlami tilni bilmaydi)."""

    def __init__(self, key: str, **kwargs: object) -> None:
        self.key = key
        self.kwargs = kwargs

    def render(self, lang: str | None) -> str:
        values = {k: v.render(lang) if isinstance(v, Text) else v for k, v in self.kwargs.items()}
        return t(self.key, lang, **values)

    def __str__(self) -> str:
        return self.render(DEFAULT_LANGUAGE)


class LocalizedError(Exception):
    """Foydalanuvchiga ko'rsatiladigan xato: handler uni foydalanuvchi tilida chiqaradi."""

    def __init__(self, key: str, **kwargs: object) -> None:
        super().__init__(key)
        self.key = key
        self.kwargs = kwargs

    def render(self, lang: str | None) -> str:
        return t(self.key, lang, **self.kwargs)

    def __str__(self) -> str:
        return self.render(DEFAULT_LANGUAGE)
