from app.core.permissions import Permission, effective_permissions, role_has_permission


def test_override_can_grant_and_revoke():
    perms = effective_permissions("viewer", {"export_reports": True, "view_vehicles": False})
    assert Permission.EXPORT_REPORTS in perms
    assert Permission.VIEW_VEHICLES not in perms
    assert Permission.VIEW_ALERTS in perms


def test_override_cannot_grant_anything_to_blocked_or_pending():
    assert effective_permissions("blocked", {"manage_users": True}) == set()
    assert effective_permissions("pending", {"view_alerts": True}) == set()


def test_unknown_override_name_is_ignored():
    assert effective_permissions("driver", {"not_a_permission": True}) == {Permission.VIEW_ALERTS}


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
