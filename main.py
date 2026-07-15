# main.py
import asyncio
import aiohttp
import logging
import sys
from datetime import datetime

# Konfigürasyon ve Depolama
from config import config
from database import db_mgr
from storage.cache import cache

# Tarayıcılar (Scanners)
from scanners.dexscreener import DexScreenerScanner
from scanners.goplus import GoPlusScanner
from scanners.birdeye import BirdeyeScanner
from scanners.gmgn import GMGNScanner
from scanners.twitter import TwitterScanner

# Analizörler (Analyzers)
from analyzers.rugcheck import RugCheckAnalyzer
from analyzers.liquidity import LiquidityAnalyzer
from analyzers.volume import VolumeAnalyzer
from analyzers.holders import HoldersAnalyzer
from analyzers.wallets import WalletsAnalyzer
from analyzers.deployer import DeployerAnalyzer
from analyzers.washtrade import WashTradeAnalyzer
from analyzers.social import SocialAnalyzer
from analyzers.gemscore import GemScoreAnalyzer

# Yapay Zeka ve İletişim
from ai.scorer import AIScorer
from telegram import TelegramNotifier

# Logging Ayarları
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/gembot_pro.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("GemBot.Main")

class GemBotPro:
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.telegram: Optional[TelegramNotifier] = None
        self.running = False

    async def initialize(self):
        """Tüm servisleri ve asenkron istemcileri ayağa kaldırır."""
        logger.info("GemBot Pro başlatılıyor...")
        
        # 1. Veritabanını hazırla
        await db_mgr.initialize()

        # 2. Asenkron HTTP Session oluştur (TCP Connector ile bağlantı havuzu yönetimi)
        connector = aiohttp.TCPConnector(limit=100, keepalive_timeout=30)
        self.session = aiohttp.ClientSession(connector=connector)

        # 3. Telegram kuyruğunu başlat
        self.telegram = TelegramNotifier(self.session)
        await self.telegram.start()

        # 4. Bağımlılıkları enjekte ederek motorları kur
        self.dex_scanner = DexScreenerScanner(self.session)
        self.goplus_scanner = GoPlusScanner(self.session)
        self.birdeye_scanner = BirdeyeScanner(self.session)
        self.gmgn_scanner = GMGNScanner(self.session)
        self.twitter_scanner = TwitterScanner(self.session)

        self.rug_analyzer = RugCheckAnalyzer()
        self.liq_analyzer = LiquidityAnalyzer()
        self.vol_analyzer = VolumeAnalyzer()
        self.holders_analyzer = HoldersAnalyzer()
        self.wallets_analyzer = WalletsAnalyzer()
        self.deployer_analyzer = DeployerAnalyzer()
        self.washtrade_analyzer = WashTradeAnalyzer()
        self.social_analyzer = SocialAnalyzer()
        self.gemscore_analyzer = GemScoreAnalyzer()
        
        self.ai_scorer = AIScorer()

        logger.info("Tüm sistemler başarıyla ilklendirildi.")

    async def process_token(self, pair: dict):
        """Tek bir token için tam analiz hattını (pipeline) çalıştırır."""
        token_address = pair.get("baseToken", {}).get("address")
        chain = pair.get("chainId", "").lower()
        symbol = pair.get("baseToken", {}).get("symbol", "N/A")
        name = pair.get("baseToken", {}).get("name", "N/A")

        if not token_address or chain not in config.SUPPORTED_CHAINS:
            return

        # 1. Mükerrer Kontrolü (Cache ve DB Guard)
        if await cache.get(token_address) or await db_mgr.is_gem_notified(token_address):
            return

        # Önbelleğe alarak aynı döngüde tekrar taranmasını engelle
        await cache.set(token_address, True, ttl=config.CACHE_TTL_SECONDS)
        logger.info(f"🔎 Yeni Token İncelemede: {symbol} ({token_address}) - Zincir: {chain.upper()}")

        try:
            # 2. Asenkron Paralel Veri Toplama (Concurrency)
            # Tüm API isteklerini aynı anda asenkron olarak tetikliyoruz
            security_task = asyncio.create_task(self.goplus_scanner.check_token_security(chain, token_address))
            wallet_task = asyncio.create_task(self.gmgn_scanner.get_wallet_analytics(chain, token_address))
            twitter_task = asyncio.create_task(self.twitter_scanner.get_social_volume(symbol))
            
            security_data, wallet_data, twitter_data = await asyncio.gather(
                security_task, wallet_task, twitter_task, return_exceptions=True
            )

            # Hata durumunda boş dict dönerek analizörün çökmesini engelle
            security_data = security_data if not isinstance(security_data, Exception) else None
            wallet_data = wallet_data if not isinstance(wallet_data, Exception) else None
            twitter_data = twitter_data if not isinstance(twitter_data, Exception) else None

            # 3. Analiz Adımları (Pipeline execution)
            rug_res = await self.rug_analyzer.analyze(security_data, chain)
            liq_res = await self.liq_analyzer.analyze(pair, security_data)
            vol_res = await self.vol_analyzer.analyze(pair)
            
            # Mock holders data (Gerçek API'lerden yoksun kalındığında fallback)
            mock_holders = [{"address": "0x1", "percent": 0.05}] * 50
            holders_res = await self.holders_analyzer.analyze(mock_holders)
            
            wallets_res = await self.wallets_analyzer.analyze(wallet_data)
            
            deployer_address = pair.get("info", {}).get("imageUrl", "").split("/")[-1] or "0x0000000" # Örnek deployer parsing
            deployer_res = await self.deployer_analyzer.analyze(deployer_address)
            
            washtrade_res = await self.washtrade_analyzer.analyze(pair, wallet_data)
            social_res = await self.social_analyzer.analyze(twitter_data, pair)

            # Tüm analiz verilerini birleştir
            analysis_results = {
                "rugcheck": rug_res,
                "liquidity": liq_res,
                "volume": vol_res,
                "holders": holders_res,
                "wallets": wallets_res,
                "deployer": deployer_res,
                "washtrade": washtrade_res,
                "social": social_res
            }

            # 4. Skorlama ve Tahmin Motorları
            scores = await self.gemscore_analyzer.calculate(analysis_results)
            
            # AI Gem Score Hesaplama
            ai_gem_score = await self.ai_scorer.predict_ai_score(analysis_results, scores["gem_score"])
            scores["gem_score"] = ai_gem_score # Hibrit skoru ana skor yap

            logger.info(f"📊 Token: {symbol} | Gem Score: {scores['gem_score']:.1f} | Risk Score: {scores['risk_score']:.1f}")

            # 5. Eşik Kontrolü ve Bildirim Gönderimi
            if scores["gem_score"] >= config.MIN_GEM_SCORE_TO_ALERT and not rug_res["is_honeypot"]:
                logger.info(f"🎯 GEM BULUNDU! Sinyal gönderiliyor: {symbol}")
                
                token_summary = {
                    "address": token_address,
                    "chain": chain,
                    "name": name,
                    "symbol": symbol,
                    "mcap": float(pair.get("marketCap", 0.0)),
                    "liquidity": liq_res["liquidity_usd"],
                    "holders_count": holders_res["holder_count"],
                    "smart_wallets": wallets_res["smart_wallets_count"],
                    "fresh_wallets": wallets_res["fresh_wallets_count"],
                    "holder_growth": holders_res["holder_growth_pct"],
                    "volume_growth": vol_res["volume_growth_pct"],
                    "liquidity_growth": liq_res["liquidity_growth_24h"],
                    "deployer_reputation": deployer_res["deployer_reputation_score"]
                }
                
                # Telegram bildirim kuyruğuna ekle
                await self.telegram.send_gem_alert(token_summary, scores)
                
                # Veritabanına kaydet (Kalıcılık)
                await db_mgr.save_gem(
                    address=token_address,
                    name=name,
                    symbol=symbol,
                    chain=chain,
                    mcap=token_summary["mcap"],
                    liquidity=token_summary["liquidity"],
                    gem_score=scores["gem_score"],
                    risk_score=scores["risk_score"]
                )

        except Exception as e:
            logger.error(f"{symbol} analiz edilirken kritik hata oluştu: {e}", exc_info=True)

    async def run_scan_cycle(self):
        """Her 15 saniyede bir tetiklenen ana tarama döngüsü."""
        try:
            logger.info("Tarama döngüsü başladı...")
            # DexScreener'dan yeni hareketli çiftleri çek
            new_pairs = await self.dex_scanner.scan_new_pairs()
            
            if new_pairs:
                logger.info(f"{len(new_pairs)} yeni/aktif çift yakalandı. Analiz hattına gönderiliyor...")
                # Tüm çiftleri eş zamanlı asenkron görevler olarak işle
                tasks = [self.process_token(pair) for pair in new_pairs]
                await asyncio.gather(*tasks)
                
        except Exception as e:
            logger.error(f"Tarama döngüsünde genel hata: {e}")

    async def start(self):
        """Sonsuz tarama döngüsünü asenkron yönetir."""
        await self.initialize()
        self.running = True
        logger.info(f"GemBot Pro aktif. Tarama aralığı: {config.SCAN_INTERVAL_SECONDS} saniye.")

        while self.running:
            start_time = asyncio.get_event_loop().time()
            await self.run_scan_cycle()
            
            # Zaman kaymalarını önlemek için hassas zamanlayıcı (drift correction)
            elapsed = asyncio.get_event_loop().time() - start_time
            sleep_time = max(0.1, config.SCAN_INTERVAL_SECONDS - elapsed)
            await asyncio.sleep(sleep_time)

    async def stop(self):
        """Botu kapatırken kaynakları düzgünce serbest bırakır (Graceful Shutdown)."""
        self.running = False
        logger.info("Sistem kapatılıyor...")
        if self.telegram:
            await self.telegram.stop()
        if self.session:
            await self.session.close()
        logger.info("Sistem başarıyla durduruldu. Güvenli çıkış yapıldı.")

if __name__ == "__main__":
    bot = GemBotPro()
    try:
        asyncio.run(bot.start())
    except KeyboardInterrupt:
        logger.info("Kullanıcı tarafından sonlandırıldı.")
        asyncio.run(bot.stop())
