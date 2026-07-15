# database.py
import aiosqlite
import logging
from typing import Optional
from config import config

logger = logging.getLogger("GemBot.Database")

class DatabaseManager:
    def __init__(self, db_path: str):
        self.db_path = db_path

    async def initialize(self) -> None:
        """Veritabanı tablolarını asenkron olarak ayağa kaldırır."""
        async with aiosqlite.connect(self.db_path) as db:
            # Sinyal gönderilen tokenlar tablosu
            await db.execute("""
                CREATE TABLE IF NOT EXISTS detected_gems (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    address TEXT UNIQUE NOT NULL,
                    name TEXT,
                    symbol TEXT,
                    chain TEXT NOT NULL,
                    mcap REAL,
                    liquidity REAL,
                    gem_score REAL,
                    risk_score REAL,
                    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # Deployer itibar ve geçmiş tablosu
            await db.execute("""
                CREATE TABLE IF NOT EXISTS deployers (
                    address TEXT PRIMARY KEY,
                    previous_tokens_count INTEGER DEFAULT 0,
                    rug_history_count INTEGER DEFAULT 0,
                    reputation_score REAL DEFAULT 100.0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.commit()
            logger.info("Asenkron veritabanı tabloları hazır.")

    async def is_gem_notified(self, address: str) -> bool:
        """Bu token adresi daha önce Telegram'a sinyal olarak atıldı mı?"""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT 1 FROM detected_gems WHERE address = ?", (address,)
            ) as cursor:
                result = await cursor.fetchone()
                return result is not None

    async def save_gem(self, address: str, name: str, symbol: str, chain: str, mcap: float, liquidity: float, gem_score: float, risk_score: float) -> None:
        """Keşfedilen gem'i veri tabanına kaydeder."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR IGNORE INTO detected_gems (address, name, symbol, chain, mcap, liquidity, gem_score, risk_score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (address, name, symbol, chain, mcap, liquidity, gem_score, risk_score))
            await db.commit()

    async def get_deployer_stats(self, deployer_address: str) -> Optional[dict]:
        """Deployer'ın geçmiş itibar verilerini döner."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT previous_tokens_count, rug_history_count, reputation_score FROM deployers WHERE address = ?", 
                (deployer_address,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return {
                        "previous_tokens_count": row[0],
                        "rug_history_count": row[1],
                        "reputation_score": row[2]
                    }
                return None

# Global DB yöneticisi
db_mgr = DatabaseManager(config.SQLITE_DB_PATH)
