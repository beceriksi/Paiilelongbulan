# scanners/birdeye.py
import aiohttp
import logging
from typing import Dict, Any, Optional
from config import config

logger = logging.getLogger("GemBot.Birdeye")

class BirdeyeScanner:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.headers = {
            "X-API-KEY": config.BIRDEYE_API_KEY,
            "accept": "application/json"
        }

    async def get_token_overview(self, chain: str, token_address: str) -> Optional[Dict[str, Any]]:
        """Token'ın genel piyasa metriklerini (fiyat, mcap, hacim) getirir."""
        url = f"https://public-api.birdeye.so/defi/token_overview?address={token_address}"
        headers = {**self.headers, "x-chain": chain.lower()}
        
        try:
            async with self.session.get(url, headers=headers, timeout=10) as response:
                if response.status == 200:
                    res_data = await response.json()
                    if res_data.get("success"):
                        return res_data.get("data")
                else:
                    logger.warning(f"Birdeye API hata kodu: {response.status}")
        except Exception as e:
            logger.error(f"Birdeye API sorgu hatası: {e}")
        return None
