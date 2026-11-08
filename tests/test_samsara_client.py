import httpx
import pytest

from app.services.samsara.client import SamsaraAPIError, SamsaraClient


def _client(handler, sleeps: list[float] | None = None) -> SamsaraClient:
    async def fake_sleep(seconds: float) -> None:
        if sleeps is not None:
            sleeps.append(seconds)

    return SamsaraClient("secret-token", transport=httpx.MockTransport(handler), sleep=fake_sleep)


async def test_pagination_collects_all_pages_and_sends_bearer_token():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if "after" not in request.url.params:
            return httpx.Response(200, json={"data": [{"id": "1"}], "pagination": {"endCursor": "c1", "hasNextPage": True}})
        return httpx.Response(200, json={"data": [{"id": "2"}], "pagination": {"endCursor": "", "hasNextPage": False}})

    async with _client(handler) as client:
        vehicles = await client.list_vehicles()

    assert [v["id"] for v in vehicles] == ["1", "2"]
    assert seen[0].headers["Authorization"] == "Bearer secret-token"
    assert "secret-token" not in str(seen[0].url)
    assert seen[1].url.params["after"] == "c1"


async def test_rate_limit_is_retried_with_retry_after():
    calls = {"n": 0}
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, json={"data": []})

    async with _client(handler, sleeps) as client:
        assert await client.list_drivers() == []
    assert sleeps == [2.0]


async def test_client_error_raises():
    async with _client(lambda request: httpx.Response(401, text="unauthorized")) as client:
        with pytest.raises(SamsaraAPIError) as info:
            await client.list_vehicles()
    assert info.value.status_code == 401


async def test_vehicle_stats_params():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(request.url.params)
        return httpx.Response(200, json={"data": [{"id": "1", "fuelPercent": {"value": 40}}]})

    async with _client(handler) as client:
        stats = await client.get_vehicle_stats(types=("gps", "fuelPercents"), vehicle_ids=["1", "2"])
    assert captured == {"types": "gps,fuelPercents", "vehicleIds": "1,2"}
    assert stats[0]["fuelPercent"]["value"] == 40
