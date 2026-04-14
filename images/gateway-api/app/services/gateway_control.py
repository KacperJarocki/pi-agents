import httpx

from ..core.config import get_settings


class GatewayAgentClient:
    def __init__(self):
        self._settings = get_settings()

    async def get_status(self) -> dict:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{self._settings.gateway_agent_url}/status")
            r.raise_for_status()
            return r.json()

    async def validate(self, cfg: dict) -> dict:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(f"{self._settings.gateway_agent_url}/validate", json=cfg)
            r.raise_for_status()
            return r.json()

    async def apply(self, cfg: dict) -> dict:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(f"{self._settings.gateway_agent_url}/apply", json=cfg)
            r.raise_for_status()
            return r.json()

    async def rollback(self) -> dict:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(f"{self._settings.gateway_agent_url}/rollback")
            r.raise_for_status()
            return r.json()
