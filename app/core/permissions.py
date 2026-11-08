from collections.abc import Mapping
from enum import Enum


class Permission(str, Enum):
    VIEW_ALERTS = "view_alerts"
    ACK_ALERTS = "ack_alerts"
    RESOLVE_ALERTS = "resolve_alerts"
    VIEW_VEHICLES = "view_vehicles"
    VIEW_REPORTS = "view_reports"
    EXPORT_REPORTS = "export_reports"
    MANAGE_USERS = "manage_users"
    MANAGE_ROLES = "manage_roles"
    MANAGE_GROUPS = "manage_groups"
    MANAGE_ROUTING = "manage_routing"
    SEND_BROADCAST = "send_broadcast"
    MANAGE_SETTINGS = "manage_settings"
    VIEW_AUDIT_LOG = "view_audit_log"


ALL_PERMISSIONS: set[Permission] = set(Permission)

# Owner va Admin bir xil flag to'plamiga ega — ular orasidagi farq (masalan
# faqat Owner boshqa birovni Admin qila olishi) handler darajasida 3-bosqichda
# qo'shiladi, flag orqali emas.
ROLE_PERMISSIONS: dict[str, set[Permission]] = {
    "owner": ALL_PERMISSIONS,
    "admin": ALL_PERMISSIONS,
    "dispatcher": {
        Permission.VIEW_ALERTS,
        Permission.ACK_ALERTS,
        Permission.RESOLVE_ALERTS,
        Permission.VIEW_VEHICLES,
    },
    "manager": {
        Permission.VIEW_ALERTS,
        Permission.VIEW_VEHICLES,
        Permission.VIEW_REPORTS,
        Permission.EXPORT_REPORTS,
    },
    "viewer": {
        Permission.VIEW_ALERTS,
        Permission.VIEW_VEHICLES,
        Permission.VIEW_REPORTS,
    },
    "driver": {
        Permission.VIEW_ALERTS,
    },
    "blocked": set(),
    "pending": set(),
}


def role_has_permission(role: str, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, set())


def effective_permissions(role: str, overrides: Mapping[str, bool] | None = None) -> set[Permission]:
    """Rol standarti + Owner bergan individual override'lar. Blocked/Pending'ga override ham huquq bermaydi."""
    if role in ("blocked", "pending"):
        return set()
    permissions = set(ROLE_PERMISSIONS.get(role, set()))
    for name, allowed in (overrides or {}).items():
        try:
            permission = Permission(name)
        except ValueError:
            continue
        if allowed:
            permissions.add(permission)
        else:
            permissions.discard(permission)
    return permissions
