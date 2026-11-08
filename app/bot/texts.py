"""Foydalanuvchi va admin matnlari (o'zbekcha). 4-bosqichda i18n'ga ko'chiriladi."""

from html import escape

from app.core.permissions import Permission
from app.db.models import User, UserRole, UserStatus

ROLE_LABELS: dict[UserRole, str] = {
    UserRole.OWNER: "👑 Owner",
    UserRole.ADMIN: "🛡 Admin",
    UserRole.DISPATCHER: "🎧 Dispatcher",
    UserRole.MANAGER: "📊 Manager",
    UserRole.VIEWER: "👁 Viewer",
    UserRole.DRIVER: "🚚 Driver",
    UserRole.BLOCKED: "🚫 Blocked",
    UserRole.PENDING: "⏳ Rol berilmagan",
}

STATUS_LABELS: dict[UserStatus, str] = {
    UserStatus.PENDING: "⏳ Kutilmoqda",
    UserStatus.ACTIVE: "✅ Faol",
    UserStatus.BLOCKED: "🚫 Bloklangan",
}

PERMISSION_LABELS: dict[Permission, str] = {
    Permission.VIEW_ALERTS: "Alertlarni ko'rish",
    Permission.ACK_ALERTS: "Alertni qabul qilish",
    Permission.RESOLVE_ALERTS: "Alertni yopish",
    Permission.VIEW_VEHICLES: "Mashinalarni ko'rish",
    Permission.VIEW_REPORTS: "Hisobotlarni ko'rish",
    Permission.EXPORT_REPORTS: "Hisobot eksporti",
    Permission.MANAGE_USERS: "Foydalanuvchilar",
    Permission.MANAGE_ROLES: "Rollar",
    Permission.MANAGE_GROUPS: "Guruhlar",
    Permission.MANAGE_ROUTING: "Routing qoidalari",
    Permission.SEND_BROADCAST: "Ommaviy xabar",
    Permission.MANAGE_SETTINGS: "Sozlamalar",
    Permission.VIEW_AUDIT_LOG: "Audit log",
}

POSITION_SUGGESTIONS = ["Dispatcher", "Fleet Manager", "Safety Manager", "Driver", "Mechanic"]

NOT_REGISTERED = "Avval botga shaxsiy chatda /start yozib ro'yxatdan o'ting."
PENDING = "⏳ So'rovingiz ko'rib chiqilmoqda. Admin tasdiqlagach xabar beramiz."
BLOCKED = "🚫 Kirish huquqingiz yo'q. Savollar bo'lsa, administratorga murojaat qiling."
NO_PERMISSION = "⛔ Sizda bu amal uchun ruxsat yo'q."


def user_summary(user: User) -> str:
    username = f" (@{escape(user.username)})" if user.username else ""
    depots = ", ".join(escape(d.name) for d in user.depots) or "—"
    driver = escape(user.driver.full_name or user.driver.samsara_id) if user.driver else "—"
    return (
        f"👤 <b>{escape(user.full_name or '—')}</b>{username}\n"
        f"🆔 <code>{user.telegram_id}</code>\n"
        f"📱 {escape(user.phone or '—')}\n"
        f"💼 {escape(user.position or '—')}\n"
        f"🏢 Depo: {depots}\n"
        f"🚚 Samsara haydovchisi: {driver}\n"
        f"🎭 Rol: {ROLE_LABELS[user.role]}\n"
        f"📌 Holat: {STATUS_LABELS[user.status]}"
    )


def registration_request(user: User) -> str:
    return f"🆕 <b>Yangi ro'yxatdan o'tish so'rovi</b>\n\n{user_summary(user)}"
