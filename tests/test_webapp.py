import json
import time
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from app.api import webapp
from app.db.models import Alert, AlertStatus, Depot, Document, Driver, User, UserRole, UserStatus, Vehicle
from app.main import app
from app.services.alerts.ingest import ingest_alert
from app.services.webapp_auth import InitDataError, sign_init_data, validate_init_data
from tests.helpers import OWNER_CHAT_ID, FakeBot, add_user, normalized

TOKEN = "123456:TEST-TOKEN"


class WebBot(FakeBot):
    def __init__(self) -> None:
        super().__init__()
        self.documents: list[dict] = []

    async def send_document(self, chat_id, document, **kwargs):
        self.documents.append({"chat_id": chat_id, "document": document, **kwargs})
        return SimpleNamespace(document=SimpleNamespace(file_id="tg-file-1", file_unique_id="tg-u-1"))

    async def edit_message_reply_markup(self, **kwargs):
        return True

    async def set_my_commands(self, *a, **kw):
        return True

    async def delete_my_commands(self, *a, **kw):
        return True


def init_data(user_id: int, *, age: int = 0, token: str = TOKEN) -> str:
    fields = {"auth_date": str(int(time.time()) - age), "query_id": "q1", "user": json.dumps({"id": user_id, "first_name": "T"})}
    return sign_init_data(fields, token)


@pytest.fixture
async def client(session):
    bot = WebBot()
    app.dependency_overrides[webapp.telegram_bot] = lambda: bot
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        http.bot = bot
        yield http
    app.dependency_overrides.clear()


def auth(user_id: int, **kw) -> dict:
    return {"Authorization": f"tma {init_data(user_id, **kw)}"}


# --- initData ---

def test_init_data_validation():
    user = validate_init_data(init_data(42), TOKEN)
    assert user["id"] == 42
    with pytest.raises(InitDataError, match="signature"):
        validate_init_data(init_data(42, token="999:OTHER"), TOKEN)
    with pytest.raises(InitDataError, match="expired"):
        validate_init_data(init_data(42, age=3 * 24 * 3600), TOKEN)
    tampered = init_data(42).replace("%22id%22%3A+42", "%22id%22%3A+1000")
    with pytest.raises(InitDataError):
        validate_init_data(tampered, TOKEN)
    with pytest.raises(InitDataError):
        validate_init_data("garbage", TOKEN)


async def test_auth_rejections(session, client):
    await add_user(session, OWNER_CHAT_ID, UserRole.OWNER)
    await add_user(session, 5001, UserRole.PENDING, status=UserStatus.PENDING)
    assert (await client.get("/api/app/me")).status_code == 401
    assert (await client.get("/api/app/me", headers={"Authorization": "tma bad"})).status_code == 401
    assert (await client.get("/api/app/me", headers=auth(OWNER_CHAT_ID, age=10**6))).status_code == 401
    assert (await client.get("/api/app/me", headers=auth(5001))).status_code == 403  # tasdiqlanmagan
    assert (await client.get("/api/app/me", headers=auth(9999))).status_code == 403  # ro'yxatda yo'q


# --- Profil va ruxsatlar ---

async def test_me_sections_follow_permissions(session, client):
    await add_user(session, OWNER_CHAT_ID, UserRole.OWNER, language="ru")
    await add_user(session, 5002, UserRole.VIEWER)
    owner = (await client.get("/api/app/me", headers=auth(OWNER_CHAT_ID))).json()
    assert owner["language"] == "ru" and "users" in owner["sections"] and "reports" in owner["sections"]
    assert owner["fuel_threshold"] == 20
    assert owner["texts"]["webapp.tab.home"] == "Главная" and "docs.type.cdl" in owner["texts"]
    viewer = (await client.get("/api/app/me", headers=auth(5002))).json()
    assert "users" not in viewer["sections"]
    assert (await client.get("/api/app/users", headers=auth(5002))).status_code == 403


# --- Alertlar ---

async def _fleet(session):
    north, south = Depot(name="North"), Depot(name="South")
    session.add_all([north, south])
    await session.commit()
    session.add_all([Vehicle(samsara_id="v-n", name="North Truck", depot=north, fuel_percent=10),
                     Vehicle(samsara_id="v-s", name="South Truck", depot=south)])
    await session.commit()
    for event, veh, name in (("n1", "v-n", "North Truck"), ("s1", "v-s", "South Truck")):
        await ingest_alert(session, normalized(event, "crash", vehicle_samsara_id=veh, vehicle_name=name), owner_chat_id=OWNER_CHAT_ID)
    return north


