# storage/cache.py
import time
from typing import Any, Dict, Optional

class MemoryCache:
    def __init__(self, default_ttl: int = 3600):
        self._cache: Dict[str, Dict[str, Any]] = {}
        self.default_ttl = default_ttl

    async def get(self, key: str) -> Optional[Any]:
        if key not in self._cache:
            return None
        
        item = self._cache[key]
        if time.time() > item["expires_at"]:
            await self.delete(key)
            return None
            
        return item["value"]

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        expire_in = ttl if ttl is not None else self.default_ttl
        self._cache[key] = {
            "value": value,
            "expires_at": time.time() + expire_in
        }

    async def delete(self, key: str) -> None:
        self._cache.pop(key, None)

    async def clear_expired(self) -> None:
        now = time.time()
        expired_keys = [k for k, v in self._cache.items() if now > v["expires_at"]]
        for k in expired_keys:
            self._cache.pop(k, None)

# Global önbellek yöneticisi
cache = MemoryCache()
