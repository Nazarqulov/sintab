"""Bir nechta handler ishlatadigan matn bloklari. Barcha so'zlar app/locales/*.json'da."""

from html import escape

from app.core.i18n import t
from app.core.permissions import Permission
from app.db.models import User, UserRole, UserStatus

POSITION_SUGGESTIONS = ["Dispatcher", "Fleet Manager", "Safety Manager", "Driver", "Mechanic"]


def role_label(role: UserRole, lang: str | None) -> str:
    return t(f"role.{role.value}", lang)


def status_label(status: UserStatus, lang: str | None) -> str:
    return t(f"status.{status.value}", lang)


def permission_label(permission: Permission, lang: str | None) -> str:
    return t(f"perm.{permission.value}", lang)


def user_summary(user: User, lang: str | None) -> str:
    return t(
        "user.summary",
        lang,
        name=escape(user.full_name or "—"),
        username=f" (@{escape(user.username)})" if user.username else "",
        telegram_id=user.telegram_id,
        phone=escape(user.phone or "—"),
        position=escape(user.position or "—"),
        depots=", ".join(escape(d.name) for d in user.depots) or "—",
        driver=escape(user.driver.full_name or user.driver.samsara_id) if user.driver else "—",
        role=role_label(user.role, lang),
        status=status_label(user.status, lang),
    )


def registration_request(user: User, lang: str | None) -> str:
    return f"{t('user.request_title', lang)}\n\n{user_summary(user, lang)}"
