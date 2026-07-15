# scanners/helius.py
import aiohttp
import logging
from typing import Dict, Any, Optional
from config import config

logger = logging.getLogger("GemBot.Helius")

class HeliusScanner:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.api_key = config.HELIUS_API_KEY

    async def get_solana_token_metadata(self, token_address: str) -> Optional[Dict[str, Any]]:
        """DAS (Digital Asset Standard) API kullanarak Solana token detaylarını asenkron çeker."""
        if not self.api_key:
            return None

        url = f"https://mainnet.helius-rpc.com/?api-key={self.api_key}"
        payload = {
            "jsonrpc": "2.0",
            "id": "gem-bot-request",
            "method": "getAsset",
            "params": {
                "id": token_address
            }
        }
        try:
            async with self.session.post(url, json=payload, timeout=10) as response:
                if response.status == 200:
                    res_data = await response.json()
                    return res_data.get("result")
        except Exception as e:
            logger.error(f"Helius API DAS sorgu hatası: {e}")
        return None
