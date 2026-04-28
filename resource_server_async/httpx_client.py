import httpx
from typing import Any, Dict

class AsyncHttpClient:
    def __init__(
        self, 
        timeout: float = 30.0,
        headers: dict[str, str] = {"Content-Type": "application/json"}
    ):
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers=headers
        )
    
    async def get(self, url: str) -> Dict[Any, Any]:
        response = await self._client.get(url)
        response.raise_for_status()
        return response.json()
    
    async def post(self, url: str, data: dict = None) -> Dict[Any, Any]:
        response = await self._client.post(url, json=data)
        response.raise_for_status()
        return response.json()
    
    async def close(self):
        await self._client.aclose()