# tests/test_analyzers.py
import unittest
import asyncio
from analyzers.rugcheck import RugCheckAnalyzer
from analyzers.gemscore import GemScoreAnalyzer

class TestGemBotAnalyzers(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.rug_analyzer = RugCheckAnalyzer()
        self.gemscore_analyzer = GemScoreAnalyzer()

    def tearDown(self):
        self.loop.close()

    def test_rugcheck_honeypot(self):
        """RugCheck'in Honeypot riskini doğru etiketlediğini doğrular."""
        mock_raw_goplus = {"is_honeypot": "1", "cannot_buy": "1"}
        
        result = self.loop.run_until_complete(
            self.rug_analyzer.analyze(mock_raw_goplus, "ethereum")
        )
        self.assertTrue(result["is_honeypot"])
        self.assertEqual(result["rug_score"], 100)
        self.assertFalse(result["trustworthy"])

    def test_gemscore_calculation_flow(self):
        """Ağırlıklı Gem Score ve Risk Score hesaplamasını doğrular."""
        mock_analysis_results = {
            "rugcheck": {"rug_score": 10.0, "is_honeypot": False},
            "liquidity": {"liquidity_score": 90.0, "liquidity_usd": 150000.0, "liquidity_growth_24h": 5.0},
            "volume": {"volume_momentum": 85.0, "volume_24h": 500000.0, "volume_growth_pct": 120.0},
            "holders": {"holder_score": 80.0, "holder_count": 500, "is_dangerous_concentration": False},
            "wallets": {"wallet_score": 90.0, "smart_wallets_count": 8, "fresh_wallets_count": 15},
            "deployer": {"deployer_reputation_score": 95.0},
            "washtrade": {"wash_trade_score": 100.0, "wash_trade_detected": False},
            "social": {"social_score": 80.0}
        }

        scores = self.loop.run_until_complete(
            self.gemscore_analyzer.calculate(mock_analysis_results)
        )
        
        # Gem skoru 0 ile 100 arasında ve mantıklı olmalı
        self.assertGreater(scores["gem_score"], 50.0)
        self.assertLessEqual(scores["gem_score"], 100.0)
        # Honeypot olmadığı için rug olasılığı çok yüksek olmamalı
        self.assertLess(scores["rug_probability"], 30.0)

if __name__ == "__main__":
    unittest.main()