async def test_alert_list_is_scoped_and_actions_work(session, client):
    north = await _fleet(session)
    await add_user(session, OWNER_CHAT_ID, UserRole.OWNER)
    disp = await add_user(session, 5003, UserRole.DISPATCHER)
    (await disp.awaitable_attrs.depots).append(north)
    await session.commit()

    assert (await client.get("/api/app/alerts", headers=auth(OWNER_CHAT_ID))).json()["total"] == 2
    mine = (await client.get("/api/app/alerts", headers=auth(5003))).json()
    assert mine["total"] == 1 and mine["items"][0]["vehicle"] == "North Truck"

    foreign = await session.scalar(select(Alert).where(Alert.samsara_alert_id == "s1"))
    assert (await client.get(f"/api/app/alerts/{foreign.id}", headers=auth(5003))).status_code == 404
    assert (await client.post(f"/api/app/alerts/{foreign.id}/action", headers=auth(5003), json={"action": "ack"})).status_code == 404

    own = mine["items"][0]["id"]
    detail = (await client.get(f"/api/app/alerts/{own}", headers=auth(5003))).json()
    assert detail["can"]["ack"] and not detail["can"]["resolve"]
    done = await client.post(f"/api/app/alerts/{own}/action", headers=auth(5003), json={"action": "resolve", "reason": "false_alarm"})
    assert done.status_code == 400  # avval qabul qilish kerak
    ok = await client.post(f"/api/app/alerts/{own}/action", headers=auth(5003), json={"action": "ack"})
    assert ok.status_code == 200 and ok.json()["alert"]["status"] == "acknowledged"
    resolved = await client.post(f"/api/app/alerts/{own}/action", headers=auth(5003), json={"action": "resolve", "reason": "false_alarm"})
    assert resolved.json()["alert"]["resolution_reason"] == "false_alarm"
    assert (await session.get(Alert, own, populate_existing=True)).status == AlertStatus.RESOLVED
    assert (await client.post(f"/api/app/alerts/{own}/action", headers=auth(5003), json={"action": "bogus"})).status_code == 400

    viewer = await add_user(session, 5004, UserRole.VIEWER)
    assert viewer
    assert (await client.post(f"/api/app/alerts/{foreign.id}/action", headers=auth(5004), json={"action": "ack"})).status_code in (400, 403)


async def test_dashboard_and_vehicles(session, client):
    await _fleet(session)
    await add_user(session, OWNER_CHAT_ID, UserRole.OWNER)
    data = (await client.get("/api/app/dashboard", headers=auth(OWNER_CHAT_ID))).json()
    assert data["open"] == 2 and data["new"] == 2 and data["vehicles"] == 2 and data["low_fuel"] == 1
    assert len(data["week"]) == 7 and sum(d["count"] for d in data["week"]) == 2
    vehicles = (await client.get("/api/app/vehicles?q=north", headers=auth(OWNER_CHAT_ID))).json()["items"]
    assert [v["name"] for v in vehicles] == ["North Truck"] and vehicles[0]["depot"] == "North"
    hos = (await client.get("/api/app/hos", headers=auth(OWNER_CHAT_ID))).json()
    assert hos["items"] == [] and hos["error"]  # testda Samsara token yo'q


# --- Hujjatlar ---

async def test_document_upload_send_delete(session, client):
    await add_user(session, OWNER_CHAT_ID, UserRole.OWNER)
    files = {"file": ("policy.pdf", b"%PDF-1.4 test", "application/pdf")}
    form = {"doc_type": "insurance", "owner_type": "company", "title": "Progressive", "expires_at": "2027-12-31"}
    created = await client.post("/api/app/documents", headers=auth(OWNER_CHAT_ID), files=files, data=form)
    assert created.status_code == 200, created.text
    doc = created.json()["document"]
    assert doc["expires_at"] == "2027-12-31" and doc["can_delete"]
    assert client.bot.documents[0]["chat_id"] == OWNER_CHAT_ID
    stored = await session.scalar(select(Document))
    assert stored.file_id == "tg-file-1" and stored.title == "Progressive"

    bad = await client.post("/api/app/documents", headers=auth(OWNER_CHAT_ID),
                            files={"file": ("x.exe", b"MZ", "application/x-msdownload")}, data=form)
    assert bad.status_code == 400
    bad_date = await client.post("/api/app/documents", headers=auth(OWNER_CHAT_ID), files=files, data={**form, "expires_at": "ertaga"})
    assert bad_date.status_code == 400

    listing = (await client.get("/api/app/documents?owner=company", headers=auth(OWNER_CHAT_ID))).json()
    assert [d["title"] for d in listing["items"]] == ["Progressive"]
    assert (await client.get("/api/app/documents?expiring=true", headers=auth(OWNER_CHAT_ID))).json()["items"] == []
    sent = await client.post(f"/api/app/documents/{doc['id']}/send", headers=auth(OWNER_CHAT_ID))
    assert sent.status_code == 200 and client.bot.documents[-1]["document"] == "tg-file-1"
    assert (await client.delete(f"/api/app/documents/{doc['id']}", headers=auth(OWNER_CHAT_ID))).status_code == 200
    assert await session.scalar(select(Document)) is None


