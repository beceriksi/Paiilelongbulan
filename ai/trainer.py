# ai/trainer.py
import logging
import aiosqlite
import numpy as np
from typing import List, Tuple
from config import config

logger = logging.getLogger("GemBot.AI.Trainer")

class AITrainer:
    def __init__(self, db_path: str):
        self.db_path = db_path

    async def collect_training_data(self) -> List[Tuple[float, float, float, float, int]]:
        """
        Veritabanından geçmiş başarılı ve başarısız token verilerini eğitim seti olarak çeker.
        """
        samples = []
        try:
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute(
                    "SELECT mcap, liquidity, gem_score, risk_score FROM detected_gems"
                ) as cursor:
                    rows = await cursor.fetchall()
                    for row in rows:
                        mcap, liq, gem_score, risk_score = row
                        # Hedef Değişken (Y): Eğer risk_score < 30 ve gem_score > 75 ise başarılı (1), değilse (0)
                        label = 1 if (risk_score < 30 and gem_score > 75) else 0
                        samples.append((mcap, liq, gem_score, risk_score, label))
        except Exception as e:
            logger.error(f"Eğitim verisi toplanamadı: {e}")
        return samples

    async def train_model(self) -> Dict[str, Any]:
        """
        En küçük kareler yöntemi veya basit gradyan inişi (gradient descent) ile katsayıları optimize eder.
        """
        data = await self.collect_training_data()
        if len(data) < 10:
            logger.info("Eğitim için yeterli veri seti henüz birikmedi (En az 10 kayıt gerekiyor).")
            return {"status": "insufficient_data"}

        try:
            # Numpy ile matris hazırlığı
            X = np.array([[s[0]/max(s[1],1), s[2]/100, s[3]/100] for s in data])
            y = np.array([s[4] for s in data])

            # Basit doğrusal regresyon çözümü
            # X^T * X * beta = X^T * y
            XT_X = np.dot(X.T, X)
            XT_y = np.dot(X.T, y)
            
            # Singüler matris kontrolü ve katsayı güncelleme
            if np.linalg.det(XT_X) != 0:
                beta = np.linalg.solve(XT_X, XT_y)
                logger.info(f"AI Model katsayıları başarıyla güncellendi: {beta}")
                return {"status": "success", "coefficients": beta.tolist()}
            
        except Exception as e:
            logger.error(f"AI Model eğitimi sırasında hata: {e}")
        
        return {"status": "failed"}
