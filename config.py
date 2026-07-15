# config.py
import os
from pathlib import Path
from dotenv import load_dotenv

# .env yüklemesi
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

class Config:
    # Telegram Konfigürasyonu
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # API Anahtarları
    HELIUS_API_KEY: str = os.getenv("HELIUS_API_KEY", "")
    BIRDEYE_API_KEY: str = os.getenv("BIRDEYE_API_KEY", "")
    TWITTER_BEARER_TOKEN: str = os.getenv("TWITTER_BEARER_TOKEN", "")

    # Depolama ve Önbellek
    SQLITE_DB_PATH: str = os.getenv("SQLITE_DB_PATH", str(BASE_DIR / "storage" / "gembot_pro.db"))
    CACHE_TTL_SECONDS: int = int(os.getenv("CACHE_TTL_SECONDS", "3600"))

    # Bot Çalışma Ayarları
    SCAN_INTERVAL_SECONDS: int = int(os.getenv("SCAN_INTERVAL_SECONDS", "15"))
    MIN_GEM_SCORE_TO_ALERT: int = int(os.getenv("MIN_GEM_SCORE_TO_ALERT", "70"))

    # Puanlama Ağırlıkları (Toplam: 100 Puan)
    WEIGHT_LP_LOCK: float = 10.0
    WEIGHT_SMART_WALLET: float = 20.0
    WEIGHT_HOLDER_GROWTH: float = 15.0
    WEIGHT_DEPLOYER_REPUTATION: float = 20.0
    WEIGHT_FRESH_WALLET: float = 10.0
    WEIGHT_SOCIAL_SCORE: float = 5.0
    WEIGHT_VOLUME_MOMENTUM: float = 10.0
    WEIGHT_LIQUIDITY_MOMENTUM: float = 10.0

    # Güvenlik Eşikleri
    MAX_SINGLE_HOLDER_PCT: float = 8.0
    MAX_TOP10_HOLDER_PCT: float = 35.0
    MAX_TAX_PCT: float = 10.0

    # Desteklenen Ağlar
    SUPPORTED_CHAINS = [
        "solana", "base", "ethereum", "bsc", 
        "arbitrum", "optimism", "robinhood", "polygon", "avalanche"
    ]

# Global konfigürasyon nesnesi
config = Config()

# Gerekli dizinlerin otomatik oluşturulması
for folder in ["storage", "logs"]:
    Path(BASE_DIR / folder).mkdir(exist_ok=True)
