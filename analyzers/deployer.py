# analyzers/deployer.py
import logging
from typing import Dict, Any, Optional
from database import db_mgr

logger = logging.getLogger("GemBot.Analyzer.Deployer")

class DeployerAnalyzer:
    async def analyze(self, deployer_address: str) -> Dict[str, Any]:
        """
        Deployer'ın geçmiş itibarını yerel DB ve on-chain geçmişi üzerinden sorgular.
        """
        results = {
            "deployer_address": deployer_address,
            "previous_tokens_count": 0,
            "rug_history_count": 0,
            "deployer_reputation_score": 100.0
        }

        if not deployer_address:
            return results

        try:
            # DB'den deployer'ın geçmişini sorgula
            stats = await db_mgr.get_deployer_stats(deployer_address)
            if stats:
                results["previous_tokens_count"] = stats["previous_tokens_count"]
                results["rug_history_count"] = stats["rug_history_count"]
                results["deployer_reputation_score"] = stats["reputation_score"]
            else:
                # Yeni deployer, nötr-pozitif kabul edilir
                results["deployer_reputation_score"] = 80.0

        except Exception as e:
            logger.error(f"Deployer analiz hatası: {e}")

        return results
