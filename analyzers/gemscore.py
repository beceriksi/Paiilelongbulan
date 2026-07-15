# analyzers/gemscore.py
import logging
from typing import Dict, Any
from config import config

logger = logging.getLogger("GemBot.Analyzer.GemScore")

class GemScoreAnalyzer:
    async def calculate(self, analysis_results: Dict[str, Any]) -> Dict[str, Any]:
        """
        Ağırlıklı formüllerle Gem Score (0-100), Risk Score (0-100), 
        Rug Probability (%) ve Moon Probability (%) hesaplar.
        """
        scores = {
            "gem_score": 0.0,
            "risk_score": 0.0,
            "rug_probability": 0.0,
            "moon_probability": 0.0
        }

        try:
            # 1. Ağırlıklı Gem Score Hesaplama
            weighted_score = (
                (analysis_results["liquidity"].get("liquidity_score", 0) * (config.WEIGHT_LP_LOCK / 10.0)) +
                (analysis_results["wallets"].get("wallet_score", 0) * (config.WEIGHT_SMART_WALLET / 10.0)) +
                (analysis_results["holders"].get("holder_score", 0) * (config.WEIGHT_HOLDER_GROWTH / 10.0)) +
                (analysis_results["deployer"].get("deployer_reputation_score", 0) * (config.WEIGHT_DEPLOYER_REPUTATION / 10.0)) +
                (analysis_results["social"].get("social_score", 0) * (config.WEIGHT_SOCIAL_SCORE / 10.0)) +
                (analysis_results["volume"].get("volume_momentum", 0) * (config.WEIGHT_VOLUME_MOMENTUM / 10.0)) +
                (analysis_results["washtrade"].get("wash_trade_score", 0) * (config.WEIGHT_LIQUIDITY_MOMENTUM / 10.0))
            ) / 10.0 # Normalize et

            scores["gem_score"] = min(max(weighted_score, 0.0), 100.0)

            # 2. Risk Skoru ve Rug Olasılığı Hesaplama
            rug_check = analysis_results["rugcheck"]
            base_risk = rug_check.get("rug_score", 0)
            
            if rug_check.get("is_honeypot"):
                scores["rug_probability"] = 99.0
                scores["risk_score"] = 100.0
            else:
                # Toplam risk = kontrat riski + cüzdan riskleri
                extra_risk = 0
                if analysis_results["holders"].get("is_dangerous_concentration"):
                    extra_risk += 20
                if analysis_results["washtrade"].get("wash_trade_detected"):
                    extra_risk += 15
                
                scores["risk_score"] = min(base_risk + extra_risk, 100.0)
                scores["rug_probability"] = min(scores["risk_score"] * 0.95, 99.0)

            # 3. Moon Probability (Yükseliş Potansiyeli) Hesaplama
            if scores["risk_score"] > 60:
                # Güvenlik riski yüksekse yükseliş şansı sıfırlanır
                scores["moon_probability"] = 5.0
            else:
                # Hacim, sosyal güç ve akıllı para girişi moon olasılığını tetikler
                momentum = (
                    analysis_results["volume"].get("volume_momentum", 0) * 0.4 +
                    analysis_results["social"].get("social_score", 0) * 0.3 +
                    analysis_results["wallets"].get("wallet_score", 0) * 0.3
                )
                scores["moon_probability"] = min(max(momentum, 10.0), 95.0)

        except Exception as e:
            logger.error(f"GemScore hesaplama hatası: {e}")

        return scores