async def test_driver_documents_are_limited(session, client):
    aziz, bek = Driver(samsara_id="d1", full_name="Aziz"), Driver(samsara_id="d2", full_name="Bek")
    session.add_all([aziz, bek])
    await session.commit()
    await add_user(session, 5005, UserRole.DRIVER, driver_id=aziz.id)
    session.add(Document(owner_type="driver", owner_id=bek.id, doc_type="cdl", title="bek", file_id="f", file_unique_id="u",
                         file_name="b.pdf", mime_type="application/pdf"))
    await session.commit()
    other = await session.scalar(select(Document))
    assert (await client.get("/api/app/documents", headers=auth(5005))).json()["items"] == []
    assert (await client.post(f"/api/app/documents/{other.id}/send", headers=auth(5005))).status_code == 400
    # Haydovchi egasini o'zgartira olmaydi: har doim o'zining nomiga yoziladi.
    created = await client.post("/api/app/documents", headers=auth(5005), data={"doc_type": "cdl", "owner_type": "company"},
                                files={"file": ("cdl.pdf", b"x", "application/pdf")})
    assert created.status_code == 200 and created.json()["document"]["owner_id"] == aziz.id
    assert (await client.get("/api/app/alerts", headers=auth(5005))).json()["total"] == 0  # faqat o'z alertlari


# --- Hisobotlar ---

async def test_report_and_export(session, client):
    await _fleet(session)
    await add_user(session, OWNER_CHAT_ID, UserRole.OWNER)
    report = (await client.get("/api/app/reports?period=7d", headers=auth(OWNER_CHAT_ID))).json()
    assert report["total"] == 2 and report["by_type"][0]["type"] == "crash" and report["can_export"]
    assert (await client.get("/api/app/reports?period=bogus", headers=auth(OWNER_CHAT_ID))).status_code == 422
    sent = await client.post("/api/app/reports/send", headers=auth(OWNER_CHAT_ID), json={"period": "7d", "fmt": "xlsx"})
    assert sent.status_code == 200
    assert client.bot.documents[-1]["document"].filename.endswith(".xlsx")
    assert (await client.post("/api/app/reports/send", headers=auth(OWNER_CHAT_ID), json={"period": "7d", "fmt": "exe"})).status_code == 400


# --- Foydalanuvchilarni boshqarish ---

async def test_user_management(session, client):
    north = Depot(name="North")
    session.add(north)
    await session.commit()
    await add_user(session, OWNER_CHAT_ID, UserRole.OWNER)
    admin = await add_user(session, 5006, UserRole.ADMIN)
    pending = await add_user(session, 5007, UserRole.PENDING, status=UserStatus.PENDING, full_name="Newbie")

    listing = (await client.get("/api/app/users", headers=auth(OWNER_CHAT_ID))).json()
    assert listing["items"][0]["name"] == "Newbie" and listing["depots"] == [{"id": north.id, "name": "North"}]

    approved = await client.post(f"/api/app/users/{pending.id}", headers=auth(5006),
                                 json={"action": "approve", "role": "dispatcher", "depot_ids": [north.id]})
    assert approved.status_code == 200, approved.text
    assert approved.json()["user"]["status"] == "active" and approved.json()["user"]["depots"] == ["North"]
    assert any(m["chat_id"] == 5007 for m in client.bot.sent)

    # Admin Owner'ga ham, boshqa Admin'ga ham tega olmaydi; o'zini ham o'zgartira olmaydi.
    owner = await session.scalar(select(User).where(User.telegram_id == OWNER_CHAT_ID))
    assert (await client.post(f"/api/app/users/{owner.id}", headers=auth(5006), json={"action": "block"})).status_code == 400
    assert (await client.post(f"/api/app/users/{admin.id}", headers=auth(5006), json={"action": "block"})).status_code == 400
    assert (await client.post(f"/api/app/users/{pending.id}", headers=auth(5006), json={"action": "role", "role": "owner"})).status_code == 400

    blocked = await client.post(f"/api/app/users/{pending.id}", headers=auth(5006), json={"action": "block"})
    assert blocked.json()["user"]["status"] == "blocked"
    assert (await client.get("/api/app/me", headers=auth(5007))).status_code == 403  # bloklangan kira olmaydi


def test_static_app_is_served():
    from pathlib import Path

    root = Path(webapp.__file__).parent.parent / "webapp"
    assert (root / "index.html").exists() and (root / "app.js").exists()


async def test_static_route(client):
    response = await client.get("/app/")
    assert response.status_code == 200 and "telegram-web-app.js" in response.text
