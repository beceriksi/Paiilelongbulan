# scanners/goplus.py
import aiohttp
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("GemBot.GoPlus")

class GoPlusScanner:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.base_url = "https://api.gopluslabs.io/api/v1"

    # GoPlus zincir ID haritası
    CHAIN_MAP = {
        "ethereum": "1",
        "bsc": "56",
        "polygon": "137",
        "optimism": "10",
        "arbitrum": "42161",
        "avalanche": "43114",
        "base": "8453"
    }

    async def check_token_security(self, chain: str, token_address: str) -> Optional[Dict[str, Any]]:
        """Token güvenliğini GoPlus API üzerinden sorgular."""
        chain_id = self.CHAIN_MAP.get(chain.lower())
        if not chain_id:
            logger.warning(f"GoPlus, {chain} zincirini desteklemiyor veya tanımlanmamış.")
            return None

        url = f"{self.base_url}/token_security/{chain_id}?addresses={token_address}"
        try:
            async with self.session.get(url, timeout=10) as response:
                if response.status == 200:
                    res_data = await response.json()
                    if res_data.get("code") == 1:
                        # Sonuç adres anahtarı altında döner
                        return res_data.get("result", {}).get(token_address.lower())
                logger.warning(f"GoPlus API hata döndü: {response.status}")
        except Exception as e:
            logger.error(f"GoPlus güvenlik sorgusu hatası: {e}")
        return None
