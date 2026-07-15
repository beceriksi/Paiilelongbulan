# ai/scorer.py
import logging
import numpy as np
from typing import Dict, Any

logger = logging.getLogger("GemBot.AI.Scorer")

class AIScorer:
    def __init__(self):
        # Üretim ortamı için eğitilmiş varsayılan katsayılar (Ağırlık vektörü)
        # Sırasıyla: [mcap_liquidity_ratio, volume_momentum, wallet_score, risk_score]
        self.weights = np.array([0.25, 0.35, 0.30, -0.20])
        self.bias = 15.0

    async def predict_ai_score(self, analysis_results: Dict[str, Any], gem_score: float) -> float:
        """
        Numpy kullanarak hafif ve hızlı bir çoklu değişkenli skor tahmini yapar.
        """
        try:
            # Girdileri normalize et (0 - 1.0 arasına çek)
            mcap = float(analysis_results["volume"].get("volume_24h", 0) * 1.5) # Tahmini mcap proxy
            liq = float(analysis_results["liquidity"].get("liquidity_usd", 1))
            mcap_liq_ratio = min(mcap / max(liq, 1.0), 10.0) / 10.0 # Güvenlik rasyosu

            vol_momentum = float(analysis_results["volume"].get("volume_momentum", 0)) / 100.0
            wallet_score = float(analysis_results["wallets"].get("wallet_score", 0)) / 100.0
            risk_score = float(analysis_results["rugcheck"].get("rug_score", 0)) / 100.0

            # Karar matrisi
            features = np.array([mcap_liq_ratio, vol_momentum, wallet_score, risk_score])
            
            # Matris çarpımı ile doğrusal skor tahmini (Linear Decision Boundary)
            raw_ai_score = np.dot(features, self.weights) * 100.0 + self.bias
            
            # Hibrit yaklaşım: Statik Gem Score ile AI skorunu harmanla (%70 Statik, %30 Yapay Zeka)
            hybrid_score = (gem_score * 0.70) + (raw_ai_score * 0.30)

            return round(min(max(hybrid_score, 0.0), 100.0), 2)

        except Exception as e:
            logger.error(f"AI Scorer tahmini sırasında hata: {e}")
            return round(gem_score, 2) # Hata anında güvenli fallback
