# analyzers/rugcheck.py
import logging
from typing import Dict, Any

logger = logging.getLogger("GemBot.Analyzer.RugCheck")

class RugCheckAnalyzer:
    async def analyze(self, raw_security_data: Dict[str, Any], chain: str) -> Dict[str, Any]:
        """
        GoPlus veya Helius'tan gelen ham güvenlik verilerini normalize eder ve riskleri puanlar.
        """
        results = {
            "is_honeypot": False,
            "is_mintable": False,
            "has_freeze_authority": False,
            "buy_tax": 0.0,
            "sell_tax": 0.0,
            "is_proxy": False,
            "trustworthy": True,
            "rug_score": 0 # 0-100 arası rug riski (100 en tehlikeli)
        }

        if not raw_security_data:
            results["rug_score"] = 50  # Veri yoksa nötr-risk kabul edilir
            return results

        try:
            # EVM Analizi (GoPlus verisi varsayımıyla)
            if chain.lower() != "solana":
                results["is_honeypot"] = raw_security_data.get("is_honeypot", "0") == "1"
                results["is_mintable"] = raw_security_data.get("is_mintable", "0") == "1"
                results["has_freeze_authority"] = raw_security_data.get("cannot_buy", "0") == "1" # Alım engeli var mı?
                results["buy_tax"] = float(raw_security_data.get("buy_tax", 0.0)) * 100
                results["sell_tax"] = float(raw_security_data.get("sell_tax", 0.0)) * 100
                results["is_proxy"] = raw_security_data.get("is_proxy", "0") == "1"

                # Risk puanlama mantığı
                risk = 0
                if results["is_honeypot"]: risk += 100
                if results["is_mintable"]: risk += 30
                if results["has_freeze_authority"]: risk += 40
                if results["buy_tax"] > 10 or results["sell_tax"] > 10: risk += 25
                if results["is_proxy"]: risk += 15
                results["rug_score"] = min(risk, 100)
            
            # Solana Analizi (Helius/RugCheck verisi varsayımıyla)
            else:
                # Solana token detayları kontrolü
                results["is_mintable"] = raw_security_data.get("mintable", False)
                results["has_freeze_authority"] = raw_security_data.get("freezable", False)
                
                risk = 0
                if results["is_mintable"]: risk += 50
                if results["has_freeze_authority"]: risk += 50
                results["rug_score"] = min(risk, 100)

            # Güven sınırı aşımı kontrolü
            if results["is_honeypot"] or results["rug_score"] >= 70:
                results["trustworthy"] = False

        except Exception as e:
            logger.error(f"RugCheck analiz hatası: {e}")
            results["rug_score"] = 50

        return results
    
