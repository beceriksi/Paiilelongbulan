import os

class Config:
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
    SUPPORTED_CHAINS = ["solana", "ethereum", "base", "bsc"]
    CACHE_TTL_SECONDS = 3600
    MIN_GEM_SCORE_TO_ALERT = 75.0
    SCAN_INTERVAL_SECONDS = 15

config = Config()
