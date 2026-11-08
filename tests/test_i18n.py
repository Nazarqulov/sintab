import re
from pathlib import Path

import pytest

from app.bot.commands import GROUP_ADMIN_COMMANDS, GUEST_COMMANDS, USER_COMMANDS
from app.bot.keyboards.menu import REPLY_SECTIONS, SECTIONS
from app.core.i18n import CATALOG, DEFAULT_LANGUAGE, LANGUAGES, Text, placeholders, t
from app.core.permissions import Permission
from app.db.models import UserRole, UserStatus
from app.services.alerts.formatter import RESOLUTION_REASONS
from app.services.alerts.types import AlertType
from app.services.preferences import NOTIFY_LEVELS

APP_DIR = Path(__file__).resolve().parent.parent / "app"
NAMESPACES = {key.split(".")[0] for key in CATALOG[DEFAULT_LANGUAGE]}
KEY_LITERAL = re.compile(r"""["']((?:[a-z_]+\.)+[a-z_]+)["']""")


def test_all_languages_have_same_keys():
    reference = set(CATALOG[DEFAULT_LANGUAGE])
    for lang in LANGUAGES:
        assert set(CATALOG[lang]) == reference, f"{lang}: {set(CATALOG[lang]) ^ reference}"


@pytest.mark.parametrize("lang", LANGUAGES)
def test_placeholders_match_default_language(lang):
    for key, text in CATALOG[DEFAULT_LANGUAGE].items():
        assert placeholders(CATALOG[lang][key]) == placeholders(text), key


def test_every_literal_key_in_code_is_translated():
    missing = set()
    for path in APP_DIR.rglob("*.py"):
        if "db" in path.relative_to(APP_DIR).parts:  # ForeignKey("users.id") kabi satrlar
            continue
        for key in KEY_LITERAL.findall(path.read_text(encoding="utf-8")):
            if key.split(".")[0] in NAMESPACES and key not in CATALOG[DEFAULT_LANGUAGE]:
                missing.add(f"{key} ({path.relative_to(APP_DIR)})")
    assert not missing, sorted(missing)


def test_dynamic_key_families_are_translated():
    keys = (
        [f"role.{r.value}" for r in UserRole]
        + [f"status.{s.value}" for s in UserStatus]
        + [f"perm.{p.value}" for p in Permission]
        + [f"alert.type.{a.value}" for a in AlertType]
        + [f"alert.reason.{r}" for r in RESOLUTION_REASONS]
        + [f"settings.notify.{level}" for level in NOTIFY_LEVELS]
        + [f"settings.hint.{field}" for field in ("lang", "tz", "notify", "quiet")]
        + [f"cmd.{c.name}" for c in USER_COMMANDS]
        + [f"cmd.{name}" for name in GUEST_COMMANDS + GROUP_ADMIN_COMMANDS]
        + [f"menu.{s.key}" for s in SECTIONS]
        + [f"menu.{key}" for key in REPLY_SECTIONS]
    )
    missing = [key for key in keys if key not in CATALOG[DEFAULT_LANGUAGE]]
    assert not missing, missing


def test_fallbacks():
    assert t("common.back", "xx") == t("common.back", "uz")
    assert t("no.such.key", "ru") == "no.such.key"
    assert t("users.deleted", "en", name="Bob") == "🗑 Bob deleted."


def test_nested_text_is_rendered_in_target_language():
    reply = Text("action.muted", type=Text("alert.type.speeding"))
    assert "Превышение скорости" in reply.render("ru")
    assert "Speeding" in reply.render("en")
