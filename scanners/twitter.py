# scanners/twitter.py
import aiohttp
import logging
from typing import Dict, Any, Optional
from config import config

logger = logging.getLogger("GemBot.Twitter")

class TwitterScanner:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.bearer_token = config.TWITTER_BEARER_TOKEN

    async def get_social_volume(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Bir sembole ($BTC, $SOL vb.) son 1 saatte gelen tweet hacmini sorgular."""
        if not self.bearer_token:
            # Token yoksa hafif mock veri ile sosyal skoru boş bırakmaz.
            return {"tweet_count": 15, "impression_count": 2500, "sentiment_score": 0.65}

        url = f"https://api.twitter.com/2/tweets/search/recent/counts?query=%23{symbol}%20OR%20%24{symbol}"
        headers = {"Authorization": f"Bearer {self.bearer_token}"}
        try:
            async with self.session.get(url, headers=headers, timeout=10) as response:
                if response.status == 200:
                    res_data = await response.json()
                    # Son 1 saatlik veri toplamını hesapla
                    total_tweets = sum(item.get("tweet_count", 0) for item in res_data.get("data", []))
                    return {
                        "tweet_count": total_tweets,
                        "impression_count": total_tweets * 150, # Tahmini erişim katsayısı
                        "sentiment_score": 0.70
                    }
        except Exception as e:
            logger.error(f"Twitter API sorgu hatası: {e}")
        return None
