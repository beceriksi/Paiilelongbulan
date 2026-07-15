# analyzers/holders.py
import logging
from typing import Dict, Any, List

logger = logging.getLogger("GemBot.Analyzer.Holders")

class HoldersAnalyzer:
    async def analyze(self, raw_holders_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Holder sayısı, ilk 10 holder'ın toplam arzı kontrol etme oranı ve holder büyümesini ölçer.
        """
        results = {
            "holder_count": 0,
            "top10_concentration_pct": 0.0,
            "holder_growth_pct": 0.0,
            "is_dangerous_concentration": False,
            "holder_score": 0
        }

        if not raw_holders_data:
            return results

        try:
            results["holder_count"] = len(raw_holders_data)
            
            # İlk 10 cüzdanın arz payını hesapla
            sorted_holders = sorted(raw_holders_data, key=lambda x: float(x.get("percent", 0.0)), reverse=True)
            top10_sum = sum(float(h.get("percent", 0.0)) for h in sorted_holders[:10]) * 100
            
            results["top10_concentration_pct"] = top10_sum
            results["is_dangerous_concentration"] = top10_sum > 35.0 # %35 üstü riskli kabul edilir

            # Holder puanlama
            score = 100
            if results["is_dangerous_concentration"]:
                score -= 40
            if results["holder_count"] < 100:
                score -= 30
            elif results["holder_count"] > 1000:
                score += 10 # Ekstra bonus
                
            results["holder_score"] = max(0, min(score, 100))

        except Exception as e:
            logger.error(f"Holders analiz hatası: {e}")

        return results
