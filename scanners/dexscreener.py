# scanners/dexscreener.py
import aiohttp
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger("GemBot.DexScreener")

class DexScreenerScanner:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.base_url = "https://api.dexscreener.com/latest/dex"

    async def _request(self, endpoint: str) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/{endpoint}"
        try:
            async with self.session.get(url, timeout=10) as response:
                if response.status == 200:
                    return await response.json()
                logger.warning(f"DexScreener API hata döndü: {response.status}")
        except Exception as e:
            logger.error(f"DexScreener istek hatası: {e}")
        return None

    async def scan_new_pairs(self) -> List[Dict[str, Any]]:
        """Yeni eklenen çiftleri arar (DexScreener'ın token ve çift keşif mekanizması üzerinden)."""
        # Not: DexScreener'ın en popüler genel yeni likidite izleme endpoint'ini simüle eder.
        data = await self._request("search?q=WETH") # Örnek WETH çiftleri araması
        if data and "pairs" in data:
            return data["pairs"][:15]  # En güncel 15 çifti dön
        return []

    async def get_token_pairs(self, token_address: str) -> List[Dict[str, Any]]:
        """Belirli bir token adresine ait tüm likidite çiftlerini getirir."""
        data = await self._request(f"tokens/{token_address}")
        if data and "pairs" in data:
            return data["pairs"]
        return []
