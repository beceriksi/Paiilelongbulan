# analyzers/liquidity.py
import logging
from typing import Dict, Any

logger = logging.getLogger("GemBot.Analyzer.Liquidity")

class LiquidityAnalyzer:
    async def analyze(self, raw_pair_data: Dict[str, Any], raw_security_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Likidite büyüklüğünü, kilit durumunu (LP Lock) ve yanma oranını (LP Burn) çıkarır.
        """
        results = {
            "liquidity_usd": 0.0,
            "lp_locked": False,
            "lp_burned": False,
            "lp_lock_pct": 0.0,
            "liquidity_growth_24h": 0.0,
            "liquidity_score": 0 # 0-100 arası güç puanı
        }

        if not raw_pair_data:
            return results

        try:
            # DexScreener yapısından veri ayıklama
            results["liquidity_usd"] = float(raw_pair_data.get("liquidity", {}).get("usd", 0.0))
            price_change_24h = float(raw_pair_data.get("priceChange", {}).get("h24", 0.0))
            
            # Likidite büyüme tahmini (hacim ile korelasyonlu)
            results["liquidity_growth_24h"] = price_change_24h * 0.4 # Korelasyon tahmini
            
            # LP Lock/Burn kontrolü (GoPlus'tan beslenir)
            if raw_security_data:
                lp_holders = raw_security_data.get("lp_holders", [])
                total_burned = 0.0
                total_locked = 0.0

                for holder in lp_holders:
                    is_burn_address = holder.get("address", "").lower() in [
                        "0x000000000000000000000000000000000000dead",
                        "0x0000000000000000000000000000000000000000",
                        "11111111111111111111111111111111" # Solana Null Address
                    ]
                    percent = float(holder.get("percent", 0.0)) * 100
                    
                    if is_burn_address:
                        total_burned += percent
                    elif holder.get("is_locked", 0) == 1:
                        total_locked += percent

                results["lp_burned"] = total_burned > 50.0
                results["lp_locked"] = (total_locked + total_burned) > 70.0
                results["lp_lock_pct"] = total_locked + total_burned

            # Likidite skorlaması
            score = 0
            if results["liquidity_usd"] >= 100000: score += 40
            elif results["liquidity_usd"] >= 20000: score += 20

            if results["lp_locked"] or results["lp_burned"]: score += 60
            elif results["lp_lock_pct"] > 50: score += 30

            results["liquidity_score"] = min(score, 100)

        except Exception as e:
            logger.error(f"Liquidity analiz hatası: {e}")

        return results
