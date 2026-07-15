# analyzers/wallets.py
import logging
from typing import Dict, Any

logger = logging.getLogger("GemBot.Analyzer.Wallets")

class WalletsAnalyzer:
    async def analyze(self, raw_wallet_analytics: Dict[str, Any]) -> Dict[str, Any]:
        """
        Cüzdan tiplerinin (Smart, Fresh, Insider, Sniper) taranan token'daki oranını hesaplar.
        """
        results = {
            "smart_wallets_count": 0,
            "fresh_wallets_count": 0,
            "insider_wallets_count": 0,
            "sniper_wallets_count": 0,
            "bundle_detected": False,
            "wallet_clusters_detected": False,
            "wallet_score": 0
        }

        if not raw_wallet_analytics:
            return results

        try:
            results["smart_wallets_count"] = raw_wallet_analytics.get("smart_wallets_count", 0)
            results["fresh_wallets_count"] = raw_wallet_analytics.get("fresh_wallets_count", 0)
            results["insider_wallets_count"] = raw_wallet_analytics.get("insider_wallets_count", 0)
            results["sniper_wallets_count"] = raw_wallet_analytics.get("sniper_wallets_count", 0)
            results["bundle_detected"] = raw_wallet_analytics.get("bundle_detected", False)
            
            # Kümelenmiş (birbiriyle ilişkili) cüzdan tespiti simülasyonu
            if results["insider_wallets_count"] > 3 or results["sniper_wallets_count"] > 10:
                results["wallet_clusters_detected"] = True

            # Cüzdan kalitesi skorlaması
            score = 30 # Başlangıç taban puanı
            score += results["smart_wallets_count"] * 10
            score -= results["insider_wallets_count"] * 15
            
            if results["bundle_detected"]:
                score -= 30
            if results["fresh_wallets_count"] > 20:
                score += 15 # Yeni cüzdan ilgisi iyidir

            results["wallet_score"] = max(0, min(score, 100))

        except Exception as e:
            logger.error(f"Wallets analiz hatası: {e}")

        return results
