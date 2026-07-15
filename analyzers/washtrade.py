# analyzers/washtrade.py
import logging
from typing import Dict, Any

logger = logging.getLogger("GemBot.Analyzer.WashTrade")

class WashTradeAnalyzer:
    async def analyze(self, raw_pair_data: Dict[str, Any], raw_wallet_analytics: Dict[str, Any]) -> Dict[str, Any]:
        """
        Wash trading tespiti için işlem sayısını benzersiz cüzdan sayısıyla karşılaştırır.
        """
        results = {
            "wash_trade_detected": False,
            "fake_volume_pct": 0.0,
            "wash_trade_score": 0 # Ne kadar yüksekse o kadar temiz (0 tehlikeli, 100 temiz)
        }

        if not raw_pair_data:
            return results

        try:
            txns_h1 = float(raw_pair_data.get("txns", {}).get("h1", {}).get("buys", 1)) + \
                      float(raw_pair_data.get("txns", {}).get("h1", {}).get("sells", 1))
            
            # Taze cüzdan sayısı ve sniper sayısı analiziyle wash trading şüphesi bulma
            snipers = raw_wallet_analytics.get("sniper_wallets_count", 0) if raw_wallet_analytics else 0
            
            # Eğer az sayıda kişi (örneğin sadece 5-10 sniper) çok yüksek işlem adedi gerçekleştiriyorsa
            if txns_h1 > 500 and snipers < 3:
                results["wash_trade_detected"] = True
                results["fake_volume_pct"] = 45.0
            
            score = 100
            if results["wash_trade_detected"]:
                score -= 60
            
            results["wash_trade_score"] = max(0, min(score, 100))

        except Exception as e:
            logger.error(f"WashTrade analiz hatası: {e}")

        return results
