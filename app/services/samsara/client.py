import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class SamsaraAPIError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"Samsara API {status_code}: {detail}")
        self.status_code = status_code


class SamsaraClient:
    """Samsara REST API (Bearer token). Token faqat sarlavhada — URL'ga hech qachon qo'yilmaydi."""

    def __init__(
        self,
        token: str,
        base_url: str = "https://api.samsara.com",
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 15.0,
        max_retries: int = 3,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=timeout,
            transport=transport,
        )
        self._max_retries = max_retries
        self._sleep = sleep

    async def __aenter__(self) -> "SamsaraClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def ping(self) -> None:
        await self._get("/fleet/vehicles", {"limit": "1"})

    async def list_vehicles(self) -> list[dict[str, Any]]:
        return await self._paginate("/fleet/vehicles")

    async def list_drivers(self) -> list[dict[str, Any]]:
        return await self._paginate("/fleet/drivers")

    async def get_vehicle_stats(
        self,
        types: Iterable[str] = ("gps", "fuelPercents", "engineStates"),
        vehicle_ids: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        params = {"types": ",".join(types)}
        if vehicle_ids:
            params["vehicleIds"] = ",".join(vehicle_ids)
        return await self._paginate("/fleet/vehicles/stats", params)

    async def get_hos_clocks(self, driver_ids: Iterable[str] | None = None) -> list[dict[str, Any]]:
        params = {"driverIds": ",".join(driver_ids)} if driver_ids else None
        return await self._paginate("/fleet/hos/clocks", params)

    async def _paginate(self, path: str, params: dict[str, str] | None = None) -> list[dict[str, Any]]:
        query = dict(params or {})
        items: list[dict[str, Any]] = []
        while True:
            body = await self._get(path, query)
            items.extend(body.get("data") or [])
            pagination = body.get("pagination") or {}
            if not (pagination.get("hasNextPage") and pagination.get("endCursor")):
                return items
            query["after"] = pagination["endCursor"]

    async def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        for attempt in range(self._max_retries + 1):
            response = await self._client.get(path, params=params)
            retryable = response.status_code == 429 or response.status_code >= 500
            if retryable and attempt < self._max_retries:
                delay = _retry_after(response) if response.status_code == 429 else 2**attempt
                logger.warning("Samsara API %s %s — %.1fs keyin qayta", response.status_code, path, delay)
                await self._sleep(delay)
                continue
            if response.is_error:
                raise SamsaraAPIError(response.status_code, response.text[:200])
            return response.json()
        raise AssertionError("unreachable")


def _retry_after(response: httpx.Response) -> float:
    try:
        return max(float(response.headers.get("Retry-After", "1")), 0.0)
    except ValueError:
        return 1.0
