# telegram.py
import asyncio
import aiohttp
import logging
from typing import Dict, Any
from config import config

logger = logging.getLogger("GemBot.Telegram")

class TelegramNotifier:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.token = config.TELEGRAM_BOT_TOKEN
        self.chat_id = config.TELEGRAM_CHAT_ID
        self.base_url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task] = None

    async def start(self):
        """Mesaj gönderme işçisini (worker) arka planda başlatır."""
        if not self.token or not self.chat_id:
            logger.warning("Telegram Bot Token veya Chat ID eksik! Bildirimler devre dışı.")
            return
        self._worker_task = asyncio.create_task(self._message_worker())
        logger.info("Asenkron Telegram kuyruğu aktif.")

    async def stop(self):
        """Kuyruk işçisini güvenle sonlandırır."""
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            logger.info("Telegram kuyruğu durduruldu.")

    async def _message_worker(self):
        """Kuyruktan mesajları sırayla alır ve rate limit korumasıyla gönderir."""
        while True:
            message = await self._queue.get()
            try:
                payload = {
                    "chat_id": self.chat_id,
                    "text": message,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True
                }
                async with self.session.post(self.base_url, json=payload, timeout=10) as response:
                    if response.status == 429: # Too Many Requests
                        retry_after = int(response.headers.get("Retry-After", 5))
                        logger.warning(f"Telegram Rate Limit! {retry_after} saniye bekleniyor...")
                        await asyncio.sleep(retry_after)
                        # Mesajı tekrar göndermek üzere en başa koy
                        await self._queue.put(message)
                    elif response.status != 200:
                        logger.error(f"Telegram mesaj gönderme hatası: Status {response.status}")
                
                # Telegram spam koruması için her mesaj arasında kısa bir bekleme
                await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Telegram worker istisnası: {e}")
                await asyncio.sleep(2)
            finally:
                self._queue.task_done()

    async def send_gem_alert(self, token_data: Dict[str, Any], score_data: Dict[str, Any]):
        """Gem alarmını estetik HTML formatında hazırlar ve kuyruğa ekler."""
        # DexScreener ve Birdeye link üreticileri
        address = token_data.get("address", "")
        chain = token_data.get("chain", "solana").lower()
        
        dex_link = f"https://dexscreener.com/{chain}/{address}"
        birdeye_link = f"https://birdeye.so/token/{address}?chain={chain}"

        message = (
            f"🚨 <b>GEM DETECTED</b>\n\n"
            f"<b>Name:</b> {token_data.get('name', 'N/A')}\n"
            f"<b>Symbol:</b> {token_data.get('symbol', 'N/A')}\n"
            f"<b>Chain:</b> {chain.upper()}\n"
            f"<b>Market Cap:</b> ${token_data.get('mcap', 0):,.2f}\n"
            f"<b>Liquidity:</b> ${token_data.get('liquidity', 0):,.2f}\n"
            f"<b>Holders:</b> {token_data.get('holders_count', 0):,}\n"
            f"<b>Smart Wallets:</b> {token_data.get('smart_wallets', 0)}\n"
            f"<b>Fresh Wallets:</b> {token_data.get('fresh_wallets', 0)}\n"
            f"<b>Holder Growth:</b> +{token_data.get('holder_growth', 0.0):.1f}%\n"
            f"<b>Volume Growth:</b> +{token_data.get('volume_growth', 0.0):.1f}%\n"
            f"<b>Liquidity Growth:</b> +{token_data.get('liquidity_growth', 0.0):.1f}%\n"
            f"<b>Deployer Reputation:</b> {token_data.get('deployer_reputation', 0.0):.1f}/100\n\n"
            f"🎯 <b>Gem Score:</b> <b>{score_data.get('gem_score', 0):.1f}/100</b>\n"
            f"⚠️ <b>Risk Score:</b> {score_data.get('risk_score', 0):.1f}/100\n"
            f"📈 <b>Moon Probability:</b> {score_data.get('moon_probability', 0):.1f}%\n\n"
            f"🔗 <a href='{dex_link}'>DexScreener Link</a> | <a href='{birdeye_link}'>Birdeye Link</a>"
        )
        
        await self._queue.put(message)
