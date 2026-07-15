# analyzers/social.py
import logging
import time
from typing import Dict, Any

logger = logging.getLogger("GemBot.Analyzer.Social")

class SocialAnalyzer:
    async def analyze(self, raw_twitter_data: Dict[str, Any], raw_pair_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Sosyal medya ve web sitelerinin popülerlik gücünü hesaplar.
        """
        results = {
            "twitter_volume": 0,
            "sentiment_score": 0.5,
            "website_age_days": 180, # Varsayılan güvenli gün sayısı
            "has_telegram": False,
            "social_score": 0
        }

        try:
            if raw_twitter_data:
                results["twitter_volume"] = raw_twitter_data.get("tweet_count", 0)
                results["sentiment_score"] = raw_twitter_data.get("sentiment_score", 0.5)

            if raw_pair_data:
                # DexScreener websiteleri ve sosyal medya linklerini döner
                websites = [info for info in raw_pair_data.get("info", {}).get("websites", [])]
                socials = [info for info in raw_pair_data.get("info", {}).get("socials", [])]
                
                results["has_telegram"] = any(s.get("type") == "telegram" for s in socials)
                
                if websites:
                    # Simüle edilmiş domain yaş kontrolü
                    results["website_age_days"] = 30 # Yeni açılmış web sitesi varsayımı
            
            # Sosyal Güç Skoru Hesaplama
            score = 10
            if results["twitter_volume"] > 50: score += 40
            elif results["twitter_volume"] > 10: score += 20
            
            if results["has_telegram"]: score += 30
            if results["website_age_days"] > 90: score += 20

            results["social_score"] = min(score, 100)

        except Exception as e:
            logger.error(f"Social analiz hatası: {e}")

        return results
