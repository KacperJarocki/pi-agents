import httpx

from ..core.config import get_settings


class GatewayAgentClient:
    def __init__(self):
        self._settings = get_settings()

    async def get_status(self) -> tuple[int, dict | str]:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{self._settings.gateway_agent_url}/status")
            return _coerce_response(r)

    async def validate(self, cfg: dict) -> tuple[int, dict | str]:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(f"{self._settings.gateway_agent_url}/validate", json=cfg)
            return _coerce_response(r)

    async def apply(self, cfg: dict) -> tuple[int, dict | str]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(f"{self._settings.gateway_agent_url}/apply", json=cfg)
            return _coerce_response(r)

    async def rollback(self) -> tuple[int, dict | str]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(f"{self._settings.gateway_agent_url}/rollback")
            return _coerce_response(r)

    async def block_device(self, mac: str) -> tuple[int, dict | str]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(f"{self._settings.gateway_agent_url}/block", json={"mac": mac})
            return _coerce_response(r)

    async def unblock_device(self, mac: str) -> tuple[int, dict | str]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.delete(f"{self._settings.gateway_agent_url}/block/{mac}")
            return _coerce_response(r)

    async def list_blocked(self) -> tuple[int, dict | str]:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{self._settings.gateway_agent_url}/blocked")
            return _coerce_response(r)


def _coerce_response(r: httpx.Response) -> tuple[int, dict | str]:
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, r.text
