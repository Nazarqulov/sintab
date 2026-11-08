from app.core.permissions import Permission, role_has_permission


def test_owner_has_all_permissions():
    for permission in Permission:
        assert role_has_permission("owner", permission)


def test_admin_has_all_permissions():
    for permission in Permission:
        assert role_has_permission("admin", permission)


def test_viewer_cannot_manage_users():
    assert not role_has_permission("viewer", Permission.MANAGE_USERS)


def test_dispatcher_can_ack_but_not_export_reports():
    assert role_has_permission("dispatcher", Permission.ACK_ALERTS)
    assert not role_has_permission("dispatcher", Permission.EXPORT_REPORTS)


def test_driver_can_view_alerts_only():
    assert role_has_permission("driver", Permission.VIEW_ALERTS)
    assert not role_has_permission("driver", Permission.ACK_ALERTS)


def test_blocked_and_pending_have_no_permissions():
    for role in ("blocked", "pending"):
        assert not any(role_has_permission(role, permission) for permission in Permission)


def test_unknown_role_has_no_permissions():
    assert not role_has_permission("not-a-real-role", Permission.VIEW_ALERTS)
