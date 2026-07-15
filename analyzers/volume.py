# analyzers/volume.py
import logging
from typing import Dict, Any

logger = logging.getLogger("GemBot.Analyzer.Volume")

class VolumeAnalyzer:
    async def analyze(self, raw_pair_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        İşlem hacmi artışlarını ve alım-satım dengesini analiz eder.
        """
        results = {
            "volume_24h": 0.0,
            "volume_growth_pct": 0.0,
            "buy_sell_ratio": 1.0,
            "volume_momentum": 0 # 0-100 arası güç puanı
        }

        if not raw_pair_data:
            return results

        try:
            results["volume_24h"] = float(raw_pair_data.get("volume", {}).get("h24", 0.0))
            
            # Farklı zaman dilimlerindeki hacim dengesiyle momentum ölçümü
            vol_m5 = float(raw_pair_data.get("volume", {}).get("m5", 0.0))
            vol_h1 = float(raw_pair_data.get("volume", {}).get("h1", 0.0))
            
            if vol_h1 > 0:
                # 5 dakikalık hacmin 1 saatlik hacme oranı normun üstündeyse ivmelenme vardır
                results["volume_growth_pct"] = (vol_m5 / (vol_h1 / 12.0)) * 100.0

            buys = float(raw_pair_data.get("txns", {}).get("h1", {}).get("buys", 1))
            sells = float(raw_pair_data.get("txns", {}).get("h1", {}).get("sells", 1))
            
            if sells > 0:
                results["buy_sell_ratio"] = buys / sells

            # Hacim ivme skoru
            score = 0
            if results["volume_growth_pct"] > 150: score += 50
            elif results["volume_growth_pct"] > 100: score += 30
            
            if results["buy_sell_ratio"] > 1.5: score += 50
            elif results["buy_sell_ratio"] > 1.1: score += 30

            results["volume_momentum"] = min(score, 100)

        except Exception as e:
            logger.error(f"Volume analiz hatası: {e}")

        return results
